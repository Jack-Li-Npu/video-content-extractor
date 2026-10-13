import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import main
from app.douyin_service import (
    DouyinManager,
    Job,
    caption_records,
    find_video,
    inspect_video,
    normalize_url,
    safe_media_url,
)
from app.transcript_service import ExtractionError

VIDEO = "7685972770793999667"
URL = f"https://www.douyin.com/video/{VIDEO}"


@pytest.mark.parametrize(
    "value", [URL, f"https://www.douyin.com/jingxuan?modal_id={VIDEO}", f"https://douyin.com/video/{VIDEO}/"]
)
def test_douyin_url_identity(value):
    assert normalize_url(value) == (URL, VIDEO)


def test_short_url_requires_normal_browser_resolution():
    assert normalize_url("https://v.douyin.com/abc123/") == ("https://v.douyin.com/abc123/", "")


@pytest.mark.parametrize(
    "value",
    [
        "http://www.douyin.com/video/" + VIDEO,
        "https://douyin.com.evil.test/video/" + VIDEO,
        "https://user:secret@douyin.com/video/" + VIDEO,
        "https://douyin.com:bad/video/" + VIDEO,
        "https://douyin.com/user/someone",
        "file:///etc/passwd",
        "https://[broken",
        "https://127.0.0.1/video/" + VIDEO,
    ],
)
def test_rejects_invalid_and_non_video_urls(value):
    with pytest.raises(ExtractionError) as error:
        normalize_url(value)
    assert error.value.code == "invalid_url"


def fixture_video():
    return {
        "aweme_id": VIDEO,
        "desc": "Public title <script>",
        "author": {"nickname": "Creator"},
        "video": {
            "duration": 1891861,
            "bit_rate": [
                {
                    "bit_rate": 10000,
                    "play_addr": {"width": 3840, "height": 2160, "url_list": ["https://v.douyinvod.com/4k"]},
                },
                {
                    "bit_rate": 1000,
                    "play_addr": {
                        "width": 1280,
                        "height": 720,
                        "url_list": ["https://v.douyinvod.com/720", "http://127.0.0.1/private"],
                    },
                },
            ],
            "subtitleInfos": [{"LanguageCodeName": "en", "Url": "https://video.snssdk.com/captions"}],
        },
    }


def test_matches_only_requested_video_and_prefers_moderate_media():
    target = fixture_video()
    other = {**target, "aweme_id": "7685972770793999668"}
    data = {"aweme_list": [other, target], "account": {"private": "not retained"}}
    assert find_video(data, VIDEO) is target
    assert find_video(data, "missing") is None
    inspected = inspect_video(target)
    assert inspected["duration"] == 1891.861
    assert inspected["_media_urls"] == ["https://v.douyinvod.com/720", "https://v.douyinvod.com/4k"]
    assert inspected["tracks"][0]["language"] == "en"
    job = Job("random", URL, VIDEO, True, Path("/unused"), video=inspected)
    public = json.dumps(job.public())
    assert "_media_urls" not in public and "_url" not in public
    assert "video.snssdk.com" not in public and "account" not in public


@pytest.mark.parametrize(
    "value",
    [
        "https://douyinvod.com.evil.test/a",
        "http://v.douyinvod.com/a",
        "https://127.0.0.1/a",
        "https://user:secret@v.douyinvod.com/a",
        "https://v.douyinvod.com:bad/a",
    ],
)
def test_media_url_guard(value):
    assert not safe_media_url(value)


def test_caption_json_and_vtt_preserve_fractional_actual_durations():
    expected = [{"start": 3601.125, "duration": 4.375, "text": "Hello 世界"}]
    assert (
        caption_records(
            json.dumps({"utterances": [{"start_time": 3601125, "end_time": 3605500, "text": "Hello 世界"}]})
        )
        == expected
    )
    assert caption_records("WEBVTT\n\n00:01:01.125 --> 00:01:05.500\nHello 世界")[0]["duration"] == 4.375
    assert caption_records("1\n01:00:01,125 --> 01:00:05,500\nHello 世界") == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "<html>Verification</html>",
        '{"utterances":[]}',
        '{"utterances":[{"start_time":0,"end_time":0,"text":"Hi"}]}',
        '{"utterances":[{"start_time":0,"end_time":NaN,"text":"Hi"}]}',
    ],
)
def test_empty_and_invalid_captions_fail_explicitly(text):
    with pytest.raises(ExtractionError) as error:
        caption_records(text)
    assert error.value.code == "captions_invalid"


