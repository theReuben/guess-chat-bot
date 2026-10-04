"""Tests for numbered author labels, random slide placement and renumbering."""

from __future__ import annotations

import copy
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

from weekly_slides_bot import (
    _AUTHOR_BAR_PT,
    _PT,
    _find_author_element,
    _find_body_element,
    _get_shape_text,
    append_slides,
    collect_deck_authors,
    format_author_label,
    generate_slides,
    parse_author_label,
    renumber_slides,
)


def _shape(obj_id: str, y_pt: float, text: str) -> dict:
    return {
        "objectId": obj_id,
        "size": {
            "width": {"magnitude": 500 * _PT, "unit": "EMU"},
            "height": {"magnitude": 40 * _PT, "unit": "EMU"},
        },
        "transform": {"translateX": 24 * _PT, "translateY": y_pt * _PT, "unit": "EMU"},
        "shape": {"text": {"textElements": [{"textRun": {"content": text}}]}},
    }


def _submission_slide(slide_id: str, label: str, body: str) -> dict:
    return {
        "objectId": slide_id,
        "pageElements": [
            _shape(f"{slide_id}_author", 10, label),
            _shape(f"{slide_id}_body", _AUTHOR_BAR_PT + 5, body),
        ],
    }


class _Request:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeDeck:
    """Just enough of the Slides API to move, duplicate and relabel slides."""

    def __init__(self, submissions: list[tuple[str, str]]):
        self.slides = [{"objectId": "title", "pageElements": []}]
        for i, (label, body) in enumerate(submissions):
            self.slides.append(_submission_slide(f"s{i}", label, body))
        self.slides.append({"objectId": "end", "pageElements": []})
        self._next_id = 0

    def presentations(self):
        return self

    def get(self, presentationId):
        return _Request(lambda: {"slides": copy.deepcopy(self.slides)})

    def batchUpdate(self, presentationId, body):
        return _Request(lambda: {"replies": [self._apply(r) for r in body["requests"]]})

    def _index(self, slide_id: str) -> int:
        return next(i for i, s in enumerate(self.slides) if s["objectId"] == slide_id)

    def _element(self, obj_id: str) -> dict:
        for slide in self.slides:
            for elem in slide["pageElements"]:
                if elem["objectId"] == obj_id:
                    return elem
        raise KeyError(obj_id)

    def _apply(self, req: dict) -> dict:
        if "duplicateObject" in req:
            idx = self._index(req["duplicateObject"]["objectId"])
            self._next_id += 1
            new_id = f"new{self._next_id}"
            dup = copy.deepcopy(self.slides[idx])
            dup["objectId"] = new_id
            for elem in dup["pageElements"]:
                elem["objectId"] = elem["objectId"].replace(self.slides[idx]["objectId"], new_id, 1)
            self.slides.insert(idx + 1, dup)
            return {"duplicateObject": {"objectId": new_id}}
        if "updateSlidesPosition" in req:
            (slide_id,) = req["updateSlidesPosition"]["slideObjectIds"]
            target = req["updateSlidesPosition"]["insertionIndex"]
            idx = self._index(slide_id)
            slide = self.slides.pop(idx)
            # insertionIndex is relative to the arrangement before the move.
            self.slides.insert(target - 1 if target > idx else target, slide)
        elif "deleteText" in req:
            self._element(req["deleteText"]["objectId"])["shape"]["text"]["textElements"] = []
        elif "insertText" in req:
            elem = self._element(req["insertText"]["objectId"])
            elem["shape"]["text"]["textElements"] = [{"textRun": {"content": req["insertText"]["text"]}}]
        return {}

    def labels(self) -> list[str]:
        return [
            _get_shape_text(_find_author_element(s["pageElements"]))
            for s in self.slides[1:-1]
        ]

    def bodies(self) -> list[str]:
        return [
            _get_shape_text(_find_body_element(s["pageElements"]))
            for s in self.slides[1:-1]
        ]


def _sub(i: int, author: str) -> dict:
    return {"id": str(i), "author": author, "body": f"answer by {author}", "images": [], "youtube_ids": []}


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def test_named_label_carries_number_and_name():
    assert format_author_label(7, "Sam", named=True) == "#7 — Answer: Sam"


def test_anonymous_label_leaves_the_answer_blank():
    assert format_author_label(7, "Sam", named=False) == "#7 — Answer:"


