# WanderAI Backend Requirements

## 1. Overview

The `wanderai-backend` service supports WanderAI's Travel Companion feature.

The backend must allow the iOS app to request a destination-specific, persona-led travel podcast. For the MVP, the podcast will use two personas:

- Photographer
- Historian

Each persona must research the destination from the live internet from its own perspective. The backend must verify the research, generate a conversational podcast script, create multi-voice audio, and return downloadable episode assets to the iOS app.

The backend must not maintain a permanent destination knowledge base.

---

## 2. Product Goals

The backend must:

1. Accept podcast generation requests for a trip stop.
2. Create and track asynchronous generation jobs.
3. Run live internet research for each selected persona.
4. Store source URLs and research findings.
5. Verify claims before script generation.
6. Generate a conversational, persona-led podcast script.
7. Generate audio using distinct voices.
8. Store audio, transcript, citations, and metadata.
9. Return job status and episode download information.
10. Support offline playback after the iOS app downloads the episode.

---

## 3. MVP Scope

### Included

- FastAPI backend
- Supabase PostgreSQL
- Supabase Auth integration-ready architecture
- Photographer research agent
- Historian research agent
- Verification agent
- Podcast editor agent
- Background job processing
- External model API calls
- Text-to-speech generation
- Object storage integration
- Render deployment
- GitHub CI
- Structured logs and error handling

### Excluded

- Permanent destination knowledge base
- Self-hosted language models
- Custom-trained models
- More than two personas
- Real-time voice interruption
- Automatic geofence playback
- Public podcast marketplace
- Long-term storage of full scraped webpages
- Unlimited autonomous browsing

---

## 4. Functional Requirements

### 4.1 Health Check

The backend must expose:

```http
GET /health
```

Expected response:

```json
{
  "status": "ok"
}
```

---

### 4.2 Create Podcast Job

The backend must expose:

```http
POST /v1/podcast-jobs
```

Request body:

```json
{
  "trip_id": "11111111-1111-1111-1111-111111111111",
  "stop_id": "22222222-2222-2222-2222-222222222222",
  "destination_name": "Lake Crescent",
  "region": "Olympic National Park, Washington",
  "visit_date": "2026-07-30",
  "episode_minutes": 8,
  "personas": [
    "photographer",
    "historian"
  ]
}
```

Response:

```json
{
  "job_id": "generated-job-id",
  "status": "queued"
}
```

The endpoint must return HTTP `202 Accepted`.

---

### 4.3 Read Podcast Job

The backend must expose:

```http
GET /v1/podcast-jobs/{job_id}
```

The endpoint must return:

- Job ID
- Job status
- Destination
- Selected personas
- Error information when failed
- Episode metadata when completed

Supported job states:

```text
queued
researching
verifying
scripting
generating_audio
completed
failed
```

---

### 4.4 Internal Job Processing

The backend must expose an internal endpoint:

```http
POST /v1/internal/jobs/{job_id}/process
```

This endpoint must:

- Be callable by QStash or a background worker
- Verify request authenticity in production
- Be idempotent
- Prevent completed jobs from being processed again
- Update job status in Supabase
- Capture errors and mark jobs as failed

---

### 4.5 Photographer Research Agent

The Photographer agent must research:

- Visual identity
- Scenic viewpoints
- Lighting conditions
- Seasonal appearance
- Reflections
- Photography restrictions
- Tripod and drone rules
- Accessible photography locations
- Details travelers may overlook

The Photographer agent must return structured findings with:

- Claim
- Source URL
- Source title
- Publisher
- Source type
- Confidence
- Classification
- Podcast potential
- Usage guidance

---

### 4.6 Historian Research Agent

The Historian agent must research:

- Indigenous history
- Place-name origins
- Settlement history
- Major events
- Architecture
- Local industries
- Documented folklore
- Contested interpretations

The Historian agent must:

- Distinguish verified history from folklore
- Avoid invented quotations
- Prefer tribal, archival, museum, government, and academic sources
- Avoid referring to living communities only in the past tense
- Include confidence and source information

---

### 4.7 Verification Agent

The Verification agent must:

