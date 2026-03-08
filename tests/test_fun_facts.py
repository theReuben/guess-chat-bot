"""Tests for the fun facts generation feature."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, call, patch

# Provide required env vars before importing the module
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

from weekly_slides_bot import generate_fun_facts


class TestGenerateFunFacts:
    """Unit tests for the generate_fun_facts helper."""

    @patch("weekly_slides_bot.GEMINI_API_KEY", None)
    def test_returns_empty_when_no_api_key(self):
        """Feature is disabled when GEMINI_API_KEY is not set."""
        result = generate_fun_facts("Topic", [{"body": "answer"}])
        assert result == ""

    @patch("weekly_slides_bot.GEMINI_API_KEY", "fake-key")
    def test_returns_empty_for_empty_submissions(self):
        """No submissions means nothing to generate."""
        result = generate_fun_facts("Topic", [])
        assert result == ""

    @patch("weekly_slides_bot.GEMINI_API_KEY", "fake-key")
    def test_returns_empty_for_submissions_without_body(self):
        """Submissions with empty bodies should be skipped."""
        result = generate_fun_facts("Topic", [{"body": ""}, {"body": ""}])
        assert result == ""

    @patch("weekly_slides_bot.GEMINI_API_KEY", "fake-key")
    @patch("weekly_slides_bot.requests.post")
    def test_successful_generation(self, mock_post):
        """Successful API call returns the generated text."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": "• Fact one\n• Fact two"}]}}
            ]
        }
        mock_post.return_value = mock_resp

        subs = [{"body": "cats"}, {"body": "dogs"}]
        result = generate_fun_facts("Pets", subs)

        assert result == "• Fact one\n• Fact two"
        mock_post.assert_called_once()
        # Verify the API key is included in the URL
        url_arg = mock_post.call_args[0][0]
        assert "key=fake-key" in url_arg
        # Verify the prompt contains the topic and submissions
        payload = mock_post.call_args[1]["json"]
        prompt_text = payload["contents"][0]["parts"][0]["text"]
        assert "Pets" in prompt_text
        assert "cats" in prompt_text
        assert "dogs" in prompt_text

    @patch("weekly_slides_bot.GEMINI_API_KEY", "fake-key")
    @patch("weekly_slides_bot.requests.post")
    def test_includes_conversation_in_prompt(self, mock_post):
        """Conversation messages are included in the prompt when provided."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": "• Fact"}]}}
            ]
        }
        mock_post.return_value = mock_resp

        subs = [{"body": "answer1"}]
        conv = ["I disagree with that!", "Great choice"]
        generate_fun_facts("Topic", subs, conversation_messages=conv)

        payload = mock_post.call_args[1]["json"]
        prompt_text = payload["contents"][0]["parts"][0]["text"]
        assert "I disagree with that!" in prompt_text
        assert "Great choice" in prompt_text

    @patch("weekly_slides_bot.GEMINI_API_KEY", "fake-key")
    @patch("weekly_slides_bot.requests.post")
    def test_no_conversation_section_when_empty(self, mock_post):
        """When no conversation messages, the conversation section is omitted."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": "• Fact"}]}}
            ]
        }
        mock_post.return_value = mock_resp

        generate_fun_facts("Topic", [{"body": "answer1"}], conversation_messages=[])

        payload = mock_post.call_args[1]["json"]
        prompt_text = payload["contents"][0]["parts"][0]["text"]
        assert "conversation" not in prompt_text.lower()

    @patch("weekly_slides_bot.GEMINI_API_KEY", "fake-key")
    @patch("weekly_slides_bot.requests.post", side_effect=Exception("Network error"))
    def test_api_error_returns_empty(self, _mock_post):
        """API failures are handled gracefully, returning an empty string."""
        result = generate_fun_facts("Topic", [{"body": "answer"}])
        assert result == ""

    @patch("weekly_slides_bot.GEMINI_API_KEY", "fake-key")
    @patch("weekly_slides_bot.requests.post")
    def test_prompt_is_anonymous(self, mock_post):
        """The prompt must not contain author names."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "candidates": [
                {"content": {"parts": [{"text": "• Fact"}]}}
            ]
        }
        mock_post.return_value = mock_resp

        subs = [{"body": "cats", "author": "SecretUser123"}]
        generate_fun_facts("Pets", subs)

        payload = mock_post.call_args[1]["json"]
        prompt_text = payload["contents"][0]["parts"][0]["text"]
        assert "SecretUser123" not in prompt_text
        assert "anonymous" in prompt_text.lower()


class TestBuildDeckFunFacts:
    """Verify that build_deck inserts {{FUNFACTS}} on the title slide."""

    @patch("weekly_slides_bot.execute_with_retry")
    def test_funfacts_placeholder_replaced(self, mock_exec):
        """build_deck should include a replaceAllText for {{FUNFACTS}}."""
        # Set up mock to return slide IDs and template slide
        mock_slides_svc = MagicMock()
        mock_drive_svc = MagicMock()

        # First call: _get_slide_ids (via execute_with_retry)
        # Second call: title slide batchUpdate
        # Third call: _find_template_slide_id (via execute_with_retry)
        # ... subsequent calls for each submission
        slide_data = {
            "slides": [
                {"objectId": "title_slide"},
                {
                    "objectId": "template_slide",
                    "pageElements": [
                        {
                            "shape": {
                                "text": {
                                    "textElements": [
                                        {"textRun": {"content": "{{AUTHOR}}"}}
                                    ]
                                }
                            }
                        }
                    ],
                },
                {"objectId": "end_slide"},
            ]
        }
        # _get_slide_ids returns slide IDs; _find_template_slide_id scans all slides
        mock_exec.side_effect = [
            slide_data,  # _get_slide_ids
            None,        # title slide batchUpdate (TOPIC + FUNFACTS)
            slide_data,  # _find_template_slide_id
            slide_data,  # _get_slide_ids (for template_index)
            None,        # deleteObject (template slide)
        ]

        from weekly_slides_bot import build_deck

        build_deck(
            mock_slides_svc,
            mock_drive_svc,
            "pres123",
            "Test Topic",
            [],  # no submissions for simplicity
            named=True,
            image_cache={},
            fun_facts="• Fun fact one\n• Fun fact two",
        )

        # The second call should be the title slide batchUpdate
        title_batch_call = mock_exec.call_args_list[1]
        # Extract the requests from the batchUpdate body
        batch_body = title_batch_call[0][0]
        # The mock wraps the request object; check via the presentations().batchUpdate call
        # We need to check the keyword arguments used to build the batchUpdate
        calls_to_batch = mock_slides_svc.presentations().batchUpdate.call_args_list
        assert len(calls_to_batch) >= 1
        first_batch = calls_to_batch[0]
        body = first_batch[1]["body"]
        requests_list = body["requests"]

        # Should have two replaceAllText: one for TOPIC and one for FUNFACTS
        replace_texts = [
            r["replaceAllText"]["containsText"]["text"]
            for r in requests_list
            if "replaceAllText" in r
        ]
        assert "{{TOPIC}}" in replace_texts
        assert "{{FUNFACTS}}" in replace_texts

        # Verify the FUNFACTS replacement text
        for r in requests_list:
            if "replaceAllText" in r and r["replaceAllText"]["containsText"]["text"] == "{{FUNFACTS}}":
                assert r["replaceAllText"]["replaceText"] == "• Fun fact one\n• Fun fact two"

    @patch("weekly_slides_bot.execute_with_retry")
    def test_funfacts_cleared_when_empty(self, mock_exec):
        """When fun_facts is empty, {{FUNFACTS}} should be replaced with empty string."""
        mock_slides_svc = MagicMock()
        mock_drive_svc = MagicMock()

        slide_data = {
            "slides": [
                {"objectId": "title_slide"},
                {
                    "objectId": "template_slide",
                    "pageElements": [
                        {
                            "shape": {
                                "text": {
                                    "textElements": [
                                        {"textRun": {"content": "{{AUTHOR}}"}}
                                    ]
                                }
                            }
                        }
                    ],
                },
                {"objectId": "end_slide"},
            ]
        }
        mock_exec.side_effect = [
            slide_data,  # _get_slide_ids
            None,        # title slide batchUpdate
            slide_data,  # _find_template_slide_id
            slide_data,  # _get_slide_ids (for template_index)
            None,        # deleteObject
        ]

        from weekly_slides_bot import build_deck

        build_deck(
            mock_slides_svc,
            mock_drive_svc,
            "pres123",
            "Test Topic",
            [],
            named=True,
            image_cache={},
            fun_facts="",
        )

        calls_to_batch = mock_slides_svc.presentations().batchUpdate.call_args_list
        first_batch = calls_to_batch[0]
        body = first_batch[1]["body"]
        requests_list = body["requests"]

        for r in requests_list:
            if "replaceAllText" in r and r["replaceAllText"]["containsText"]["text"] == "{{FUNFACTS}}":
                assert r["replaceAllText"]["replaceText"] == ""
