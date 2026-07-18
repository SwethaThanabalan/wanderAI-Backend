-- WanderAI Backend Seed Data
-- Optional: run this to insert test data for local development.
-- Requires a valid user in auth.users (or set user_id to NULL for testing).

-- Example: Insert a test research job (with NULL user_id for dev)
insert into public.research_jobs (
    trip_id,
    stop_id,
    destination_name,
    region,
    visit_date,
    episode_minutes,
    personas,
    status
) values (
    '11111111-1111-1111-1111-111111111111',
    '22222222-2222-2222-2222-222222222222',
    'Lake Crescent',
    'Olympic National Park, Washington',
    '2026-07-30',
    8,
    '["photographer", "historian"]'::jsonb,
    'queued'
)
on conflict do nothing;
