"""Tests for execute_with_retry exponential backoff on Google API errors."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

# Provide required env vars before importing the module
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from weekly_slides_bot import StorageQuotaExceededError, execute_with_retry


def _make_http_error(status: int, body: str = "") -> HttpError:
    """Create an HttpError with the given status code."""
    resp = MagicMock()
    resp.status = status
    return HttpError(resp, body.encode())


class TestExecuteWithRetrySuccess:
    """Happy-path: execute succeeds on first attempt."""

    def test_returns_result_on_success(self):
        request = MagicMock()
        request.execute.return_value = {"id": "abc"}
        assert execute_with_retry(request) == {"id": "abc"}
        request.execute.assert_called_once()

    def test_passes_through_non_retryable_error(self):
        request = MagicMock()
        request.execute.side_effect = _make_http_error(404, "Not Found")
        with pytest.raises(HttpError):
            execute_with_retry(request)
        request.execute.assert_called_once()


class TestExecuteWithRetryBackoff:
    """Retry behaviour on transient errors."""

    @patch("weekly_slides_bot.time.sleep")
    def test_retries_on_429(self, mock_sleep):
        request = MagicMock()
        request.execute.side_effect = [
            _make_http_error(429, "Rate Limit Exceeded"),
            {"ok": True},
        ]
        result = execute_with_retry(request)
        assert result == {"ok": True}
        assert request.execute.call_count == 2
        mock_sleep.assert_called_once()

    @patch("weekly_slides_bot.time.sleep")
    def test_retries_on_500(self, mock_sleep):
        request = MagicMock()
        request.execute.side_effect = [
            _make_http_error(500, "Internal Server Error"),
            {"ok": True},
        ]
        result = execute_with_retry(request)
        assert result == {"ok": True}
        assert request.execute.call_count == 2

    @patch("weekly_slides_bot.time.sleep")
    def test_retries_on_503(self, mock_sleep):
        request = MagicMock()
        request.execute.side_effect = [
            _make_http_error(503, "Service Unavailable"),
            {"ok": True},
        ]
        result = execute_with_retry(request)
        assert result == {"ok": True}
        assert request.execute.call_count == 2

    @patch("weekly_slides_bot.time.sleep")
    def test_retries_on_403_rate_limit(self, mock_sleep):
        request = MagicMock()
        request.execute.side_effect = [
            _make_http_error(403, "rateLimitExceeded"),
            {"ok": True},
        ]
        result = execute_with_retry(request)
        assert result == {"ok": True}
        assert request.execute.call_count == 2

    def test_does_not_retry_403_without_rate_limit(self):
        request = MagicMock()
        request.execute.side_effect = _make_http_error(403, "Forbidden")
        with pytest.raises(HttpError):
            execute_with_retry(request)
        request.execute.assert_called_once()

    @patch("weekly_slides_bot.time.sleep")
    def test_raises_after_max_retries(self, mock_sleep):
        request = MagicMock()
        request.execute.side_effect = _make_http_error(429, "Rate Limit")
        with pytest.raises(HttpError):
            execute_with_retry(request, max_retries=3)
        assert request.execute.call_count == 4  # 1 initial + 3 retries
        assert mock_sleep.call_count == 3

    @patch("weekly_slides_bot.time.sleep")
    def test_exponential_backoff_increases(self, mock_sleep):
        request = MagicMock()
        request.execute.side_effect = [
            _make_http_error(429, "Rate Limit"),
            _make_http_error(429, "Rate Limit"),
            _make_http_error(429, "Rate Limit"),
            {"ok": True},
        ]
        execute_with_retry(request, max_retries=5)
        assert mock_sleep.call_count == 3
        waits = [c.args[0] for c in mock_sleep.call_args_list]
        # Each wait should be 2^attempt + jitter (jitter is random.random() ∈ [0,1))
        assert 1.0 <= waits[0] < 2.0  # 2^0 + jitter
        assert 2.0 <= waits[1] < 3.0  # 2^1 + jitter
        assert 4.0 <= waits[2] < 5.0  # 2^2 + jitter


class TestExecuteWithRetryStorageQuota:
    """storageQuotaExceeded is raised as StorageQuotaExceededError (not retried)."""

    def test_raises_storage_quota_exceeded_error(self):
        request = MagicMock()
        request.execute.side_effect = _make_http_error(
            403, "storageQuotaExceeded"
        )
        with pytest.raises(StorageQuotaExceededError, match="storage quota exceeded"):
            execute_with_retry(request)
        request.execute.assert_called_once()

    def test_not_retried(self):
        request = MagicMock()
        request.execute.side_effect = _make_http_error(
            403,
            '{"error": {"errors": [{"reason": "storageQuotaExceeded"}]}}',
        )
        with pytest.raises(StorageQuotaExceededError):
            execute_with_retry(request, max_retries=3)
        request.execute.assert_called_once()


class TestExecuteWithRetryRefreshError:
    """RefreshError (expired/revoked token) is not retried."""

    def test_raises_immediately_on_refresh_error(self):
        request = MagicMock()
        request.execute.side_effect = RefreshError("invalid_grant: Token has been expired or revoked.")
        with pytest.raises(RefreshError):
            execute_with_retry(request)
        request.execute.assert_called_once()

    def test_prints_actionable_message(self, capsys):
        request = MagicMock()
        request.execute.side_effect = RefreshError("invalid_grant")
        with pytest.raises(RefreshError):
            execute_with_retry(request)
        captured = capsys.readouterr()
        assert "credential refresh failed" in captured.out
        assert "GOOGLE_OAUTH_TOKEN" in captured.out
