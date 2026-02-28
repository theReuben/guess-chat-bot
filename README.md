# Guess Chat Bot

A Discord bot that scans a submissions channel for **GUESS CHAT** rounds, generates two Google Slides presentations (named and anonymous) from a template deck, and posts the links in a results channel.

---

## Overview

When someone posts a `GUESS CHAT <topic>` marker in the submissions channel, players reply with their `SUBMISSION <text>` messages (optionally attaching images). The bot:

1. Finds the latest marker and collects all submissions after it.
2. Copies a Google Slides template twice — one **named** deck (answers revealed) and one **anonymous** deck (for guessing).
3. Fills each deck with one slide per submission, inserting the player's answer and any images.
4. Shares both decks publicly ("anyone with the link can view").
5. Posts a summary message in the results channel with the anonymous link and the named link hidden in Discord spoiler tags.

---

## Features

- **Round detection** — detects new rounds by tracking the marker message ID in `state.json`.
- **Image support** — Discord attachment images are re-uploaded to Google Drive (to avoid CDN link expiration) and placed in a 2×2 grid on each slide.
- **Incremental updates** — if the bot runs again in the same round, it appends only the new submissions.
- **Duplicate prevention** — processed message IDs are stored in state.
- **Auto-posting** — posts results directly to a Discord channel.
- **Scheduled runs** — GitHub Actions triggers every Friday at 11:30 AM UK time (handles BST/GMT automatically).
- **Manual trigger** — run from the GitHub Actions UI with an optional `force_reset` to start a fresh round.

---

## How the Game Works

1. An admin posts a message starting with `GUESS CHAT <topic>` in the submissions channel (e.g. `GUESS CHAT DnD Characters`).
2. Players reply with `SUBMISSION <their answer>`, optionally attaching images.
3. The bot runs (scheduled or manual) and generates the two decks.
4. The results channel receives a message with:
   - A link to the **anonymous** deck (everyone can guess).
   - A link to the **named** deck hidden in spoiler tags (reveal after guessing).

---

## Prerequisites

- **Python 3.10+**
- A **Discord bot** with the Message Content intent enabled.
- A **Google Cloud project** with the Slides API and Drive API enabled.
- A **Google OAuth2 Desktop App** client ID and a refresh token for your personal Google account.

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
3. Go to **APIs & Services → OAuth consent screen**, configure it (External type), and add your Google account as a test user.
4. Go to **APIs & Services → Credentials**, click **Create Credentials → OAuth client ID**, choose **Desktop app**, and download the JSON as `oauth_client.json`.
5. Generate a refresh token by running an OAuth flow (e.g. using `google-auth-oauthlib`'s `InstalledAppFlow`) with the scopes `https://www.googleapis.com/auth/presentations` and `https://www.googleapis.com/auth/drive`. Save the resulting token JSON (containing `client_id`, `client_secret`, `refresh_token`, and `token_uri`) as `oauth_token.json`.
6. Create a folder in Google Drive to store the generated decks. Note the folder ID from the URL (`DRIVE_FOLDER_ID`).

### Template Deck

1. Create a new Google Slides presentation with **3 slides**:
   - **Slide 1 (Title)**: add a text box containing `{{TOPIC}}` — this will be replaced with the round topic.
   - **Slide 2 (Submission template)**: add text boxes containing `{{AUTHOR}}` and `{{BODY}}` — these are replaced for each submission; this slide is duplicated once per submission.
   - **Slide 3 (End)**: a static closing slide — no modifications.
2. Share the presentation with your Google account (the one used for OAuth) as **Editor** (it likely already has access as the owner).
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
└── service_account.json    ← not committed (in .gitignore)
```

### GitHub Secrets

Add the following secrets to your repository (**Settings → Secrets and variables → Actions**):

| Secret | Description |
|---|---|
| `DISCORD_TOKEN` | Discord bot token |
| `DISCORD_CHANNEL_ID` | Submissions channel ID |
| `DISCORD_RESULTS_CHANNEL_ID` | Results channel ID |
| `DRIVE_FOLDER_ID` | Google Drive folder ID for generated decks |
| `TEMPLATE_DECK_ID` | Google Slides template presentation ID |
| `GOOGLE_OAUTH_TOKEN` | OAuth2 token JSON with `client_id`, `client_secret`, `refresh_token`, and `token_uri` |

---

## How to Run

### Automatic (Scheduled)

The workflow runs every **Friday at 11:30 AM UK time**. Two cron expressions handle the clocks-change:

- `30 10 * * 5` — 10:30 UTC = 11:30 BST (summer, UTC+1)
- `30 11 * * 5` — 11:30 UTC = 11:30 GMT (winter, UTC+0)

At the start of each run the workflow reads the current UK hour and skips execution if it is not 11, preventing double-runs during the DST change weekends.

### Manual Trigger

1. Go to **Actions → Weekly Slides** in GitHub.
2. Click **Run workflow**.
3. Set `force_reset` to `true` to wipe saved state and create brand-new decks even if the marker hasn't changed.

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
  "processed_ids": ["111", "222", "333"]
}
```

To reset state manually, delete or empty `state.json` on the `state` branch, or trigger the workflow with `force_reset = true`.

---

## Round Detection Logic

| Scenario | Behaviour |
|---|---|
| New `GUESS CHAT` marker (different ID) | Creates fresh decks, resets processed IDs |
| Same marker + new `SUBMISSION` messages | Appends new slides to existing decks |
| Same marker + no new submissions | Exits early, nothing posted |
| No `GUESS CHAT` marker found | Exits early, nothing posted |

---

## Discord Results Message Format

```
## Guess Chat — DnD Characters

**Questions (anonymous):** https://docs.google.com/presentation/d/.../edit?usp=sharing
**Answers:** ||https://docs.google.com/presentation/d/.../edit?usp=sharing||

**Submissions (5 total, 4 unique submitters):**
  • Alice
  • Bob (×2)
  • Charlie
  • Diana
```

---

## DST / Timezone Handling

The UK observes **BST (UTC+1)** from late March to late October and **GMT (UTC+0)** otherwise. GitHub Actions cron uses UTC, so two cron triggers are used:

- **Summer**: `30 10 * * 5` fires at 10:30 UTC = 11:30 BST.
- **Winter**: `30 11 * * 5` fires at 11:30 UTC = 11:30 GMT.

On clocks-change Fridays, both crons fire. The DST guard at the start of the job reads `TZ='Europe/London' date +%H` and skips the run if the UK hour is not 11.

---

## Cost

Running on GitHub Actions free tier: **$0/month**. Each run takes under a minute.

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
| State branch missing | It is created automatically on the first successful run |

---

## Security Notes

- `service_account.json`, `oauth_client.json`, `oauth_token.json`, and `.env` are excluded by `.gitignore` and must never be committed.
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
└── weekly_slides_bot.py                # Main bot script
```
