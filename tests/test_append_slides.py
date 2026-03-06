"""Tests for the append_slides function text insertion logic."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch, call

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
    _find_author_element,
    _find_body_element,
    _get_shape_text,
    append_slides,
)


def _make_shape_element(obj_id: str, x_pt: float, y_pt: float, w_pt: float, h_pt: float, text: str | None = None):
    """Build a page element dict mimicking Google Slides API shape structure."""
    elem = {
        "objectId": obj_id,
        "size": {
            "width": {"magnitude": w_pt * _PT, "unit": "EMU"},
            "height": {"magnitude": h_pt * _PT, "unit": "EMU"},
        },
        "transform": {
            "translateX": x_pt * _PT,
            "translateY": y_pt * _PT,
            "unit": "EMU",
        },
    }
    if text is not None:
        elem["shape"] = {"text": {"textElements": [{"textRun": {"content": text}}]}}
    else:
        elem["shape"] = {}
    return elem


def _fake_slide(slide_id: str, author_text: str = "Answer: OldUser", body_text: str = "Old body"):
    """Return a slide dict with author and body shapes containing real text."""
    author_elem = _make_shape_element(
        f"{slide_id}_author", 24, 10, 300, 40, text=author_text,
    )
    body_elem = _make_shape_element(
        f"{slide_id}_body", 24, _AUTHOR_BAR_PT + 5, 500, 300, text=body_text,
    )
    return {
        "objectId": slide_id,
        "pageElements": [author_elem, body_elem],
    }


class TestAppendSlidesUsesInsertText:
    """append_slides must use insertText (not replaceAllText) after clearing text."""

    def test_insert_text_used_for_new_submission(self):
        """Appended slides insert author/body via insertText, not replaceAllText."""
        # Build a mock presentation with a title slide, one existing submission, and end slide
        title_slide = {"objectId": "title", "pageElements": []}
        existing_slide = _fake_slide("existing")
        end_slide = {"objectId": "end", "pageElements": []}
        initial_slides = [title_slide, existing_slide, end_slide]

        new_slide_id = "new_slide_1"
        new_slide = _fake_slide(new_slide_id, author_text="Answer: OldUser", body_text="Old body")

        # Track all batchUpdate calls
        batch_calls = []

        def mock_batch_update(**kwargs):
            batch_calls.append(kwargs["body"]["requests"])
            mock_resp = MagicMock()
            if any("duplicateObject" in r for r in kwargs["body"]["requests"]):
                mock_resp.return_value = {
                    "replies": [{"duplicateObject": {"objectId": new_slide_id}}]
                }
            else:
                mock_resp.return_value = {}
            return mock_resp

        # Mock presentations().get() to return current state
        get_call_count = [0]

        def mock_get(**kwargs):
            get_call_count[0] += 1
            mock_resp = MagicMock()
            if get_call_count[0] == 1:
                # First get: initial presentation state
                mock_resp.return_value = {"slides": initial_slides}
            else:
                # After duplication: include the new slide
                mock_resp.return_value = {"slides": [title_slide, existing_slide, new_slide, end_slide]}
            return mock_resp

        mock_slides_svc = MagicMock()
        mock_slides_svc.presentations.return_value.batchUpdate.side_effect = mock_batch_update
        mock_slides_svc.presentations.return_value.get.side_effect = mock_get

        mock_drive_svc = MagicMock()

        submissions = [{"id": "1", "author": "NewUser", "body": "New body text", "images": [], "youtube_ids": []}]

        with patch("weekly_slides_bot.execute_with_retry", side_effect=lambda req: req()):
            errors = append_slides(
                mock_slides_svc,
                mock_drive_svc,
                "pres_123",
                submissions,
                named=True,
                image_cache={},
            )

        assert errors == []

        # Collect all requests from all batchUpdate calls
        all_requests = [req for batch in batch_calls for req in batch]

        # Verify insertText was used (not replaceAllText)
        insert_text_reqs = [r for r in all_requests if "insertText" in r]
        replace_all_reqs = [r for r in all_requests if "replaceAllText" in r]

        assert len(insert_text_reqs) == 2, f"Expected 2 insertText requests, got {len(insert_text_reqs)}"
        assert len(replace_all_reqs) == 0, f"Expected 0 replaceAllText requests, got {len(replace_all_reqs)}"

        # Verify the correct text was inserted
        inserted_texts = {r["insertText"]["text"] for r in insert_text_reqs}
        assert "Answer: NewUser" in inserted_texts
        assert "New body text" in inserted_texts

    def test_anonymous_mode_author_text(self):
        """In anonymous mode, the author text should be 'Answer:' without the name."""
        title_slide = {"objectId": "title", "pageElements": []}
        existing_slide = _fake_slide("existing")
        end_slide = {"objectId": "end", "pageElements": []}
        initial_slides = [title_slide, existing_slide, end_slide]

        new_slide_id = "new_slide_1"
        new_slide = _fake_slide(new_slide_id)

        batch_calls = []

        def mock_batch_update(**kwargs):
            batch_calls.append(kwargs["body"]["requests"])
            mock_resp = MagicMock()
            if any("duplicateObject" in r for r in kwargs["body"]["requests"]):
                mock_resp.return_value = {
                    "replies": [{"duplicateObject": {"objectId": new_slide_id}}]
                }
            else:
                mock_resp.return_value = {}
            return mock_resp

        get_call_count = [0]

        def mock_get(**kwargs):
            get_call_count[0] += 1
            mock_resp = MagicMock()
            if get_call_count[0] == 1:
                mock_resp.return_value = {"slides": initial_slides}
            else:
                mock_resp.return_value = {"slides": [title_slide, existing_slide, new_slide, end_slide]}
            return mock_resp

        mock_slides_svc = MagicMock()
        mock_slides_svc.presentations.return_value.batchUpdate.side_effect = mock_batch_update
        mock_slides_svc.presentations.return_value.get.side_effect = mock_get

        mock_drive_svc = MagicMock()

        submissions = [{"id": "1", "author": "TestUser", "body": "My answer", "images": [], "youtube_ids": []}]

        with patch("weekly_slides_bot.execute_with_retry", side_effect=lambda req: req()):
            append_slides(
                mock_slides_svc,
                mock_drive_svc,
                "pres_123",
                submissions,
                named=False,
                image_cache={},
            )

        all_requests = [req for batch in batch_calls for req in batch]
        insert_text_reqs = [r for r in all_requests if "insertText" in r]
        inserted_texts = {r["insertText"]["text"] for r in insert_text_reqs}
        assert "Answer:" in inserted_texts
        assert "My answer" in inserted_texts


class TestFindAuthorElement:
    """Tests for the _find_author_element helper."""

    def test_finds_author_above_bar(self):
        """Shape above the author bar threshold is returned."""
        author = _make_shape_element("auth", 24, 10, 300, 40, text="Answer: User")
        body = _make_shape_element("body", 24, _AUTHOR_BAR_PT + 5, 500, 300, text="Some text")
        result = _find_author_element([author, body])
        assert result is not None
        assert result["objectId"] == "auth"

    def test_returns_none_when_no_shapes_above_bar(self):
        """Returns None if all shapes are below the author bar and none start with Answer:."""
        body = _make_shape_element("body", 24, _AUTHOR_BAR_PT + 5, 500, 300, text="Some text")
        result = _find_author_element([body])
        assert result is None

    def test_fallback_finds_author_by_text_content(self):
        """When no shapes are above the bar, fallback finds shape starting with 'Answer:'."""
        # Both shapes below the author bar threshold — position-based search fails
        author = _make_shape_element("auth", 24, _AUTHOR_BAR_PT + 5, 300, 40, text="Answer: User")
        body = _make_shape_element("body", 24, _AUTHOR_BAR_PT + 50, 500, 300, text="Some body text")
        result = _find_author_element([author, body])
        assert result is not None
        assert result["objectId"] == "auth"

    def test_returns_largest_shape_above_bar(self):
        """When multiple shapes exist above the bar, the largest is returned."""
        small = _make_shape_element("small", 24, 5, 50, 20, text="Small")
        large = _make_shape_element("large", 24, 10, 300, 40, text="Large")
        result = _find_author_element([small, large])
        assert result is not None
        assert result["objectId"] == "large"


class TestFindBodyElement:
    """Tests for the _find_body_element helper."""

    def test_finds_body_below_bar(self):
        """Shape below the author bar threshold is returned."""
        author = _make_shape_element("auth", 24, 10, 300, 40, text="Answer: User")
        body = _make_shape_element("body", 24, _AUTHOR_BAR_PT + 5, 500, 300, text="Some text")
        result = _find_body_element([author, body])
        assert result is not None
        assert result["objectId"] == "body"

    def test_returns_none_when_no_shapes(self):
        """Returns None if there are no shape elements."""
        result = _find_body_element([])
        assert result is None

    def test_fallback_finds_body_by_text_content(self):
        """When no shapes are below the bar, fallback finds shape not starting with 'Answer:'."""
        # Both shapes above the author bar threshold — position-based search fails
        author = _make_shape_element("auth", 24, 10, 300, 40, text="Answer: User")
        body = _make_shape_element("body", 24, 20, 500, 300, text="Some body text")
        result = _find_body_element([author, body])
        assert result is not None
        assert result["objectId"] == "body"


class TestGetShapeText:
    """Tests for the _get_shape_text helper."""

    def test_extracts_text_from_single_run(self):
        elem = _make_shape_element("e", 0, 0, 100, 100, text="Hello world")
        assert _get_shape_text(elem) == "Hello world"

    def test_returns_empty_for_no_text(self):
        elem = _make_shape_element("e", 0, 0, 100, 100)
        assert _get_shape_text(elem) == ""


class TestFallbackAppendSlides:
    """append_slides should insert text even when shapes aren't at expected positions."""

    def test_text_inserted_when_body_above_threshold(self):
        """Text is inserted via fallback when body shape is above the author bar threshold."""
        title_slide = {"objectId": "title", "pageElements": []}
        # Existing slide with body above the threshold (unusual template layout)
        author_elem = _make_shape_element("exist_author", 24, 5, 300, 40, text="Answer: OldUser")
        body_elem = _make_shape_element("exist_body", 24, 20, 500, 300, text="Old body text")
        existing_slide = {
            "objectId": "existing",
            "pageElements": [author_elem, body_elem],
        }
        end_slide = {"objectId": "end", "pageElements": []}
        initial_slides = [title_slide, existing_slide, end_slide]

        new_slide_id = "new_slide_1"
        new_author = _make_shape_element(f"{new_slide_id}_author", 24, 5, 300, 40, text="Answer: OldUser")
        new_body = _make_shape_element(f"{new_slide_id}_body", 24, 20, 500, 300, text="Old body text")
        new_slide = {
            "objectId": new_slide_id,
            "pageElements": [new_author, new_body],
        }

        batch_calls = []

        def mock_batch_update(**kwargs):
            batch_calls.append(kwargs["body"]["requests"])
            mock_resp = MagicMock()
            if any("duplicateObject" in r for r in kwargs["body"]["requests"]):
                mock_resp.return_value = {
                    "replies": [{"duplicateObject": {"objectId": new_slide_id}}]
                }
            else:
                mock_resp.return_value = {}
            return mock_resp

        get_call_count = [0]

        def mock_get(**kwargs):
            get_call_count[0] += 1
            mock_resp = MagicMock()
            if get_call_count[0] == 1:
                mock_resp.return_value = {"slides": initial_slides}
            else:
                mock_resp.return_value = {"slides": [title_slide, existing_slide, new_slide, end_slide]}
            return mock_resp

        mock_slides_svc = MagicMock()
        mock_slides_svc.presentations.return_value.batchUpdate.side_effect = mock_batch_update
        mock_slides_svc.presentations.return_value.get.side_effect = mock_get

        submissions = [{"id": "1", "author": "NewUser", "body": "New body text", "images": [], "youtube_ids": []}]

        with patch("weekly_slides_bot.execute_with_retry", side_effect=lambda req: req()):
            errors = append_slides(
                mock_slides_svc,
                MagicMock(),
                "pres_123",
                submissions,
                named=True,
                image_cache={},
            )

        assert errors == []

        all_requests = [req for batch in batch_calls for req in batch]
        insert_text_reqs = [r for r in all_requests if "insertText" in r]
        assert len(insert_text_reqs) == 2, f"Expected 2 insertText requests, got {len(insert_text_reqs)}"
        inserted_texts = {r["insertText"]["text"] for r in insert_text_reqs}
        assert "Answer: NewUser" in inserted_texts
        assert "New body text" in inserted_texts

        # Ensure the author text is written into the author textbox on the new slide
        author_req = next(
            r for r in insert_text_reqs if r["insertText"]["text"] == "Answer: NewUser"
        )
        assert author_req["insertText"]["objectId"] == f"{new_slide_id}_author"

        # Ensure the body text is written into the body textbox on the new slide
        body_req = next(
            r for r in insert_text_reqs if r["insertText"]["text"] == "New body text"
        )
        assert body_req["insertText"]["objectId"] == f"{new_slide_id}_body"

    def test_text_inserted_when_author_below_threshold_and_body_above(self):
        """Fallback still inserts text correctly when author is below and body is above the author bar threshold."""
        title_slide = {"objectId": "title", "pageElements": []}
        # Existing slide with author below the threshold and body above it (swapped layout).
        author_elem = _make_shape_element(
            "exist_author",
            24,
            _AUTHOR_BAR_PT + 10,
            300,
            40,
            text="Answer: OldUser",
        )
        body_elem = _make_shape_element(
            "exist_body",
            24,
            _AUTHOR_BAR_PT - 20,
            500,
            300,
            text="Old body text",
        )
        existing_slide = {
            "objectId": "existing",
            "pageElements": [author_elem, body_elem],
        }
        end_slide = {"objectId": "end", "pageElements": []}
        initial_slides = [title_slide, existing_slide, end_slide]

        new_slide_id = "new_slide_2"
        new_author = _make_shape_element(
            f"{new_slide_id}_author",
            24,
            _AUTHOR_BAR_PT + 10,
            300,
            40,
            text="Answer: OldUser",
        )
        new_body = _make_shape_element(
            f"{new_slide_id}_body",
            24,
            _AUTHOR_BAR_PT - 20,
            500,
            300,
            text="Old body text",
        )
        new_slide = {
            "objectId": new_slide_id,
            "pageElements": [new_author, new_body],
        }

        batch_calls = []

        def mock_batch_update(**kwargs):
            batch_calls.append(kwargs["body"]["requests"])
            mock_resp = MagicMock()
            if any("duplicateObject" in r for r in kwargs["body"]["requests"]):
                mock_resp.return_value = {
                    "replies": [{"duplicateObject": {"objectId": new_slide_id}}]
                }
            else:
                mock_resp.return_value = {}
            return mock_resp

        get_call_count = [0]

        def mock_get(**kwargs):
            get_call_count[0] += 1
            mock_resp = MagicMock()
            if get_call_count[0] == 1:
                mock_resp.return_value = {"slides": initial_slides}
            else:
                mock_resp.return_value = {
                    "slides": [title_slide, existing_slide, new_slide, end_slide]
                }
            return mock_resp

        mock_slides_svc = MagicMock()
        mock_slides_svc.presentations.return_value.batchUpdate.side_effect = mock_batch_update
        mock_slides_svc.presentations.return_value.get.side_effect = mock_get

        submissions = [
            {
                "id": "2",
                "author": "NewUser",
                "body": "New body text",
                "images": [],
                "youtube_ids": [],
            }
        ]

        with patch("weekly_slides_bot.execute_with_retry", side_effect=lambda req: req()):
            errors = append_slides(
                mock_slides_svc,
                MagicMock(),
                "pres_456",
                submissions,
                named=True,
                image_cache={},
            )

        assert errors == []

        all_requests = [req for batch in batch_calls for req in batch]
        insert_text_reqs = [r for r in all_requests if "insertText" in r]
        assert len(insert_text_reqs) == 2, f"Expected 2 insertText requests, got {len(insert_text_reqs)}"
        inserted_texts = {r["insertText"]["text"] for r in insert_text_reqs}
        assert "Answer: NewUser" in inserted_texts
        assert "New body text" in inserted_texts

        author_req = next(
            r for r in insert_text_reqs if r["insertText"]["text"] == "Answer: NewUser"
        )
        assert author_req["insertText"]["objectId"] == f"{new_slide_id}_author"

        body_req = next(
            r for r in insert_text_reqs if r["insertText"]["text"] == "New body text"
        )
        assert body_req["insertText"]["objectId"] == f"{new_slide_id}_body"
