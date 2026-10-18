"""Platform-specific configuration and isolated optional speech-runtime checks."""

import json
import os
import platform
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path

from .transcript_service import ExtractionError

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "backend/workers/local_subtitles.py"


def selected_backend():
    backend = os.environ.get("STT_BACKEND", "auto").strip().lower()
    if backend == "auto":
        return "mlx" if platform.system() == "Darwin" else "faster-whisper"
    if backend not in {"mlx", "faster-whisper"}:
        raise ExtractionError("speech_backend_invalid", "Choose auto, mlx, or faster-whisper for STT_BACKEND.", 422)
    return backend


def setup_hint():
    return "Run .\\setup-douyin.ps1, then restart the app." if platform.system() == "Windows" else (
        "Run ./setup-douyin.sh, then restart the app." if platform.system() == "Darwin" else
        "Install the optional faster-whisper environment and a local model, then restart the app."
    )


def runtime_paths(backend=None):
    backend = backend or selected_backend()
    if backend == "faster-whisper":
        suffix = ".venv/Scripts/python.exe" if platform.system() == "Windows" else ".venv/bin/python"
        python = ROOT / "stt/windows" / suffix
        model = ROOT / "stt/windows/models/whisper-turbo"
    else:
        python = ROOT / "stt/.venv/bin/python"
        model = Path.home() / ".cache/efficient-content-extractor/models/whisper-turbo"
    # Keep the venv executable rather than resolving its base-interpreter symlink.
    return Path(os.environ.get("STT_PYTHON") or python), Path(os.environ.get("STT_MODEL_DIR") or model)


def worker_environment():
    environment = {
        **os.environ,
        "HF_HUB_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    prefixes = []
    if platform.system() == "Darwin":
        prefixes.extend(("/opt/homebrew/bin", "/usr/local/bin"))
    elif platform.system() == "Windows":
        python, _ = runtime_paths()
        site = python.parent.parent / "Lib/site-packages/nvidia"
        for package in ("cublas", "cudnn", "cuda_nvrtc", "cuda_runtime"):
            for directory in (site / package / "bin", site / package / "lib"):
                if directory.is_dir():
                    prefixes.append(str(directory))
    environment["PATH"] = os.pathsep.join([*prefixes, environment.get("PATH", "")])
    return environment


def speech_options():
    device = os.environ.get("STT_DEVICE", "auto").strip().lower()
    compute = os.environ.get("STT_COMPUTE_TYPE", "auto").strip().lower()
    if device not in {"auto", "cuda", "cpu"}:
        raise ExtractionError("speech_device_unavailable", "Choose auto, cuda, or cpu for STT_DEVICE.", 422)
    if compute not in {"auto", "default", "int8", "float16", "float32", "int8_float16", "int8_float32",
                       "bfloat16", "int8_bfloat16"}:
        raise ExtractionError("speech_compute_unsupported", "Choose a supported STT_COMPUTE_TYPE.", 422)
    return device, compute


@lru_cache(maxsize=8)
def _probe_runtime(python, backend, device, compute, _time_bucket):
    try:
        result = subprocess.run(
            [python, "-B", "-X", "utf8", str(WORKER), "--check-runtime", "--backend", backend,
             "--device", device, "--compute-type", compute],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
            check=False, env=worker_environment(),
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ExtractionError("speech_runtime_missing", "The local speech runtime could not start. " + setup_hint(), 422) from exc
    try:
        data = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise ExtractionError("speech_runtime_missing", "The local speech runtime did not pass its readiness check. " + setup_hint(), 422) from exc
    if result.returncode:
        code = data.get("error") if isinstance(data, dict) else None
        if code not in {"speech_runtime_missing", "speech_device_unavailable", "speech_compute_unsupported",
                        "speech_backend_invalid"}:
            code = "speech_runtime_missing"
        messages = {
            "speech_device_unavailable": "The requested speech device or its NVIDIA libraries are unavailable. "
                                         "Run local speech setup for CUDA, or select STT_DEVICE=cpu and restart.",
            "speech_compute_unsupported": "The requested speech precision is not supported by this device. "
                                          "Use STT_COMPUTE_TYPE=auto and restart.",
            "speech_backend_invalid": "The selected speech backend is not supported on this platform.",
            "speech_runtime_missing": "The local speech runtime is incomplete. " + setup_hint(),
        }
        raise ExtractionError(code, messages[code], 422)
    if not isinstance(data, dict) or data.get("backend") != backend or not data.get("device"):
        raise ExtractionError("speech_runtime_missing", "The speech readiness response was invalid. " + setup_hint(), 422)
    return data


def runtime_details():
    backend = selected_backend()
    python, model = runtime_paths(backend)
    required = ("config.json", "model.bin", "tokenizer.json", "preprocessor_config.json")
    model_ready = all((model / name).is_file() for name in required) if backend == "faster-whisper" else (
        (model / "config.json").is_file()
        and any((model / name).is_file() for name in ("weights.npz", "weights.safetensors"))
    )
    if not python.is_file() or not model_ready:
        raise ExtractionError("speech_setup_required", "Local speech needs one-time setup. " + setup_hint(), 422)
    if backend == "mlx" and (
        not shutil.which("ffmpeg", path=worker_environment()["PATH"])
        or not shutil.which("ffprobe", path=worker_environment()["PATH"])
    ):
        raise ExtractionError("speech_runtime_missing", "FFmpeg and FFprobe are required for MLX speech. Install FFmpeg, then retry.", 422)
    device, compute = speech_options()
    details = _probe_runtime(str(python), backend, device, compute, int(time.monotonic() // 30))
    return python, model, details


def check_setup():
    python, model, _ = runtime_details()
    return python, model


def speech_capabilities():
    backend = "unknown"
    try:
        backend = selected_backend()
        _, _, details = runtime_details()
        return {
            "available": True, "backend": backend, "device": details["device"],
            "compute_type": details.get("compute_type"), "runtime_verified": True,
            "message": "Local speech runtime and model files are ready. Inference remains on this computer.",
            "setup_hint": setup_hint(),
        }
    except ExtractionError as exc:
        return {
            "available": False, "backend": backend, "device": os.environ.get("STT_DEVICE", "auto"),
            "runtime_verified": False, "message": str(exc), "code": exc.code, "setup_hint": setup_hint(),
        }
