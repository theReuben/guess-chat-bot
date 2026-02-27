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

import io
import json
import os
import re
from pathlib import Path
from typing import Any

import discord
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
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

SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive",
]

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
# Google API helpers
# ---------------------------------------------------------------------------


def get_google_services():
    creds = service_account.Credentials.from_service_account_file(
        GOOGLE_CREDS_FILE, scopes=SCOPES
    )
    slides = build("slides", "v1", credentials=creds)
    drive = build("drive", "v3", credentials=creds)
    return slides, drive


def copy_presentation(drive_svc, title: str) -> str:
    """Copy the template deck and return the new presentation ID."""
    body: dict[str, Any] = {"name": title}
    if DRIVE_FOLDER_ID:
        body["parents"] = [DRIVE_FOLDER_ID]
    result = drive_svc.files().copy(fileId=TEMPLATE_DECK_ID, body=body).execute()
    return result["id"]


def share_presentation(drive_svc, file_id: str) -> None:
    """Share presentation as anyone-with-link viewer."""
    drive_svc.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
        fields="id",
    ).execute()


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
    file_obj = (
        drive_svc.files()
        .create(body=meta, media_body=media, fields="id")
        .execute()
    )
    file_id = file_obj["id"]

    # Make the file publicly readable so Slides API can fetch it
    drive_svc.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
        fields="id",
    ).execute()

    public_url = f"https://drive.google.com/uc?id={file_id}&export=download"
    cache[url] = public_url
    return public_url


# ---------------------------------------------------------------------------
# Image grid layout
# ---------------------------------------------------------------------------

# 2×2 grid on the right half of a slide (positions in EMU: 1pt = 12700 EMU)
_PT = 12700  # EMU per point


def _image_requests(slide_id: str, image_urls: list[str]) -> list[dict]:
    """Return createImage requests for up to 4 images in a 2×2 grid."""
    requests_list = []
    for idx, img_url in enumerate(image_urls[:4]):
        col = idx % 2
        row = idx // 2
        left_pt = 400 + col * 170
        top_pt = 60 + row * 195
        requests_list.append(
            {
                "createImage": {
                    "url": img_url,
                    "elementProperties": {
                        "pageObjectId": slide_id,
                        "size": {
                            "width": {"magnitude": 160 * _PT, "unit": "EMU"},
                            "height": {"magnitude": 185 * _PT, "unit": "EMU"},
                        },
                        "transform": {
                            "scaleX": 1,
                            "scaleY": 1,
                            "translateX": left_pt * _PT,
                            "translateY": top_pt * _PT,
                            "unit": "EMU",
                        },
                    },
                }
            }
        )
    return requests_list


# ---------------------------------------------------------------------------
# Slides building
# ---------------------------------------------------------------------------


def _get_slide_ids(slides_svc, pres_id: str) -> list[str]:
    pres = slides_svc.presentations().get(presentationId=pres_id).execute()
    return [s["objectId"] for s in pres.get("slides", [])]


def _find_template_slide_id(slides_svc, pres_id: str) -> str:
    """Return the objectId of the slide that contains {{AUTHOR}}."""
    pres = slides_svc.presentations().get(presentationId=pres_id).execute()
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
    ).execute()

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
        dup_resp = (
            slides_svc.presentations()
            .batchUpdate(
                presentationId=pres_id,
                body={
                    "requests": [
                        {"duplicateObject": {"objectId": last_inserted_id}}
                    ]
                },
            )
            .execute()
        )
        new_slide_id = dup_resp["replies"][0]["duplicateObject"]["objectId"]

        # Move the new slide to position right after the template
        target_index = template_index + i + 1
        slides_svc.presentations().batchUpdate(
            presentationId=pres_id,
            body={
                "requests": [
                    {
                        "updateSlidesPosition": {
                            "slideObjectIds": [new_slide_id],
                            "insertionIndex": target_index,
                        }
                    }
                ]
            },
        ).execute()

        # Replace placeholders on the new slide
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
        slides_svc.presentations().batchUpdate(
            presentationId=pres_id,
            body={"requests": text_requests},
        ).execute()

        # Insert images
        if image_urls:
            drive_urls = [
                upload_image_to_drive(drive_svc, u, image_cache)
                for u in image_urls[:4]
            ]
            drive_urls = [u for u in drive_urls if u]
            if drive_urls:
                slides_svc.presentations().batchUpdate(
                    presentationId=pres_id,
                    body={"requests": _image_requests(new_slide_id, drive_urls)},
                ).execute()

    # Delete the original template slide
    slides_svc.presentations().batchUpdate(
        presentationId=pres_id,
        body={
            "requests": [{"deleteObject": {"objectId": template_slide_id}}]
        },
    ).execute()


