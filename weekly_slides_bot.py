"""
weekly_slides_bot.py

One-shot Discord bot that:
1. Finds the most recent GUESS CHAT marker in the submissions channel
2. Collects SUBMISSION messages posted after that marker
3. Builds named + anonymous Google Slides decks from a template
4. Posts links in the results channel
5. Persists state to state.json for incremental updates
"""

from __future__ import annotations

import asyncio
import datetime
import io
import json
import os
import random
import re
import ssl
import time
import traceback
import zoneinfo
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import discord
import requests
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as AuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload
from PIL import Image, UnidentifiedImageError

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
DISCORD_CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])
DISCORD_RESULTS_CHANNEL_ID = int(os.environ["DISCORD_RESULTS_CHANNEL_ID"])
_MOD_CHANNEL_RAW = os.environ.get("DISCORD_MOD_CHANNEL_ID")
DISCORD_MOD_CHANNEL_ID: int | None = int(_MOD_CHANNEL_RAW) if _MOD_CHANNEL_RAW else None
_TEST_CHANNEL_RAW = os.environ.get("DISCORD_TEST_CHANNEL_ID")
DISCORD_TEST_CHANNEL_ID: int | None = int(_TEST_CHANNEL_RAW) if _TEST_CHANNEL_RAW else None
_STRIM_CHANNEL_RAW = os.environ.get("DISCORD_STRIM_CHANNEL_ID")
DISCORD_STRIM_CHANNEL_ID: int | None = int(_STRIM_CHANNEL_RAW) if _STRIM_CHANNEL_RAW else None
MOD_ROLE_NAME = os.environ.get("MOD_ROLE_NAME", "Mod")
BOT_MODE = os.environ.get("BOT_MODE", "slides")
GOOGLE_CREDS_FILE = os.environ.get("GOOGLE_CREDS_FILE", "oauth_token.json")
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive",
]
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID")
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
TEMPLATE_DECK_ID = os.environ["TEMPLATE_DECK_ID"]
GEMINI_API_KEY: str | None = os.environ.get("GEMINI_API_KEY")
GITHUB_TOKEN: str | None = os.environ.get("GITHUB_TOKEN")
GITHUB_REPOSITORY: str | None = os.environ.get("GITHUB_REPOSITORY")
# Manual override for the GUESS CHAT marker message.  Set this when the round
# was announced by a person rather than the bot: the bot then treats that
# message as the round marker instead of scanning the channel history, and
# skips posting its own announcement.  Persisted to state so that subsequent
# scheduled runs in the same round keep using it.
MARKER_MESSAGE_ID: str | None = (os.environ.get("MARKER_MESSAGE_ID") or "").strip() or None


def _load_dispatch_inputs() -> dict:
    """Return the workflow_dispatch inputs from the Actions event payload."""
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        return {}
    try:
        return json.loads(Path(path).read_text()).get("inputs") or {}
    except (OSError, ValueError):
        return {}


# Set when the run was started by a /guesschat slash command, so the bot can
# replace the "⏳ Running…" reply with the outcome.  Read straight from the
# event payload rather than through ${{ }} in the workflow: this repo is
# public, and anything expanded there is printed in the Actions logs.
_DISPATCH_INPUTS = _load_dispatch_inputs()
INTERACTION_APP_ID: str | None = _DISPATCH_INPUTS.get("interaction_app_id") or None
INTERACTION_TOKEN: str | None = _DISPATCH_INPUTS.get("interaction_token") or None

MARKER_PREFIX = "GUESS CHAT"
SUBMISSION_PREFIX = "SUBMISSION"

# Regexes that tolerate leading markdown formatting (headings, bold, italic).
# Examples matched by _MARKER_LINE_RE: "GUESS CHAT Topic", "# GUESS CHAT Topic"
# Examples matched by _SUBMISSION_RE: "SUBMISSION answer", "**SUBMISSION** answer",
# "SUBMISSION: answer", "**SUBMISSION:** answer" (the separator is dropped; a
# dash only counts as one when followed by a space, so "-1" keeps its sign)
_MD_PREFIX_RE = re.compile(r"^[#*_ \t]+")
_MARKER_LINE_RE = re.compile(r"^[#*_ \t]*(GUESS\s+CHAT)\b\s*(.*)", re.IGNORECASE)
_SUBMISSION_RE = re.compile(
    r"^[#*_ \t]*(SUBMISSION)\b[*_]*\s*(?:[:–—][*_]*\s*|-[*_]*\s+)?(.*)", re.IGNORECASE | re.DOTALL
)
_URL_RE = re.compile(r"https?://[^\s<>\"]+")

# YouTube URL pattern – matches standard, short, and embed URLs.
_YOUTUBE_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?[^\s]*v=|youtu\.be/|youtube\.com/embed/)"
    r"(?P<id>[A-Za-z0-9_-]{11})"
    r"[^\s]*",
)


# Regex to parse the channel description/topic set by moderators.
# Expected format: "Current Guess Chat: <topic>"
_CHANNEL_TOPIC_RE = re.compile(r"Current\s+Guess\s+Chat:\s*(.+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Topic extraction
# ---------------------------------------------------------------------------


def extract_topic(content: str) -> str:
    """Extract the topic from a GUESS CHAT message.

    Handles both ``GUESS CHAT Topic`` on one line and multi-line formats
    like ``# GUESS CHAT\\n# Topic``.  Returns ``"Unknown"`` when no topic
    text can be found.
    """
    first_line = content.split("\n", 1)[0]
    marker_match = _MARKER_LINE_RE.match(first_line)
    topic = marker_match.group(2).strip() if marker_match and marker_match.group(2).strip() else ""
    if not topic:
        for line in content.split("\n")[1:]:
            candidate = _MD_PREFIX_RE.sub("", line).strip()
            if candidate:
                topic = candidate
                break
    if not topic:
        topic = "Unknown"
    return topic


def parse_channel_topic(description: str | None) -> str | None:
    """Parse the topic from a channel description.

    Returns the topic string if the description matches
    ``Current Guess Chat: <topic>``, otherwise ``None``.
    """
    if not description or not isinstance(description, str):
        return None
    m = _CHANNEL_TOPIC_RE.match(description.strip())
    return m.group(1).strip() if m else None


_UK_TZ = zoneinfo.ZoneInfo("Europe/London")
_DEADLINE_HOUR = 11
_DEADLINE_MINUTE = 30


def next_friday_deadline_unix(reference_utc: datetime.datetime | None = None) -> int:
    """Return the Unix timestamp of the next Friday at 11:30 UK time.

    If *reference_utc* is not provided, ``datetime.datetime.now(UTC)`` is used.
    When the reference time is already on a Friday but before 11:30 UK, the
    deadline is set to the same Friday.  Otherwise it is set to the next
    occurring Friday.
    """
    now_utc = reference_utc or datetime.datetime.now(datetime.timezone.utc)
    now_uk = now_utc.astimezone(_UK_TZ)
    days_until_friday = (4 - now_uk.weekday()) % 7  # weekday 4 == Friday
    if days_until_friday == 0:
        # Today is Friday — use next week if we're already at or past the deadline time
        if now_uk.hour > _DEADLINE_HOUR or (
            now_uk.hour == _DEADLINE_HOUR and now_uk.minute >= _DEADLINE_MINUTE
        ):
            days_until_friday = 7
    target_date = now_uk.date() + datetime.timedelta(days=days_until_friday)
    deadline_uk = datetime.datetime(
        target_date.year,
        target_date.month,
        target_date.day,
        _DEADLINE_HOUR,
        _DEADLINE_MINUTE,
        0,
        tzinfo=_UK_TZ,
    )
    return int(deadline_uk.timestamp())


def build_announcement_message(topic: str, deadline_ts: int | None = None) -> str:
    """Build the formatted announcement message for a new Guess Chat round."""
    if deadline_ts is None:
        deadline_ts = next_friday_deadline_unix()
    return (
        f"# GUESS CHAT\n"
        f"# {topic.upper()}\n"
        f"@everyone\n"
        f"- start your message with **SUBMISSION**, e.g. `SUBMISSION your answer here`\n"
        f"- deadline: <t:{deadline_ts}:F> (<t:{deadline_ts}:R>)"
    )


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


def load_state() -> dict:
    path = Path(STATE_FILE)
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_state(state: dict) -> None:
    Path(STATE_FILE).write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# GitHub issue creation on error
# ---------------------------------------------------------------------------

_ISSUE_LABEL = "bot-error"


def create_github_issue(exc: BaseException) -> None:
    """Create a GitHub issue for an unhandled bot exception.

    Requires ``GITHUB_TOKEN`` and ``GITHUB_REPOSITORY`` environment variables.
    If either is missing a warning is printed and the function returns.
    Duplicate open issues with the same title are avoided by searching
    before creating.
    """
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        print("[warn] GITHUB_TOKEN or GITHUB_REPOSITORY not set; skipping issue creation.")
        return

    exc_type = type(exc).__qualname__
    title = f"Bot error: {exc_type}: {exc}"
    # Truncate title to fit GitHub's limit
    if len(title) > 256:
        title = title[:253] + "..."

    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    body_lines = [
        f"**Bot mode:** `{BOT_MODE}`",
        f"**Time (UTC):** {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "### Traceback",
        "```",
        "".join(tb).rstrip(),
        "```",
    ]
    body = "\n".join(body_lines)

    api_url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }

    try:
        # Check for an existing open issue with the same title
        search_url = "https://api.github.com/search/issues"
        query = f"repo:{GITHUB_REPOSITORY} is:issue is:open in:title {exc_type}"
        resp = requests.get(search_url, headers=headers, params={"q": query}, timeout=15)
        if resp.ok:
            for item in resp.json().get("items", []):
                if item.get("title") == title:
                    print(f"[info] Duplicate issue already open: #{item['number']}")
                    return

        # Ensure the label exists (ignore errors if it already does)
        requests.post(
            f"{api_url}/labels",
            headers=headers,
            json={"name": _ISSUE_LABEL, "color": "d73a4a", "description": "Automated error report from bot run"},
            timeout=15,
        )

        issue_resp = requests.post(
            f"{api_url}/issues",
            headers=headers,
            json={"title": title, "body": body, "labels": [_ISSUE_LABEL]},
            timeout=15,
        )
        if issue_resp.ok:
            issue_number = issue_resp.json().get("number")
            print(f"[info] Created GitHub issue #{issue_number}")
            _self_assign_issue(api_url, headers, issue_number)
        else:
            print(f"[error] Failed to create GitHub issue: {issue_resp.status_code} {issue_resp.text}")
    except Exception as api_exc:  # noqa: BLE001
        print(f"[error] Could not create GitHub issue: {api_exc}")


