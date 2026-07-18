# WanderAI Backend Design

## 1. Architecture

```text
SwiftUI iOS App
        |
        v
FastAPI API on Render
        |
        +--> Supabase PostgreSQL
        |
        +--> Background Job Trigger
        |       - local BackgroundTasks in development
        |       - QStash or Render worker in production
        |
        +--> Research Workflow
        |       - coordinator
        |       - photographer agent
        |       - historian agent
        |       - verifier
        |       - podcast editor
        |
        +--> External Model APIs
        |
        +--> TTS Provider
        |
        +--> Object Storage
```

The model providers host the models. Render hosts the backend code, prompts, workflow logic, and API.

---

## 2. Recommended Stack

| Responsibility | Technology |
|---|---|
| Backend API | FastAPI |
| Runtime | Python 3.12 |
| Hosting | Render |
| Database | Supabase PostgreSQL |
| Authentication | Supabase Auth |
| Background processing | QStash or Render worker |
| Research and generation | OpenAI Responses API or Gemini API |
| TTS | OpenAI TTS initially |
| File storage | Cloudflare R2 or Supabase Storage |
| Monitoring | Sentry |
| CI | GitHub Actions |

---

## 3. Repository Structure

```text
wanderai-backend/
├── .kiro/
│   └── specs/
│       └── wanderai-backend/
│           ├── requirements.md
│           ├── design.md
│           └── tasks.md
├── app/
│   ├── api/
│   │   ├── routes.py
│   │   └── dependencies.py
│   ├── agents/
│   │   ├── coordinator.py
│   │   ├── photographer.py
│   │   ├── historian.py
│   │   ├── verifier.py
│   │   └── podcast_editor.py
│   ├── core/
│   │   ├── config.py
│   │   ├── logging.py
│   │   └── security.py
│   ├── models/
│   │   ├── jobs.py
│   │   ├── research.py
│   │   └── podcast.py
│   ├── services/
│   │   ├── supabase_service.py
│   │   ├── research_service.py
│   │   ├── podcast_service.py
│   │   ├── tts_service.py
│   │   ├── storage_service.py
│   │   └── qstash_service.py
│   ├── workflows/
│   │   └── podcast_generation.py
│   └── main.py
├── scripts/
│   └── test_supabase.py
├── supabase/
│   ├── schema.sql
│   ├── policies.sql
│   └── seed.sql
├── tests/
│   ├── test_health.py
│   ├── test_jobs.py
│   └── test_workflow.py
├── .env.example
├── .gitignore
├── Dockerfile
├── render.yaml
├── requirements.txt
└── README.md
```

---

# 4. Supabase Connection Instructions

## 4.1 Create a Supabase Project

1. Open Supabase
2. Create a new project
3. Name the project `wanderai`
4. Select the closest region to expected users
5. Create and securely save the database password
6. Wait until provisioning is complete

---

## 4.2 Retrieve API Credentials

In Supabase:

```text
Project Settings
→ API
```

Copy:

- Project URL
- Anon key
- Service-role key

Add these to `.env`:

```env
SUPABASE_URL=https://YOUR_PROJECT_ID.supabase.co
SUPABASE_ANON_KEY=YOUR_ANON_KEY
SUPABASE_SERVICE_ROLE_KEY=YOUR_SERVICE_ROLE_KEY
```

Key rules:

| Credential | Location | Usage |
|---|---|---|
| Project URL | Backend and iOS | Supabase endpoint |
| Anon key | iOS or public client | Authenticated access under RLS |
| Service-role key | Backend only | Trusted server operations |
| Database password | Admin tools only | Direct DB and migrations |

Never place the service-role key in the iOS app.

---

## 4.3 Install Dependencies

Add to `requirements.txt`:

```text
fastapi>=0.115,<1.0
uvicorn[standard]>=0.34,<1.0
pydantic-settings>=2.8,<3.0
supabase>=2.15,<3.0
httpx>=0.28,<1.0
openai>=1.75,<2.0
qstash>=3.2,<4.0
boto3>=1.37,<2.0
pytest>=8.0,<9.0
```

Install:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 4.4 Configure Environment Settings

Create `app/core/config.py`:

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    public_api_url: str = "http://localhost:8000"

    supabase_url: str
    supabase_service_role_key: str
    supabase_anon_key: str | None = None

    openai_api_key: str | None = None

    qstash_token: str | None = None
    qstash_current_signing_key: str | None = None
    qstash_next_signing_key: str | None = None

    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket_name: str = "wanderai-audio"

    sentry_dsn: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

