"""Tests for YouTube video detection, URL stripping, and video slide requests."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Provide required env vars before importing the module
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

from weekly_slides_bot import (
    _PT,
    _VIDEO_ASPECT,
    Box,
    _video_requests,
    extract_youtube_ids,
    generate_slides,
    plan_slide_layout,
    strip_youtube_urls,
)

_SLIDE_ID = "slide1"


# ---------------------------------------------------------------------------
# extract_youtube_ids
# ---------------------------------------------------------------------------


class TestExtractYoutubeIds:
    def test_standard_url(self):
        assert extract_youtube_ids("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == ["dQw4w9WgXcQ"]

    def test_short_url(self):
        assert extract_youtube_ids("https://youtu.be/dQw4w9WgXcQ") == ["dQw4w9WgXcQ"]

    def test_embed_url(self):
        assert extract_youtube_ids("https://www.youtube.com/embed/dQw4w9WgXcQ") == ["dQw4w9WgXcQ"]

    def test_no_youtube_url(self):
        assert extract_youtube_ids("Just some text without a link") == []

    def test_url_with_extra_params(self):
        ids = extract_youtube_ids("https://www.youtube.com/watch?v=abc123_DEF0&t=120")
        assert ids == ["abc123_DEF0"]

    def test_multiple_urls(self):
        text = "Check https://youtu.be/aaaAAAaaa11 and https://youtu.be/bbbBBBbbb22"
        assert extract_youtube_ids(text) == ["aaaAAAaaa11", "bbbBBBbbb22"]

    def test_url_without_www(self):
        assert extract_youtube_ids("https://youtube.com/watch?v=dQw4w9WgXcQ") == ["dQw4w9WgXcQ"]

    def test_url_without_https(self):
        assert extract_youtube_ids("http://www.youtube.com/watch?v=dQw4w9WgXcQ") == ["dQw4w9WgXcQ"]

    def test_mixed_text_and_url(self):
        text = "My answer is this song https://youtu.be/dQw4w9WgXcQ because it's great"
        assert extract_youtube_ids(text) == ["dQw4w9WgXcQ"]


# ---------------------------------------------------------------------------
# strip_youtube_urls
# ---------------------------------------------------------------------------


class TestStripYoutubeUrls:
    def test_strips_standard_url(self):
        result = strip_youtube_urls("Check this https://www.youtube.com/watch?v=dQw4w9WgXcQ ok")
        assert result == "Check this ok"

    def test_strips_short_url(self):
        result = strip_youtube_urls("See https://youtu.be/dQw4w9WgXcQ")
        assert result == "See"

    def test_no_url_unchanged(self):
        assert strip_youtube_urls("No links here") == "No links here"

    def test_only_url_returns_empty(self):
        assert strip_youtube_urls("https://youtu.be/dQw4w9WgXcQ") == ""

    def test_strips_multiple_urls(self):
        text = "A https://youtu.be/aaaAAAaaa11 B https://youtu.be/bbbBBBbbb22 C"
        assert strip_youtube_urls(text) == "A B C"


# ---------------------------------------------------------------------------
# _video_requests
# ---------------------------------------------------------------------------


class TestVideoRequests:
    _BOX = Box(100, 120, 480, 270)

    def test_no_ids_returns_empty(self):
        assert _video_requests(_SLIDE_ID, [], self._BOX) == []

    def test_only_first_video_embedded(self):
        reqs = _video_requests(_SLIDE_ID, ["id1", "id2"], self._BOX)
        assert len(reqs) == 1
        assert reqs[0]["createVideo"]["id"] == "id1"
        assert reqs[0]["createVideo"]["source"] == "YOUTUBE"

    def test_placed_in_its_box(self):
        props = _video_requests(_SLIDE_ID, ["abc123_DEF0"], self._BOX)[0]["createVideo"]["elementProperties"]
        assert props["pageObjectId"] == _SLIDE_ID
        assert props["transform"]["translateX"] == 100 * _PT
        assert props["transform"]["translateY"] == 120 * _PT
        assert props["size"]["width"]["magnitude"] == 480 * _PT
        assert props["size"]["height"]["magnitude"] == 270 * _PT


class TestVideoLayout:
    """Videos are planned as 16:9 media, beside or below any text."""

    @pytest.mark.parametrize("text", ["", "This song - it lives in my head rent free"])
    def test_video_box_is_16_by_9(self, text):
        layout = plan_slide_layout(960, 540, 94, text, [_VIDEO_ASPECT])
        (box,) = layout.media_boxes
        assert box.w / box.h == pytest.approx(16 / 9)
        assert box.x + box.w <= 960 and box.y + box.h <= 540


# ---------------------------------------------------------------------------
# Integration: generate_slides with YouTube URLs
# ---------------------------------------------------------------------------


def _make_client(marker_msg, *sub_msgs):
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
    mock_client.user = MagicMock(id=marker_msg.author.id)
    mock_client.get_channel.side_effect = lambda cid: (
        mock_channel if cid == 1 else mock_results_channel
    )
    return mock_client


def _make_sub_msg(msg_id, content, attachments=None):
    sub_msg = MagicMock()
    sub_msg.id = msg_id
    sub_msg.content = content
    sub_msg.attachments = attachments or []
    sub_msg.author = MagicMock()
    sub_msg.author.id = 999
    sub_msg.author.display_name = "User"
    sub_msg.guild = MagicMock()
    sub_msg.guild.get_member.return_value = None
    sub_msg.guild.fetch_member = AsyncMock(return_value=MagicMock(display_name="User"))
    return sub_msg


class TestYoutubeSubmissionIntegration:
    """generate_slides must detect YouTube links and pass youtube_ids to build_deck."""

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_youtube_ids_extracted(self, _load, _gcs, _copy, _share, mock_build, _save):
        """Submission with a YouTube link must populate youtube_ids."""
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Videos"

        sub_msg = _make_sub_msg(200, "SUBMISSION Check this https://youtu.be/dQw4w9WgXcQ")
        mock_client = _make_client(marker_msg, sub_msg)
        await generate_slides(mock_client)

        assert mock_build.called
        first_call = mock_build.call_args_list[0]
        submissions = first_call.kwargs.get("submissions") or first_call.args[4]
        assert len(submissions) == 1
        assert submissions[0]["youtube_ids"] == ["dQw4w9WgXcQ"]

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_youtube_url_stripped_from_body(self, _load, _gcs, _copy, _share, mock_build, _save):
        """YouTube URL must be removed from the body text."""
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Videos"

        sub_msg = _make_sub_msg(200, "SUBMISSION My song https://youtu.be/dQw4w9WgXcQ enjoy")
        mock_client = _make_client(marker_msg, sub_msg)
        await generate_slides(mock_client)

        first_call = mock_build.call_args_list[0]
        submissions = first_call.kwargs.get("submissions") or first_call.args[4]
        assert "youtu.be" not in submissions[0]["body"]
        assert "dQw4w9WgXcQ" not in submissions[0]["body"]
        assert submissions[0]["body"] == "My song enjoy"

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_no_youtube_url_empty_list(self, _load, _gcs, _copy, _share, mock_build, _save):
        """Submission without YouTube link must have empty youtube_ids."""
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test"

        sub_msg = _make_sub_msg(200, "SUBMISSION Plain answer")
        mock_client = _make_client(marker_msg, sub_msg)
        await generate_slides(mock_client)

        first_call = mock_build.call_args_list[0]
        submissions = first_call.kwargs.get("submissions") or first_call.args[4]
        assert submissions[0]["youtube_ids"] == []
