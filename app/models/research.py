"""Pydantic models for research sources and findings."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class SourceType(StrEnum):
    """Classification of research source origin."""

    OFFICIAL = "official"
    ACADEMIC = "academic"
    ARCHIVAL = "archival"
    MUSEUM = "museum"
    TRIBAL = "tribal"
    GOVERNMENT = "government"
    NEWS = "news"
    BLOG = "blog"
    TRAVEL = "travel"
    FORUM = "forum"
    OTHER = "other"


class FindingClassification(StrEnum):
    """Classification of a research finding."""

    VERIFIED_FACT = "verified_fact"
    DOCUMENTED_FOLKLORE = "documented_folklore"
    CONTESTED = "contested"
    UNVERIFIED = "unverified"
    OUTDATED = "outdated"
    REJECTED = "rejected"


class PodcastPotential(StrEnum):
    """How suitable a finding is for the podcast."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# --- Research Source ---


class ResearchSource(BaseModel):
    """A source discovered during research."""

    url: str
    title: str | None = None
    publisher: str | None = None
    source_type: SourceType = SourceType.OTHER
    published_at: datetime | None = None
    reliability_score: float | None = Field(default=None, ge=0.0, le=1.0)
    supporting_excerpt: str | None = None


class ResearchSourceRecord(ResearchSource):
    """Research source as stored in Supabase."""

    id: UUID
    research_job_id: UUID
    persona_id: str
    retrieved_at: datetime
    created_at: datetime


# --- Research Finding ---


class ResearchFinding(BaseModel):
    """A factual claim discovered during research."""

    claim: str
    classification: FindingClassification
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source_urls: list[str] = Field(default_factory=list)
    podcast_potential: PodcastPotential = PodcastPotential.MEDIUM
    usage_guidance: str | None = None


class ResearchFindingRecord(BaseModel):
    """Research finding as stored in Supabase."""

    id: UUID
    research_job_id: UUID
    persona_id: str
    claim: str
    classification: FindingClassification
    confidence: float | None = None
    approved: bool = False
    source_ids: list[str] = Field(default_factory=list)
    podcast_potential: str | None = None
    usage_guidance: str | None = None
    created_at: datetime


# --- Agent Output ---


class AgentResearchOutput(BaseModel):
    """Structured output from a research agent."""

    persona_id: str
    destination_name: str
    sources: list[ResearchSource] = Field(default_factory=list)
    findings: list[ResearchFinding] = Field(default_factory=list)
    queries_used: int = 0
    sources_reviewed: int = 0


# --- Verification ---


class VerificationResult(BaseModel):
    """Result of verifying a single finding."""

    finding_claim: str
    approved: bool
    rejection_reason: str | None = None
    updated_classification: FindingClassification | None = None
    notes: str | None = None


class VerificationOutput(BaseModel):
    """Full output from the verification agent."""

    approved_findings: list[ResearchFinding] = Field(default_factory=list)
    rejected_findings: list[VerificationResult] = Field(default_factory=list)
    conflicts_detected: list[str] = Field(default_factory=list)
