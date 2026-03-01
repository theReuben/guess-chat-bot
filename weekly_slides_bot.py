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
import io
import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any

import discord
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
DISCORD_CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])
DISCORD_RESULTS_CHANNEL_ID = int(os.environ["DISCORD_RESULTS_CHANNEL_ID"])
GOOGLE_CREDS_FILE = os.environ.get("GOOGLE_CREDS_FILE", "service_account.json")
DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID")
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
TEMPLATE_DECK_ID = os.environ["TEMPLATE_DECK_ID"]

MARKER_PREFIX = "GUESS CHAT"
SUBMISSION_PREFIX = "SUBMISSION"

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
# Google API retry helper
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS_CODES = (429, 500, 502, 503)


def execute_with_retry(request, max_retries: int = 5):
    """Execute a Google API request with exponential backoff on transient errors."""
    for attempt in range(max_retries + 1):
        try:
            return request.execute()
        except HttpError as exc:
            status = exc.resp.status
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


# ---------------------------------------------------------------------------
# Google API helpers
# ---------------------------------------------------------------------------


def get_google_services():
    with open(GOOGLE_CREDS_FILE) as f:
        token_data = json.load(f)
    creds = Credentials(
        token=None,
        refresh_token=token_data["refresh_token"],
        client_id=token_data["client_id"],
        client_secret=token_data["client_secret"],
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
    )
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


def share_presentation(drive_svc, file_id: str) -> None:
    """Share presentation as anyone-with-link viewer."""
    execute_with_retry(
        drive_svc.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
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


def presentation_url(pres_id: str) -> str:
    return f"https://docs.google.com/presentation/d/{pres_id}/edit?usp=sharing"


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------


def upload_image_to_drive(drive_svc, url: str, cache: dict[str, str]) -> str | None:
    """Download image from *url* and upload to Drive; returns a public URL."""
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
    file_obj = execute_with_retry(
        drive_svc.files().create(body=meta, media_body=media, fields="id")
    )
    file_id = file_obj["id"]

    # Make the file publicly readable so Slides API can fetch it
    execute_with_retry(
        drive_svc.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            fields="id",
        )
    )

    public_url = f"https://drive.google.com/uc?id={file_id}&export=download"
    cache[url] = public_url
    return public_url


# ---------------------------------------------------------------------------
# Image grid layout
# ---------------------------------------------------------------------------

# Image layout constants (all in points; multiply by _PT to get EMU)
_PT = 12700          # EMU per point
_SLIDE_W_PT = 720    # standard 16:9 slide width in points
_SLIDE_H_PT = 405    # standard 16:9 slide height in points
_IMG_MARGIN_PT = 36  # slide edge margin used for image placement
_AUTHOR_BAR_PT = 55  # height reserved for the author label at the top
_BODY_Y_TOLERANCE_PT = 5  # tolerance when matching the body element Y position


def _image_requests(slide_id: str, image_urls: list[str], has_text: bool = True) -> list[dict]:
    """Return createImage requests for up to 4 images in a 1–2 column grid.

    When *has_text* is True the images are placed in the right portion of the
    slide to leave room for the body text on the left.  When *has_text* is
    False (image-only submission) the images fill the full available slide area.
    """
    urls = image_urls[:4]
    if not urls:
        return []

    n_cols = min(len(urls), 2)
    n_rows = (len(urls) + n_cols - 1) // n_cols
    gap = 8  # points between images

    if has_text:
        area_x = 400
        area_y = _AUTHOR_BAR_PT
        area_w = _SLIDE_W_PT - area_x - _IMG_MARGIN_PT   # ≈ 284 pt
        area_h = _SLIDE_H_PT - area_y - _IMG_MARGIN_PT   # ≈ 314 pt
    else:
        area_x = _IMG_MARGIN_PT
        area_y = _AUTHOR_BAR_PT
        area_w = _SLIDE_W_PT - 2 * _IMG_MARGIN_PT        # ≈ 648 pt
        area_h = _SLIDE_H_PT - area_y - _IMG_MARGIN_PT   # ≈ 314 pt

    img_w = (area_w - gap * (n_cols - 1)) // n_cols
    img_h = (area_h - gap * (n_rows - 1)) // n_rows

    requests_list = []
    for idx, img_url in enumerate(urls):
        col = idx % n_cols
        row = idx // n_cols
        left = area_x + col * (img_w + gap)
        top = area_y + row * (img_h + gap)
        requests_list.append(
            {
                "createImage": {
                    "url": img_url,
                    "elementProperties": {
                        "pageObjectId": slide_id,
                        "size": {
                            "width": {"magnitude": img_w * _PT, "unit": "EMU"},
                            "height": {"magnitude": img_h * _PT, "unit": "EMU"},
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": left * _PT,
                            "translateY": top * _PT,
                            "unit": "EMU",
                        },
                    },
                }
            }
        )
    return requests_list


