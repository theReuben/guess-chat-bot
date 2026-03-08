"""Tests for image layout in _image_requests and image-only submission handling."""

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
    _IMG_MARGIN_PT,
    _PT,
    _SLIDE_H_PT,
    _SLIDE_W_PT,
    _TEXT_IMG_GAP_PT,
    _TEXT_SPLIT_PT,
    _body_resize_requests,
    _image_requests,
    generate_slides,
)

_SLIDE_ID = "slide1"
_URLS = [f"https://example.com/img{i}.png" for i in range(4)]


class TestImageRequestsEmpty:
    def test_no_urls_returns_empty(self):
        assert _image_requests(_SLIDE_ID, []) == []

    def test_no_urls_returns_empty_image_only(self):
        assert _image_requests(_SLIDE_ID, [], has_text=False) == []


class TestImageRequestsHasText:
    """Images must stay within the right portion of the slide."""

    def _reqs(self, count: int) -> list[dict]:
        return _image_requests(_SLIDE_ID, _URLS[:count], has_text=True)

    def test_one_image_single_request(self):
        assert len(self._reqs(1)) == 1

    def test_two_images_two_requests(self):
        assert len(self._reqs(2)) == 2

    def test_four_images_four_requests(self):
        assert len(self._reqs(4)) == 4

    def test_more_than_four_capped(self):
        assert len(_image_requests(_SLIDE_ID, _URLS + ["extra"], has_text=True)) == 4

    def test_images_start_in_right_half(self):
        """All images must start at or past the text/image split."""
        for req in self._reqs(4):
            props = req["createImage"]["elementProperties"]
            translate_x_pt = props["transform"]["translateX"] // _PT
            assert translate_x_pt >= _TEXT_SPLIT_PT, f"translateX {translate_x_pt}pt is in the text area"

    def test_images_stay_within_slide_width(self):
        """Right edge of every image must not exceed slide width."""
        for req in self._reqs(4):
            props = req["createImage"]["elementProperties"]
            left_pt = props["transform"]["translateX"] // _PT
            w_pt = props["size"]["width"]["magnitude"] // _PT
            assert left_pt + w_pt <= _SLIDE_W_PT, (
                f"Image right edge {left_pt + w_pt}pt exceeds slide width {_SLIDE_W_PT}pt"
            )

    def test_images_stay_within_slide_height(self):
        """Bottom edge of every image must not exceed slide height."""
        for req in self._reqs(4):
            props = req["createImage"]["elementProperties"]
            top_pt = props["transform"]["translateY"] // _PT
            h_pt = props["size"]["height"]["magnitude"] // _PT
            assert top_pt + h_pt <= _SLIDE_H_PT, (
                f"Image bottom edge {top_pt + h_pt}pt exceeds slide height {_SLIDE_H_PT}pt"
            )

    def test_slide_id_on_all_requests(self):
        for req in self._reqs(2):
            assert req["createImage"]["elementProperties"]["pageObjectId"] == _SLIDE_ID


class TestImageRequestsNoText:
    """Image-only submissions must use the full available slide area."""

    def _reqs(self, count: int) -> list[dict]:
        return _image_requests(_SLIDE_ID, _URLS[:count], has_text=False)

    def test_one_image_single_request(self):
        assert len(self._reqs(1)) == 1

    def test_four_images_four_requests(self):
        assert len(self._reqs(4)) == 4

    def test_images_start_near_left_margin(self):
        """First column must start close to the left margin."""
        for req in self._reqs(1):
            props = req["createImage"]["elementProperties"]
            translate_x_pt = props["transform"]["translateX"] // _PT
            assert translate_x_pt == _IMG_MARGIN_PT

    def test_images_stay_within_slide_width(self):
        for req in self._reqs(4):
            props = req["createImage"]["elementProperties"]
            left_pt = props["transform"]["translateX"] // _PT
            w_pt = props["size"]["width"]["magnitude"] // _PT
            assert left_pt + w_pt <= _SLIDE_W_PT

    def test_images_stay_within_slide_height(self):
        for req in self._reqs(4):
            props = req["createImage"]["elementProperties"]
            top_pt = props["transform"]["translateY"] // _PT
            h_pt = props["size"]["height"]["magnitude"] // _PT
            assert top_pt + h_pt <= _SLIDE_H_PT

    def test_single_image_wider_than_with_text(self):
        """A single image-only image should be wider than in text+image mode."""
        req_no_text = self._reqs(1)[0]
        req_has_text = _image_requests(_SLIDE_ID, _URLS[:1], has_text=True)[0]
        w_no_text = req_no_text["createImage"]["elementProperties"]["size"]["width"]["magnitude"]
        w_has_text = req_has_text["createImage"]["elementProperties"]["size"]["width"]["magnitude"]
        assert w_no_text > w_has_text

    def test_starts_at_author_bar_y(self):
        """Images should start below the author bar."""
        for req in self._reqs(1):
            top_pt = req["createImage"]["elementProperties"]["transform"]["translateY"] // _PT
            assert top_pt == _AUTHOR_BAR_PT


class TestNoImagesDoesNotOverlapText:
    """With images present, no image should overlap the text area (x < split)."""

    def test_images_do_not_invade_text_area_with_text(self):
        reqs = _image_requests(_SLIDE_ID, _URLS[:4], has_text=True)
        for req in reqs:
            left_pt = req["createImage"]["elementProperties"]["transform"]["translateX"] // _PT
            assert left_pt >= _TEXT_SPLIT_PT


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


