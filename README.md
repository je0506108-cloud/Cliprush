# ClipRush backend starter (v0.1)

This is a real starter API codebase, not a deployed service.

## What it does
- `GET /health`: health check and FFmpeg availability.
- `POST /v1/links/check`: classifies supported source URLs and extracts common YouTube/Twitch IDs.
- `POST /v1/jobs/from-link`: creates an explicit blocked job until an approved provider is configured; it does not pretend to download the video.
- `POST /v1/uploads`: accepts video uploads.
- `POST /v1/jobs/{job_id}/process?start_seconds=0&duration_seconds=30`: exports a basic MP4 segment from an uploaded file using FFmpeg.
- `GET /v1/jobs/{job_id}` and `/download`: inspect/download a completed upload-based clip.

## Run locally
Requires Python 3.11+ and FFmpeg installed.
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
Open `http://127.0.0.1:8000/docs` to test the API.

## Important unfinished items before real public use
1. Configure a real HTTPS host and `CLIPRUSH_ALLOWED_ORIGINS`.
2. Add a platform-approved provider for each source; provider must return a media file or stream the user is authorized to process. Do not scrape or bypass access restrictions.
3. Replace in-memory `JOBS` and `DAILY_SUCCESS` with a persistent database and queue.
4. Implement authentication and server-enforced 20-successful-clips-per-user-per-day quota.
5. Add highlight detection, caption rendering, storage cleanup, rate limiting, upload scanning, and cost controls.
6. Add tests and monitoring. This starter is not production-ready.

The link endpoint intentionally returns `blocked_provider_not_configured` until an approved media-access integration is implemented. Twitch embed preview URLs need the deployed HTTPS domain in the `parent` parameter.
