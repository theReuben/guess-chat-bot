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


class TestGetGoogleServicesUnrecognisedFormat:
    """get_google_services rejects unrecognised credential file formats."""

    def test_oauth_client_config_raises_value_error(self):
        """An OAuth client-config JSON (with 'installed' key) is rejected."""
        bad_data = json.dumps({"installed": {"client_id": "x", "client_secret": "y"}})
        with patch("builtins.open", mock_open(read_data=bad_data)):
            with pytest.raises(ValueError, match="Unrecognised credential file format"):
                get_google_services()

    def test_oauth_client_config_hint_mentions_installed(self):
        bad_data = json.dumps({"installed": {"client_id": "x"}})
        with patch("builtins.open", mock_open(read_data=bad_data)):
            with pytest.raises(ValueError) as exc_info:
                get_google_services()
        assert "installed" in str(exc_info.value)

    def test_web_client_config_raises_value_error(self):
        bad_data = json.dumps({"web": {"client_id": "x", "client_secret": "y"}})
        with patch("builtins.open", mock_open(read_data=bad_data)):
            with pytest.raises(ValueError, match="Unrecognised credential file format"):
                get_google_services()

    def test_empty_json_raises_value_error(self):
        with patch("builtins.open", mock_open(read_data="{}")):
            with pytest.raises(ValueError, match="Unrecognised credential file format"):
                get_google_services()

    def test_unknown_type_raises_value_error(self):
        bad_data = json.dumps({"type": "something_else"})
        with patch("builtins.open", mock_open(read_data=bad_data)):
            with pytest.raises(ValueError, match="Unrecognised credential file format"):
                get_google_services()

    def test_service_account_type_raises_value_error(self):
        """Service account credentials are no longer supported."""
        bad_data = json.dumps({"type": "service_account", "project_id": "x"})
        with patch("builtins.open", mock_open(read_data=bad_data)):
            with pytest.raises(ValueError, match="Unrecognised credential file format"):
                get_google_services()


class TestGetGoogleServicesAuthorizedUser:
    """get_google_services supports authorized_user type credentials."""

    _FAKE_AUTH_USER_DATA = json.dumps({
        "type": "authorized_user",
        "client_id": "fake-client-id",
        "client_secret": "fake-client-secret",
        "refresh_token": "fake-refresh-token",
    })

    @patch("weekly_slides_bot.build")
    @patch("builtins.open", mock_open(read_data=_FAKE_AUTH_USER_DATA))
    def test_authorized_user_credentials_used(self, mock_build):
        mock_build.return_value = MagicMock()
        with patch("google.oauth2.credentials.Credentials.refresh"):
            slides, drive = get_google_services()
        assert mock_build.call_count == 2
