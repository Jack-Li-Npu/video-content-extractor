"""Retrieve one Douyin video's captions after manual browser verification.

No signature generation, challenge solving, private desktop code, or cookie export.
The browser owns login. Only a matching video's metadata is retained by the job.
"""

import json
import logging
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass, field
from itertools import zip_longest
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import requests
from playwright.sync_api import Error as BrowserError

from .douyin_browser import (
    browser_channel,
    browser_failure,
    install_caption_media_guard,
    read_caption_text,
    refresh_video_metadata,
)
from .douyin_captions import caption_records, discover_caption_tracks
from .speech_service import check_setup, probe_video, transcribe_job
from .transcript_service import ExtractionError

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
TERMINAL = {"completed", "failed", "cancelled"}
MEDIA_DOMAINS = (
    "douyinvod.com",
    "douyin.com",
    "snssdk.com",
    "toutiaovod.com",
    "bytecdn.cn",
    "bytecdn.com",
    "ibytedtos.com",
)


def normalize_url(value):
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except (AttributeError, TypeError, ValueError) as exc:
        raise ExtractionError("invalid_url", "Paste a valid Douyin video link.", 400) from exc
    if parsed.scheme != "https" or parsed.hostname not in {"douyin.com", "www.douyin.com", "v.douyin.com"}:
        raise ExtractionError("invalid_url", "Paste an HTTPS Douyin video or share link.", 400)
    if parsed.username or parsed.password or port not in (None, 443):
        raise ExtractionError("invalid_url", "This Douyin link has an invalid address.", 400)
    query = parse_qs(parsed.query)
    match = re.fullmatch(r"/video/([0-9]{16,22})/?", parsed.path)
    identity = match.group(1) if match else (query.get("modal_id") or [""])[0]
    if re.fullmatch(r"[0-9]{16,22}", identity):
        return f"https://www.douyin.com/video/{identity}", identity
    if parsed.hostname == "v.douyin.com" and re.fullmatch(r"/[A-Za-z0-9_-]{3,80}/?", parsed.path):
        return f"https://v.douyin.com{parsed.path}", ""
    raise ExtractionError("invalid_url", "Use an individual Douyin video link, not a profile or collection.", 400)


def safe_media_url(value):
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
            and bool(parsed.hostname)
            and any(parsed.hostname == domain or parsed.hostname.endswith("." + domain) for domain in MEDIA_DOMAINS)
        )
    except (TypeError, ValueError):
        return False


def safe_caption_url(value):
    """Only text-track addresses supplied by matching platform metadata."""
    if safe_media_url(value):
        return True
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
            and bool(parsed.hostname)
            and any(
                parsed.hostname == domain or parsed.hostname.endswith("." + domain)
                for domain in ("bytescm.com", "douyinstatic.com")
            )
        )
    except (TypeError, ValueError):
        return False


def find_video(data, identity, depth=0):
    if depth > 16:
        return None
    if isinstance(data, dict):
        if str(data.get("aweme_id", "")) == identity and isinstance(data.get("video"), dict):
            return data
        values = data.values()
    elif isinstance(data, list):
        values = data
    else:
        return None
    for value in values:
        match = find_video(value, identity, depth + 1)
        if match:
            return match
    return None


