"""Tests for the Vercel Discord interactions endpoint.

The endpoint is the front door to the bot: it authenticates Discord, decides
who may run what, and translates slash commands into workflow_dispatch inputs.
It never touches Google or Discord's REST API itself.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest
from nacl.signing import SigningKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vercel" / "api"))

import interactions  # noqa: E402


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


@pytest.fixture
def keypair():
    signing_key = SigningKey.generate()
    public_key = signing_key.verify_key.encode().hex()
    return signing_key, public_key


def test_valid_signature_is_accepted(keypair):
    signing_key, public_key = keypair
    body = b'{"type": 1}'
    timestamp = "1700000000"
    signature = signing_key.sign(timestamp.encode() + body).signature.hex()

    assert interactions.verify_signature(public_key, signature, timestamp, body)


def test_tampered_body_is_rejected(keypair):
    signing_key, public_key = keypair
    timestamp = "1700000000"
    signature = signing_key.sign(timestamp.encode() + b'{"type": 1}').signature.hex()

    assert not interactions.verify_signature(
        public_key, signature, timestamp, b'{"type": 2}'
    )


def test_mismatched_timestamp_is_rejected(keypair):
    signing_key, public_key = keypair
    body = b'{"type": 1}'
    signature = signing_key.sign(b"1700000000" + body).signature.hex()

    assert not interactions.verify_signature(
        public_key, signature, "1700000099", body
    )


def test_signature_from_a_different_key_is_rejected(keypair):
    _, public_key = keypair
    other = SigningKey.generate()
    body = b'{"type": 1}'
    timestamp = "1700000000"
    signature = other.sign(timestamp.encode() + body).signature.hex()

    assert not interactions.verify_signature(public_key, signature, timestamp, body)


@pytest.mark.parametrize(
    "signature",
    ["", "not-hex", "aabb"],  # Discord probes the endpoint with junk signatures
)
def test_malformed_signatures_are_rejected_without_raising(keypair, signature):
    _, public_key = keypair

    assert not interactions.verify_signature(
        public_key, signature, "1700000000", b"{}"
    )


def test_missing_public_key_rejects_everything(keypair):
    signing_key, _ = keypair
    body = b"{}"
    signature = signing_key.sign(b"1" + body).signature.hex()

    assert not interactions.verify_signature("", signature, "1", body)


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_other_guilds_are_refused():
    with patch.object(interactions, "DISCORD_GUILD_ID", "111"):
        assert interactions.authorize({"guild_id": "222"}) is not None


def test_configured_guild_is_allowed():
    with patch.object(interactions, "DISCORD_GUILD_ID", "111"), \
            patch.object(interactions, "DISCORD_MOD_ROLE_ID", ""):
        assert interactions.authorize({"guild_id": "111"}) is None


def test_member_without_the_mod_role_is_refused():
    interaction = {"guild_id": "111", "member": {"roles": ["999"]}}
    with patch.object(interactions, "DISCORD_GUILD_ID", "111"), \
            patch.object(interactions, "DISCORD_MOD_ROLE_ID", "777"):
        assert interactions.authorize(interaction) is not None


def test_member_with_the_mod_role_is_allowed():
    interaction = {"guild_id": "111", "member": {"roles": ["777", "999"]}}
    with patch.object(interactions, "DISCORD_GUILD_ID", "111"), \
            patch.object(interactions, "DISCORD_MOD_ROLE_ID", "777"):
        assert interactions.authorize(interaction) is None


def test_dm_invocation_is_refused_when_a_role_is_required():
    """A DM interaction has no `member`, so the role check must not crash."""
    with patch.object(interactions, "DISCORD_GUILD_ID", ""), \
            patch.object(interactions, "DISCORD_MOD_ROLE_ID", "777"):
        assert interactions.authorize({"user": {"id": "1"}}) is not None


# ---------------------------------------------------------------------------
# Slash command → workflow inputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,mode", [("preview", "preview"), ("announce", "announce"), ("time", "strim")]
)
def test_shortcut_subcommands_map_to_their_mode(name, mode):
    assert interactions.build_inputs(name, {}) == ({"bot_mode": mode}, None)


def test_every_shortcut_target_is_a_mode_the_workflow_accepts():
    assert set(interactions.MODE_SHORTCUTS.values()) <= set(interactions.VALID_MODES)


def test_marker_defaults_to_preview_mode():
    inputs, reason = interactions.build_inputs("marker", {"message_id": "12345"})

    assert reason is None
    assert inputs == {"bot_mode": "preview", "marker_message_id": "12345"}


def test_marker_honours_an_explicit_mode():
    inputs, _ = interactions.build_inputs(
        "marker", {"message_id": "12345", "mode": "announce"}
    )

    assert inputs == {"bot_mode": "announce", "marker_message_id": "12345"}


@pytest.mark.parametrize("bad", ["", "   ", "abc", "123abc", "<@12345>"])
def test_marker_rejects_anything_that_is_not_a_message_id(bad):
    inputs, reason = interactions.build_inputs("marker", {"message_id": bad})

    assert inputs is None
    assert "message_id" in reason


def test_run_passes_mode_through():
    inputs, _ = interactions.build_inputs("run", {"mode": "slides"})

    assert inputs == {"bot_mode": "slides"}


def test_run_omits_force_reset_unless_asked():
    inputs, _ = interactions.build_inputs(
        "run", {"mode": "slides", "force_reset": False}
    )

    assert "force_reset" not in inputs


def test_run_sends_force_reset_as_the_string_the_workflow_expects():
    inputs, _ = interactions.build_inputs(
        "run", {"mode": "slides", "force_reset": True}
    )

    assert inputs["force_reset"] == "true"


def test_run_accepts_an_optional_marker():
    inputs, _ = interactions.build_inputs(
        "run", {"mode": "announce", "marker_message_id": "98765"}
    )

    assert inputs == {"bot_mode": "announce", "marker_message_id": "98765"}


def test_run_rejects_a_malformed_marker():
    inputs, reason = interactions.build_inputs(
        "run", {"mode": "announce", "marker_message_id": "nope"}
    )

    assert inputs is None
    assert "marker_message_id" in reason


def test_unknown_subcommand_is_reported():
    inputs, reason = interactions.build_inputs("destroy", {})

    assert inputs is None
    assert "destroy" in reason


def test_every_registered_mode_is_accepted_by_the_endpoint():
    """The registration script and the endpoint must agree on the mode list."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vercel"))
    with patch.dict(
        "os.environ",
        {
            "DISCORD_APPLICATION_ID": "1",
            "DISCORD_TOKEN": "t",
            "DISCORD_GUILD_ID": "2",
        },
    ):
        import register_commands

    assert register_commands.MODES == interactions.VALID_MODES


