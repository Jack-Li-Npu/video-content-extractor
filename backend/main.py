"""Local web interface for the upstream caption extraction service."""

import asyncio
import html
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.douyin_service import manager
from app.transcript_service import ExtractionError, fetch_selected, get_video_info

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@asynccontextmanager
async def lifespan(_app):
    yield
    await asyncio.to_thread(manager.shutdown)


app = FastAPI(title="Local Video Transcript", version="2.1.0", lifespan=lifespan)
ORIGINS = {"http://127.0.0.1:8000", "http://localhost:8000", "http://127.0.0.1:5173", "http://localhost:5173"}
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])
app.add_middleware(
    CORSMiddleware, allow_origins=sorted(ORIGINS), allow_methods=["GET", "POST"], allow_headers=["Content-Type"]
)


@app.middleware("http")
async def local_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method == "POST" and origin and origin not in ORIGINS and origin != str(request.base_url).rstrip("/"):
        return JSONResponse(
            {"code": "invalid_origin", "message": "Open the app on localhost to continue."}, status_code=403
        )
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(ExtractionError)
async def extraction_error(_request: Request, exc: ExtractionError):
    return JSONResponse({"success": False, "code": exc.code, "message": str(exc)}, status_code=exc.status)


class VideoRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class ExtractRequest(VideoRequest):
    track_id: str = Field(min_length=1, max_length=100)


class DouyinRequest(VideoRequest):
    allow_asr: bool = False


class ConfirmRequest(BaseModel):
    track_id: str = Field(default="", max_length=100)


def timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


@app.get("/health")
def health():
    return {"status": "healthy", "mode": "captions-and-optional-local-speech"}


def public_job(job, request: Request):
    data = job.public()
    if data["result"] and job.media:
        data["result"] = {**data["result"], "local_viewer_url": str(request.url_for("douyin_viewer", identity=job.id))}
    return data


@app.post("/api/douyin/jobs")
def douyin_start(body: DouyinRequest, request: Request):
    started = manager.start(body.url, body.allow_asr)
    return public_job(manager.get(started["id"]), request)


@app.get("/api/douyin/current-job")
def douyin_current(request: Request):
    return {"job": public_job(manager.job, request) if manager.job else None}


@app.post("/api/douyin/jobs/{identity}/retry-speech")
def douyin_retry(identity: str, request: Request):
    manager.retry_speech(identity)
    return public_job(manager.get(identity), request)


@app.post("/api/douyin/jobs/{identity}/show-browser")
def douyin_reveal(identity: str, request: Request):
    manager.reveal_browser(identity)
    return public_job(manager.get(identity), request)


@app.get("/api/douyin/jobs/{identity}")
def douyin_status(identity: str, request: Request):
    return public_job(manager.get(identity), request)


@app.post("/api/douyin/jobs/{identity}/confirm")
def douyin_confirm(identity: str, body: ConfirmRequest, request: Request):
    manager.confirm(identity, body.track_id)
    return public_job(manager.get(identity), request)


@app.post("/api/douyin/jobs/{identity}/cancel")
def douyin_cancel(identity: str, request: Request):
    manager.cancel(identity)
    return public_job(manager.get(identity), request)


@app.get("/api/douyin/jobs/{identity}/video")
def douyin_video(identity: str):
    job = manager.get(identity)
    if job.state != "completed" or not job.media or not job.media.is_file():
        raise ExtractionError("video_missing", "This local video is no longer available. Extract the link again.", 404)
    return FileResponse(job.media, media_type="video/mp4")


@app.get("/api/douyin/jobs/{identity}/viewer")
def douyin_viewer(identity: str, request: Request):
    job = manager.get(identity)
    if job.state != "completed" or not job.media:
        raise ExtractionError("video_missing", "This local video is no longer available. Extract the link again.", 404)
    title = html.escape(job.video["title"])
    media = html.escape(str(request.url_for("douyin_video", identity=identity)), quote=True)
    return HTMLResponse(
        f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>body{{margin:0;background:#111;color:#eee;font:16px system-ui}}main{{max-width:1100px;margin:32px auto;padding:0 20px}}video{{width:100%;max-height:80vh}}h1{{font-size:20px}}p{{color:#aaa}}</style>
<main><h1>{title}</h1><video controls preload="metadata" src="{media}"></video><p>Local playback. Timestamp links seek this copy; it stays available until the next Douyin extraction or server shutdown.</p></main>
<script>const video=document.querySelector('video');function seek(){{const t=Number(new URLSearchParams(location.hash.slice(1)).get('t'));if(Number.isFinite(t)&&t>=0)video.currentTime=t;}}video.addEventListener('loadedmetadata',seek);window.addEventListener('hashchange',seek);</script></html>''',
        headers={
            "Content-Security-Policy": "default-src 'none'; media-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'"
        },
    )


@app.post("/api/video-info")
def video_info(request: VideoRequest):
    return {"success": True, **get_video_info(request.url)}


@app.post("/api/extract")
def extract(request: ExtractRequest):
    started = time.perf_counter()
    result = fetch_selected(request.url, request.track_id)
    segments = result.pop("segments")
    lines = [
        {
            "start": item.start,
            "duration": item.duration,
            "text": item.text,
            "seconds": int(item.start),
            "timestamp": timestamp(item.start),
        }
        for item in segments
    ]
    return {
        "success": True,
        **result,
        "track_id": request.track_id,
        "transcript_lines": lines,
        "plain_text": "\n".join(item.text for item in segments),
        "generated_at": time.time(),
        "extraction_ms": round((time.perf_counter() - started) * 1000),
    }


DIST = ROOT / "frontend" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")
else:

    @app.get("/")
    def setup_needed():
        return JSONResponse({"message": "Run ./setup.sh to build the web interface."}, status_code=503)
