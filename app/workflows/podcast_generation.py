"""Podcast generation workflow.

Manages the end-to-end processing of a podcast job:
1. Validate job is processable (not already completed)
2. Research phase (photographer + historian in parallel)
3. Verification phase
4. Script generation phase
5. Audio generation phase
6. Upload phase
7. Create episode record
8. Mark job completed

Handles errors at each stage, marking the job as failed with
a descriptive message if any phase fails.
"""

import traceback
from uuid import UUID

from app.agents.coordinator import (
    run_audio_phase,
    run_research_phase,
    run_scripting_phase,
    run_upload_phase,
    run_verification_phase,
    store_research_data,
)
from app.core.logging import get_logger
from app.models.jobs import JobStatus
from app.services import supabase_service

logger = get_logger(__name__)


async def process_podcast_job(job_id: UUID) -> None:
    """Process a podcast generation job through all pipeline stages.

    This is the main entry point called by BackgroundTasks (dev)
    or the internal processing endpoint (production via QStash).

    The function is idempotent: completed jobs are skipped.
    Failed jobs can be safely reprocessed.
    """
    logger.info("Starting podcast job processing", extra={"job_id": str(job_id)})

    # 1. Fetch and validate the job
    job = supabase_service.get_research_job(job_id)

    if not job:
        logger.error("Job not found", extra={"job_id": str(job_id)})
        return

    # Idempotency: skip completed jobs
    if job["status"] == JobStatus.COMPLETED:
        logger.info("Job already completed, skipping", extra={"job_id": str(job_id)})
        return

    # Allow reprocessing of failed jobs
    if job["status"] == JobStatus.FAILED:
        logger.info("Reprocessing previously failed job", extra={"job_id": str(job_id)})

    destination_name = job["destination_name"]
    region = job.get("region")
    visit_date = str(job.get("visit_date")) if job.get("visit_date") else None
    episode_minutes = job["episode_minutes"]
    personas = job["personas"]
    user_id = job.get("user_id")
    trip_id = UUID(job["trip_id"])
    stop_id = UUID(job["stop_id"])

    try:
        # 2. Research phase
        supabase_service.update_job_status(job_id, JobStatus.RESEARCHING)

        research_outputs = await run_research_phase(
            job_id=job_id,
            destination_name=destination_name,
            region=region,
            visit_date=visit_date,
            personas=personas,
        )

        if not research_outputs:
            raise RuntimeError("All research agents failed to produce results")

        # Fail early if no findings to verify
        total_findings = sum(len(o.findings) for o in research_outputs)
        if total_findings == 0:
            raise RuntimeError("Research produced zero findings — nothing to verify")

        # 3. Verification phase
        supabase_service.update_job_status(job_id, JobStatus.VERIFYING)

        verification = await run_verification_phase(
            job_id=job_id,
            research_outputs=research_outputs,
        )

        # Store research data in Supabase
        await store_research_data(
            job_id=job_id,
            research_outputs=research_outputs,
            verification=verification,
        )

        if not verification.approved_findings:
            raise RuntimeError("No findings were approved by verification")

        # 4. Scripting phase
        supabase_service.update_job_status(job_id, JobStatus.SCRIPTING)

        script = await run_scripting_phase(
            job_id=job_id,
            destination_name=destination_name,
            region=region,
            episode_minutes=episode_minutes,
            personas=personas,
            verification=verification,
        )

        if not script.segments:
            raise RuntimeError("Podcast editor produced no dialogue segments")

        # 5. Audio generation phase (with duration validation)
        supabase_service.update_job_status(job_id, JobStatus.GENERATING_AUDIO)

        # Build findings text for potential expansion
        import json as _json
        _findings_text = _json.dumps([
            {
                "claim": f.claim,
                "classification": f.classification,
                "confidence": f.confidence,
                "source_urls": f.source_urls,
                "podcast_potential": f.podcast_potential,
                "usage_guidance": f.usage_guidance,
            }
            for f in verification.approved_findings
        ], indent=2)

        audio_bytes, script = await run_audio_phase(
            job_id=job_id,
            script=script,
            episode_minutes=episode_minutes,
            personas=personas,
            findings_text=_findings_text,
        )

        if not audio_bytes:
            raise RuntimeError("Audio generation produced empty output")

        # 6. Upload phase
        upload_result = await run_upload_phase(
            job_id=job_id,
            script=script,
            audio_bytes=audio_bytes,
            episode_minutes=episode_minutes,
            research_outputs=research_outputs,
            verification=verification,
        )

        # 7. Create episode record
        episode = supabase_service.create_podcast_episode(
            research_job_id=job_id,
            user_id=UUID(user_id) if user_id else None,
            trip_id=trip_id,
            stop_id=stop_id,
            title=script.title,
            destination_name=destination_name,
            duration_seconds=upload_result.get("duration_seconds") or int(script.total_estimated_duration_seconds or 0),
            personas=personas,
            chapters=upload_result.get("chapters"),
            citations=upload_result.get("citations"),
            audio_object_key=upload_result.get("audio_object_key"),
            transcript_object_key=upload_result.get("transcript_object_key"),
            metadata_object_key=upload_result.get("metadata_object_key"),
        )

        # 8. Save result and mark completed
        supabase_service.save_job_result(
            job_id=job_id,
            result={
                "episode_id": episode["id"],
                "title": script.title,
                "duration_seconds": upload_result.get("duration_seconds") or int(script.total_estimated_duration_seconds or 0),
                "segments_count": len(script.segments),
                "chapters_count": len(script.chapters),
            },
            citations=upload_result.get("citations"),
        )

        supabase_service.update_job_status(job_id, JobStatus.COMPLETED)

        logger.info(
            "Podcast job completed successfully",
            extra={
                "job_id": str(job_id),
                "episode_id": episode["id"],
                "title": script.title,
            },
        )

    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        logger.error(
            "Podcast job failed",
            extra={
                "job_id": str(job_id),
                "error": error_msg,
                "traceback": traceback.format_exc(),
            },
        )

        supabase_service.update_job_status(
            job_id=job_id,
            status=JobStatus.FAILED,
            error_message=error_msg,
        )
