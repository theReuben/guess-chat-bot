# Copilot Instructions

## Repository Overview

This is **Guess Chat Bot** — a one-shot Python Discord bot that:

1. Scans a Discord submissions channel for a `GUESS CHAT <topic>` marker message.
2. Collects subsequent `SUBMISSION <text>` messages (with optional image attachments).
3. Generates two Google Slides decks from a template — one **named** (answers revealed) and one **anonymous** (for guessing).
4. Shares both decks publicly and posts the links to a Discord results channel.
5. Persists round state to `state.json` (stored on an orphan `state` git branch) for incremental updates.

The workflow runs automatically every Friday via GitHub Actions and can also be triggered manually.

## Tech Stack

- **Language**: Python 3.10+
- **Discord library**: `discord.py` ≥ 2.3 (async, `discord.Client` subclass)
- **Google APIs**: `google-api-python-client` for Slides v1 and Drive v3; `google-auth` for OAuth2 credentials
- **HTTP**: `requests` for downloading Discord image attachments before re-uploading to Drive
- **CI/CD**: GitHub Actions (`.github/workflows/weekly-slides.yml`)
- **Testing**: `pytest` with `unittest.mock` and `pytest-asyncio` for async tests

## Key Files

| File | Purpose |
|---|---|
| `weekly_slides_bot.py` | Single-file main bot script — all logic lives here |
| `requirements.txt` | Runtime Python dependencies |
| `.env.example` | Template showing required environment variables |
| `.github/workflows/weekly-slides.yml` | Scheduled + manual GitHub Actions workflow |
| `tests/test_cleanup.py` | Unit tests for Drive file deletion helpers |
| `tests/test_display_name.py` | Tests for Discord member display-name resolution |

## Architecture Notes

- **All bot logic is in `weekly_slides_bot.py`** — there are no sub-modules. Keep changes surgical and in this single file unless a new module is clearly warranted.
- `OneShotClient` subclasses `discord.Client`; `on_ready` triggers `generate_slides()` and then closes the client.
- `generate_slides()` is the core async function that orchestrates Discord reading, Google API calls, and state management.
- Google credentials are loaded from a JSON file (path in `GOOGLE_CREDS_FILE` env var, default `service_account.json`). In CI the file is written from the `GOOGLE_OAUTH_TOKEN` secret.
- State is a simple JSON dict with keys: `marker_id`, `topic`, `named_pres_id`, `anon_pres_id`, `processed_ids`.

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | Discord bot token |
| `DISCORD_CHANNEL_ID` | ✅ | Submissions channel ID (int) |
| `DISCORD_RESULTS_CHANNEL_ID` | ✅ | Results channel ID (int) |
| `TEMPLATE_DECK_ID` | ✅ | Google Slides template presentation ID |
| `GOOGLE_CREDS_FILE` | ❌ | Path to OAuth2 token JSON (default: `service_account.json`) |
| `DRIVE_FOLDER_ID` | ❌ | Google Drive folder ID for generated decks |
| `STATE_FILE` | ❌ | Path to state JSON file (default: `state.json`) |

## Coding Conventions

- Use `from __future__ import annotations` at the top of every Python file.
- Type-annotate all function signatures; use `dict`, `list`, `set` (lowercase) for built-in generics, and `Any` from `typing` where needed.
- Keep helper functions small and focused. Group related helpers with a comment banner like `# --- Section name ---`.
- `print()` is used for logging — prefix with `[info]`, `[warn]`, or `[error]`.
- Catch broad exceptions only at call-site boundaries (e.g. network/API calls), comment them with `# noqa: BLE001`.
- Do not commit secrets, credentials, or `state.json` — all are excluded by `.gitignore`.

## Testing

- Tests live in the `tests/` directory and are discovered automatically by `pytest`.
- Set required environment variables with `os.environ.setdefault(...)` **before** importing `weekly_slides_bot` in any test file.
- Use `unittest.mock.MagicMock` for Google API service objects and Discord objects.
- Use `pytest-asyncio` and `@pytest.mark.asyncio` for async test methods.
- Run tests locally:
  ```bash
  pip install -r requirements.txt pytest pytest-asyncio
  pytest tests/
  ```

## Running Locally

```bash
cp .env.example .env
# Fill in real values in .env
export $(grep -v '^#' .env | xargs)
python weekly_slides_bot.py
```

## DST / Timezone Handling

The workflow uses two cron triggers to handle UK daylight saving time:
- `30 10 * * 5` → 10:30 UTC = 11:30 BST (summer)
- `30 11 * * 5` → 11:30 UTC = 11:30 GMT (winter)

A DST guard step checks `TZ='Europe/London' date +%H` and skips the run if the UK hour is not `11`, preventing double-runs on clocks-change Fridays.