def append_slides(
    slides_svc,
    drive_svc,
    pres_id: str,
    new_submissions: list[dict],
    named: bool,
    image_cache: dict[str, str],
) -> None:
    """Append slides for new submissions to an existing deck."""
    pres = slides_svc.presentations().get(presentationId=pres_id).execute()
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
        dup_resp = (
            slides_svc.presentations()
            .batchUpdate(
                presentationId=pres_id,
                body={
                    "requests": [
                        {"duplicateObject": {"objectId": source_slide_id}}
                    ]
                },
            )
            .execute()
        )
        new_slide_id = dup_resp["replies"][0]["duplicateObject"]["objectId"]

        # Move before the end slide
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
        ).execute()

        # Clear existing text elements and replace with new content
        # Get current text in the slide shape elements
        new_pres = (
            slides_svc.presentations().get(presentationId=pres_id).execute()
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
        if clear_requests:
            slides_svc.presentations().batchUpdate(
                presentationId=pres_id,
                body={"requests": clear_requests},
            ).execute()

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
        slides_svc.presentations().batchUpdate(
            presentationId=pres_id,
            body={"requests": text_requests},
        ).execute()

        # Insert images
        if image_urls:
            drive_urls = [
                upload_image_to_drive(drive_svc, u, image_cache)
                for u in image_urls[:4]
            ]
            drive_urls = [u for u in drive_urls if u]
            if drive_urls:
                slides_svc.presentations().batchUpdate(
                    presentationId=pres_id,
                    body={"requests": _image_requests(new_slide_id, drive_urls)},
                ).execute()


# ---------------------------------------------------------------------------
# Results message formatting
# ---------------------------------------------------------------------------


def format_results_message(
    topic: str,
    submissions: list[dict],
    named_url: str,
    anon_url: str,
) -> str:
    submitter_counts: dict[str, int] = {}
    for sub in submissions:
        submitter_counts[sub["author"]] = submitter_counts.get(sub["author"], 0) + 1

    sorted_names = sorted(submitter_counts.keys(), key=str.lower)
    name_lines = []
    for name in sorted_names:
        count = submitter_counts[name]
        if count > 1:
            name_lines.append(f"  • {name} (×{count})")
        else:
            name_lines.append(f"  • {name}")

    total = len(submissions)
    unique = len(submitter_counts)

    lines = [
        f"## Guess Chat — {topic}",
        "",
        f"**Questions (anonymous):** {anon_url}",
        f"**Answers:** ||{named_url}||",
        "",
        f"**Submissions ({total} total, {unique} unique submitters):**",
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
    async for msg in channel.history(limit=1000, after=marker_msg):
        if msg.content.upper().startswith(SUBMISSION_PREFIX):
            body = msg.content[len(SUBMISSION_PREFIX):].strip()
            images = [a.url for a in msg.attachments if a.content_type and a.content_type.startswith("image/")]
            all_submissions.append(
                {
                    "id": str(msg.id),
                    "author": msg.author.display_name,
                    "body": body,
                    "images": images,
                }
            )

    if not all_submissions:
        print("[info] No SUBMISSION messages found after the marker.")
        return

    slides_svc, drive_svc = get_google_services()

    prev_marker_id = state.get("marker_id")
    named_pres_id = state.get("named_pres_id")
    anon_pres_id = state.get("anon_pres_id")
    processed_ids: set[str] = set(state.get("processed_ids", []))

    new_round = prev_marker_id != marker_id

    if new_round:
        print(f"[info] New round detected (marker {marker_id}); creating fresh decks.")
        named_pres_id = copy_presentation(drive_svc, f"Guess Chat — {topic} (Named)")
        anon_pres_id = copy_presentation(drive_svc, f"Guess Chat — {topic} (Anonymous)")
        share_presentation(drive_svc, named_pres_id)
        share_presentation(drive_svc, anon_pres_id)
        processed_ids = set()

    new_submissions = [s for s in all_submissions if s["id"] not in processed_ids]

    if not new_submissions:
        print("[info] No new submissions since last run; nothing to do.")
        return

    image_cache: dict[str, str] = {}

    if new_round:
        print(f"[info] Building decks for {len(all_submissions)} submission(s).")
        build_deck(slides_svc, drive_svc, named_pres_id, topic, all_submissions, named=True, image_cache=image_cache)
        build_deck(slides_svc, drive_svc, anon_pres_id, topic, all_submissions, named=False, image_cache=image_cache)
    else:
        print(f"[info] Appending {len(new_submissions)} new submission(s) to existing decks.")
        append_slides(slides_svc, drive_svc, named_pres_id, new_submissions, named=True, image_cache=image_cache)
        append_slides(slides_svc, drive_svc, anon_pres_id, new_submissions, named=False, image_cache=image_cache)

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
    save_state(state)
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
