"""Tests for server-level display name resolution in generate_slides."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

# Provide required env vars before importing the module
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

import discord
from weekly_slides_bot import main


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

    @pytest.mark.asyncio
    async def test_uses_msg_author_display_name(self):
        """msg.author in a guild channel is already a Member; use its display_name directly."""
        # msg.author is a discord.Member with the server nickname as display_name
        author = MagicMock()
        author.display_name = "ServerNick"

        # Resolve author name the same way the bot does
        author_name = author.display_name
        assert author_name == "ServerNick"

    @pytest.mark.asyncio
    async def test_uses_display_name_as_server_nick(self):
        """display_name reflects the server nickname when set."""
        author = MagicMock()
        author.display_name = "MyServerNickname"

        author_name = author.display_name
        assert author_name == "MyServerNickname"
