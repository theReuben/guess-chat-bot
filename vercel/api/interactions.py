"""Discord HTTP interactions endpoint for the Guess Chat bot.

Discord POSTs signed interaction payloads here.  The endpoint deliberately
does none of the bot's work: Discord requires a reply within 3 seconds and a
deck build takes minutes, so the handler verifies the signature, translates
the slash command into a ``workflow_dispatch`` on the Weekly Slides GitHub
Actions workflow, and answers immediately.  The workflow does the slow part
and posts its own results message when it finishes.

Environment (set these in the Vercel project settings):

==========================  =================================================
``DISCORD_PUBLIC_KEY``      App's public key, for Ed25519 signature checks
``DISCORD_GUILD_ID``        Only this guild may invoke the commands
``DISCORD_MOD_ROLE_ID``     Optional; when set the invoker must hold this role
``GH_DISPATCH_TOKEN``       GitHub token with ``actions: write`` (dispatch) and
                            ``contents: read`` (``/guesschat status``)
``GITHUB_REPOSITORY``       ``owner/repo``
``GITHUB_WORKFLOW_FILE``    Workflow filename (default ``weekly-slides.yml``)
``GITHUB_REF``              Branch to run the workflow from (default ``main``)
==========================  =================================================
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

DISCORD_PUBLIC_KEY = os.environ.get("DISCORD_PUBLIC_KEY", "")
DISCORD_GUILD_ID = os.environ.get("DISCORD_GUILD_ID", "")
DISCORD_MOD_ROLE_ID = os.environ.get("DISCORD_MOD_ROLE_ID", "")
GH_DISPATCH_TOKEN = os.environ.get("GH_DISPATCH_TOKEN", "")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY", "")
GITHUB_WORKFLOW_FILE = os.environ.get("GITHUB_WORKFLOW_FILE", "weekly-slides.yml")
GITHUB_REF = os.environ.get("GITHUB_REF", "main")

# Discord interaction request/response types
PING = 1
APPLICATION_COMMAND = 2
PONG = 1
CHANNEL_MESSAGE_WITH_SOURCE = 4
EPHEMERAL = 1 << 6

# Bot modes accepted by the workflow's bot_mode input.
VALID_MODES = ("preview", "slides", "announce", "test_slides", "test_announce")

# Discord's deadline is 3 seconds; leave room for TLS setup and a cold start.
_GITHUB_TIMEOUT_S = 2.5


# ---------------------------------------------------------------------------
# Request authentication
# ---------------------------------------------------------------------------


def verify_signature(
    public_key: str, signature: str, timestamp: str, body: bytes
) -> bool:
    """Return True when the request genuinely came from Discord.

    Discord signs ``timestamp + body`` with the application's Ed25519 key, and
    probes the endpoint with deliberately malformed signatures when the URL is
    saved.  Every failure mode therefore has to come back as ``False`` (→ 401)
    rather than raising.
    """
    if not (public_key and signature and timestamp):
        return False
    try:
        VerifyKey(bytes.fromhex(public_key)).verify(
            timestamp.encode() + body, bytes.fromhex(signature)
        )
    except (BadSignatureError, ValueError):
        return False
    return True


def authorize(interaction: dict) -> str | None:
    """Return a refusal message when the invoker may not run these commands.

    Discord already hides the command from non-mods via
    ``default_member_permissions``, but that is a client-side affordance —
    the endpoint is public, so the guild and role are checked here too.
    """
    if DISCORD_GUILD_ID and interaction.get("guild_id") != DISCORD_GUILD_ID:
        return "These commands only work in the Guess Chat server."
    if DISCORD_MOD_ROLE_ID:
        roles = (interaction.get("member") or {}).get("roles") or []
        if DISCORD_MOD_ROLE_ID not in roles:
            return "Only mods can run this command."
    return None


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------


def _github_request(method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"https://api.github.com{path}", data=data, method=method
    )
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {GH_DISPATCH_TOKEN}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    # GitHub requires every request to identify itself.
    req.add_header("User-Agent", "guess-chat-bot-interactions")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=_GITHUB_TIMEOUT_S) as resp:
        raw = resp.read()
    # workflow_dispatch answers 204 with an empty body.
    return json.loads(raw) if raw else {}


def dispatch_workflow(inputs: dict[str, str]) -> None:
    """Trigger the Weekly Slides workflow with the given inputs."""
    _github_request(
        "POST",
        f"/repos/{GITHUB_REPOSITORY}/actions/workflows/{GITHUB_WORKFLOW_FILE}/dispatches",
        {"ref": GITHUB_REF, "inputs": inputs},
    )


def read_state() -> dict:
    """Read ``state.json`` from the orphan ``state`` branch."""
    payload = _github_request(
        "GET", f"/repos/{GITHUB_REPOSITORY}/contents/state.json?ref=state"
    )
    encoded = payload.get("content", "")
    if not encoded:
        return {}
    return json.loads(base64.b64decode(encoded).decode())


# ---------------------------------------------------------------------------
# Command handling
# ---------------------------------------------------------------------------


def _reply(content: str, ephemeral: bool = False) -> dict:
    data: dict = {"content": content}
    if ephemeral:
        data["flags"] = EPHEMERAL
    return {"type": CHANNEL_MESSAGE_WITH_SOURCE, "data": data}


def _subcommand(interaction: dict) -> tuple[str, dict]:
    """Return the invoked subcommand name and its options as a flat dict."""
    options = (interaction.get("data") or {}).get("options") or []
    if not options:
        return "", {}
    sub = options[0]
    values = {o["name"]: o.get("value") for o in (sub.get("options") or [])}
    return sub.get("name", ""), values


def _workflow_url() -> str:
    return (
        f"https://github.com/{GITHUB_REPOSITORY}/actions/"
        f"workflows/{GITHUB_WORKFLOW_FILE}"
    )


def _started_message(inputs: dict[str, str]) -> str:
    lines = [f"▶️ Started a **{inputs['bot_mode']}** run."]
    if inputs.get("marker_message_id"):
        lines.append(
            f"Using message `{inputs['marker_message_id']}` as the announcement."
        )
    if inputs.get("force_reset") == "true":
        lines.append("⚠️ State wiped — brand-new decks will be created.")
    lines.append(f"Results will be posted when it finishes · <{_workflow_url()}>")
    return "\n".join(lines)


_BAD_ID = (
    "must be a Discord message ID (numbers only). Turn on Developer Mode, "
    "then right-click the message → Copy Message ID."
)


def build_inputs(name: str, options: dict) -> tuple[dict[str, str] | None, str | None]:
    """Map a subcommand to workflow inputs.

    Returns ``(inputs, None)`` on success or ``(None, reason)`` when the
    options don't make sense.
    """
    if name == "preview":
        return {"bot_mode": "preview"}, None

    if name == "marker":
        message_id = str(options.get("message_id") or "").strip()
        if not message_id.isdigit():
            return None, f"`message_id` {_BAD_ID}"
        return {
            "bot_mode": options.get("mode") or "preview",
            "marker_message_id": message_id,
        }, None

    if name == "run":
        inputs = {"bot_mode": options.get("mode") or "preview"}
        message_id = str(options.get("marker_message_id") or "").strip()
        if message_id:
            if not message_id.isdigit():
                return None, f"`marker_message_id` {_BAD_ID}"
            inputs["marker_message_id"] = message_id
        if options.get("force_reset"):
            inputs["force_reset"] = "true"
        return inputs, None

    return None, f"Unknown command `{name}`."


def _status_reply() -> dict:
    try:
        state = read_state()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return _reply(
                "No saved state yet — the bot hasn't completed a round.",
                ephemeral=True,
            )
        return _reply(f"Could not read the saved state: {exc}", ephemeral=True)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return _reply(f"Could not read the saved state: {exc}", ephemeral=True)

    if not state:
        return _reply(
            "No saved state yet — the bot hasn't completed a round.", ephemeral=True
        )

    lines = [f"**Current round:** {state.get('topic', 'unknown')}"]
    if state.get("marker_id"):
        lines.append(f"Marker message: `{state['marker_id']}`")
    if state.get("marker_override_id"):
        lines.append(
            f"⚙️ Announcement override active: `{state['marker_override_id']}`"
        )
    lines.append(f"Submissions processed: {len(state.get('processed_ids') or [])}")
    if state.get("anon_pres_id"):
        lines.append(
            f"Questions: <https://docs.google.com/presentation/d/{state['anon_pres_id']}/edit>"
        )
    if state.get("named_pres_id"):
        lines.append(
            f"Answers: <https://docs.google.com/presentation/d/{state['named_pres_id']}/edit>"
        )
    return _reply("\n".join(lines), ephemeral=True)


def handle_command(interaction: dict) -> dict:
    """Turn a slash command into a workflow run and build Discord's reply."""
    denied = authorize(interaction)
    if denied:
        return _reply(denied, ephemeral=True)

    name, options = _subcommand(interaction)
    if name == "status":
        return _status_reply()

    inputs, reason = build_inputs(name, options)
    if inputs is None:
        return _reply(reason, ephemeral=True)

    if inputs["bot_mode"] not in VALID_MODES:
        return _reply(f"Unknown mode `{inputs['bot_mode']}`.", ephemeral=True)

    try:
        dispatch_workflow(inputs)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return _reply(
            f"Could not start the workflow: {exc}\nTry the Actions UI: <{_workflow_url()}>",
            ephemeral=True,
        )

    return _reply(_started_message(inputs))


# ---------------------------------------------------------------------------
# Vercel entry point
# ---------------------------------------------------------------------------


class handler(BaseHTTPRequestHandler):  # noqa: N801 — Vercel requires this name
    def do_GET(self) -> None:  # noqa: N802
        """Health check, so the endpoint can be monitored for uptime."""
        self._send(200, {"status": "ok"})

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))

        if not verify_signature(
            DISCORD_PUBLIC_KEY,
            self.headers.get("X-Signature-Ed25519", ""),
            self.headers.get("X-Signature-Timestamp", ""),
            body,
        ):
            self._send(401, {"error": "invalid request signature"})
            return

        try:
            interaction = json.loads(body)
        except ValueError:
            self._send(400, {"error": "malformed payload"})
            return

        kind = interaction.get("type")
        if kind == PING:
            self._send(200, {"type": PONG})
        elif kind == APPLICATION_COMMAND:
            self._send(200, handle_command(interaction))
        else:
            self._send(400, {"error": f"unsupported interaction type {kind}"})

    def _send(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt: str, *args) -> None:
        """Silence the default per-request access log line."""
