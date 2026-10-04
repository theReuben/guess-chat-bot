"""Tests for slide layout: page size, text fitting, media packing and the
requests that apply a layout, plus image-only submission handling."""

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
    _AUTHOR_BAR_PT,
    _GAP_PT,
    _MARGIN_PT,
    _MAX_FONT_PT,
    _MIN_FONT_PT,
    _PT,
    Box,
    _arrange_media,
    _body_resize_requests,
    _fit_text,
    _image_requests,
    _line_count,
    _page_size_pt,
    _text_fit_requests,
    _video_requests,
    generate_slides,
    plan_slide_layout,
)

# The current template's page: 960×540pt, content starting below the author label.
_W, _H, _TOP = 960, 540, 94
_AREA = Box(_MARGIN_PT, _TOP, _W - 2 * _MARGIN_PT, _H - _TOP - _MARGIN_PT)


def _inside(box: Box, area: Box, tol: float = 0.01) -> bool:
    return (
        box.x >= area.x - tol and box.y >= area.y - tol
        and box.x + box.w <= area.x + area.w + tol
        and box.y + box.h <= area.y + area.h + tol
    )


def _overlap(a: Box, b: Box) -> bool:
    return not (
        a.x + a.w <= b.x + 0.01 or b.x + b.w <= a.x + 0.01
        or a.y + a.h <= b.y + 0.01 or b.y + b.h <= a.y + 0.01
    )


# ---------------------------------------------------------------------------
# Page size
# ---------------------------------------------------------------------------


class TestPageSize:
    def test_reads_the_decks_page_size(self):
        pres = {"pageSize": {"width": {"magnitude": 12192000}, "height": {"magnitude": 6858000}}}
        assert _page_size_pt(pres) == (960, 540)

    def test_falls_back_to_the_standard_slide(self):
        assert _page_size_pt({}) == (720, 405)


# ---------------------------------------------------------------------------
# Text fitting
# ---------------------------------------------------------------------------


class TestLineCount:
    def test_each_paragraph_is_at_least_one_line(self):
        assert _line_count("a\nb\n\nc", 100) == 4

    def test_words_wrap_whole(self):
        # "aaaa" is about 2em; four of them with spaces need two 5em lines.
        assert _line_count("aaaa aaaa aaaa aaaa", 5) == 2

    def test_a_word_longer_than_a_line_breaks(self):
        assert _line_count("a" * 40, 5) >= 4


class TestFitText:
    def test_short_text_gets_the_maximum_size(self):
        assert _fit_text("Pizza", 900, 400) == (_MAX_FONT_PT, False)

    def test_empty_text_needs_no_room(self):
        assert _fit_text("", 10, 10) == (_MAX_FONT_PT, False)

    def test_longer_text_gets_a_smaller_font(self):
        short, _ = _fit_text("Maine Coon", 400, 400)
        long, _ = _fit_text("Maine Coon " * 30, 400, 400)
        assert long < short

    def test_never_below_the_minimum(self):
        assert _fit_text("word " * 2000, 200, 100)[0] == _MIN_FONT_PT

    def test_fitted_text_fits(self):
        text = "Penguin - Mario Kart World\nYellow Deck - Balatro\nShiki - TWEWY"
        font, _ = _fit_text(text, 300, 200)
        lines = _line_count(text, (300 - 2 * 7.2) / font)
        assert lines * font * 1.2 <= 200 - 2 * 7.2

    def test_prefers_whole_lines_over_slightly_larger_wrapped_text(self):
        text = "Penguin - Mario Kart World\nYellow Deck - Balatro"
        font, wraps = _fit_text(text, 600, 400)
        assert not wraps
        assert _line_count(text, (600 - 2 * 7.2) / font) == 2

    def test_wraps_when_whole_lines_would_be_tiny(self):
        text = "A really quite long single line answer that goes on and on and on"
        _, wraps = _fit_text(text, 250, 400)
        assert wraps