def test_rejects_unsafe_redirect_before_following_it(tmp_path, monkeypatch):
    response = MagicMock()
    response.__enter__.return_value = response
    response.is_redirect = True
    response.headers = {"Location": "http://127.0.0.1/private"}
    session = MagicMock()
    session.get.return_value = response
    monkeypatch.setattr("app.douyin_service.requests.Session", lambda: session)
    context = MagicMock()
    context.cookies.return_value = []
    with pytest.raises(ExtractionError) as error:
        DouyinManager()._download(
            Job("job", URL, VIDEO, True, tmp_path), context, "https://v.douyinvod.com/video", tmp_path / "video", 100
        )
    assert error.value.code == "media_address_rejected"
    assert session.get.call_count == 1
    session.close.assert_called_once()


def test_manual_confirmation_required_and_single_job_isolated(tmp_path, monkeypatch):
    manager = DouyinManager()
    manager.job = Job("unguessable", URL, VIDEO, True, tmp_path, state="opening_browser")
    with pytest.raises(ExtractionError) as error:
        manager.confirm("unguessable")
    assert error.value.code == "job_not_waiting"
    assert not manager.job.action.is_set()
    manager.job.state = "waiting_verification"
    with pytest.raises(ExtractionError):
        manager.get("different")
    with pytest.raises(ExtractionError) as error:
        manager.start(URL)
    assert error.value.code == "douyin_busy"
    manager.confirm("unguessable")
    assert manager.job.action.is_set()
    manager.cancel("unguessable")
    assert manager.job.cancelled.is_set()
    manager.shutdown()
    assert not tmp_path.exists()


def test_api_protects_origin_and_viewer_escapes_source_title(tmp_path, monkeypatch):
    manager = DouyinManager()
    media = tmp_path / "source.mp4"
    media.write_bytes(b"synthetic-video")
    manager.job = Job(
        "unguessable",
        URL,
        VIDEO,
        True,
        tmp_path,
        state="completed",
        media=media,
        video={"title": "</title><script>bad()</script>", "tracks": []},
        result={"platform": "douyin"},
    )
    monkeypatch.setattr(main, "manager", manager)
    client = TestClient(main.app, base_url="http://127.0.0.1:8003")
    response = client.post("/api/douyin/jobs", json={"url": URL}, headers={"Origin": "https://evil.test"})
    assert response.status_code == 403
    status = client.get("/api/douyin/jobs/unguessable").json()
    assert client.get("/api/douyin/current-job").json()["job"] == status
    assert status["result"]["local_viewer_url"] == "http://127.0.0.1:8003/api/douyin/jobs/unguessable/viewer"
    viewer = client.get("/api/douyin/jobs/unguessable/viewer")
    assert "<script>bad()</script>" not in viewer.text
    assert "&lt;script&gt;" in viewer.text
    assert "frame-ancestors 'none'" in viewer.headers["Content-Security-Policy"]
    assert client.get("/api/douyin/jobs/different/video").status_code == 404
    response = client.get("/api/douyin/jobs/unguessable/video", headers={"Range": "bytes=0-8"})
    assert response.status_code == 206 and response.content == b"synthetic"