def _find_body_element(page_elements: list[dict]) -> dict | None:
    """Return the body text box element (largest shape below the author bar)."""
    candidates = [
        elem for elem in page_elements
        if elem.get("shape")
        and elem.get("transform", {}).get("translateY", 0) / _PT >= _AUTHOR_BAR_PT - _BODY_Y_TOLERANCE_PT
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda e: (
            e.get("size", {}).get("width", {}).get("magnitude", 0)
            * e.get("size", {}).get("height", {}).get("magnitude", 0)
        ),
    )


def _body_resize_requests(page_elements: list[dict], has_images: bool) -> list[dict]:
    """Return updatePageElementTransform requests to resize the body text box.

    When *has_images* is False (text-only submission) the body text box is
    expanded to fill the full available content area of the slide.  When
    *has_images* is True the template layout is unchanged and an empty list is
    returned.
    """
    if has_images:
        return []
    elem = _find_body_element(page_elements)
    if elem is None:
        return []

    area_x = _IMG_MARGIN_PT * _PT
    area_y = _AUTHOR_BAR_PT * _PT
    area_w = (_SLIDE_W_PT - 2 * _IMG_MARGIN_PT) * _PT
    area_h = (_SLIDE_H_PT - _AUTHOR_BAR_PT - _IMG_MARGIN_PT) * _PT

    elem_w = elem["size"]["width"]["magnitude"]
    elem_h = elem["size"]["height"]["magnitude"]

    return [
        {
            "updatePageElementTransform": {
                "objectId": elem["objectId"],
                "transform": {
                    "scaleX": area_w / elem_w,
                    "scaleY": area_h / elem_h,
                    "translateX": area_x,
                    "translateY": area_y,
                    "unit": "EMU",
                },
                "applyMode": "ABSOLUTE",
            }
        }
    ]


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


