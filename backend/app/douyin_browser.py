"""Restricted metadata/caption requests in the user's verified official-page context.

The platform's normal page owns authentication and request hooks. This module does
not generate signatures, export cookies, solve checks, or proxy arbitrary requests.
"""

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as BrowserError

from .transcript_service import ExtractionError

METADATA_LIMIT = 8 * 1024 * 1024
CAPTION_LIMIT = 10 * 1024 * 1024
OFFICIAL_HOSTS = {"www.douyin.com", "douyin.com"}


def browser_channel():
    """Choose a browser without using or inspecting a personal browser profile."""
    configured = os.environ.get("DOUYIN_BROWSER_CHANNEL", "auto").strip().lower()
    if configured not in {"auto", "chrome", "chromium", "msedge"}:
        raise ExtractionError(
            "browser_setup_required",
            "Set DOUYIN_BROWSER_CHANNEL to auto, chrome, chromium, or msedge, then restart the extractor.",
            422,
        )
    if configured != "auto":
        return None if configured == "chromium" else configured
    if sys.platform == "darwin" and Path("/Applications/Google Chrome.app").is_dir():
        return "chrome"
    if sys.platform == "win32":
        for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            location = os.environ.get(name)
            if location and (Path(location) / "Google/Chrome/Application/chrome.exe").is_file():
                return "chrome"
    # Playwright's installed Chromium is the default on other systems and when
    # Chrome is absent. Keep the dedicated profile path stable across upgrades.
    return None


def browser_failure(error):
    """Map known Playwright errors to instructions, never returning raw URLs/logs."""
    message = str(error).lower()
    if any(
        value in message
        for value in ("processsingleton", "singletonlock", "profile is in use", "user data directory is already in use")
    ):
        return ExtractionError(
            "browser_profile_in_use",
            "The dedicated Douyin browser profile is already in use. Close its other window or extractor instance, then retry.",
            422,
        )
    if "executable doesn't exist" in message or ("distribution" in message and "not found" in message):
        return ExtractionError(
            "browser_not_installed",
            "The selected Douyin browser is not installed. Run the project's browser setup, or install Chrome and set DOUYIN_BROWSER_CHANNEL=chrome, then restart.",
            422,
        )
    if "has been closed" in message or "browser closed" in message:
        return ExtractionError(
            "browser_closed",
            "The Douyin browser was closed before extraction finished. Retry and keep its window open until caption checking or media download finishes.",
            422,
        )
    if "timeout" in message:
        return ExtractionError(
            "browser_timeout",
            "The Douyin browser timed out. Check access to Douyin and your connection, then retry extraction.",
            422,
        )
    return ExtractionError(
        "browser_failed",
        "The Douyin browser could not complete extraction. Check your connection, close any stale dedicated Douyin window, then retry.",
        422,
    )


# Return bounded text rather than JSON.parse, preserving 64-bit video IDs. Fetch
# stays in the normal page context, including its existing authenticated session.
_FETCH_TEXT = """async ({url, limit}) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  let reader;
  try {
    const sameOrigin = new URL(url, location.href).origin === location.origin;
    const response = await fetch(url, {
      method: 'GET', credentials: sameOrigin ? 'include' : 'omit',
      redirect: 'error', signal: controller.signal
    });
    if (!response.ok) return {status: response.status, error: 'http'};
    const size = Number(response.headers.get('content-length') || 0);
    const type = (response.headers.get('content-type') || '').toLowerCase();
    if (size > limit) return {status: response.status, error: 'size'};
    if (/^(video|audio|image)\\//.test(type)) return {status: response.status, error: 'media'};
    reader = response.body?.getReader();
    if (!reader) return {status: response.status, error: 'empty'};
    const decoder = new TextDecoder('utf-8', {fatal: true});
    let count = 0, text = '';
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      count += value.byteLength;
      if (count > limit) return {status: response.status, error: 'size'};
      text += decoder.decode(value, {stream: true});
      if (text.includes('\\u0000')) return {status: response.status, error: 'binary'};
    }
    text += decoder.decode();
    return {status: response.status, text};
  } catch { return {status: 0, error: 'connection'}; }
  finally {
    controller.abort();
    if (reader) { try { await reader.cancel(); } catch {} }
    clearTimeout(timer);
  }
}"""


