"""Tests for channel-description announcement flow, reminder logic, and error routing."""

from __future__ import annotations

import datetime
import os
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Provide required env vars before importing the module
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

import discord
from weekly_slides_bot import (
    build_announcement_message,
    _resolve_mod_mention,
    check_mod_and_announce,
    extract_topic,
    next_friday_deadline_unix,
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


class TestBuildAnnouncementMessage:
    """Tests for the build_announcement_message helper."""

    def test_contains_guess_chat_heading(self):
        msg = build_announcement_message("Favourite Food")
        assert "# GUESS CHAT" in msg

    def test_topic_uppercased_as_heading(self):
        msg = build_announcement_message("Favourite Food")
        assert "# FAVOURITE FOOD" in msg

    def test_contains_everyone_ping(self):
        msg = build_announcement_message("Favourite Food")
        assert "@everyone" in msg

    def test_contains_submission_tag(self):
        msg = build_announcement_message("Favourite Food")
        assert "**SUBMISSION**" in msg

    def test_contains_deadline_timestamp(self):
        msg = build_announcement_message("Favourite Food")
        assert "deadline: <t:" in msg
        assert ":F>" in msg

    def test_deadline_is_integer_timestamp(self):
        msg = build_announcement_message("Favourite Food")
        match = re.search(r"<t:(\d+):F>", msg)
        assert match is not None, "Discord timestamp not found in message"
        assert int(match.group(1)) > 0


class TestNextFridayDeadlineUnix:
    """Tests for the next_friday_deadline_unix helper."""

    def test_returns_integer(self):
        ts = next_friday_deadline_unix()
        assert isinstance(ts, int)

    def test_returns_a_friday(self):
        ts = next_friday_deadline_unix()
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        assert dt.weekday() == 4  # 4 == Friday

    def test_next_friday_from_non_friday(self):
        # Monday 2026-03-09 12:00 UTC
        ref = datetime.datetime(2026, 3, 9, 12, 0, tzinfo=datetime.timezone.utc)
        ts = next_friday_deadline_unix(ref)
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        assert dt.weekday() == 4

    def test_friday_before_deadline_returns_same_friday(self):
        """If it's Friday before 11:30 UK, the deadline is the same Friday."""
        # Friday 2026-03-13 09:00 UTC (10:00 UK / BST doesn't start until late March)
        ref = datetime.datetime(2026, 3, 13, 9, 0, tzinfo=datetime.timezone.utc)
        ts = next_friday_deadline_unix(ref)
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        assert dt.weekday() == 4
        assert dt.date() == datetime.date(2026, 3, 13)

    def test_friday_after_deadline_returns_next_friday(self):
        """If it's Friday at or after 11:30 UK, the deadline is the following Friday."""
        # Friday 2026-03-13 12:00 UTC (12:00 UK in winter = after 11:30)
        ref = datetime.datetime(2026, 3, 13, 12, 0, tzinfo=datetime.timezone.utc)
        ts = next_friday_deadline_unix(ref)
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        assert dt.weekday() == 4
        assert dt.date() == datetime.date(2026, 3, 20)


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

        mock_channel.send.assert_called_once()
        sent_text = mock_channel.send.call_args.args[0]
        assert "# GUESS CHAT" in sent_text
        assert "FAVOURITE FOOD" in sent_text
        assert "@everyone" in sent_text
        assert "**SUBMISSION**" in sent_text
        assert "deadline:" in sent_text

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
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={"last_announced_topic": "Favourite Food"})
    async def test_skips_already_announced_topic(self, _load, mock_save):
        """Should not re-post if the topic hasn't changed."""
        mock_channel = MagicMock()
        mock_channel.topic = "Current Guess Chat: Favourite Food"
        mock_channel.guild = None
        mock_channel.send = AsyncMock()

        mock_mod_channel = MagicMock()
        mock_mod_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = lambda cid: {
            1: mock_channel,
            3: mock_mod_channel,
        }.get(cid)

        await check_mod_and_announce(mock_client)

        mock_channel.send.assert_not_called()
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={"last_announced_topic": "Old Topic"})
    async def test_sends_reminder_when_topic_unchanged(self, _load, _save):
        """When topic hasn't changed, send a reminder to the mod channel."""
        mock_channel = MagicMock()
        mock_channel.topic = "Current Guess Chat: Old Topic"
        mock_channel.guild = None  # no guild → fallback mention
        mock_channel.send = AsyncMock()

        mock_mod_channel = MagicMock()
        mock_mod_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = lambda cid: {
            1: mock_channel,
            3: mock_mod_channel,
        }.get(cid)

        await check_mod_and_announce(mock_client)

        mock_mod_channel.send.assert_called_once()
        reminder = mock_mod_channel.send.call_args.args[0]
        assert "haven't announced a new guess chat" in reminder.lower()
        assert "new one this week" in reminder.lower()
        mock_channel.send.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", None)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={"last_announced_topic": "Old Topic"})
    async def test_no_reminder_when_mod_channel_not_configured(self, _load, _save):
        """When DISCORD_MOD_CHANNEL_ID is not set, no reminder is sent."""
        mock_channel = MagicMock()
        mock_channel.topic = "Current Guess Chat: Old Topic"
        mock_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.return_value = mock_channel

        # Should not raise
        await check_mod_and_announce(mock_client)
        mock_channel.send.assert_not_called()

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

        mock_channel.send.assert_called_once()
        sent_text = mock_channel.send.call_args.args[0]
        assert "# GUESS CHAT" in sent_text
        assert "NEW TOPIC" in sent_text
        assert "@everyone" in sent_text
        assert "**SUBMISSION**" in sent_text
        assert "deadline:" in sent_text

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_sends_confirmation_to_mod_channel(self, _load, _save):
        """After posting a new announcement, a confirmation is sent to the mod channel."""
        posted_msg = MagicMock()
        posted_msg.id = 12345

        mock_channel = MagicMock()
        mock_channel.topic = "Current Guess Chat: Favourite Food"
        mock_channel.send = AsyncMock(return_value=posted_msg)
        mock_channel.guild = MagicMock()
        mock_channel.guild.id = 99999
        mock_channel.guild.roles = []  # no roles → fallback mention

        mock_mod_channel = MagicMock()
        mock_mod_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = lambda cid: {
            1: mock_channel,
            3: mock_mod_channel,
        }.get(cid)

        await check_mod_and_announce(mock_client)

        mock_mod_channel.send.assert_called_once()
        confirmation = mock_mod_channel.send.call_args.args[0]
        assert "Favourite Food" in confirmation
        assert "extras" in confirmation.lower()
        assert "https://discord.com/channels/99999/1/12345" in confirmation

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", None)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_no_confirmation_when_mod_channel_not_configured(self, _load, _save):
        """When DISCORD_MOD_CHANNEL_ID is not set, no confirmation is sent."""
        posted_msg = MagicMock()
        posted_msg.id = 12345

        mock_channel = MagicMock()
        mock_channel.topic = "Current Guess Chat: Movies"
        mock_channel.send = AsyncMock(return_value=posted_msg)

        mock_client = MagicMock()
        mock_client.get_channel.return_value = mock_channel

        await check_mod_and_announce(mock_client)

        # Only the GUESS CHAT announcement should be sent, no mod confirmation
        mock_channel.send.assert_called_once()
        sent_text = mock_channel.send.call_args.args[0]
        assert "# GUESS CHAT" in sent_text
        assert "MOVIES" in sent_text
        assert "@everyone" in sent_text
        assert "**SUBMISSION**" in sent_text
        assert "deadline:" in sent_text

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_confirmation_uses_role_mention_when_role_exists(self, _load, _save):
        """Confirmation message uses <@&ROLE_ID> when the mod role is found in the guild."""
        posted_msg = MagicMock()
        posted_msg.id = 99

        mod_role = MagicMock(spec=discord.Role)
        mod_role.name = "Mod"
        mod_role.id = 55555
        mod_role.mention = "<@&55555>"

        guild = MagicMock(spec=discord.Guild)
        guild.id = 11111
        guild.roles = [mod_role]

        mock_channel = MagicMock()
        mock_channel.topic = "Current Guess Chat: Cats"
        mock_channel.send = AsyncMock(return_value=posted_msg)
        mock_channel.guild = guild

        mock_mod_channel = MagicMock()
        mock_mod_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = lambda cid: {
            1: mock_channel,
            3: mock_mod_channel,
        }.get(cid)

        await check_mod_and_announce(mock_client)

        mock_mod_channel.send.assert_called_once()
        confirmation = mock_mod_channel.send.call_args.args[0]
        assert "<@&55555>" in confirmation

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_confirmation_falls_back_when_role_not_found(self, _load, _save):
        """Confirmation message falls back to @MOD_ROLE_NAME when role is not in guild."""
        posted_msg = MagicMock()
        posted_msg.id = 88

        guild = MagicMock(spec=discord.Guild)
        guild.id = 22222
        guild.roles = []  # no roles

        mock_channel = MagicMock()
        mock_channel.topic = "Current Guess Chat: Dogs"
        mock_channel.send = AsyncMock(return_value=posted_msg)
        mock_channel.guild = guild

        mock_mod_channel = MagicMock()
        mock_mod_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = lambda cid: {
            1: mock_channel,
            3: mock_mod_channel,
        }.get(cid)

        await check_mod_and_announce(mock_client)

        mock_mod_channel.send.assert_called_once()
        confirmation = mock_mod_channel.send.call_args.args[0]
        # Should NOT contain a numeric role-mention form
        assert "<@&" not in confirmation
        # Should contain the plain @RoleName fallback
        assert "@Mod" in confirmation

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={"last_announced_topic": "Old"})
    async def test_reminder_uses_role_mention_when_role_exists(self, _load, _save):
        """Reminder message uses <@&ROLE_ID> when the mod role is found in the guild."""
        mod_role = MagicMock(spec=discord.Role)
        mod_role.name = "Mod"
        mod_role.id = 44444
        mod_role.mention = "<@&44444>"

        guild = MagicMock(spec=discord.Guild)
        guild.roles = [mod_role]

        mock_channel = MagicMock()
        mock_channel.topic = "Current Guess Chat: Old"
        mock_channel.guild = guild
        mock_channel.send = AsyncMock()

        mock_mod_channel = MagicMock()
        mock_mod_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = lambda cid: {
            1: mock_channel,
            3: mock_mod_channel,
        }.get(cid)

        await check_mod_and_announce(mock_client)

        mock_mod_channel.send.assert_called_once()
        reminder = mock_mod_channel.send.call_args.args[0]
        assert "<@&44444>" in reminder


