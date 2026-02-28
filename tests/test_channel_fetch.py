"""Tests for fetch_channel fallback when get_channel returns None."""

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
from weekly_slides_bot import generate_slides


class TestChannelFetchFallback:
    """Ensure generate_slides falls back to fetch_channel when get_channel returns None."""

    def _make_marker_and_sub(self):
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test Topic"

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION My answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "User"
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = None
        sub_msg.guild.fetch_member = AsyncMock(return_value=MagicMock(display_name="User"))
        return marker_msg, sub_msg

    def _make_channel(self, marker_msg, sub_msg):
        """Build a mock channel whose history() yields marker then submission."""
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
        return mock_channel

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_fetch_channel_called_when_get_channel_returns_none(
        self, _load, _gcs, _copy, _share, mock_build, _save
    ):
        """When get_channel returns None, fetch_channel should be used as fallback."""
        marker_msg, sub_msg = self._make_marker_and_sub()
        mock_channel = self._make_channel(marker_msg, sub_msg)
        mock_results_channel = MagicMock()
        mock_results_channel.send = AsyncMock()

        mock_client = MagicMock()
        # get_channel returns None for both channels (cache miss)
        mock_client.get_channel.return_value = None
        # fetch_channel returns the proper channels
        mock_client.fetch_channel = AsyncMock(
            side_effect=lambda cid: mock_channel if cid == 1 else mock_results_channel
        )

        await generate_slides(mock_client)

        # fetch_channel should have been called for the submissions channel
        mock_client.fetch_channel.assert_any_call(1)
        # build_deck should have been called (proving the channel was found)
        assert mock_build.called, "build_deck should have been called after fetch_channel fallback"

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_returns_early_when_both_get_and_fetch_fail(
        self, _load, _gcs, _copy, _share, mock_build, _save
    ):
        """When both get_channel and fetch_channel fail, generate_slides should return early."""
        mock_client = MagicMock()
        mock_client.get_channel.return_value = None
        mock_client.fetch_channel = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(), "not found")
        )

        await generate_slides(mock_client)

        assert not mock_build.called, "build_deck should not be called when channel cannot be found"

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_fetch_channel_not_called_when_get_channel_succeeds(
        self, _load, _gcs, _copy, _share, mock_build, _save
    ):
        """When get_channel succeeds, fetch_channel should not be called."""
        marker_msg, sub_msg = self._make_marker_and_sub()
        mock_channel = self._make_channel(marker_msg, sub_msg)
        mock_results_channel = MagicMock()
        mock_results_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = lambda cid: (
            mock_channel if cid == 1 else mock_results_channel
        )
        mock_client.fetch_channel = AsyncMock()

        await generate_slides(mock_client)

        mock_client.fetch_channel.assert_not_called()
        assert mock_build.called

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_results_channel_uses_fetch_fallback(
        self, _load, _gcs, _copy, _share, mock_build, _save
    ):
        """Results channel should also fall back to fetch_channel when get_channel returns None."""
        marker_msg, sub_msg = self._make_marker_and_sub()
        mock_channel = self._make_channel(marker_msg, sub_msg)
        mock_results_channel = MagicMock()
        mock_results_channel.send = AsyncMock()

        mock_client = MagicMock()
        # get_channel returns the submissions channel but None for results
        mock_client.get_channel.side_effect = lambda cid: (
            mock_channel if cid == 1 else None
        )
        mock_client.fetch_channel = AsyncMock(return_value=mock_results_channel)

        await generate_slides(mock_client)

        # fetch_channel should have been called for the results channel
        mock_client.fetch_channel.assert_called_with(2)
        # Results message should have been posted
        mock_results_channel.send.assert_called_once()