@pytest.mark.parametrize("text, name", [
    ("#7 — Answer: Sam", "Sam"),
    ("#7 — Answer:", ""),
    ("#12 - Answer: Sam Smith", "Sam Smith"),
    ("#7 — Sam", "Sam"),
    ("#12 - Sam Smith", "Sam Smith"),
    ("#7", ""),
    ("Answer: Sam", "Sam"),
    ("Answer:", ""),
    ("  #3 — Bo  \n", "Bo"),
])
def test_parse_recognises_new_and_legacy_labels(text, name):
    assert parse_author_label(text) == name


@pytest.mark.parametrize("text", ["#1 fan of cheese", "Pizza", "", "#hashtag"])
def test_parse_rejects_text_that_is_not_a_label(text):
    assert parse_author_label(text) is None


def test_author_element_found_by_numbered_label_below_the_bar():
    """The content signal works for numbered labels, wherever the box sits."""
    author = _shape("a", _AUTHOR_BAR_PT + 50, "#4 — Sam")
    body = _shape("b", _AUTHOR_BAR_PT + 5, "an answer")
    assert _find_author_element([body, author])["objectId"] == "a"


def test_collect_deck_authors_reads_numbered_and_legacy_labels():
    deck = FakeDeck([("#1 — Alice", "x"), ("Answer: Bob", "y"), ("#3", "z")])
    with patch("weekly_slides_bot.execute_with_retry", side_effect=lambda r: r.execute()):
        assert collect_deck_authors(deck, "pres") == ["Alice", "Bob"]


# ---------------------------------------------------------------------------
# Renumbering
# ---------------------------------------------------------------------------


def test_renumber_rewrites_labels_in_deck_order():
    deck = FakeDeck([("#3 — Cat", "c"), ("Answer: Dan", "d"), ("#1 — Answer: Amy", "a")])
    with patch("weekly_slides_bot.execute_with_retry", side_effect=lambda r: r.execute()):
        positions = renumber_slides(deck, "pres", named=True)

    assert deck.labels() == ["#1 — Answer: Cat", "#2 — Answer: Dan", "#3 — Answer: Amy"]
    assert positions["s0"] == 2  # 1-indexed position in the whole deck


def test_renumber_anonymous_deck_drops_names():
    deck = FakeDeck([("#2", "x"), ("Answer:", "y"), ("#1 — Answer:", "z")])
    with patch("weekly_slides_bot.execute_with_retry", side_effect=lambda r: r.execute()):
        renumber_slides(deck, "pres", named=False)

    assert deck.labels() == ["#1 — Answer:", "#2 — Answer:", "#3 — Answer:"]


def test_renumber_skips_the_api_call_when_nothing_changes():
    deck = FakeDeck([("#1 — Answer: Amy", "a"), ("#2 — Answer: Ben", "b")])
    deck.batchUpdate = MagicMock()
    with patch("weekly_slides_bot.execute_with_retry", side_effect=lambda r: r.execute()):
        renumber_slides(deck, "pres", named=True)

    deck.batchUpdate.assert_not_called()


# ---------------------------------------------------------------------------
# append_slides placement
# ---------------------------------------------------------------------------


def _append(deck: FakeDeck, subs: list[dict], named: bool, seed: int) -> list[dict]:
    with patch("weekly_slides_bot.execute_with_retry", side_effect=lambda r: r.execute()):
        return append_slides(deck, MagicMock(), "pres", subs, named=named, image_cache={}, seed=seed)


def test_appended_slides_are_numbered_in_order_with_every_name_present():
    deck = FakeDeck([("#1 — Amy", "a"), ("#2 — Ben", "b"), ("#3 — Cat", "c")])
    _append(deck, [_sub(1, "Dan"), _sub(2, "Eve")], named=True, seed=42)

    labels = deck.labels()
    assert [label.split(" — ")[0] for label in labels] == ["#1", "#2", "#3", "#4", "#5"]
    assert all(" — Answer: " in label for label in labels)
    assert {parse_author_label(label) for label in labels} == {"Amy", "Ben", "Cat", "Dan", "Eve"}
    assert deck.slides[0]["objectId"] == "title" and deck.slides[-1]["objectId"] == "end"


