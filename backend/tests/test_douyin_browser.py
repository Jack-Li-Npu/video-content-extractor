import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import Error as BrowserError

from app import douyin_browser
from app.douyin_browser import install_caption_media_guard, official_page, read_caption_text, refresh_video_metadata
from app.douyin_service import find_video, safe_caption_url
from app.transcript_service import ExtractionError

VIDEO = "7685972770793999667"
URL = f"https://www.douyin.com/video/{VIDEO}"
CAPTION_URL = "https://captions.douyinvod.com/track.json"


@pytest.mark.parametrize("configured, expected", [("chrome", "chrome"), ("chromium", None), ("msedge", "msedge")])
def test_browser_channel_can_be_selected_without_reading_a_personal_profile(monkeypatch, configured, expected):
    monkeypatch.setenv("DOUYIN_BROWSER_CHANNEL", configured)
    assert douyin_browser.browser_channel() == expected


def test_invalid_browser_channel_has_an_actionable_configuration_error(monkeypatch):
    monkeypatch.setenv("DOUYIN_BROWSER_CHANNEL", "private-browser-token")
    with pytest.raises(ExtractionError) as error:
        douyin_browser.browser_channel()
    assert error.value.code == "browser_setup_required"
    assert "private-browser-token" not in str(error.value)


@pytest.mark.parametrize("installed_in", [None, "PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"])
def test_windows_auto_uses_installed_chrome_or_playwright_chromium(tmp_path, monkeypatch, installed_in):
    monkeypatch.setenv("DOUYIN_BROWSER_CHANNEL", "auto")
    monkeypatch.setattr(douyin_browser.sys, "platform", "win32")
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        location = tmp_path / variable.replace("(", "").replace(")", "")
        monkeypatch.setenv(variable, str(location))
        if variable == installed_in:
            executable = location / "Google/Chrome/Application/chrome.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"synthetic executable marker")
    assert douyin_browser.browser_channel() == ("chrome" if installed_in else None)


@pytest.mark.parametrize(
    "message, code",
    [
        ("ProcessSingleton lock failure", "browser_profile_in_use"),
        ("user data directory is already in use", "browser_profile_in_use"),
        ("Executable doesn't exist", "browser_not_installed"),
        ("Chromium distribution 'chrome' is not found", "browser_not_installed"),
        ("Target page, context or browser has been closed", "browser_closed"),
        ("Timeout 60000ms exceeded", "browser_timeout"),
        ("unexpected protocol error", "browser_failed"),
    ],
)
def test_browser_failures_have_safe_platform_instructions(message, code):
    result = douyin_browser.browser_failure(BrowserError(message + " https://private.test/?token=secret"))
    assert result.code == code
    assert "private.test" not in str(result) and "secret" not in str(result)


def page_with_response(response):
    page = MagicMock()
    page.url = URL
    page.evaluate.return_value = response
    return page


@pytest.mark.parametrize("url", [URL, f"https://douyin.com/video/{VIDEO}", "https://www.douyin.com:443/"])
def test_official_page_requires_exact_https_origin(url):
    assert official_page(SimpleNamespace(url=url))


@pytest.mark.parametrize(
    "url",
    [
        "http://www.douyin.com/",
        "https://www.douyin.com.evil.test/",
        "https://evil-douyin.com/",
        "https://v.douyin.com/share/",
        "https://user:secret@www.douyin.com/",
        "https://www.douyin.com:8443/",
        "https://www.douyin.com:bad/",
        "https://[broken",
        "about:blank",
        None,
    ],
)
def test_untrusted_browser_page_cannot_supply_authenticated_requests(url):
    assert not official_page(SimpleNamespace(url=url))


def test_metadata_requests_only_exact_video_operation_and_preserves_numeric_64_bit_id():
    other = {"aweme_id": 7685972770793999668, "video": {}}
    target = {"aweme_id": int(VIDEO), "video": {"duration": 1875}}
    page = page_with_response({"status": 200, "text": json.dumps({"aweme_list": [other, target]})})
    assert refresh_video_metadata(page, VIDEO, find_video) == target
    arguments = page.evaluate.call_args.args[1]
    assert arguments == {
        "url": f"/aweme/v1/web/aweme/detail/?aweme_id={VIDEO}",
        "limit": douyin_browser.METADATA_LIMIT,
    }
    assert refresh_video_metadata(page, "7685972770793999669", find_video) is None


@pytest.mark.parametrize("identity", [None, 1234, "", "1234", VIDEO + "&other=1", "７" * 19])
def test_invalid_metadata_identity_never_reaches_page_request(identity):
    page = page_with_response({"status": 200, "text": "{}"})
    assert refresh_video_metadata(page, identity, find_video) is None
    page.evaluate.assert_not_called()