@pytest.mark.parametrize("needs_verification", [False, True])
def test_normal_playback_one_confirmation_runs_through_caption_export(tmp_path, monkeypatch, needs_verification):
    import playwright.sync_api

    manager = DouyinManager()
    job = Job("id", URL, VIDEO, False, tmp_path)
    manager.job = job
    context, page, response = MagicMock(), MagicMock(), MagicMock()
    context.pages = [page]
    response.url = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
    response.headers = {"content-type": "application/json"}
    response.body.return_value = json.dumps({"aweme_detail": fixture_video()}).encode()
    page.locator.return_value.all_text_contents.return_value = []
    navigations = []

    def navigate(*args, **kwargs):
        navigations.append(args[0])
        if not needs_verification or len(navigations) > 1:
            context.on.call_args[0][1](response)

    page.goto.side_effect = navigate
    browser = MagicMock()
    browser.__enter__.return_value.chromium.launch_persistent_context.return_value = context
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: browser)
    monkeypatch.setenv("DOUYIN_BROWSER_PROFILE", str(tmp_path / "profile"))

    def wait(current, _page):
        assert needs_verification and current.state == "waiting_verification"
        manager.confirm(current.id)

    monkeypatch.setattr(manager, "_wait", wait)
    monkeypatch.setattr(manager, "_speech", lambda _: pytest.fail("captions should skip speech"))
    monkeypatch.setattr(
        manager,
        "_download",
        lambda *_: pytest.fail("captions must not download video media"),
    )
    monkeypatch.setattr(
        "app.douyin_service.read_caption_text",
        lambda *_: "1\n00:00:01,125 --> 00:00:05,500\nComplete caption text",
    )
    manager._run(job)
    assert job.state == "completed"
    assert job.result["source"] == "platform"
    assert job.result["plain_text"] == "Complete caption text"
    assert job.result["manual_verification_confirmed"] is needs_verification
    assert job.result["media_downloaded"] is False
    assert job.result["captions_only"] is True
    assert job.caption_probe_complete
    context.cookies.assert_not_called()
    assert len(navigations) == (2 if needs_verification else 1)
    context.close.assert_called()


@pytest.mark.parametrize("allow_asr", [True, False])
def test_speech_flow_respects_opt_out_and_retries_audio_variant(tmp_path, monkeypatch, allow_asr):
    import playwright.sync_api

    manager = DouyinManager()
    job = Job("id", URL, VIDEO, allow_asr, tmp_path)
    manager.job = job
    data = fixture_video()
    data["video"]["subtitleInfos"] = []
    context, page, response = MagicMock(), MagicMock(), MagicMock()
    context.pages = [page]
    response.url = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
    response.headers = {"content-type": "application/json"}
    response.body.return_value = json.dumps(data).encode()
    page.goto.side_effect = lambda *args, **kwargs: context.on.call_args[0][1](response)
    browser = MagicMock()
    browser.__enter__.return_value.chromium.launch_persistent_context.return_value = context
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: browser)
    monkeypatch.setenv("DOUYIN_BROWSER_PROFILE", str(tmp_path / "profile"))
    setup = MagicMock()
    monkeypatch.setattr("app.douyin_service.check_setup", setup)
    probe = MagicMock(side_effect=[ExtractionError("no_audio", "no audio", 422), 10.125])
    monkeypatch.setattr("app.douyin_service.probe_video", probe)
    downloads = []

    def download(_job, _context, url, path, _limit):
        downloads.append(url)
        path.write_bytes(b"video")

    monkeypatch.setattr(manager, "_download", download)
    monkeypatch.setattr(manager, "_wait", lambda *_: pytest.fail("no manual step required for normal playback"))
    monkeypatch.setattr(
        "app.douyin_service.transcribe_job",
        lambda current: (
            {
                "segments": [{"start": 0.125, "duration": 9, "text": "Full speech", "quality_flags": []}],
                "metadata": {"language": "en"},
            }
            if current.audio_validated
            else pytest.fail("must check audio")
        ),
    )
    manager._run(job)
    if not allow_asr:
        assert job.state == "failed" and job.code == "captions_not_found"
        assert job.result is None and job.media is None and downloads == []
        setup.assert_not_called()
        probe.assert_not_called()
        return
    assert job.state == "completed" and job.result["source"] == "asr"
    assert downloads == ["https://v.douyinvod.com/720", "https://v.douyinvod.com/4k"]
    assert job.result["duration"] == 10.125


