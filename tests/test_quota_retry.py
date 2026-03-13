"""Tests for copy_presentation_with_quota_retry backoff behaviour."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

# Provide required env vars before importing the module
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

from weekly_slides_bot import StorageQuotaExceededError, copy_presentation_with_quota_retry


class TestCopyPresentationWithQuotaRetry:
    """copy_presentation_with_quota_retry retries on quota errors with backoff."""

    @patch("weekly_slides_bot.copy_presentation", return_value="new-id")
    def test_returns_id_on_first_success(self, mock_copy):
        drive_svc = MagicMock()
        result = copy_presentation_with_quota_retry(drive_svc, "My Title")
        assert result == "new-id"
        mock_copy.assert_called_once_with(drive_svc, "My Title")

    @patch("weekly_slides_bot.time.sleep")
    @patch(
        "weekly_slides_bot.copy_presentation",
        side_effect=[StorageQuotaExceededError("quota"), "recovered-id"],
    )
    def test_retries_once_on_quota_error(self, mock_copy, mock_sleep):
        drive_svc = MagicMock()
        result = copy_presentation_with_quota_retry(drive_svc, "Title", max_retries=4)
        assert result == "recovered-id"
        assert mock_copy.call_count == 2
        mock_sleep.assert_called_once_with(2)  # 2^1 = 2 seconds

    @patch("weekly_slides_bot.time.sleep")
    @patch(
        "weekly_slides_bot.copy_presentation",
        side_effect=[
            StorageQuotaExceededError("quota"),
            StorageQuotaExceededError("quota"),
            "recovered-id",
        ],
    )
    def test_retries_multiple_times_with_exponential_backoff(self, mock_copy, mock_sleep):
        drive_svc = MagicMock()
        result = copy_presentation_with_quota_retry(drive_svc, "Title", max_retries=4)
        assert result == "recovered-id"
        assert mock_copy.call_count == 3
        waits = [c.args[0] for c in mock_sleep.call_args_list]
        assert waits == [2, 4]  # 2^1, 2^2

    @patch("weekly_slides_bot.time.sleep")
    @patch(
        "weekly_slides_bot.copy_presentation",
        side_effect=StorageQuotaExceededError("quota"),
    )
    def test_raises_after_max_retries_exhausted(self, mock_copy, mock_sleep):
        drive_svc = MagicMock()
        with pytest.raises(StorageQuotaExceededError):
            copy_presentation_with_quota_retry(drive_svc, "Title", max_retries=2)
        assert mock_copy.call_count == 3  # 1 initial + 2 retries
        assert mock_sleep.call_count == 2

    @patch("weekly_slides_bot.time.sleep")
    @patch(
        "weekly_slides_bot.copy_presentation",
        side_effect=StorageQuotaExceededError("quota"),
    )
    def test_backoff_sequence_for_max_retries_4(self, mock_copy, mock_sleep):
        drive_svc = MagicMock()
        with pytest.raises(StorageQuotaExceededError):
            copy_presentation_with_quota_retry(drive_svc, "Title", max_retries=4)
        waits = [c.args[0] for c in mock_sleep.call_args_list]
        assert waits == [2, 4, 8, 16]  # 2^1 through 2^4

    @patch("weekly_slides_bot.time.sleep")
    @patch(
        "weekly_slides_bot.copy_presentation",
        side_effect=[StorageQuotaExceededError("quota"), RuntimeError("other error")],
    )
    def test_non_quota_errors_propagate_immediately(self, mock_copy, mock_sleep):
        drive_svc = MagicMock()
        with pytest.raises(RuntimeError, match="other error"):
            copy_presentation_with_quota_retry(drive_svc, "Title", max_retries=4)
        assert mock_copy.call_count == 2
        mock_sleep.assert_called_once()  # slept after first (quota) failure only
