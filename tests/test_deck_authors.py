"""Tests for reading author names back off the named deck.

Slides added by hand never pass through the Discord scan, so the results
message reads the names off the deck as well as from the submissions.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

from weekly_slides_bot import (
    _AUTHOR_BAR_PT,
    _PT,
    collect_deck_authors,
    format_results_message,
    read_deck_authors,
)


def _shape(obj_id: str, y_pt: float, w_pt: float, h_pt: float, text: str):
    return {
        "objectId": obj_id,
        "size": {
            "width": {"magnitude": w_pt * _PT, "unit": "EMU"},
            "height": {"magnitude": h_pt * _PT, "unit": "EMU"},
        },
        "transform": {"translateX": 24 * _PT, "translateY": y_pt * _PT, "unit": "EMU"},
        "shape": {"text": {"textElements": [{"textRun": {"content": text}}]}},
    }


def _submission_slide(slide_id: str, author_text: str, body_text: str = "some answer"):
    return {
        "objectId": slide_id,
        "pageElements": [
            _shape(f"{slide_id}_author", 10, 300, 40, author_text),
            _shape(f"{slide_id}_body", _AUTHOR_BAR_PT + 5, 500, 300, body_text),
        ],
    }


def _plain_slide(slide_id: str, text: str):
    return {
        "objectId": slide_id,
        "pageElements": [_shape(f"{slide_id}_t", 100, 500, 200, text)],
    }


def _slides_svc(slides: list[dict]):
    svc = MagicMock()
    svc.presentations.return_value.get.return_value.execute.return_value = {
        "slides": slides
    }
    return svc


def test_collects_author_names_skipping_title_and_end_slides():
    svc = _slides_svc([
        _plain_slide("title", "Guess Chat — DnD Characters"),
        _submission_slide("s1", "Answer: Alice"),
        _submission_slide("s2", "Answer: Bob"),
        _plain_slide("end", "Thanks for playing"),
    ])

    assert collect_deck_authors(svc, "pres") == ["Alice", "Bob"]


def test_includes_manually_added_slides():
    """A slide a mod typed by hand is picked up like any other."""
    svc = _slides_svc([
        _plain_slide("title", "Guess Chat"),
        _submission_slide("s1", "Answer: Alice"),
        _submission_slide("manual", "Answer: Eve"),
        _plain_slide("end", "The End"),
    ])

    assert collect_deck_authors(svc, "pres") == ["Alice", "Eve"]


def test_ignores_anonymous_deck_and_blank_authors():
    svc = _slides_svc([
        _plain_slide("title", "Guess Chat"),
        _submission_slide("s1", "Answer:"),
        _submission_slide("s2", "Answer:   "),
        _plain_slide("end", "The End"),
    ])

    assert collect_deck_authors(svc, "pres") == []


def test_deduplicates_repeated_names():
    svc = _slides_svc([
        _plain_slide("title", "Guess Chat"),
        _submission_slide("s1", "Answer: Alice"),
        _submission_slide("s2", "Answer: Alice"),
        _plain_slide("end", "The End"),
    ])

    assert collect_deck_authors(svc, "pres") == ["Alice"]


def test_short_deck_yields_no_names():
    svc = _slides_svc([_plain_slide("title", "Guess Chat")])

    assert collect_deck_authors(svc, "pres") == []


def test_results_message_merges_deck_authors_with_submissions():
    submissions = [
        {"author": "Alice", "body": "a"},
        {"author": "bob", "body": "b"},
    ]
    msg = format_results_message(
        "DnD Characters",
        submissions,
        "https://named",
        "https://anon",
        deck_authors=["Alice", "Eve"],
    )

    assert "**Submissions (3):**" in msg
    # Sorted case-insensitively, no duplicate for Alice
    assert msg.splitlines()[-3:] == ["  • Alice", "  • bob", "  • Eve"]


def test_results_message_without_deck_authors_is_unchanged():
    submissions = [{"author": "Alice", "body": "a"}]
    msg = format_results_message("Topic", submissions, "https://named", "https://anon")

    assert "**Submissions (1):**" in msg
    assert msg.endswith("  • Alice")


def test_results_message_lists_deck_authors_when_no_submissions():
    """The preview re-post has no submissions but should still name the deck."""
    msg = format_results_message(
        "Topic", [], "https://named", "https://anon", deck_authors=["Eve"],
    )

    assert "**Submissions (1):**" in msg
    assert msg.endswith("  • Eve")


@pytest.mark.asyncio
async def test_read_deck_authors_survives_api_failure():
    """A failed Slides read must not stop the results message going out."""
    with patch(
        "weekly_slides_bot.collect_deck_authors", side_effect=RuntimeError("boom")
    ):
        assert await read_deck_authors(MagicMock(), "pres") == []
