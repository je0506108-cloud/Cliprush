"""
ClipRush backend starter
- FastAPI API for URL classification, job creation, local authorized uploads, and clip processing.
- Link jobs intentionally do NOT scrape or download videos from third-party platforms.
- To process a link, implement a platform-approved provider that returns media the user is authorized to process.
"""
from __future__ import annotations

import os
import re
import uuid
import shutil
import subprocess
from pathlib import Path
from datetime import datetime, timezone, date
from typing import Optional
from urllib.parse import urlparse, parse_qs

from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

APP_NAME = "ClipRush API"
DATA_DIR = Path(os.getenv("CLIPRUSH_DATA_DIR", "./data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
for folder in (UPLOAD_DIR, OUTPUT_DIR):
    folder.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_MB = int(os.getenv("CLIPRUSH_MAX_UPLOAD_MB", "500"))
DAILY_LIMIT = int(os.getenv("CLIPRUSH_DAILY_LIMIT", "20"))

app = FastAPI(title=APP_NAME, version="0.1.0",
              description="Starter API. Configure HTTPS hosting and an approved media provider before public launch.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
    "https://je0506108-cloud.github.io",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# In-memory demo storage only. Replace with a persistent database before multi-user deployment.
JOBS: dict[str, dict] = {}
DAILY_SUCCESS: dict[str, dict[str, int]] = {}

class LinkRequest(BaseModel):
    url: HttpUrl
    user_id: str = "demo-user"

class JobResponse(BaseModel):
    job_id: str
    status: str
    platform: str
    message: str
    preview_url: Optional[str] = None

def classify_url(raw_url: str) -> dict:
    p = urlparse(raw_url)
    host = (p.hostname or "").lower().removeprefix("www.")
    platform = "unknown"
    video_id = None
    preview_url = None
    if host in {"youtube.com", "m.youtube.com", "youtu.be", "youtube-nocookie.com"} or host.endswith(".youtube.com"):
        platform = "youtube"
        if host == "youtu.be":
            video_id = p.path.strip("/").split("/")[0] or None
        elif p.path == "/watch":
            video_id = parse_qs(p.query).get("v", [None])[0]
        else:
            m = re.match(r"^/(?:shorts|embed|live)/([^/?]+)", p.path)
            video_id = m.group(1) if m else None
        if video_id and re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
            preview_url = f"https://www.youtube-nocookie.com/embed/{video_id}"
        else:
            video_id = None
    elif host in {"twitch.tv", "m.twitch.tv", "clips.twitch.tv"} or host.endswith(".twitch.tv"):
        platform = "twitch"
        parts = [x for x in p.path.split("/") if x]
        if host == "clips.twitch.tv" and parts:
            video_id = parts[0]
        elif len(parts) >= 2 and parts[0].lower() == "clip":
            video_id = parts[1]
        if video_id:
            preview_url = f"https://clips.twitch.tv/embed?clip={video_id}&parent=YOUR_HTTPS_DOMAIN"
    elif host in {"tiktok.com", "m.tiktok.com", "vm.tiktok.com"} or host.endswith(".tiktok.com"):
        platform = "tiktok"
    elif host in {"kick.com", "m.kick.com"} or host.endswith(".kick.com"):
        platform = "kick"
    elif host == "instagram.com" or host.endswith(".instagram.com"):
        platform = "instagram"
    elif host == "facebook.com" or host.endswith(".facebook.com") or host == "fb.watch":
        platform = "facebook"
    elif host in {"x.com", "twitter.com", "mobile.twitter.com"} or host.endswith(".x.com"):
        platform = "x"
    elif host == "vimeo.com" or host.endswith(".vimeo.com"):
        platform = "vimeo"
    elif host == "streamable.com" or host.endswith(".streamable.com"):
        platform = "streamable"
    elif host == "rumble.com" or host.endswith(".rumble.com"):
        platform = "rumble"
    return {"platform": platform, "video_id": video_id, "preview_url": preview_url, "host": host}

@app.get("/")
def root():
    return {"name": APP_NAME, "status": "starter", "docs": "/docs",
            "note": "This API classifies links and accepts uploads. Link-to-clip needs an approved media provider."}

@app.get("/health")
def health():
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat(), "ffmpeg_available": shutil.which("ffmpeg") is not None}

@app.post("/v1/links/check")
def check_link(request: LinkRequest):
    info = classify_url(str(request.url))
    if info["platform"] == "unknown":
        raise HTTPException(status_code=400, detail="Unsupported link domain.")
    return {
        "valid": True,
        **info,
        "can_process_automatically": False,
        "message": (
            "Recognized link. A platform-approved media provider is not configured yet."
            if info["platform"] != "unknown" else "Unsupported source."
        )
    }

@app.post("/v1/jobs/from-link", response_model=JobResponse)
def create_link_job(request: LinkRequest):
    info = classify_url(str(request.url))
    if info["platform"] == "unknown":
        raise HTTPException(status_code=400, detail="Unsupported link domain.")
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {
        "job_id": job_id, "status": "blocked_provider_not_configured",
        "user_id": request.user_id, "source_url": str(request.url),
        "platform": info["platform"], "created_at": datetime.now(timezone.utc).isoformat(),
        "message": "Link recognized, but no approved media-access provider is configured. No video was downloaded."
    }
    return JobResponse(job_id=job_id, status=JOBS[job_id]["status"],
                       platform=info["platform"], message=JOBS[job_id]["message"],
                       preview_url=info["preview_url"])

@app.post("/v1/uploads", response_model=JobResponse)
async def upload_video(user_id: str = "demo-user", file: UploadFile = File(...)):
    allowed = {"video/mp4", "video/webm", "video/quicktime", "video/x-matroska"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=415, detail="Upload an MP4, WebM, MOV, or MKV video.")
    job_id = uuid.uuid4().hex[:12]
    suffix = Path(file.filename or "upload.mp4").suffix.lower()
    if suffix not in {".mp4", ".webm", ".mov", ".mkv"}:
        suffix = ".mp4"
    dest = UPLOAD_DIR / f"{job_id}{suffix}"
    total = 0
    with dest.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_UPLOAD_MB * 1024 * 1024:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail=f"Upload exceeds {MAX_UPLOAD_MB} MB limit.")
            out.write(chunk)
    JOBS[job_id] = {
        "job_id": job_id, "status": "uploaded",
        "user_id": user_id, "source_path": str(dest),
        "platform": "upload", "created_at": datetime.now(timezone.utc).isoformat(),
        "message": "Upload received. Call /v1/jobs/{job_id}/process to create a basic clip."
    }
    return JobResponse(job_id=job_id, status="uploaded", platform="upload",
                       message=JOBS[job_id]["message"])

def process_clip(job_id: str, start_seconds: float, duration_seconds: float):
    job = JOBS[job_id]
    source = Path(job["source_path"])
    output = OUTPUT_DIR / f"{job_id}.mp4"
    # Uses FFmpeg stream-copy for a fast starter clip; keyframe alignment may make start time approximate.
    cmd = [
        "ffmpeg", "-y", "-ss", str(start_seconds), "-i", str(source),
        "-t", str(duration_seconds), "-c", "copy", "-movflags", "+faststart", str(output)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if result.returncode != 0 or not output.exists() or output.stat().st_size == 0:
            job["status"] = "failed"
            job["message"] = "FFmpeg could not create this clip. Check the uploaded file and FFmpeg installation."
            job["error"] = result.stderr[-1200:]
            return
        job["status"] = "completed"
        job["output_path"] = str(output)
        job["message"] = "Clip exported."
    except Exception as exc:
        job["status"] = "failed"
        job["message"] = f"Processing failed: {type(exc).__name__}"

@app.post("/v1/jobs/{job_id}/process")
def process_uploaded_job(job_id: str, start_seconds: float = 0, duration_seconds: float = 30):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.get("platform") != "upload" or not job.get("source_path"):
        raise HTTPException(status_code=400, detail="This starter endpoint processes uploaded videos only. Link jobs need an approved provider.")
    if shutil.which("ffmpeg") is None:
        raise HTTPException(status_code=503, detail="FFmpeg is not installed on the server.")
    if start_seconds < 0 or duration_seconds < 1 or duration_seconds > 180:
        raise HTTPException(status_code=400, detail="Use start_seconds >= 0 and duration_seconds from 1 to 180.")
    process_clip(job_id, start_seconds, duration_seconds)
    return job

@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    public = {k: v for k, v in job.items() if k not in {"source_path", "output_path", "error"}}
    if job.get("status") == "completed":
        public["download_endpoint"] = f"/v1/jobs/{job_id}/download"
    if job.get("error"):
        public["error"] = job["error"]
    return public

@app.get("/v1/jobs/{job_id}/download")
def download_clip(job_id: str):
    from fastapi.responses import FileResponse
    job = JOBS.get(job_id)
    if not job or job.get("status") != "completed" or not job.get("output_path"):
        raise HTTPException(status_code=404, detail="Completed clip not found.")
    path = Path(job["output_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Output file no longer exists.")
    return FileResponse(path, media_type="video/mp4", filename=f"cliprush-{job_id}.mp4")
