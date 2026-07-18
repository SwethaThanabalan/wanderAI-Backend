"""Research Coordinator.

Orchestrates the full research and generation pipeline:
1. Run research agents (Photographer, Historian) in parallel
2. Run the Verification agent
3. Run the Podcast Editor
4. Generate audio via TTS
5. Upload assets to storage
6. Create episode record

The coordinator manages state transitions, error handling,
retries, and timeouts for the entire pipeline.
"""

import asyncio
import json
from uuid import UUID

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.jobs import JobStatus
from app.models.podcast import PodcastScript
from app.models.research import AgentResearchOutput, VerificationOutput
from app.services import supabase_service

logger = get_logger(__name__)


async def run_research_phase(
    job_id: UUID,
    destination_name: str,
    region: str | None,
    visit_date: str | None,
    personas: list[str],
) -> list[AgentResearchOutput]:
    """Run all research agents in parallel with timeout."""
    from app.agents.historian import run_historian_research
    from app.agents.photographer import run_photographer_research

    settings = get_settings()
    tasks = []

    if "photographer" in personas:
        tasks.append(
            run_photographer_research(
                destination_name=destination_name,
                region=region,
                visit_date=visit_date,
            )
        )

    if "historian" in personas:
        tasks.append(
            run_historian_research(
                destination_name=destination_name,
                region=region,
                visit_date=visit_date,
            )
        )

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=settings.research_timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.error(
            "Research phase timed out",
            extra={"job_id": str(job_id), "timeout": settings.research_timeout_seconds},
        )
        raise

    # Process results, filtering out exceptions
    outputs: list[AgentResearchOutput] = []
    for result in results:
        if isinstance(result, Exception):
            logger.error(
                "Research agent failed",
                extra={"job_id": str(job_id), "error": str(result)},
            )
        elif isinstance(result, AgentResearchOutput):
            outputs.append(result)

    return outputs


async def run_verification_phase(
    job_id: UUID,
    research_outputs: list[AgentResearchOutput],
) -> VerificationOutput:
    """Run the verification agent on research findings."""
    from app.agents.verifier import run_verification

    verification = await run_verification(research_outputs)

    logger.info(
        "Verification phase completed",
        extra={
            "job_id": str(job_id),
            "approved": len(verification.approved_findings),
            "rejected": len(verification.rejected_findings),
        },
    )

    return verification


async def run_scripting_phase(
    job_id: UUID,
    destination_name: str,
    region: str | None,
    episode_minutes: int,
    personas: list[str],
    verification: VerificationOutput,
) -> PodcastScript:
    """Run the podcast editor to generate the script."""
    from app.agents.podcast_editor import run_podcast_editor

    script = await run_podcast_editor(
        destination_name=destination_name,
        region=region,
        episode_minutes=episode_minutes,
        personas=personas,
        approved_findings=verification.approved_findings,
    )

    return script


async def run_audio_phase(
    job_id: UUID,
    script: PodcastScript,
) -> bytes:
    """Generate episode audio from the script."""
    from app.services.audio_service import generate_episode_audio

    audio_bytes = await generate_episode_audio(script)

    logger.info(
        "Audio generation completed",
        extra={"job_id": str(job_id), "audio_bytes": len(audio_bytes)},
    )

    return audio_bytes


async def run_upload_phase(
    job_id: UUID,
    script: PodcastScript,
    audio_bytes: bytes,
    research_outputs: list[AgentResearchOutput],
    verification: VerificationOutput,
) -> dict:
    """Save all episode assets to temporary storage."""
    from app.services.audio_service import estimate_duration_seconds
    from app.services.temp_storage_service import (
        save_audio,
        save_citations,
        save_metadata,
        save_transcript,
    )

    # Build transcript data
    transcript_data = {
        "title": script.title,
        "segments": [
            {
                "segment_id": seg.segment_id,
                "speaker": seg.speaker,
                "dialogue": seg.dialogue,
                "dialogue_type": seg.dialogue_type,
                "finding_ids": seg.finding_ids,
            }
            for seg in script.segments
        ],
    }

    # Build citations
    citations = []
    for output in research_outputs:
        for source in output.sources:
            citations.append({
                "persona_id": output.persona_id,
                "url": source.url,
                "title": source.title,
                "publisher": source.publisher,
                "source_type": source.source_type,
            })

    # Build metadata
    duration_seconds = estimate_duration_seconds(script)
    metadata = {
        "title": script.title,
        "destination_name": script.destination_name,
        "personas": script.personas,
        "chapters": [ch.model_dump() for ch in script.chapters],
        "citations": citations,
        "total_duration_seconds": duration_seconds,
    }

    # Save all assets to /tmp/wanderai/<job_id>/
    audio_key = save_audio(job_id, audio_bytes)
    transcript_key = save_transcript(job_id, transcript_data)
    citations_key = save_citations(job_id, citations)
    metadata_key = save_metadata(job_id, metadata)

    # Save object keys to the job record
    supabase_service.save_job_object_keys(
        job_id=job_id,
        audio_object_key=audio_key,
        transcript_object_key=transcript_key,
        metadata_object_key=metadata_key,
    )

    return {
        "audio_object_key": audio_key,
        "transcript_object_key": transcript_key,
        "metadata_object_key": metadata_key,
        "citations": citations,
        "chapters": [ch.model_dump() for ch in script.chapters],
        "duration_seconds": duration_seconds,
    }


async def store_research_data(
    job_id: UUID,
    research_outputs: list[AgentResearchOutput],
    verification: VerificationOutput,
) -> None:
    """Persist research sources and findings to Supabase."""
    for output in research_outputs:
        # Store sources
        sources_dicts = [s.model_dump() for s in output.sources]
        supabase_service.insert_research_sources(
            research_job_id=job_id,
            persona_id=output.persona_id,
            sources=sources_dicts,
        )

        # Store findings
        findings_dicts = [f.model_dump() for f in output.findings]
        supabase_service.insert_research_findings(
            research_job_id=job_id,
            persona_id=output.persona_id,
            findings=findings_dicts,
        )

    # Update approval status
    approved_claims = [f.claim for f in verification.approved_findings]
    supabase_service.update_findings_approval(
        research_job_id=job_id,
        approved_claims=approved_claims,
    )
