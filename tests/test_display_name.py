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
        """generate_slides() must record author names from msg.author.display_name."""
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test Topic"

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION My answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.display_name = "ServerNickname"
        sub_msg.author.name = "account_username"  # different to confirm display_name is used

        mock_client = self._make_client(marker_msg, sub_msg)

        await generate_slides(mock_client)

        assert mock_build.called, "build_deck should have been called"
        # Retrieve submissions by keyword name to avoid positional fragility
        first_call = mock_build.call_args_list[0]
        submissions = first_call.kwargs.get("submissions") or first_call.args[4]
        assert len(submissions) == 1
        assert submissions[0]["author"] == "ServerNickname", (
            "author should be the server nickname from display_name"
        )
        assert submissions[0]["author"] != "account_username", (
            "author must not fall back to the account-level username"
        )