def test_metadata_requests_are_refused_on_non_official_page():
    page = page_with_response({"status": 200, "text": "{}"})
    page.url = "https://evil.test/"
    assert refresh_video_metadata(page, VIDEO, find_video) is None
    page.evaluate.assert_not_called()


@pytest.mark.parametrize(
    "response",
    [
        None,
        [],
        {"status": 403, "error": "http"},
        {"status": 200, "error": "size"},
        {"status": 200, "text": "<html>Verification</html>"},
        {"status": 200, "text": {"not": "text"}},
        {"status": 200, "text": "\ud800"},
        {"status": 200, "text": "é" * 40},
    ],
)
def test_metadata_failures_are_bounded_and_do_not_return_unrelated_payloads(response, monkeypatch):
    monkeypatch.setattr(douyin_browser, "METADATA_LIMIT", 64)
    page = page_with_response(response)
    assert refresh_video_metadata(page, VIDEO, find_video) is None


def test_metadata_browser_error_does_not_claim_success():
    page = page_with_response(None)
    page.evaluate.side_effect = BrowserError("page closed")
    assert refresh_video_metadata(page, VIDEO, find_video) is None


def test_caption_text_stays_in_memory_and_uses_bounded_browser_fetch():
    expected = '{"utterances":[{"start_time":125,"end_time":1875,"text":"你好"}]}'
    page = page_with_response({"status": 200, "text": expected})
    job = SimpleNamespace(cancelled=threading.Event())
    assert read_caption_text(job, page, CAPTION_URL, safe_caption_url) == expected
    assert page.evaluate.call_args.args[1] == {"url": CAPTION_URL, "limit": douyin_browser.CAPTION_LIMIT}


@pytest.mark.parametrize(
    "response, code",
    [
        (None, "caption_request_failed"),
        ({"status": 403, "error": "http"}, "caption_request_failed"),
        ({"status": 0, "error": "connection"}, "caption_request_failed"),
        ({"status": 200, "error": "size"}, "captions_invalid"),
        ({"status": 200, "error": "media"}, "captions_invalid"),
        ({"status": 200, "error": "binary"}, "captions_invalid"),
        ({"status": 200, "text": ""}, "captions_invalid"),
        ({"status": 200, "text": "\ud800"}, "captions_invalid"),
        ({"status": 200, "text": "text\x00binary"}, "captions_invalid"),
        ({"status": 200, "text": "é" * 40}, "captions_invalid"),
    ],
)
def test_caption_fetch_rejects_failed_oversized_binary_or_bad_unicode_response(response, code, monkeypatch):
    monkeypatch.setattr(douyin_browser, "CAPTION_LIMIT", 64)
    page = page_with_response(response)
    job = SimpleNamespace(cancelled=threading.Event())
    with pytest.raises(ExtractionError) as error:
        read_caption_text(job, page, CAPTION_URL, safe_caption_url)
    assert error.value.code == code


@pytest.mark.parametrize("address", ["file:///etc/passwd", "https://127.0.0.1/private", "https://evil.test/captions"])
def test_caption_asset_allowlist_is_checked_before_browser_fetch(address):
    page = page_with_response({"status": 200, "text": "text"})
    job = SimpleNamespace(cancelled=threading.Event())
    with pytest.raises(ExtractionError) as error:
        read_caption_text(job, page, address, safe_caption_url)
    assert error.value.code == "caption_address_rejected"
    page.evaluate.assert_not_called()


def test_caption_fetch_stops_after_browser_navigates_away_from_official_page():
    page = page_with_response({"status": 200, "text": "text"})
    page.url = "https://evil.test/"
    job = SimpleNamespace(cancelled=threading.Event())
    with pytest.raises(ExtractionError) as error:
        read_caption_text(job, page, CAPTION_URL, safe_caption_url)
    assert error.value.code == "caption_request_failed"
    page.evaluate.assert_not_called()


@pytest.mark.parametrize("cancel_during_fetch", [False, True])
def test_caption_fetch_observes_cancellation_before_and_after_io(cancel_during_fetch):
    job = SimpleNamespace(cancelled=threading.Event())
    page = page_with_response({"status": 200, "text": "text"})
    if cancel_during_fetch:

        def respond(*_):
            job.cancelled.set()
            return {"status": 200, "text": "text"}

        page.evaluate.side_effect = respond
    else:
        job.cancelled.set()
    with pytest.raises(ExtractionError) as error:
        read_caption_text(job, page, CAPTION_URL, safe_caption_url)
    assert error.value.code == "cancelled"
    assert page.evaluate.call_count == int(cancel_during_fetch)