def mock_browser(monkeypatch, tmp_path, data=None):
    """Run the real workflow against controlled page events, without a browser."""
    import playwright.sync_api

    context, page, response = MagicMock(), MagicMock(), MagicMock()
    context.pages = [page]
    page.url = URL
    page.locator.return_value.all_text_contents.return_value = []
    page.evaluate.return_value = {"status": 403, "error": "http"}
    if data is not None:
        response.url = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
        response.headers = {"content-type": "application/json"}
        response.body.return_value = json.dumps({"aweme_detail": data}).encode()
        page.goto.side_effect = lambda *args, **kwargs: context.on.call_args[0][1](response)
    browser = MagicMock()
    browser.__enter__.return_value.chromium.launch_persistent_context.return_value = context
    monkeypatch.setattr(playwright.sync_api, "sync_playwright", lambda: browser)
    monkeypatch.setenv("DOUYIN_BROWSER_PROFILE", str(tmp_path / "profile"))
    return context, page


def block_speech_and_media(monkeypatch, manager):
    monkeypatch.setattr(manager, "_download", lambda *_: pytest.fail("video download must not run"))
    monkeypatch.setattr(manager, "_speech", lambda *_: pytest.fail("speech recognition must not run"))
    monkeypatch.setattr("app.douyin_service.check_setup", lambda: pytest.fail("speech setup must not run"))
    monkeypatch.setattr("app.douyin_service.probe_video", lambda *_: pytest.fail("media probe must not run"))


def test_selected_language_waits_for_choice_and_reads_only_that_caption(tmp_path, monkeypatch):
    data = fixture_video()
    data["video"]["subtitleInfos"].append(
        {"LanguageCodeName": "zh-CN", "Url": "https://video.snssdk.com/chinese-captions"}
    )
    context, _page = mock_browser(monkeypatch, tmp_path, data)
    manager = DouyinManager()
    job = Job("id", URL, VIDEO, False, tmp_path)
    manager.job = job
    block_speech_and_media(monkeypatch, manager)

    def choose_track(current, _page):
        assert current.state == "waiting_track"
        assert [track["language"] for track in current.public()["video"]["tracks"]] == ["en", "zh-CN"]
        assert "_url" not in json.dumps(current.public())
        manager.confirm(current.id, "platform:1")

    monkeypatch.setattr(manager, "_wait", choose_track)
    caption_read = MagicMock(return_value="1\n01:00:01,125 --> 01:00:05,500\n完整字幕")
    monkeypatch.setattr("app.douyin_service.read_caption_text", caption_read)
    manager._run(job)
    assert job.state == "completed"
    assert job.result["language"] == "zh-CN"
    assert job.result["track_id"] == "platform:1"
    assert job.result["plain_text"] == "完整字幕"
    assert job.result["transcript_lines"][0]["start"] == 3601.125
    assert job.result["transcript_lines"][0]["duration"] == 4.375
    assert job.result["media_downloaded"] is False
    assert not job.manual_confirmed  # A track choice is not identity verification.
    assert caption_read.call_args.args[2] == "https://video.snssdk.com/chinese-captions"
    context.cookies.assert_not_called()


def test_invalid_track_choice_never_falls_back_to_another_language(tmp_path, monkeypatch):
    mock_browser(monkeypatch, tmp_path, fixture_video())
    manager = DouyinManager()
    job = Job("id", URL, VIDEO, False, tmp_path, choice="platform:missing")
    manager.job = job
    block_speech_and_media(monkeypatch, manager)
    monkeypatch.setattr("app.douyin_service.read_caption_text", lambda *_: pytest.fail("wrong track must not be read"))
    manager._run(job)
    assert job.state == "failed"
    assert job.code == "caption_selection_required"
    assert job.result is None


def test_default_captions_only_job_stops_without_any_media_when_no_track_exists(tmp_path, monkeypatch):
    data = fixture_video()
    data["video"]["subtitleInfos"] = []
    context, page = mock_browser(monkeypatch, tmp_path, data)
    observe_metadata = page.goto.side_effect
    route = MagicMock()
    route.request.url = "https://v.douyinvod.com/opaque-player-stream"
    route.request.resource_type = "fetch"
    route.request.headers = {}

    def navigate(*args, **kwargs):
        context.route.call_args.args[1](route)
        observe_metadata(*args, **kwargs)

    page.goto.side_effect = navigate
    manager = DouyinManager()
    job = Job("id", URL, VIDEO, False, tmp_path)
    manager.job = job
    block_speech_and_media(monkeypatch, manager)
    monkeypatch.setattr(manager, "_wait", lambda *_: pytest.fail("matched metadata needs no repeated verification"))
    manager._run(job)
    assert job.state == "failed" and job.code == "captions_not_found"
    assert job.caption_probe_complete
    assert job.media is None and job.result is None
    assert "No media was downloaded" in job.message
    public = job.public()
    assert public["state"] == "failed" and public["media_downloaded"] is False
    assert public["blocked_media_requests"] == 1
    assert page.evaluate.call_count == 1  # Restricted caption metadata refresh.
    context.cookies.assert_not_called()


