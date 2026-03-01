"""Tests for graceful error handling when image insertion fails in build_deck and append_slides."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

# Provide required env vars before importing the module
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

from googleapiclient.errors import HttpError

from weekly_slides_bot import _insert_images, append_slides, build_deck


def _make_http_error(status: int, body: str = "") -> HttpError:
    resp = MagicMock()
    resp.status = status
    return HttpError(resp, body.encode())


def _mock_slides_svc(batch_update_side_effect=None):
    """Return a mock Slides service.

    The mock supports presentations().get() and presentations().batchUpdate().
    When *batch_update_side_effect* is provided it is applied to the batchUpdate
    execute method so that callers can selectively raise errors.
    """
    svc = MagicMock()

    # presentations().get() returns a fake presentation with the template slide
    template_slide = {
        "objectId": "template_slide",
        "pageElements": [
            {
                "objectId": "author_elem",
                "shape": {
                    "shapeType": "TEXT_BOX",
                    "text": {
                        "textElements": [
                            {"textRun": {"content": "{{AUTHOR}}"}}
                        ],
                    },
                },
                "size": {
                    "width": {"magnitude": 400 * 12700},
                    "height": {"magnitude": 55 * 12700},
                },
                "transform": {"translateX": 0, "translateY": 0},
            }
        ],
    }
    fake_pres = {
        "slides": [
            {"objectId": "title_slide", "pageElements": []},
            template_slide,
            {"objectId": "end_slide", "pageElements": []},
        ]
    }
    svc.presentations().get().execute.return_value = fake_pres

    # batchUpdate returns a reply with a duplicated slide id
    default_batch_return = {
        "replies": [{"duplicateObject": {"objectId": "new_slide_1"}}]
    }
    if batch_update_side_effect is None:
        svc.presentations().batchUpdate().execute.return_value = default_batch_return
    else:
        svc.presentations().batchUpdate().execute.side_effect = batch_update_side_effect

    return svc


class TestBuildDeckImageInsertionError:
    """build_deck must not crash when inserting images into slides fails."""

    @patch("weekly_slides_bot.upload_image_to_drive", return_value="https://drive.google.com/uc?id=123")
    def test_build_deck_error_message_nonempty_for_empty_str_exception(self, _upload):
        """When str(exc) is empty, the error issue must still contain useful info."""
        call_count = {"n": 0}
        error = Exception()  # str(Exception()) == ""

        def batch_side_effect(*args, **kwargs):
            call_count["n"] += 1
            req = MagicMock()

            def execute():
                if call_count["n"] in (4, 5):
                    raise error
                return {
                    "replies": [{"duplicateObject": {"objectId": "new_slide_1"}}]
                }

            req.execute = execute
            return req

        slides_svc = MagicMock()
        slides_svc.presentations().get().execute.return_value = {
            "slides": [
                {"objectId": "title_slide", "pageElements": []},
                {
                    "objectId": "template_slide",
                    "pageElements": [
                        {
                            "objectId": "author_elem",
                            "shape": {
                                "shapeType": "TEXT_BOX",
                                "text": {
                                    "textElements": [
                                        {"textRun": {"content": "{{AUTHOR}}"}}
                                    ],
                                },
                            },
                            "size": {
                                "width": {"magnitude": 400 * 12700},
                                "height": {"magnitude": 55 * 12700},
                            },
                            "transform": {"translateX": 0, "translateY": 0},
                        }
                    ],
                },
                {"objectId": "end_slide", "pageElements": []},
            ]
        }
        slides_svc.presentations().batchUpdate.side_effect = batch_side_effect

        drive_svc = MagicMock()
        submissions = [
            {
                "id": "1",
                "author": "TestUser",
                "body": "My answer",
                "images": ["https://cdn.discord.com/img.png"],
            }
        ]

        errors = build_deck(slides_svc, drive_svc, "pres123", "Topic", submissions, named=True, image_cache={})
        assert len(errors) >= 1
        issue = errors[0]["issue"]
        # The issue must not end with ": " — it must contain real detail
        assert not issue.endswith(": ")
        assert "Exception()" in issue

    @patch("weekly_slides_bot.upload_image_to_drive", return_value="https://drive.google.com/uc?id=123")
    def test_build_deck_continues_on_image_http_error(self, _upload):
        """An HttpError during image insertion should be caught, not raised."""
        call_count = {"n": 0}
        error = _make_http_error(400, "Invalid image")

        def batch_side_effect(*args, **kwargs):
            call_count["n"] += 1
            # Fail on a later call (image insertion), succeed on others
            # The image insertion call includes createImage requests
            req = MagicMock()

            def execute():
                # Check if this is the image insertion call by inspecting
                # whether we've gotten past the initial calls
                # For simplicity, raise on the 4th batchUpdate call
                # (title text, dup, position+text, image)
                if call_count["n"] == 4:
                    raise error
                return {
                    "replies": [{"duplicateObject": {"objectId": "new_slide_1"}}]
                }

            req.execute = execute
            return req

        slides_svc = MagicMock()
        slides_svc.presentations().get().execute.return_value = {
            "slides": [
                {"objectId": "title_slide", "pageElements": []},
                {
                    "objectId": "template_slide",
                    "pageElements": [
                        {
                            "objectId": "author_elem",
                            "shape": {
                                "shapeType": "TEXT_BOX",
                                "text": {
                                    "textElements": [
                                        {"textRun": {"content": "{{AUTHOR}}"}}
                                    ],
                                },
                            },
                            "size": {
                                "width": {"magnitude": 400 * 12700},
                                "height": {"magnitude": 55 * 12700},
                            },
                            "transform": {"translateX": 0, "translateY": 0},
                        }
                    ],
                },
                {"objectId": "end_slide", "pageElements": []},
            ]
        }
        slides_svc.presentations().batchUpdate.side_effect = batch_side_effect

        drive_svc = MagicMock()
        submissions = [
            {
                "id": "1",
                "author": "TestUser",
                "body": "My answer",
                "images": ["https://cdn.discord.com/img.png"],
            }
        ]

        # Should NOT raise
        build_deck(slides_svc, drive_svc, "pres123", "Topic", submissions, named=True, image_cache={})


class TestAppendSlidesImageInsertionError:
    """append_slides must not crash when inserting images into slides fails."""

    @patch("weekly_slides_bot.upload_image_to_drive", return_value="https://drive.google.com/uc?id=123")
    def test_append_slides_continues_on_image_http_error(self, _upload):
        """An HttpError during image insertion should be caught, not raised."""
        call_count = {"n": 0}
        error = _make_http_error(400, "Invalid image")

        def batch_side_effect(*args, **kwargs):
            call_count["n"] += 1
            req = MagicMock()

            def execute():
                # The image insertion is the last batchUpdate call for a submission
                # in append_slides: dup, move, clear+resize, text replace, image
                if call_count["n"] == 5:
                    raise error
                return {
                    "replies": [{"duplicateObject": {"objectId": "new_slide_1"}}]
                }

            req.execute = execute
            return req

        # The first get() returns initial deck; subsequent get() calls return
        # deck that includes the duplicated slide so next() can find it.
        initial_pres = {
            "slides": [
                {"objectId": "title_slide", "pageElements": []},
                {
                    "objectId": "sub_slide",
                    "pageElements": [
                        {
                            "objectId": "text_elem",
                            "shape": {
                                "shapeType": "TEXT_BOX",
                                "text": {"textElements": [{"textRun": {"content": "old text"}}]},
                            },
                            "size": {
                                "width": {"magnitude": 400 * 12700},
                                "height": {"magnitude": 314 * 12700},
                            },
                            "transform": {
                                "translateX": 0,
                                "translateY": 55 * 12700,
                            },
                        }
                    ],
                },
                {"objectId": "end_slide", "pageElements": []},
            ]
        }
        pres_with_new_slide = {
            "slides": [
                {"objectId": "title_slide", "pageElements": []},
                {"objectId": "sub_slide", "pageElements": []},
                {
                    "objectId": "new_slide_1",
                    "pageElements": [
                        {
                            "objectId": "text_elem_new",
                            "shape": {
                                "shapeType": "TEXT_BOX",
                                "text": {"textElements": [{"textRun": {"content": "old text"}}]},
                            },
                            "size": {
                                "width": {"magnitude": 400 * 12700},
                                "height": {"magnitude": 314 * 12700},
                            },
                            "transform": {
                                "translateX": 0,
                                "translateY": 55 * 12700,
                            },
                        }
                    ],
                },
                {"objectId": "end_slide", "pageElements": []},
            ]
        }

        slides_svc = MagicMock()

        get_call_count = {"n": 0}

        def get_side_effect(*args, **kwargs):
            get_req = MagicMock()
            get_call_count["n"] += 1
            if get_call_count["n"] == 1:
                get_req.execute.return_value = initial_pres
            else:
                get_req.execute.return_value = pres_with_new_slide
            return get_req

        slides_svc.presentations().get.side_effect = get_side_effect
        slides_svc.presentations().batchUpdate.side_effect = batch_side_effect

        drive_svc = MagicMock()
        submissions = [
            {
                "id": "1",
                "author": "TestUser",
                "body": "My answer",
                "images": ["https://cdn.discord.com/img.png"],
            }
        ]

        # Should NOT raise
        append_slides(slides_svc, drive_svc, "pres123", submissions, named=True, image_cache={})


class TestInsertImagesHelper:
    """Tests for the _insert_images fallback behavior."""

    def test_returns_empty_on_success(self):
        """No errors returned when batch insertion succeeds."""
        svc = MagicMock()
        svc.presentations().batchUpdate().execute.return_value = {}
        result = _insert_images(svc, "pres", "slide1", ["https://img/1"], True, "A")
        assert result == []

    def test_returns_empty_when_no_urls(self):
        """No errors returned for empty URL list."""
        svc = MagicMock()
        result = _insert_images(svc, "pres", "slide1", [], True, "A")
        assert result == []

    def test_fallback_succeeds_returns_empty(self):
        """When batch fails but individual retries succeed, no errors."""
        call_count = {"n": 0}

        def side_effect(*a, **kw):
            call_count["n"] += 1
            req = MagicMock()
            if call_count["n"] == 1:
                req.execute.side_effect = Exception("batch fail")
            else:
                req.execute.return_value = {}
            return req

        svc = MagicMock()
        svc.presentations().batchUpdate.side_effect = side_effect
        result = _insert_images(svc, "pres", "slide1", ["https://img/1"], True, "A")
        assert result == []

    def test_fallback_fails_returns_errors(self):
        """When both batch and individual retries fail, error details returned."""
        def side_effect(*a, **kw):
            req = MagicMock()
            req.execute.side_effect = Exception("permanent fail")
            return req

        svc = MagicMock()
        svc.presentations().batchUpdate.side_effect = side_effect
        result = _insert_images(svc, "pres", "slide1", ["https://img/1"], True, "A")
        assert len(result) == 1
        assert "permanent fail" in result[0]

    def test_partial_fallback_failure(self):
        """When some individual images fail, only those produce errors."""
        call_count = {"n": 0}

        def side_effect(*a, **kw):
            call_count["n"] += 1
            req = MagicMock()
            if call_count["n"] == 1:
                # Batch fails
                req.execute.side_effect = Exception("batch fail")
            elif call_count["n"] == 2:
                # First individual succeeds
                req.execute.return_value = {}
            else:
                # Second individual fails
                req.execute.side_effect = Exception("img2 fail")
            return req

        svc = MagicMock()
        svc.presentations().batchUpdate.side_effect = side_effect
        result = _insert_images(
            svc, "pres", "slide1",
            ["https://img/1", "https://img/2"], True, "A",
        )
        assert len(result) == 1
        assert "img2 fail" in result[0]

    def test_empty_exception_str_uses_repr(self):
        """When str(exc) is empty, repr(exc) is used for error detail."""
        def side_effect(*a, **kw):
            req = MagicMock()
            req.execute.side_effect = Exception()  # str(Exception()) == ""
            return req

        svc = MagicMock()
        svc.presentations().batchUpdate.side_effect = side_effect
        result = _insert_images(svc, "pres", "slide1", ["https://img/1"], True, "A")
        assert len(result) == 1
        assert "Exception()" in result[0]


class TestUploadImageUrl:
    """upload_image_to_drive must produce lh3.googleusercontent.com URLs."""

    @patch("weekly_slides_bot.execute_with_retry")
    @patch("weekly_slides_bot.requests.get")
    def test_url_uses_lh3_format(self, mock_get, mock_exec):
        from weekly_slides_bot import upload_image_to_drive

        resp = MagicMock()
        resp.content = b"\x89PNG"
        resp.headers = {"content-type": "image/png"}
        mock_get.return_value = resp
        mock_exec.side_effect = [
            {"id": "abc123"},  # files().create()
            {},                # permissions().create()
        ]

        cache: dict[str, str] = {}
        result = upload_image_to_drive(MagicMock(), "https://cdn.discord.com/img.png", cache)
        assert result is not None
        assert "lh3.googleusercontent.com" in result
        assert "abc123" in result
        assert "export=download" not in result
