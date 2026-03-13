# Guess Chat Bot

A Discord bot that scans a submissions channel for **GUESS CHAT** rounds, generates two Google Slides presentations (named and anonymous) from a template deck, and posts the links in a results channel.

---

## Overview

When a mod updates the submissions channel description to `Current Guess Chat: <topic>`, the bot detects the new topic and posts a `GUESS CHAT <topic>` announcement. Players reply with their `SUBMISSION <text>` messages (optionally attaching images). The bot:

1. Finds the latest marker and collects all submissions after it.
2. Copies a Google Slides template twice — one **named** deck (answers revealed) and one **anonymous** deck (for guessing).
3. Fills each deck with one slide per submission, inserting the player's answer and any images.
4. Shares both decks publicly ("anyone with the link can view").
5. Posts a summary message in the results channel with the anonymous link and the named link.

---

## Features

- **Round detection** — detects new rounds by tracking the marker message ID in `state.json`.
- **Channel-description announcement** — reads the channel description for the current topic and posts a `GUESS CHAT` marker automatically.
- **Mod channel confirmation** — after posting a new announcement, sends a confirmation to the mod channel with `@Mods`, the new theme, a link to the posted message, and asks whether there are any extras to add.
- **Friday reminder** — if the topic hasn't changed by the Friday run, sends a reminder to the mod channel asking if there's a new guess chat this week.
- **Error routing** — processing errors (e.g. image upload failures) are sent to the mod channel when configured, falling back to the results channel.
- **Image support** — Discord attachment images are re-uploaded to Google Drive (to avoid CDN link expiration) and placed in a 2×2 grid on each slide.
- **YouTube video embedding** — YouTube links in submissions are detected and embedded as playable videos on the slide (first video only; used when no image attachments are present).
- **Clickable hyperlinks** — URLs in submission text are automatically converted to clickable hyperlinks on the slides.
- **Markdown-tolerant detection** — `GUESS CHAT` and `SUBMISSION` prefixes are recognised even with leading markdown formatting (headings, bold, italic), e.g. `# GUESS CHAT` or `**SUBMISSION**`.
- **Display name resolution** — the bot fetches each submitter's guild member profile to use their server nickname (`display_name`) instead of their username.
- **Incremental updates** — if the bot runs again in the same round, it appends only the new submissions.
- **Duplicate prevention** — processed message IDs are stored in state; only the latest submission per author is kept.
- **Auto-posting** — posts results directly to a Discord channel.
- **API retry with backoff** — transient Google API errors (429, 500, 503) are retried with exponential backoff.
- **Scheduled runs** — GitHub Actions triggers every Friday at 11:30 AM UK time (handles BST/GMT automatically).
- **Manual trigger** — run from the GitHub Actions UI with an optional `force_reset` to start a fresh round.
- **Fun facts generation** *(optional)* — uses Google Gemini to generate 3–5 fun bullet points about submission commonalities, outliers, and patterns. Inserted into the `{{FUNFACTS}}` placeholder on the title slide. Enabled by setting the `GEMINI_API_KEY` environment variable; disabled (placeholder cleared) when the key is absent.
- **Automatic GitHub issue creation** *(optional)* — when an unhandled exception occurs during a bot run, a GitHub issue is automatically created with the traceback, bot mode, and timestamp. Duplicate issues are detected and skipped. Enabled by setting `GITHUB_TOKEN` and `GITHUB_REPOSITORY` environment variables (both are automatically available in GitHub Actions).

---

## How the Game Works

1. A mod updates the submissions channel description to `Current Guess Chat: <topic>` (e.g. `Current Guess Chat: DnD Characters`).
2. The bot detects the new topic and posts a `GUESS CHAT <topic>` announcement in the submissions channel.
3. The bot sends a confirmation message to the mod channel with `@Mods`, the new theme, a link to the posted message, and asks if there are any extras to add.
4. Players reply with `SUBMISSION <their answer>`, optionally attaching images.
5. The bot runs on Friday and generates the two decks.
6. The results channel receives a message with:
   - A link to the **anonymous** deck (everyone can guess).
   - A link to the **named** deck (answers revealed).
