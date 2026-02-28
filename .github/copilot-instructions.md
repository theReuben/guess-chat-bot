# Copilot Instructions

## Project Overview

**Guess Chat Bot** is a one-shot Discord bot written in Python that automates a weekly "Guess Chat" game. It:

1. Scans a Discord submissions channel for a `GUESS CHAT <topic>` marker message.
2. Collects all `SUBMISSION <text>` messages (with optional image attachments) posted after the marker.
3. Builds two Google Slides presentations from a template deck — one **named** (answers revealed) and one **anonymous** (for guessing).
4. Shares both presentations publicly and posts a summary message in a Discord results channel.
5. Persists state to `state.json` (on an orphan `state` branch in GitHub) for incremental updates across runs.

The bot runs on a **GitHub Actions** schedule (every Friday at 11:30 AM UK time) and also supports manual `workflow_dispatch` triggers with an optional `force_reset` flag.

---

## Tech Stack

- **Language**: Python 3.10+
- **Key libraries** (`requirements.txt`):
  - `discord.py>=2.3` — Discord API client
  - `google-api-python-client>=2.100` — Google Slides & Drive APIs
  - `google-auth>=2.23` — OAuth2 credentials
  - `requests>=2.31` — HTTP downloads (for image re-upload)
- **Testing**: `pytest` with `pytest-asyncio` for async tests; `unittest.mock` for all external service mocking
- **CI/CD**: GitHub Actions (`.github/workflows/weekly-slides.yml`)

---

## Repository Structure

```
guess-chat-bot/
├── .env.example                    # Environment variable template (never commit .env)
├── .gitignore                      # Excludes secrets, state.json, build artifacts
├── .github/
│   ├── copilot-instructions.md     # This file
│   └── workflows/
│       └── weekly-slides.yml       # Scheduled + manual GitHub Actions workflow
├── README.md
├── requirements.txt
├── tests/
│   ├── __init__.py
│   ├── test_cleanup.py             # Tests for delete_drive_file
│   └── test_display_name.py       # Tests for intents config and author display name
└── weekly_slides_bot.py            # Entire bot logic (single module)
```

---

## Environment Variables

All configuration comes from environment variables (see `.env.example`):

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | Yes | Discord bot token |
| `DISCORD_CHANNEL_ID` | Yes | Submissions channel ID (int) |
| `DISCORD_RESULTS_CHANNEL_ID` | Yes | Results channel ID (int) |
| `TEMPLATE_DECK_ID` | Yes | Google Slides template presentation ID |
| `GOOGLE_CREDS_FILE` | No | Path to OAuth token JSON (default: `service_account.json`) |
| `DRIVE_FOLDER_ID` | No | Google Drive folder ID for generated decks |
| `STATE_FILE` | No | Path to state JSON file (default: `state.json`) |

---

## Coding Conventions

- **Single-module design**: all bot logic lives in `weekly_slides_bot.py`. Keep it that way unless the module exceeds a clearly unmanageable size.
- **Type hints**: use standard Python type hints (`list[dict]`, `str | None`, etc.); use `from __future__ import annotations` at the top of every file.
- **`Any` imports**: import `Any` from `typing` for Google API response dicts.
- **Error handling**: catch broad `Exception` only in helper utilities (e.g. `delete_drive_file`, `upload_image_to_drive`) where graceful degradation is intentional. Add `# noqa: BLE001` to suppress linter warnings on those bare-exception catches.
- **Print-based logging**: use `[info]`, `[warn]`, and `[error]` prefixes — no external logging library.
- **Discord intents**: only `message_content` is requested. Do **not** add `members` or other privileged intents unless absolutely necessary and documented.
- **No secrets in code**: credentials and tokens are always read from environment variables or external files listed in `.gitignore`.

---

## Testing

Run tests with:

```bash
pip install -r requirements.txt pytest pytest-asyncio
pytest tests/
```

Tests use `unittest.mock` (`MagicMock`, `AsyncMock`, `patch`) to mock all Discord and Google API calls. External environment variables are set via `os.environ.setdefault(...)` at the top of each test file **before** importing `weekly_slides_bot`.

When adding new features:
- Mock all external services (Discord client, Google Slides/Drive service objects).
- Test both the happy path and graceful error handling.
- Use `@pytest.mark.asyncio` for any `async` test functions.

---

## Running Locally

```bash
cp .env.example .env
# Edit .env with real values
export $(grep -v '^#' .env | xargs)
python weekly_slides_bot.py
```

The bot connects to Discord, processes the current round, then disconnects (one-shot pattern via `OneShotClient`).

---

## Key Concepts

- **Round detection**: a new round is detected when the most recent `GUESS CHAT` marker message ID differs from the `marker_id` stored in `state.json`. New rounds create fresh presentation copies; the same round appends slides incrementally.
- **Deduplication**: only the **latest** submission per author is kept (`seen_authors` dict keyed by `display_name`).
- **Image handling**: Discord CDN URLs are short-lived. Images are downloaded via `requests` and re-uploaded to Google Drive as publicly readable files before being inserted into slides.
- **Template slides**: the Google Slides template must have exactly 3 slides — title (`{{TOPIC}}`), submission template (`{{AUTHOR}}` and `{{BODY}}`), and a static end slide.
- **State persistence**: `state.json` is committed to an orphan `state` branch by the workflow after each successful run; it is excluded from the main branch by `.gitignore`.
