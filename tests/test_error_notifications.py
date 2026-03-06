"""Tests for error notification flow in generate_slides."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Provide required env vars before importing the module
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("DISCORD_CHANNEL_ID", "1")
os.environ.setdefault("DISCORD_RESULTS_CHANNEL_ID", "2")
os.environ.setdefault("TEMPLATE_DECK_ID", "tpl")

from weekly_slides_bot import build_deck, append_slides, generate_slides, slide_url, discord_message_url, format_error_message, presentation_url, publish_presentation


class _ClientHelper:
    """Shared helper for building mock Discord clients."""

    @staticmethod
    def make_client(marker_msg, sub_msg):
        call_count = 0

        async def history_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield marker_msg
            else:
                yield sub_msg

        mock_channel = MagicMock()
        mock_channel.history = history_side_effect

        mock_results_channel = MagicMock()
        mock_results_channel.send = AsyncMock()

        mock_client = MagicMock()
        mock_client.user = MagicMock(id=marker_msg.author.id)
        mock_client.get_channel.side_effect = lambda cid: (
            mock_channel if cid == 1 else mock_results_channel
        )
        return mock_client, mock_results_channel


class TestBuildDeckReturnsErrors:
    """build_deck must return error dicts when image processing fails."""

    @patch("weekly_slides_bot.upload_image_to_drive", return_value=None)
    def test_returns_error_on_failed_upload(self, _upload):
        """Failing to upload an image should produce an error dict."""
        slides_svc = MagicMock()
        slides_svc.presentations().get().execute.return_value = {
            "slides": [
                {"objectId": "title", "pageElements": []},
                {
                    "objectId": "tpl",
                    "pageElements": [
                        {
                            "objectId": "a",
                            "shape": {
                                "shapeType": "TEXT_BOX",
                                "text": {"textElements": [{"textRun": {"content": "{{AUTHOR}}"}}]},
                            },
                            "size": {"width": {"magnitude": 400 * 12700}, "height": {"magnitude": 55 * 12700}},
                            "transform": {"translateX": 0, "translateY": 0},
                        }
                    ],
                },
                {
                    "objectId": "new1",
                    "pageElements": [
                        {
                            "objectId": "body1",
                            "shape": {"shapeType": "TEXT_BOX"},
                            "size": {"width": {"magnitude": 400 * 12700}, "height": {"magnitude": 314 * 12700}},
                            "transform": {"translateX": 0, "translateY": 55 * 12700},
                        }
                    ],
                },
                {"objectId": "end", "pageElements": []},
            ]
        }
        slides_svc.presentations().batchUpdate().execute.return_value = {
            "replies": [{"duplicateObject": {"objectId": "new1"}}]
        }

        submissions = [{"id": "1", "author": "Alice", "body": "answer", "images": ["https://cdn/img.png"]}]
        errors = build_deck(slides_svc, MagicMock(), "pres", "Topic", submissions, named=True, image_cache={})

        assert len(errors) == 1
        assert errors[0]["author"] == "Alice"
        assert "image" in errors[0]["issue"].lower()
        assert "slide_number" in errors[0]
        assert "slide_id" in errors[0]
        assert errors[0]["message_id"] == "1"

    def test_returns_empty_when_no_issues(self):
        """No errors should be returned when everything succeeds."""
        slides_svc = MagicMock()
        slides_svc.presentations().get().execute.return_value = {
            "slides": [
                {"objectId": "title", "pageElements": []},
                {
                    "objectId": "tpl",
                    "pageElements": [
                        {
                            "objectId": "a",
                            "shape": {
                                "shapeType": "TEXT_BOX",
                                "text": {"textElements": [{"textRun": {"content": "{{AUTHOR}}"}}]},
                            },
                            "size": {"width": {"magnitude": 400 * 12700}, "height": {"magnitude": 55 * 12700}},
                            "transform": {"translateX": 0, "translateY": 0},
                        }
                    ],
                },
                {
                    "objectId": "new1",
                    "pageElements": [
                        {
                            "objectId": "body1",
                            "shape": {
                                "shapeType": "TEXT_BOX",
                                "text": {"textElements": [{"textRun": {"content": "answer"}}]},
                            },
                            "size": {"width": {"magnitude": 400 * 12700}, "height": {"magnitude": 314 * 12700}},
                            "transform": {"translateX": 0, "translateY": 55 * 12700},
                        }
                    ],
                },
                {"objectId": "end", "pageElements": []},
            ]
        }
        slides_svc.presentations().batchUpdate().execute.return_value = {
            "replies": [{"duplicateObject": {"objectId": "new1"}}]
        }

        submissions = [{"id": "1", "author": "Bob", "body": "answer", "images": []}]
        errors = build_deck(slides_svc, MagicMock(), "pres", "Topic", submissions, named=True, image_cache={})
        assert errors == []


class TestAppendSlidesReturnsErrors:
    """append_slides must return error dicts when image processing fails."""

    @patch("weekly_slides_bot.upload_image_to_drive", return_value=None)
    def test_returns_error_on_failed_upload(self, _upload):
        """Failing to upload an image in append flow should produce an error dict."""
        slides_svc = MagicMock()

        initial_pres = {
            "slides": [
                {"objectId": "title", "pageElements": []},
                {
                    "objectId": "sub1",
                    "pageElements": [
                        {
                            "objectId": "te",
                            "shape": {
                                "shapeType": "TEXT_BOX",
                                "text": {"textElements": [{"textRun": {"content": "old"}}]},
                            },
                            "size": {"width": {"magnitude": 400 * 12700}, "height": {"magnitude": 314 * 12700}},
                            "transform": {"translateX": 0, "translateY": 55 * 12700},
                        }
                    ],
                },
                {"objectId": "end", "pageElements": []},
            ]
        }
        with_new = {
            "slides": [
                {"objectId": "title", "pageElements": []},
                {"objectId": "sub1", "pageElements": []},
                {
                    "objectId": "new1",
                    "pageElements": [
                        {
                            "objectId": "te2",
                            "shape": {
                                "shapeType": "TEXT_BOX",
                                "text": {"textElements": [{"textRun": {"content": "old"}}]},
                            },
                            "size": {"width": {"magnitude": 400 * 12700}, "height": {"magnitude": 314 * 12700}},
                            "transform": {"translateX": 0, "translateY": 55 * 12700},
                        }
                    ],
                },
                {"objectId": "end", "pageElements": []},
            ]
        }

        get_count = {"n": 0}

        def get_se(*a, **kw):
            r = MagicMock()
            get_count["n"] += 1
            r.execute.return_value = initial_pres if get_count["n"] == 1 else with_new
            return r

        slides_svc.presentations().get.side_effect = get_se
        slides_svc.presentations().batchUpdate().execute.return_value = {
            "replies": [{"duplicateObject": {"objectId": "new1"}}]
        }

        submissions = [{"id": "1", "author": "Carol", "body": "text", "images": ["https://cdn/img.png"]}]
        errors = append_slides(slides_svc, MagicMock(), "pres", submissions, named=True, image_cache={})

        assert len(errors) == 1
        assert errors[0]["author"] == "Carol"
        assert "slide_number" in errors[0]
        assert "slide_id" in errors[0]
        assert errors[0]["message_id"] == "1"


class TestGenerateSlidesSendsErrors:
    """generate_slides must send error notifications to the results channel."""

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck", return_value=[{
        "author": "Dave",
        "issue": "Image upload failed",
        "slide_number": 2,
        "slide_id": "slide_abc",
        "message_id": "200",
    }])
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_error_sent_to_results_channel(
        self, _load, _gcs, _copy, _share, _build, _save
    ):
        """When build_deck returns errors, they must be sent to the results channel."""
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test"

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "Dave"
        sub_msg.guild = MagicMock()
        sub_msg.guild.id = 12345
        sub_msg.guild.get_member.return_value = MagicMock(display_name="Dave")

        mock_client, mock_results_channel = _ClientHelper.make_client(marker_msg, sub_msg)
        await generate_slides(mock_client)

        # Results message + 1 error notification = exactly 2 send calls
        send_calls = mock_results_channel.send.call_args_list
        assert len(send_calls) == 2
        error_msg = send_calls[1].args[0]
        assert "Dave" in error_msg
        assert "Image upload failed" in error_msg
        assert "slide 2" in error_msg
        assert "slide_abc" in error_msg
        assert "message" in error_msg.lower()
        assert "200" in error_msg

    @pytest.mark.asyncio
    @patch("weekly_slides_bot.save_state")
    @patch("weekly_slides_bot.build_deck", return_value=[])
    @patch("weekly_slides_bot.share_presentation")
    @patch("weekly_slides_bot.copy_presentation", return_value="pres_id")
    @patch("weekly_slides_bot.get_google_services", return_value=(MagicMock(), MagicMock()))
    @patch("weekly_slides_bot.load_state", return_value={})
    async def test_no_error_messages_when_no_errors(
        self, _load, _gcs, _copy, _share, _build, _save
    ):
        """When build_deck returns no errors, only the results message is sent."""
        marker_msg = MagicMock()
        marker_msg.id = 100
        marker_msg.content = "GUESS CHAT Test"

        sub_msg = MagicMock()
        sub_msg.id = 200
        sub_msg.content = "SUBMISSION answer"
        sub_msg.attachments = []
        sub_msg.author = MagicMock()
        sub_msg.author.id = 999
        sub_msg.author.display_name = "User"
        sub_msg.guild = MagicMock()
        sub_msg.guild.get_member.return_value = MagicMock(display_name="User")

        mock_client, mock_results_channel = _ClientHelper.make_client(marker_msg, sub_msg)
        await generate_slides(mock_client)

        # Only 1 send call (the results message)
        assert mock_results_channel.send.call_count == 1


class TestURLHelpers:
    """Tests for slide_url, discord_message_url, and presentation_url helpers."""

    def test_slide_url_format(self):
        url = slide_url("pres123", "slide_abc")
        assert url == "https://docs.google.com/presentation/d/pres123/edit#slide=id.slide_abc"

    def test_discord_message_url_format(self):
        url = discord_message_url(111, 222, "333")
        assert url == "https://discord.com/channels/111/222/333"

    def test_presentation_url_uses_pub_not_edit(self):
        """presentation_url must use /pub (view-only) rather than /edit."""
        url = presentation_url("pres456")
        assert "/pub" in url
        assert "/edit" not in url

    def test_presentation_url_format(self):
        url = presentation_url("pres456")
        assert url == "https://docs.google.com/presentation/d/pres456/pub?usp=sharing"


class TestPublishPresentation:
    """Unit tests for publish_presentation helper."""

    def test_publishes_head_revision(self):
        drive_svc = MagicMock()
        drive_svc.revisions().list().execute.return_value = {
            "revisions": [{"id": "1"}, {"id": "2"}]
        }
        publish_presentation(drive_svc, "file_abc")
        drive_svc.revisions().update.assert_called_once_with(
            fileId="file_abc",
            revisionId="2",
            body={"published": True, "publishAuto": True},
            fields="published",
        )
        drive_svc.revisions().update().execute.assert_called_once()

    def test_skips_when_no_revisions(self, capsys):
        drive_svc = MagicMock()
        drive_svc.revisions().list().execute.return_value = {"revisions": []}
        # Should not raise and should warn
        publish_presentation(drive_svc, "file_empty")
        drive_svc.revisions().update.assert_not_called()
        captured = capsys.readouterr()
        assert "[warn]" in captured.out


class TestFormatErrorMessage:
    """Tests for the format_error_message helper."""

    def test_multiline_output(self):
        err = {"author": "Alice", "issue": "Upload failed", "slide_number": 3, "slide_id": "s3", "message_id": "99"}
        result = format_error_message(err, "pres1", guild_id=10, channel_id=20)
        lines = result.split("\n")
        assert len(lines) == 3

    def test_contains_author_and_issue(self):
        err = {"author": "Bob", "issue": "Bad image", "slide_number": 2, "slide_id": "s2", "message_id": "50"}
        result = format_error_message(err, "pres1", guild_id=10, channel_id=20)
        assert "Bob" in result
        assert "Bad image" in result

    def test_contains_slide_link(self):
        err = {"author": "Eve", "issue": "Oops", "slide_number": 4, "slide_id": "s4", "message_id": "60"}
        result = format_error_message(err, "pres1", guild_id=10, channel_id=20)
        assert "slide 4" in result
        assert "s4" in result

    def test_contains_message_link_when_guild_present(self):
        err = {"author": "Eve", "issue": "Oops", "slide_number": 1, "slide_id": "s1", "message_id": "70"}
        result = format_error_message(err, "pres1", guild_id=10, channel_id=20)
        assert "message" in result.lower()
        assert "70" in result

    def test_no_message_link_without_guild(self):
        err = {"author": "Eve", "issue": "Oops", "slide_number": 1, "slide_id": "s1", "message_id": "70"}
        result = format_error_message(err, "pres1", guild_id=None, channel_id=20)
        assert "discord.com" not in result

    def test_no_message_link_without_message_id(self):
        err = {"author": "Eve", "issue": "Oops", "slide_number": 1, "slide_id": "s1", "message_id": ""}
        result = format_error_message(err, "pres1", guild_id=10, channel_id=20)
        assert "discord.com" not in result
