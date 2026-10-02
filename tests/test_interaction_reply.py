"""Tests for updating the /guesschat reply with the run's outcome, and the
extra state saved for /guesschat status."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests

os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

import weekly_slides_bot
from weekly_slides_bot import (
    _load_dispatch_inputs,
    check_mod_and_announce,
    generate_slides,
    report_to_interaction,
    set_outcome,
)

ORIGINAL = (
    "▶️ Started the bot in **preview** mode.\n"
    "⏳ Running… this message updates when it finishes · <https://github.com/x>"
)


@pytest.fixture(autouse=True)
def _reset_outcome():
    weekly_slides_bot._run_outcome = None
    yield
    weekly_slides_bot._run_outcome = None


@pytest.fixture
def credentials():
    with patch.object(weekly_slides_bot, "INTERACTION_APP_ID", "app1"), \
            patch.object(weekly_slides_bot, "INTERACTION_TOKEN", "secret-token"), \
            patch.object(weekly_slides_bot, "GITHUB_REPOSITORY", "owner/repo"), \
            patch.dict(os.environ, {"GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ID": "99"}):
        yield


def _fake_requests(content: str = ORIGINAL):
    get = MagicMock()
    get.return_value.json.return_value = {"content": content}
    patch_ = MagicMock()
    return get, patch_


# ---------------------------------------------------------------------------
# Reading the dispatch inputs
# ---------------------------------------------------------------------------


def test_dispatch_inputs_are_read_from_the_event_payload(tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"inputs": {"interaction_token": "tok"}}))
    with patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event)}):
        assert _load_dispatch_inputs() == {"interaction_token": "tok"}


def test_dispatch_inputs_are_empty_outside_actions():
    with patch.dict(os.environ, {}, clear=True):
        assert _load_dispatch_inputs() == {}


def test_scheduled_runs_have_no_inputs(tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"schedule": "30 10 * * 5"}))
    with patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event)}):
        assert _load_dispatch_inputs() == {}


# ---------------------------------------------------------------------------
# report_to_interaction
# ---------------------------------------------------------------------------


def test_does_nothing_without_credentials():
    with patch.object(weekly_slides_bot, "INTERACTION_TOKEN", None), \
            patch("weekly_slides_bot.requests.get") as get:
        report_to_interaction()
    get.assert_not_called()


def test_replaces_the_pending_line_with_the_outcome(credentials):
    get, patch_ = _fake_requests()
    set_outcome("✅ Posted the decks")
    with patch("weekly_slides_bot.requests.get", get), patch("weekly_slides_bot.requests.patch", patch_):
        report_to_interaction()

    assert get.call_args.kwargs["headers"]["User-Agent"].startswith("DiscordBot ")
    assert patch_.call_args.kwargs["headers"]["User-Agent"].startswith("DiscordBot ")
    url = patch_.call_args.args[0]
    assert url == "https://discord.com/api/v10/webhooks/app1/secret-token/messages/@original"
    content = patch_.call_args.kwargs["json"]["content"]
    assert content.splitlines() == [
        "▶️ Started the bot in **preview** mode.",
        "✅ Posted the decks · [run log](<https://github.com/owner/repo/actions/runs/99>)",
    ]


def test_defaults_to_finished_when_no_outcome_was_recorded(credentials):
    get, patch_ = _fake_requests()
    with patch("weekly_slides_bot.requests.get", get), patch("weekly_slides_bot.requests.patch", patch_):
        report_to_interaction()

    assert "✅ Finished." in patch_.call_args.kwargs["json"]["content"]


def test_failures_are_logged_without_the_token(credentials, capsys):
    err = requests.HTTPError("404 for url https://discord.com/.../secret-token/...")
    err.response = MagicMock(status_code=404)
    get = MagicMock(side_effect=err)
    with patch("weekly_slides_bot.requests.get", get):
        report_to_interaction()

    out = capsys.readouterr().out
    assert "HTTP 404" in out
    assert "secret-token" not in out


# ---------------------------------------------------------------------------
# Outcomes and state for /guesschat status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("weekly_slides_bot.save_state")
@patch("weekly_slides_bot.load_state", return_value={})
async def test_announce_records_the_outcome_and_deadline(_load, mock_save):
    channel = MagicMock(topic="Current Guess Chat: Movies")
    channel.send = AsyncMock(return_value=MagicMock(jump_url="https://discord.com/channels/1/2/3"))
    client = MagicMock()
    client.get_channel.return_value = channel

    with patch("weekly_slides_bot.next_friday_deadline_unix", return_value=1700000000):
        await check_mod_and_announce(client)

    assert weekly_slides_bot._run_outcome == "✅ Announced **Movies**: https://discord.com/channels/1/2/3"
    assert mock_save.call_args.args[0]["deadline_ts"] == 1700000000
    assert "<t:1700000000:R>" in channel.send.call_args.args[0]


@pytest.mark.asyncio
@patch("weekly_slides_bot.load_state", return_value={})
async def test_missing_topic_is_explained(_load):
    client = MagicMock()
    client.get_channel.return_value = MagicMock(topic="Welcome!")
    await check_mod_and_announce(client)

    assert "Current Guess Chat:" in weekly_slides_bot._run_outcome


@pytest.mark.asyncio
@patch("weekly_slides_bot.save_state")
@patch("weekly_slides_bot.build_deck", return_value=[])
@patch("weekly_slides_bot.share_presentation")
@patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
@patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
@patch("weekly_slides_bot.read_deck_authors", return_value=["Hand Added"])
@patch("weekly_slides_bot.load_state", return_value={})
async def test_generate_slides_saves_submitters_and_last_run(*mocks):
    mock_save = mocks[-1]
    marker = MagicMock(id=100, content="GUESS CHAT Test")
    sub = MagicMock(id=200, content="SUBMISSION answer", attachments=[])
    sub.author = MagicMock(id=999, display_name="dave")
    sub.guild = MagicMock()
    sub.guild.get_member.return_value = MagicMock(display_name="dave")

    calls = 0

    async def history(*args, **kwargs):
        nonlocal calls
        calls += 1
        yield marker if calls == 1 else sub

    results = MagicMock()
    results.send = AsyncMock(return_value=MagicMock(jump_url="https://discord.com/channels/1/2/5"))
    client = MagicMock()
    client.get_channel.side_effect = lambda cid: MagicMock(history=history) if cid == 1 else results

    with patch("weekly_slides_bot.BOT_MODE", "preview"), \
            patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3), \
            patch("weekly_slides_bot.time.time", return_value=1234.5):
        await generate_slides(client)

    saved = mock_save.call_args.args[0]
    assert saved["submitters"] == ["dave", "Hand Added"]
    assert saved["last_run"] == {"mode": "preview", "at": 1234}
    assert weekly_slides_bot._run_outcome.startswith("✅ Added 1 new submission(s)")
    assert "https://discord.com/channels/1/2/5" in weekly_slides_bot._run_outcome