# ---------------------------------------------------------------------------
# Media packing
# ---------------------------------------------------------------------------


class TestArrangeMedia:
    def test_no_media(self):
        assert _arrange_media([], _AREA) == ([], 0.0)

    @pytest.mark.parametrize("aspects", [
        [1.0], [16 / 9], [0.5], [1.5, 0.7], [1.0, 0.7, 0.45], [0.9, 1.5, 1.45, 1.37], [3.0, 3.0, 0.3],
    ])
    def test_boxes_stay_inside_and_do_not_overlap(self, aspects):
        boxes, covered = _arrange_media(aspects, _AREA)
        assert len(boxes) == len(aspects)
        assert all(_inside(b, _AREA) for b in boxes)
        assert not any(_overlap(a, b) for i, a in enumerate(boxes) for b in boxes[i + 1:])
        assert covered == pytest.approx(sum(b.w * b.h for b in boxes))

    @pytest.mark.parametrize("aspects", [[1.0], [0.45, 1.5, 2.0], [0.7, 0.7, 0.7, 0.7]])
    def test_items_keep_their_shape(self, aspects):
        boxes, _ = _arrange_media(aspects, _AREA)
        for aspect, box in zip(aspects, boxes):
            assert box.w / box.h == pytest.approx(aspect)

    def test_a_single_image_touches_two_opposite_edges(self):
        (box,), _ = _arrange_media([1.0], _AREA)
        assert box.h == pytest.approx(_AREA.h)  # square in a wide area: full height
        assert box.x + box.w / 2 == pytest.approx(_AREA.x + _AREA.w / 2)  # centred

    def test_tall_images_go_side_by_side(self):
        boxes, _ = _arrange_media([0.5, 0.5], _AREA)
        assert boxes[0].y == pytest.approx(boxes[1].y)
        assert boxes[1].x >= boxes[0].x + boxes[0].w + _GAP_PT - 0.01

    def test_wide_images_stack_in_a_narrow_area(self):
        narrow = Box(0, 0, 300, 600)
        boxes, _ = _arrange_media([2.0, 2.0], narrow)
        assert boxes[0].x == pytest.approx(boxes[1].x)
        assert boxes[1].y > boxes[0].y

    def test_similar_images_get_similar_sizes(self):
        boxes, _ = _arrange_media([1.4, 0.7, 0.67], _AREA)
        heights = [b.h for b in boxes]
        assert max(heights) / min(heights) < 1.5


# ---------------------------------------------------------------------------
# Whole-slide planning
# ---------------------------------------------------------------------------