def _self_assign_issue(api_url: str, headers: dict[str, str], issue_number: int) -> None:
    """Try to assign the authenticated GitHub user to the given issue.

    Looks up the current user via ``GET /user`` and then adds them as an
    assignee.  Failures are logged but never raised — assignment is
    best-effort.
    """
    try:
        user_resp = requests.get("https://api.github.com/user", headers=headers, timeout=15)
        if not user_resp.ok:
            print(f"[warn] Could not look up GitHub user for self-assign: {user_resp.status_code}")
            return
        login = user_resp.json().get("login")
        if not login:
            print("[warn] GitHub user response missing login; skipping self-assign.")
            return
        assign_resp = requests.post(
            f"{api_url}/issues/{issue_number}/assignees",
            headers=headers,
            json={"assignees": [login]},
            timeout=15,
        )
        if assign_resp.ok:
            print(f"[info] Assigned {login} to issue #{issue_number}")
        else:
            print(f"[warn] Could not assign issue #{issue_number}: {assign_resp.status_code}")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Self-assign failed for issue #{issue_number}: {exc}")


# ---------------------------------------------------------------------------
# Google API retry helper
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS_CODES = (429, 500, 502, 503)


class StorageQuotaExceededError(Exception):
    """Raised when the Google Drive storage quota has been exceeded."""


