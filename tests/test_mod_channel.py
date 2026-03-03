"""Tests for channel-description announcement flow, reminder logic, and error routing."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Provide required env vars before importing the module
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

import discord
from weekly_slides_bot import (
    check_mod_and_announce,
    extract_topic,
    parse_channel_topic,
)


class TestExtractTopic:
    """Tests for the extract_topic helper."""

    def test_topic_on_same_line(self):
        assert extract_topic("GUESS CHAT Favourite Food") == "Favourite Food"

    def test_topic_on_next_line_with_heading(self):
        assert extract_topic("# GUESS CHAT\n# DnD Characters") == "DnD Characters"

    def test_unknown_when_no_topic(self):
        assert extract_topic("GUESS CHAT") == "Unknown"

    def test_heading_marker_with_topic_on_same_line(self):
        assert extract_topic("# GUESS CHAT DnD Characters") == "DnD Characters"

    def test_strips_markdown_from_second_line(self):
        assert extract_topic("# GUESS CHAT\n# LEAST FAVE POKEMON\n- @everyone") == "LEAST FAVE POKEMON"


class TestParseChannelTopic:
    """Tests for the parse_channel_topic helper."""

    def test_parses_standard_format(self):
        assert parse_channel_topic("Current Guess Chat: dnd character") == "dnd character"

    def test_parses_with_extra_spaces(self):
        assert parse_channel_topic("Current  Guess  Chat:   pizza ") == "pizza"

    def test_returns_none_for_empty_string(self):
        assert parse_channel_topic("") is None

    def test_returns_none_for_unrelated_text(self):
        assert parse_channel_topic("Welcome to the server!") is None

    def test_case_insensitive(self):
        assert parse_channel_topic("current guess chat: Movies") == "Movies"

    def test_returns_none_for_none_input(self):
        assert parse_channel_topic(None) is None


class TestCheckModAndAnnounce:
    """Tests for the channel-description based check_mod_and_announce function."""

    @pytest.mark.asyncio
    async def test_exits_when_submissions_channel_not_found(self):
        """Should return immediately if submissions channel cannot be resolved."""
        mock_client = MagicMock()
        mock_client.get_channel.return_value = None
        await check_mod_and_announce(mock_client)

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_exits_when_no_topic_in_description(self, _load):
        """Should return if the channel description doesn't contain a topic."""
        mock_channel = MagicMock()
        mock_channel.topic = "Welcome to the server!"
        mock_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.return_value = mock_channel

        await check_mod_and_announce(mock_client)

        mock_channel.send.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_posts_announcement_for_new_topic(self, _load, _save):
        """When channel description has a new topic, post GUESS CHAT message."""
        mock_channel = MagicMock()
        mock_channel.topic = "Current Guess Chat: Favourite Food"
        mock_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.return_value = mock_channel

        await check_mod_and_announce(mock_client)

        mock_channel.send.assert_called_once_with("GUESS CHAT Favourite Food")

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_saves_announced_topic(self, _load, mock_save):
        """After posting, the topic is persisted in state."""
        mock_channel = MagicMock()
        mock_channel.topic = "Current Guess Chat: Movies"
        mock_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.return_value = mock_channel

        await check_mod_and_announce(mock_client)

        mock_save.assert_called_once()
        saved_state = mock_save.call_args.args[0]
        assert saved_state["last_announced_topic"] == "Movies"

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={"last_announced_topic": "Favourite Food"})
    async def test_skips_already_announced_topic(self, _load, mock_save):
        """Should not re-post if the topic hasn't changed."""
        mock_channel = MagicMock()
        mock_channel.topic = "Current Guess Chat: Favourite Food"
        mock_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.return_value = mock_channel

        await check_mod_and_announce(mock_client)

        mock_channel.send.assert_not_called()
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={"last_announced_topic": "Old Topic"})
    async def test_announces_when_topic_changes(self, _load, _save):
        """When topic changes, a new announcement is posted."""
        mock_channel = MagicMock()
        mock_channel.topic = "Current Guess Chat: New Topic"
        mock_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.return_value = mock_channel

        await check_mod_and_announce(mock_client)

        mock_channel.send.assert_called_once_with("GUESS CHAT New Topic")


