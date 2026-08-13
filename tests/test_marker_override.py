"""Tests for overriding the GUESS CHAT marker with an explicit message ID.

Used when a person announces the round instead of the bot: the bot adopts
their message rather than posting a second announcement, and builds the decks
from it.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

import weekly_slides_bot as bot
from weekly_slides_bot import marker_topic, resolve_marker_message


def _message(msg_id: int, content: str, author_id: int = 999):
    msg = MagicMock()
    msg.id = msg_id
    msg.content = content
    msg.author.id = author_id
    return msg


def _channel(history: list, topic: str | None = None):
    """Build a channel mock whose history() is an async iterator."""
    channel = MagicMock()
    channel.topic = topic

    def _history(**_kwargs):
        async def _gen():
            for msg in history:
                yield msg

        return _gen()

    channel.history = _history
    channel.fetch_message = AsyncMock()
    return channel


# ---------------------------------------------------------------------------
# resolve_marker_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_override_is_fetched_directly_without_scanning_history():
    manual = _message(555, "Hey everyone, this week we're doing DnD Characters")
    channel = _channel([_message(111, "GUESS CHAT Old Topic", author_id=42)])
    channel.fetch_message = AsyncMock(return_value=manual)

    result = await resolve_marker_message(channel, bot_user_id=42, override_id="555")

    assert result is manual
    channel.fetch_message.assert_awaited_once_with(555)


@pytest.mark.asyncio
async def test_unfetchable_override_falls_back_to_history_scan():
    bot_marker = _message(111, "GUESS CHAT Old Topic", author_id=42)
    channel = _channel([bot_marker])
    channel.fetch_message = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(), "not found")
    )

    result = await resolve_marker_message(channel, bot_user_id=42, override_id="555")

    assert result is bot_marker


@pytest.mark.asyncio
async def test_non_numeric_override_falls_back_to_history_scan():
    bot_marker = _message(111, "GUESS CHAT Old Topic", author_id=42)
    channel = _channel([bot_marker])

    result = await resolve_marker_message(
        channel, bot_user_id=42, override_id="not-an-id"
    )

    assert result is bot_marker


@pytest.mark.asyncio
async def test_without_override_prefers_the_bots_own_marker():
    mod_marker = _message(222, "GUESS CHAT Mod Topic", author_id=7)
    bot_marker = _message(111, "GUESS CHAT Bot Topic", author_id=42)
    channel = _channel([mod_marker, bot_marker])

    result = await resolve_marker_message(channel, bot_user_id=42)

    assert result is bot_marker


@pytest.mark.asyncio
async def test_without_override_falls_back_to_a_non_bot_marker():
    mod_marker = _message(222, "GUESS CHAT Mod Topic", author_id=7)
    channel = _channel([mod_marker])

    result = await resolve_marker_message(channel, bot_user_id=42)

    assert result is mod_marker


@pytest.mark.asyncio
async def test_no_marker_found_returns_none():
    channel = _channel([_message(1, "just chatting")])

    assert await resolve_marker_message(channel, bot_user_id=42) is None


# ---------------------------------------------------------------------------
# marker_topic
# ---------------------------------------------------------------------------


def test_topic_comes_from_a_well_formed_marker():
    channel = _channel([], topic="Current Guess Chat: Something Else")
    msg = _message(1, "# GUESS CHAT\n# DND CHARACTERS")

    assert marker_topic(msg, channel) == "DND CHARACTERS"


def test_freeform_override_falls_back_to_the_channel_description():
    channel = _channel([], topic="Current Guess Chat: DnD Characters")
    msg = _message(1, "Hey everyone, this week's theme is up!")

    assert marker_topic(msg, channel) == "DnD Characters"


def test_freeform_override_without_a_channel_description_uses_the_message():
    channel = _channel([], topic=None)
    msg = _message(1, "Theme line one\nDnD Characters")

    assert marker_topic(msg, channel) == "DnD Characters"


# ---------------------------------------------------------------------------
# adopt_manual_announcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adopting_an_announcement_posts_nothing_to_the_submissions_channel():
    manual = _message(555, "GUESS CHAT DnD Characters")
    submissions_channel = _channel([], topic="Current Guess Chat: DnD Characters")
    submissions_channel.fetch_message = AsyncMock(return_value=manual)
    submissions_channel.send = AsyncMock()
    submissions_channel.guild = None

    mod_channel = MagicMock()
    mod_channel.send = AsyncMock()
    client = MagicMock()
    client.get_channel.return_value = mod_channel

    saved: dict = {}
    with patch.object(bot, "MARKER_MESSAGE_ID", "555"), \
            patch.object(bot, "BOT_MODE", "announce"), \
            patch.object(bot, "DISCORD_MOD_CHANNEL_ID", 3), \
            patch.object(bot, "load_state", return_value={}), \
            patch.object(bot, "save_state", side_effect=saved.update):
        await bot.adopt_manual_announcement(client, submissions_channel)

    submissions_channel.send.assert_not_awaited()
    mod_channel.send.assert_awaited_once()
    assert "DnD Characters" in mod_channel.send.await_args.args[0]
    assert saved["last_announced_topic"] == "DnD Characters"
    assert saved["marker_override_id"] == "555"


@pytest.mark.asyncio
async def test_check_mod_and_announce_short_circuits_on_override():
    submissions_channel = _channel([], topic="Current Guess Chat: New Topic")
    submissions_channel.send = AsyncMock()
    client = MagicMock()
    client.get_channel.return_value = submissions_channel

    with patch.object(bot, "MARKER_MESSAGE_ID", "555"), \
            patch.object(bot, "adopt_manual_announcement", AsyncMock()) as adopt:
        await bot.check_mod_and_announce(client)

    adopt.assert_awaited_once()
    submissions_channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_unfetchable_override_in_announce_mode_saves_nothing():
    submissions_channel = _channel([], topic="Current Guess Chat: DnD Characters")
    submissions_channel.fetch_message = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(), "not found")
    )
    submissions_channel.send = AsyncMock()
    client = MagicMock()

    with patch.object(bot, "MARKER_MESSAGE_ID", "555"), \
            patch.object(bot, "save_state") as save:
        await bot.adopt_manual_announcement(client, submissions_channel)

    save.assert_not_called()
    submissions_channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_posting_its_own_announcement_clears_a_stale_override():
    submissions_channel = _channel([], topic="Current Guess Chat: Next Topic")
    submissions_channel.send = AsyncMock(return_value=_message(777, "posted"))
    submissions_channel.guild = None
    client = MagicMock()
    client.get_channel.return_value = None  # no mod channel lookup needed

    saved: dict = {}
    prev_state = {"last_announced_topic": "DnD Characters", "marker_override_id": "555"}
    with patch.object(bot, "MARKER_MESSAGE_ID", None), \
            patch.object(bot, "BOT_MODE", "announce"), \
            patch.object(bot, "DISCORD_MOD_CHANNEL_ID", None), \
            patch.object(bot, "load_state", return_value=prev_state), \
            patch.object(bot, "save_state", side_effect=saved.update):
        client.get_channel.return_value = submissions_channel
        await bot.check_mod_and_announce(client)

    submissions_channel.send.assert_awaited_once()
    assert saved["last_announced_topic"] == "Next Topic"
    assert "marker_override_id" not in saved
