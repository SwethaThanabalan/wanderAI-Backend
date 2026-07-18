-- WanderAI Backend Row Level Security Policies
-- Run this AFTER schema.sql in Supabase SQL Editor.

-- Enable RLS on all tables
alter table public.research_jobs enable row level security;
alter table public.research_sources enable row level security;
alter table public.research_findings enable row level security;
alter table public.podcast_episodes enable row level security;

-- Research Jobs: users can read their own jobs
create policy "Users can read own research jobs"
on public.research_jobs
for select
using (auth.uid() = user_id);

-- Research Sources: users can read sources from their own jobs
create policy "Users can read own research sources"
on public.research_sources
for select
using (
    research_job_id in (
        select id from public.research_jobs
        where user_id = auth.uid()
    )
);

-- Research Findings: users can read findings from their own jobs
create policy "Users can read own research findings"
on public.research_findings
for select
using (
    research_job_id in (
        select id from public.research_jobs
        where user_id = auth.uid()
    )
);

-- Podcast Episodes: users can read their own episodes
create policy "Users can read own podcast episodes"
on public.podcast_episodes
for select
using (auth.uid() = user_id);

-- NOTE: Inserts and updates are performed by the backend using the
-- service-role key, which bypasses RLS. No insert/update policies
-- are needed for the MVP.
