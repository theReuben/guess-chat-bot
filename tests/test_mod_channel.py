"""Tests for mod-channel announcement flow and error routing."""

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
from weekly_slides_bot import _has_mod_role, check_mod_and_announce


class TestHasModRole:
    """Tests for the _has_mod_role helper."""

    def test_returns_true_when_member_has_mod_role(self):
        role = MagicMock()
        role.name = "Mod"
        member = MagicMock(spec=discord.Member)
        member.roles = [MagicMock(name="@everyone"), role]
        assert _has_mod_role(member) is True

    def test_returns_false_when_member_lacks_mod_role(self):
        role = MagicMock()
        role.name = "Regular"
        member = MagicMock(spec=discord.Member)
        member.roles = [role]
        assert _has_mod_role(member) is False

    def test_returns_false_for_empty_roles(self):
        member = MagicMock(spec=discord.Member)
        member.roles = []
        assert _has_mod_role(member) is False

    def test_case_sensitive(self):
        role = MagicMock()
        role.name = "mod"
        member = MagicMock(spec=discord.Member)
        member.roles = [role]
        assert _has_mod_role(member) is False


class TestCheckModAndAnnounce:
    """Tests for the check_mod_and_announce function."""

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", None)
    async def test_exits_when_mod_channel_not_configured(self):
        """Should return immediately if mod channel ID is not set."""
        mock_client = MagicMock()
        await check_mod_and_announce(mock_client)
        mock_client.get_channel.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    async def test_exits_when_mod_channel_not_found(self):
        """Should return if the mod channel cannot be resolved."""
        mock_client = MagicMock()
        mock_client.get_channel.return_value = None
        await check_mod_and_announce(mock_client)
        mock_client.get_channel.assert_called_once_with(3)

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={})
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    async def test_forwards_announcement_to_submissions_channel(self, _load, _save):
        """When a Mod posts GUESS CHAT, the message is forwarded to the submissions channel."""
        mod_role = MagicMock()
        mod_role.name = "Mod"

        mod_author = MagicMock(spec=discord.Member)
        mod_author.roles = [mod_role]

        mod_msg = MagicMock()
        mod_msg.id = 500
        mod_msg.content = "GUESS CHAT Favourite Food\nSubmit your favourite food with an image!"
        mod_msg.author = mod_author

        async def mod_history(*args, **kwargs):
            yield mod_msg

        mock_mod_channel = MagicMock()
        mock_mod_channel.history = mod_history

        mock_submissions_channel = MagicMock()
        mock_submissions_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = lambda cid: {
            3: mock_mod_channel,
            1: mock_submissions_channel,
        }.get(cid)

        await check_mod_and_announce(mock_client)

        mock_submissions_channel.send.assert_called_once_with(
            "GUESS CHAT Favourite Food\nSubmit your favourite food with an image!"
        )

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={})
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    async def test_saves_announced_message_id(self, _load, mock_save):
        """After forwarding, the mod message ID is persisted in state."""
        mod_role = MagicMock()
        mod_role.name = "Mod"

        mod_author = MagicMock(spec=discord.Member)
        mod_author.roles = [mod_role]

        mod_msg = MagicMock()
        mod_msg.id = 500
        mod_msg.content = "GUESS CHAT Topic"
        mod_msg.author = mod_author

        async def mod_history(*args, **kwargs):
            yield mod_msg

        mock_mod_channel = MagicMock()
        mock_mod_channel.history = mod_history

        mock_submissions_channel = MagicMock()
        mock_submissions_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = lambda cid: {
            3: mock_mod_channel,
            1: mock_submissions_channel,
        }.get(cid)

        await check_mod_and_announce(mock_client)

        mock_save.assert_called_once()
        saved_state = mock_save.call_args.args[0]
        assert saved_state["last_announced_mod_msg_id"] == "500"

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={"last_announced_mod_msg_id": "500"})
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    async def test_skips_already_announced_message(self, _load, mock_save):
        """Should not re-forward a message that was already announced."""
        mod_role = MagicMock()
        mod_role.name = "Mod"

        mod_author = MagicMock(spec=discord.Member)
        mod_author.roles = [mod_role]

        mod_msg = MagicMock()
        mod_msg.id = 500
        mod_msg.content = "GUESS CHAT Topic"
        mod_msg.author = mod_author

        async def mod_history(*args, **kwargs):
            yield mod_msg

        mock_mod_channel = MagicMock()
        mock_mod_channel.history = mod_history

        mock_submissions_channel = MagicMock()
        mock_submissions_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = lambda cid: {
            3: mock_mod_channel,
            1: mock_submissions_channel,
        }.get(cid)

        await check_mod_and_announce(mock_client)

        mock_submissions_channel.send.assert_not_called()
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={})
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    async def test_ignores_non_mod_messages(self, _load, _save):
        """Only messages from members with the Mod role should be considered."""
        regular_role = MagicMock()
        regular_role.name = "Regular"

        regular_author = MagicMock(spec=discord.Member)
        regular_author.roles = [regular_role]

        regular_msg = MagicMock()
        regular_msg.id = 600
        regular_msg.content = "GUESS CHAT Topic"
        regular_msg.author = regular_author

        async def mod_history(*args, **kwargs):
            yield regular_msg

        mock_mod_channel = MagicMock()
        mock_mod_channel.history = mod_history

        mock_submissions_channel = MagicMock()
        mock_submissions_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = lambda cid: {
            3: mock_mod_channel,
            1: mock_submissions_channel,
        }.get(cid)

        await check_mod_and_announce(mock_client)

        mock_submissions_channel.send.assert_not_called()
        _save.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={})
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    async def test_ignores_non_marker_messages_from_mods(self, _load, _save):
        """Mod messages that do not match GUESS CHAT pattern are skipped."""
        mod_role = MagicMock()
        mod_role.name = "Mod"

        mod_author = MagicMock(spec=discord.Member)
        mod_author.roles = [mod_role]

        mod_msg = MagicMock()
        mod_msg.id = 700
        mod_msg.content = "Hey everyone, no guess chat this week"
        mod_msg.author = mod_author

        async def mod_history(*args, **kwargs):
            yield mod_msg

        mock_mod_channel = MagicMock()
        mock_mod_channel.history = mod_history

        mock_submissions_channel = MagicMock()
        mock_submissions_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = lambda cid: {
            3: mock_mod_channel,
            1: mock_submissions_channel,
        }.get(cid)

        await check_mod_and_announce(mock_client)

        mock_submissions_channel.send.assert_not_called()
        _save.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={})
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    async def test_ignores_non_member_authors(self, _load, _save):
        """Non-Member authors (e.g. User objects) are skipped when fetch_member fails."""
        # A User (not Member) has no roles
        user_author = MagicMock(spec=discord.User)
        user_author.id = 900

        user_msg = MagicMock()
        user_msg.id = 800
        user_msg.content = "GUESS CHAT Topic"
        user_msg.author = user_author
        user_msg.guild = MagicMock()
        user_msg.guild.fetch_member = AsyncMock(
            side_effect=discord.NotFound(MagicMock(), "not found")
        )

        async def mod_history(*args, **kwargs):
            yield user_msg

        mock_mod_channel = MagicMock()
        mock_mod_channel.history = mod_history

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = lambda cid: {
            3: mock_mod_channel,
            1: MagicMock(),
        }.get(cid)

        await check_mod_and_announce(mock_client)

        _save.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={})
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    async def test_resolves_user_author_via_fetch_member(self, _load, _save):
        """When msg.author is a User, the bot resolves to Member via fetch_member."""
        mod_role = MagicMock()
        mod_role.name = "Mod"

        resolved_member = MagicMock(spec=discord.Member)
        resolved_member.roles = [mod_role]

        user_author = MagicMock(spec=discord.User)
        user_author.id = 900

        user_msg = MagicMock()
        user_msg.id = 800
        user_msg.content = "GUESS CHAT Topic"
        user_msg.author = user_author
        user_msg.guild = MagicMock()
        user_msg.guild.fetch_member = AsyncMock(return_value=resolved_member)

        async def mod_history(*args, **kwargs):
            yield user_msg

        mock_mod_channel = MagicMock()
        mock_mod_channel.history = mod_history

        mock_submissions_channel = MagicMock()
        mock_submissions_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = lambda cid: {
            3: mock_mod_channel,
            1: mock_submissions_channel,
        }.get(cid)

        await check_mod_and_announce(mock_client)

        mock_submissions_channel.send.assert_called_once_with("GUESS CHAT Topic")


class TestStatePreservation:
    """Tests that generate_slides preserves keys from other modes."""

    @staticmethod
    def _make_client(marker_msg, sub_msg):
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

        mock_client = MagicMock()
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
    @patch("weekly_slides_bot.load_state", return_value={"last_announced_mod_msg_id": "500"})
    async def test_generate_slides_preserves_announced_mod_msg_id(
        self, _load, _gcs, _copy, _share, _build, mock_save
    ):
        """generate_slides must not erase last_announced_mod_msg_id from state."""
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

        mock_client = self._make_client(marker_msg, sub_msg)
        await generate_slides(mock_client)

        mock_save.assert_called()
        saved_state = mock_save.call_args.args[0]
        assert saved_state.get("last_announced_mod_msg_id") == "500"
        assert "marker_id" in saved_state


class TestErrorRoutingToModChannel:
    """Tests that errors are sent to the mod channel when configured."""

    @staticmethod
    def _make_client(marker_msg, sub_msg, mod_channel_id=None):
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