class TestFridayReminder:
    """Tests that generate_slides sends a reminder when the topic hasn't changed."""

    @staticmethod
    def _make_client(marker_msg, sub_msg, mod_channel=None, channel_topic=""):
        """Build a mock client with a bot user ID of 42."""
        bot_user = MagicMock()
        bot_user.id = 42

        call_count = 0

        async def history_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield marker_msg
            else:
                yield sub_msg

        mock_channel = MagicMock()
        mock_channel.history = history_side_effect
        mock_channel.guild = MagicMock()
        mock_channel.guild.id = 12345
        mock_channel.topic = channel_topic

        mock_results_channel = MagicMock()
        mock_results_channel.send = AsyncMock()

        def get_channel(cid):
            if cid == 1:
                return mock_channel
            if cid == 2:
                return mock_results_channel
            if cid == 3 and mod_channel is not None:
                return mod_channel
            return None

        mock_client = MagicMock()
        mock_client.user = bot_user
        mock_client.get_channel.side_effect = get_channel
        return mock_client, mock_results_channel, mod_channel

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck", return_value=[])
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={"topic": "Old Topic"})
    async def test_sends_reminder_when_topic_unchanged(
        self, _load, _gcs, _copy, _share, _build, _save
    ):
        """When channel description topic matches last known topic, remind mods."""
        from weekly_slides_bot import generate_slides

        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Old Topic"
        marker_msg.author = MagicMock()
        marker_msg.author.id = 42  # bot's own message

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "User"
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = MagicMock(display_name="User")

        mock_mod_channel = MagicMock()
        mock_mod_channel.send = AsyncMock()

        mock_client, _, _ = self._make_client(
            marker_msg, sub_msg,
            mod_channel=mock_mod_channel,
            channel_topic="Current Guess Chat: Old Topic",
        )
        await generate_slides(mock_client)

        mock_mod_channel.send.assert_called_once_with(
            "@Mods we haven't announced a new guess chat yet, is there a new one this week?"
        )

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck", return_value=[])
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={"topic": "Old Topic"})
    async def test_no_reminder_when_topic_changed(
        self, _load, _gcs, _copy, _share, _build, _save
    ):
        """When channel description topic differs from last known, no reminder sent."""
        from weekly_slides_bot import generate_slides

        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT New Topic"
        marker_msg.author = MagicMock()
        marker_msg.author.id = 42

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "User"
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = MagicMock(display_name="User")

        mock_mod_channel = MagicMock()
        mock_mod_channel.send = AsyncMock()

        mock_client, _, _ = self._make_client(
            marker_msg, sub_msg,
            mod_channel=mock_mod_channel,
            channel_topic="Current Guess Chat: New Topic",
        )
        await generate_slides(mock_client)

        mock_mod_channel.send.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", None)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck", return_value=[])
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={"topic": "Old Topic"})
    async def test_no_reminder_when_mod_channel_not_configured(
        self, _load, _gcs, _copy, _share, _build, _save
    ):
        """When DISCORD_MOD_CHANNEL_ID is not set, no reminder is sent."""
        from weekly_slides_bot import generate_slides

        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Old Topic"
        marker_msg.author = MagicMock()
        marker_msg.author.id = 42

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "User"
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = MagicMock(display_name="User")

        mock_client, _, _ = self._make_client(
            marker_msg, sub_msg,
            channel_topic="Current Guess Chat: Old Topic",
        )
        # Should not raise even without mod channel
        await generate_slides(mock_client)


class TestBotOnlyMarkerFilter:
    """Tests that generate_slides only considers the bot's own messages for the GUESS CHAT marker."""

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck", return_value=[])
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_ignores_non_bot_marker_messages(
        self, _load, _gcs, _copy, _share, _build, mock_save
    ):
        """Only GUESS CHAT messages from the bot itself should be used as markers."""
        from weekly_slides_bot import generate_slides

        bot_user = MagicMock()
        bot_user.id = 42

        # A non-bot user posted a GUESS CHAT message
        user_marker = MagicMock()
        user_marker.id = 100
        user_marker.content = "GUESS CHAT SomeOtherTopic"
        user_marker.author = MagicMock()
        user_marker.author.id = 999  # not the bot

        async def history_side_effect(*args, **kwargs):
            yield user_marker

        mock_channel = MagicMock()
        mock_channel.history = history_side_effect
        mock_channel.guild = MagicMock()
        mock_channel.guild.id = 12345
        mock_channel.topic = ""

        mock_client = MagicMock()
        mock_client.user = bot_user
        mock_client.get_channel.return_value = mock_channel

        await generate_slides(mock_client)

        # Should not have called save_state since no bot marker was found
        mock_save.assert_not_called()


class TestStatePreservation:
    """Tests that generate_slides preserves keys from other modes."""

    @staticmethod
    def _make_client(marker_msg, sub_msg):
        bot_user = MagicMock()
        bot_user.id = 42

        call_count = 0

        async def history_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield marker_msg
            else:
                yield sub_msg

        mock_channel = MagicMock()
        mock_channel.history = history_side_effect
        mock_channel.guild = MagicMock()
        mock_channel.guild.id = 12345
        mock_channel.topic = ""

        mock_results_channel = MagicMock()
        mock_results_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.user = bot_user
        mock_client.get_channel.side_effect = lambda cid: (
            mock_channel if cid == 1 else mock_results_channel
        )
        return mock_client

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck", return_value=[])
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={"last_announced_topic": "Movies"})
    async def test_generate_slides_preserves_announced_topic(
        self, _load, _gcs, _copy, _share, _build, mock_save
    ):
        """generate_slides must not erase last_announced_topic from state."""
        from weekly_slides_bot import generate_slides

        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test"
        marker_msg.author = MagicMock()
        marker_msg.author.id = 42  # bot's own message

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "User"
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = MagicMock(display_name="User")

        mock_client = self._make_client(marker_msg, sub_msg)
        await generate_slides(mock_client)

        mock_save.assert_called()
        saved_state = mock_save.call_args.args[0]
        assert saved_state.get("last_announced_topic") == "Movies"
        assert "marker_id" in saved_state


