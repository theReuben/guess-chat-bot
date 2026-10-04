"""Tests for resubmissions: only a person's latest submission is kept, and a
resubmission updates their existing slide rather than adding a second one."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

from tests.test_slide_numbering import FakeDeck
from weekly_slides_bot import generate_slides, replace_resubmitted_slides, submitter_key


def _sub(msg_id: str, author: str, body: str, author_id: str | None = None) -> dict:
    sub = {"id": msg_id, "author": author, "body": body, "images": [], "youtube_ids": []}
    if author_id is not None:
        sub["author_id"] = author_id
    return sub


def _replace(named: FakeDeck, anon: FakeDeck, subs: list[dict]):
    svc = MagicMock()
    decks = {"named": named, "anon": anon}
    svc.presentations.return_value.get.side_effect = lambda presentationId: decks[presentationId].get(presentationId)
    svc.presentations.return_value.batchUpdate.side_effect = (
        lambda presentationId, body: decks[presentationId].batchUpdate(presentationId, body)
    )
    with patch("weekly_slides_bot.execute_with_retry", side_effect=lambda r: r.execute()):
        return replace_resubmitted_slides(svc, MagicMock(), "named", "anon", subs, {})


# ---------------------------------------------------------------------------
# submitter_key
# ---------------------------------------------------------------------------


def test_submitters_are_identified_by_discord_id():
    assert submitter_key(_sub("1", "hush", "x", author_id="42")) == "42"


def test_submissions_without_an_id_fall_back_to_the_name():
    assert submitter_key(_sub("1", "hush", "x")) == "hush"


# ---------------------------------------------------------------------------
# replace_resubmitted_slides
# ---------------------------------------------------------------------------


def _decks():
    named = FakeDeck([("#1 — Answer: Amy", "a"), ("#2 — Answer: hush", "old answer"), ("#3 — Answer: Cat", "c")])
    anon = FakeDeck([("#1 — Answer:", "a"), ("#2 — Answer:", "old answer"), ("#3 — Answer:", "c")])
    return named, anon


def test_resubmission_replaces_the_slide_in_both_decks_in_place():
    named, anon = _decks()
    errors, not_found = _replace(named, anon, [_sub("9", "hush", "new answer", "42")])

    assert errors == [] and not_found == []
    assert named.bodies() == ["a", "new answer", "c"]
    assert anon.bodies() == ["a", "new answer", "c"]
    # Same number and position; no slide added.
    assert named.labels() == ["#1 — Answer: Amy", "#2 — Answer: hush", "#3 — Answer: Cat"]
    assert anon.labels() == ["#1 — Answer:", "#2 — Answer:", "#3 — Answer:"]


def test_old_label_formats_are_matched_and_updated():
    named = FakeDeck([("Answer: hush", "old"), ("#2 — Bo", "b")])
    anon = FakeDeck([("#1", "old"), ("#2", "b")])
    _, not_found = _replace(named, anon, [_sub("9", "hush", "new", "42")])

    assert not_found == []
    assert named.bodies()[0] == "new"


def test_missing_earlier_slide_is_reported_and_handed_back_to_add():
    named, anon = _decks()
    resub = _sub("9", "Dan", "new", "7")
    errors, not_found = _replace(named, anon, [resub])

    assert not_found == [resub]
    (err,) = errors
    assert err["author"] == "Dan"
    assert "earlier slide couldn't be found" in err["issue"]
    assert named.bodies() == ["a", "old answer", "c"]  # untouched


def test_mismatched_anonymous_deck_is_left_alone():
    """If the anonymous deck has no slide with that number, neither deck changes."""
    named, _ = _decks()
    anon = FakeDeck([("#1 — Answer:", "a")])
    _, not_found = _replace(named, anon, [_sub("9", "hush", "new", "42")])

    assert len(not_found) == 1
    assert named.bodies() == ["a", "old answer", "c"]


# ---------------------------------------------------------------------------
# generate_slides
# ---------------------------------------------------------------------------


def _message(msg_id: int, user_id: int, name: str, text: str):
    m = MagicMock(id=msg_id, content=f"SUBMISSION {text}", attachments=[])
    m.author = MagicMock(id=user_id, display_name=name)
    m.guild = MagicMock()
    m.guild.get_member.return_value = MagicMock(display_name=name)
    return m


def _client(marker, msgs):
    calls = 0

    async def history(*args, **kwargs):
        nonlocal calls
        calls += 1
        for msg in [marker] if calls == 1 else msgs:
            yield msg

    channel = MagicMock(history=history)
    results = MagicMock(send=AsyncMock(return_value=MagicMock(jump_url="https://x")))
    client = MagicMock()
    client.user = MagicMock(id=marker.author.id)
    client.get_channel.side_effect = lambda cid: channel if cid == 1 else results
    return client


async def _run(state: dict, msgs: list):
    marker = MagicMock(id=100, content="GUESS CHAT Test")
    with patch("weekly_slides_bot.load_state", return_value=state), \
            patch("weekly_slides_bot.save_state") as save, \
            patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock())), \
            patch("weekly_slides_bot.read_deck_authors", return_value=[]), \
            patch("weekly_slides_bot.replace_resubmitted_slides", return_value=([], [])) as replace, \
            patch("weekly_slides_bot.append_slides", return_value=[]) as append, \
            patch("weekly_slides_bot.build_deck", return_value=[]) as build, \
            patch("weekly_slides_bot.copy_presentation", return_value="pres"), \
            patch("weekly_slides_bot.share_presentation"):
        await generate_slides(_client(marker, msgs))
    return replace, append, build, save


_STATE = {"marker_id": "100", "named_pres_id": "named", "anon_pres_id": "anon", "processed_ids": ["201", "202"]}


@pytest.mark.asyncio
async def test_resubmission_after_a_run_replaces_instead_of_appending():
    msgs = [_message(201, 1, "Amy", "first"), _message(202, 2, "hush", "old"), _message(203, 2, "hush", "new")]
    replace, append, _, save = await _run(dict(_STATE), msgs)

    (resubs,) = [c.args[4] for c in replace.call_args_list]
    assert [s["body"] for s in resubs] == ["new"]
    append.assert_not_called()
    assert "203" in save.call_args.args[0]["processed_ids"]


@pytest.mark.asyncio
async def test_replacement_that_cannot_find_its_slide_is_appended():
    msgs = [_message(202, 2, "hush", "old"), _message(203, 2, "hush", "new")]
    marker = MagicMock(id=100, content="GUESS CHAT Test")
    with patch("weekly_slides_bot.load_state", return_value=dict(_STATE)), \
            patch("weekly_slides_bot.save_state"), \
            patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock())), \
            patch("weekly_slides_bot.read_deck_authors", return_value=[]), \
            patch("weekly_slides_bot.replace_resubmitted_slides",
                  side_effect=lambda svc, d, n, a, subs, c: ([{"author": "hush", "issue": "missing", "slide_number": "?", "slide_id": "", "message_id": "203"}], subs)), \
            patch("weekly_slides_bot.append_slides", return_value=[]) as append:
        await generate_slides(_client(marker, msgs))

    assert [s["body"] for s in append.call_args_list[0].args[3]] == ["new"]


@pytest.mark.asyncio
async def test_brand_new_submitters_are_still_appended():
    msgs = [_message(201, 1, "Amy", "first"), _message(202, 2, "hush", "old"), _message(204, 3, "Dan", "hi")]
    replace, append, _, _ = await _run(dict(_STATE), msgs)

    replace.assert_not_called()
    assert [s["author"] for s in append.call_args_list[0].args[3]] == ["Dan"]


@pytest.mark.asyncio
async def test_a_nickname_change_still_counts_as_the_same_person():
    msgs = [_message(301, 9, "OldNick", "first"), _message(302, 9, "NewNick", "second")]
    _, _, build, _ = await _run({}, msgs)

    subs = build.call_args_list[0].args[4]
    assert [s["body"] for s in subs] == ["second"]


@pytest.mark.asyncio
async def test_two_people_sharing_a_display_name_both_count():
    msgs = [_message(301, 9, "Sam", "first"), _message(302, 10, "Sam", "second")]
    _, _, build, _ = await _run({}, msgs)

    assert len(build.call_args_list[0].args[4]) == 2
