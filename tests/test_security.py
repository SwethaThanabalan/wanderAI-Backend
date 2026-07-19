"""Tests for QStash signature verification."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException


class TestQStashSignatureVerification:
    """Tests for verify_qstash_signature."""

    @pytest.mark.asyncio
    @patch("app.core.security.get_settings")
    async def test_skipped_in_development(self, mock_settings):
        """Development mode should skip verification entirely."""
        from app.core.security import verify_qstash_signature

        settings = MagicMock()
        settings.is_development = True
        mock_settings.return_value = settings

        request = MagicMock()
        result = await verify_qstash_signature(request)
        assert result is True

    @pytest.mark.asyncio
    @patch("app.core.security.get_settings")
    async def test_missing_signature_returns_401(self, mock_settings):
        """Missing upstash-signature header should raise 401."""
        from app.core.security import verify_qstash_signature

        settings = MagicMock()
        settings.is_development = False
        settings.qstash_current_signing_key = "sig_test_key_123456789012"
        mock_settings.return_value = settings

        request = MagicMock()
        request.headers = {}

        with pytest.raises(HTTPException) as exc_info:
            await verify_qstash_signature(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch("qstash.Receiver")
    @patch("app.core.security.get_settings")
    async def test_uses_public_url_not_internal_render_url(self, mock_settings, MockReceiver):
        """Verification URL should use PUBLIC_API_URL + path, not the internal Render URL.

        On Render, the request arrives at an internal URL like
        http://10.0.0.1:8000/v1/internal/jobs/{id}/process
        but QStash signed against the public URL:
        https://wanderai-backend.onrender.com/v1/internal/jobs/{id}/process
        """
        from app.core.security import verify_qstash_signature

        settings = MagicMock()
        settings.is_development = False
        settings.qstash_current_signing_key = "sig_test_key_123456789012"
        settings.qstash_next_signing_key = "sig_next_key_123456789012"
        settings.public_api_url = "https://wanderai-backend.onrender.com"
        mock_settings.return_value = settings

        job_id = str(uuid4())

        # Simulate Render's internal request
        request = MagicMock()
        request.headers = {"upstash-signature": "test-sig-value"}
        request.body = AsyncMock(return_value=b'{"job_id": "' + job_id.encode() + b'"}')
        # Internal Render URL (different from public)
        request.url.path = f"/v1/internal/jobs/{job_id}/process"

        mock_receiver_instance = MagicMock()
        MockReceiver.return_value = mock_receiver_instance

        await verify_qstash_signature(request)

        # Verify it used the PUBLIC URL, not the internal one
        mock_receiver_instance.verify.assert_called_once()
        call_kwargs = mock_receiver_instance.verify.call_args[1]
        expected_url = f"https://wanderai-backend.onrender.com/v1/internal/jobs/{job_id}/process"
        assert call_kwargs["url"] == expected_url
        # Body should be raw, not re-serialized
        assert job_id in call_kwargs["body"]

    @pytest.mark.asyncio
    @patch("qstash.Receiver")
    @patch("app.core.security.get_settings")
    async def test_invalid_signature_returns_401(self, mock_settings, MockReceiver):
        """Invalid signature should raise 401."""
        from app.core.security import verify_qstash_signature

        settings = MagicMock()
        settings.is_development = False
        settings.qstash_current_signing_key = "sig_test_key_123456789012"
        settings.qstash_next_signing_key = ""
        settings.public_api_url = "https://example.com"
        mock_settings.return_value = settings

        request = MagicMock()
        request.headers = {"upstash-signature": "bad-signature"}
        request.body = AsyncMock(return_value=b'{}')
        request.url.path = "/v1/internal/jobs/123/process"

        mock_receiver_instance = MagicMock()
        mock_receiver_instance.verify.side_effect = Exception("signature mismatch")
        MockReceiver.return_value = mock_receiver_instance

        with pytest.raises(HTTPException) as exc_info:
            await verify_qstash_signature(request)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    @patch("qstash.Receiver")
    @patch("app.core.security.get_settings")
    async def test_keys_are_stripped(self, mock_settings, MockReceiver):
        """Signing keys should be stripped of whitespace."""
        from app.core.security import verify_qstash_signature

        settings = MagicMock()
        settings.is_development = False
        settings.qstash_current_signing_key = "  sig_current_key  "
        settings.qstash_next_signing_key = "  sig_next_key  "
        settings.public_api_url = "https://example.com"
        mock_settings.return_value = settings

        request = MagicMock()
        request.headers = {"upstash-signature": "test-sig"}
        request.body = AsyncMock(return_value=b'{}')
        request.url.path = "/v1/internal/jobs/123/process"

        mock_receiver_instance = MagicMock()
        MockReceiver.return_value = mock_receiver_instance

        await verify_qstash_signature(request)

        MockReceiver.assert_called_once_with(
            current_signing_key="sig_current_key",
            next_signing_key="sig_next_key",
        )