class TestResolveModMention:
    """Unit tests for the _resolve_mod_mention helper."""

    def test_returns_role_mention_when_role_found(self):
        """Returns role.mention when the role exists in the guild."""
        mod_role = MagicMock(spec=discord.Role)
        mod_role.name = "Mod"
        mod_role.mention = "<@&12345>"

        guild = MagicMock(spec=discord.Guild)
        guild.roles = [mod_role]

        assert _resolve_mod_mention(guild) == "<@&12345>"

    def test_returns_fallback_when_role_not_found(self):
        """Returns @MOD_ROLE_NAME when no matching role exists."""
        guild = MagicMock(spec=discord.Guild)
        guild.roles = []

        result = _resolve_mod_mention(guild)
        assert result.startswith("@")
        assert "<@&" not in result

    def test_returns_fallback_when_guild_is_none(self):
        """Returns @MOD_ROLE_NAME when guild is None."""
        result = _resolve_mod_mention(None)
        assert result.startswith("@")
        assert "<@&" not in result


class TestMarkerFallback:
    """Tests that generate_slides prefers bot markers but falls back to any marker."""

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck", return_value=[])
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_falls_back_to_non_bot_marker(
        self, _load, _gcs, _copy, _share, mock_build, mock_save
    ):
        """When no bot-authored marker exists, a legacy non-bot marker is used."""
        from weekly_slides_bot import generate_slides

        bot_user = MagicMock()
        bot_user.id = 42

        # A non-bot user posted a GUESS CHAT message (legacy)
        user_marker = MagicMock()
        user_marker.id = 100
        user_marker.content = "GUESS CHAT LegacyTopic"
        user_marker.author = MagicMock()
        user_marker.author.id = 999  # not the bot

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 888
        sub_msg.author.display_name = "User"
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = MagicMock(display_name="User")

        call_count = 0

        async def history_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield user_marker
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

        await generate_slides(mock_client)

        # The legacy marker should have been used — save_state should be called
        mock_save.assert_called()
        saved_state = mock_save.call_args.args[0]
        assert saved_state["marker_id"] == "100"

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck", return_value=[])
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_prefers_bot_marker_over_legacy(
        self, _load, _gcs, _copy, _share, mock_build, mock_save
    ):
        """When both a bot marker and a legacy marker exist, the bot marker wins."""
        from weekly_slides_bot import generate_slides

        bot_user = MagicMock()
        bot_user.id = 42

        # Legacy non-bot marker (older, appears later in history)
        legacy_marker = MagicMock()
        legacy_marker.id = 50
        legacy_marker.content = "GUESS CHAT OldTopic"
        legacy_marker.author = MagicMock()
        legacy_marker.author.id = 999

        # Bot-authored marker (newer, appears first in history)
        bot_marker = MagicMock()
        bot_marker.id = 100
        bot_marker.content = "GUESS CHAT NewTopic"
        bot_marker.author = MagicMock()
        bot_marker.author.id = 42

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 888
        sub_msg.author.display_name = "User"
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = MagicMock(display_name="User")

        call_count = 0

        async def history_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: marker scan — bot marker appears first (most recent)
                yield bot_marker
                yield legacy_marker
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

        await generate_slides(mock_client)

        mock_save.assert_called()
        saved_state = mock_save.call_args.args[0]
        assert saved_state["marker_id"] == "100"  # bot marker, not legacy


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

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.generate_slides", new_callable=AsyncMock)
    @patch("weekly_slides_bot.BOT_MODE", "preview")
    async def test_preview_mode_calls_generate_slides(self, mock_gen):
        """preview mode must call generate_slides (not check_mod_and_announce)."""
        from weekly_slides_bot import OneShotClient
        client = MagicMock(spec=OneShotClient)
        client.close = AsyncMock()
        client.on_ready = OneShotClient.on_ready.__get__(client, OneShotClient)
        await client.on_ready()
        mock_gen.assert_called_once()