def test_active_page_metadata_refresh_recovers_missing_passive_response(tmp_path, monkeypatch):
    data = fixture_video()
    # A numeric 64-bit identity must survive JSON parsing without JS rounding.
    data["aweme_id"] = int(VIDEO)
    context, page = mock_browser(monkeypatch, tmp_path)
    page.evaluate.return_value = {"status": 200, "text": json.dumps({"aweme_detail": data})}
    manager = DouyinManager()
    job = Job("id", URL, VIDEO, False, tmp_path)
    manager.job = job
    block_speech_and_media(monkeypatch, manager)
    monkeypatch.setattr(manager, "_wait", lambda *_: pytest.fail("active metadata should avoid a repeated login"))
    monkeypatch.setattr(
        "app.douyin_service.read_caption_text", lambda *_: "1\n00:00:00,125 --> 00:00:01,875\nActive metadata caption"
    )
    manager._run(job)
    assert job.state == "completed"
    assert job.result["video_id"] == VIDEO
    assert job.result["plain_text"] == "Active metadata caption"
    assert page.evaluate.call_args.args[1]["url"] == f"/aweme/v1/web/aweme/detail/?aweme_id={VIDEO}"
    assert not job.manual_confirmed
    context.cookies.assert_not_called()


def test_caption_probe_refreshes_incomplete_passive_metadata_without_downloading_media(tmp_path, monkeypatch):
    incomplete = fixture_video()
    incomplete["video"]["subtitleInfos"] = []
    context, page = mock_browser(monkeypatch, tmp_path, incomplete)
    page.evaluate.return_value = {"status": 200, "text": json.dumps({"aweme_detail": fixture_video()})}
    manager = DouyinManager()
    job = Job("id", URL, VIDEO, False, tmp_path)
    manager.job = job
    block_speech_and_media(monkeypatch, manager)
    monkeypatch.setattr(
        "app.douyin_service.read_caption_text", lambda *_: "1\n00:00:00,125 --> 00:00:01,875\nRecovered track"
    )
    manager._run(job)
    assert job.state == "completed"
    assert job.result["plain_text"] == "Recovered track"
    assert page.evaluate.call_count == 1
    assert job.caption_probe_complete
    assert job.media is None
    context.cookies.assert_not_called()


def test_completed_caption_result_reports_player_media_requests_blocked_by_probe(tmp_path, monkeypatch):
    context, page = mock_browser(monkeypatch, tmp_path, fixture_video())
    observe_metadata = page.goto.side_effect
    route = MagicMock()
    route.request.url = "https://v.douyinvod.com/opaque-player-stream"
    route.request.resource_type = "fetch"
    route.request.headers = {}

    def navigate(*args, **kwargs):
        context.route.call_args.args[1](route)
        observe_metadata(*args, **kwargs)

    page.goto.side_effect = navigate
    manager = DouyinManager()
    job = Job("id", URL, VIDEO, False, tmp_path)
    manager.job = job
    block_speech_and_media(monkeypatch, manager)
    monkeypatch.setattr(
        "app.douyin_service.read_caption_text", lambda *_: "1\n00:00:00,125 --> 00:00:01,875\nCaption text"
    )
    manager._run(job)
    assert job.state == "completed"
    assert job.result["blocked_media_requests"] == 1
    assert job.result["media_downloaded"] is False
    route.abort.assert_called_once()