class TestBodyResizeRequests:
    """_body_resize_requests must resize the body text box for all submissions."""

    # Simulate a template-style slide with an author bar and a body text box.
    # Author bar: y=0, height=55pt; body box: y=55pt, occupying the left 400pt.
    _ELEMS = [
        {
            "objectId": "author_elem",
            "shape": {"shapeType": "TEXT_BOX"},
            "size": {
                "width": {"magnitude": 720 * _PT},
                "height": {"magnitude": _AUTHOR_BAR_PT * _PT},
            },
            "transform": {
                "scaleX": 1,
                "scaleY": 1,
                "translateX": 0,
                "translateY": 0,
                "unit": "EMU",
            },
        },
        {
            "objectId": "body_elem",
            "shape": {"shapeType": "TEXT_BOX"},
            "size": {
                "width": {"magnitude": 400 * _PT},
                "height": {"magnitude": 314 * _PT},
            },
            "transform": {
                "scaleX": 1,
                "scaleY": 1,
                "translateX": 0,
                "translateY": _AUTHOR_BAR_PT * _PT,
                "unit": "EMU",
            },
        },
    ]

    def test_returns_resize_when_has_images(self):
        reqs = _body_resize_requests(self._ELEMS, has_images=True)
        assert len(reqs) == 2

    def test_returns_empty_for_no_elements(self):
        assert _body_resize_requests([], has_images=False) == []

    def test_returns_empty_when_no_elements_below_author_bar(self):
        """No qualifying elements (all shapes are above the author bar)."""
        only_author = [self._ELEMS[0]]  # only the author bar element (y=0)
        assert _body_resize_requests(only_author, has_images=False) == []

    def test_returns_single_request_for_text_only(self):
        reqs = _body_resize_requests(self._ELEMS, has_images=False)
        assert len(reqs) == 2

    def test_autofit_shrink_on_overflow_applied(self):
        """Second request must set SHRINK_TEXT_ON_OVERFLOW on the body element."""
        reqs = _body_resize_requests(self._ELEMS, has_images=False)
        autofit_req = reqs[1]["updateShapeProperties"]
        assert autofit_req["objectId"] == "body_elem"
        assert autofit_req["shapeProperties"]["autofit"]["autofitType"] == "SHRINK_TEXT_ON_OVERFLOW"
        assert autofit_req["fields"] == "autofit.autofitType"

    def test_autofit_applied_with_images_too(self):
        """SHRINK_TEXT_ON_OVERFLOW must also be set when images are present."""
        reqs = _body_resize_requests(self._ELEMS, has_images=True)
        autofit_req = reqs[1]["updateShapeProperties"]
        assert autofit_req["shapeProperties"]["autofit"]["autofitType"] == "SHRINK_TEXT_ON_OVERFLOW"

    def test_request_targets_body_element(self):
        req = _body_resize_requests(self._ELEMS, has_images=False)[0]
        assert req["updatePageElementTransform"]["objectId"] == "body_elem"

    def test_apply_mode_is_absolute(self):
        req = _body_resize_requests(self._ELEMS, has_images=False)[0]
        assert req["updatePageElementTransform"]["applyMode"] == "ABSOLUTE"

    def test_body_element_positioned_at_content_area(self):
        transform = _body_resize_requests(self._ELEMS, has_images=False)[0][
            "updatePageElementTransform"
        ]["transform"]
        assert transform["translateX"] == _IMG_MARGIN_PT * _PT
        assert transform["translateY"] == _AUTHOR_BAR_PT * _PT

    def test_body_element_expanded_to_full_content_width(self):
        transform = _body_resize_requests(self._ELEMS, has_images=False)[0][
            "updatePageElementTransform"
        ]["transform"]
        elem_w = 400 * _PT  # original body width from _ELEMS
        expected_scale_x = (_SLIDE_W_PT - 2 * _IMG_MARGIN_PT) * _PT / elem_w
        assert transform["scaleX"] == pytest.approx(expected_scale_x)

    def test_body_element_wider_than_original_template_area(self):
        """Rendered width after resize must exceed the original left-half area."""
        transform = _body_resize_requests(self._ELEMS, has_images=False)[0][
            "updatePageElementTransform"
        ]["transform"]
        elem_w = 400 * _PT
        rendered_w = elem_w * transform["scaleX"]
        original_text_area_w = 400 * _PT  # typical left-half text area
        assert rendered_w > original_text_area_w

    def test_with_images_constrains_body_to_left(self):
        """When images are present, body must be constrained to the left portion."""
        transform = _body_resize_requests(self._ELEMS, has_images=True)[0][
            "updatePageElementTransform"
        ]["transform"]
        elem_w = 400 * _PT
        rendered_w = elem_w * transform["scaleX"]
        max_text_w = (_TEXT_SPLIT_PT - _TEXT_IMG_GAP_PT - _IMG_MARGIN_PT) * _PT
        assert rendered_w == pytest.approx(max_text_w)

    def test_with_images_body_does_not_overlap_image_area(self):
        """Body right edge must not reach the image split point."""
        transform = _body_resize_requests(self._ELEMS, has_images=True)[0][
            "updatePageElementTransform"
        ]["transform"]
        elem_w = 400 * _PT
        rendered_w = elem_w * transform["scaleX"]
        left = transform["translateX"]
        right_edge_pt = (left + rendered_w) / _PT
        assert right_edge_pt <= _TEXT_SPLIT_PT - _TEXT_IMG_GAP_PT
