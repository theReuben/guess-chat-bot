"""Tests for on_ready guild diagnostics and improved error messages."""

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
from weekly_slides_bot import OneShotClient, generate_slides


class TestOnReadyGuildDiagnostics:
    """Ensure on_ready logs guild membership for troubleshooting."""

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.generate_slides", new_callable=AsyncMock)
    async def test_logs_connected_guilds(self, mock_gen, capsys):
        """on_ready should log guild names when the bot is in servers."""
        client = MagicMock()

        guild1 = MagicMock()
        guild1.name = "Test Server"
        guild2 = MagicMock()
        guild2.name = "Another Server"
        client.guilds = [guild1, guild2]
        client.close = AsyncMock()

        await OneShotClient.on_ready(client)

        output = capsys.readouterr().out
        assert "Connected to 2 guild(s)" in output
        assert "Test Server" in output
        assert "Another Server" in output

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.generate_slides", new_callable=AsyncMock)
    async def test_logs_error_when_no_guilds(self, mock_gen, capsys):
        """on_ready should log an error with invite instructions when bot is in no guilds."""
        client = MagicMock()
        client.guilds = []
        client.close = AsyncMock()

        await OneShotClient.on_ready(client)

        output = capsys.readouterr().out
        assert "Bot is not in any guilds" in output
        assert "Invite the bot" in output

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.generate_slides", new_callable=AsyncMock)
    async def test_generate_slides_still_called_with_no_guilds(self, mock_gen):
        """on_ready should still call generate_slides even with no guilds (fetch_channel may work)."""
        client = MagicMock()
        client.guilds = []
        client.close = AsyncMock()

        await OneShotClient.on_ready(client)

        mock_gen.assert_called_once_with(client)


class TestChannelNotFoundErrorMessages:
    """Ensure error messages for channel-not-found suggest checking the bot invitation."""

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_submissions_channel_error_suggests_invite(self, _load, capsys):
        """Error for missing submissions channel should mention invitation and permissions."""
        mock_client = MagicMock()
        mock_client.get_channel.return_value = None
        mock_client.fetch_channel = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(), "not found")
        )

        await generate_slides(mock_client)

        output = capsys.readouterr().out
        assert "Ensure the bot has been invited" in output
        assert "View Channels" in output

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_results_channel_error_suggests_invite(
        self, _load, _gcs, _copy, _share, _build, _save, capsys
    ):
        """Error for missing results channel should mention invitation and permissions."""
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
        sub_msg.guild.get_member.return_value = None
        sub_msg.guild.fetch_member = AsyncMock(return_value=MagicMock(display_name="User"))

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

        mock_client = MagicMock()
        # Submissions channel works, results channel doesn't
        mock_client.get_channel.side_effect = lambda cid: (
            mock_channel if cid == 1 else None
        )
        mock_client.fetch_channel = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(), "not found")
        )

        await generate_slides(mock_client)

        output = capsys.readouterr().out
        assert "Ensure the bot has been invited" in output
        assert "Send Messages" in output