def test_inline_existing_captions_skip_both_asset_fetch_and_video(tmp_path, monkeypatch):
    data = fixture_video()
    data["video"]["subtitleInfos"] = []
    data["video"]["cla_info"] = {
        "caption_infos": [{"lang": "zh", "utterances": [{"start_time": 125, "end_time": 1875, "text": "已有字幕"}]}]
    }
    context, _page = mock_browser(monkeypatch, tmp_path, data)
    manager = DouyinManager()
    job = Job("id", URL, VIDEO, False, tmp_path)
    manager.job = job
    block_speech_and_media(monkeypatch, manager)
    monkeypatch.setattr("app.douyin_service.read_caption_text", lambda *_: pytest.fail("inline captions need no fetch"))
    manager._run(job)
    assert job.state == "completed"
    assert job.result["plain_text"] == "已有字幕"
    assert job.media is None
    assert "utterances" not in json.dumps(job.public())
    context.cookies.assert_not_called()


def test_invalid_existing_caption_does_not_silently_download_video_even_when_speech_allowed(tmp_path, monkeypatch):
    mock_browser(monkeypatch, tmp_path, fixture_video())
    manager = DouyinManager()
    job = Job("id", URL, VIDEO, True, tmp_path)
    manager.job = job
    block_speech_and_media(monkeypatch, manager)
    monkeypatch.setattr("app.douyin_service.read_caption_text", lambda *_: "<html>Verification</html>")
    manager._run(job)
    assert job.state == "failed" and job.code == "captions_invalid"
    assert job.media is None and job.result is None


@pytest.mark.parametrize("allow_asr", [None, False, True])
def test_api_requires_explicit_speech_opt_in(tmp_path, monkeypatch, allow_asr):
    manager = MagicMock()
    job = Job("unguessable", URL, VIDEO, allow_asr is True, tmp_path)
    manager.start.return_value = {"id": job.id}
    manager.get.return_value = job
    monkeypatch.setattr(main, "manager", manager)
    body = {"url": URL}
    if allow_asr is not None:
        body["allow_asr"] = allow_asr
    response = TestClient(main.app, base_url="http://127.0.0.1:8003").post("/api/douyin/jobs", json=body)
    assert response.status_code == 200
    manager.start.assert_called_once_with(URL, allow_asr is True)
    assert response.json()["captions_only"] is (allow_asr is not True)


def test_manager_default_is_captions_only_without_launching_real_worker(tmp_path, monkeypatch):
    worker = MagicMock()
    monkeypatch.setattr("app.douyin_service.threading.Thread", lambda **_: worker)
    monkeypatch.setattr("app.douyin_service.tempfile.mkdtemp", lambda **_: str(tmp_path))
    manager = DouyinManager()
    started = manager.start(URL)
    assert started["allow_asr"] is False
    assert started["captions_only"] is True
    assert manager.job.allow_asr is False
    worker.start.assert_called_once()


def test_cancelled_metadata_is_not_accepted_even_if_already_captured(tmp_path):
    manager = DouyinManager()
    job = Job("id", URL, VIDEO, False, tmp_path)
    manager.job = job
    manager.cancel(job.id)
    page, retain = MagicMock(), MagicMock()
    with pytest.raises(ExtractionError) as error:
        manager._read_metadata(job, page, [fixture_video()], retain)
    assert error.value.code == "cancelled"
    page.wait_for_timeout.assert_not_called()
    retain.assert_not_called()


@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("parser_fails", [False, True])
def test_cancellation_during_caption_parsing_never_publishes_success_or_caption_failure(
    tmp_path, monkeypatch, inline, parser_fails
):
    data = fixture_video()
    if inline:
        data["video"]["subtitleInfos"] = [
            {"language": "zh", "utterances": [{"start_time": 125, "end_time": 1875, "text": "已有字幕"}]}
        ]
    context, _page = mock_browser(monkeypatch, tmp_path, data)
    manager = DouyinManager()
    job = Job("id", URL, VIDEO, False, tmp_path)
    manager.job = job
    block_speech_and_media(monkeypatch, manager)
    reader = MagicMock(return_value="1\n00:00:00,125 --> 00:00:01,875\nExisting captions")
    monkeypatch.setattr("app.douyin_service.read_caption_text", reader)

    def parse_and_cancel(value):
        records = caption_records(value)
        manager.cancel(job.id)
        if parser_fails:
            raise ExtractionError("captions_invalid", "Caption parsing failed while cancellation was accepted.", 422)
        return records

    monkeypatch.setattr("app.douyin_service.caption_records", parse_and_cancel)
    manager._run(job)
    assert job.state == "cancelled" and job.code == "cancelled"
    assert job.result is None and job.public()["result"] is None
    assert reader.call_count == (0 if inline else 1)
    assert not tmp_path.exists()
    context.close.assert_called()


