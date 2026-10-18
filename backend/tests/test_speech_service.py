import json
from unittest.mock import MagicMock

import pytest

from app.douyin_service import DouyinManager, Job
from app.speech_service import probe_video, worker_failure
from app.transcript_service import ExtractionError

URL = "https://www.douyin.com/video/7685972770793999667"


def test_worker_errors_are_actionable_without_leaking_private_log(tmp_path):
    log = tmp_path / "stt.log"
    log.write_text(
        "private-session-secret /private/path\n" + json.dumps({"error": "no_audio", "message": "secret-url"}) + "\n"
    )
    failure = worker_failure(log, 2)
    assert failure.code == "no_audio"
    assert "no audio" in str(failure)
    assert "secret" not in str(failure) and "/private" not in str(failure)
    log.write_text("Traceback secret\nModuleNotFoundError: missing_library")
    assert worker_failure(log, 1).code == "speech_runtime_missing"
    log.write_text("unrecognized failure /secret")
    assert "stopped by the system" in str(worker_failure(log, -9))


@pytest.mark.parametrize(
    "info,code",
    [
        ({"streams": [{"codec_type": "video"}], "format": {"duration": "10"}}, "no_audio"),
        ({"streams": [{"codec_type": "audio"}], "format": {"duration": "nan"}}, "media_invalid"),
    ],
)
def test_audio_probe_rejects_silent_video_variants_before_model_work(info, code, monkeypatch):
    monkeypatch.setattr("app.speech_service.selected_backend", lambda: "mlx")
    result = MagicMock(returncode=0, stdout=json.dumps(info))
    monkeypatch.setattr("app.speech_service.shutil.which", lambda *args, **kwargs: "/ffprobe")
    monkeypatch.setattr("app.speech_service.subprocess.run", lambda *args, **kwargs: result)
    with pytest.raises(ExtractionError) as failure:
        probe_video("/video")
    assert failure.value.code == code


def test_retry_retains_video_and_never_reopens_browser(tmp_path, monkeypatch):
    manager = DouyinManager()
    media = tmp_path / "source.mp4"
    media.write_bytes(b"validated-audio-video")
    job = Job(
        "id",
        URL,
        "7685972770793999667",
        True,
        tmp_path,
        state="transcribing",
        media=media,
        audio_validated=True,
        manual_confirmed=True,
        video={"title": "Title", "tracks": [], "duration": 10},
    )
    manager.job = job
    manager._fail(job, ExtractionError("speech_failed", "retry", 422))
    assert job.public()["can_retry_speech"] and media.exists()
    monkeypatch.setattr(manager, "_run", lambda *_: pytest.fail("must not acquire again"))
    monkeypatch.setattr(
        "app.douyin_service.transcribe_job",
        lambda _: {
            "segments": [{"start": 0.125, "duration": 3.875, "text": "Complete speech", "quality_flags": []}],
            "metadata": {"language": "en"},
        },
    )
    manager.retry_speech(job.id)
    manager.worker.join(timeout=2)
    assert job.state == "completed"
    assert job.result["manual_verification_confirmed"] is True
    assert job.result["transcript_lines"][0]["start"] == 0.125
    assert media.exists()
    manager.shutdown()
    assert not tmp_path.exists()


def test_cancelled_retry_clears_acquired_video(tmp_path):
    manager = DouyinManager()
    media = tmp_path / "source.mp4"
    media.touch()
    job = Job("id", URL, "7685972770793999667", True, tmp_path, media=media, audio_validated=True)
    manager._fail(job, ExtractionError("cancelled", "cancelled", 409))
    assert job.state == "cancelled"
    assert not job.public()["can_retry_speech"] and not tmp_path.exists()


def test_preflight_uses_virtual_environment_path_and_detects_missing_mlx(tmp_path, monkeypatch):
    from app.speech_service import check_setup

    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").touch()
    (model / "weights.npz").touch()
    monkeypatch.setenv("STT_PYTHON", str(python))
    monkeypatch.setenv("STT_MODEL_DIR", str(model))
    monkeypatch.setenv("STT_BACKEND", "mlx")
    monkeypatch.setattr("app.speech_service.shutil.which", lambda *args, **kwargs: "/ffmpeg")
    run = MagicMock(return_value=MagicMock(returncode=1, stdout=json.dumps({"error": "speech_runtime_missing"})))
    monkeypatch.setattr("app.speech_service.subprocess.run", run)
    with pytest.raises(ExtractionError) as failure:
        check_setup()
    assert failure.value.code == "speech_runtime_missing"
    assert run.call_args[0][0][0] == str(python)
    run.return_value.returncode = 0
    run.return_value.stdout = json.dumps({"backend": "mlx", "device": "gpu", "compute_type": "model_default"})
    assert check_setup() == (python, model)