class TestErrorRoutingToModChannel:
    """Tests that errors are sent to the mod channel when configured."""

    @staticmethod
    def _make_client(marker_msg, sub_msg, mod_channel_id=None):
        bot_user = MagicMock()
        bot_user.id = 42

        call_count = 0

        async def history_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield marker_msg
            else:
                yield sub_msg

        mock_channel = MagicMock()
        mock_channel.history = history_side_effect
        mock_channel.guild = MagicMock()
        mock_channel.guild.id = 12345
        mock_channel.topic = ""

        mock_results_channel = MagicMock()
        mock_results_channel.send = AsyncMock()

        mock_mod_channel = MagicMock()
        mock_mod_channel.send = AsyncMock()

        def get_channel(cid):
            if cid == 1:
                return mock_channel
            if cid == 2:
                return mock_results_channel
            if mod_channel_id is not None and cid == mod_channel_id:
                return mock_mod_channel
            return None

        mock_client = MagicMock()
        mock_client.user = bot_user
        mock_client.get_channel.side_effect = get_channel
        return mock_client, mock_results_channel, mock_mod_channel

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck", return_value=[{
        "author": "Dave",
        "issue": "Image upload failed",
        "slide_number": 2,
        "slide_id": "slide_abc",
        "message_id": "200",
    }])
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_errors_sent_to_mod_channel(
        self, _load, _gcs, _copy, _share, _build, _save
    ):
        """When DISCORD_MOD_CHANNEL_ID is set, errors go to the mod channel."""
        from weekly_slides_bot import generate_slides

        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test"
        marker_msg.author = MagicMock()
        marker_msg.author.id = 42

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "Dave"
        sub_msg.guild = MagicMock()
        sub_msg.guild.id = 12345
        sub_msg.guild.get_member.return_value = MagicMock(display_name="Dave")

        mock_client, mock_results_channel, mock_mod_channel = self._make_client(
            marker_msg, sub_msg, mod_channel_id=3
        )
        await generate_slides(mock_client)

        # Results message goes to results channel
        results_calls = mock_results_channel.send.call_args_list
        assert len(results_calls) == 1  # only the results message

        # Error notification goes to mod channel
        mod_calls = mock_mod_channel.send.call_args_list
        assert len(mod_calls) == 1
        assert "Dave" in mod_calls[0].args[0]

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", None)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck", return_value=[{
        "author": "Dave",
        "issue": "Image upload failed",
        "slide_number": 2,
        "slide_id": "slide_abc",
        "message_id": "200",
    }])
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_errors_fall_back_to_results_channel(
        self, _load, _gcs, _copy, _share, _build, _save
    ):
        """When DISCORD_MOD_CHANNEL_ID is not set, errors go to results channel."""
        from weekly_slides_bot import generate_slides

        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test"
        marker_msg.author = MagicMock()
        marker_msg.author.id = 42

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "Dave"
        sub_msg.guild = MagicMock()
        sub_msg.guild.id = 12345
        sub_msg.guild.get_member.return_value = MagicMock(display_name="Dave")

        mock_client, mock_results_channel, _ = self._make_client(
            marker_msg, sub_msg, mod_channel_id=None
        )
        await generate_slides(mock_client)

        # Both results + error go to results channel
        results_calls = mock_results_channel.send.call_args_list
        assert len(results_calls) == 2  # results message + error notification


class TestOneShotClientMode:
    """Tests that OneShotClient dispatches based on BOT_MODE."""

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.generate_slides", new_callable=AsyncMock)
    @patch("weekly_slides_bot.BOT_MODE", "slides")
    async def test_slides_mode(self, mock_gen):
        from weekly_slides_bot import OneShotClient
        client = MagicMock(spec=OneShotClient)
        client.close = AsyncMock()
        client.on_ready = OneShotClient.on_ready.__get__(client, OneShotClient)
        await client.on_ready()
        mock_gen.assert_called_once()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.check_mod_and_announce", new_callable=AsyncMock)
    @patch("weekly_slides_bot.BOT_MODE", "announce")
    async def test_announce_mode(self, mock_announce):
        from weekly_slides_bot import OneShotClient
        client = MagicMock(spec=OneShotClient)
        client.close = AsyncMock()
        client.on_ready = OneShotClient.on_ready.__get__(client, OneShotClient)
        await client.on_ready()
        mock_announce.assert_called_once()
