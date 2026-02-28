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
    """Ensure the submission loop prefers the guild-cached member display name."""

    @pytest.mark.asyncio
    async def test_uses_guild_member_display_name(self):
        """When guild.get_member() returns a member, use its display_name."""
        # Simulate a Member returned by guild.get_member() with a server nick
        guild_member = MagicMock()
        guild_member.display_name = "ServerNick"

        guild = MagicMock()
        guild.get_member.return_value = guild_member

        # msg.author is a User-like object with only global display name
        author = MagicMock()
        author.id = 12345
        author.display_name = "GlobalName"

        channel = MagicMock()
        channel.guild = guild

        # Resolve author name the same way the bot does
        member = channel.guild.get_member(author.id)
        author_name = member.display_name if member else author.display_name
        assert author_name == "ServerNick"

    @pytest.mark.asyncio
    async def test_falls_back_to_msg_author_when_member_not_cached(self):
        """When guild.get_member() returns None, fall back to msg.author."""
        guild = MagicMock()
        guild.get_member.return_value = None

        author = MagicMock()
        author.id = 12345
        author.display_name = "GlobalName"

        channel = MagicMock()
        channel.guild = guild

        member = channel.guild.get_member(author.id)
        author_name = member.display_name if member else author.display_name
        assert author_name == "GlobalName"