def inspect_video(data):
    video = data["video"]
    candidates = []
    for entry in video.get("bit_rate") or []:
        address = entry.get("play_addr") or {}
        if address.get("url_list"):
            # DASH tracks can be video-only even when packaged in an MP4
            # container. Prefer explicit progressive variants before sorting
            # for moderate resolution/bitrate within each format class.
            edge = min(address.get("width") or 9999, address.get("height") or 9999)
            format_name = str(entry.get("format") or "").lower()
            rank = 0 if format_name == "mp4" else 2 if format_name == "dash" else 1
            candidates.append((rank, abs(edge - 720), entry.get("bit_rate") or 0, address))
    candidates.sort(key=lambda row: row[:3])
    progressive = [row[3] for row in candidates if row[0] == 0]
    # Keep the primary playback variants early if smaller tracks lack audio.
    addresses = progressive[:1] + [video.get("play_addr_h264") or {}, video.get("play_addr") or {}]
    addresses += progressive[1:] + [row[3] for row in candidates if row[0] != 0]
    groups = list(
        dict.fromkeys(
            tuple(url for url in address.get("url_list", []) if safe_media_url(url)) for address in addresses
        )
    )
    # Try one mirror of each variant before a second mirror of any variant.
    # Otherwise the five-attempt budget can retry one silent DASH track five
    # times without ever reaching a returned variant with narration.
    urls = list(
        dict.fromkeys(url for mirrors in zip_longest(*groups) for url in mirrors if url is not None)
    )
    tracks = discover_caption_tracks(data, safe_caption_url)
    duration = float(video.get("duration") or data.get("duration") or 0) / 1000
    return {
        "platform": "douyin",
        "video_id": str(data["aweme_id"]),
        "title": str(data.get("desc") or "Douyin video"),
        "channel": str((data.get("author") or {}).get("nickname") or ""),
        "duration": duration,
        "url": f"https://www.douyin.com/video/{data['aweme_id']}",
        "tracks": tracks,
        "_media_urls": urls,
    }


@dataclass
class Job:
    id: str
    url: str
    identity: str
    allow_asr: bool
    directory: Path
    state: str = "opening_browser"
    message: str = "Opening Douyin to check this video's captions…"
    video: dict | None = None
    result: dict | None = None
    media: Path | None = None
    code: str = ""
    choice: str = ""
    audio_validated: bool = False
    manual_confirmed: bool = False
    worker_exit_code: int | None = None
    caption_probe_complete: bool = False
    blocked_media_requests: int = 0
    action: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    show_browser: threading.Event = field(default_factory=threading.Event)

    def public(self):
        video = (
            None if self.video is None else {key: value for key, value in self.video.items() if not key.startswith("_")}
        )
        if video:
            video["tracks"] = [
                {key: value for key, value in track.items() if not key.startswith("_")} for track in video["tracks"]
            ]
        return {
            "id": self.id,
            "url": self.url,
            "state": self.state,
            "message": self.message,
            "code": self.code,
            "video": video,
            "result": self.result if self.state == "completed" else None,
            "can_retry_speech": self.state == "failed"
            and self.audio_validated
            and bool(self.media and self.media.is_file()),
            "diagnostic": {"code": self.code, "worker_exit_code": self.worker_exit_code} if self.code else None,
            "allow_asr": self.allow_asr,
            "captions_only": not self.allow_asr,
            "caption_probe_complete": self.caption_probe_complete,
            "media_downloaded": bool(self.media and self.media.is_file()),
            "blocked_media_requests": self.blocked_media_requests,
        }


class DouyinManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.job = None
        self.worker = None

    def start(self, url, allow_asr=False):
        normalized, identity = normalize_url(url)
        with self.lock:
            if self.job and self.job.state not in TERMINAL:
                raise ExtractionError(
                    "douyin_busy", "A Douyin extraction is running. Complete or cancel it first.", 409
                )
            if self.job:
                shutil.rmtree(self.job.directory, ignore_errors=True)
            job = Job(
                secrets.token_urlsafe(24),
                normalized,
                identity,
                allow_asr,
                Path(tempfile.mkdtemp(prefix="transcript-douyin-")),
            )
            os.chmod(job.directory, 0o700)
            self.job = job
            self.worker = threading.Thread(target=self._run, args=(job,), daemon=True)
            self.worker.start()
            return job.public()

    def get(self, identity):
        if not self.job or identity != self.job.id:
            raise ExtractionError("job_missing", "This extraction has ended. Start a new one.", 404)
        return self.job

    def confirm(self, identity, track_id=""):
        job = self.get(identity)
        if job.state not in {"waiting_verification", "waiting_track"}:
            raise ExtractionError("job_not_waiting", "This extraction is not waiting for confirmation.", 409)
        if job.state == "waiting_verification":
            job.manual_confirmed = True
            job.state, job.message = "reading_video", "Verification acknowledged. Checking this video's captions…"
        else:
            if not any(track["id"] == track_id for track in job.video["tracks"]):
                raise ExtractionError("caption_selection_required", "Choose an available caption track.", 422)
            job.choice = track_id
            job.state, job.message = "downloading_captions", "Retrieving the selected caption track…"
        job.action.set()
        return job.public()

    def reveal_browser(self, identity):
        job = self.get(identity)
        if job.state != "waiting_verification":
            raise ExtractionError("job_not_waiting", "The verification window is not needed at this step.", 409)
        job.show_browser.set()
        return job.public()

    def retry_speech(self, identity):
        with self.lock:
            job = self.get(identity)
            if not job.public()["can_retry_speech"]:
                raise ExtractionError(
                    "retry_unavailable", "This job has no acquired audio to reuse. Retry extraction.", 409
                )
            previous_worker = self.worker
        # Workers publish completion/failure under the same lock. Never wait
        # for their exit while holding it, then recheck state after the wait.
        if previous_worker:
            previous_worker.join(timeout=2)
        with self.lock:
            job = self.get(identity)
            if (
                not job.public()["can_retry_speech"]
                or self.worker is not previous_worker
                or (previous_worker and previous_worker.is_alive())
            ):
                raise ExtractionError(
                    "retry_unavailable",
                    "The previous speech task is still stopping or has changed. Retry shortly.",
                    409,
                )
            job.code, job.worker_exit_code = "", None
            job.cancelled.clear()
            job.state, job.message = "transcribing", "Retrying speech recognition on the acquired video…"
            self.worker = threading.Thread(target=self._retry_speech, args=(job,), daemon=True)
            self.worker.start()
            return job.public()

    def _retry_speech(self, job):
        try:
            self._speech(job)
        except ExtractionError as exc:
            self._fail(job, exc)
        except Exception as exc:  # noqa: BLE001 -- worker boundary, do not expose raw worker output
            LOG.warning("Speech retry failed (%s)", type(exc).__name__)
            self._fail(
                job,
                ExtractionError(
                    "speech_failed",
                    "Speech recognition failed. Retry speech recognition; the acquired video is kept.",
                    422,
                ),
            )

    @staticmethod
    def _check_cancelled(job):
        if job.cancelled.is_set():
            raise ExtractionError("cancelled", "Extraction cancelled.", 409)

    def _complete(self, job, lines, source, provider, language, track_id):
        self._check_cancelled(job)
        normalized = []
        for original in lines:
            self._check_cancelled(job)
            cue = dict(original)
            total = int(cue["start"])
            cue["seconds"] = total
            cue["timestamp"] = (
                f"{total // 3600:02}:{total // 60 % 60:02}:{total % 60:02}"
                if total >= 3600
                else f"{total // 60:02}:{total % 60:02}"
            )
            normalized.append(cue)
        result = {
            **{key: value for key, value in job.video.items() if not key.startswith("_") and key != "tracks"},
            "success": True,
            "source": source,
            "provider": provider,
            "language": language,
            "track_id": track_id,
            "generated_at": time.time(),
            "transcript_lines": normalized,
            "plain_text": "\n".join(cue["text"] for cue in normalized),
            "acquisition_provider": "douyin-browser-captions" if source == "platform" else "playwright-browser",
            "manual_verification_confirmed": job.manual_confirmed,
            "media_downloaded": job.media is not None,
            "captions_only": not job.allow_asr,
            "blocked_media_requests": job.blocked_media_requests,
        }
        with self.lock:
            self._check_cancelled(job)
            job.result = result
            job.state, job.message = (
                "completed",
                "Transcript and Codex export ready. Speech timings are estimates."
                if source == "asr"
                else "Transcript and Codex export ready.",
            )

    def _speech(self, job):
        self._check_cancelled(job)
        recognized = transcribe_job(job)
        lines = [
            {
                "start": cue["start"],
                "duration": cue["duration"],
                "text": cue["text"],
                "quality_flags": cue["quality_flags"],
            }
            for cue in recognized["segments"]
        ]
        metadata = recognized["metadata"]
        self._complete(
            job, lines, "asr", metadata.get("provider", "local-whisper"), metadata["language"], "asr:original"
        )

    def _fail(self, job, exc):
        with self.lock:
            if job.cancelled.is_set():
                exc = ExtractionError("cancelled", "Extraction cancelled.", 409)
            job.result = None
            job.code, job.message = exc.code, str(exc)
            job.state = "cancelled" if exc.code == "cancelled" else "failed"
        # Keep only the current failed job's acquired video/log for a direct
        # speech retry. Starting another job or normal shutdown clears it.
        if job.state == "cancelled" or not job.audio_validated:
            shutil.rmtree(job.directory, ignore_errors=True)

    def cancel(self, identity):
        with self.lock:
            job = self.get(identity)
            if job.state not in TERMINAL:
                job.cancelled.set()
                job.action.set()
            return job.public()

    def shutdown(self):
        with self.lock:
            job, worker = self.job, self.worker
            if job and job.state not in TERMINAL:
                job.cancelled.set()
                job.action.set()
        if worker:
            worker.join(timeout=35)
        if job:
            shutil.rmtree(job.directory, ignore_errors=True)

    def _wait(self, job, page, timeout=900):
        deadline = time.monotonic() + timeout
        while not job.action.is_set():
            if job.cancelled.is_set() or time.monotonic() >= deadline or page.is_closed():
                raise ExtractionError(
                    "cancelled", "Verification was cancelled or timed out. Start again when ready.", 409
                )
            page.wait_for_timeout(250)
            if job.show_browser.is_set():
                job.show_browser.clear()
                page.bring_to_front()
        job.action.clear()
        if job.cancelled.is_set():
            raise ExtractionError("cancelled", "Extraction cancelled.", 409)

    def _download(self, job, context, url, destination, limit):
        if not safe_media_url(url):
            raise ExtractionError("media_address_rejected", "Douyin returned an unsupported media host.", 422)
        session = requests.Session()
        session.trust_env = False
        for cookie in context.cookies([url]):
            session.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie["path"])
        try:
            for _ in range(6):
                if not safe_media_url(url):
                    raise ExtractionError("media_address_rejected", "Douyin returned an unsupported media host.", 422)
                with session.get(
                    url, headers={"Referer": job.url}, stream=True, allow_redirects=False, timeout=(15, 30)
                ) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get("Location", ""))
                        continue
                    if response.status_code != 200:
                        raise ExtractionError(
                            "media_request_failed",
                            "The media server refused this download. Complete any Douyin verification and retry.",
                            422,
                        )
                    if "text/html" in response.headers.get("Content-Type", ""):
                        raise ExtractionError(
                            "media_request_failed",
                            "Douyin returned a verification page instead of media. Check its browser window.",
                            422,
                        )
                    count = 0
                    with destination.open("wb") as stream:
                        for chunk in response.iter_content(1024 * 1024):
                            if job.cancelled.is_set():
                                raise ExtractionError("cancelled", "Extraction cancelled.", 409)
                            count += len(chunk)
                            if count > limit:
                                raise ExtractionError(
                                    "media_too_large",
                                    "This media exceeds the local download limit. Import a smaller copy instead.",
                                    422,
                                )
                            stream.write(chunk)
                            job.message = f"Downloading the source video… {count // (1024 * 1024)} MB"
                    if count == 0:
                        raise ExtractionError("media_empty", "The media download was empty. Retry the link.", 422)
                    return
            raise ExtractionError(
                "media_redirect_failed", "The media server redirected too many times. Retry the link.", 422
            )
        except requests.RequestException as exc:
            raise ExtractionError(
                "media_connection_failed", "The media connection failed. Check your connection and retry.", 502
            ) from exc
        finally:
            session.close()

    def _run(self, job):
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as playwright:
                profile = Path(
                    os.environ.get(
                        "DOUYIN_BROWSER_PROFILE",
                        str(Path.home() / ".local/share/efficient-content-extractor/douyin-browser"),
                    )
                )
                profile.mkdir(parents=True, exist_ok=True)
                os.chmod(profile, 0o700)
                context = playwright.chromium.launch_persistent_context(
                    str(profile), headless=False, channel=browser_channel(), service_workers="block"
                )
                media_guard = {"blocked_media_requests": 0}
                context_closed = False
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    captured = []
                    caption_urls = set()
                    media_urls = set()
                    observed_media_urls = []
                    media_guard = install_caption_media_guard(context, caption_urls, media_urls)

                    def retain(match):
                        captured[:] = [match]
                        caption_urls.update(
                            track["_url"]
                            for track in discover_caption_tracks(match, safe_caption_url)
                            if "_url" in track
                        )
                        for address in inspect_video(match)["_media_urls"]:
                            if address not in media_urls:
                                observed_media_urls.append(address)
                                media_urls.add(address)

                    def observe(response):
                        address = urlsplit(response.url)
                        if address.hostname not in {"www.douyin.com", "douyin.com"} or not address.path.startswith(
                            "/aweme/"
                        ):
                            return
                        try:
                            if (
                                "json" not in response.headers.get("content-type", "")
                                or int(response.headers.get("content-length") or 0) > 8 * 1024 * 1024
                            ):
                                return
                            body = response.body()
                            # Passive metadata is already loaded by the site;
                            # only retained data is bounded here. Active refresh
                            # and caption fetches enforce streaming read limits.
                            if len(body) > 8 * 1024 * 1024:
                                return
                            data = json.loads(body)
                            match = find_video(data, job.identity) if job.identity else None
                            if match:
                                retain(match)
                        except (BrowserError, ValueError, TypeError, KeyError) as exc:
                            LOG.debug("Skipped unreadable playback response (%s)", type(exc).__name__)

                    context.on("response", observe)
                    page.goto(job.url, wait_until="domcontentloaded", timeout=60000)
                    if not job.identity:
                        job.url, job.identity = normalize_url(page.url)
                    job.state, job.message = "reading_video", "Checking available playback data for this video…"
                    self._read_metadata(job, page, captured, retain)
                    if not captured:
                        job.state = "waiting_verification"
                        job.message = (
                            "Complete Douyin's requested login or verification, then click I’ve verified, continue."
                        )
                        page.bring_to_front()
                        self._wait(job, page)
                        job.state, job.message = (
                            "reading_video",
                            "Verification acknowledged. Checking this video's captions…",
                        )
                        page.goto(job.url, wait_until="domcontentloaded", timeout=60000)
                        self._read_metadata(job, page, captured, retain)
                    if not captured:
                        raise ExtractionError(
                            "douyin_verification_blocked",
                            "Douyin still did not return this video's playback data. Check its browser page, then retry extraction.",
                            422,
                        )
                    job.video = inspect_video(captured[0])
                    job.state, job.message = (
                        "probing_captions",
                        "Looking for existing timed captions; media stays blocked…",
                    )
                    if not job.video["tracks"]:
                        # Refresh only the matching video's metadata in its normal
                        # page context. No signature generation or generic proxy.
                        refreshed = refresh_video_metadata(page, job.identity, find_video)
                        if refreshed:
                            retain(refreshed)
                            job.video = inspect_video(refreshed)
                    job.caption_probe_complete = True
                    self._check_cancelled(job)
                    tracks = job.video["tracks"]
                    if len(tracks) > 1 and not job.choice:
                        job.state, job.message = "waiting_track", "Choose an available caption track, then continue."
                        self._wait(job, page)
                    selected = next(
                        (track for track in tracks if track["id"] == job.choice), tracks[0] if tracks else None
                    )
                    if job.choice and (not selected or selected["id"] != job.choice):
                        raise ExtractionError(
                            "caption_selection_required",
                            "The selected caption track is no longer available. Retry extraction.",
                            422,
                        )
                    if selected:
                        job.state = "downloading_captions"
                        job.message = "Retrieving the existing caption track…"
                        text = selected.get("_inline")
                        if text is None:
                            text = read_caption_text(job, page, selected["_url"], safe_caption_url)
                        self._check_cancelled(job)
                        lines = caption_records(text)
                        self._check_cancelled(job)
                        source, provider, language = "platform", "douyin-browser", selected["language"]
                        track_id = selected["id"]
                    else:
                        if not job.allow_asr:
                            raise ExtractionError(
                                "captions_not_found",
                                "No retrievable timed captions were exposed for this video. No media was downloaded. "
                                "You can opt in to local speech recognition and retry, or use a video with captions.",
                                422,
                            )
                        check_setup()
                        job.state = "downloading_media"
                        job.message = "Waiting briefly for this video's normal playback metadata…"
                        addresses = self._wait_for_media_urls(job, page, observed_media_urls)
                        job.message = "Downloading the source for local speech recognition…"
                        job.media = job.directory / "source.mp4"
                        last_error = None
                        for address in addresses[:5]:
                            try:
                                self._download(job, context, address, job.media, 1024 * 1024 * 1024)
                                duration = probe_video(job.media)
                                job.audio_validated = True
                                job.video["duration"] = duration
                                last_error = None
                                break
                            except ExtractionError as exc:
                                if exc.code == "cancelled":
                                    raise
                                last_error = exc
                        if last_error:
                            raise last_error
                        if not job.audio_validated:
                            raise ExtractionError(
                                "media_unavailable",
                                "Douyin returned no usable video with audio. Check playback in its window, then retry extraction.",
                                422,
                            )
                        job.blocked_media_requests = media_guard["blocked_media_requests"]
                        context.close()
                        context_closed = True
                        self._speech(job)
                    job.blocked_media_requests = media_guard["blocked_media_requests"]
                    if selected:
                        self._complete(job, lines, source, provider, language, track_id)
                finally:
                    job.blocked_media_requests = media_guard["blocked_media_requests"]
                    if not context_closed:
                        try:
                            context.close()
                        except BrowserError:
                            pass
        except ExtractionError as exc:
            self._fail(job, exc)
        except BrowserError as exc:
            LOG.warning("Douyin browser failed (%s)", type(exc).__name__)
            self._fail(job, browser_failure(exc))
        except Exception as exc:  # noqa: BLE001 -- worker boundary: never expose URLs or browser secrets in errors
            LOG.warning("Douyin browser worker failed (%s)", type(exc).__name__)
            self._fail(
                job,
                ExtractionError(
                    "browser_failed",
                    "The Douyin browser could not complete extraction. Check its window and your connection, then retry extraction.",
                    422,
                ),
            )

    def _wait_for_media_urls(self, job, page, observed_media_urls):
        """Allow late matching playback responses, with no new requests or host exemptions."""
        for attempt in range(41):
            self._check_cancelled(job)
            addresses = list(
                dict.fromkeys(
                    address for address in [*job.video["_media_urls"], *observed_media_urls] if safe_media_url(address)
                )
            )
            if addresses:
                return addresses
            if attempt < 40:
                # Playwright dispatches its ordinary response callbacks while
                # waiting. Hydration/refresh data can arrive before playback
                # variants; only retain() for this exact video supplies URLs.
                page.wait_for_timeout(250)
        return []

    def _read_metadata(self, job, page, captured, retain):
        """Observe matching JSON/hydration, with a restricted page-context refresh."""
        for attempt in range(60):
            self._check_cancelled(job)
            if captured:
                return
            page.wait_for_timeout(250)
            self._check_cancelled(job)
            for script in page.locator('script[id="RENDER_DATA"]').all_text_contents():
                try:
                    if len(script.encode("utf-8")) > 8 * 1024 * 1024:
                        continue
                    match = find_video(json.loads(unquote(script)), job.identity)
                    if match:
                        self._check_cancelled(job)
                        retain(match)
                        return
                except (ValueError, TypeError, UnicodeError):
                    pass
            if attempt in {3, 31}:
                match = refresh_video_metadata(page, job.identity, find_video)
                self._check_cancelled(job)
                if match:
                    retain(match)
                    return


manager = DouyinManager()