---

## 4.5 Create Supabase Client

Create `app/services/supabase_service.py`:

```python
from functools import lru_cache

from supabase import Client, create_client

from app.core.config import get_settings


@lru_cache
def get_supabase() -> Client:
    settings = get_settings()

    if not settings.supabase_url:
        raise RuntimeError("SUPABASE_URL is not configured.")

    if not settings.supabase_service_role_key:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY is not configured."
        )

    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key,
    )
```

---

## 4.6 Create Database Schema

Open:

```text
Supabase Dashboard
→ SQL Editor
```

Run the contents of `supabase/schema.sql`.

Suggested schema:

```sql
create extension if not exists "pgcrypto";

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
```

---

## 4.7 Enable Row Level Security

Run:

```sql
alter table public.research_jobs enable row level security;
alter table public.research_sources enable row level security;
alter table public.research_findings enable row level security;
alter table public.podcast_episodes enable row level security;
```

Add policies:

```sql
create policy "Users can read own research jobs"
on public.research_jobs
for select
using (auth.uid() = user_id);

create policy "Users can read own podcast episodes"
on public.podcast_episodes
for select
using (auth.uid() = user_id);
```

For the MVP, the backend should perform inserts and updates using the service-role key.

---

## 4.8 Test Supabase Connection

Create `scripts/test_supabase.py`:

```python
from app.services.supabase_service import get_supabase


def main() -> None:
    client = get_supabase()

    response = (
        client
        .table("research_jobs")
        .select("id")
        .limit(1)
        .execute()
    )

    print("Supabase connection successful.")
    print(response.data)


if __name__ == "__main__":
    main()
```

Run:

```bash
python scripts/test_supabase.py
```

Expected:

```text
Supabase connection successful.
[]
```

---

# 5. API Design

## Create Job

```http
POST /v1/podcast-jobs
```

The route must:

1. Validate request data
2. Insert a row in `research_jobs`
3. Trigger local background processing in development
4. Trigger QStash or worker processing in production
5. Return HTTP `202`

---

## Get Job

```http
GET /v1/podcast-jobs/{job_id}
```

The route must:

- Return job state
- Return errors
- Return episode metadata when complete
- Enforce user ownership after auth is enabled

---

## Internal Processing

```http
POST /v1/internal/jobs/{job_id}/process
```

The route must:

- Verify QStash signatures in production
- Exit safely for completed jobs
- Move through state transitions
- Store errors
- Be retry-safe

---

# 6. Agent Workflow Design

```text
Coordinator
    |
    +--> Photographer Agent
    |
    +--> Historian Agent
    |
    v
Verifier
    |
    v
Podcast Editor
    |
    v
TTS Generation
    |
    v
Storage Upload
    |
    v
Completed Episode
```

The Photographer and Historian agents may run in parallel.

The Podcast Editor must not have web access.

---

# 7. Data Models

## Research Finding

```json
{
  "persona_id": "photographer",
  "claim": "The lake's visual color changes between shallow and deep sections.",
  "classification": "verified_fact",
  "confidence": 0.94,
  "source_urls": [
    "https://example.com"
  ],
  "podcast_potential": "high",
  "usage_guidance": "Use as an arrival observation."
}
```

## Episode Segment

```json
{
  "segment_id": "segment-01",
  "speaker": "photographer",
  "dialogue": "Notice how the shoreline shifts from turquoise to deeper blue.",
  "finding_ids": [
    "finding-01"
  ],
  "dialogue_type": "observation"
}
```

---

# 8. Deployment Design

## Render

Use a web service for FastAPI.

Production environment variables:

```text
APP_ENV
PUBLIC_API_URL
SUPABASE_URL
SUPABASE_SERVICE_ROLE_KEY
SUPABASE_ANON_KEY
OPENAI_API_KEY
QSTASH_TOKEN
QSTASH_CURRENT_SIGNING_KEY
QSTASH_NEXT_SIGNING_KEY
R2_ACCOUNT_ID
R2_ACCESS_KEY_ID
R2_SECRET_ACCESS_KEY
R2_BUCKET_NAME
SENTRY_DSN
```

Do not commit secrets to GitHub.

---

# 9. Testing Strategy

Required tests:

- Health endpoint
- Request validation
- Supabase connection
- Job creation
- Job retrieval
- Job status transitions
- Idempotent processing
- Unsupported persona rejection
- Failed job persistence
- Verifier rejects unsupported claims
- Podcast editor receives approved findings only
