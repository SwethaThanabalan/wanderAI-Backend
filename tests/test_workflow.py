"""Tests for the podcast generation workflow."""

from unittest.mock import AsyncMock, MagicMock, call, patch
from uuid import uuid4

import pytest

from app.models.jobs import JobStatus


class TestWorkflowIdempotency:
    """Tests for workflow idempotency and error handling."""

    @pytest.mark.asyncio
    @patch("app.workflows.podcast_generation.supabase_service")
    async def test_completed_job_is_skipped(self, mock_supabase):
        """Completed jobs should not be reprocessed."""
        from app.workflows.podcast_generation import process_podcast_job

        job_id = uuid4()
        mock_supabase.get_research_job.return_value = {
            "id": str(job_id),
            "status": "completed",
            "destination_name": "Test",
        }

        await process_podcast_job(job_id)

        # Should NOT have updated status
        mock_supabase.update_job_status.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.workflows.podcast_generation.supabase_service")
    async def test_missing_job_returns_early(self, mock_supabase):
        """Non-existent job should return without error."""
        from app.workflows.podcast_generation import process_podcast_job

        job_id = uuid4()
        mock_supabase.get_research_job.return_value = None

        await process_podcast_job(job_id)

        mock_supabase.update_job_status.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.workflows.podcast_generation.run_research_phase")
    @patch("app.workflows.podcast_generation.supabase_service")
    async def test_failed_research_marks_job_failed(self, mock_supabase, mock_research):
        """If research phase raises, job should be marked failed."""
        from app.workflows.podcast_generation import process_podcast_job

        job_id = uuid4()
        mock_supabase.get_research_job.return_value = {
            "id": str(job_id),
            "status": "queued",
            "destination_name": "Test Destination",
            "region": None,
            "visit_date": None,
            "episode_minutes": 8,
            "personas": ["photographer", "historian"],
            "user_id": None,
            "trip_id": str(uuid4()),
            "stop_id": str(uuid4()),
        }

        mock_research.side_effect = RuntimeError("Research timeout")

        await process_podcast_job(job_id)

        # Should have been marked as researching first, then failed
        calls = mock_supabase.update_job_status.call_args_list
        assert len(calls) >= 2

        # Extract status values from all calls
        status_values = []
        for c in calls:
            args, kwargs = c
            if len(args) >= 2:
                status_values.append(args[1])
            elif "status" in kwargs:
                status_values.append(kwargs["status"])

        assert JobStatus.RESEARCHING in status_values
        assert JobStatus.FAILED in status_values

        # Failed should be the last status
        assert status_values[-1] == JobStatus.FAILED


class TestWorkflowStateTransitions:
    """Tests for job state transitions during processing."""

    @pytest.mark.asyncio
    @patch("app.workflows.podcast_generation.store_research_data")
    @patch("app.workflows.podcast_generation.run_verification_phase")
    @patch("app.workflows.podcast_generation.run_research_phase")
    @patch("app.workflows.podcast_generation.supabase_service")
    async def test_transitions_to_researching_then_verifying(
        self, mock_supabase, mock_research, mock_verify, mock_store
    ):
        """Job with findings should transition through researching and verifying states."""
        from app.models.research import (
            AgentResearchOutput,
            ResearchFinding,
            FindingClassification,
            VerificationOutput,
        )
        from app.workflows.podcast_generation import process_podcast_job

        job_id = uuid4()
        mock_supabase.get_research_job.return_value = {
            "id": str(job_id),
            "status": "queued",
            "destination_name": "Test",
            "region": None,
            "visit_date": None,
            "episode_minutes": 8,
            "personas": ["photographer"],
            "user_id": None,
            "trip_id": str(uuid4()),
            "stop_id": str(uuid4()),
        }

        # Return research with at least one finding so we reach verification
        mock_research.return_value = [
            AgentResearchOutput(
                persona_id="photographer",
                destination_name="Test",
                findings=[
                    ResearchFinding(
                        claim="Test claim",
                        classification=FindingClassification.VERIFIED_FACT,
                        confidence=0.9,
                        source_urls=["https://example.com"],
                    )
                ],
            )
        ]

        # Verification returns no approved findings to stop the pipeline
        mock_verify.return_value = VerificationOutput()
        mock_store.return_value = None

        await process_podcast_job(job_id)

        # Extract status values from all update_job_status calls
        status_values = []
        for c in mock_supabase.update_job_status.call_args_list:
            args, kwargs = c
            if len(args) >= 2:
                status_values.append(args[1])
            elif "status" in kwargs:
                status_values.append(kwargs["status"])

        assert JobStatus.RESEARCHING in status_values
        assert JobStatus.VERIFYING in status_values

    @pytest.mark.asyncio
    @patch("app.workflows.podcast_generation.run_research_phase")
    @patch("app.workflows.podcast_generation.supabase_service")
    async def test_zero_findings_fails_before_verification(self, mock_supabase, mock_research):
        """Job with zero findings should fail without reaching verification."""
        from app.models.research import AgentResearchOutput
        from app.workflows.podcast_generation import process_podcast_job

        job_id = uuid4()
        mock_supabase.get_research_job.return_value = {
            "id": str(job_id),
            "status": "queued",
            "destination_name": "Test",
            "region": None,
            "visit_date": None,
            "episode_minutes": 8,
            "personas": ["photographer"],
            "user_id": None,
            "trip_id": str(uuid4()),
            "stop_id": str(uuid4()),
        }

        # Research returns output with zero findings
        mock_research.return_value = [
            AgentResearchOutput(persona_id="photographer", destination_name="Test")
        ]

        await process_podcast_job(job_id)

        # Should go RESEARCHING then FAILED (never VERIFYING)
        status_values = []
        for c in mock_supabase.update_job_status.call_args_list:
            args, kwargs = c
            if len(args) >= 2:
                status_values.append(args[1])
            elif "status" in kwargs:
                status_values.append(kwargs["status"])

        assert JobStatus.RESEARCHING in status_values
        assert JobStatus.FAILED in status_values
        assert JobStatus.VERIFYING not in status_values
