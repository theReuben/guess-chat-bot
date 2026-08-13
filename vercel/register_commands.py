"""Register the /guesschat slash commands with Discord.

Run this once, and again whenever the command definitions below change.
Guild commands are used rather than global ones because they appear
immediately instead of taking up to an hour to propagate.

    export DISCORD_APPLICATION_ID=...   # Developer Portal → General Information
    export DISCORD_TOKEN=...            # the bot token
    export DISCORD_GUILD_ID=...         # the server to register in
    python register_commands.py

Pass ``--clear`` to remove every registered command from the guild.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

APPLICATION_ID = os.environ["DISCORD_APPLICATION_ID"]
BOT_TOKEN = os.environ["DISCORD_TOKEN"]
GUILD_ID = os.environ["DISCORD_GUILD_ID"]

# Application command option types
SUB_COMMAND = 1
STRING = 3
BOOLEAN = 5

# Bitfield for MANAGE_GUILD.  Discord hides the command from anyone without
# it; the endpoint re-checks server-side because that hiding is only a
# client-side affordance.
MANAGE_GUILD = "32"

# Discord's edge rejects urllib's default "Python-urllib/x.y" agent with a 403,
# so every request has to identify itself the way the API docs require.
USER_AGENT = "DiscordBot (https://github.com/theReuben/guess-chat-bot, 1.0)"

MODES = ("preview", "slides", "announce", "test_slides", "test_announce")
_MODE_CHOICES = [{"name": mode, "value": mode} for mode in MODES]

COMMANDS = [
    {
        "name": "guesschat",
        "description": "Run the Guess Chat slide bot",
        "default_member_permissions": MANAGE_GUILD,
        "options": [
            {
                "type": SUB_COMMAND,
                "name": "preview",
                "description": "Rebuild the decks and post the result to the mod channel",
            },
            {
                "type": SUB_COMMAND,
                "name": "marker",
                "description": "Use an announcement someone else posted as this round's marker",
                "options": [
                    {
                        "type": STRING,
                        "name": "message_id",
                        "description": "ID of the announcement message (right-click → Copy Message ID)",
                        "required": True,
                    },
                    {
                        "type": STRING,
                        "name": "mode",
                        "description": "Mode to run with (default: preview)",
                        "choices": _MODE_CHOICES,
                    },
                ],
            },
            {
                "type": SUB_COMMAND,
                "name": "run",
                "description": "Run the bot with full control over the options",
                "options": [
                    {
                        "type": STRING,
                        "name": "mode",
                        "description": "Which mode to run",
                        "required": True,
                        "choices": _MODE_CHOICES,
                    },
                    {
                        "type": STRING,
                        "name": "marker_message_id",
                        "description": "Use this message as the GUESS CHAT announcement",
                    },
                    {
                        "type": BOOLEAN,
                        "name": "force_reset",
                        "description": "Wipe saved state and build brand-new decks",
                    },
                ],
            },
            {
                "type": SUB_COMMAND,
                "name": "status",
                "description": "Show the current round, deck links and processed count",
            },
        ],
    },
]


def put_commands(commands: list[dict]) -> list[dict]:
    """Bulk-overwrite the guild's commands (anything not listed is removed)."""
    url = (
        f"https://discord.com/api/v10/applications/{APPLICATION_ID}"
        f"/guilds/{GUILD_ID}/commands"
    )
    req = urllib.request.Request(url, data=json.dumps(commands).encode(), method="PUT")
    req.add_header("Authorization", f"Bot {BOT_TOKEN}")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # Discord explains option-schema mistakes in the body; surface it.
        sys.exit(f"Discord rejected the commands ({exc.code}):\n{exc.read().decode()}")


def main() -> None:
    commands = [] if "--clear" in sys.argv else COMMANDS
    registered = put_commands(commands)
    if not commands:
        print("Cleared all guild commands.")
        return
    for cmd in registered:
        subs = ", ".join(
            o["name"] for o in cmd.get("options", []) if o["type"] == SUB_COMMAND
        )
        print(f"Registered /{cmd['name']} ({subs})")


if __name__ == "__main__":
    main()