class TestPlanSlideLayout:
    def test_text_only_uses_the_whole_content_area(self):
        layout = plan_slide_layout(_W, _H, _TOP, "Maine Coon", [])
        assert layout.text_box == _AREA
        assert layout.media_boxes == []
        assert layout.font_pt == _MAX_FONT_PT

    def test_media_only_uses_the_whole_content_area(self):
        layout = plan_slide_layout(_W, _H, _TOP, "", [1.0, 1.0])
        assert len(layout.media_boxes) == 2
        assert all(_inside(b, _AREA) for b in layout.media_boxes)

    def test_uses_the_real_page_size(self):
        small = plan_slide_layout(720, 405, _TOP, "", [16 / 9])
        large = plan_slide_layout(960, 540, _TOP, "", [16 / 9])
        assert large.media_boxes[0].w > small.media_boxes[0].w
        assert large.media_boxes[0].x + large.media_boxes[0].w > 720

    @pytest.mark.parametrize("text, aspects", [
        ("Pizza - food", [1.45]),
        ("Penguin - Mario Kart World\nYellow Deck - Balatro\nShiki - TWEWY", [1.0, 0.7, 0.45]),
        ("word " * 120, [1.0, 1.0]),
        ("This song - it lives in my head rent free", [16 / 9]),
    ])
    def test_text_and_media_never_overlap(self, text, aspects):
        layout = plan_slide_layout(_W, _H, _TOP, text, aspects)
        assert _inside(layout.text_box, _AREA)
        assert all(_inside(b, _AREA) for b in layout.media_boxes)
        assert not any(_overlap(layout.text_box, b) for b in layout.media_boxes)

    def test_a_short_answer_leaves_most_of_the_room_to_its_picture(self):
        layout = plan_slide_layout(_W, _H, _TOP, "Pizza - food", [1.45])
        (img,) = layout.media_boxes
        assert img.w * img.h > 0.5 * _AREA.w * _AREA.h
        assert layout.font_pt >= 28

    def test_a_list_answer_is_not_broken_mid_line(self):
        text = "Penguin - Mario Kart World\nYellow Deck - Balatro\nShiki - TWEWY"
        layout = plan_slide_layout(_W, _H, _TOP, text, [1.0, 0.7, 0.45])
        _, wraps = _fit_text(text, layout.text_box.w, layout.text_box.h)
        assert not wraps

    def test_short_text_only_is_centred(self):
        assert plan_slide_layout(_W, _H, _TOP, "Maine Coon", []).centred

    def test_a_wrapping_paragraph_stays_left_aligned(self):
        assert not plan_slide_layout(_W, _H, _TOP, "a fairly long sentence " * 15, []).centred

    def test_a_short_list_above_the_pictures_is_centred(self):
        text = "Penguin - Mario Kart World\nYellow Deck - Balatro\nShiki - TWEWY"
        layout = plan_slide_layout(_W, _H, _TOP, text, [1.0, 0.7, 0.45])
        assert layout.text_box.w == _AREA.w  # stacked above the pictures
        assert layout.centred

    def test_text_beside_the_pictures_stays_left_aligned(self):
        layout = plan_slide_layout(_W, _H, _TOP, "Pizza - food", [1.45])
        assert layout.text_box.w < _AREA.w  # in a column beside the picture
        assert not layout.centred

    def test_a_long_answer_claims_more_room(self):
        short = plan_slide_layout(_W, _H, _TOP, "Pizza", [1.0])
        long = plan_slide_layout(_W, _H, _TOP, "a fairly long sentence " * 15, [1.0])
        area = lambda b: b.w * b.h  # noqa: E731
        assert area(long.text_box) > area(short.text_box)


# ---------------------------------------------------------------------------
# Requests that apply a layout
# ---------------------------------------------------------------------------


class TestMediaRequests:
    def test_images_are_created_in_their_boxes(self):
        boxes = [Box(10, 20, 100, 50), Box(120, 20, 80, 50)]
        reqs = _image_requests("s1", ["https://a", "https://b"], boxes)
        assert [r["createImage"]["url"] for r in reqs] == ["https://a", "https://b"]
        props = reqs[1]["createImage"]["elementProperties"]
        assert props["pageObjectId"] == "s1"
        assert props["size"]["width"]["magnitude"] == 80 * _PT
        assert props["transform"]["translateX"] == 120 * _PT

    def test_only_the_first_video_is_embedded(self):
        reqs = _video_requests("s1", ["v1", "v2"], Box(0, 0, 160, 90))
        assert len(reqs) == 1
        assert reqs[0]["createVideo"]["id"] == "v1"
        assert reqs[0]["createVideo"]["source"] == "YOUTUBE"

    def test_no_video_ids(self):
        assert _video_requests("s1", [], Box(0, 0, 1, 1)) == []