def official_page(page):
    try:
        address = urlsplit(page.url)
        return (
            address.scheme == "https"
            and address.hostname in OFFICIAL_HOSTS
            and address.port in (None, 443)
            and not address.username
            and not address.password
        )
    except (TypeError, ValueError):
        return False


def refresh_video_metadata(page, identity, find_video):
    """Read only this video's public detail operation; never a caller-selected API."""
    if not isinstance(identity, str) or not re.fullmatch(r"[0-9]{16,22}", identity) or not official_page(page):
        return None
    try:
        response = page.evaluate(
            _FETCH_TEXT,
            {"url": f"/aweme/v1/web/aweme/detail/?aweme_id={identity}", "limit": METADATA_LIMIT},
        )
        if not isinstance(response, dict) or response.get("status") != 200 or response.get("error"):
            return None
        text = response.get("text")
        if not isinstance(text, str) or len(text.encode("utf-8")) > METADATA_LIMIT:
            return None
        return find_video(json.loads(text), identity)
    except (BrowserError, ValueError, TypeError, UnicodeError):
        return None


def read_caption_text(job, page, address, url_validator):
    """Fetch a metadata-discovered caption asset into memory, with no media fallback."""
    if not url_validator(address):
        raise ExtractionError("caption_address_rejected", "This caption track has an unsupported address.", 422)
    if not official_page(page):
        raise ExtractionError("caption_request_failed", "Return to the official Douyin page, then retry captions.", 422)
    if job.cancelled.is_set():
        raise ExtractionError("cancelled", "Extraction cancelled.", 409)
    try:
        response = page.evaluate(_FETCH_TEXT, {"url": address, "limit": CAPTION_LIMIT})
    except BrowserError as exc:
        raise ExtractionError(
            "caption_request_failed", "The browser could not retrieve this caption track.", 502
        ) from exc
    if not isinstance(response, dict):
        raise ExtractionError("caption_request_failed", "The browser could not retrieve this caption track.", 502)
    if response.get("error") in {"size", "media", "binary"}:
        raise ExtractionError("captions_invalid", "The caption address returned oversized or non-text content.", 422)
    if response.get("status") != 200 or response.get("error"):
        raise ExtractionError(
            "caption_request_failed",
            "This caption track could not be read in the verified browser. Try another track or retry extraction.",
            502,
        )
    text = response.get("text")
    try:
        valid = isinstance(text, str) and 0 < len(text.encode("utf-8")) <= CAPTION_LIMIT and "\x00" not in text
    except UnicodeError:
        valid = False
    if not valid:
        raise ExtractionError("captions_invalid", "Douyin returned an empty or invalid caption file.", 422)
    if job.cancelled.is_set():
        raise ExtractionError("cancelled", "Extraction cancelled.", 409)
    return text


def install_caption_media_guard(context, caption_urls, media_urls=None):
    """Let page/login/metadata/captions load; stop player video/audio transfers.

    URL sets are updated only from validated, matching video metadata. The
    explicit STT fallback uses its own bounded downloader after this probe.
    """
    stats = {"blocked_media_requests": 0}
    media_urls = set() if media_urls is None else media_urls

    def route_request(route):
        request = route.request
        try:
            address = urlsplit(request.url)
        except ValueError:
            route.abort()
            return
        known_caption = request.url in caption_urls
        explicit_media = (
            request.resource_type == "media"
            or request.url in media_urls
            or "range" in {key.lower() for key in request.headers}
            or bool(re.search(r"\.(mp4|m4s|m3u8|mpd|ts|webm|mp3|m4a|aac|ogg|wav)$", address.path, re.IGNORECASE))
            or address.path.startswith(("/aweme/v1/play/", "/aweme/v1/playwm/"))
        )
        media_host = bool(
            address.hostname
            and any(
                address.hostname == host or address.hostname.endswith("." + host)
                for host in ("douyinvod.com", "toutiaovod.com")
            )
        )
        if explicit_media or (media_host and not known_caption):
            stats["blocked_media_requests"] += 1
            route.abort()
        else:
            route.continue_()

    context.route("**/*", route_request)
    context.add_init_script("""(() => {
      const pause = () => document.querySelectorAll('video, audio').forEach(node => {
        node.autoplay = false; node.preload = 'none'; node.pause();
      });
      document.addEventListener('play', pause, true);
      document.addEventListener('DOMContentLoaded', pause);
    })();""")
    return stats
