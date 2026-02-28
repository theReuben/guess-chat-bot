"""Tests for _body_fill_requests slide-layout helper."""

from __future__ import annotations

import os

# Provide required env vars before importing the module
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

from weekly_slides_bot import (
    _MARGIN_EMU,
    _SLIDE_WIDTH_EMU,
    _body_fill_requests,
)

_BODY_SHAPE = {
    "objectId": "body_shape",
    "shape": {
        "text": {
            "textElements": [
                {"textRun": {"content": "{{BODY}}"}}
            ]
        }
    },
    "transform": {
        "scaleX": 1,
        "scaleY": 1,
        "translateX": 500000,
        "translateY": 200000,
        "unit": "EMU",
    },
    "size": {
        "width": {"magnitude": 4000000, "unit": "EMU"},
        "height": {"magnitude": 3000000, "unit": "EMU"},
    },
}

_AUTHOR_SHAPE = {
    "objectId": "author_shape",
    "shape": {
        "text": {
            "textElements": [
                {"textRun": {"content": "{{AUTHOR}}"}}
            ]
        }
    },
    "transform": {
        "scaleX": 1,
        "scaleY": 1,
        "translateX": 500000,
        "translateY": 50000,
        "unit": "EMU",
    },
    "size": {
        "width": {"magnitude": 4000000, "unit": "EMU"},
        "height": {"magnitude": 100000, "unit": "EMU"},
    },
}


def _make_slide(*elements):
    return {"objectId": "slide1", "pageElements": list(elements)}


class TestBodyFillRequestsWithImages:
    """When the submission has images only TEXT_AUTOFIT is applied."""

    def test_returns_single_autofit_request(self):
        slide = _make_slide(_AUTHOR_SHAPE, _BODY_SHAPE)
        reqs = _body_fill_requests(slide, has_images=True)

        assert len(reqs) == 1
        req = reqs[0]
        assert "updateShapeProperties" in req
        props = req["updateShapeProperties"]
        assert props["objectId"] == "body_shape"
        assert props["shapeProperties"]["autofit"]["autofitType"] == "TEXT_AUTOFIT"
        assert props["fields"] == "autofit"

    def test_targets_body_shape_not_author_shape(self):
        slide = _make_slide(_AUTHOR_SHAPE, _BODY_SHAPE)
        reqs = _body_fill_requests(slide, has_images=True)

        shape_req = reqs[0]["updateShapeProperties"]
        assert shape_req["objectId"] == "body_shape"
        assert shape_req["objectId"] != "author_shape"


class TestBodyFillRequestsWithoutImages:
    """When there are no images TEXT_AUTOFIT + full-width resize are applied."""

    def test_returns_three_requests(self):
        slide = _make_slide(_AUTHOR_SHAPE, _BODY_SHAPE)
        reqs = _body_fill_requests(slide, has_images=False)

        assert len(reqs) == 3

    def test_includes_autofit_request(self):
        slide = _make_slide(_AUTHOR_SHAPE, _BODY_SHAPE)
        reqs = _body_fill_requests(slide, has_images=False)

        autofit_reqs = [r for r in reqs if "updateShapeProperties" in r]
        assert len(autofit_reqs) == 1
        assert autofit_reqs[0]["updateShapeProperties"]["shapeProperties"]["autofit"][
            "autofitType"
        ] == "TEXT_AUTOFIT"

    def test_includes_transform_request_with_margin_x(self):
        slide = _make_slide(_AUTHOR_SHAPE, _BODY_SHAPE)
        reqs = _body_fill_requests(slide, has_images=False)

        transform_reqs = [r for r in reqs if "updatePageElementTransform" in r]
        assert len(transform_reqs) == 1
        transform = transform_reqs[0]["updatePageElementTransform"]["transform"]
        assert transform["translateX"] == _MARGIN_EMU

    def test_includes_size_request_spanning_full_width(self):
        slide = _make_slide(_AUTHOR_SHAPE, _BODY_SHAPE)
        reqs = _body_fill_requests(slide, has_images=False)

        size_reqs = [r for r in reqs if "updatePageElementSize" in r]
        assert len(size_reqs) == 1
        width = size_reqs[0]["updatePageElementSize"]["size"]["width"]["magnitude"]
        assert width == _SLIDE_WIDTH_EMU - 2 * _MARGIN_EMU

    def test_preserves_original_height(self):
        slide = _make_slide(_AUTHOR_SHAPE, _BODY_SHAPE)
        reqs = _body_fill_requests(slide, has_images=False)

        size_reqs = [r for r in reqs if "updatePageElementSize" in r]
        height = size_reqs[0]["updatePageElementSize"]["size"]["height"]
        assert height == {"magnitude": 3000000, "unit": "EMU"}

    def test_preserves_vertical_position(self):
        slide = _make_slide(_AUTHOR_SHAPE, _BODY_SHAPE)
        reqs = _body_fill_requests(slide, has_images=False)

        transform_reqs = [r for r in reqs if "updatePageElementTransform" in r]
        transform = transform_reqs[0]["updatePageElementTransform"]["transform"]
        assert transform["translateY"] == 200000  # original translateY preserved


class TestBodyFillRequestsEdgeCases:
    """Edge cases for _body_fill_requests."""

    def test_returns_empty_when_no_body_placeholder(self):
        slide = _make_slide(_AUTHOR_SHAPE)
        reqs = _body_fill_requests(slide, has_images=False)

        assert reqs == []

    def test_returns_empty_for_slide_with_no_elements(self):
        slide = {"objectId": "slide1", "pageElements": []}
        reqs = _body_fill_requests(slide, has_images=False)

        assert reqs == []

    def test_handles_missing_size_gracefully(self):
        """Shape without a 'size' field should not raise."""
        shape_no_size = {
            "objectId": "body_shape",
            "shape": {
                "text": {
                    "textElements": [{"textRun": {"content": "{{BODY}}"}}]
                }
            },
            "transform": {
                "scaleX": 1,
                "scaleY": 1,
                "translateX": 500000,
                "translateY": 200000,
                "unit": "EMU",
            },
        }
        slide = _make_slide(shape_no_size)
        reqs = _body_fill_requests(slide, has_images=False)

        # Should still produce 3 requests without raising
        assert len(reqs) == 3
