# WanderAI Backend Tasks

## Phase 1 — Repository and Local Setup

- [ ] 1. Create the `wanderai-backend` GitHub repository
- [ ] 2. Add `.kiro/specs/wanderai-backend/`
- [ ] 3. Add `requirements.md`, `design.md`, and `tasks.md`
- [ ] 4. Create the FastAPI project structure
- [ ] 5. Add `.gitignore`
- [ ] 6. Add `.env.example`
- [ ] 7. Add `requirements.txt`
- [ ] 8. Add a `/health` endpoint
- [ ] 9. Confirm FastAPI runs locally
- [ ] 10. Add basic pytest configuration

---

## Phase 2 — Supabase Setup

- [ ] 11. Create the Supabase project
- [ ] 12. Copy the Project URL
- [ ] 13. Copy the anon key
- [ ] 14. Copy the service-role key
- [ ] 15. Add Supabase credentials to local `.env`
- [ ] 16. Add Supabase credentials to `.env.example` without values
- [ ] 17. Create `app/core/config.py`
- [ ] 18. Create `app/services/supabase_service.py`
- [ ] 19. Create `supabase/schema.sql`
- [ ] 20. Run the schema in Supabase SQL Editor
- [ ] 21. Enable Row Level Security
- [ ] 22. Add read policies for user-owned jobs and episodes
- [ ] 23. Create `scripts/test_supabase.py`
- [ ] 24. Confirm the backend can query `research_jobs`
- [ ] 25. Document that the service-role key is backend-only

---

## Phase 3 — Podcast Job API

- [ ] 26. Create podcast job request and response models
- [ ] 27. Add supported persona enum
- [ ] 28. Add supported job-status enum
- [ ] 29. Implement `POST /v1/podcast-jobs`
- [ ] 30. Insert new jobs into Supabase
- [ ] 31. Return HTTP `202 Accepted`
- [ ] 32. Implement `GET /v1/podcast-jobs/{job_id}`
- [ ] 33. Return `404` for unknown jobs
- [ ] 34. Add request validation tests
- [ ] 35. Add job creation tests
- [ ] 36. Add job retrieval tests

---

## Phase 4 — Local Job Processing

- [ ] 37. Create `app/workflows/podcast_generation.py`
- [ ] 38. Add a mock processing workflow
- [ ] 39. Transition job from `queued` to `researching`
- [ ] 40. Transition mock job to `completed`
- [ ] 41. Save mock result JSON in Supabase
- [ ] 42. Trigger mock workflow with FastAPI BackgroundTasks
- [ ] 43. Confirm the end-to-end local job flow works
- [ ] 44. Add failure handling
- [ ] 45. Save error messages in Supabase
- [ ] 46. Prevent completed jobs from rerunning

---

## Phase 5 — Live Research Agents

- [ ] 47. Create the Research Coordinator
- [ ] 48. Create Photographer agent instructions
- [ ] 49. Create Historian agent instructions
- [ ] 50. Add external model client
- [ ] 51. Add web-search capability
- [ ] 52. Enforce Photographer research budget
- [ ] 53. Enforce Historian research budget
- [ ] 54. Return structured research output
- [ ] 55. Store source metadata in `research_sources`
- [ ] 56. Store findings in `research_findings`
- [ ] 57. Run Photographer and Historian research in parallel
- [ ] 58. Add timeouts
- [ ] 59. Add retry logic
- [ ] 60. Add source deduplication

---

## Phase 6 — Verification

- [ ] 61. Create the Verification agent
- [ ] 62. Verify that each claim has supporting evidence
- [ ] 63. Reject unsupported claims
- [ ] 64. Detect conflicting dates
- [ ] 65. Label folklore
- [ ] 66. Flag outdated regulations
- [ ] 67. Save approved findings
- [ ] 68. Save rejected claims and reasons
- [ ] 69. Add verifier unit tests

---

## Phase 7 — Podcast Script

- [ ] 70. Create Photographer speaking persona
- [ ] 71. Create Historian speaking persona
- [ ] 72. Create Podcast Editor agent
- [ ] 73. Disable web access for Podcast Editor
- [ ] 74. Pass only approved findings to Podcast Editor
- [ ] 75. Generate episode title
- [ ] 76. Generate chapter structure
- [ ] 77. Generate conversational dialogue
- [ ] 78. Map segments to finding IDs
- [ ] 79. Enforce episode duration target
- [ ] 80. Add script critic
- [ ] 81. Allow one controlled revision
- [ ] 82. Save final transcript

---

## Phase 8 — Audio and Storage

- [ ] 83. Create TTS service
- [ ] 84. Assign distinct voices
- [ ] 85. Generate per-persona audio
- [ ] 86. Combine audio segments
- [ ] 87. Create transcript JSON
- [ ] 88. Create citations JSON
- [ ] 89. Create metadata JSON
- [ ] 90. Configure R2 or Supabase Storage
- [ ] 91. Upload episode assets
- [ ] 92. Save object keys in Supabase
- [ ] 93. Return signed download URLs
- [ ] 94. Create `podcast_episodes` row
- [ ] 95. Mark job as `completed`

---

## Phase 9 — Production Job Processing

- [ ] 96. Create QStash account
- [ ] 97. Add QStash environment variables
- [ ] 98. Implement production enqueue logic
- [ ] 99. Implement internal process endpoint
- [ ] 100. Verify QStash signatures
- [ ] 101. Add idempotency protection
- [ ] 102. Add retry handling
- [ ] 103. Add maximum processing duration
- [ ] 104. Add dead-letter or manual retry strategy

---

## Phase 10 — Authentication and Ownership

- [ ] 105. Connect Supabase Auth
- [ ] 106. Validate Supabase JWTs in FastAPI
- [ ] 107. Attach authenticated `user_id` to jobs
- [ ] 108. Enforce job ownership
- [ ] 109. Enforce episode ownership
- [ ] 110. Test cross-user access denial
- [ ] 111. Confirm the iOS app uses only the anon key

---

## Phase 11 — Deployment

- [ ] 112. Create Dockerfile
- [ ] 113. Create `render.yaml`
- [ ] 114. Push repository to GitHub
- [ ] 115. Create Render Blueprint
- [ ] 116. Add production environment variables
- [ ] 117. Deploy the API
- [ ] 118. Confirm `/health`
- [ ] 119. Update `PUBLIC_API_URL`
- [ ] 120. Confirm job creation in production
- [ ] 121. Confirm QStash processing in production

---

## Phase 12 — CI, Monitoring, and Hardening

- [ ] 122. Add GitHub Actions
- [ ] 123. Run tests on pull requests
- [ ] 124. Add Sentry
- [ ] 125. Add structured logging
- [ ] 126. Add request IDs
- [ ] 127. Add rate limiting
- [ ] 128. Add cost logging per job
- [ ] 129. Add model usage limits
- [ ] 130. Add alerts for repeated failures
- [ ] 131. Add cleanup for temporary research artifacts
- [ ] 132. Add README setup instructions