@pytest.mark.parametrize("source", ["platform", "asr"])
def test_accepted_cancellation_during_common_normalization_wins_over_result_publication(tmp_path, source):
    manager = DouyinManager()
    job = Job("id", URL, VIDEO, source == "asr", tmp_path, state="reading_video", video=inspect_video(fixture_video()))
    manager.job = job
    normalizing, resume = threading.Event(), threading.Event()
    errors = []

    class PausedTimestamp(float):
        def __int__(self):
            normalizing.set()
            if not resume.wait(timeout=2):
                raise AssertionError("Normalization was not resumed")
            return super().__int__()

    lines = [{"start": PausedTimestamp(3601.125), "duration": 4.375, "text": "Complete caption text"}]

    def complete():
        try:
            manager._complete(job, lines, source, "provider", "en", "platform:0")
        except ExtractionError as error:
            manager._fail(job, error)
        except Exception as error:  # noqa: BLE001 -- surface worker failures in the test thread
            errors.append(error)

    worker = threading.Thread(target=complete)
    worker.start()
    try:
        assert normalizing.wait(timeout=2)
        manager.cancel(job.id)
    finally:
        resume.set()
        worker.join(timeout=2)
    assert not worker.is_alive() and not errors
    assert job.state == "cancelled" and job.result is None
    assert "timestamp" not in lines[0]  # Cancelled normalization cannot mutate the source records.


def test_cancel_after_completion_preserves_the_already_published_result(tmp_path):
    manager = DouyinManager()
    job = Job("id", URL, VIDEO, False, tmp_path, video=inspect_video(fixture_video()))
    manager.job = job
    manager._complete(
        job, [{"start": 0.125, "duration": 1.75, "text": "Caption text"}], "platform", "provider", "en", "platform:0"
    )
    result = job.result
    assert manager.cancel(job.id)["state"] == "completed"
    assert job.result is result
    assert not job.cancelled.is_set()


def test_speech_retry_joins_previous_worker_without_holding_publication_lock(tmp_path, monkeypatch):
    manager = DouyinManager()
    media = tmp_path / "source.mp4"
    media.write_bytes(b"validated-media")
    job = Job("id", URL, VIDEO, True, tmp_path, state="failed", media=media, audio_validated=True)
    manager.job = job
    previous, retry = MagicMock(), MagicMock()
    previous.is_alive.return_value = False
    manager.worker = previous

    def join_without_lock(**_):
        assert manager.lock.acquire(blocking=False), "Worker join cannot hold the worker's publication lock"
        manager.lock.release()

    previous.join.side_effect = join_without_lock
    monkeypatch.setattr("app.douyin_service.threading.Thread", lambda **_: retry)
    assert manager.retry_speech(job.id)["state"] == "transcribing"
    previous.join.assert_called_once_with(timeout=2)
    retry.start.assert_called_once()


def test_speech_retry_does_not_start_another_worker_before_previous_exit(tmp_path, monkeypatch):
    manager = DouyinManager()
    media = tmp_path / "source.mp4"
    media.write_bytes(b"validated-media")
    job = Job("id", URL, VIDEO, True, tmp_path, state="failed", media=media, audio_validated=True)
    manager.job = job
    previous, constructor = MagicMock(), MagicMock()
    previous.is_alive.return_value = True
    manager.worker = previous
    monkeypatch.setattr("app.douyin_service.threading.Thread", constructor)
    with pytest.raises(ExtractionError) as error:
        manager.retry_speech(job.id)
    assert error.value.code == "retry_unavailable"
    constructor.assert_not_called()
    assert job.state == "failed" and media.exists()
