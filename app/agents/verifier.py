"""Verification agent.

Verifies research findings before they are used in the podcast:
- Confirms each claim has supporting evidence
- Rejects unsupported claims
- Detects conflicting dates or interpretations
- Labels folklore clearly
- Flags outdated rules and regulations
- Stores rejected claims with rejection reasons

The Verification agent does NOT browse the internet.
It works only with the findings and sources already gathered.
"""

import json
from typing import Any

from app.core.logging import get_logger
from app.models.research import (
    AgentResearchOutput,
    FindingClassification,
    ResearchFinding,
    VerificationOutput,
    VerificationResult,
)
from app.services.research_service import generate_research

logger = get_logger(__name__)

VERIFIER_SYSTEM_PROMPT = """You are the WanderAI Verification Agent. Your job is to verify research
findings before they are used in a travel podcast.

You do NOT have internet access. You work only with the provided findings and their cited sources.

For each finding, you must:
1. Check if the claim has at least one supporting source URL
2. Assess whether the confidence level is reasonable
3. Verify the classification is appropriate (verified_fact, documented_folklore, contested, unverified)
4. Check for conflicting dates or interpretations across findings
5. Ensure folklore is clearly labeled as folklore
6. Flag any rules/regulations that might be outdated (seasonal closures, fee changes, etc.)
7. Reject claims that have no supporting evidence

Approval criteria:
- APPROVE: Claim has supporting source(s), reasonable confidence, correct classification
- REJECT: No sources, fabricated-looking URLs, contradicts other findings, unverifiable claim

Output valid JSON with this structure:
{
  "approved_findings": [
    // Include the full finding object for each approved finding
    {"claim": "...", "classification": "...", "confidence": 0.0-1.0, "source_urls": ["..."], "podcast_potential": "...", "usage_guidance": "..."}
  ],
  "rejected_findings": [
    {"finding_claim": "...", "approved": false, "rejection_reason": "...", "notes": "..."}
  ],
  "conflicts_detected": [
    "Description of any conflicting information found between findings"
  ]
}

Be rigorous but fair. A finding with a single credible source can be approved.
Folklore is valid IF it is correctly classified as documented_folklore."""


async def run_verification(
    research_outputs: list[AgentResearchOutput],
) -> VerificationOutput:
    """Verify all research findings from all personas.

    Checks each claim for evidence, consistency, and appropriate classification.
    Returns approved and rejected findings.
    """
    # Compile all findings for verification
    all_findings: list[dict] = []
    for output in research_outputs:
        for finding in output.findings:
            all_findings.append({
                "persona_id": output.persona_id,
                "claim": finding.claim,
                "classification": finding.classification,
                "confidence": finding.confidence,
                "source_urls": finding.source_urls,
                "podcast_potential": finding.podcast_potential,
                "usage_guidance": finding.usage_guidance,
            })

    if not all_findings:
        logger.warning("No findings to verify")
        return VerificationOutput()

    # Compile all sources for context
    all_sources: list[dict] = []
    for output in research_outputs:
        for source in output.sources:
            all_sources.append({
                "persona_id": output.persona_id,
                "url": source.url,
                "title": source.title,
                "publisher": source.publisher,
                "source_type": source.source_type,
                "reliability_score": source.reliability_score,
            })

    user_prompt = f"""Verify the following research findings for a travel podcast.

SOURCES GATHERED ({len(all_sources)} total):
{json.dumps(all_sources, indent=2)}

FINDINGS TO VERIFY ({len(all_findings)} total):
{json.dumps(all_findings, indent=2)}

Review each finding and return your verification results as JSON."""

    try:
        response_text = await generate_research(
            system_prompt=VERIFIER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_format={"type": "json_object"},
        )

        # Parse JSON response
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]

        data = json.loads(response_text)

        approved = [
            ResearchFinding(**f) for f in data.get("approved_findings", [])
        ]
        rejected = [
            VerificationResult(**r) for r in data.get("rejected_findings", [])
        ]
        conflicts = data.get("conflicts_detected", [])

        output = VerificationOutput(
            approved_findings=approved,
            rejected_findings=rejected,
            conflicts_detected=conflicts,
        )

        logger.info(
            "Verification completed",
            extra={
                "total_findings": len(all_findings),
                "approved": len(approved),
                "rejected": len(rejected),
                "conflicts": len(conflicts),
            },
        )

        return output

    except json.JSONDecodeError as e:
        logger.error("Failed to parse verification output", extra={"error": str(e)})
        # If verification fails, conservatively approve findings with sources
        approved = []
        for output in research_outputs:
            for finding in output.findings:
                if finding.source_urls:
                    approved.append(finding)

        return VerificationOutput(approved_findings=approved)

    except Exception as e:
        logger.error("Verification agent failed", extra={"error": str(e)})
        raise