# ---------------------------------------------------------------------------
# handle_command
# ---------------------------------------------------------------------------


def _command(name: str, options: list[dict] | None = None) -> dict:
    return {
        "type": interactions.APPLICATION_COMMAND,
        "guild_id": "111",
        "member": {"roles": ["777"]},
        "data": {
            "name": "guesschat",
            "options": [{"name": name, "type": 1, "options": options or []}],
        },
    }


@pytest.fixture
def allowed():
    with patch.object(interactions, "DISCORD_GUILD_ID", "111"), \
            patch.object(interactions, "DISCORD_MOD_ROLE_ID", "777"), \
            patch.object(interactions, "GITHUB_REPOSITORY", "owner/repo"):
        yield


def test_a_command_dispatches_the_workflow_and_confirms(allowed):
    with patch.object(interactions, "dispatch_workflow") as dispatch:
        reply = interactions.handle_command(_command("preview"))

    dispatch.assert_called_once_with({"bot_mode": "preview"})
    assert reply["type"] == interactions.CHANNEL_MESSAGE_WITH_SOURCE
    assert "preview" in reply["data"]["content"]
    # Run confirmations stay visible so the mod channel keeps a record.
    assert "flags" not in reply["data"]


def test_announce_dispatches_and_warns_that_it_posts_publicly(allowed):
    with patch.object(interactions, "dispatch_workflow") as dispatch:
        reply = interactions.handle_command(_command("announce"))

    dispatch.assert_called_once_with({"bot_mode": "announce"})
    assert "submissions channel" in reply["data"]["content"]


def test_time_dispatches_strim_mode_and_names_the_destination(allowed):
    with patch.object(interactions, "dispatch_workflow") as dispatch:
        reply = interactions.handle_command(_command("time"))

    dispatch.assert_called_once_with({"bot_mode": "strim"})
    assert "stream channel" in reply["data"]["content"]


