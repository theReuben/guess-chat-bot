"""Tests for rate-limit pacing in the submission collection loop."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Provide required env vars before importing the module
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

from weekly_slides_bot import generate_slides


class TestFetchMemberPacing:
    """Ensure asyncio.sleep is called after each fetch_member API call."""

    def _make_client(self, marker_msg, sub_msgs):
        """Build a minimal mock Discord client."""
        call_count = 0

        async def history_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield marker_msg
            else:
                for msg in sub_msgs:
                    yield msg

        mock_channel = MagicMock()
        mock_channel.history = history_side_effect

        mock_results_channel = MagicMock()
        mock_results_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = lambda cid: (
            mock_channel if cid == 1 else mock_results_channel
        )
        return mock_client

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.asyncio.sleep", new_callable=AsyncMock)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_sleep_called_after_fetch_member(
        self, _load, _gcs, _copy, _share, _build, _save, mock_sleep
    ):
        """asyncio.sleep must be called after each fetch_member REST call."""

        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test Topic"

        # Two submissions from different authors → two fetch_member calls
        sub_msgs = []
        for i, (uid, name) in enumerate([(10, "Alice"), (20, "Bob")]):
            msg = MagicMock()
            msg.id = 200 + i
            msg.content = f"SUBMISSION Answer {i}"
            msg.attachments = []
            msg.author = MagicMock()
            msg.author.id = uid
            msg.author.display_name = name
            msg.guild = MagicMock()
            msg.guild.get_member.return_value = None
            msg.guild.fetch_member = AsyncMock(
                return_value=MagicMock(display_name=name)
            )
            sub_msgs.append(msg)

        mock_client = self._make_client(marker_msg, sub_msgs)
        await generate_slides(mock_client)

        # asyncio.sleep should have been called once per unique author
        assert mock_sleep.await_count == 2
        mock_sleep.assert_awaited_with(0.25)

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.asyncio.sleep", new_callable=AsyncMock)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_no_sleep_for_cached_member(
        self, _load, _gcs, _copy, _share, _build, _save, mock_sleep
    ):
        """asyncio.sleep must not be called when get_member returns a hit."""

        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test Topic"

        mock_member = MagicMock(display_name="Alice")

        msg = MagicMock()
        msg.id = 200
        msg.content = "SUBMISSION Answer"
        msg.attachments = []
        msg.author = MagicMock()
        msg.author.id = 10
        msg.author.display_name = "Alice"
        msg.guild = MagicMock()
        # get_member returns a hit → no API call needed
        msg.guild.get_member.return_value = mock_member
        msg.guild.fetch_member = AsyncMock()

        mock_client = self._make_client(marker_msg, [msg])
        await generate_slides(mock_client)

        # No sleep because get_member returned a cached result
        mock_sleep.assert_not_awaited()
        msg.guild.fetch_member.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.asyncio.sleep", new_callable=AsyncMock)
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_sleep_called_once_per_unique_author(
        self, _load, _gcs, _copy, _share, _build, _save, mock_sleep
    ):
        """Two submissions from the same author should only trigger one fetch_member call."""

        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test Topic"

        guild_mock = MagicMock()
        guild_mock.get_member.return_value = None
        guild_mock.fetch_member = AsyncMock(
            return_value=MagicMock(display_name="Alice")
        )

        sub_msgs = []
        for i in range(3):
            msg = MagicMock()
            msg.id = 200 + i
            msg.content = f"SUBMISSION Answer {i}"
            msg.attachments = []
            msg.author = MagicMock()
            msg.author.id = 10  # same author for all
            msg.author.display_name = "Alice"
            msg.guild = guild_mock
            sub_msgs.append(msg)

        mock_client = self._make_client(marker_msg, sub_msgs)
        await generate_slides(mock_client)

        # Only one fetch + sleep because same author is cached after first call
        assert mock_sleep.await_count == 1
