"""Tests that blocking Google API calls are offloaded to a thread."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# Provide required env vars before importing the module
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

from weekly_slides_bot import generate_slides


class TestBlockingCallsOffloaded:
    """Ensure blocking Google/IO calls run via asyncio.to_thread."""

    def _make_client(self, marker_msg, sub_msg):
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

        mock_results_channel = MagicMock()
        mock_results_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.get_channel.side_effect = lambda cid: (
            mock_channel if cid == 1 else mock_results_channel
        )
        return mock_client

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.asyncio.to_thread", new_callable=AsyncMock)
    @patch("weekly_slides_bot.asyncio.sleep", new_callable=AsyncMock)
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_google_calls_use_to_thread(self, _load, _sleep, mock_to_thread):
        """generate_slides() must use asyncio.to_thread for blocking calls."""
        mock_slides_svc = MagicMock()
        mock_drive_svc = MagicMock()

        # to_thread returns appropriate values for each call
        mock_to_thread.side_effect = [
            (mock_slides_svc, mock_drive_svc),  # get_google_services
            "named_id",   # copy_presentation (named)
            "anon_id",    # copy_presentation (anon)
            None,         # share_presentation (named)
            None,         # share_presentation (anon)
            [],           # build_deck (named)
            [],           # build_deck (anon)
            None,         # save_state
        ]

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
        sub_msg.guild.get_member.return_value = MagicMock(display_name="User")

        mock_client = self._make_client(marker_msg, sub_msg)
        await generate_slides(mock_client)

        # Verify to_thread was called for each blocking operation
        assert mock_to_thread.await_count == 8
        called_funcs = [c.args[0].__name__ for c in mock_to_thread.call_args_list]
        assert called_funcs == [
            "get_google_services",
            "copy_presentation",
            "copy_presentation",
            "share_presentation",
            "share_presentation",
            "build_deck",
            "build_deck",
            "save_state",
        ]

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.asyncio.to_thread", new_callable=AsyncMock)
    @patch("weekly_slides_bot.asyncio.sleep", new_callable=AsyncMock)
    @patch("weekly_slides_bot.load_state", return_value={
        "marker_id": "100",
        "named_pres_id": "existing_named",
        "anon_pres_id": "existing_anon",
        "processed_ids": [],
    })
    async def test_append_path_uses_to_thread(self, _load, _sleep, mock_to_thread):
        """Existing-round append path must also use asyncio.to_thread."""
        mock_slides_svc = MagicMock()
        mock_drive_svc = MagicMock()

        mock_to_thread.side_effect = [
            (mock_slides_svc, mock_drive_svc),  # get_google_services
            [],    # append_slides (named)
            [],    # append_slides (anon)
            None,  # save_state
        ]

        marker_msg = MagicMock()
        marker_msg.id = 100  # same marker_id as state
        marker_msg.content = "GUESS CHAT Test Topic"

        sub_msg = MagicMock()
        sub_msg.id = 300
        sub_msg.content = "SUBMISSION New answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "User"
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = MagicMock(display_name="User")

        mock_client = self._make_client(marker_msg, sub_msg)
        await generate_slides(mock_client)

        assert mock_to_thread.await_count == 4
        called_funcs = [c.args[0].__name__ for c in mock_to_thread.call_args_list]
        assert called_funcs == [
            "get_google_services",
            "append_slides",
            "append_slides",
            "save_state",
        ]