7. If the channel description hasn't changed by Friday, the bot sends a reminder to the mod channel.

---

## Prerequisites

- **Python 3.10+**
- A **Discord bot** with the Message Content intent enabled.
- A **Google Cloud project** with the Slides API and Drive API enabled.
- An **OAuth2 Desktop App** client ID with a refresh token (see setup below).

---

## Setup

### Discord Bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create a new application.
2. Navigate to **Bot**, enable **Message Content Intent** under *Privileged Gateway Intents*.
3. Under **OAuth2 → URL Generator**, select scopes: `bot` only.
4. Select permissions: `View Channels`, `Read Message History`, `Send Messages`.
5. Use the generated URL to invite the bot to your server.
6. Copy the bot token — this is your `DISCORD_TOKEN`.
7. Enable **Developer Mode** in Discord (User Settings → Advanced), then right-click the submissions channel and results channel to copy their IDs (`DISCORD_CHANNEL_ID` and `DISCORD_RESULTS_CHANNEL_ID`).

### Google Cloud

1. Go to the [Google Cloud Console](https://console.cloud.google.com/) and create or select a project.
2. Enable the **Google Slides API** and **Google Drive API**.

#### OAuth2 User Credentials

3. Go to **APIs & Services → OAuth consent screen**, configure it (External type), and add your Google account as a test user.
4. Go to **APIs & Services → Credentials**, click **Create Credentials → OAuth client ID**, choose **Desktop app**, and download the JSON as `oauth_client.json`.
5. Generate a refresh token by running an OAuth flow (e.g. using `google-auth-oauthlib`'s `InstalledAppFlow`) with the scopes `https://www.googleapis.com/auth/presentations` and `https://www.googleapis.com/auth/drive`. Save the resulting token JSON (containing `client_id`, `client_secret`, `refresh_token`, and `token_uri`) as `oauth_token.json`.
6. Create a folder in Google Drive to store the generated decks. Note the folder ID from the URL (`DRIVE_FOLDER_ID`).

> **Note:** OAuth2 refresh tokens for apps in "Testing" mode expire after 7 days. To prevent expiry the scheduled workflow includes a Monday preview run that refreshes the token automatically (Thursday → Monday = 4 days, Monday → Thursday = 3 days). If you still see `RefreshError: Token has been expired or revoked`, re-run the OAuth flow and update the `GOOGLE_OAUTH_TOKEN` secret.

### Gemini API Key *(optional — for fun facts generation)*

The bot can automatically generate fun facts about each round's submissions using the **Google Gemini** LLM. This feature is **optional** — if no key is set, the `{{FUNFACTS}}` placeholder is simply cleared.

1. Go to [Google AI Studio](https://aistudio.google.com/apikey).
2. Sign in with your Google account.
3. Click **Create API key** and select (or create) a Google Cloud project.
4. Copy the generated key — this is your `GEMINI_API_KEY`.
5. Add it as a GitHub Actions secret (see [GitHub Secrets](#github-secrets) below) or to your `.env` file for local runs.

> **Cost:** The Gemini API free tier allows **15 requests per minute** and **1,500 requests per day** for `gemini-2.0-flash` — more than enough for this bot, which makes one request per round. There is no charge unless you explicitly upgrade to a paid plan. See the [Gemini API pricing page](https://ai.google.dev/pricing) for current limits.

### Template Deck

1. Create a new Google Slides presentation with **3 slides**:
   - **Slide 1 (Title)**: add a text box containing `{{TOPIC}}` — this will be replaced with the round topic. Optionally add a text box containing `{{FUNFACTS}}` — this will be filled with LLM-generated fun facts about the submissions (requires `GEMINI_API_KEY`; cleared if the feature is disabled).
   - **Slide 2 (Submission template)**: add text boxes containing `{{AUTHOR}}` and `{{BODY}}` — these are replaced for each submission; this slide is duplicated once per submission.
   - **Slide 3 (End)**: a static closing slide — no modifications.
2. The presentation should already be accessible under your Google account (the one used for OAuth).
3. Copy the presentation ID from the URL (`TEMPLATE_DECK_ID`).

### GitHub Repository Setup

Push all the files to your repository:

```
guess-chat-bot/
├── .env.example
├── .gitignore
├── .github/
│   └── workflows/
│       └── weekly-slides.yml
├── README.md
├── requirements.txt
├── weekly_slides_bot.py
└── oauth_token.json        ← not committed (in .gitignore)
```

### GitHub Secrets

Add the following secrets to your repository (**Settings → Secrets and variables → Actions**):

| Secret | Description |
|---|---|
| `DISCORD_TOKEN` | Discord bot token |
| `DISCORD_CHANNEL_ID` | Submissions channel ID |
| `DISCORD_RESULTS_CHANNEL_ID` | Results channel ID |
| `DISCORD_MOD_CHANNEL_ID` | *(optional)* Mod channel ID — used for confirmations, reminders, and error notifications |
| `DRIVE_FOLDER_ID` | Google Drive folder ID for generated decks |
| `TEMPLATE_DECK_ID` | Google Slides template presentation ID |
| `GOOGLE_OAUTH_TOKEN` | OAuth2 token JSON with `client_id`, `client_secret`, `refresh_token`, and `token_uri` |
| `GEMINI_API_KEY` | *(optional)* Google Gemini API key — enables automatic fun facts generation on the title slide |

The following environment variables are set automatically by the workflow or have sensible defaults. Override them in `.env` when running locally:

| Variable | Default | Description |
|---|---|---|
| `BOT_MODE` | `slides` | `slides` to generate decks, `announce` to post the GUESS CHAT marker and mod confirmation |
| `MOD_ROLE_NAME` | `Mod` | Discord role name used to identify moderators |
| `GITHUB_TOKEN` | *(set by Actions)* | GitHub token — enables automatic issue creation on unhandled errors |
| `GITHUB_REPOSITORY` | *(set by Actions)* | Repository in `owner/repo` format — used with `GITHUB_TOKEN` for issue creation |

---

## How to Run

### Automatic (Scheduled)

The workflow runs every **Friday at 11:30 AM UK time** (slides mode) and again at **6:00 PM UK time** (announce mode). Preview runs on **Monday and Thursday at 9:00 PM UK time** send results to the mod channel — the Monday run also keeps the OAuth refresh token alive (tokens in "Testing" mode expire after 7 days). Two cron expressions per mode handle the clocks-change:

- `30 10 * * 5` — 10:30 UTC = 11:30 BST (slides, summer)
- `30 11 * * 5` — 11:30 UTC = 11:30 GMT (slides, winter)
- `0 17 * * 5` — 17:00 UTC = 18:00 BST (announce, summer)
- `0 18 * * 5` — 18:00 UTC = 18:00 GMT (announce, winter)
- `0 20 * * 1` — 20:00 UTC = 21:00 BST (Monday preview, summer)
- `0 21 * * 1` — 21:00 UTC = 21:00 GMT (Monday preview, winter)
- `0 20 * * 4` — 20:00 UTC = 21:00 BST (Thursday preview, summer)
- `0 21 * * 4` — 21:00 UTC = 21:00 GMT (Thursday preview, winter)

At the start of each run the workflow reads the current UK time and sets the appropriate mode, skipping the run for any other time to prevent double-runs during DST change weekends.

> **Why doesn't the bot run exactly at the given times?**
> GitHub Actions scheduled workflows are not guaranteed to start at the exact cron time — jobs can be delayed by minutes to (rarely) tens of minutes during periods of high runner demand. The slides cron fires at `:30` past the hour, so even a modest delay can push the clock past the hour boundary (e.g., 11:30 BST → 12:05 BST). To keep the bot running despite such delays the DST guard accepts **UK hour 11 or UK hour 12 with minute < 30** for slides mode. This 30-minute grace window still correctly rejects the "wrong" DST-transition cron, which always fires at `:30` past the *next* hour (12:30 BST/GMT). The announce and preview crons fire at `:00`, so any delay up to 59 minutes stays within the expected UK hour and no extra tolerance is needed.

### Manual Trigger

1. Go to **Actions → Weekly Slides** in GitHub.
2. Click **Run workflow**.
3. The default mode is **preview**, which sends results to the mod channel for testing.
4. Set `force_reset` to `true` to wipe saved state and create brand-new decks even if the marker hasn't changed.

### Running Locally

```bash
# Copy and fill in values
cp .env.example .env
# (edit .env with real values)

# Export env vars
export $(grep -v '^#' .env | xargs)

# Run
python weekly_slides_bot.py
```

---

## State Persistence

State is stored in `state.json` (excluded from the main branch by `.gitignore`) and persisted across runs on an **orphan `state` branch** in the same repository.

The workflow:
1. Fetches `state.json` from the `state` branch before running.
2. Runs the bot (which may update `state.json`).
3. Commits the updated `state.json` back to the `state` branch.

`state.json` contains:

```json
{
  "marker_id": "1234567890123456789",
  "topic": "DnD Characters",
  "named_pres_id": "abc123...",
  "anon_pres_id":  "xyz789...",
  "processed_ids": ["111", "222", "333"],
  "last_announced_topic": "DnD Characters"
}
```

To reset state manually, delete or empty `state.json` on the `state` branch, or trigger the workflow with `force_reset = true`.

---

## Round Detection Logic

| Scenario | Behaviour |
|---|---|
| New `GUESS CHAT` marker (different ID) | Creates fresh decks, resets processed IDs |
| Same marker + new `SUBMISSION` messages | Appends new slides to existing decks |
| Same marker + no new submissions | Exits early, nothing posted (in **preview** mode the existing deck links are still posted to the mod channel) |
| No `GUESS CHAT` marker found | Exits early, nothing posted |

---

## Discord Results Message Format

```
## Guess Chat — DnD Characters

**Questions (anonymous):** https://docs.google.com/presentation/d/.../edit?usp=sharing
**Answers:** https://docs.google.com/presentation/d/.../edit?usp=sharing

**Submissions (5 total, 4 unique submitters):**
  • Alice
  • Bob (×2)
  • Charlie
  • Diana
```

---

## DST / Timezone Handling

The UK observes **BST (UTC+1)** from late March to late October and **GMT (UTC+0)** otherwise. GitHub Actions cron uses UTC, so two cron triggers per mode are used:

- **Slides — Summer**: `30 10 * * 5` fires at 10:30 UTC = 11:30 BST.
- **Slides — Winter**: `30 11 * * 5` fires at 11:30 UTC = 11:30 GMT.
- **Announce — Summer**: `0 17 * * 5` fires at 17:00 UTC = 18:00 BST.
- **Announce — Winter**: `0 18 * * 5` fires at 18:00 UTC = 18:00 GMT.
- **Preview (Mon) — Summer**: `0 20 * * 1` fires at 20:00 UTC = 21:00 BST.
- **Preview (Mon) — Winter**: `0 21 * * 1` fires at 21:00 UTC = 21:00 GMT.
- **Preview (Thu) — Summer**: `0 20 * * 4` fires at 20:00 UTC = 21:00 BST.
- **Preview (Thu) — Winter**: `0 21 * * 4` fires at 21:00 UTC = 21:00 GMT.

On clocks-change days, both crons for the same mode fire. The DST guard at the start of the job reads `TZ='Europe/London' date +%H` and `+%M` and applies the following logic:

| Condition | Action |
|---|---|
| UK hour = 11, **or** UK hour = 12 and minute < 30 | Run in **slides** mode |
| UK hour = 18 | Run in **announce** mode |
| UK hour = 21 | Run in **preview** mode |
| Anything else | Skip (log "skipping this scheduled run") |

The slides guard uses a 30-minute grace window (12:00–12:29 UK) to survive GitHub Actions scheduling delays. The "wrong" DST-transition cron for slides always lands at exactly `:30` past the next hour, so it is still rejected (12:30 BST or 12:30 GMT fails `minute < 30`). Announce and preview crons fire at `:00`, so any delay under 60 minutes stays within the expected hour without needing an extra buffer.

---

## Cost

Running on GitHub Actions free tier: **$0/month**. Each run takes under a minute. The optional Gemini fun-facts feature uses the free tier of the Gemini API (up to 1,500 requests/day for `gemini-2.0-flash`), so there is no additional cost.

---

## Testing

Run the test suite with:

```bash
pip install -r requirements.txt pytest pytest-asyncio
pytest tests/
```

Tests use `unittest.mock` (`MagicMock`, `AsyncMock`, `patch`) to mock all Discord and Google API calls — no real credentials are needed.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Bot can't find the channel | Ensure Message Content Intent is enabled and the bot has been invited with `View Channels` + `Read Message History` permissions |
| `KeyError: DISCORD_TOKEN` | Set the required environment variable or GitHub secret |
| Google API 403 error | Make sure your Google account has Editor access to both the template deck and the Drive folder, and that the OAuth token has the correct scopes |
| Template slide not found | Ensure Slide 2 of the template contains the text `{{AUTHOR}}` in a text box |
| Images not appearing | Discord CDN links expire; the bot re-uploads images to Drive — check the OAuth token has Drive write access |
| Double-run on DST change | The DST guard handles this; check the workflow logs for "skipping this scheduled run" |
| Bot skipped due to runner delay | The slides guard tolerates up to 30 min of GitHub Actions delay (accepts UK 12:00–12:29); if a run was still skipped, trigger it manually via **Actions → Run workflow** |
| State branch missing | It is created automatically on the first successful run |
| `RefreshError: Token has been expired or revoked` | Your Google OAuth token needs refreshing — re-run the OAuth consent flow and update the `GOOGLE_OAUTH_TOKEN` secret. The Monday and Thursday preview runs keep the token alive automatically; if it still expires, check that the scheduled workflow is running. When `GITHUB_TOKEN` and `GITHUB_REPOSITORY` are set, the bot automatically creates a GitHub issue for this error |

---

## Security Notes

- `oauth_client.json`, `oauth_token.json`, and `.env` are excluded by `.gitignore` and must never be committed.
- Google access uses OAuth2 with your personal account, granting only the scopes authorised during the OAuth consent flow.
- Generated presentations are shared as "anyone with the link can view" — they are not indexed or searchable.
- All secrets are stored as GitHub Actions secrets and never echoed in logs.

---

## File Structure

```
guess-chat-bot/
├── .env.example                        # Environment variable template
├── .gitignore                          # Excludes secrets, state, build artifacts
├── .github/
│   └── workflows/
│       └── weekly-slides.yml           # GitHub Actions scheduled workflow
├── README.md                           # This file
├── requirements.txt                    # Python dependencies
├── tests/                              # pytest test suite
│   ├── test_cleanup.py
│   ├── test_display_name.py
│   ├── test_error_notifications.py
│   ├── test_execute_retry.py
│   ├── test_hyperlinks.py
│   ├── test_image_handling.py
│   ├── test_image_insert_error.py
│   ├── test_markdown_detection.py
│   ├── test_mod_channel.py
│   ├── test_rate_limit.py
│   ├── test_thread_offload.py
│   └── test_youtube.py
└── weekly_slides_bot.py                # Main bot script
```
