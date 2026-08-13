"""Tests for `strim` mode — the /guesschat time destination.

strim is a real run: it builds the decks and persists state exactly like the
Friday `slides` run, but posts to the stream channel and keeps posting when
there is nothing new, so the command can be fired again mid-stream.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

import weekly_slides_bot as bot

STRIM_ID = 1054729620844453898
MOD_ID = 3
RESULTS_ID = 2


def _msg(msg_id: int, content: str, author_id: int = 999):
    m = MagicMock()
    m.id = msg_id
    m.content = content
    m.attachments = []
    m.author = MagicMock()
    m.author.id = author_id
    m.author.display_name = "User"
    m.guild = MagicMock()
    m.guild.get_member.return_value = MagicMock(display_name="User")
    return m


def _client(marker, submissions, channels):
    """Build a client whose submissions channel yields marker + submissions."""
    submissions_channel = MagicMock()
    submissions_channel.topic = "Current Guess Chat: Test"
    submissions_channel.guild = MagicMock(id=42)

    def history(**kwargs):
        async def gen():
            for m in ([marker] if "after" not in kwargs else submissions):
                yield m

        return gen()

    submissions_channel.history = history

    client = MagicMock()
    client.user = MagicMock(id=555)
    lookup = {1: submissions_channel, **channels}
    client.get_channel.side_effect = lambda cid: lookup.get(cid)
    return client


def _sendable():
    ch = MagicMock()
    ch.send = AsyncMock()
    return ch


@pytest.fixture
def strim_env():
    with patch.object(bot, "BOT_MODE", "strim"), \
            patch.object(bot, "DISCORD_STRIM_CHANNEL_ID", STRIM_ID), \
            patch.object(bot, "DISCORD_MOD_CHANNEL_ID", MOD_ID), \
            patch.object(bot, "MARKER_MESSAGE_ID", None):
        yield


# ---------------------------------------------------------------------------
# Channel routing
# ---------------------------------------------------------------------------


def test_notice_channel_is_the_stream_channel_in_strim_mode(strim_env):
    assert bot.notice_channel_id() == STRIM_ID


def test_notice_channel_is_still_the_mod_channel_in_preview_mode():
    with patch.object(bot, "BOT_MODE", "preview"), \
            patch.object(bot, "DISCORD_MOD_CHANNEL_ID", MOD_ID):
        assert bot.notice_channel_id() == MOD_ID


def test_strim_is_a_reposting_mode_but_slides_is_not():
    assert "strim" in bot.REPOSTING_MODES
    # The Friday run must stay silent when it has nothing to say.
    assert "slides" not in bot.REPOSTING_MODES


# ---------------------------------------------------------------------------
# A real run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_results_go_to_the_stream_channel_not_the_results_channel(strim_env):
    strim, results = _sendable(), _sendable()
    client = _client(
        _msg(100, "GUESS CHAT Test"),
        [_msg(200, "SUBMISSION my answer")],
        {STRIM_ID: strim, RESULTS_ID: results, MOD_ID: _sendable()},
    )

    with patch.object(bot, "load_state", return_value={}), \
            patch.object(bot, "save_state"), \
            patch.object(bot, "get_google_services", return_value=(MagicMock(), MagicMock())), \
            patch.object(bot, "copy_presentation_with_quota_retry", return_value="pres"), \
            patch.object(bot, "share_presentation"), \
            patch.object(bot, "delete_old_images"), \
            patch.object(bot, "empty_trash"), \
            patch.object(bot, "generate_fun_facts", return_value=""), \
            patch.object(bot, "build_deck", return_value=[]), \
            patch.object(bot, "collect_deck_authors", return_value=["User"]):
        await bot.generate_slides(client)

    strim.send.assert_awaited_once()
    assert "Guess Chat" in strim.send.await_args.args[0]
    results.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_strim_persists_state_so_the_friday_run_stays_quiet(strim_env):
    """Unlike test_slides, strim consumes the round."""
    saved: dict = {}
    client = _client(
        _msg(100, "GUESS CHAT Test"),
        [_msg(200, "SUBMISSION my answer")],
        {STRIM_ID: _sendable(), MOD_ID: _sendable()},
    )

    with patch.object(bot, "load_state", return_value={}), \
            patch.object(bot, "save_state", side_effect=saved.update), \
            patch.object(bot, "get_google_services", return_value=(MagicMock(), MagicMock())), \
            patch.object(bot, "copy_presentation_with_quota_retry", return_value="pres"), \
            patch.object(bot, "share_presentation"), \
            patch.object(bot, "delete_old_images"), \
            patch.object(bot, "empty_trash"), \
            patch.object(bot, "generate_fun_facts", return_value=""), \
            patch.object(bot, "build_deck", return_value=[]), \
            patch.object(bot, "collect_deck_authors", return_value=[]):
        await bot.generate_slides(client)

    assert saved["marker_id"] == "100"
    assert "200" in saved["processed_ids"]


@pytest.mark.asyncio
async def test_rerunning_reposts_the_existing_deck_links(strim_env):
    """Firing /guesschat time twice should show the decks again, not go silent."""
    strim = _sendable()
    state = {
        "marker_id": "100",
        "topic": "Test",
        "named_pres_id": "named1",
        "anon_pres_id": "anon1",
        "processed_ids": ["200"],
    }
    client = _client(
        _msg(100, "GUESS CHAT Test"),
        [_msg(200, "SUBMISSION my answer")],
        {STRIM_ID: strim, MOD_ID: _sendable()},
    )

    with patch.object(bot, "load_state", return_value=dict(state)), \
            patch.object(bot, "save_state"), \
            patch.object(bot, "get_google_services", return_value=(MagicMock(), MagicMock())), \
            patch.object(bot, "collect_deck_authors", return_value=["User"]), \
            patch.object(bot, "build_deck") as build, \
            patch.object(bot, "append_slides") as append:
        await bot.generate_slides(client)

    strim.send.assert_awaited_once()
    assert "named1" in strim.send.await_args.args[0]
    # Nothing new to add, so the decks are left alone.
    build.assert_not_called()
    append.assert_not_called()


@pytest.mark.asyncio
async def test_missing_stream_channel_id_skips_the_post_rather_than_crashing():
    client = _client(
        _msg(100, "GUESS CHAT Test"),
        [_msg(200, "SUBMISSION my answer")],
        {MOD_ID: _sendable()},
    )

    with patch.object(bot, "BOT_MODE", "strim"), \
            patch.object(bot, "DISCORD_STRIM_CHANNEL_ID", None), \
            patch.object(bot, "DISCORD_MOD_CHANNEL_ID", MOD_ID), \
            patch.object(bot, "MARKER_MESSAGE_ID", None), \
            patch.object(bot, "load_state", return_value={}), \
            patch.object(bot, "save_state") as save, \
            patch.object(bot, "get_google_services", return_value=(MagicMock(), MagicMock())), \
            patch.object(bot, "copy_presentation_with_quota_retry", return_value="pres"), \
            patch.object(bot, "share_presentation"), \
            patch.object(bot, "delete_old_images"), \
            patch.object(bot, "empty_trash"), \
            patch.object(bot, "generate_fun_facts", return_value=""), \
            patch.object(bot, "build_deck", return_value=[]):
        await bot.generate_slides(client)

    # The decks were still built and the round still recorded.
    save.assert_called_once()
