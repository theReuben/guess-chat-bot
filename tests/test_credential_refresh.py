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

_FAKE_SA_DATA = json.dumps({
    "type": "service_account",
    "project_id": "fake-project",
    "private_key_id": "key-id",
    "private_key": (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA2a2rwplBQLFwEvBWwgamGEB0wEBuqFLn23SPbehh6KVETbmH\n"
        "jGQXKMOlCMCDm0JFBGE1Xh6kMjBkMGTbYPuOSe8X2VjkFa7FM5LjKHCVTGGqO3o\n"
        "UJkgOXRniaZMFbMHYN8AQMOdp6mmCo9GQ5jgAIT7m1FnhRzFKf9PvpJp3m2Dlh+M\n"
        "N5dG2RxdPQwJr42pPBULGqLGbEUfMEJFOqFh+CJwNPlg3lUMiSM+fSPKNMRd0Rlv\n"
        "N0fiAdE3cBm7gpfB2Bx3rJRbfQgHBFlmJRsMn2mmFxJMR3GM5a0FqKSm/HQRsfix\n"
        "Q8g0TuVrMKamppMGFiS+dEJkYrWjnulYJmBM3QIDAQABAoIBAC5RgZ+hBx7xHNaM\n"
        "pPgwGMnCd6FPqkFt0X3J2mCk4RswEaKkjXH4nMpjMHFgEqMBoDkfnW6JKpUVjkjH\n"
        "-----END RSA PRIVATE KEY-----\n"
    ),
    "client_email": "bot@fake-project.iam.gserviceaccount.com",
    "client_id": "123456789",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
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


class TestGetGoogleServicesServiceAccount:
    """get_google_services supports service account credentials."""

    @patch("weekly_slides_bot.build")
    @patch("builtins.open", mock_open(read_data=_FAKE_SA_DATA))
    def test_service_account_credentials_used(self, mock_build):
        mock_build.return_value = MagicMock()
        mock_creds = MagicMock()
        with patch(
            "weekly_slides_bot.ServiceAccountCredentials.from_service_account_info",
            return_value=mock_creds,
        ):
            slides, drive = get_google_services()
        assert mock_build.call_count == 2
        # The credentials passed to build() should be the service account ones
        creds_used = mock_build.call_args_list[0].kwargs["credentials"]
        assert creds_used is mock_creds

    @patch("weekly_slides_bot.build")
    @patch("builtins.open", mock_open(read_data=_FAKE_SA_DATA))
    def test_service_account_scopes_passed(self, mock_build):
        mock_build.return_value = MagicMock()
        mock_creds = MagicMock()
        with patch(
            "weekly_slides_bot.ServiceAccountCredentials.from_service_account_info",
            return_value=mock_creds,
        ) as mock_from_sa:
            get_google_services()
        _, kwargs = mock_from_sa.call_args
        assert set(kwargs["scopes"]) == {
            "https://www.googleapis.com/auth/presentations",
            "https://www.googleapis.com/auth/drive",
        }

    @patch("weekly_slides_bot.build")
    @patch("builtins.open", mock_open(read_data=_FAKE_SA_DATA))
    def test_service_account_refresh_error_propagates(self, _mock_build):
        mock_creds = MagicMock()
        mock_creds.refresh.side_effect = RefreshError("transport error")
        with patch(
            "weekly_slides_bot.ServiceAccountCredentials.from_service_account_info",
            return_value=mock_creds,
        ):
            with pytest.raises(RefreshError):
                get_google_services()