def execute_with_retry(request, max_retries: int = 5) -> Any:
    """Execute a Google API request with exponential backoff on transient errors."""
    for attempt in range(max_retries + 1):
        try:
            return request.execute()
        except RefreshError as exc:
            print(
                "[error] Google credential refresh failed during API call. "
                "Please check the credentials file configured via GOOGLE_CREDS_FILE "
                "and, in CI, the GOOGLE_OAUTH_TOKEN secret that populates it."
            )
            raise
        except HttpError as exc:
            status = exc.resp.status
            exc_text = str(exc) + getattr(exc, "content", b"").decode("utf-8", errors="replace")
            if status == 403 and "storageQuotaExceeded" in exc_text:
                raise StorageQuotaExceededError(
                    "Google Drive storage quota exceeded. "
                    "Free up space in Google Drive or upgrade storage, then re-run the bot."
                ) from exc
            retryable = status in _RETRYABLE_STATUS_CODES or (
                status == 403 and "rateLimitExceeded" in str(exc)
            )
            if retryable and attempt < max_retries:
                wait = (2 ** attempt) + random.random()
                print(
                    f"[warn] Google API error (HTTP {status}); "
                    f"retrying in {wait:.1f}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(wait)
            else:
                raise
        except (ssl.SSLEOFError, ssl.SSLError, ConnectionError, OSError) as exc:
            if attempt < max_retries:
                wait = (2 ** attempt) + random.random()
                print(
                    f"[warn] Transient connection error: {exc!r}; "
                    f"retrying in {wait:.1f}s (attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(wait)
            else:
                raise


# ---------------------------------------------------------------------------
# Google API helpers
# ---------------------------------------------------------------------------


def get_google_services():
    try:
        with open(GOOGLE_CREDS_FILE) as f:
            token_data = json.load(f)
    except json.JSONDecodeError:
        raise ValueError(
            f"[error] Credentials file {GOOGLE_CREDS_FILE!r} contains invalid JSON. "
            "If you are in CI, check that the GOOGLE_OAUTH_TOKEN secret was base64-decoded "
            "correctly and is not empty or truncated."
        )

    cred_type = token_data.get("type")

    if cred_type == "authorized_user" or (not cred_type and "refresh_token" in token_data):
        label = "authorized-user" if cred_type == "authorized_user" else "legacy format"
        print(f"[info] Using OAuth2 user credentials ({label}).")
        creds = Credentials(
            token=None,
            refresh_token=token_data["refresh_token"],
            client_id=token_data["client_id"],
            client_secret=token_data["client_secret"],
            token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        )
    else:
        # Detect common mistakes: OAuth client-config files have "installed"
        # or "web" top-level keys instead of credential fields.
        hint = ""
        if "installed" in token_data or "web" in token_data:
            hint = (
                " The file looks like an OAuth client-config JSON "
                "(it contains an 'installed' or 'web' key). "
                "You need to provide an authorized-user credentials "
                "JSON instead."
            )
        msg = (
            f"[error] Unrecognised credential file format "
            f"(type={cred_type!r}).{hint} "
            f"Please check the credentials file configured via GOOGLE_CREDS_FILE "
            f"and, in CI, the GOOGLE_OAUTH_TOKEN secret that populates it."
        )
        raise ValueError(msg)

    # Eagerly refresh the token so we fail fast with a clear message
    # instead of crashing deep inside the first API call.
    try:
        creds.refresh(AuthRequest())
    except RefreshError:
        print(
            "[error] Google OAuth token is expired or revoked. "
            "Please regenerate your refresh token and update the "
            "credentials file (GOOGLE_CREDS_FILE) or, in CI, "
            "the GOOGLE_OAUTH_TOKEN secret."
        )
        raise
    slides = build("slides", "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    return slides, drive


def copy_presentation(drive_svc, title: str) -> str:
    """Copy the template deck and return the new presentation ID."""
    body: dict[str, Any] = {"name": title}
    if DRIVE_FOLDER_ID:
        body["parents"] = [DRIVE_FOLDER_ID]
    result = execute_with_retry(drive_svc.files().copy(fileId=TEMPLATE_DECK_ID, body=body))
    return result["id"]


def copy_presentation_with_quota_retry(
    drive_svc, title: str, max_retries: int = 4
) -> str:
    """Copy the template deck, retrying with backoff on quota errors.

    Google Drive quota may not update immediately after emptying trash.
    Retrying with exponential backoff gives the quota time to propagate.
    """
    for attempt in range(max_retries + 1):
        try:
            return copy_presentation(drive_svc, title)
        except StorageQuotaExceededError:
            if attempt == max_retries:
                raise
            wait = 2 ** (attempt + 1)  # 2, 4, 8, 16 seconds
            print(
                f"[warn] Storage quota exceeded after trash empty; "
                f"retrying in {wait}s (attempt {attempt + 1}/{max_retries})"
            )
            time.sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


def share_presentation(drive_svc, file_id: str) -> None:
    """Share presentation as anyone-with-link editor."""
    execute_with_retry(
        drive_svc.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "writer"},
            fields="id",
        )
    )


def delete_drive_file(drive_svc, file_id: str) -> None:
    """Delete a file from Google Drive. Silently ignores missing files."""
    try:
        execute_with_retry(drive_svc.files().delete(fileId=file_id))
        print(f"[info] Deleted old file {file_id} from Drive.")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Could not delete file {file_id}: {exc}")


def delete_old_images(drive_svc) -> None:
    """Delete all 'submission_image' files owned by us from the Drive folder."""
    if not DRIVE_FOLDER_ID:
        return
    try:
        query = (
            f"name = 'submission_image' and '{DRIVE_FOLDER_ID}' in parents "
            f"and trashed = false and 'me' in owners"
        )
        resp = execute_with_retry(
            drive_svc.files().list(q=query, fields="files(id)", pageSize=1000)
        )
        files = resp.get("files", [])
        if not files:
            return
        print(f"[info] Deleting {len(files)} old submission image(s) from Drive.")
        for f in files:
            delete_drive_file(drive_svc, f["id"])
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Could not clean up old images: {exc}")


def empty_trash(drive_svc) -> None:
    """Permanently delete all trashed files to free Drive quota."""
    try:
        execute_with_retry(drive_svc.files().emptyTrash())
        print("[info] Emptied Drive trash.")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Could not empty Drive trash: {exc}")


def presentation_url(pres_id: str) -> str:
    return f"https://docs.google.com/presentation/d/{pres_id}/edit?usp=sharing"


def slide_url(pres_id: str, slide_id: str) -> str:
    """Return a direct URL to a specific slide in a Google Slides presentation."""
    return f"https://docs.google.com/presentation/d/{pres_id}/edit#slide=id.{slide_id}"


def discord_message_url(guild_id: int, channel_id: int, message_id: str) -> str:
    """Return a direct URL to a Discord message."""
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def _resolve_mod_mention(guild: discord.Guild | None) -> str:
    """Return a proper Discord role mention for MOD_ROLE_NAME.

    Looks up the role by name in *guild* and returns ``<@&ROLE_ID>``.
    Falls back to ``@{MOD_ROLE_NAME}`` (plain text) if the guild is None or
    the role cannot be found.
    """
    if guild is not None:
        role = discord.utils.get(guild.roles, name=MOD_ROLE_NAME)
        if role is not None:
            return role.mention
    return f"@{MOD_ROLE_NAME}"


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------


class UploadedImage(NamedTuple):
    url: str        # public Drive URL the Slides API can fetch
    aspect: float   # width / height, for laying the image out


# EXIF orientations that rotate the picture a quarter turn.
_QUARTER_TURN_ORIENTATIONS = (5, 6, 7, 8)


def _image_aspect(data: bytes) -> float:
    """Return the width/height ratio of an image, as it will be displayed.

    Falls back to square for anything Pillow can't read, which still lays
    out sensibly.
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            w, h = img.size
            if img.getexif().get(0x0112) in _QUARTER_TURN_ORIENTATIONS:
                w, h = h, w
    except (UnidentifiedImageError, OSError, ValueError):
        return 1.0
    return w / h if w and h else 1.0


def upload_image_to_drive(
    drive_svc, url: str, cache: dict[str, UploadedImage]
) -> UploadedImage | None:
    """Download image from *url* and upload it to Drive.

    Returns its public URL and aspect ratio, or ``None`` if it couldn't be
    downloaded or uploaded.
    """
    if url in cache:
        return cache[url]
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] could not download image {url}: {exc}")
        return None

    content_type = resp.headers.get("content-type", "image/png").split(";")[0]
    fh = io.BytesIO(resp.content)
    media = MediaIoBaseUpload(fh, mimetype=content_type, resumable=False)
    meta: dict[str, Any] = {"name": "submission_image"}
    if DRIVE_FOLDER_ID:
        meta["parents"] = [DRIVE_FOLDER_ID]
    try:
        file_obj = execute_with_retry(
            drive_svc.files().create(body=meta, media_body=media, fields="id")
        )
    except StorageQuotaExceededError:
        print("[warn] Google Drive storage quota exceeded — skipping image upload.")
        return None
    file_id = file_obj["id"]

    # Make the file publicly readable so Slides API can fetch it
    execute_with_retry(
        drive_svc.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            fields="id",
        )
    )

    uploaded = UploadedImage(
        f"https://lh3.googleusercontent.com/d/{file_id}", _image_aspect(resp.content)
    )
    cache[url] = uploaded
    return uploaded


# ---------------------------------------------------------------------------
# Slide layout
# ---------------------------------------------------------------------------

# Layout constants (all in points; multiply by _PT to get EMU)
_PT = 12700          # EMU per point
_SLIDE_W_PT = 720    # fallback page size when a deck doesn't report one
_SLIDE_H_PT = 405
_MARGIN_PT = 24      # slide edge margin
_GAP_PT = 12         # gap between text and media, and between media items
_AUTHOR_BAR_PT = 55  # height reserved for the author label at the top
_AUTHOR_GAP_PT = 6   # gap between the author label and the content below it
_BODY_Y_TOLERANCE_PT = 5  # tolerance when matching the body element Y position

_MIN_FONT_PT = 10
_MAX_FONT_PT = 54
# Text larger than this reads comfortably on stream, so a layout gains
# nothing by giving the text more room beyond it.
_READABLE_FONT_PT = 28
# How much a readable font is worth against media coverage when choosing
# where to split a slide (coverage is a 0–1 fraction of the content area).
_TEXT_WEIGHT = 0.5
# Wrapping a line mid-way ("Penguin - Mario / Kart World") reads worse than
# slightly smaller text; a whole-line font size is used when it is at least
# this fraction of the largest wrapped size, and wrapping costs this factor
# in a layout's text score.
_WHOLE_LINE_MIN_RATIO = 0.75
_WRAP_PENALTY = 0.6
_TEXT_INSET_PT = 7.2  # Slides' default text box padding on each side
_LINE_HEIGHT = 1.2    # line height as a multiple of font size at 100% spacing
_VIDEO_ASPECT = 16 / 9

# Candidate splits between text and media, as a fraction of the content area
# given to the text: side by side (text left) and stacked (text on top).
_SIDE_SPLITS = (0.25, 0.33, 0.4, 0.5, 0.6)
_STACK_SPLITS = (0.15, 0.25, 0.35, 0.5)


@dataclass(frozen=True)
class Box:
    """A rectangle on the slide, in points."""

    x: float
    y: float
    w: float
    h: float

    def swapped(self) -> "Box":
        return Box(self.y, self.x, self.h, self.w)


@dataclass(frozen=True)
class SlideLayout:
    text_box: Box
    font_pt: float
    media_boxes: list[Box]
    # Short text spanning the slide is centred over the centred media; text
    # that wraps, or sits in a column beside the media, stays left-aligned.
    centred: bool = False


def _page_size_pt(pres: dict) -> tuple[float, float]:
    """Return the deck's page width and height in points."""
    size = pres.get("pageSize") or {}
    w = size.get("width", {}).get("magnitude")
    h = size.get("height", {}).get("magnitude")
    if not (w and h):
        return _SLIDE_W_PT, _SLIDE_H_PT
    return w / _PT, h / _PT


# Approximate Arial glyph widths in ems, enough to estimate line wrapping.
_NARROW_CHARS = frozenset("iljtfrI.,:;'!|()[]{} \"")
_WIDE_CHARS = frozenset("mwMW@%")


def _char_width_em(ch: str) -> float:
    if ch in _NARROW_CHARS:
        return 0.3
    if ch in _WIDE_CHARS:
        return 0.85
    if ch.isupper():
        return 0.68
    if ch.isdigit():
        return 0.56
    if ord(ch) < 0x2E80:
        return 0.52
    return 1.0  # CJK, emoji


def _text_width_em(text: str) -> float:
    return sum(_char_width_em(ch) for ch in text)


def _line_count(text: str, max_em: float) -> int:
    """Return how many lines *text* wraps to in a box *max_em* ems wide."""
    space = _char_width_em(" ")
    lines = 0
    for paragraph in text.split("\n"):
        lines += 1
        width = 0.0
        for word in paragraph.split(" "):
            word_w = _text_width_em(word)
            if width and width + space + word_w > max_em:
                lines += 1
                width = 0.0
            elif width:
                width += space
            # A word longer than a line breaks mid-word.
            while word_w > max_em:
                lines += 1
                word_w -= max_em
            width += word_w
    return lines


def _fit_text(text: str, box_w: float, box_h: float) -> tuple[float, bool]:
    """Return a font size at which *text* fits the box, and whether it wraps.

    Prefers the largest size at which every line fits whole, unless wrapping
    allows a much larger one.
    """
    usable_w = box_w - 2 * _TEXT_INSET_PT
    usable_h = box_h - 2 * _TEXT_INSET_PT
    if not text.strip():
        return _MAX_FONT_PT, False
    paragraphs = text.count("\n") + 1
    largest: float | None = None
    for font in range(_MAX_FONT_PT, _MIN_FONT_PT - 1, -1):
        lines = _line_count(text, usable_w / font)
        if lines * font * _LINE_HEIGHT > usable_h:
            continue
        if largest is None:
            largest = font
        if lines == paragraphs:
            if font >= largest * _WHOLE_LINE_MIN_RATIO:
                return font, False
            break
    return (largest if largest is not None else _MIN_FONT_PT), True


def _row_splits(n: int) -> list[list[int]]:
    """Return every way to split *n* items, in order, into rows."""
    if n == 0:
        return [[]]
    return [[k] + rest for k in range(1, n + 1) for rest in _row_splits(n - k)]


def _justified_rows(aspects: list[float], rows: list[int], area: Box) -> list[Box]:
    """Lay items out in rows of equal-height items, centred in *area*.

    Each row is scaled to span the full width; if the rows are then too tall
    in total, everything shrinks to fit the height.
    """
    groups, start = [], 0
    for count in rows:
        groups.append(aspects[start:start + count])
        start += count

    heights = [(area.w - _GAP_PT * (len(g) - 1)) / sum(g) for g in groups]
    gaps_h = _GAP_PT * (len(groups) - 1)
    if sum(heights) + gaps_h > area.h:
        scale = (area.h - gaps_h) / sum(heights)
        heights = [h * scale for h in heights]

    boxes = []
    y = area.y + (area.h - sum(heights) - gaps_h) / 2
    for group, h in zip(groups, heights):
        row_w = h * sum(group) + _GAP_PT * (len(group) - 1)
        x = area.x + (area.w - row_w) / 2
        for aspect in group:
            boxes.append(Box(x, y, h * aspect, h))
            x += h * aspect + _GAP_PT
        y += h + _GAP_PT
    return boxes


def _arrange_media(aspects: list[float], area: Box) -> tuple[list[Box], float]:
    """Pack media items (by width/height aspect) into *area*, keeping their shapes.

    Tries every arrangement into rows and into columns and keeps the best,
    judged by the sum of each item's side length (the square root of its
    area).  That rewards covering the area but favours items of similar size
    over one huge item beside tiny ones.  Returns the boxes, in item order,
    and the area they cover.
    """
    if not aspects or area.w <= 0 or area.h <= 0:
        return [], 0.0
    best: tuple[list[Box], float] = ([], 0.0)
    best_score = -1.0
    for split in _row_splits(len(aspects)):
        by_rows = _justified_rows(aspects, split, area)
        by_cols = [
            b.swapped()
            for b in _justified_rows([1 / a for a in aspects], split, area.swapped())
        ]
        for boxes in (by_rows, by_cols):
            if any(b.w <= 0 or b.h <= 0 for b in boxes):
                continue
            score = sum((b.w * b.h) ** 0.5 for b in boxes)
            if score > best_score:
                best_score = score
                best = (boxes, sum(b.w * b.h for b in boxes))
    return best


def plan_slide_layout(
    page_w: float,
    page_h: float,
    content_top: float,
    body_text: str,
    media_aspects: list[float],
) -> SlideLayout:
    """Decide where a submission's text and media go on the slide.

    Text-only and media-only slides get the whole content area.  With both,
    each candidate split (text beside or above the media) is scored on how
    much of the area the media covers plus how readable the text is, and the
    best one wins, so a short answer leaves most of the room to its pictures
    and a long one claims what it needs.
    """
    area = Box(
        _MARGIN_PT,
        content_top,
        page_w - 2 * _MARGIN_PT,
        page_h - content_top - _MARGIN_PT,
    )
    if not media_aspects:
        font, wraps = _fit_text(body_text, area.w, area.h)
        return SlideLayout(area, font, [], centred=not wraps)
    if not body_text.strip():
        return SlideLayout(area, _MAX_FONT_PT, _arrange_media(media_aspects, area)[0])

    candidates = []
    for frac in _SIDE_SPLITS:
        text_w = area.w * frac
        candidates.append((
            Box(area.x, area.y, text_w, area.h),
            Box(area.x + text_w + _GAP_PT, area.y, area.w - text_w - _GAP_PT, area.h),
        ))
    for frac in _STACK_SPLITS:
        text_h = area.h * frac
        candidates.append((
            Box(area.x, area.y, area.w, text_h),
            Box(area.x, area.y + text_h + _GAP_PT, area.w, area.h - text_h - _GAP_PT),
        ))

    best: tuple[float, SlideLayout] | None = None
    for text_box, media_area in candidates:
        font, wraps = _fit_text(body_text, text_box.w, text_box.h)
        boxes, covered = _arrange_media(media_aspects, media_area)
        readability = min(font, _READABLE_FONT_PT) / _READABLE_FONT_PT
        if wraps:
            readability *= _WRAP_PENALTY
        score = covered / (area.w * area.h) + _TEXT_WEIGHT * readability
        if best is None or score > best[0]:
            centred = text_box.w == area.w and not wraps
            best = (score, SlideLayout(text_box, font, boxes, centred))
    return best[1]


def _element_box_requests(object_id: str, elem: dict, box: Box) -> dict:
    """Return a request moving and scaling *elem* to fill *box*."""
    return {
        "updatePageElementTransform": {
            "objectId": object_id,
            "transform": {
                "scaleX": box.w * _PT / elem["size"]["width"]["magnitude"],
                "scaleY": box.h * _PT / elem["size"]["height"]["magnitude"],
                "shearX": 0,
                "shearY": 0,
                "translateX": box.x * _PT,
                "translateY": box.y * _PT,
                "unit": "EMU",
            },
            "applyMode": "ABSOLUTE",
        }
    }


def _media_element_properties(slide_id: str, box: Box) -> dict:
    return {
        "pageObjectId": slide_id,
        "size": {
            "width": {"magnitude": box.w * _PT, "unit": "EMU"},
            "height": {"magnitude": box.h * _PT, "unit": "EMU"},
        },
        "transform": {
            "scaleX": 1,
            "scaleY": 1,
            "translateX": box.x * _PT,
            "translateY": box.y * _PT,
            "unit": "EMU",
        },
    }


def _image_requests(slide_id: str, image_urls: list[str], boxes: list[Box]) -> list[dict]:
    """Return createImage requests placing each image in its planned box."""
    return [
        {
            "createImage": {
                "url": url,
                "elementProperties": _media_element_properties(slide_id, box),
            }
        }
        for url, box in zip(image_urls, boxes)
    ]


def _video_requests(slide_id: str, video_ids: list[str], box: Box) -> list[dict]:
    """Return a createVideo request for the first YouTube video."""
    if not video_ids:
        return []
    return [
        {
            "createVideo": {
                "source": "YOUTUBE",
                "id": video_ids[0],
                "elementProperties": _media_element_properties(slide_id, box),
            }
        }
    ]


def _insert_images(
    slides_svc,
    pres_id: str,
    slide_id: str,
    drive_urls: list[str],
    boxes: list[Box],
    author: str,
) -> list[str]:
    """Insert images into a slide, falling back to per-image insertion on failure.

    Returns a list of error description strings (empty if all images inserted
    successfully).
    """
    reqs = _image_requests(slide_id, drive_urls, boxes)
    if not reqs:
        return []

    # Try inserting all images in a single batch first.
    try:
        execute_with_retry(
            slides_svc.presentations().batchUpdate(
                presentationId=pres_id,
                body={"requests": reqs},
            )
        )
        return []
    except Exception as exc:  # noqa: BLE001
        batch_detail = str(exc) or repr(exc)
        print(
            f"[warn] Batch image insert failed for '{author}': {batch_detail}; "
            "retrying images individually"
        )

    # Fall back to inserting each image individually so one bad image
    # does not prevent the others from being placed on the slide.
    error_details: list[str] = []
    for req, url in zip(reqs, drive_urls):
        try:
            execute_with_retry(
                slides_svc.presentations().batchUpdate(
                    presentationId=pres_id,
                    body={"requests": [req]},
                )
            )
        except Exception as exc:  # noqa: BLE001
            detail = str(exc) or repr(exc)
            print(f"[warn] Could not insert image for '{author}' ({url}): {detail}")
            error_details.append(detail)

    return error_details


def _get_shape_text(elem: dict) -> str:
    """Extract the concatenated text content from a shape element."""
    parts: list[str] = []
    for te in elem.get("shape", {}).get("text", {}).get("textElements", []):
        parts.append(te.get("textRun", {}).get("content", ""))
    return "".join(parts)


def _elem_area(e: dict) -> float:
    """Return the rendered area (width × height) of a page element in EMU²."""
    return (
        e.get("size", {}).get("width", {}).get("magnitude", 0)
        * e.get("size", {}).get("height", {}).get("magnitude", 0)
    )


# Author box labels.  Slides are numbered so chat can refer to them while
# guessing: "#7 — Answer:" on the anonymous deck, "#7 — Answer: Sam" on the
# named one.  Decks built between the number being added and "Answer:" coming
# back read "#7" / "#7 — Sam", and older decks and hand-added slides use
# "Answer:" / "Answer: Sam", so all of these are recognised when reading a
# deck back.
_AUTHOR_LABEL_RE = re.compile(
    r"^(?:#\d+(?:\s+[—–-](?:\s+Answer:)?\s*(?P<numbered>.*))?|Answer:(?P<legacy>.*))$",
    re.DOTALL,
)


def format_author_label(number: int, author: str, named: bool) -> str:
    """Return the author box text for submission slide *number*."""
    if named and author:
        return f"#{number} — Answer: {author}"
    return f"#{number} — Answer:"


def parse_author_label(text: str) -> str | None:
    """Return the name in an author box label.

    Returns ``""`` for an anonymous label and ``None`` when *text* is not an
    author label at all.
    """
    m = _AUTHOR_LABEL_RE.match(text.strip())
    if m is None:
        return None
    return (m.group("numbered") or m.group("legacy") or "").strip()


def _is_author_label(elem: dict) -> bool:
    return parse_author_label(_get_shape_text(elem)) is not None


def _find_author_element(page_elements: list[dict]) -> dict | None:
    """Return the author text box element.

    Strategy (most-reliable first):
    1. Shape whose text is an author label (``#7``, ``#7 — Sam``, ``Answer: Sam``)
       — this is explicit and layout-independent.
    2. Largest shape physically inside the author bar area (y < threshold).
    """
    shapes = [elem for elem in page_elements if elem.get("shape")]

    # Primary: shape whose text is an author label (explicit content signal)
    candidates = [elem for elem in shapes if _is_author_label(elem)]
    if candidates:
        return max(candidates, key=_elem_area)

    # Fallback: largest shape above the author bar threshold (position-based)
    candidates = [
        elem for elem in shapes
        if elem.get("transform", {}).get("translateY", 0) / _PT < _AUTHOR_BAR_PT - _BODY_Y_TOLERANCE_PT
    ]
    if candidates:
        return max(candidates, key=_elem_area)

    return None


def _find_body_element(page_elements: list[dict]) -> dict | None:
    """Return the body text box element.

    The author element is identified first and excluded from all searches so
    that an author bar placed at an unusual vertical position never gets
    mistakenly returned as the body.

    Strategy (most-reliable first):
    1. Largest non-author shape below the author bar threshold (position-based).
    2. Largest non-author shape whose text is not an author label (content
       fallback for slides whose layout does not match the expected positions).
    """
    shapes = [elem for elem in page_elements if elem.get("shape")]

    author_elem = _find_author_element(page_elements)
    author_id = author_elem.get("objectId") if author_elem else None
    non_author = [s for s in shapes if s.get("objectId") != author_id]

    # Primary: largest non-author shape below the author bar threshold
    candidates = [
        elem for elem in non_author
        if elem.get("transform", {}).get("translateY", 0) / _PT >= _AUTHOR_BAR_PT - _BODY_Y_TOLERANCE_PT
    ]
    if candidates:
        return max(candidates, key=_elem_area)

    # Fallback: largest non-author shape whose text is not an author label
    candidates = [elem for elem in non_author if not _is_author_label(elem)]
    if candidates:
        return max(candidates, key=_elem_area)

    return None


def _author_bottom_emu(page_elements: list[dict]) -> int:
    """Return the bottom edge (in EMU) of the author element on the slide.

    Falls back to ``_AUTHOR_BAR_PT * _PT`` when the author element cannot be
    found or its geometry is incomplete.
    """
    author = _find_author_element(page_elements)
    if author is None:
        return int(_AUTHOR_BAR_PT * _PT)

    t = author.get("transform", {})
    y = t.get("translateY", 0)
    scale_y = t.get("scaleY", 1)
    intrinsic_h = author.get("size", {}).get("height", {}).get("magnitude", 0)
    rendered_h = intrinsic_h * scale_y
    bottom = y + rendered_h
    # Never let the gap be less than _AUTHOR_BAR_PT (sanity floor)
    return int(max(bottom, _AUTHOR_BAR_PT * _PT))


def _body_resize_requests(page_elements: list[dict], box: Box) -> list[dict]:
    """Return requests moving the body text box to *box* and centring its text.

    The text is centred vertically so a short answer sits in the middle of
    its space rather than clinging to the top.
    """
    elem = _find_body_element(page_elements)
    if elem is None:
        return []
    return [
        _element_box_requests(elem["objectId"], elem, box),
        {
            "updateShapeProperties": {
                "objectId": elem["objectId"],
                "shapeProperties": {
                    "contentAlignment": "MIDDLE",
                },
                "fields": "contentAlignment",
            }
        },
    ]


def _text_fit_requests(element_id: str, font_pt: float, centred: bool = False) -> list[dict]:
    """Return requests setting the body font size and paragraph alignment.

    The alignment is always set, because an appended slide is a copy of an
    existing one and would otherwise inherit that slide's alignment.
    """
    return [
        {
            "updateTextStyle": {
                "objectId": element_id,
                "textRange": {"type": "ALL"},
                "style": {
                    "fontSize": {
                        "magnitude": font_pt,
                        "unit": "PT",
                    }
                },
                "fields": "fontSize",
            }
        },
        {
            "updateParagraphStyle": {
                "objectId": element_id,
                "textRange": {"type": "ALL"},
                "style": {"alignment": "CENTER" if centred else "START"},
                "fields": "alignment",
            }
        },
    ]


def _to_utf16_index(text: str, index: int) -> int:
    """Convert a Python string index to a UTF-16 code unit index for Slides.

    Google Slides text indices are UTF-16 code unit offsets, whereas Python
    string indices are Unicode code point offsets. This helper converts
    from the latter to the former.
    """
    # utf-16-le has no BOM; each code unit is 2 bytes, so bytes/2 == code units
    return len(text[:index].encode("utf-16-le")) // 2


def _hyperlink_requests(element_id: str, body_text: str) -> list[dict]:
    """Return updateTextStyle requests to make URLs in *body_text* clickable hyperlinks."""
    reqs: list[dict] = []
    for m in _URL_RE.finditer(body_text):
        url = m.group(0)
        start = _to_utf16_index(body_text, m.start())
        end = _to_utf16_index(body_text, m.end())
        reqs.append(
            {
                "updateTextStyle": {
                    "objectId": element_id,
                    "textRange": {
                        "type": "FIXED_RANGE",
                        "startIndex": start,
                        "endIndex": end,
                    },
                    "style": {"link": {"url": url}},
                    "fields": "link",
                }
            }
        )
    return reqs


# ---------------------------------------------------------------------------
# YouTube helpers
# ---------------------------------------------------------------------------


def extract_youtube_ids(text: str) -> list[str]:
    """Return a list of YouTube video IDs found in *text*."""
    return [m.group("id") for m in _YOUTUBE_URL_RE.finditer(text)]


def strip_youtube_urls(text: str) -> str:
    """Remove YouTube URLs from *text* and collapse extra whitespace."""
    cleaned = _YOUTUBE_URL_RE.sub("", text)
    # Collapse whitespace left behind but preserve intentional newlines
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Fun facts generation (optional, requires GEMINI_API_KEY)
# ---------------------------------------------------------------------------

# The -latest alias tracks Google's current Flash model, so this doesn't go
# stale as models are retired.
_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"

_FUN_FACT_BULLET_RE = re.compile(r"^(?:#+|[•*\-–]|\d+[.)])\s*")
_FUN_FACT_MARKUP_RE = re.compile(r"\*\*|__|`|(?<!\w)\*|\*(?!\w)")


def clean_fun_facts(text: str) -> str:
    """Normalise Gemini's reply to plain ``• `` bullets for the title slide.

    The model is asked for plain bullets but often answers in markdown
    (``* **bold**``, ``1.``, headings), which Slides would show verbatim.
    """
    lines = []
    for line in text.splitlines():
        line = _FUN_FACT_BULLET_RE.sub("", line.strip())
        line = _FUN_FACT_MARKUP_RE.sub("", line).strip()
        if line:
            lines.append(f"• {line}")
    return "\n".join(lines)


def generate_fun_facts(
    topic: str,
    submissions: list[dict],
    conversation_messages: list[str] | None = None,
) -> str:
    """Generate fun facts about submissions using Google Gemini.

    Returns a short block of bullet points suitable for inserting into the
    title slide ``{{FUNFACTS}}`` placeholder.  If the feature is disabled
    (no ``GEMINI_API_KEY``) or the API call fails, returns an empty string
    so the placeholder is simply cleared.
    """
    if not GEMINI_API_KEY:
        return ""

    sub_texts = [sub["body"] for sub in submissions if sub.get("body")]
    if not sub_texts:
        return ""

    sub_list = "\n".join(f"- {t}" for t in sub_texts)

    prompt = (
        f'Here are anonymous submissions for a guessing game about "{topic}".\n'
        "Write 3–5 succinct bullet points about commonalities, outliers, "
        "or interesting patterns. Each bullet should be a single concise phrase or short sentence.\n"
        "Do NOT include any introductory or preamble sentence — output ONLY the bullet points.\n"
        "Do NOT mention any names or identifying information — keep everything "
        "completely anonymous so as not to give away any answers.\n"
        "Format each bullet point on its own line starting with \"• \".\n\n"
        f"Submissions:\n{sub_list}"
    )

    if conversation_messages:
        conv_list = "\n".join(f"- {m}" for m in conversation_messages)
        prompt += (
            "\n\nThe following are other messages from the conversation "
            "(discussions, disagreements about submissions, etc.). "
            "You may reference interesting discussion points but do NOT "
            "include any names:\n"
            f"{conv_list}"
        )

    url = f"{_GEMINI_URL}?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        print("[info] Fun facts generated successfully.")
        return clean_fun_facts(text)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Failed to generate fun facts via Gemini API ({exc}); placeholder will be cleared.")
        return ""


# ---------------------------------------------------------------------------
# Slides building
# ---------------------------------------------------------------------------


def _get_slide_ids(slides_svc, pres_id: str) -> list[str]:
    pres = execute_with_retry(slides_svc.presentations().get(presentationId=pres_id))
    return [s["objectId"] for s in pres.get("slides", [])]


def _find_template_slide_id(slides_svc, pres_id: str) -> str:
    """Return the objectId of the slide that contains {{AUTHOR}}."""
    pres = execute_with_retry(slides_svc.presentations().get(presentationId=pres_id))
    for slide in pres.get("slides", []):
        for elem in slide.get("pageElements", []):
            shape = elem.get("shape", {})
            for tb in shape.get("text", {}).get("textElements", []):
                content = tb.get("textRun", {}).get("content", "")
                if "{{AUTHOR}}" in content:
                    return slide["objectId"]
    raise RuntimeError("Could not find template slide ({{AUTHOR}} placeholder) in deck")


def _lay_out_submission(
    slides_svc,
    drive_svc,
    pres_id: str,
    slide_id: str,
    page_elements: list[dict],
    page_size: tuple[float, float],
    sub: dict,
    image_cache: dict[str, UploadedImage],
    err_meta: dict,
) -> list[dict]:
    """Size and place a submission slide's body text and media.

    Images are uploaded first because their shapes decide the layout.  The
    slide's text must already be in place.  Returns error dicts for any
    processing problems.
    """
    errors: list[dict] = []
    author = sub["author"]
    body_text = sub["body"]
    image_urls = sub.get("images", [])[:4]
    youtube_ids = sub.get("youtube_ids", [])

    uploaded: list[UploadedImage] = []
    if image_urls:
        results = [upload_image_to_drive(drive_svc, u, image_cache) for u in image_urls]
        failed_uploads = sum(1 for r in results if r is None)
        if failed_uploads:
            errors.append({
                "author": author,
                "issue": f"Failed to upload {failed_uploads} image(s) to Google Drive",
                **err_meta,
            })
        uploaded = [r for r in results if r is not None]
    # Embed a YouTube video only when there are no image attachments
    video_ids = youtube_ids if not image_urls else []
    aspects = [img.aspect for img in uploaded] or ([_VIDEO_ASPECT] if video_ids else [])

    content_top = _author_bottom_emu(page_elements) / _PT + _AUTHOR_GAP_PT
    layout = plan_slide_layout(*page_size, content_top, body_text, aspects)

    text_reqs = _body_resize_requests(page_elements, layout.text_box)
    body_elem = _find_body_element(page_elements)
    if body_elem and body_text:
        text_reqs.extend(_text_fit_requests(body_elem["objectId"], layout.font_pt, layout.centred))
        if _URL_RE.search(body_text):
            text_reqs.extend(_hyperlink_requests(body_elem["objectId"], body_text))
    if text_reqs:
        execute_with_retry(
            slides_svc.presentations().batchUpdate(
                presentationId=pres_id,
                body={"requests": text_reqs},
            )
        )

    if uploaded:
        img_errors = _insert_images(
            slides_svc, pres_id, slide_id, [img.url for img in uploaded],
            layout.media_boxes, author=author,
        )
        for detail in img_errors:
            errors.append({
                "author": author,
                "issue": f"Could not insert image(s) into slide: {detail}",
                **err_meta,
            })

    if video_ids and layout.media_boxes:
        try:
            execute_with_retry(
                slides_svc.presentations().batchUpdate(
                    presentationId=pres_id,
                    body={"requests": _video_requests(slide_id, video_ids, layout.media_boxes[0])},
                )
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] Could not embed YouTube video for '{author}': {str(exc) or repr(exc)}")

    return errors


def build_deck(
    slides_svc,
    drive_svc,
    pres_id: str,
    topic: str,
    submissions: list[dict],
    named: bool,
    image_cache: dict[str, str],
    fun_facts: str = "",
) -> list[dict]:
    """Populate a freshly copied presentation with submission slides.

    Returns a list of error dicts (``{"author": ..., "issue": ...}``) for any
    processing problems encountered (e.g. failed image uploads).
    """
    errors: list[dict] = []

    # --- Replace {{TOPIC}} and {{FUNFACTS}} on title slide ---
    slide_ids = _get_slide_ids(slides_svc, pres_id)
    title_slide_id = slide_ids[0]
    title_requests: list[dict] = [
        {
            "replaceAllText": {
                "containsText": {"text": "{{TOPIC}}"},
                "replaceText": topic,
                "pageObjectIds": [title_slide_id],
            }
        },
        {
            "replaceAllText": {
                "containsText": {"text": "{{FUNFACTS}}"},
                "replaceText": fun_facts,
                "pageObjectIds": [title_slide_id],
            }
        },
    ]
    execute_with_retry(
        slides_svc.presentations().batchUpdate(
            presentationId=pres_id,
            body={"requests": title_requests},
        )
    )

    template_slide_id = _find_template_slide_id(slides_svc, pres_id)

    # Build slides for each submission, then delete the original template slide
    last_inserted_id = template_slide_id
    slide_ids = _get_slide_ids(slides_svc, pres_id)
    template_index = slide_ids.index(template_slide_id)

    for i, sub in enumerate(submissions):
        author = sub["author"]
        body_text = sub["body"]

        # Duplicate the template slide
        dup_resp = execute_with_retry(
            slides_svc.presentations().batchUpdate(
                presentationId=pres_id,
                body={
                    "requests": [
                        {"duplicateObject": {"objectId": last_inserted_id}}
                    ]
                },
            )
        )
        new_slide_id = dup_resp["replies"][0]["duplicateObject"]["objectId"]

        # Move the new slide and replace placeholders in a single batch
        target_index = template_index + i + 1
        author_text = format_author_label(i + 1, author, named)
        batch_requests: list[dict] = [
            {
                "updateSlidesPosition": {
                    "slideObjectIds": [new_slide_id],
                    "insertionIndex": target_index,
                }
            },
            {
                "replaceAllText": {
                    "containsText": {"text": "{{AUTHOR}}"},
                    "replaceText": author_text,
                    "pageObjectIds": [new_slide_id],
                }
            },
            {
                "replaceAllText": {
                    "containsText": {"text": "{{BODY}}"},
                    "replaceText": body_text,
                    "pageObjectIds": [new_slide_id],
                }
            },
        ]
        execute_with_retry(
            slides_svc.presentations().batchUpdate(
                presentationId=pres_id,
                body={"requests": batch_requests},
            )
        )

        new_pres = execute_with_retry(
            slides_svc.presentations().get(presentationId=pres_id)
        )
        new_slide = next(
            s for s in new_pres["slides"] if s["objectId"] == new_slide_id
        )
        errors.extend(_lay_out_submission(
            slides_svc, drive_svc, pres_id, new_slide_id,
            new_slide.get("pageElements", []), _page_size_pt(new_pres), sub, image_cache,
            # Final slide number (1-indexed) once the template slide is removed
            err_meta={
                "slide_number": template_index + i + 1,
                "slide_id": new_slide_id,
                "message_id": sub.get("id", ""),
            },
        ))

    # Delete the original template slide
    execute_with_retry(
        slides_svc.presentations().batchUpdate(
            presentationId=pres_id,
            body={
                "requests": [{"deleteObject": {"objectId": template_slide_id}}]
            },
        )
    )

    return errors


def append_slides(
    slides_svc,
    drive_svc,
    pres_id: str,
    new_submissions: list[dict],
    named: bool,
    image_cache: dict[str, str],
    seed: int | None = None,
) -> list[dict]:
    """Add slides for new submissions to an existing deck.

    Each new slide lands at a random position among the existing submission
    slides, so late submitters aren't given away by sitting at the end.  Pass
    the same *seed* for the named and anonymous decks so the slides line up.
    Every slide is renumbered afterwards.

    Returns a list of error dicts (``{"author": ..., "issue": ...}``) for any
    processing problems encountered (e.g. failed image uploads).
    """
    errors: list[dict] = []
    rng = random.Random(seed)
    pres = execute_with_retry(
        slides_svc.presentations().get(presentationId=pres_id)
    )
    slides = pres.get("slides", [])

    # Find the last submission slide (slide before the end slide)
    # The end slide is the last slide; submission slides are everything in between
    if len(slides) < 2:
        print("[warn] existing deck has fewer slides than expected; skipping append")
        return errors

    # Use the second-to-last slide as the duplication source
    source_slide_id = slides[-2]["objectId"]
    page_size = _page_size_pt(pres)

    for i, sub in enumerate(new_submissions):
        author = sub["author"]
        body_text = sub["body"]

        # Duplicate an existing submission slide
        dup_resp = execute_with_retry(
            slides_svc.presentations().batchUpdate(
                presentationId=pres_id,
                body={
                    "requests": [
                        {"duplicateObject": {"objectId": source_slide_id}}
                    ]
                },
            )
        )
        new_slide_id = dup_resp["replies"][0]["duplicateObject"]["objectId"]

        # Get current text in the slide shape elements
        new_pres = execute_with_retry(
            slides_svc.presentations().get(presentationId=pres_id)
        )
        slide_ids = [s["objectId"] for s in new_pres["slides"]]
        current_index = slide_ids.index(new_slide_id)
        new_slide = new_pres["slides"][current_index]

        # Move to a random spot between the title slide and the end slide.
        # insertionIndex counts positions before the move, hence the +1 when
        # moving later in the deck.
        target_index = 1 + rng.randint(0, len(slide_ids) - 3)
        if target_index != current_index:
            execute_with_retry(
                slides_svc.presentations().batchUpdate(
                    presentationId=pres_id,
                    body={
                        "requests": [
                            {
                                "updateSlidesPosition": {
                                    "slideObjectIds": [new_slide_id],
                                    "insertionIndex": (
                                        target_index if target_index < current_index
                                        else target_index + 1
                                    ),
                                }
                            }
                        ]
                    },
                )
            )

        # Clear existing text elements and replace with new content

        page_elements = new_slide.get("pageElements", [])
        body_elem = _find_body_element(page_elements)
        author_elem = _find_author_element(page_elements)
        body_obj_id = body_elem["objectId"] if body_elem else None
        author_obj_id = author_elem["objectId"] if author_elem else None

        clear_requests = []
        for elem in page_elements:
            shape = elem.get("shape", {})
            if shape.get("text"):
                clear_requests.append(
                    {
                        "deleteText": {
                            "objectId": elem["objectId"],
                            "textRange": {"type": "ALL"},
                        }
                    }
                )
            elif elem.get("image"):
                clear_requests.append(
                    {"deleteObject": {"objectId": elem["objectId"]}}
                )
            elif elem.get("video"):
                clear_requests.append(
                    {"deleteObject": {"objectId": elem["objectId"]}}
                )
        if clear_requests:
            execute_with_retry(
                slides_svc.presentations().batchUpdate(
                    presentationId=pres_id,
                    body={"requests": clear_requests},
                )
            )

        # Insert new text directly into the identified shapes.
        # We use insertText rather than replaceAllText because the
        # duplicated slide contains real content (not {{AUTHOR}}/{{BODY}}
        # placeholders) and the text was cleared above.
        # Provisional number; the whole deck is renumbered once all new
        # slides are in place.
        author_text = format_author_label(target_index, author, named)
        text_requests = []
        if author_obj_id:
            text_requests.append(
                {
                    "insertText": {
                        "objectId": author_obj_id,
                        "text": author_text,
                        "insertionIndex": 0,
                    }
                }
            )
        if body_obj_id:
            text_requests.append(
                {
                    "insertText": {
                        "objectId": body_obj_id,
                        "text": body_text,
                        "insertionIndex": 0,
                    }
                }
            )
        if text_requests:
            execute_with_retry(
                slides_svc.presentations().batchUpdate(
                    presentationId=pres_id,
                    body={"requests": text_requests},
                )
            )

        errors.extend(_lay_out_submission(
            slides_svc, drive_svc, pres_id, new_slide_id, page_elements, page_size,
            sub, image_cache,
            err_meta={
                "slide_number": target_index + 1,  # 1-indexed; corrected below
                "slide_id": new_slide_id,
                "message_id": sub.get("id", ""),
            },
        ))

    slide_numbers = renumber_slides(slides_svc, pres_id, named)
    for err in errors:
        err["slide_number"] = slide_numbers.get(err["slide_id"], err["slide_number"])

    return errors


def renumber_slides(slides_svc, pres_id: str, named: bool) -> dict[str, int]:
    """Rewrite every author box so submission slides are numbered in order.

    Names are read back off the existing labels, so hand-added slides keep
    their names and old ``Answer: <name>`` labels are converted.  Returns the
    1-indexed position of every slide in the deck, keyed by slide ID.
    """
    pres = execute_with_retry(
        slides_svc.presentations().get(presentationId=pres_id)
    )
    slides = pres.get("slides", [])
    requests_list: list[dict] = []
    number = 0
    for slide in slides[1:-1]:
        author_elem = _find_author_element(slide.get("pageElements", []))
        if author_elem is None:
            continue
        text = _get_shape_text(author_elem).strip()
        name = parse_author_label(text)
        if name is None:
            continue
        number += 1
        label = format_author_label(number, name, named)
        if label == text:
            continue
        requests_list.extend([
            {
                "deleteText": {
                    "objectId": author_elem["objectId"],
                    "textRange": {"type": "ALL"},
                }
            },
            {
                "insertText": {
                    "objectId": author_elem["objectId"],
                    "text": label,
                    "insertionIndex": 0,
                }
            },
        ])
    if requests_list:
        execute_with_retry(
            slides_svc.presentations().batchUpdate(
                presentationId=pres_id,
                body={"requests": requests_list},
            )
        )
    return {slide["objectId"]: i + 1 for i, slide in enumerate(slides)}


# ---------------------------------------------------------------------------
# Results message formatting
# ---------------------------------------------------------------------------


def collect_deck_authors(slides_svc, pres_id: str) -> list[str]:
    """Return the author names written on the submission slides of a named deck.

    Slides added by hand (mod-added extras, submissions relayed from outside
    the channel) never pass through the Discord scan, so the results message
    would otherwise omit them.  Reading the names back off the named deck
    picks them up regardless of how the slide got there.

    The first slide (title) and last slide (end) are skipped; every submission
    slide carries an author box reading ``#<n> — <name>`` (or the older
    ``Answer: <name>``).  Slides whose author box is missing or carries no
    name — including the anonymous deck, where it reads just ``#<n>`` —
    contribute nothing.
    """
    pres = execute_with_retry(
        slides_svc.presentations().get(presentationId=pres_id)
    )
    slides = pres.get("slides", [])
    names: list[str] = []
    for slide in slides[1:-1]:
        author_elem = _find_author_element(slide.get("pageElements", []))
        if author_elem is None:
            continue
        name = parse_author_label(_get_shape_text(author_elem))
        if name and name not in names:
            names.append(name)
    return names


async def read_deck_authors(slides_svc, named_pres_id: str) -> list[str]:
    """Read author names off the named deck, tolerating API failures.

    The name list is a nicety on top of the deck links, so a failed Slides
    read must not stop the results message from going out.
    """
    try:
        return await asyncio.to_thread(collect_deck_authors, slides_svc, named_pres_id)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Could not read author names from the deck: {str(exc) or repr(exc)}")
        return []


def format_results_message(
    topic: str,
    submissions: list[dict],
    named_url: str,
    anon_url: str,
    deck_authors: list[str] | None = None,
) -> str:
    """Build the Discord results message.

    ``deck_authors`` are names read back off the named deck (see
    :func:`collect_deck_authors`) so that manually added slides are listed
    alongside the submissions parsed from Discord.
    """
    names = {sub["author"] for sub in submissions}
    names.update(deck_authors or [])
    sorted_names = sorted(names, key=str.lower)
    name_lines = [f"  • {name}" for name in sorted_names]

    lines = [
        f"## Guess Chat — {topic}",
        "",
        f"**Questions (anonymous):** {anon_url}",
        # Spoilered so a stray click on the wrong link doesn't ruin the game.
        f"**Answers:** ||{named_url}||",
        "",
        f"**Submissions ({len(sorted_names)}):**",
    ]
    lines.extend(name_lines)
    return "\n".join(lines)


def format_error_message(
    err: dict,
    pres_id: str,
    guild_id: int | None,
    channel_id: int,
) -> str:
    """Build one bullet line describing a processing error."""
    s_url = slide_url(pres_id, err.get("slide_id", ""))
    s_num = err.get("slide_number", "?")
    m_id = err.get("message_id", "")

    # <> around the URLs stops Discord unfurling a preview for every link.
    parts = [f"• **{err['author']}** — {err['issue']}", f"[slide {s_num}](<{s_url}>)"]
    if guild_id is not None and m_id:
        m_url = discord_message_url(guild_id, channel_id, m_id)
        parts.append(f"[message](<{m_url}>)")
    return " · ".join(parts)


_DISCORD_MESSAGE_LIMIT = 2000


def format_error_summary(
    errors: list[dict],
    pres_id: str,
    guild_id: int | None,
    channel_id: int,
) -> list[str]:
    """Build the messages reporting a run's processing errors.

    All errors go in one message under a single heading, split only when
    Discord's length limit would be exceeded.
    """
    if not errors:
        return []
    plural = "s" if len(errors) != 1 else ""
    messages = [f"⚠️ **{len(errors)} processing issue{plural}** while building the decks:"]
    for err in errors:
        line = format_error_message(err, pres_id, guild_id, channel_id)
        if len(messages[-1]) + 1 + len(line) > _DISCORD_MESSAGE_LIMIT:
            messages.append(line)
        else:
            messages[-1] += "\n" + line
    return messages


# ---------------------------------------------------------------------------
# Slash-command reply
# ---------------------------------------------------------------------------

# One-line summary of what this run did, shown in the slash-command reply.
_run_outcome: str | None = None

# The interactions endpoint ends its reply with a line starting with this;
# it is swapped for the outcome when the run finishes.
_PENDING_MARKER = "⏳"


def set_outcome(text: str) -> None:
    """Record what this run did, for the slash-command reply."""
    global _run_outcome
    _run_outcome = text


def _run_log_url() -> str | None:
    server = os.environ.get("GITHUB_SERVER_URL")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not (server and run_id and GITHUB_REPOSITORY):
        return None
    return f"{server}/{GITHUB_REPOSITORY}/actions/runs/{run_id}"


def report_to_interaction() -> None:
    """Replace the "⏳ Running…" line of the slash-command reply with the outcome.

    Discord only accepts edits for 15 minutes after the command, so a run that
    sat in the queue too long just logs a warning.
    """
    if not (INTERACTION_APP_ID and INTERACTION_TOKEN):
        return
    outcome = _run_outcome or "✅ Finished."
    log_url = _run_log_url()
    if log_url:
        outcome += f" · [run log](<{log_url}>)"
    url = (
        f"https://discord.com/api/v10/webhooks/{INTERACTION_APP_ID}/"
        f"{INTERACTION_TOKEN}/messages/@original"
    )
    # Discord's edge rejects generic HTTP-library user agents.
    headers = {"User-Agent": "DiscordBot (https://github.com/theReuben/guess-chat-bot, 1.0)"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        lines = [
            line for line in (resp.json().get("content") or "").splitlines()
            if not line.startswith(_PENDING_MARKER)
        ]
        lines.append(outcome)
        content = "\n".join(lines)[:_DISCORD_MESSAGE_LIMIT]
        requests.patch(url, headers=headers, json={"content": content}, timeout=10).raise_for_status()
        print("[info] Updated the slash-command reply.")
    except requests.RequestException as exc:
        # The exception text includes the URL, and so the token.
        status = getattr(exc.response, "status_code", None)
        print(f"[warn] Could not update the slash-command reply (HTTP {status}).")


# ---------------------------------------------------------------------------
# Mode routing
# ---------------------------------------------------------------------------

# Modes that still post when there is nothing new to add, re-showing the
# current decks rather than exiting silently.  ``slides`` is deliberately
# absent: the Friday run must stay quiet when it has nothing to say.
REPOSTING_MODES = ("preview", "test_slides", "strim")


def notice_channel_id() -> int | None:
    """Channel for mode-specific notices (deck re-posts, new-round notices)."""
    if BOT_MODE == "test_slides":
        return DISCORD_TEST_CHANNEL_ID
    if BOT_MODE == "strim":
        return DISCORD_STRIM_CHANNEL_ID
    return DISCORD_MOD_CHANNEL_ID


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


async def resolve_marker_message(
    channel,
    bot_user_id: int | None,
    override_id: str | None = None,
) -> "discord.Message | None":
    """Return the GUESS CHAT message that marks the start of the current round.

    When ``override_id`` is given that message is used directly, which lets a
    round announced by a person — in whatever wording they chose — drive the
    decks.  A message that cannot be fetched is reported and ignored, falling
    back to the normal scan rather than skipping the run.

    Otherwise the channel history is scanned for the most recent marker,
    preferring one posted by the bot itself and falling back to any author's
    so that legacy mod-posted markers keep working.
    """
    if override_id:
        try:
            msg = await channel.fetch_message(int(override_id))
        except (discord.HTTPException, ValueError) as exc:
            print(f"[warn] Could not fetch marker override message {override_id}: {exc}")
        else:
            print(f"[info] Using marker override message {override_id}.")
            return msg

    fallback_marker_msg = None
    async for msg in channel.history(limit=500):
        first_line = msg.content.split("\n", 1)[0]
        if _MARKER_LINE_RE.match(first_line):
            if bot_user_id is not None and msg.author.id == bot_user_id:
                return msg
            if fallback_marker_msg is None:
                fallback_marker_msg = msg

    if fallback_marker_msg is not None:
        print("[info] Using non-bot GUESS CHAT marker as fallback.")
    return fallback_marker_msg


def marker_topic(marker_msg, channel) -> str:
    """Return the round topic for a marker message.

    An overridden marker was written by a person and need not be worded like
    the bot's own announcement, so when the message does not parse as a
    ``GUESS CHAT`` marker the mod-set channel description is preferred.
    """
    first_line = marker_msg.content.split("\n", 1)[0]
    if _MARKER_LINE_RE.match(first_line):
        return extract_topic(marker_msg.content)
    return parse_channel_topic(getattr(channel, "topic", None)) or extract_topic(
        marker_msg.content
    )


async def generate_slides(client: discord.Client) -> None:
    state = load_state()

    # --- Fetch submissions channel ---
    channel = client.get_channel(DISCORD_CHANNEL_ID)
    if channel is None:
        print(f"[error] Could not find channel {DISCORD_CHANNEL_ID}")
        return

    # --- Find the GUESS CHAT marker for this round ---
    # An explicit override (env var this run, or one saved by a previous run)
    # wins; otherwise scan the channel history.
    marker_override = MARKER_MESSAGE_ID or state.get("marker_override_id")
    bot_user_id = client.user.id if client.user else None
    marker_msg = await resolve_marker_message(channel, bot_user_id, marker_override)

    if marker_msg is None:
        print("[info] No GUESS CHAT marker found; nothing to do.")
        set_outcome("ℹ️ No GUESS CHAT announcement found, so there's nothing to build.")
        return

    marker_id = str(marker_msg.id)
    topic = marker_topic(marker_msg, channel)

    # --- Collect SUBMISSION messages and conversation after the marker ---
    all_submissions: list[dict] = []
    conversation_messages: list[str] = []
    _member_cache: dict[int, discord.Member | None] = {}
    async for msg in channel.history(limit=1000, after=marker_msg):
        sub_match = _SUBMISSION_RE.match(msg.content)
        if sub_match:
            body = sub_match.group(2).strip()
            images = [a.url for a in msg.attachments if a.content_type and a.content_type.startswith("image/")]
            # Resolve the guild Member to get the server-specific display name (nickname).
            # channel.history() uses the REST API which does not reliably include partial
            # member data, so msg.author may be a User (no nick). We use get_member() for
            # a cache hit and fall back to fetch_member() (a REST call that works without
            # the privileged members intent) to get the server-level display name.
            uid = msg.author.id
            if uid not in _member_cache and msg.guild is not None:
                member: discord.Member | None = msg.guild.get_member(uid)
                if member is None:
                    try:
                        member = await msg.guild.fetch_member(uid)
                    except discord.HTTPException:
                        member = None
                    # Yield control and pace API calls to avoid rate-limits
                    await asyncio.sleep(0.25)
                _member_cache[uid] = member
            cached_member = _member_cache.get(uid)
            author_name = cached_member.display_name if cached_member is not None else msg.author.display_name
            youtube_ids = extract_youtube_ids(body)
            if youtube_ids:
                body = strip_youtube_urls(body)
            all_submissions.append(
                {
                    "id": str(msg.id),
                    "author": author_name,
                    "body": body,
                    "images": images,
                    "youtube_ids": youtube_ids,
                }
            )
        elif msg.content.strip():
            # Collect non-submission messages as conversation context
            # (anonymised — no author names stored).
            conversation_messages.append(msg.content.strip())

    if not all_submissions:
        # In the re-posting modes, re-post the existing deck links from state so
        # that the pipeline can always be verified.  However, if the marker has
        # changed (new round), the old decks are stale — notify the mod channel
        # about the new topic instead of re-posting irrelevant links.
        prev_marker_id = state.get("marker_id")
        is_new_round = prev_marker_id != marker_id
        if BOT_MODE in REPOSTING_MODES and state.get("named_pres_id") and not is_new_round:
            print("[info] No SUBMISSION messages found — re-posting existing deck links.")
            named_pres_id = state["named_pres_id"]
            anon_pres_id = state["anon_pres_id"]
            post_topic = state.get("topic", topic)
            repost_channel_id = notice_channel_id()
            if repost_channel_id is not None:
                post_channel = client.get_channel(repost_channel_id)
                if post_channel is not None:
                    named_url = presentation_url(named_pres_id)
                    anon_url = presentation_url(anon_pres_id)
                    # Every name in this re-post comes off the deck itself,
                    # including any slides added by hand since the last run.
                    slides_svc, _ = await asyncio.to_thread(get_google_services)
                    deck_authors = await read_deck_authors(slides_svc, named_pres_id)
                    msg_text = format_results_message(
                        post_topic, [], named_url, anon_url, deck_authors,
                    )
                    posted = await post_channel.send(msg_text)
                    print("[info] Posted results to channel.")
                    set_outcome(f"✅ No new submissions; re-posted the current decks: {posted.jump_url}")
            return
        if BOT_MODE in REPOSTING_MODES and is_new_round:
            print(f"[info] New round detected (topic: '{topic}') but no submissions yet.")
            notify_channel_id = notice_channel_id()
            if notify_channel_id is not None:
                notify_channel = client.get_channel(notify_channel_id)
                if notify_channel is not None:
                    await notify_channel.send(
                        f"New Guess Chat round detected: **{topic}**\n"
                        f"No submissions yet — will generate slides once submissions arrive."
                    )
                    print("[info] Posted new-round notice to channel.")
            set_outcome(f"ℹ️ **{topic}** has no submissions yet.")
            return
        print("[info] No SUBMISSION messages found after the marker.")
        set_outcome(f"ℹ️ **{topic}** has no submissions yet.")
        return

    # Keep only the latest submission per author
    seen_authors: dict[str, int] = {}
    for i, sub in enumerate(all_submissions):
        seen_authors[sub["author"]] = i
    all_submissions = [all_submissions[i] for i in sorted(seen_authors.values())]

    slides_svc, drive_svc = await asyncio.to_thread(get_google_services)

    prev_marker_id = state.get("marker_id")
    named_pres_id = state.get("named_pres_id")
    anon_pres_id = state.get("anon_pres_id")
    processed_ids: set[str] = set(state.get("processed_ids", []))

    new_round = prev_marker_id != marker_id

    if new_round:
        print(f"[info] New round detected (marker {marker_id}); creating fresh decks.")
        # Delete previous round's decks and images to free Drive quota
        if named_pres_id:
            await asyncio.to_thread(delete_drive_file, drive_svc, named_pres_id)
        if anon_pres_id:
            await asyncio.to_thread(delete_drive_file, drive_svc, anon_pres_id)
        await asyncio.to_thread(delete_old_images, drive_svc)
        await asyncio.to_thread(empty_trash, drive_svc)
        try:
            named_pres_id = await asyncio.to_thread(copy_presentation_with_quota_retry, drive_svc, f"Guess Chat — {topic} (Named)")
            anon_pres_id = await asyncio.to_thread(copy_presentation_with_quota_retry, drive_svc, f"Guess Chat — {topic} (Anonymous)")
        except StorageQuotaExceededError:
            print("[error] Google Drive storage quota exceeded — cannot create new decks.")
            notify_channel = None
            if BOT_MODE == "test_slides" and DISCORD_TEST_CHANNEL_ID is not None:
                notify_channel = client.get_channel(DISCORD_TEST_CHANNEL_ID)
            elif DISCORD_MOD_CHANNEL_ID is not None:
                notify_channel = client.get_channel(DISCORD_MOD_CHANNEL_ID)
            if notify_channel is not None:
                await notify_channel.send(
                    "❌ **Google Drive storage quota exceeded** — the bot cannot create "
                    "new slide decks until space is freed up or the storage plan is upgraded."
                )
            set_outcome("❌ Google Drive storage is full, so new decks couldn't be created.")
            return
        await asyncio.to_thread(share_presentation, drive_svc, named_pres_id)
        await asyncio.to_thread(share_presentation, drive_svc, anon_pres_id)
        processed_ids = set()

    new_submissions = [s for s in all_submissions if s["id"] not in processed_ids]

    if not new_submissions:
        if BOT_MODE not in REPOSTING_MODES:
            print("[info] No new submissions since last run; nothing to do.")
            set_outcome("ℹ️ No new submissions since the last run; nothing posted.")
            return
        print("[info] No new submissions — will still post current results.")

    errors: list[dict] = []  # populated below only when slides are built/appended

    if new_submissions:
        image_cache: dict[str, str] = {}

        if new_round:
            fun_facts = await asyncio.to_thread(
                generate_fun_facts, topic, all_submissions, conversation_messages,
            )
            print(f"[info] Building decks for {len(all_submissions)} submission(s).")
            # Shuffle so the slide order doesn't give away who posted first.
            # Both decks share the one order so the answers line up.
            deck_order = random.sample(all_submissions, len(all_submissions))
            errors = await asyncio.to_thread(build_deck, slides_svc, drive_svc, named_pres_id, topic, deck_order, named=True, image_cache=image_cache, fun_facts=fun_facts)
            await asyncio.to_thread(build_deck, slides_svc, drive_svc, anon_pres_id, topic, deck_order, named=False, image_cache=image_cache, fun_facts=fun_facts)
        else:
            print(f"[info] Adding {len(new_submissions)} new submission(s) to existing decks.")
            seed = random.randrange(2**32)
            errors = await asyncio.to_thread(append_slides, slides_svc, drive_svc, named_pres_id, new_submissions, named=True, image_cache=image_cache, seed=seed)
            await asyncio.to_thread(append_slides, slides_svc, drive_svc, anon_pres_id, new_submissions, named=False, image_cache=image_cache, seed=seed)

        # Update processed IDs
        for sub in new_submissions:
            processed_ids.add(sub["id"])

    # Post results
    # In preview mode the message goes to the mod channel for a sanity check
    # before the public Friday post; in test_slides mode it goes to the test
    # channel; in strim mode it goes to the stream channel where the round is
    # played; in normal slides mode it goes to the public results channel.
    if BOT_MODE == "strim":
        if DISCORD_STRIM_CHANNEL_ID is None:
            print("[error] strim mode requires DISCORD_STRIM_CHANNEL_ID to be set; skipping post.")
            post_channel = None
        else:
            post_channel = client.get_channel(DISCORD_STRIM_CHANNEL_ID)
            if post_channel is None:
                print(f"[error] Could not find strim channel {DISCORD_STRIM_CHANNEL_ID}")
    elif BOT_MODE == "test_slides":
        if DISCORD_TEST_CHANNEL_ID is None:
            print("[error] test_slides mode requires DISCORD_TEST_CHANNEL_ID to be set; skipping post.")
            post_channel = None
        else:
            post_channel = client.get_channel(DISCORD_TEST_CHANNEL_ID)
            if post_channel is None:
                print(f"[error] Could not find test channel {DISCORD_TEST_CHANNEL_ID}")
    elif BOT_MODE == "preview":
        if DISCORD_MOD_CHANNEL_ID is None:
            print("[error] Preview mode requires DISCORD_MOD_CHANNEL_ID to be set; skipping post.")
            post_channel = None
        else:
            post_channel = client.get_channel(DISCORD_MOD_CHANNEL_ID)
            if post_channel is None:
                print(f"[error] Could not find mod channel {DISCORD_MOD_CHANNEL_ID}")
    else:
        post_channel = client.get_channel(DISCORD_RESULTS_CHANNEL_ID)
        if post_channel is None:
            print(f"[error] Could not find results channel {DISCORD_RESULTS_CHANNEL_ID}")

    deck_authors: list[str] = []
    if post_channel is None:
        set_outcome("❌ Built the decks but couldn't find the channel to post them in.")
    else:
        named_url = presentation_url(named_pres_id)
        anon_url = presentation_url(anon_pres_id)
        # Read the names back off the named deck so that slides added by hand
        # since the last run are listed alongside the Discord submissions.
        deck_authors = await read_deck_authors(slides_svc, named_pres_id)
        msg_text = format_results_message(
            topic, all_submissions, named_url, anon_url, deck_authors,
        )
        posted = await post_channel.send(msg_text)
        print("[info] Posted results message.")
        if new_submissions:
            outcome = f"✅ Added {len(new_submissions)} new submission(s) and posted the decks: {posted.jump_url}"
        else:
            outcome = f"✅ No new submissions; re-posted the current decks: {posted.jump_url}"
        if errors:
            outcome += f"\n⚠️ {len(errors)} processing issue(s); details sent to the mods."
        set_outcome(outcome)

        # Send error notifications for processing issues
        # In test_slides mode all notifications go to the test channel.
        error_channel = None
        if BOT_MODE == "test_slides":
            error_channel = post_channel
        elif DISCORD_MOD_CHANNEL_ID is not None:
            error_channel = client.get_channel(DISCORD_MOD_CHANNEL_ID)
            if error_channel is None:
                print(f"[warn] Could not find mod channel {DISCORD_MOD_CHANNEL_ID}; falling back to post channel")
        if error_channel is None:
            error_channel = post_channel
        guild_id = channel.guild.id if channel.guild else None
        for text in format_error_summary(errors, named_pres_id, guild_id, DISCORD_CHANNEL_ID):
            await error_channel.send(text)
        if errors:
            print(f"[info] Sent {len(errors)} error notification(s).")

    # Persist state (preserve keys written by other modes, e.g. last_announced_topic)
    # In test_slides mode, skip persistence so test runs don't affect production state.
    if BOT_MODE == "test_slides":
        print("[info] test_slides mode — skipping state persistence.")
        return
    prev_state = load_state()
    prev_state.update({
        "marker_id": marker_id,
        "topic": topic,
        "named_pres_id": named_pres_id,
        "anon_pres_id": anon_pres_id,
        "processed_ids": list(processed_ids),
        "submitters": sorted(
            {sub["author"] for sub in all_submissions} | set(deck_authors), key=str.lower,
        ),
        "last_run": {"mode": BOT_MODE, "at": int(time.time())},
    })
    if MARKER_MESSAGE_ID:
        # Remember the override so the rest of the round's scheduled runs use
        # the same marker without needing the env var set again.  Cleared when
        # the bot posts its own announcement for the next round.
        prev_state["marker_override_id"] = MARKER_MESSAGE_ID
    state = prev_state
    await asyncio.to_thread(save_state, state)
    print("[info] State saved.")


# ---------------------------------------------------------------------------
# Channel-description announcement flow
# ---------------------------------------------------------------------------


async def adopt_manual_announcement(client: discord.Client, submissions_channel) -> None:
    """Record a human-posted message as this round's GUESS CHAT announcement.

    Used when ``MARKER_MESSAGE_ID`` is set, i.e. someone announced the round
    themselves.  The bot posts nothing, saves the message ID so that the
    slides run builds the decks from it, and tells the mod channel which
    message it adopted.
    """
    try:
        marker_msg = await submissions_channel.fetch_message(int(MARKER_MESSAGE_ID))
    except (discord.HTTPException, ValueError) as exc:
        print(f"[error] Could not fetch marker override message {MARKER_MESSAGE_ID}: {exc}")
        set_outcome(f"❌ Couldn't fetch message `{MARKER_MESSAGE_ID}` from the submissions channel.")
        return

    topic = marker_topic(marker_msg, submissions_channel)
    set_outcome(f"✅ Using the existing announcement for **{topic}**: {marker_msg.jump_url}")
    print(f"[info] Adopting message {MARKER_MESSAGE_ID} as the announcement for '{topic}'.")

    if BOT_MODE == "test_announce":
        confirm_channel_id = DISCORD_TEST_CHANNEL_ID
    else:
        confirm_channel_id = DISCORD_MOD_CHANNEL_ID
    if confirm_channel_id is not None:
        confirm_channel = client.get_channel(confirm_channel_id)
        if confirm_channel is not None:
            guild = getattr(submissions_channel, "guild", None)
            mod_mention = _resolve_mod_mention(guild)
            lines = [
                f"{mod_mention} Using the existing announcement for **{topic}** "
                f"— I won't post my own.",
                "Are there any extras we should add?",
            ]
            guild_id = guild.id if guild is not None else None
            if guild_id is not None:
                lines.append(
                    discord_message_url(guild_id, DISCORD_CHANNEL_ID, str(marker_msg.id))
                )
            await confirm_channel.send("\n".join(lines))
            print("[info] Sent confirmation.")
        else:
            print(f"[warn] Could not find channel {confirm_channel_id}")

    if BOT_MODE == "test_announce":
        print("[info] test_announce mode — skipping state persistence.")
        return
    state = load_state()
    state["last_announced_topic"] = topic
    state["marker_override_id"] = MARKER_MESSAGE_ID
    state["deadline_ts"] = next_friday_deadline_unix()
    await asyncio.to_thread(save_state, state)
    print("[info] State saved (manual announcement adopted).")


async def check_mod_and_announce(client: discord.Client) -> None:
    """Read the submissions channel description for a new Guess Chat topic and post the announcement.

    The channel description is expected to follow the format
    ``Current Guess Chat: <topic>``.  If the topic differs from the last
    announced topic (stored in state), the bot posts a ``GUESS CHAT <topic>``
    message in the submissions channel.  If the topic is unchanged, a reminder
    is sent to the mod channel.

    Setting ``MARKER_MESSAGE_ID`` short-circuits all of this: the round was
    announced by someone else, so that message is adopted instead.
    """
    # --- Read the submissions channel description ---
    submissions_channel = client.get_channel(DISCORD_CHANNEL_ID)
    if submissions_channel is None:
        print(f"[error] Could not find submissions channel {DISCORD_CHANNEL_ID}")
        return

    if MARKER_MESSAGE_ID:
        await adopt_manual_announcement(client, submissions_channel)
        return

    description = getattr(submissions_channel, "topic", "") or ""
    topic = parse_channel_topic(description)
    if topic is None:
        print("[info] Channel description does not contain a Guess Chat topic; nothing to do.")
        set_outcome(
            "ℹ️ The submissions channel description has no "
            "`Current Guess Chat: <topic>` line, so nothing was announced."
        )
        return

    # --- Check whether this topic has already been announced ---
    state = load_state()
    if topic == state.get("last_announced_topic"):
        print("[info] Topic unchanged; already announced.")
        set_outcome(f"ℹ️ **{topic}** was already announced, so the mods were sent a reminder instead.")
        # Send a reminder asking if there's a new topic.
        # In test_announce mode the reminder goes to the test channel.
        if BOT_MODE == "test_announce":
            reminder_channel_id = DISCORD_TEST_CHANNEL_ID
        else:
            reminder_channel_id = DISCORD_MOD_CHANNEL_ID
        if reminder_channel_id is not None:
            reminder_channel = client.get_channel(reminder_channel_id)
            if reminder_channel is not None:
                guild = getattr(submissions_channel, "guild", None)
                mod_mention = _resolve_mod_mention(guild)
                await reminder_channel.send(
                    f"{mod_mention} we haven't announced a new guess chat yet, is there a new one this week?"
                )
                print("[info] Sent reminder about missing new topic.")
            else:
                print(f"[warn] Could not find channel {reminder_channel_id}")
        return

    # --- Post the GUESS CHAT announcement ---
    # In test_announce mode the announcement goes to the test channel so it
    # doesn't pollute the real submissions channel.
    if BOT_MODE == "test_announce":
        if DISCORD_TEST_CHANNEL_ID is None:
            print("[error] test_announce mode requires DISCORD_TEST_CHANNEL_ID to be set; skipping.")
            return
        announce_channel = client.get_channel(DISCORD_TEST_CHANNEL_ID)
        announce_channel_id = DISCORD_TEST_CHANNEL_ID
        if announce_channel is None:
            print(f"[error] Could not find test channel {DISCORD_TEST_CHANNEL_ID}")
            return
    else:
        announce_channel = submissions_channel
        announce_channel_id = DISCORD_CHANNEL_ID
    deadline_ts = next_friday_deadline_unix()
    posted_msg = await announce_channel.send(build_announcement_message(topic, deadline_ts))
    print(f"[info] Posted GUESS CHAT announcement for topic '{topic}'.")
    set_outcome(f"✅ Announced **{topic}**: {posted_msg.jump_url}")

    # --- Send confirmation ---
    # In test_announce mode the confirmation also goes to the test channel.
    if BOT_MODE == "test_announce":
        confirm_channel_id = DISCORD_TEST_CHANNEL_ID
    else:
        confirm_channel_id = DISCORD_MOD_CHANNEL_ID
    if confirm_channel_id is not None:
        confirm_channel = client.get_channel(confirm_channel_id)
        if confirm_channel is not None:
            guild = getattr(submissions_channel, "guild", None)
            mod_mention = _resolve_mod_mention(guild)
            guild_id = guild.id if guild is not None else None
            if guild_id is not None:
                msg_url = discord_message_url(guild_id, announce_channel_id, str(posted_msg.id))
                await confirm_channel.send(
                    f"{mod_mention} New Guess Chat theme: **{topic}**\n"
                    f"Are there any extras we should add?\n"
                    f"{msg_url}"
                )
            else:
                await confirm_channel.send(
                    f"{mod_mention} New Guess Chat theme: **{topic}**\n"
                    f"Are there any extras we should add?"
                )
            print("[info] Sent confirmation.")
        else:
            print(f"[warn] Could not find channel {confirm_channel_id}")

    # Persist the announced topic to avoid re-announcing.
    # In test_announce mode, skip persistence so test runs don't affect production state.
    if BOT_MODE == "test_announce":
        print("[info] test_announce mode — skipping state persistence.")
        return
    state["last_announced_topic"] = topic
    state["deadline_ts"] = deadline_ts
    # The bot has announced this round itself, so any override saved for the
    # previous round no longer applies.
    state.pop("marker_override_id", None)
    await asyncio.to_thread(save_state, state)
    print("[info] State saved (announcement tracked).")


# ---------------------------------------------------------------------------
# Discord client
# ---------------------------------------------------------------------------


class OneShotClient(discord.Client):
    async def on_ready(self) -> None:
        print(f"[info] Logged in as {self.user}")
        try:
            if BOT_MODE in ("announce", "test_announce"):
                await check_mod_and_announce(self)
            elif BOT_MODE in ("slides", "preview", "test_slides", "strim"):
                await generate_slides(self)
            else:
                print(f"[warn] Unknown BOT_MODE '{BOT_MODE}'; proceeding with generate_slides.")
                await generate_slides(self)
        except Exception as exc:
            print(f"[error] Unhandled exception in on_ready: {exc}")
            set_outcome(f"❌ The run failed: {str(exc) or type(exc).__name__}")
            await asyncio.to_thread(create_github_issue, exc)
            raise
        finally:
            await asyncio.to_thread(report_to_interaction)
            await self.close()


def main() -> None:
    if INTERACTION_TOKEN:
        # Keep the token out of the public Actions logs, e.g. in tracebacks.
        print(f"::add-mask::{INTERACTION_TOKEN}")
    intents = discord.Intents.default()
    intents.message_content = True
    client = OneShotClient(intents=intents)
    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
