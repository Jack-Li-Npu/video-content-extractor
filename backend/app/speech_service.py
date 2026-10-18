"""Run the optional offline speech worker and expose safe, actionable failures."""

import json
import math
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from .speech_runtime import WORKER, check_setup, runtime_paths, selected_backend, speech_options, worker_environment
from .transcript_service import ExtractionError

ROOT = Path(__file__).resolve().parents[2]
ERRORS = {
    "no_audio": "The downloaded video has no audio. Retry extraction to try another playback version.",
    "no_speech": "No usable speech was recognized. Check the video's audio, then retry extraction.",
    "timing_invalid": "Speech recognition returned invalid timing. Retry speech recognition; the acquired video is kept.",
    "text_invalid": "Speech recognition could not preserve the recognized text. Retry speech recognition.",
    "model_missing": "The local speech model is incomplete. Run local speech setup, then retry speech recognition.",
    "media_invalid": "The acquired file could not be read as a complete video. Retry extraction.",
    "audio_decode_failed": "The acquired audio could not be decoded. Retry extraction to try another playback version.",
    "speech_runtime_missing": "Local speech setup is incomplete. Run the setup command shown in the app, then retry.",
    "speech_failed": "The local speech worker failed. Retry speech recognition; the acquired video is kept.",
    "speech_backend_invalid": "The selected local speech backend is not supported on this platform.",
    "speech_device_unavailable": "NVIDIA speech inference is unavailable. Check the CUDA runtime and GPU memory, or select CPU.",
    "speech_compute_unsupported": "This speech precision is not supported. Use STT_COMPUTE_TYPE=auto and restart.",
}


def probe_video(path):
    if selected_backend() == "faster-whisper":
        python, _ = runtime_paths()
        try:
            result = subprocess.run(
                [str(python), "-B", "-X", "utf8", str(WORKER), str(path), "--probe"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                check=False, env=worker_environment(),
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
            )
            info = json.loads(result.stdout)
            if not isinstance(info, dict):
                raise TypeError("invalid media probe response")
            if result.returncode:
                raise ExtractionError("media_invalid", ERRORS["media_invalid"], 422)
            if not info.get("has_audio"):
                raise ExtractionError("no_audio", ERRORS["no_audio"], 422)
            duration = float(info.get("duration", 0))
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("invalid duration")
            return duration
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
            raise ExtractionError("media_invalid", ERRORS["media_invalid"], 422) from exc
    executable = shutil.which("ffprobe", path=worker_environment()["PATH"])
    if not executable:
        raise ExtractionError(
            "speech_runtime_missing", "FFmpeg tools are missing. Install FFmpeg, then retry extraction.", 422
        )
    try:
        result = subprocess.run(
            [executable, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode:
            raise ExtractionError("media_invalid", ERRORS["media_invalid"], 422)
        info = json.loads(result.stdout)
        if not any(stream.get("codec_type") == "audio" for stream in info.get("streams", [])):
            raise ExtractionError("no_audio", ERRORS["no_audio"], 422)
        duration = float(info.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("invalid duration")
        return duration
    except (ValueError, subprocess.TimeoutExpired) as exc:
        raise ExtractionError("media_invalid", ERRORS["media_invalid"], 422) from exc


def worker_failure(log, returncode):
    # Never send a traceback, environment, file path or arbitrary worker text to the UI.
    with log.open("rb") as stream:
        stream.seek(max(0, log.stat().st_size - 16384))
        tail = stream.read().decode("utf-8", errors="replace")
    for line in reversed(tail.splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("error") in ERRORS:
            code = data["error"]
            return ExtractionError(code, ERRORS[code], 422)
    code = "speech_runtime_missing" if "ModuleNotFoundError" in tail else "speech_failed"
    message = ERRORS[code]
    if returncode < 0:
        message = (
            "The local speech worker was stopped by the system. Close other heavy apps, then retry speech recognition."
        )
    return ExtractionError(code, message, 422)


def stop_process(process):
    if os.name == "nt":
        # This is the exact worker we created. PyAV decoding creates no child
        # FFmpeg process; never terminate by executable name or an unbounded tree.
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def transcribe_job(job):
    python, model = check_setup()
    job.state, job.message = "transcribing", "Generating speech captions locally. The next steps run automatically…"
    prefix = job.directory / "speech"
    log_path = job.directory / "stt.log"
    command = [
        str(python),
        "-B",
        "-X",
        "utf8",
        str(ROOT / "backend/workers/local_subtitles.py"),
        str(job.media),
        "--model",
        str(model),
        "--output",
        str(prefix),
        "--source-url",
        job.url,
        "--title",
        job.video["title"],
        "--video-id",
        job.identity,
        "--backend",
        selected_backend(),
    ]
    device, compute = speech_options()
    command.extend(["--device", device, "--compute-type", compute])
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    environment = worker_environment()
    # Forced worker termination skips TemporaryDirectory cleanup. Keep decoded
    # audio inside the job so cancellation/shutdown owns all acquired media.
    environment.update({key: str(job.directory) for key in ("TMP", "TEMP", "TMPDIR")})
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, env=environment, **options)
        deadline = time.monotonic() + 3600
        while process.poll() is None:
            if job.cancelled.is_set() or time.monotonic() > deadline:
                stop_process(process)
                raise ExtractionError("cancelled", "Speech recognition was cancelled or timed out.", 409)
            time.sleep(0.25)
    if process.returncode:
        job.worker_exit_code = process.returncode
        raise worker_failure(log_path, process.returncode)
    try:
        recognized = json.loads(Path(str(prefix) + ".json").read_text(encoding="utf-8"))
        if not isinstance(recognized, dict) or not isinstance(recognized.get("segments"), list) or not recognized["segments"]:
            raise ValueError("empty captions")
        return recognized
    except (ValueError, KeyError, OSError) as exc:
        raise ExtractionError("speech_failed", ERRORS["speech_failed"], 422) from exc