def test_named_and_anonymous_decks_get_the_same_order():
    existing = [("x", "a"), ("x", "b"), ("x", "c"), ("x", "d")]
    named = FakeDeck([(f"#{i + 1} — P{i}", body) for i, (_, body) in enumerate(existing)])
    anon = FakeDeck([(f"#{i + 1}", body) for i, (_, body) in enumerate(existing)])
    subs = [_sub(1, "Dan"), _sub(2, "Eve"), _sub(3, "Fay")]

    _append(named, subs, named=True, seed=7)
    _append(anon, subs, named=False, seed=7)

    assert named.bodies() == anon.bodies()
    assert anon.labels() == [f"#{n} — Answer:" for n in range(1, 8)]


def test_late_submissions_do_not_always_land_at_the_end():
    positions = set()
    for seed in range(20):
        deck = FakeDeck([("#1 — Amy", "a"), ("#2 — Ben", "b"), ("#3 — Cat", "c")])
        _append(deck, [_sub(1, "Dan")], named=True, seed=seed)
        positions.add(deck.bodies().index("answer by Dan"))

    assert len(positions) > 1


def test_error_slide_numbers_reflect_final_positions():
    deck = FakeDeck([("#1 — Amy", "a"), ("#2 — Ben", "b")])
    sub = _sub(1, "Dan")
    sub["images"] = ["https://cdn/img.png"]
    with patch("weekly_slides_bot.upload_image_to_drive", return_value=None):
        errors = _append(deck, [sub, _sub(2, "Eve")], named=True, seed=3)

    (err,) = errors
    final_index = next(i for i, s in enumerate(deck.slides) if s["objectId"] == err["slide_id"])
    assert err["slide_number"] == final_index + 1


# ---------------------------------------------------------------------------
# generate_slides shuffles a fresh round
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_new_round_builds_both_decks_from_one_shuffled_order():
    marker = MagicMock(id=100, content="GUESS CHAT Test")
    msgs = []
    for i in range(6):
        m = MagicMock(id=200 + i, content=f"SUBMISSION answer {i}", attachments=[])
        m.author = MagicMock(id=i, display_name=f"User{i}")
        m.guild = MagicMock()
        m.guild.get_member.return_value = MagicMock(display_name=f"User{i}")
        msgs.append(m)
    calls = 0

    async def history(*args, **kwargs):
        nonlocal calls
        calls += 1
        for msg in [marker] if calls == 1 else msgs:
            yield msg

    channel = MagicMock(history=history)
    results_channel = MagicMock(send=AsyncMock())
    client = MagicMock()
    client.user = MagicMock(id=marker.author.id)
    client.get_channel.side_effect = lambda cid: channel if cid == 1 else results_channel

    with patch("weekly_slides_bot.load_state", return_value={}), \
            patch("weekly_slides_bot.save_state"), \
            patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock())), \
            patch("weekly_slides_bot.copy_presentation", return_value="pres"), \
            patch("weekly_slides_bot.share_presentation"), \
            patch("weekly_slides_bot.read_deck_authors", return_value=[]), \
            patch("weekly_slides_bot.random.sample", side_effect=lambda seq, k: list(reversed(seq))), \
            patch("weekly_slides_bot.build_deck", return_value=[]) as build:
        await generate_slides(client)

    named_order = [s["author"] for s in build.call_args_list[0].args[4]]
    anon_order = [s["author"] for s in build.call_args_list[1].args[4]]
    assert named_order == anon_order == [f"User{i}" for i in reversed(range(6))]


def test_collect_deck_authors_never_lists_the_answer_prompt_as_a_name():
    deck = FakeDeck([("#1 — Answer:", "x"), ("#2 — Answer: Bo", "y")])
    with patch("weekly_slides_bot.execute_with_retry", side_effect=lambda r: r.execute()):
        assert collect_deck_authors(deck, "pres") == ["Bo"]


def test_renumber_converts_the_interim_format():
    """Decks built with "#7 — Sam" labels pick up "Answer:" on the next renumber."""
    deck = FakeDeck([("#1 — Amy", "a"), ("#2", "b")])
    with patch("weekly_slides_bot.execute_with_retry", side_effect=lambda r: r.execute()):
        renumber_slides(deck, "pres", named=True)

    assert deck.labels() == ["#1 — Answer: Amy", "#2 — Answer:"]