class TestBodyRequests:
    _ELEMS = [
        {
            "objectId": "author_elem",
            "shape": {"text": {"textElements": [{"textRun": {"content": "#1 — Sam"}}]}},
            "size": {"width": {"magnitude": 300 * _PT}, "height": {"magnitude": 30 * _PT}},
            "transform": {"translateX": 0, "translateY": 10 * _PT},
        },
        {
            "objectId": "body_elem",
            "shape": {"text": {"textElements": [{"textRun": {"content": "answer"}}]}},
            "size": {"width": {"magnitude": 400 * _PT}, "height": {"magnitude": 200 * _PT}},
            "transform": {"translateX": 0, "translateY": (_AUTHOR_BAR_PT + 5) * _PT},
        },
    ]

    def test_body_is_moved_and_scaled_to_its_box(self):
        transform, alignment = _body_resize_requests(self._ELEMS, Box(24, 94, 800, 100))
        t = transform["updatePageElementTransform"]
        assert t["objectId"] == "body_elem"
        assert t["applyMode"] == "ABSOLUTE"
        assert t["transform"]["scaleX"] == pytest.approx(2.0)
        assert t["transform"]["scaleY"] == pytest.approx(0.5)
        assert t["transform"]["translateY"] == 94 * _PT
        assert alignment["updateShapeProperties"]["shapeProperties"]["contentAlignment"] == "MIDDLE"

    def test_no_body_element(self):
        assert _body_resize_requests([], Box(0, 0, 1, 1)) == []

    def test_font_size_is_always_set(self):
        style, _ = _text_fit_requests("body_elem", 36)
        assert style["updateTextStyle"]["style"]["fontSize"] == {"magnitude": 36, "unit": "PT"}

    def test_alignment_is_always_set(self):
        """Appended slides copy an existing slide, so left must be set explicitly too."""
        _, centred = _text_fit_requests("body_elem", 36, centred=True)
        _, left = _text_fit_requests("body_elem", 36)
        assert centred["updateParagraphStyle"]["style"]["alignment"] == "CENTER"
        assert left["updateParagraphStyle"]["style"]["alignment"] == "START"
        assert left["updateParagraphStyle"]["textRange"] == {"type": "ALL"}


class TestImageOnlySubmissionBody:
    """generate_slides must use empty body text for image-only submissions."""

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
    async def test_image_only_submission_has_empty_body(
        self, _load, _gcs, _copy, _share, mock_build, _save
    ):
        """Image-only submission must use empty string as body, not '(image submission)'."""
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Image Test"

        # A submission message with no text but with an image attachment
        img_attachment = MagicMock()
        img_attachment.url = "https://cdn.discord.com/img.png"
        img_attachment.content_type = "image/png"

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION"   # no body text
        sub_msg.attachments = [img_attachment]
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "User"
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = None
        sub_msg.guild.fetch_member = AsyncMock(return_value=MagicMock(display_name="User"))

        mock_client = self._make_client(marker_msg, sub_msg)
        await generate_slides(mock_client)

        assert mock_build.called
        first_call = mock_build.call_args_list[0]
        submissions = first_call.kwargs.get("submissions") or first_call.args[4]
        assert len(submissions) == 1
        assert submissions[0]["body"] == "", (
            "Image-only submission body should be empty string, not '(image submission)'"
        )
        assert submissions[0]["images"] == ["https://cdn.discord.com/img.png"]

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck")
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_text_and_image_submission_preserves_body(
        self, _load, _gcs, _copy, _share, mock_build, _save
    ):
        """Submission with text AND image must preserve the body text."""
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Mixed Test"

        img_attachment = MagicMock()
        img_attachment.url = "https://cdn.discord.com/img.png"
        img_attachment.content_type = "image/png"

        sub_msg = MagicMock()
        sub_msg.id = 201
        sub_msg.content = "SUBMISSION My answer"
        sub_msg.attachments = [img_attachment]
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "User"
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = None
        sub_msg.guild.fetch_member = AsyncMock(return_value=MagicMock(display_name="User"))

        mock_client = self._make_client(marker_msg, sub_msg)
        await generate_slides(mock_client)

        assert mock_build.called
        first_call = mock_build.call_args_list[0]
        submissions = first_call.kwargs.get("submissions") or first_call.args[4]
        assert submissions[0]["body"] == "My answer"
        assert submissions[0]["images"] == ["https://cdn.discord.com/img.png"]