def test_caption_browser_error_is_explicit_and_sanitized():
    page = page_with_response(None)
    page.evaluate.side_effect = BrowserError("request failed: private signed URL")
    job = SimpleNamespace(cancelled=threading.Event())
    with pytest.raises(ExtractionError) as error:
        read_caption_text(job, page, CAPTION_URL, safe_caption_url)
    assert error.value.code == "caption_request_failed"
    assert "private signed URL" not in str(error.value)


@pytest.mark.parametrize(
    "url, resource_type, headers",
    [
        ("https://v.douyinvod.com/opaque", "fetch", {}),
        ("https://v.toutiaovod.com/opaque", "xhr", {}),
        ("https://www.douyin.com/aweme/v1/play/?video_id=123", "fetch", {}),
        ("https://www.douyin.com/aweme/v1/playwm/?video_id=123", "fetch", {}),
        ("https://video.snssdk.com/source.MP4?token=not-retained", "fetch", {}),
        ("https://cdn.bytecdn.cn/audio.m4a", "xhr", {}),
        ("https://video.snssdk.com/chunk.m4s", "fetch", {}),
        ("https://video.snssdk.com/stream.m3u8", "fetch", {}),
        ("https://video.snssdk.com/opaque", "media", {}),
        ("https://video.snssdk.com/opaque", "fetch", {"Range": "bytes=0-1023"}),
    ],
)
def test_media_guard_aborts_player_streams_before_transfer(url, resource_type, headers):
    context, route = MagicMock(), MagicMock()
    route.request = SimpleNamespace(url=url, resource_type=resource_type, headers=headers)
    stats = install_caption_media_guard(context, set())
    context.route.call_args.args[1](route)
    route.abort.assert_called_once()
    route.continue_.assert_not_called()
    assert stats["blocked_media_requests"] == 1


@pytest.mark.parametrize(
    "url, resource_type",
    [
        (URL, "document"),
        (f"https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={VIDEO}", "xhr"),
        ("https://lf.douyinstatic.com/app.js", "script"),
        ("https://p.bytecdn.cn/preview.jpg", "image"),
        (CAPTION_URL, "fetch"),
    ],
)
def test_media_guard_allows_login_metadata_page_assets_and_discovered_caption(url, resource_type):
    context, route = MagicMock(), MagicMock()
    route.request = SimpleNamespace(url=url, resource_type=resource_type, headers={})
    caption_urls = set()
    stats = install_caption_media_guard(context, caption_urls)
    caption_urls.add(CAPTION_URL)  # Matching metadata updates the existing guard.
    context.route.call_args.args[1](route)
    route.continue_.assert_called_once()
    route.abort.assert_not_called()
    assert stats["blocked_media_requests"] == 0
    context.add_init_script.assert_called_once()


@pytest.mark.parametrize(
    "url, resource_type, headers",
    [
        (CAPTION_URL, "media", {}),
        (CAPTION_URL, "fetch", {"Range": "bytes=0-1023"}),
        ("https://video.snssdk.com/source.mp4", "fetch", {}),
        ("https://www.douyin.com/aweme/v1/play/?video_id=123", "fetch", {}),
        ("https://www.douyin.com/aweme/v1/playwm/?video_id=123", "xhr", {}),
    ],
)
def test_known_caption_address_cannot_override_explicit_video_or_audio_signal(url, resource_type, headers):
    context, route = MagicMock(), MagicMock()
    route.request = SimpleNamespace(url=url, resource_type=resource_type, headers=headers)
    stats = install_caption_media_guard(context, {url})
    context.route.call_args.args[1](route)
    route.abort.assert_called_once()
    route.continue_.assert_not_called()
    assert stats["blocked_media_requests"] == 1


def test_matching_metadata_dynamically_blocks_opaque_media_url_on_other_cdn():
    address = "https://video.snssdk.com/opaque-resource?platform-token=not-retained"
    context = MagicMock()
    media_urls = set()
    stats = install_caption_media_guard(context, {address}, media_urls)
    dispatch = context.route.call_args.args[1]
    before = MagicMock()
    before.request = SimpleNamespace(url=address, resource_type="fetch", headers={})
    dispatch(before)
    before.continue_.assert_called_once()

    # The matching video response identifies this opaque address as media.
    # That stronger signal also takes precedence over a caption URL exemption.
    media_urls.add(address)
    after = MagicMock()
    after.request = SimpleNamespace(url=address, resource_type="fetch", headers={})
    dispatch(after)
    after.abort.assert_called_once()
    after.continue_.assert_not_called()
    assert stats["blocked_media_requests"] == 1


def test_media_guard_safely_rejects_malformed_request_url():
    context, route = MagicMock(), MagicMock()
    route.request = SimpleNamespace(url="https://[broken", resource_type="fetch", headers={})
    install_caption_media_guard(context, set())
    context.route.call_args.args[1](route)
    route.abort.assert_called_once()
    route.continue_.assert_not_called()
