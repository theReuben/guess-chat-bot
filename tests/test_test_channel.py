"""Tests for test_slides and test_announce BOT_MODE routing."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Provide required env vars before importing the module
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")


# ---------------------------------------------------------------------------
# OneShotClient dispatch tests
# ---------------------------------------------------------------------------


class TestOneShotClientTestModes:
    """Tests that OneShotClient dispatches test_slides and test_announce correctly."""

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.generate_slides", new_callable=AsyncMock)
    @patch("weekly_slides_bot.BOT_MODE", "test_slides")
    async def test_test_slides_dispatches_generate_slides(self, mock_gen):
        from weekly_slides_bot import OneShotClient

        client = MagicMock(spec=OneShotClient)
        client.close = AsyncMock()
        client.on_ready = OneShotClient.on_ready.__get__(client, OneShotClient)
        await client.on_ready()
        mock_gen.assert_called_once()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.check_mod_and_announce", new_callable=AsyncMock)
    @patch("weekly_slides_bot.BOT_MODE", "test_announce")
    async def test_test_announce_dispatches_check_mod_and_announce(self, mock_announce):
        from weekly_slides_bot import OneShotClient

        client = MagicMock(spec=OneShotClient)
        client.close = AsyncMock()
        client.on_ready = OneShotClient.on_ready.__get__(client, OneShotClient)
        await client.on_ready()
        mock_announce.assert_called_once()


# ---------------------------------------------------------------------------
# test_slides routing tests
# ---------------------------------------------------------------------------


class TestTestSlidesRouting:
    """Tests that test_slides mode routes all output to the test channel."""

    @staticmethod
    def _make_client(marker_msg, sub_msg, test_channel_id=4):
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

        mock_test_channel = MagicMock()
        mock_test_channel.send = AsyncMock()

        def get_channel(cid):
            if cid == 1:
                return mock_channel
            if cid == 2:
                return mock_results_channel
            if cid == 3:
                return mock_mod_channel
            if test_channel_id is not None and cid == test_channel_id:
                return mock_test_channel
            return None

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = get_channel
        return mock_client, mock_results_channel, mock_mod_channel, mock_test_channel

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.BOT_MODE", "test_slides")
    @patch("weekly_slides_bot.DISCORD_TEST_CHANNEL_ID", 4)
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck", return_value=[])
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_results_posted_to_test_channel(
        self, _load, _gcs, _copy, _share, _build, _save
    ):
        """In test_slides mode the results message goes to the test channel."""
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

        mock_client, mock_results, mock_mod, mock_test = self._make_client(
            marker_msg, sub_msg
        )
        await generate_slides(mock_client)

        # Results go to the test channel, not results or mod
        mock_test.send.assert_called_once()
        mock_results.send.assert_not_called()
        mock_mod.send.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.BOT_MODE", "test_slides")
    @patch("weekly_slides_bot.DISCORD_TEST_CHANNEL_ID", 4)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck", return_value=[])
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_state_not_saved_in_test_slides(
        self, _load, _gcs, _copy, _share, _build, mock_save
    ):
        """In test_slides mode state is NOT persisted."""
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

        mock_client, _, _, _ = self._make_client(marker_msg, sub_msg)
        await generate_slides(mock_client)

        mock_save.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.BOT_MODE", "test_slides")
    @patch("weekly_slides_bot.DISCORD_TEST_CHANNEL_ID", 4)
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck", return_value=[
        {"author": "User", "slide_id": "s1", "slide_number": 2, "message_id": "200", "issue": "test error"},
    ])
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_errors_posted_to_test_channel(
        self, _load, _gcs, _copy, _share, _build, _save
    ):
        """In test_slides mode error notifications go to the test channel."""
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

        mock_client, mock_results, mock_mod, mock_test = self._make_client(
            marker_msg, sub_msg
        )
        await generate_slides(mock_client)

        # Results message + error notification both go to the test channel
        assert mock_test.send.call_count == 2
        mock_mod.send.assert_not_called()
        mock_results.send.assert_not_called()


# ---------------------------------------------------------------------------
# test_announce routing tests
# ---------------------------------------------------------------------------


class TestTestAnnounceRouting:
    """Tests that test_announce mode routes output to the test channel."""

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.BOT_MODE", "test_announce")
    @patch("weekly_slides_bot.DISCORD_TEST_CHANNEL_ID", 4)
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_announcement_posted_to_test_channel(self, _load, mock_save):
        """In test_announce mode the GUESS CHAT message goes to the test channel."""
        from weekly_slides_bot import check_mod_and_announce

        mock_submissions = MagicMock()
        mock_submissions.topic = "Current Guess Chat: Fave Movie"
        mock_submissions.guild = MagicMock()
        mock_submissions.guild.id = 12345
        mock_submissions.guild.roles = []
        mock_submissions.send = AsyncMock()

        mock_test_channel = MagicMock()
        mock_test_channel.send = AsyncMock()

        mock_mod_channel = MagicMock()
        mock_mod_channel.send = AsyncMock()

        def get_channel(cid):
            if cid == 1:
                return mock_submissions
            if cid == 3:
                return mock_mod_channel
            if cid == 4:
                return mock_test_channel
            return None

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = get_channel

        await check_mod_and_announce(mock_client)

        # Announcement goes to test channel, not submissions channel
        mock_test_channel.send.assert_called()
        mock_submissions.send.assert_not_called()
        # State is not saved in test mode
        mock_save.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.BOT_MODE", "test_announce")
    @patch("weekly_slides_bot.DISCORD_TEST_CHANNEL_ID", 4)
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_confirmation_url_references_test_channel(self, _load, mock_save):
        """In test_announce mode the confirmation URL points to the test channel."""
        from weekly_slides_bot import check_mod_and_announce

        mock_submissions = MagicMock()
        mock_submissions.topic = "Current Guess Chat: Fave Movie"
        mock_submissions.guild = MagicMock()
        mock_submissions.guild.id = 12345
        mock_submissions.guild.roles = []
        mock_submissions.send = AsyncMock()

        mock_test_channel = MagicMock()
        mock_test_channel.send = AsyncMock()
        # The announcement send returns a message with an id
        mock_test_channel.send.return_value = MagicMock(id=9001)

        mock_mod_channel = MagicMock()
        mock_mod_channel.send = AsyncMock()

        def get_channel(cid):
            if cid == 1:
                return mock_submissions
            if cid == 3:
                return mock_mod_channel
            if cid == 4:
                return mock_test_channel
            return None

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = get_channel

        await check_mod_and_announce(mock_client)

        # The confirmation message (second send) should contain a URL
        # referencing the test channel (4), not the submissions channel (1).
        calls = mock_test_channel.send.call_args_list
        assert len(calls) >= 2  # announcement + confirmation
        confirm_text = calls[1].args[0]
        assert "/4/" in confirm_text, f"Expected test channel ID in URL, got: {confirm_text}"
        assert "/1/" not in confirm_text, f"Submissions channel ID should not appear in URL: {confirm_text}"

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.BOT_MODE", "test_announce")
    @patch("weekly_slides_bot.DISCORD_TEST_CHANNEL_ID", 4)
    @patch("weekly_slides_bot.DISCORD_MOD_CHANNEL_ID", 3)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={"last_announced_topic": "Fave Movie"})
    async def test_reminder_posted_to_test_channel(self, _load, mock_save):
        """In test_announce mode the reminder goes to the test channel when topic is unchanged."""
        from weekly_slides_bot import check_mod_and_announce

        mock_submissions = MagicMock()
        mock_submissions.topic = "Current Guess Chat: Fave Movie"
        mock_submissions.guild = MagicMock()
        mock_submissions.guild.id = 12345
        mock_submissions.guild.roles = []

        mock_test_channel = MagicMock()
        mock_test_channel.send = AsyncMock()

        mock_mod_channel = MagicMock()
        mock_mod_channel.send = AsyncMock()

        def get_channel(cid):
            if cid == 1:
                return mock_submissions
            if cid == 3:
                return mock_mod_channel
            if cid == 4:
                return mock_test_channel
            return None

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = get_channel

        await check_mod_and_announce(mock_client)

        # Reminder goes to test channel, not mod channel
        mock_test_channel.send.assert_called_once()
        mock_mod_channel.send.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.BOT_MODE", "test_announce")
    @patch("weekly_slides_bot.DISCORD_TEST_CHANNEL_ID", 4)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_state_not_saved_in_test_announce(self, _load, mock_save):
        """In test_announce mode state is NOT persisted."""
        from weekly_slides_bot import check_mod_and_announce

        mock_submissions = MagicMock()
        mock_submissions.topic = "Current Guess Chat: New Topic"
        mock_submissions.guild = MagicMock()
        mock_submissions.guild.id = 12345
        mock_submissions.guild.roles = []

        mock_test_channel = MagicMock()
        mock_test_channel.send = AsyncMock()

        def get_channel(cid):
            if cid == 1:
                return mock_submissions
            if cid == 4:
                return mock_test_channel
            return None

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = get_channel

        await check_mod_and_announce(mock_client)

        mock_save.assert_not_called()
