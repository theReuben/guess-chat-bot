"""Tests for URL hyperlink support in slides."""

from __future__ import annotations

import os

# Provide required env vars before importing the module
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

from weekly_slides_bot import _hyperlink_requests


class TestHyperlinkRequestsEmpty:
    """No requests when text has no URLs."""

    def test_empty_text(self):
        assert _hyperlink_requests("elem1", "") == []

    def test_no_urls(self):
        assert _hyperlink_requests("elem1", "Just some plain text") == []


class TestHyperlinkRequestsSingleURL:
    """Single URL produces one updateTextStyle request."""

    def test_single_url(self):
        text = "Check https://example.com for info"
        reqs = _hyperlink_requests("elem1", text)
        assert len(reqs) == 1

    def test_request_structure(self):
        text = "Visit https://example.com/page today"
        reqs = _hyperlink_requests("elem1", text)
        req = reqs[0]["updateTextStyle"]
        assert req["objectId"] == "elem1"
        assert req["style"]["link"]["url"] == "https://example.com/page"
        assert req["fields"] == "link"

    def test_text_range_matches_url_position(self):
        text = "Visit https://example.com today"
        reqs = _hyperlink_requests("elem1", text)
        req = reqs[0]["updateTextStyle"]
        start = req["textRange"]["startIndex"]
        end = req["textRange"]["endIndex"]
        assert text[start:end] == "https://example.com"

    def test_text_range_type_is_fixed(self):
        text = "https://example.com"
        reqs = _hyperlink_requests("elem1", text)
        assert reqs[0]["updateTextStyle"]["textRange"]["type"] == "FIXED_RANGE"

    def test_http_url(self):
        text = "Go to http://example.com"
        reqs = _hyperlink_requests("elem1", text)
        assert len(reqs) == 1
        assert reqs[0]["updateTextStyle"]["style"]["link"]["url"] == "http://example.com"


class TestHyperlinkRequestsMultipleURLs:
    """Multiple URLs produce one request each."""

    def test_two_urls(self):
        text = "See https://a.com and https://b.com"
        reqs = _hyperlink_requests("elem1", text)
        assert len(reqs) == 2

    def test_urls_in_correct_order(self):
        text = "First https://first.com then https://second.com"
        reqs = _hyperlink_requests("elem1", text)
        urls = [r["updateTextStyle"]["style"]["link"]["url"] for r in reqs]
        assert urls == ["https://first.com", "https://second.com"]

    def test_ranges_do_not_overlap(self):
        text = "A https://a.com B https://b.com"
        reqs = _hyperlink_requests("elem1", text)
        end_first = reqs[0]["updateTextStyle"]["textRange"]["endIndex"]
        start_second = reqs[1]["updateTextStyle"]["textRange"]["startIndex"]
        assert end_first <= start_second


class TestHyperlinkRequestsComplexURLs:
    """URLs with paths, query params, and fragments."""

    def test_url_with_path(self):
        text = "Link: https://example.com/path/to/page"
        reqs = _hyperlink_requests("elem1", text)
        assert reqs[0]["updateTextStyle"]["style"]["link"]["url"] == "https://example.com/path/to/page"

    def test_url_with_query(self):
        text = "Link: https://example.com?q=1&b=2"
        reqs = _hyperlink_requests("elem1", text)
        assert reqs[0]["updateTextStyle"]["style"]["link"]["url"] == "https://example.com?q=1&b=2"

    def test_url_with_fragment(self):
        text = "Link: https://example.com#section"
        reqs = _hyperlink_requests("elem1", text)
        assert reqs[0]["updateTextStyle"]["style"]["link"]["url"] == "https://example.com#section"
