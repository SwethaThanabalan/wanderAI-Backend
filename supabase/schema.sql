-- WanderAI Backend Database Schema
-- Run this in Supabase SQL Editor to create all tables.

create extension if not exists "pgcrypto";

-- Research Jobs: tracks podcast generation requests
create table if not exists public.research_jobs (
    id uuid primary key default gen_random_uuid(),

    user_id uuid references auth.users(id) on delete cascade,
    trip_id uuid not null,
    stop_id uuid not null,

    destination_name text not null,
    region text,
    visit_date date,

    episode_minutes integer not null default 8
        check (episode_minutes between 3 and 20),

    personas jsonb not null
        default '["photographer", "historian"]'::jsonb,

    status text not null default 'queued'
        check (
            status in (
                'queued',
                'researching',
                'verifying',
                'scripting',
                'generating_audio',
                'completed',
                'failed'
            )
        ),

    result jsonb,
    citations jsonb,

    audio_object_key text,
    transcript_object_key text,
    metadata_object_key text,

    error_message text,

    created_at timestamptz not null default now(),
    started_at timestamptz,
    completed_at timestamptz
);

-- Research Sources: URLs and metadata from agent research
create table if not exists public.research_sources (
    id uuid primary key default gen_random_uuid(),

    research_job_id uuid not null
        references public.research_jobs(id)
        on delete cascade,

    persona_id text not null,
    url text not null,
    title text,
    publisher text,
    source_type text,
    published_at timestamptz,
    retrieved_at timestamptz not null default now(),
    reliability_score numeric,
    supporting_excerpt text,

    created_at timestamptz not null default now()
);

-- Research Findings: factual claims extracted from sources
create table if not exists public.research_findings (
    id uuid primary key default gen_random_uuid(),

    research_job_id uuid not null
        references public.research_jobs(id)
        on delete cascade,

    persona_id text not null,
    claim text not null,
    classification text not null,
    confidence numeric,
    approved boolean not null default false,
    source_ids jsonb not null default '[]'::jsonb,
    podcast_potential text,
    usage_guidance text,

    created_at timestamptz not null default now()
);

-- Podcast Episodes: generated episode assets and metadata
create table if not exists public.podcast_episodes (
    id uuid primary key default gen_random_uuid(),

    research_job_id uuid not null unique
        references public.research_jobs(id)
        on delete cascade,

    user_id uuid references auth.users(id) on delete cascade,
    trip_id uuid not null,
    stop_id uuid not null,

    title text not null,
    destination_name text not null,
    duration_seconds integer,

    personas jsonb not null,
    chapters jsonb,
    citations jsonb,

    audio_object_key text,
    transcript_object_key text,
    metadata_object_key text,

    generated_at timestamptz not null default now()
);

-- Indexes for common query patterns
create index if not exists research_jobs_user_id_idx
    on public.research_jobs(user_id);

create index if not exists research_jobs_trip_id_idx
    on public.research_jobs(trip_id);

create index if not exists research_jobs_status_idx
    on public.research_jobs(status);

create index if not exists research_sources_job_id_idx
    on public.research_sources(research_job_id);

create index if not exists research_findings_job_id_idx
    on public.research_findings(research_job_id);

create index if not exists podcast_episodes_user_id_idx
    on public.podcast_episodes(user_id);

create index if not exists podcast_episodes_trip_id_idx
    on public.podcast_episodes(trip_id);
