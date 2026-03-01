"""Tests for markdown-formatted GUESS CHAT and SUBMISSION detection."""

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


def _make_client(marker_msg, *sub_msgs):
    """Build a minimal mock Discord client."""
    call_count = 0

    async def history_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield marker_msg
        else:
            for s in sub_msgs:
                yield s

    mock_channel = MagicMock()
    mock_channel.history = history_side_effect

    mock_results_channel = MagicMock()
    mock_results_channel.send = AsyncMock()

    mock_client = MagicMock()
    mock_client.get_channel.side_effect = lambda cid: (
        mock_channel if cid == 1 else mock_results_channel
    )
    return mock_client


def _make_sub_msg(msg_id, content):
    """Create a mock submission message."""
    sub_msg = MagicMock()
    sub_msg.id = msg_id
    sub_msg.content = content
    sub_msg.attachments = []
    sub_msg.author = MagicMock()
    sub_msg.author.id = 999
    sub_msg.author.display_name = "User"
    sub_msg.guild = MagicMock()
    sub_msg.guild.get_member.return_value = None
    sub_msg.guild.fetch_member = AsyncMock(return_value=MagicMock(display_name="User"))
    return sub_msg


class TestMarkdownMarkerDetection:
    """GUESS CHAT markers with markdown heading formatting must be detected."""

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_heading_marker_with_topic_on_next_line(
        self, _load, _gcs, _copy, _share, mock_build, _save
    ):
        """'# GUESS CHAT\\n# LEAST FAVE POKEMON' must detect topic from second line."""
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "# GUESS CHAT\n# LEAST FAVE POKEMON\n- @everyone"

        sub_msg = _make_sub_msg(200, "SUBMISSION My answer")
        mock_client = _make_client(marker_msg, sub_msg)

        await generate_slides(mock_client)

        assert mock_build.called
        first_call = mock_build.call_args_list[0]
        submissions = first_call.kwargs.get("submissions") or first_call.args[4]
        assert len(submissions) == 1

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_heading_marker_topic_extraction(
        self, _load, _gcs, _copy, _share, mock_build, _save
    ):
        """Topic should be extracted from the second line when first line is just '# GUESS CHAT'."""
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "# GUESS CHAT\n# LEAST FAVE POKEMON\n- @everyone"

        sub_msg = _make_sub_msg(200, "SUBMISSION My answer")
        mock_client = _make_client(marker_msg, sub_msg)

        await generate_slides(mock_client)

        # Check topic is used in the copy_presentation call
        from weekly_slides_bot import copy_presentation
        first_copy_call = copy_presentation.call_args_list[0]
        deck_title = first_copy_call.args[1] if len(first_copy_call.args) > 1 else first_copy_call.kwargs.get("title")
        assert "LEAST FAVE POKEMON" in deck_title

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_heading_marker_with_topic_on_same_line(
        self, _load, _gcs, _copy, _share, mock_build, _save
    ):
        """'# GUESS CHAT DnD Characters' must extract topic from same line."""
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "# GUESS CHAT DnD Characters"

        sub_msg = _make_sub_msg(200, "SUBMISSION My answer")
        mock_client = _make_client(marker_msg, sub_msg)

        await generate_slides(mock_client)

        from weekly_slides_bot import copy_presentation
        first_copy_call = copy_presentation.call_args_list[0]
        deck_title = first_copy_call.args[1] if len(first_copy_call.args) > 1 else first_copy_call.kwargs.get("title")
        assert "DnD Characters" in deck_title

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_plain_marker_still_works(
        self, _load, _gcs, _copy, _share, mock_build, _save
    ):
        """Plain 'GUESS CHAT Topic' (no markdown) must still work."""
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test Topic"

        sub_msg = _make_sub_msg(200, "SUBMISSION My answer")
        mock_client = _make_client(marker_msg, sub_msg)

        await generate_slides(mock_client)

        assert mock_build.called
        from weekly_slides_bot import copy_presentation
        first_copy_call = copy_presentation.call_args_list[0]
        deck_title = first_copy_call.args[1] if len(first_copy_call.args) > 1 else first_copy_call.kwargs.get("title")
        assert "Test Topic" in deck_title


class TestMarkdownSubmissionDetection:
    """Submissions with markdown formatting must be detected."""

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_bold_submission_prefix(
        self, _load, _gcs, _copy, _share, mock_build, _save
    ):
        """'**SUBMISSION** My answer' must be detected as a submission."""
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test Topic"

        sub_msg = _make_sub_msg(200, "**SUBMISSION** My answer")
        mock_client = _make_client(marker_msg, sub_msg)

        await generate_slides(mock_client)

        assert mock_build.called
        first_call = mock_build.call_args_list[0]
        submissions = first_call.kwargs.get("submissions") or first_call.args[4]
        assert len(submissions) == 1
        assert submissions[0]["body"] == "My answer"

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_mixed_case_submission(
        self, _load, _gcs, _copy, _share, mock_build, _save
    ):
        """'Submission My answer' (title case) must be detected."""
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test Topic"

        sub_msg = _make_sub_msg(200, "Submission My answer")
        mock_client = _make_client(marker_msg, sub_msg)

        await generate_slides(mock_client)

        assert mock_build.called
        first_call = mock_build.call_args_list[0]
        submissions = first_call.kwargs.get("submissions") or first_call.args[4]
        assert len(submissions) == 1
        assert submissions[0]["body"] == "My answer"

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_plain_submission_still_works(
        self, _load, _gcs, _copy, _share, mock_build, _save
    ):
        """Plain 'SUBMISSION My answer' must still work."""
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test Topic"

        sub_msg = _make_sub_msg(200, "SUBMISSION My answer")
        mock_client = _make_client(marker_msg, sub_msg)

        await generate_slides(mock_client)

        assert mock_build.called
        first_call = mock_build.call_args_list[0]
        submissions = first_call.kwargs.get("submissions") or first_call.args[4]
        assert len(submissions) == 1
        assert submissions[0]["body"] == "My answer"


class TestFullMarkdownScenario:
    """End-to-end test with the exact format from the issue."""

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_issue_example_format(
        self, _load, _gcs, _copy, _share, mock_build, _save
    ):
        """Exact format from the issue must work end-to-end."""
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = (
            "# GUESS CHAT\n"
            "# LEAST FAVE POKEMON\n"
            "- @everyone\n"
            "- 3 least fave mons\n"
            "- pics and why you don't like them\n"
            "- tag with **SUBMISSION**\n"
            "- deadline:"
        )

        sub_msg = _make_sub_msg(200, "**SUBMISSION** Magikarp because it's useless")
        mock_client = _make_client(marker_msg, sub_msg)

        await generate_slides(mock_client)

        assert mock_build.called
        from weekly_slides_bot import copy_presentation
        first_copy_call = copy_presentation.call_args_list[0]
        deck_title = first_copy_call.args[1] if len(first_copy_call.args) > 1 else first_copy_call.kwargs.get("title")
        assert "LEAST FAVE POKEMON" in deck_title

        first_call = mock_build.call_args_list[0]
        submissions = first_call.kwargs.get("submissions") or first_call.args[4]
        assert len(submissions) == 1
        assert submissions[0]["body"] == "Magikarp because it's useless"