def test_only_announce_carries_the_public_post_warning(allowed):
    with patch.object(interactions, "dispatch_workflow"):
        reply = interactions.handle_command(_command("preview"))

    assert "submissions channel" not in reply["data"]["content"]


def test_force_reset_is_called_out_in_the_confirmation(allowed):
    command = _command(
        "run", [{"name": "mode", "value": "slides"}, {"name": "force_reset", "value": True}]
    )
    with patch.object(interactions, "dispatch_workflow"):
        reply = interactions.handle_command(command)

    assert "State wiped" in reply["data"]["content"]


def test_an_unauthorized_caller_never_reaches_the_workflow(allowed):
    command = _command("preview")
    command["member"]["roles"] = ["000"]

    with patch.object(interactions, "dispatch_workflow") as dispatch:
        reply = interactions.handle_command(command)

    dispatch.assert_not_called()
    assert reply["data"]["flags"] == interactions.EPHEMERAL


def test_a_bad_message_id_never_reaches_the_workflow(allowed):
    command = _command("marker", [{"name": "message_id", "value": "oops"}])

    with patch.object(interactions, "dispatch_workflow") as dispatch:
        reply = interactions.handle_command(command)

    dispatch.assert_not_called()
    assert reply["data"]["flags"] == interactions.EPHEMERAL


def test_a_github_failure_is_reported_rather_than_raised(allowed):
    with patch.object(
        interactions, "dispatch_workflow", side_effect=urllib.error.URLError("down")
    ):
        reply = interactions.handle_command(_command("preview"))

    assert "Could not start the workflow" in reply["data"]["content"]
    assert reply["data"]["flags"] == interactions.EPHEMERAL


def test_status_reports_the_round_and_deck_links(allowed):
    state = {
        "topic": "DnD Characters",
        "marker_id": "100",
        "processed_ids": ["1", "2", "3"],
        "named_pres_id": "named123",
        "anon_pres_id": "anon456",
    }
    with patch.object(interactions, "read_state", return_value=state):
        reply = interactions.handle_command(_command("status"))

    content = reply["data"]["content"]
    assert "DnD Characters" in content
    assert "Submissions processed: 3" in content
    assert "named123" in content and "anon456" in content
    assert reply["data"]["flags"] == interactions.EPHEMERAL


def test_status_surfaces_an_active_marker_override(allowed):
    with patch.object(
        interactions,
        "read_state",
        return_value={"topic": "T", "marker_override_id": "555"},
    ):
        reply = interactions.handle_command(_command("status"))

    assert "555" in reply["data"]["content"]


def test_status_before_the_first_round_is_not_an_error(allowed):
    error = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
    with patch.object(interactions, "read_state", side_effect=error):
        reply = interactions.handle_command(_command("status"))

    assert "hasn't completed a round" in reply["data"]["content"]


# ---------------------------------------------------------------------------
# dispatch_workflow / read_state wire format
# ---------------------------------------------------------------------------


def test_dispatch_targets_the_workflow_with_ref_and_inputs():
    with patch.object(interactions, "_github_request", return_value={}) as req, \
            patch.object(interactions, "GITHUB_REPOSITORY", "owner/repo"), \
            patch.object(interactions, "GITHUB_WORKFLOW_FILE", "weekly-slides.yml"), \
            patch.object(interactions, "GITHUB_REF", "main"):
        interactions.dispatch_workflow({"bot_mode": "preview"})

    req.assert_called_once_with(
        "POST",
        "/repos/owner/repo/actions/workflows/weekly-slides.yml/dispatches",
        {"ref": "main", "inputs": {"bot_mode": "preview"}},
    )


def test_read_state_decodes_the_base64_contents_api_payload():
    import base64

    state = {"topic": "DnD Characters", "processed_ids": []}
    payload = {"content": base64.b64encode(json.dumps(state).encode()).decode()}

    with patch.object(interactions, "_github_request", return_value=payload):
        assert interactions.read_state() == state


def test_read_state_handles_an_empty_file():
    with patch.object(interactions, "_github_request", return_value={"content": ""}):
        assert interactions.read_state() == {}


def _sent_request(urlopen_mock):
    return urlopen_mock.call_args.args[0]