- Confirm that each factual claim has supporting evidence
- Reject unsupported claims
- Detect conflicting dates or interpretations
- Label folklore clearly
- Flag outdated rules and regulations
- Approve only verified findings for podcast use
- Store rejected claims and rejection reasons

The Podcast Editor must not browse the internet.

---

### 4.8 Podcast Editor

The Podcast Editor must:

- Use only approved findings
- Generate a conversational script
- Keep each persona distinct
- Avoid repetitive or fake banter
- Generate an episode title
- Generate chapters
- Map factual segments to source-backed findings
- Respect the requested episode length

---

### 4.9 Audio Generation

The backend must:

- Generate distinct voices for Photographer and Historian
- Create a final audio file
- Create transcript JSON
- Create citation metadata
- Upload assets to object storage
- Save storage keys in Supabase
- Return signed download URLs or backend proxy URLs

---

## 5. Supabase Requirements

The backend must use Supabase for:

- User ownership
- Research job records
- Source metadata
- Research findings
- Podcast episode metadata
- Job status tracking

The backend must use the Supabase service-role key only on the server.

The iOS app must never receive:

- Supabase service-role key
- OpenAI API key
- Gemini API key
- QStash signing keys
- Object-storage secret keys

---

## 6. Security Requirements

The backend must:

- Load secrets from environment variables
- Exclude `.env` from Git
- Validate incoming request payloads
- Verify QStash signatures before production
- Use Row Level Security in Supabase
- Prevent users from accessing another user's jobs
- Use signed URLs for private audio files
- Avoid logging secrets
- Add rate limiting before public beta
- Add job usage limits before paid model usage is exposed broadly

---

## 7. Reliability Requirements

The backend must:

- Retry temporary model and network failures
- Avoid rerunning completed jobs
- Record failure reasons
- Preserve source citations
- Track state transitions
- Support safe reprocessing of failed jobs
- Time out research that exceeds configured budgets
- Limit persona search depth and source counts

---

## 8. Research Budget Requirements

For the MVP:

### Photographer

- Maximum search queries: 5
- Maximum reviewed sources: 7
- Minimum official sources: 1
- Maximum sources per domain: 2

### Historian

- Maximum search queries: 7
- Maximum reviewed sources: 10
- Minimum official, archival, museum, academic, or tribal sources: 2
- Maximum sources per domain: 2

---

## 9. Definition of Done

The MVP is complete when a user can:

1. Request a podcast for a trip stop
2. Select Photographer and Historian
3. See a queued status
4. See progress updates
5. Receive a completed podcast
6. Download the episode
7. Read the transcript
8. View the sources used
9. Retry after a failure
10. Play the downloaded episode offline in the iOS app

## Full Live Podcast MVP Update

The implementation must no longer stop at mock research or mock podcast data.

The complete required workflow is:

```text
Podcast job
→ live internet search
→ Photographer research
→ Historian research
→ source persistence
→ verification
→ real conversational script
→ OpenAI text-to-speech
→ combined MP3
→ temporary backend download
→ local storage in the iOS app
```

### Storage decision

Do not use Cloudflare R2 for the MVP.

Generated assets must be held temporarily under:

```text
/tmp/wanderai/<job-id>/
```

Required assets:

- `episode.mp3`
- `transcript.json`
- `citations.json`
- `metadata.json`

The iOS app downloads these files and saves them under its Application Support directory for offline playback.

### Live web research

Use OpenAI web-search tooling for the Photographer and Historian agents.

The Podcast Editor must not have web access and must only use verified findings.

### Audio requirements

Use OpenAI text-to-speech with one consistent voice per persona. Generate audio per dialogue segment and concatenate it in order with short pauses. Do not add copyrighted music.

### Download endpoints

Add:

- `GET /v1/podcast-jobs/{job_id}/audio`
- `GET /v1/podcast-jobs/{job_id}/transcript`
- `GET /v1/podcast-jobs/{job_id}/citations`
- `GET /v1/podcast-jobs/{job_id}/metadata`

### Completion requirement

The implementation is not complete until a Lake Crescent job performs live research, stores sources and findings in Supabase, generates a real script, creates a real MP3, and exposes downloadable assets.
