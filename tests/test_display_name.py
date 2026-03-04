"""Tests for server-level display name resolution in generate_slides."""

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
from weekly_slides_bot import generate_slides, main


class TestIntentsConfiguration:
    """Ensure the bot requests only the required non-privileged intents."""

    @patch("weekly_slides_bot.OneShotClient")
    def test_message_content_intent_enabled(self, mock_client_cls):
        """main() must enable intents.message_content to read message text."""
        mock_instance = MagicMock()
        mock_client_cls.return_value = mock_instance

        main()

        # The first positional-or-keyword arg is 'intents='
        call_kwargs = mock_client_cls.call_args
        intents = call_kwargs.kwargs.get("intents") or call_kwargs.args[0]
        assert intents.message_content is True, "intents.message_content must be True"
        assert intents.members is False, "intents.members must not be requested (privileged intent not enabled in portal)"


class TestAuthorDisplayName:
    """Ensure the submission loop uses msg.author.display_name directly."""

    def _make_client(self, marker_msg, sub_msg):
        """Build a minimal mock Discord client with two-pass channel.history()."""
        call_count = 0

        async def history_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: scan for the GUESS CHAT marker
                yield marker_msg
            else:
                # Second call: collect SUBMISSION messages after the marker
                yield sub_msg

        mock_channel = MagicMock()
        mock_channel.history = history_side_effect

        mock_results_channel = MagicMock()
        mock_results_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.user = MagicMock(id=marker_msg.author.id)
        mock_client.get_channel.side_effect = lambda cid: (
            mock_channel if cid == 1 else mock_results_channel
        )
        return mock_client

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_generate_slides_uses_display_name(
        self, _load, _gcs, _copy, _share, mock_build, _save
    ):
        """generate_slides() must use the server-level Member display name.

        channel.history() (REST API) does not reliably include partial member
        data, so msg.author may be a User without a server nickname.  The bot
        must explicitly resolve the guild Member via fetch_member() and use
        Member.display_name so that server-specific nicknames are honoured.
        """
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test Topic"

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION My answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "AccountDisplayName"  # account-level fallback
        sub_msg.author.name = "account_username"  # different to confirm member lookup is used

        # Server member has a different (server-specific) display name
        mock_member = MagicMock()
        mock_member.display_name = "ServerNickname"

        # Simulate empty member cache so fetch_member() is called
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = None
        sub_msg.guild.fetch_member = AsyncMock(return_value=mock_member)

        mock_client = self._make_client(marker_msg, sub_msg)

        await generate_slides(mock_client)

        assert mock_build.called, "build_deck should have been called"
        # Retrieve submissions by keyword name to avoid positional fragility
        first_call = mock_build.call_args_list[0]
        submissions = first_call.kwargs.get("submissions") or first_call.args[4]
        assert len(submissions) == 1
        assert submissions[0]["author"] == "ServerNickname", (
            "author should be the server-level display name from Member.display_name"
        )
        assert submissions[0]["author"] != "AccountDisplayName", (
            "author must not fall back to the account-level display name"
        )
        assert submissions[0]["author"] != "account_username", (
            "author must not fall back to the account-level username"
        )

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_generate_slides_falls_back_when_fetch_member_fails(
        self, _load, _gcs, _copy, _share, mock_build, _save
    ):
        """generate_slides() must fall back to msg.author.display_name when fetch_member() raises."""
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test Topic"

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION My answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "AccountDisplayName"
        sub_msg.author.name = "account_username"

        # Simulate both cache miss and fetch failure
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = None
        sub_msg.guild.fetch_member = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "not found"))

        mock_client = self._make_client(marker_msg, sub_msg)

        await generate_slides(mock_client)

        assert mock_build.called, "build_deck should have been called"
        first_call = mock_build.call_args_list[0]
        submissions = first_call.kwargs.get("submissions") or first_call.args[4]
        assert len(submissions) == 1
        assert submissions[0]["author"] == "AccountDisplayName", (
            "author should fall back to msg.author.display_name when fetch_member() fails"
        )