def test_github_requests_identify_themselves():
    """GitHub rejects requests that send no User-Agent."""
    with patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = b""
        interactions._github_request("POST", "/repos/o/r/x", {"a": 1})

    assert _sent_request(urlopen).get_header("User-agent")


def test_discord_requests_use_the_required_bot_agent():
    """Discord's edge 403s urllib's default Python-urllib agent."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vercel"))
    with patch.dict(
        "os.environ",
        {"DISCORD_APPLICATION_ID": "1", "DISCORD_TOKEN": "t", "DISCORD_GUILD_ID": "2"},
    ):
        import register_commands

    with patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = b"[]"
        register_commands.put_commands([])

    assert _sent_request(urlopen).get_header("User-agent").startswith("DiscordBot ")


# ---------------------------------------------------------------------------
# The HTTP handler end to end
# ---------------------------------------------------------------------------


def _post(body: bytes, signature: str, timestamp: str) -> tuple[int, dict]:
    """Drive handler.do_POST directly and return (status, parsed JSON body)."""
    h = interactions.handler.__new__(interactions.handler)
    h.request_version = "HTTP/1.1"
    h.requestline = "POST /api/interactions HTTP/1.1"
    h.rfile = io.BytesIO(body)
    h.wfile = io.BytesIO()
    h.headers = {
        "Content-Length": str(len(body)),
        "X-Signature-Ed25519": signature,
        "X-Signature-Timestamp": timestamp,
    }
    h.do_POST()

    raw = h.wfile.getvalue()
    status = int(raw.split(b" ", 2)[1])
    return status, json.loads(raw.split(b"\r\n\r\n", 1)[1])


def _signed(signing_key, payload: dict) -> tuple[bytes, str, str]:
    body = json.dumps(payload).encode()
    timestamp = "1700000000"
    signature = signing_key.sign(timestamp.encode() + body).signature.hex()
    return body, signature, timestamp


def test_discords_ping_gets_a_pong(keypair):
    signing_key, public_key = keypair
    body, signature, timestamp = _signed(signing_key, {"type": interactions.PING})

    with patch.object(interactions, "DISCORD_PUBLIC_KEY", public_key):
        status, reply = _post(body, signature, timestamp)

    assert status == 200
    assert reply == {"type": interactions.PONG}


def test_an_unsigned_request_gets_a_401(keypair):
    _, public_key = keypair
    body = json.dumps({"type": interactions.PING}).encode()

    with patch.object(interactions, "DISCORD_PUBLIC_KEY", public_key):
        status, reply = _post(body, "deadbeef", "1700000000")

    assert status == 401
    assert "signature" in reply["error"]


def test_a_forged_command_never_reaches_the_workflow(keypair):
    """Signature checking must happen before anything is dispatched."""
    _, public_key = keypair
    body = json.dumps(_command("run")).encode()

    with patch.object(interactions, "DISCORD_PUBLIC_KEY", public_key), \
            patch.object(interactions, "dispatch_workflow") as dispatch:
        status, _ = _post(body, "00" * 64, "1700000000")

    assert status == 401
    dispatch.assert_not_called()


def test_a_signed_command_is_routed(keypair, allowed):
    signing_key, public_key = keypair
    body, signature, timestamp = _signed(signing_key, _command("preview"))

    with patch.object(interactions, "DISCORD_PUBLIC_KEY", public_key), \
            patch.object(interactions, "dispatch_workflow") as dispatch:
        status, reply = _post(body, signature, timestamp)

    assert status == 200
    dispatch.assert_called_once_with({"bot_mode": "preview"})
    assert reply["type"] == interactions.CHANNEL_MESSAGE_WITH_SOURCE


def test_a_signed_but_unparseable_body_gets_a_400(keypair):
    signing_key, public_key = keypair
    body = b"not json"
    timestamp = "1700000000"
    signature = signing_key.sign(timestamp.encode() + body).signature.hex()

    with patch.object(interactions, "DISCORD_PUBLIC_KEY", public_key):
        status, reply = _post(body, signature, timestamp)

    assert status == 400
    assert "malformed" in reply["error"]


def test_an_unsupported_interaction_type_gets_a_400(keypair):
    signing_key, public_key = keypair
    body, signature, timestamp = _signed(signing_key, {"type": 99})

    with patch.object(interactions, "DISCORD_PUBLIC_KEY", public_key):
        status, _ = _post(body, signature, timestamp)

    assert status == 400
