"""Tests for delete_drive_file and cleanup behaviour in generate_slides."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, call

# Provide required env vars before importing the module
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

from weekly_slides_bot import delete_drive_file


class TestDeleteDriveFile:
    """Unit tests for delete_drive_file helper."""

    def test_deletes_file_by_id(self):
        drive_svc = MagicMock()
        delete_drive_file(drive_svc, "file123")
        drive_svc.files().delete.assert_called_once_with(fileId="file123")
        drive_svc.files().delete().execute.assert_called_once()

    def test_handles_missing_file_gracefully(self):
        drive_svc = MagicMock()
        drive_svc.files().delete().execute.side_effect = Exception("404 Not Found")
        # Should not raise
        delete_drive_file(drive_svc, "missing_id")

    def test_handles_api_error_gracefully(self):
        drive_svc = MagicMock()
        drive_svc.files().delete().execute.side_effect = RuntimeError("API error")
        # Should not raise
        delete_drive_file(drive_svc, "error_id")
