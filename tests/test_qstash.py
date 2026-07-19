"""Tests for QStash service."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.qstash_service import QStashPublishError, enqueue_job_processing


class TestQStashService:
    """Tests for QStash enqueue behavior."""

    @pytest.mark.asyncio
    @patch("qstash.QStash")
    @patch("app.services.qstash_service.get_settings")
    async def test_uses_configured_us_endpoint(self, mock_settings, MockQStash):
        """QStash client should be initialized with the configured base_url."""
        settings = MagicMock()
        settings.is_development = False
        settings.qstash_token = "test-token"
        settings.qstash_url = "https://qstash-us-east-1.upstash.io"
        settings.public_api_url = "https://example.com"
        mock_settings.return_value = settings

        mock_client = MagicMock()
        mock_client.message.publish_json.return_value = MagicMock(message_id="msg-123")
        MockQStash.return_value = mock_client

        job_id = uuid4()
        await enqueue_job_processing(job_id)

        MockQStash.assert_called_once_with(
            token="test-token",
            base_url="https://qstash-us-east-1.upstash.io",
        )

    @pytest.mark.asyncio
    @patch("qstash.QStash")
    @patch("app.services.qstash_service.get_settings")
    async def test_publish_success_returns_message_id(self, mock_settings, MockQStash):
        """Successful publish should return the message ID."""
        settings = MagicMock()
        settings.is_development = False
        settings.qstash_token = "test-token"
        settings.qstash_url = "https://qstash-us-east-1.upstash.io"
        settings.public_api_url = "https://example.com"
        mock_settings.return_value = settings

        mock_client = MagicMock()
        mock_client.message.publish_json.return_value = MagicMock(message_id="msg-456")
        MockQStash.return_value = mock_client

        job_id = uuid4()
        result = await enqueue_job_processing(job_id)

        assert result == "msg-456"
        call_kwargs = mock_client.message.publish_json.call_args[1]
        assert f"/v1/internal/jobs/{job_id}/process" in call_kwargs["url"]

    @pytest.mark.asyncio
    @patch("app.services.qstash_service._mark_job_failed")
    @patch("qstash.QStash")
    @patch("app.services.qstash_service.get_settings")
    async def test_publish_failure_marks_job_failed(self, mock_settings, MockQStash, mock_mark_failed):
        """Failed publish should mark the job as failed and raise QStashPublishError."""
        settings = MagicMock()
        settings.is_development = False
        settings.qstash_token = "test-token"
        settings.qstash_url = "https://qstash-us-east-1.upstash.io"
        settings.public_api_url = "https://example.com"
        mock_settings.return_value = settings
        mock_mark_failed.return_value = None

        mock_client = MagicMock()
        mock_client.message.publish_json.side_effect = RuntimeError("user not found in this region")
        MockQStash.return_value = mock_client

        job_id = uuid4()

        with pytest.raises(QStashPublishError):
            await enqueue_job_processing(job_id)

        mock_mark_failed.assert_called_once()
        assert mock_mark_failed.call_args[0][0] == job_id

    @pytest.mark.asyncio
    @patch("app.services.qstash_service.get_settings")
    async def test_development_mode_skips_qstash(self, mock_settings):
        """In development mode, QStash should not be called."""
        settings = MagicMock()
        settings.is_development = True
        mock_settings.return_value = settings

        result = await enqueue_job_processing(uuid4())
        assert result is None
