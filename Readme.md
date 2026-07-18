# WanderAI Backend

Travel podcast generation API for the WanderAI iOS app. Generates destination-specific, persona-led audio episodes using live internet research, fact verification, and text-to-speech.

## Architecture

```
iOS App → FastAPI API → Background Processing → Episode Assets
                ↓
         Supabase PostgreSQL
                ↓
   Research Agents (Photographer + Historian)
                ↓
         Verification Agent
                ↓
         Podcast Editor → TTS → Object Storage
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| API | FastAPI (Python 3.12) |
| Database | Supabase PostgreSQL |
| Auth | Supabase Auth |
| Research & Generation | OpenAI Responses API |
| TTS | OpenAI TTS |
| Object Storage | Cloudflare R2 |
| Background Jobs | QStash (prod) / BackgroundTasks (dev) |
| Hosting | Render |
| Monitoring | Sentry |

## Quick Start

### 1. Clone and install

```bash
git clone <repo-url>
cd wanderai-backend

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your credentials
```

Required for local development:
- `SUPABASE_URL` — Your Supabase project URL
- `SUPABASE_SERVICE_ROLE_KEY` — Backend-only service role key
- `OPENAI_API_KEY` — For research and TTS

### 3. Set up database

Run `supabase/schema.sql` in your Supabase SQL Editor, then run `supabase/policies.sql` to enable Row Level Security.

### 4. Run locally

```bash
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`.

### 5. Verify

```bash
curl http://localhost:8000/health
# {"status": "ok"}
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/v1/podcast-jobs` | Create a podcast generation job |
| GET | `/v1/podcast-jobs/{job_id}` | Get job status and episode info |
| POST | `/v1/internal/jobs/{job_id}/process` | Internal: trigger job processing |

### Create a podcast job

```bash
curl -X POST http://localhost:8000/v1/podcast-jobs \
  -H "Content-Type: application/json" \
  -d '{
    "trip_id": "11111111-1111-1111-1111-111111111111",
    "stop_id": "22222222-2222-2222-2222-222222222222",
    "destination_name": "Lake Crescent",
    "region": "Olympic National Park, Washington",
    "visit_date": "2026-07-30",
    "episode_minutes": 8,
    "personas": ["photographer", "historian"]
  }'
```

Response (HTTP 202):
```json
{
  "job_id": "...",
  "status": "queued"
}
```

## Project Structure

```
app/
├── api/          # Route handlers and dependencies
├── agents/       # Research agents (photographer, historian, verifier, editor)
├── core/         # Config, logging, security
├── models/       # Pydantic models
├── services/     # External service integrations
├── workflows/    # Job processing orchestration
└── main.py       # FastAPI application
supabase/         # Database schema and policies
scripts/          # Utility scripts
tests/            # Test suite
```

## Running Tests

```bash
pytest
```

## Deployment

This project is configured for Render deployment via `render.yaml`. Push to GitHub and create a Render Blueprint to deploy.

All secrets must be set as environment variables in Render — never commit them to Git.

## Security Notes

- The `SUPABASE_SERVICE_ROLE_KEY` is backend-only. Never expose it to the iOS app.
- The iOS app uses only the `SUPABASE_ANON_KEY` with Row Level Security.
- QStash signatures are verified in production to prevent unauthorized job processing.
- All API keys are loaded from environment variables.
