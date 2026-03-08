"""Tests for automatic GitHub issue creation on bot errors."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Provide required env vars before importing the module
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

from weekly_slides_bot import create_github_issue, OneShotClient


# ---------------------------------------------------------------------------
# create_github_issue tests
# ---------------------------------------------------------------------------


class TestCreateGithubIssue:
    """Tests for the create_github_issue helper."""

    @patch("weekly_slides_bot.GITHUB_REPOSITORY", "owner/repo")
    @patch("weekly_slides_bot.GITHUB_TOKEN", None)
    def test_skips_when_token_missing(self, capsys):
        """No API call when GITHUB_TOKEN is not set."""
        create_github_issue(RuntimeError("boom"))
        assert "skipping issue creation" in capsys.readouterr().out.lower()

    @patch("weekly_slides_bot.GITHUB_REPOSITORY", None)
    @patch("weekly_slides_bot.GITHUB_TOKEN", "ghp_test")
    def test_skips_when_repo_missing(self, capsys):
        """No API call when GITHUB_REPOSITORY is not set."""
        create_github_issue(RuntimeError("boom"))
        assert "skipping issue creation" in capsys.readouterr().out.lower()

    @patch("weekly_slides_bot.requests.post")
    @patch("weekly_slides_bot.requests.get")
    @patch("weekly_slides_bot.GITHUB_REPOSITORY", "owner/repo")
    @patch("weekly_slides_bot.GITHUB_TOKEN", "ghp_test")
    def test_creates_issue_on_exception(self, mock_get, mock_post, capsys):
        """An issue is created via the GitHub API when an exception occurs."""
        # No duplicate found
        mock_get.return_value = MagicMock(ok=True, json=lambda: {"items": []})
        # Label creation + issue creation
        mock_post.return_value = MagicMock(ok=True, json=lambda: {"number": 42})

        exc = RuntimeError("Token has been expired or revoked.")
        try:
            raise exc
        except RuntimeError:
            create_github_issue(exc)

        # Should have called post twice: once for label, once for issue
        assert mock_post.call_count == 2
        issue_call = mock_post.call_args_list[1]
        assert "issues" in issue_call.args[0]
        payload = issue_call.kwargs["json"]
        assert "RuntimeError" in payload["title"]
        assert "bot-error" in payload["labels"]
        assert "Traceback" in payload["body"]
        assert "Created GitHub issue #42" in capsys.readouterr().out

    @patch("weekly_slides_bot.requests.post")
    @patch("weekly_slides_bot.requests.get")
    @patch("weekly_slides_bot.GITHUB_REPOSITORY", "owner/repo")
    @patch("weekly_slides_bot.GITHUB_TOKEN", "ghp_test")
    def test_skips_duplicate_issue(self, mock_get, mock_post, capsys):
        """No new issue is created if one with the same title is already open."""
        exc = ValueError("duplicate")
        title = f"Bot error: ValueError: {exc}"
        mock_get.return_value = MagicMock(
            ok=True,
            json=lambda: {"items": [{"title": title, "number": 7}]},
        )

        create_github_issue(exc)

        # post is never called for an issue (label creation also not reached)
        mock_post.assert_not_called()
        output = capsys.readouterr().out
        assert "Duplicate issue already open: #7" in output

    @patch("weekly_slides_bot.requests.post")
    @patch("weekly_slides_bot.requests.get")
    @patch("weekly_slides_bot.GITHUB_REPOSITORY", "owner/repo")
    @patch("weekly_slides_bot.GITHUB_TOKEN", "ghp_test")
    def test_handles_api_failure_gracefully(self, mock_get, mock_post, capsys):
        """API errors do not propagate — they are printed instead."""
        mock_get.return_value = MagicMock(ok=True, json=lambda: {"items": []})
        mock_post.side_effect = [
            MagicMock(ok=True),  # label
            MagicMock(ok=False, status_code=403, text="forbidden"),
        ]

        create_github_issue(RuntimeError("fail"))

        output = capsys.readouterr().out
        assert "Failed to create GitHub issue" in output

    @patch("weekly_slides_bot.requests.get")
    @patch("weekly_slides_bot.GITHUB_REPOSITORY", "owner/repo")
    @patch("weekly_slides_bot.GITHUB_TOKEN", "ghp_test")
    def test_handles_network_error_gracefully(self, mock_get, capsys):
        """Network errors during issue creation are caught and logged."""
        mock_get.side_effect = ConnectionError("no network")

        create_github_issue(RuntimeError("fail"))

        output = capsys.readouterr().out
        assert "Could not create GitHub issue" in output

    @patch("weekly_slides_bot.requests.post")
    @patch("weekly_slides_bot.requests.get")
    @patch("weekly_slides_bot.GITHUB_REPOSITORY", "owner/repo")
    @patch("weekly_slides_bot.GITHUB_TOKEN", "ghp_test")
    def test_title_truncated_when_too_long(self, mock_get, mock_post):
        """Issue titles longer than 256 chars are truncated."""
        mock_get.return_value = MagicMock(ok=True, json=lambda: {"items": []})
        mock_post.return_value = MagicMock(ok=True, json=lambda: {"number": 1})

        exc = RuntimeError("x" * 300)
        try:
            raise exc
        except RuntimeError:
            create_github_issue(exc)

        issue_call = mock_post.call_args_list[1]
        title = issue_call.kwargs["json"]["title"]
        assert len(title) <= 256
        assert title.endswith("...")

    @patch("weekly_slides_bot.requests.post")
    @patch("weekly_slides_bot.requests.get")
    @patch("weekly_slides_bot.BOT_MODE", "test_slides")
    @patch("weekly_slides_bot.GITHUB_REPOSITORY", "owner/repo")
    @patch("weekly_slides_bot.GITHUB_TOKEN", "ghp_test")
    def test_body_includes_bot_mode(self, mock_get, mock_post):
        """The issue body includes the current BOT_MODE."""
        mock_get.return_value = MagicMock(ok=True, json=lambda: {"items": []})
        mock_post.return_value = MagicMock(ok=True, json=lambda: {"number": 1})

        exc = RuntimeError("oops")
        try:
            raise exc
        except RuntimeError:
            create_github_issue(exc)

        issue_call = mock_post.call_args_list[1]
        body = issue_call.kwargs["json"]["body"]
        assert "test_slides" in body


# ---------------------------------------------------------------------------
# OneShotClient.on_ready exception handling tests
# ---------------------------------------------------------------------------


class TestOnReadyIssueCreation:
    """Tests that OneShotClient.on_ready creates GitHub issues on failure."""

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.create_github_issue")
    @patch("weekly_slides_bot.generate_slides", new_callable=AsyncMock, side_effect=RuntimeError("boom"))
    @patch("weekly_slides_bot.BOT_MODE", "slides")
    async def test_on_ready_creates_issue_on_exception(self, _gen, mock_create):
        """When generate_slides raises, create_github_issue is called."""
        client = MagicMock(spec=OneShotClient)
        client.close = AsyncMock()
        client.on_ready = OneShotClient.on_ready.__get__(client, OneShotClient)

        with pytest.raises(RuntimeError, match="boom"):
            await client.on_ready()

        mock_create.assert_called_once()
        exc_arg = mock_create.call_args[0][0]
        assert isinstance(exc_arg, RuntimeError)
        assert str(exc_arg) == "boom"

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.create_github_issue")
    @patch("weekly_slides_bot.generate_slides", new_callable=AsyncMock)
    @patch("weekly_slides_bot.BOT_MODE", "slides")
    async def test_on_ready_no_issue_on_success(self, _gen, mock_create):
        """When generate_slides succeeds, create_github_issue is NOT called."""
        client = MagicMock(spec=OneShotClient)
        client.close = AsyncMock()
        client.on_ready = OneShotClient.on_ready.__get__(client, OneShotClient)

        await client.on_ready()

        mock_create.assert_not_called()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.create_github_issue")
    @patch("weekly_slides_bot.generate_slides", new_callable=AsyncMock, side_effect=RuntimeError("boom"))
    @patch("weekly_slides_bot.BOT_MODE", "slides")
    async def test_on_ready_still_closes_on_exception(self, _gen, _create):
        """The client is closed even when an exception occurs."""
        client = MagicMock(spec=OneShotClient)
        client.close = AsyncMock()
        client.on_ready = OneShotClient.on_ready.__get__(client, OneShotClient)

        with pytest.raises(RuntimeError):
            await client.on_ready()

        client.close.assert_called_once()

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.create_github_issue")
    @patch("weekly_slides_bot.check_mod_and_announce", new_callable=AsyncMock, side_effect=ValueError("announce fail"))
    @patch("weekly_slides_bot.BOT_MODE", "announce")
    async def test_on_ready_creates_issue_for_announce_error(self, _announce, mock_create):
        """Issues are also created for errors in announce mode."""
        client = MagicMock(spec=OneShotClient)
        client.close = AsyncMock()
        client.on_ready = OneShotClient.on_ready.__get__(client, OneShotClient)

        with pytest.raises(ValueError, match="announce fail"):
            await client.on_ready()

        mock_create.assert_called_once()
        exc_arg = mock_create.call_args[0][0]
        assert isinstance(exc_arg, ValueError)
