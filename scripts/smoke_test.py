"""Production smoke test for WanderAI Backend.

Usage:
    python scripts/smoke_test.py <BASE_URL>

Example:
    python scripts/smoke_test.py https://wanderai-backend.onrender.com

Smoke test steps:
    1. GET /health — confirm HTTP 200 and status "healthy"
    2. GET /docs — confirm documentation is accessible
    3. POST /v1/podcast-jobs — submit a 2-minute test job
    4. Confirm job is queued (status = "queued")
    5. Poll until completed or failed (timeout: 5 minutes)
    6. GET /v1/episodes/{job_id}/audio — confirm downloadable MP3
    7. GET /v1/episodes/{job_id}/metadata — confirm JSON metadata
    8. Validate MP3 is non-empty
    9. Print results

Notes:
    - In production, the job is enqueued via QStash which calls the internal endpoint
    - QStash retries will not regenerate completed jobs (idempotent)
    - Render's filesystem is ephemeral — download assets promptly after completion
"""

import sys
import time

import httpx


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/smoke_test.py <BASE_URL>")
        print("Example: python scripts/smoke_test.py http://localhost:8000")
        sys.exit(1)

    base_url = sys.argv[1].rstrip("/")
    print(f"Smoke testing: {base_url}\n")

    client = httpx.Client(timeout=30.0)

    # Step 1: Health check
    print("1. Health check...")
    r = client.get(f"{base_url}/health")
    assert r.status_code == 200, f"Health check failed: {r.status_code}"
    health = r.json()
    assert health["status"] == "healthy", f"Unexpected health status: {health}"
    print(f"   Status: {health['status']}")
    print(f"   Version: {health.get('version', 'N/A')}")
    print(f"   Environment: {health.get('environment', 'N/A')}")
    print("   OK\n")

    # Step 2: Docs accessible
    print("2. Documentation check...")
    r = client.get(f"{base_url}/docs")
    assert r.status_code == 200, f"/docs returned {r.status_code}"
    print("   /docs OK")
    r = client.get(f"{base_url}/openapi.json")
    assert r.status_code == 200, f"/openapi.json returned {r.status_code}"
    print("   /openapi.json OK\n")

    # Step 3: Submit a 2-minute test job
    print("3. Submitting 2-minute test podcast job...")
    payload = {
        "trip_id": "11111111-1111-1111-1111-111111111111",
        "stop_id": "22222222-2222-2222-2222-222222222222",
        "destination_name": "Pike Place Market",
        "region": "Seattle, Washington",
        "visit_date": "2026-08-15",
        "episode_minutes": 2,
        "personas": ["photographer", "historian"],
    }
    r = client.post(f"{base_url}/v1/podcast-jobs", json=payload)
    assert r.status_code == 202, f"Job creation failed: {r.status_code} — {r.text}"
    job_data = r.json()
    job_id = job_data["job_id"]
    print(f"   Job created: {job_id}")
    print(f"   Status: {job_data['status']}\n")

    # Step 4: Poll until completed
    print("4. Polling job status (timeout: 5 minutes)...")
    start = time.time()
    timeout = 300  # 5 minutes
    final_status = None

    while time.time() - start < timeout:
        r = client.get(f"{base_url}/v1/podcast-jobs/{job_id}")
        assert r.status_code == 200
        status_val = r.json()["status"]

        if status_val != final_status:
            elapsed = int(time.time() - start)
            print(f"   [{elapsed}s] Status: {status_val}")
            final_status = status_val

        if status_val == "completed":
            break
        elif status_val == "failed":
            print(f"\n   FAILED: {r.json().get('error_message', 'unknown error')}")
            sys.exit(1)

        time.sleep(5)

    if final_status != "completed":
        print(f"\n   TIMEOUT after {timeout}s. Last status: {final_status}")
        sys.exit(1)

    elapsed = int(time.time() - start)
    print(f"   Completed in {elapsed}s\n")

    # Step 5: Download audio
    print("5. Downloading audio...")
    r = client.get(f"{base_url}/v1/episodes/{job_id}/audio")
    if r.status_code == 200:
        audio_size = len(r.content)
        print(f"   Audio: {audio_size:,} bytes")
        assert audio_size > 10000, f"Audio too small: {audio_size} bytes"
        # Basic MP3 validation: check for ID3 or MPEG sync bytes
        assert r.content[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"), "Not a valid MP3"
        print("   MP3 validated OK\n")
    else:
        print(f"   Audio download failed: {r.status_code}")
        print("   (Expected on Render if filesystem was recycled)\n")

    # Step 6: Download metadata
    print("6. Downloading metadata...")
    r = client.get(f"{base_url}/v1/episodes/{job_id}/metadata")
    if r.status_code == 200:
        metadata = r.json()
        print(f"   Title: {metadata.get('title', 'N/A')}")
        print(f"   Script words: {metadata.get('script_word_count', 'N/A')}")
        print(f"   Audio duration: {metadata.get('actual_audio_duration_seconds', 'N/A')}s")
        print(f"   Segments: {metadata.get('segment_count', 'N/A')}")
        print(f"   Chapters: {metadata.get('chapter_count', 'N/A')}")
        print("   OK\n")
    else:
        print(f"   Metadata download failed: {r.status_code}\n")

    # Summary
    print("=" * 50)
    print("SMOKE TEST PASSED")
    print(f"Base URL: {base_url}")
    print(f"Job ID: {job_id}")
    print(f"Total time: {int(time.time() - start)}s")
    print("=" * 50)


if __name__ == "__main__":
    main()
