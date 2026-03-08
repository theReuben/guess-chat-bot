"""Tests for early credential validation in get_google_services."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, mock_open, patch

import pytest

# Provide required env vars before importing the module
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

from google.auth.exceptions import RefreshError

from weekly_slides_bot import get_google_services

_FAKE_TOKEN_DATA = json.dumps({
    "client_id": "fake-client-id",
    "client_secret": "fake-client-secret",
    "refresh_token": "fake-refresh-token",
    "token_uri": "https://oauth2.googleapis.com/token",
})


class TestGetGoogleServicesRefreshError:
    """get_google_services fails fast when the refresh token is expired."""

    @patch("weekly_slides_bot.build")
    @patch("builtins.open", mock_open(read_data=_FAKE_TOKEN_DATA))
    def test_refresh_error_propagates(self, _mock_build):
        with patch(
            "google.oauth2.credentials.Credentials.refresh",
            side_effect=RefreshError("invalid_grant: Token has been expired or revoked."),
        ):
            with pytest.raises(RefreshError):
                get_google_services()

    @patch("weekly_slides_bot.build")
    @patch("builtins.open", mock_open(read_data=_FAKE_TOKEN_DATA))
    def test_prints_actionable_message_on_refresh_error(self, _mock_build, capsys):
        with patch(
            "google.oauth2.credentials.Credentials.refresh",
            side_effect=RefreshError("invalid_grant"),
        ):
            with pytest.raises(RefreshError):
                get_google_services()
        captured = capsys.readouterr()
        assert "expired or revoked" in captured.out
        assert "GOOGLE_OAUTH_TOKEN" in captured.out

    @patch("weekly_slides_bot.build")
    @patch("builtins.open", mock_open(read_data=_FAKE_TOKEN_DATA))
    def test_build_not_called_on_refresh_error(self, mock_build):
        with patch(
            "google.oauth2.credentials.Credentials.refresh",
            side_effect=RefreshError("invalid_grant"),
        ):
            with pytest.raises(RefreshError):
                get_google_services()
        mock_build.assert_not_called()

    @patch("weekly_slides_bot.build")
    @patch("builtins.open", mock_open(read_data=_FAKE_TOKEN_DATA))
    def test_succeeds_when_token_is_valid(self, mock_build):
        mock_build.return_value = MagicMock()
        with patch("google.oauth2.credentials.Credentials.refresh"):
            slides, drive = get_google_services()
        assert mock_build.call_count == 2
