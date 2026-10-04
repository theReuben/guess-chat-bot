"""Tests for _lay_out_submission, which sizes and places one slide's content."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

from weekly_slides_bot import _AUTHOR_BAR_PT, _PT, UploadedImage, _lay_out_submission


def _shape(obj_id: str, y_pt: float, text: str) -> dict:
    return {
        "objectId": obj_id,
        "shape": {"text": {"textElements": [{"textRun": {"content": text}}]}},
        "size": {"width": {"magnitude": 300 * _PT}, "height": {"magnitude": 30 * _PT}},
        "transform": {"translateX": 0, "translateY": y_pt * _PT},
    }


_ELEMS = [_shape("author", 59, "#1 — Sam"), _shape("body", _AUTHOR_BAR_PT + 30, "{{BODY}}")]
_META = {"slide_number": 2, "slide_id": "s1", "message_id": "9"}


def _run(sub: dict, uploads=None, page=(960, 540)):
    svc = MagicMock()
    sent: list[list[dict]] = []
    svc.presentations.return_value.batchUpdate.side_effect = (
        lambda presentationId, body: sent.append(body["requests"]) or MagicMock()
    )
    with patch("weekly_slides_bot.execute_with_retry"), \
            patch("weekly_slides_bot.upload_image_to_drive", side_effect=uploads or []):
        errors = _lay_out_submission(svc, MagicMock(), "pres", "s1", _ELEMS, page, sub, {}, _META)
    return [r for batch in sent for r in batch], errors


def _sub(body="Pizza - food", images=(), youtube=()):
    return {"author": "Sam", "body": body, "images": list(images), "youtube_ids": list(youtube)}


def test_text_only_sets_the_font_and_fills_the_real_page():
    reqs, errors = _run(_sub())
    assert errors == []
    (transform,) = [r for r in reqs if "updatePageElementTransform" in r]
    t = transform["updatePageElementTransform"]["transform"]
    # Body spans the 960pt page between the margins, below the author label.
    assert t["translateX"] + t["scaleX"] * 300 * _PT == 936 * _PT
    assert t["translateY"] > 89 * _PT
    assert any("updateTextStyle" in r for r in reqs)


def test_images_are_placed_using_their_uploaded_shapes():
    uploads = [UploadedImage("https://lh3/a", 2.0), UploadedImage("https://lh3/b", 0.5)]
    reqs, errors = _run(_sub(images=["https://cdn/a", "https://cdn/b"]), uploads)
    assert errors == []
    images = [r["createImage"] for r in reqs if "createImage" in r]
    assert [i["url"] for i in images] == ["https://lh3/a", "https://lh3/b"]
    for img, aspect in zip(images, (2.0, 0.5)):
        size = img["elementProperties"]["size"]
        assert size["width"]["magnitude"] / size["height"]["magnitude"] == aspect


def test_a_failed_upload_is_reported_and_the_rest_still_placed():
    reqs, errors = _run(_sub(images=["https://cdn/a", "https://cdn/b"]), [None, UploadedImage("https://lh3/b", 1.0)])
    assert len(errors) == 1
    assert "Failed to upload 1 image(s)" in errors[0]["issue"]
    assert errors[0]["slide_id"] == "s1"
    assert len([r for r in reqs if "createImage" in r]) == 1


def test_video_is_embedded_when_there_are_no_images():
    reqs, _ = _run(_sub(youtube=["dQw4w9WgXcQ"]))
    (video,) = [r["createVideo"] for r in reqs if "createVideo" in r]
    size = video["elementProperties"]["size"]
    assert size["width"]["magnitude"] / size["height"]["magnitude"] == 16 / 9


def test_images_take_precedence_over_video():
    reqs, _ = _run(_sub(images=["https://cdn/a"], youtube=["v"]), [UploadedImage("https://lh3/a", 1.0)])
    assert not any("createVideo" in r for r in reqs)


def test_image_only_slide_sets_no_font():
    reqs, _ = _run(_sub(body="", images=["https://cdn/a"]), [UploadedImage("https://lh3/a", 1.0)])
    assert not any("updateTextStyle" in r for r in reqs)
    assert any("createImage" in r for r in reqs)


def test_short_text_only_slide_is_centred():
    reqs, _ = _run(_sub(body="Maine Coon"))
    (para,) = [r["updateParagraphStyle"] for r in reqs if "updateParagraphStyle" in r]
    assert para["objectId"] == "body"
    assert para["style"]["alignment"] == "CENTER"
