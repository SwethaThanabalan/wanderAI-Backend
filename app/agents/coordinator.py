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
    """Run all research agents in parallel with timeout.

    Raises immediately if any agent fails (no silent empty results).
    """
    from app.agents.historian import run_historian_research
    from app.agents.photographer import run_photographer_research
    from app.agents.geologist import run_geologist_research
    from app.agents.foodie import run_foodie_research
    from app.agents.storyteller import run_storyteller_research

    settings = get_settings()
    tasks = []

    agent_map = {
        "photographer": run_photographer_research,
        "historian": run_historian_research,
        "geologist": run_geologist_research,
        "foodie": run_foodie_research,
        "storyteller": run_storyteller_research,
    }

    for persona in personas:
        if persona in agent_map:
            tasks.append(
                agent_map[persona](
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
        raise RuntimeError(f"Research timed out after {settings.research_timeout_seconds}s")

    # Fail immediately if any agent returned an exception
    outputs: list[AgentResearchOutput] = []
    errors: list[str] = []

    for result in results:
        if isinstance(result, Exception):
            errors.append(str(result))
            logger.error(
                "Research agent failed",
                extra={"job_id": str(job_id), "error": str(result)},
            )
        elif isinstance(result, AgentResearchOutput):
            outputs.append(result)

    if errors:
        raise RuntimeError(f"Research agent(s) failed: {'; '.join(errors)}")

    # Check total findings across all agents
    total_findings = sum(len(o.findings) for o in outputs)
    if total_findings == 0:
        raise RuntimeError("Research produced zero findings across all agents")

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
    visit_date: str | None = None,
) -> PodcastScript:
    """Run the podcast editor to generate the script."""
    from app.agents.podcast_editor import run_podcast_editor

    script = await run_podcast_editor(
        destination_name=destination_name,
        region=region,
        episode_minutes=episode_minutes,
        personas=personas,
        approved_findings=verification.approved_findings,
        visit_date=visit_date,
    )

    return script


async def run_audio_phase(
    job_id: UUID,
    script: PodcastScript,
    episode_minutes: int,
    personas: list[str],
    findings_text: str,
) -> tuple[bytes, PodcastScript]:
    """Generate episode audio with duration validation.

    If audio is too short (< 95% of requested duration), expands the script
    and regenerates. Returns the final audio bytes and possibly-updated script.
    """
    from app.services.audio_service import (
        generate_episode_audio,
        measure_mp3_duration_seconds,
        count_script_words,
        WORDS_PER_MINUTE,
    )
    from app.agents.podcast_editor import _expand_script, count_script_words as editor_count

    min_duration_seconds = int(episode_minutes * 60 * 0.95)
    max_audio_retries = 1

    current_script = script

    for attempt in range(1 + max_audio_retries):
        audio_bytes = await generate_episode_audio(current_script)
        actual_duration = measure_mp3_duration_seconds(audio_bytes)

        logger.info(
            "Audio duration check",
            extra={
                "job_id": str(job_id),
                "attempt": attempt + 1,
                "actual_seconds": round(actual_duration, 1),
                "min_required_seconds": min_duration_seconds,
                "word_count": count_script_words(current_script),
            },
        )

        if actual_duration >= min_duration_seconds:
            return audio_bytes, current_script

        if attempt >= max_audio_retries:
            logger.warning(
                "Audio still short after expansion, proceeding anyway",
                extra={
                    "actual": round(actual_duration, 1),
                    "required": min_duration_seconds,
                },
            )
            return audio_bytes, current_script

        # Calculate how many words to add
        missing_seconds = (episode_minutes * 60) - actual_duration
        missing_words = int(missing_seconds * WORDS_PER_MINUTE / 60)

        logger.info(
            "Audio too short, expanding script",
            extra={
                "missing_seconds": round(missing_seconds, 1),
                "missing_words": missing_words,
            },
        )

        try:
            current_script = await _expand_script(
                script=current_script,
                episode_minutes=episode_minutes,
                personas=personas,
                findings_text=findings_text,
                missing_words=missing_words,
            )
        except Exception as e:
            logger.warning("Audio expansion failed", extra={"error": str(e)})
            return audio_bytes, current_script

    return audio_bytes, current_script


async def run_upload_phase(
    job_id: UUID,
    script: PodcastScript,
    audio_bytes: bytes,
    episode_minutes: int,
    research_outputs: list[AgentResearchOutput],
    verification: VerificationOutput,
) -> dict:
    """Save all episode assets to temporary storage with duration metadata."""
    from app.services.audio_service import (
        count_script_words,
        estimate_duration_seconds,
        measure_mp3_duration_seconds,
    )
    from app.services.temp_storage_service import (
        save_audio,
        save_citations,
        save_metadata,
        save_transcript,
    )

    # Compute duration stats
    script_word_count = count_script_words(script)
    estimated_duration = estimate_duration_seconds(script)
    actual_audio_duration = round(measure_mp3_duration_seconds(audio_bytes), 1)
    target_word_count = episode_minutes * 175
    minimum_word_count = int(target_word_count * 0.95)
    requested_duration_seconds = episode_minutes * 60
    minimum_allowed_duration_seconds = int(requested_duration_seconds * 0.95)

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

    # Build metadata with duration stats
    metadata = {
        "title": script.title,
        "destination_name": script.destination_name,
        "personas": script.personas,
        "chapters": [ch.model_dump() for ch in script.chapters],
        "citations": citations,
        "requested_duration_seconds": requested_duration_seconds,
        "minimum_allowed_duration_seconds": minimum_allowed_duration_seconds,
        "target_word_count": target_word_count,
        "minimum_word_count": minimum_word_count,
        "script_word_count": script_word_count,
        "estimated_script_duration_seconds": estimated_duration,
        "actual_audio_duration_seconds": actual_audio_duration,
        "segment_count": len(script.segments),
        "chapter_count": len(script.chapters),
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

    logger.info(
        "Episode assets saved",
        extra={
            "job_id": str(job_id),
            "script_words": script_word_count,
            "actual_duration": actual_audio_duration,
            "target_duration": requested_duration_seconds,
        },
    )

    return {
        "audio_object_key": audio_key,
        "transcript_object_key": transcript_key,
        "metadata_object_key": metadata_key,
        "citations": citations,
        "chapters": [ch.model_dump() for ch in script.chapters],
        "duration_seconds": int(actual_audio_duration),
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
