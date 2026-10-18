import json
import os
import subprocess
from unittest.mock import MagicMock

import pytest

from app import speech_runtime as runtime
from app import speech_service as service
from app.transcript_service import ExtractionError


@pytest.fixture
def configured(tmp_path, monkeypatch):
    python = tmp_path / "runtime/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    model = tmp_path / "model"
    model.mkdir()
    for name in ("config.json", "model.bin", "tokenizer.json", "preprocessor_config.json"):
        (model / name).touch()
    monkeypatch.setenv("STT_BACKEND", "faster-whisper")
    monkeypatch.setenv("STT_PYTHON", str(python))
    monkeypatch.setenv("STT_MODEL_DIR", str(model))
    monkeypatch.setenv("STT_DEVICE", "auto")
    monkeypatch.setenv("STT_COMPUTE_TYPE", "auto")
    runtime._probe_runtime.cache_clear()
    return python, model


@pytest.mark.parametrize("system,backend", [("Windows", "faster-whisper"), ("Linux", "faster-whisper"), ("Darwin", "mlx")])
def test_platform_defaults_preserve_mlx_and_choose_portable_backend(system, backend, monkeypatch):
    monkeypatch.delenv("STT_BACKEND", raising=False)
    monkeypatch.setattr(runtime.platform, "system", lambda: system)
    assert runtime.selected_backend() == backend


def test_capabilities_verify_runtime_without_loading_model_or_exposing_paths(configured, monkeypatch):
    run = MagicMock(return_value=MagicMock(returncode=0, stdout=json.dumps({
        "backend": "faster-whisper", "device": "cuda", "compute_type": "int8_float16",
    })))
    monkeypatch.setattr(runtime.subprocess, "run", run)
    result = runtime.speech_capabilities()
    assert result["available"] and result["runtime_verified"]
    assert result["device"] == "cuda" and result["compute_type"] == "int8_float16"
    command = run.call_args.args[0]
    assert "--check-runtime" in command and "--model" not in command
    assert str(configured[1]) not in json.dumps(result)
    assert run.call_args.kwargs["env"]["HF_HUB_OFFLINE"] == "1"


def test_missing_model_fails_before_runtime_and_has_platform_setup_hint(configured, monkeypatch):
    (configured[1] / "tokenizer.json").unlink()
    monkeypatch.setattr(runtime.platform, "system", lambda: "Windows")
    run = MagicMock()
    monkeypatch.setattr(runtime.subprocess, "run", run)
    result = runtime.speech_capabilities()
    assert not result["available"] and result["code"] == "speech_setup_required"
    assert "setup-douyin.ps1" in result["setup_hint"]
    run.assert_not_called()


def test_gpu_preflight_error_is_actionable_and_hides_worker_secrets(configured, monkeypatch):
    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: MagicMock(
        returncode=2, stdout=json.dumps({"error": "speech_device_unavailable", "message": "/private/model SECRET_URL"}),
    ))
    result = runtime.speech_capabilities()
    assert not result["available"] and result["code"] == "speech_device_unavailable"
    assert "STT_DEVICE=cpu" in result["message"]
    assert "SECRET" not in json.dumps(result) and "/private" not in json.dumps(result)


def test_explicit_cpu_settings_reach_isolated_readiness_probe(configured, monkeypatch):
    monkeypatch.setenv("STT_DEVICE", "cpu")
    monkeypatch.setenv("STT_COMPUTE_TYPE", "int8")
    run = MagicMock(return_value=MagicMock(returncode=0, stdout=json.dumps({
        "backend": "faster-whisper", "device": "cpu", "compute_type": "int8",
    })))
    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert runtime.check_setup() == configured
    args = run.call_args.args[0]
    assert args[args.index("--device") + 1] == "cpu"
    assert args[args.index("--compute-type") + 1] == "int8"


def test_invalid_backend_is_rejected_without_running_worker(monkeypatch):
    monkeypatch.setenv("STT_BACKEND", "unexpected")
    run = MagicMock()
    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert runtime.speech_capabilities()["code"] == "speech_backend_invalid"
    run.assert_not_called()


def test_windows_dll_path_uses_platform_separator_and_preserves_parent_environment(configured, monkeypatch):
    monkeypatch.setattr(runtime.platform, "system", lambda: "Windows")
    dll = configured[0].parent.parent / "Lib/site-packages/nvidia/cudnn/bin"
    dll.mkdir(parents=True)
    monkeypatch.setenv("PATH", "ORIGINAL_PATH")
    environment = runtime.worker_environment()
    assert str(dll) in environment["PATH"].split(os.pathsep)
    assert environment["PATH"].split(os.pathsep)[-1] == "ORIGINAL_PATH"
    assert os.environ["PATH"] == "ORIGINAL_PATH"
    assert "/opt/homebrew/bin" not in environment["PATH"]


@pytest.mark.parametrize("info,code", [
    ([], "media_invalid"),
    ({"duration": 4, "has_audio": False, "has_video": True}, "no_audio"),
    ({"duration": float("nan"), "has_audio": True}, "media_invalid"),
])
def test_portable_probe_rejects_invalid_media_without_ffprobe(configured, monkeypatch, info, code):
    monkeypatch.setattr(service.subprocess, "run", lambda *args, **kwargs: MagicMock(
        returncode=0, stdout=json.dumps(info),
    ))
    monkeypatch.setattr(service.shutil, "which", lambda *args, **kwargs: pytest.fail("No FFprobe CLI required"))
    with pytest.raises(ExtractionError) as error:
        service.probe_video("video.mp4")
    assert error.value.code == code


@pytest.mark.skipif(os.name != "nt", reason="Native Windows worker termination")
def test_windows_cancel_stops_only_its_worker_without_unix_process_groups():
    process = MagicMock()
    process.poll.return_value = None
    service.stop_process(process)
    process.terminate.assert_called_once()
    process.kill.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Native Windows worker termination")
def test_windows_cancel_kills_only_unresponsive_known_worker():
    process = MagicMock()
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired("worker", 10), 0]
    service.stop_process(process)
    process.terminate.assert_called_once()
    process.kill.assert_called_once()


def test_worker_result_preserves_chinese_text_and_passes_selected_backend(configured, tmp_path, monkeypatch):
    job = MagicMock(directory=tmp_path, media=tmp_path / "source.mp4", url="https://example.test/video", identity="123")
    job.video = {"title": "中文标题"}
    job.cancelled.is_set.return_value = False
    result = {"metadata": {"provider": "faster-whisper"}, "segments": [{"text": "完整中文", "start": 0, "duration": 1}]}
    (tmp_path / "speech.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(service, "check_setup", lambda: configured)
    worker = MagicMock(returncode=0)
    worker.poll.return_value = 0
    popen = MagicMock(return_value=worker)
    monkeypatch.setattr(service.subprocess, "Popen", popen)
    assert service.transcribe_job(job)["segments"][0]["text"] == "完整中文"
    command = popen.call_args.args[0]
    assert command[command.index("--backend") + 1] == "faster-whisper"
    assert "-X" in command and "utf8" in command
    assert all(popen.call_args.kwargs["env"][key] == str(tmp_path) for key in ("TMP", "TEMP", "TMPDIR"))


def test_capabilities_api_reports_runtime_separately_from_health(monkeypatch):
    from fastapi.testclient import TestClient

    import main

    monkeypatch.setattr(main, "speech_capabilities", lambda: {"available": False, "code": "speech_setup_required"})
    client = TestClient(main.app, base_url="http://127.0.0.1:8000")
    assert client.get("/health").json()["status"] == "healthy"
    assert client.get("/api/capabilities").json()["speech"]["available"] is False