class TestPreviewModeRouting:
    """Tests that preview mode routes the results message to the mod channel."""

    @staticmethod
    def _make_client(marker_msg, sub_msg, mod_channel_id=3):
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
        mock_client.get_channel.side_effect = get_channel
        return mock_client, mock_results_channel, mock_mod_channel

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.BOT_MODE", "preview")
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck", return_value=[])
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_preview_posts_to_mod_channel(self, _load, _gcs, _copy, _share, _build, _save):
        """In preview mode the results message is sent to the mod channel."""
        from weekly_slides_bot import generate_slides

        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test"

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "User"
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = MagicMock(display_name="User")

        mock_client, mock_results_channel, mock_mod_channel = self._make_client(marker_msg, sub_msg)
        await generate_slides(mock_client)

        # Results message goes to mod channel, not public results channel
        mock_mod_channel.send.assert_called_once()
        mock_results_channel.send.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.BOT_MODE", "slides")
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck", return_value=[])
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_slides_mode_still_posts_to_results_channel(self, _load, _gcs, _copy, _share, _build, _save):
        """In normal slides mode the results message still goes to the results channel."""
        from weekly_slides_bot import generate_slides

        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test"

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "User"
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = MagicMock(display_name="User")

        mock_client, mock_results_channel, mock_mod_channel = self._make_client(marker_msg, sub_msg)
        await generate_slides(mock_client)

        # Results message goes to public results channel
        mock_results_channel.send.assert_called_once()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.BOT_MODE", "preview")
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", None)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck", return_value=[])
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_preview_without_mod_channel_skips_post(self, _load, _gcs, _copy, _share, _build, _save):
        """In preview mode without DISCORD_MOD_CHANNEL_ID nothing is posted."""
        from weekly_slides_bot import generate_slides

        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test"

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "User"
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = MagicMock(display_name="User")

        mock_client, mock_results_channel, mock_mod_channel = self._make_client(
            marker_msg, sub_msg, mod_channel_id=None
        )
        await generate_slides(mock_client)

        mock_results_channel.send.assert_not_called()
        mock_mod_channel.send.assert_not_called()

    # ------------------------------------------------------------------
    # Preview with no *new* submissions (all already processed)
    # ------------------------------------------------------------------

    @staticmethod
    def _make_client_with_processed_submission(marker_msg, sub_msg, mod_channel_id=3):
        """Client whose channel history yields the marker and a submission
        that is already recorded in processed_ids (simulating no new updates)."""
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
        mock_client.get_channel.side_effect = get_channel
        return mock_client, mock_results_channel, mock_mod_channel

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.BOT_MODE", "preview")
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch(
        "weekly_slides_bot.load_state",
        return_value={
            "marker_id": "100",
            "named_pres_id": "existing_named",
            "anon_pres_id": "existing_anon",
            "processed_ids": ["200"],
            "topic": "Test",
        },
    )
    async def test_preview_posts_even_when_no_new_submissions(
        self, _load, _gcs, _copy, _share, _save
    ):
        """Preview mode should post to the mod channel even when every
        submission has already been processed (no new updates)."""
        from weekly_slides_bot import generate_slides

        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test"

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "User"
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = MagicMock(display_name="User")

        mock_client, mock_results_channel, mock_mod_channel = (
            self._make_client_with_processed_submission(marker_msg, sub_msg)
        )
        await generate_slides(mock_client)

        mock_mod_channel.send.assert_called_once()
        mock_results_channel.send.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.BOT_MODE", "slides")
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch(
        "weekly_slides_bot.load_state",
        return_value={
            "marker_id": "100",
            "named_pres_id": "existing_named",
            "anon_pres_id": "existing_anon",
            "processed_ids": ["200"],
            "topic": "Test",
        },
    )
    async def test_slides_mode_still_exits_when_no_new_submissions(
        self, _load, _gcs, _copy, _share, _save
    ):
        """In normal slides mode the bot should still exit early when there
        are no new submissions (existing behaviour unchanged)."""
        from weekly_slides_bot import generate_slides

        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test"

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "User"
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = MagicMock(display_name="User")

        mock_client, mock_results_channel, mock_mod_channel = (
            self._make_client_with_processed_submission(marker_msg, sub_msg)
        )
        await generate_slides(mock_client)

        mock_results_channel.send.assert_not_called()
        mock_mod_channel.send.assert_not_called()

    # ------------------------------------------------------------------
    # Preview with no submissions at all (but existing decks in state)
    # ------------------------------------------------------------------

    @staticmethod
    def _make_client_with_marker_no_submissions(marker_msg, mod_channel_id=3):
        """Client whose channel history yields only the marker (no submissions)."""
        call_count = 0

        async def history_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield marker_msg
            # second call: no submissions

        mock_channel = MagicMock()
        mock_channel.history = history_side_effect
        mock_channel.guild = MagicMock()
        mock_channel.guild.id = 12345

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
        mock_client.get_channel.side_effect = get_channel
        return mock_client, mock_results_channel, mock_mod_channel

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.BOT_MODE", "preview")
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch(
        "weekly_slides_bot.load_state",
        return_value={
            "marker_id": "100",
            "named_pres_id": "existing_named",
            "anon_pres_id": "existing_anon",
            "topic": "Old Topic",
        },
    )
    async def test_preview_posts_existing_decks_when_no_submissions(self, _load):
        """Preview mode should post existing deck links from state even when
        there are no SUBMISSION messages at all (same round)."""
        from weekly_slides_bot import generate_slides

        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test"

        mock_client, mock_results_channel, mock_mod_channel = (
            self._make_client_with_marker_no_submissions(marker_msg)
        )
        await generate_slides(mock_client)

        mock_mod_channel.send.assert_called_once()
        mock_results_channel.send.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.BOT_MODE", "preview")
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch(
        "weekly_slides_bot.load_state",
        return_value={
            "marker_id": "50",
            "named_pres_id": "existing_named",
            "anon_pres_id": "existing_anon",
            "topic": "Old Topic",
        },
    )
    async def test_preview_new_round_no_submissions_posts_notice(self, _load):
        """Preview mode should notify the mod channel about a new round
        instead of re-posting stale deck links from the old round."""
        from weekly_slides_bot import generate_slides

        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT New Topic"

        mock_client, mock_results_channel, mock_mod_channel = (
            self._make_client_with_marker_no_submissions(marker_msg)
        )
        await generate_slides(mock_client)

        mock_mod_channel.send.assert_called_once()
        sent_text = mock_mod_channel.send.call_args[0][0]
        assert "New Topic" in sent_text
        assert "No submissions yet" in sent_text
        mock_results_channel.send.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.BOT_MODE", "preview")
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_preview_no_submissions_no_state_posts_new_round_notice(self, _load):
        """Preview mode with no submissions and no state should post a
        new-round notice (empty state means the marker is always new)."""
        from weekly_slides_bot import generate_slides

        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test"

        mock_client, mock_results_channel, mock_mod_channel = (
            self._make_client_with_marker_no_submissions(marker_msg)
        )
        await generate_slides(mock_client)

        mock_mod_channel.send.assert_called_once()
        sent_text = mock_mod_channel.send.call_args[0][0]
        assert "No submissions yet" in sent_text
        mock_results_channel.send.assert_not_called()