def build_deck(
    slides_svc,
    drive_svc,
    pres_id: str,
    topic: str,
    submissions: list[dict],
    named: bool,
    image_cache: dict[str, str],
) -> None:
    """Populate a freshly copied presentation with submission slides."""

    # --- Replace {{TOPIC}} on title slide ---
    slide_ids = _get_slide_ids(slides_svc, pres_id)
    title_slide_id = slide_ids[0]
    execute_with_retry(
        slides_svc.presentations().batchUpdate(
            presentationId=pres_id,
            body={
                "requests": [
                    {
                        "replaceAllText": {
                            "containsText": {"text": "{{TOPIC}}"},
                            "replaceText": topic,
                            "pageObjectIds": [title_slide_id],
                        }
                    }
                ]
            },
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
        image_urls = sub.get("images", [])

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
        author_text = f"Answer: {author}" if named else "Answer:"
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

        # Resize body text box for text-only submissions
        if not image_urls:
            new_pres = execute_with_retry(
                slides_svc.presentations().get(presentationId=pres_id)
            )
            new_slide = next(
                s for s in new_pres["slides"] if s["objectId"] == new_slide_id
            )
            resize_reqs = _body_resize_requests(new_slide.get("pageElements", []), False)
            if resize_reqs:
                execute_with_retry(
                    slides_svc.presentations().batchUpdate(
                        presentationId=pres_id,
                        body={"requests": resize_reqs},
                    )
                )

        # Insert images
        if image_urls:
            drive_urls = [
                upload_image_to_drive(drive_svc, u, image_cache)
                for u in image_urls[:4]
            ]
            drive_urls = [u for u in drive_urls if u]
            if drive_urls:
                execute_with_retry(
                    slides_svc.presentations().batchUpdate(
                        presentationId=pres_id,
                        body={"requests": _image_requests(new_slide_id, drive_urls, has_text=bool(body_text))},
                    )
                )

    # Delete the original template slide
    execute_with_retry(
        slides_svc.presentations().batchUpdate(
            presentationId=pres_id,
            body={
                "requests": [{"deleteObject": {"objectId": template_slide_id}}]
            },
        )
    )


def append_slides(
    slides_svc,
    drive_svc,
    pres_id: str,
    new_submissions: list[dict],
    named: bool,
    image_cache: dict[str, str],
) -> None:
    """Append slides for new submissions to an existing deck."""
    pres = execute_with_retry(
        slides_svc.presentations().get(presentationId=pres_id)
    )
    slides = pres.get("slides", [])

    # Find the last submission slide (slide before the end slide)
    # The end slide is the last slide; submission slides are everything in between
    if len(slides) < 2:
        print("[warn] existing deck has fewer slides than expected; skipping append")
        return

    # Use the second-to-last slide as the duplication source
    source_slide_id = slides[-2]["objectId"]
    end_slide_id = slides[-1]["objectId"]
    insert_before_index = len(slides) - 1  # before end slide

    for i, sub in enumerate(new_submissions):
        author = sub["author"]
        body_text = sub["body"]
        image_urls = sub.get("images", [])

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

        # Move before the end slide
        execute_with_retry(
            slides_svc.presentations().batchUpdate(
                presentationId=pres_id,
                body={
                    "requests": [
                        {
                            "updateSlidesPosition": {
                                "slideObjectIds": [new_slide_id],
                                "insertionIndex": insert_before_index + i,
                            }
                        }
                    ]
                },
            )
        )

        # Clear existing text elements and replace with new content
        # Get current text in the slide shape elements
        new_pres = execute_with_retry(
            slides_svc.presentations().get(presentationId=pres_id)
        )
        new_slide = next(
            s for s in new_pres["slides"] if s["objectId"] == new_slide_id
        )

        clear_requests = []
        for elem in new_slide.get("pageElements", []):
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
        # Resize body text box for text-only submissions
        resize_reqs = _body_resize_requests(
            new_slide.get("pageElements", []), has_images=bool(image_urls)
        )
        all_clear_reqs = clear_requests + resize_reqs
        if all_clear_reqs:
            execute_with_retry(
                slides_svc.presentations().batchUpdate(
                    presentationId=pres_id,
                    body={"requests": all_clear_reqs},
                )
            )

        # Set new text
        author_text = f"Answer: {author}" if named else "Answer:"
        text_requests = [
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
                body={"requests": text_requests},
            )
        )

        # Insert images
        if image_urls:
            drive_urls = [
                upload_image_to_drive(drive_svc, u, image_cache)
                for u in image_urls[:4]
            ]
            drive_urls = [u for u in drive_urls if u]
            if drive_urls:
                execute_with_retry(
                    slides_svc.presentations().batchUpdate(
                        presentationId=pres_id,
                        body={"requests": _image_requests(new_slide_id, drive_urls, has_text=bool(body_text))},
                    )
                )


# ---------------------------------------------------------------------------
# Results message formatting
# ---------------------------------------------------------------------------


def format_results_message(
    topic: str,
    submissions: list[dict],
    named_url: str,
    anon_url: str,
) -> str:
    sorted_names = sorted({sub["author"] for sub in submissions}, key=str.lower)
    name_lines = [f"  • {name}" for name in sorted_names]

    lines = [
        f"## Guess Chat — {topic}",
        "",
        f"**Questions (anonymous):** {anon_url}",
        f"**Answers:** {named_url}",
        "",
        f"**Submissions ({len(submissions)}):**",
    ]
    lines.extend(name_lines)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


async def generate_slides(client: discord.Client) -> None:
    state = load_state()

    # --- Fetch submissions channel ---
    channel = client.get_channel(DISCORD_CHANNEL_ID)
    if channel is None:
        print(f"[error] Could not find channel {DISCORD_CHANNEL_ID}")
        return

    # --- Find the most recent GUESS CHAT marker ---
    marker_msg = None
    async for msg in channel.history(limit=500):
        if msg.content.upper().startswith(MARKER_PREFIX):
            marker_msg = msg
            break

    if marker_msg is None:
        print("[info] No GUESS CHAT marker found; nothing to do.")
        return

    marker_id = str(marker_msg.id)
    # Extract topic: everything after "GUESS CHAT " (case-insensitive)
    topic_match = re.match(rf"{MARKER_PREFIX}\s+(.*)", marker_msg.content, re.IGNORECASE)
    topic = topic_match.group(1).strip() if topic_match else "Unknown"

    # --- Collect SUBMISSION messages after the marker ---
    all_submissions: list[dict] = []
    _member_cache: dict[int, discord.Member | None] = {}
    async for msg in channel.history(limit=1000, after=marker_msg):
        if msg.content.upper().startswith(SUBMISSION_PREFIX):
            body = msg.content[len(SUBMISSION_PREFIX):].strip()
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
            all_submissions.append(
                {
                    "id": str(msg.id),
                    "author": author_name,
                    "body": body,
                    "images": images,
                }
            )

    if not all_submissions:
        print("[info] No SUBMISSION messages found after the marker.")
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
        named_pres_id = await asyncio.to_thread(copy_presentation, drive_svc, f"Guess Chat — {topic} (Named)")
        anon_pres_id = await asyncio.to_thread(copy_presentation, drive_svc, f"Guess Chat — {topic} (Anonymous)")
        await asyncio.to_thread(share_presentation, drive_svc, named_pres_id)
        await asyncio.to_thread(share_presentation, drive_svc, anon_pres_id)
        processed_ids = set()

    new_submissions = [s for s in all_submissions if s["id"] not in processed_ids]

    if not new_submissions:
        print("[info] No new submissions since last run; nothing to do.")
        return

    image_cache: dict[str, str] = {}

    if new_round:
        print(f"[info] Building decks for {len(all_submissions)} submission(s).")
        await asyncio.to_thread(build_deck, slides_svc, drive_svc, named_pres_id, topic, all_submissions, named=True, image_cache=image_cache)
        await asyncio.to_thread(build_deck, slides_svc, drive_svc, anon_pres_id, topic, all_submissions, named=False, image_cache=image_cache)
    else:
        print(f"[info] Appending {len(new_submissions)} new submission(s) to existing decks.")
        await asyncio.to_thread(append_slides, slides_svc, drive_svc, named_pres_id, new_submissions, named=True, image_cache=image_cache)
        await asyncio.to_thread(append_slides, slides_svc, drive_svc, anon_pres_id, new_submissions, named=False, image_cache=image_cache)

    # Update processed IDs
    for sub in new_submissions:
        processed_ids.add(sub["id"])

    # Post results
    results_channel = client.get_channel(DISCORD_RESULTS_CHANNEL_ID)
    if results_channel is None:
        print(f"[error] Could not find results channel {DISCORD_RESULTS_CHANNEL_ID}")
    else:
        named_url = presentation_url(named_pres_id)
        anon_url = presentation_url(anon_pres_id)
        msg_text = format_results_message(topic, all_submissions, named_url, anon_url)
        await results_channel.send(msg_text)
        print("[info] Posted results message.")

    # Persist state
    state = {
        "marker_id": marker_id,
        "topic": topic,
        "named_pres_id": named_pres_id,
        "anon_pres_id": anon_pres_id,
        "processed_ids": list(processed_ids),
    }
    await asyncio.to_thread(save_state, state)
    print("[info] State saved.")


# ---------------------------------------------------------------------------
# Discord client
# ---------------------------------------------------------------------------


class OneShotClient(discord.Client):
    async def on_ready(self) -> None:
        print(f"[info] Logged in as {self.user}")
        try:
            await generate_slides(self)
        finally:
            await self.close()


def main() -> None:
    intents = discord.Intents.default()
    intents.message_content = True
    client = OneShotClient(intents=intents)
    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
