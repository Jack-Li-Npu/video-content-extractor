import json
import os
import struct
import sys
import tempfile
import types
import unittest
import wave
from fractions import Fraction
from pathlib import Path
from unittest.mock import MagicMock, patch

from workers.local_subtitles import (
    ExtractionError,
    check_runtime,
    configure_windows_dlls,
    decode_audio_pyav,
    export,
    faster_whisper_options,
    normalize_segments,
    probe_media,
    probe_media_pyav,
    select_backend,
    timestamp,
    transcribe_faster_whisper,
    transcribe_file,
    validate_model,
)


class CaptionRegressions(unittest.TestCase):
    def test_clip_offset_and_export_after_one_hour(self):
        raw = [
            {
                "start": 0.125,
                "end": 2.875,
                "text": " First sentence. Second sentence.",
                "words": [
                    {"word": " First", "start": 0.125, "end": 0.5},
                    {"word": " sentence.", "start": 0.5, "end": 1.25},
                    {"word": " Second", "start": 2, "end": 2.5},
                    {"word": " sentence.", "start": 2.5, "end": 2.875},
                ],
            }
        ]
        segments = normalize_segments(raw, offset=3660, duration=3, language="en")
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["start"], 3660.125)
        self.assertEqual(segments[0]["duration"], 1.125)
        self.assertEqual(timestamp(3660.125, True), "01:01:00,125")
        with tempfile.TemporaryDirectory() as folder:
            prefix = Path(folder) / "captions"
            export({"metadata": {}, "segments": segments}, prefix)
            srt = Path(str(prefix) + ".srt").read_text(encoding="utf-8")
            md = Path(str(prefix) + ".md").read_text(encoding="utf-8")
            self.assertIn("01:01:00,125 --> 01:01:01,250", srt)
            for segment in segments:
                self.assertIn(segment["text"], srt)
                self.assertIn(segment["text"], md)
            self.assertEqual(json.loads(Path(str(prefix) + ".json").read_text(encoding="utf-8"))["segments"], segments)

    def test_empty_asr_is_an_error(self):
        with self.assertRaises(ExtractionError) as error:
            normalize_segments([], offset=0, duration=10, language="en")
        self.assertEqual(error.exception.code, "no_speech")

    def test_sentence_and_pause_splits_preserve_multilingual_text(self):
        examples = [
            ("zh", ["经济学该回头了。", "不要把人弄丢。"]),
            ("ja", ["図を見てください。", "仕組みを説明します。"]),
            ("th", ["ลองดูกราฟนี้", "แล้วดูผลลัพธ์"]),
            ("zh", ["GDP增长放缓。", "AI investment is rising."]),
            ("en", ["First sentence.", " Second sentence."]),
        ]
        for language, parts in examples:
            with self.subTest(language=language, parts=parts):
                raw = [
                    {
                        "start": 0.125,
                        "end": 3.75,
                        "text": "".join(parts),
                        "words": [
                            {"word": parts[0], "start": 0.125, "end": 1.25},
                            {"word": parts[1], "start": 2.25, "end": 3.75},
                        ],
                    }
                ]
                segments = normalize_segments(raw, offset=0, duration=4, language=language)
                self.assertEqual([cue["text"] for cue in segments], [part.strip() for part in parts])
                self.assertEqual([(cue["start"], cue["end"]) for cue in segments], [(0.125, 1.25), (2.25, 3.75)])
                self.assertFalse(any("word_alignment_fallback" in cue["quality_flags"] for cue in segments))
                with tempfile.TemporaryDirectory() as folder:
                    prefix = Path(folder) / "captions"
                    export({"metadata": {}, "segments": segments}, prefix)
                    restored = json.loads(Path(str(prefix) + ".json").read_text(encoding="utf-8"))["segments"]
                    self.assertEqual(restored, segments)
                    for cue in segments:
                        self.assertIn(cue["text"], Path(str(prefix) + ".srt").read_text(encoding="utf-8"))
                        self.assertIn(cue["text"], Path(str(prefix) + ".md").read_text(encoding="utf-8"))

    def test_bad_timing_is_an_error(self):
        for start, end in ((1, 0.5), (float("nan"), 2), (0, 12)):
            with self.assertRaises(ExtractionError) as error:
                normalize_segments(
                    [{"text": "Speech", "start": start, "end": end}], offset=0, duration=10, language="en"
                )
            self.assertEqual(error.exception.code, "timing_invalid")

    def test_bad_word_alignment_keeps_complete_native_text(self):
        raw = [
            {
                "start": 0,
                "end": 2,
                "text": "Keep all this text.",
                "words": [
                    {"word": " Keep", "start": 0, "end": 0},
                ],
            }
        ]
        segment = normalize_segments(raw, offset=0, duration=3, language="en")[0]
        self.assertEqual(segment["text"], raw[0]["text"])
        self.assertIn("word_alignment_fallback", segment["quality_flags"])

    def test_missing_file_is_distinct(self):
        with self.assertRaises(ExtractionError) as error:
            probe_media(Path(tempfile.gettempdir()) / "missing-subtitle-regression-video.mp4")
        self.assertEqual(error.exception.code, "file_missing")


class CrossPlatformSpeech(unittest.TestCase):
    def setUp(self):
        platform_patch = patch("workers.local_subtitles.platform.system", return_value="Linux")
        platform_patch.start()
        self.addCleanup(platform_patch.stop)
        self.environment = patch.dict(
            os.environ, {"STT_BACKEND": "auto", "STT_DEVICE": "auto", "STT_COMPUTE_TYPE": "auto"}
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def model(self, folder, backend="faster-whisper"):
        model = Path(folder) / "model"
        model.mkdir()
        files = ("config.json", "model.bin", "tokenizer.json", "preprocessor_config.json")
        if backend == "mlx":
            files = ("config.json", "weights.npz")
        for name in files:
            (model / name).write_bytes(b"synthetic fixture")
        return model

    @staticmethod
    def decoded_audio(command, **kwargs):
        with wave.open(command[-1], "wb") as stream:
            stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            stream.writeframes(b"\x01\x00" * 160)
        return types.SimpleNamespace(returncode=0)

    @staticmethod
    def decoded_audio_pyav(path, audio, **kwargs):
        CrossPlatformSpeech.decoded_audio([str(audio)])

    @staticmethod
    def fake_runtime(count=1):
        ct2 = types.ModuleType("ctranslate2")
        ct2.get_cuda_device_count = MagicMock(return_value=count)
        ct2.get_supported_compute_types = MagicMock(return_value={"int8", "int8_float16", "float32"})
        engine = types.SimpleNamespace(
            model=types.SimpleNamespace(
                device="cuda" if count else "cpu", compute_type="int8_float16" if count else "int8"
            ),
            transcribe=MagicMock(
                return_value=(
                    iter(
                        [
                            types.SimpleNamespace(
                                start=0.125,
                                end=2.875,
                                text="你好。再见。",
                                words=[
                                    types.SimpleNamespace(word="你好。", start=0.125, end=1.25, probability=0.9),
                                    types.SimpleNamespace(word="再见。", start=2.0, end=2.875, probability=0.4),
                                ],
                            ),
                        ]
                    ),
                    types.SimpleNamespace(language="zh", language_probability=0.99),
                )
            ),
        )
        faster = types.ModuleType("faster_whisper")
        faster.WhisperModel = MagicMock(return_value=engine)
        faster.audio = types.SimpleNamespace(decode_audio=MagicMock(return_value=[0.001] * 1600))
        return ct2, faster, engine

    def test_auto_backend_preserves_mac_and_selects_ct2_elsewhere(self):
        for system, expected in (("Darwin", "mlx"), ("Windows", "faster-whisper"), ("Linux", "faster-whisper")):
            with self.subTest(system=system), patch("workers.local_subtitles.platform.system", return_value=system):
                self.assertEqual(select_backend(), expected)
        with patch.dict(os.environ, {"STT_BACKEND": "mlx"}):
            self.assertEqual(select_backend(), "mlx")
            self.assertEqual(select_backend("faster-whisper"), "faster-whisper")
        with self.assertRaises(ExtractionError) as failure:
            select_backend("unknown")
        self.assertEqual(failure.exception.code, "speech_backend_invalid")

    def test_cuda_and_cpu_defaults_and_explicit_override(self):
        ct2, _, _ = self.fake_runtime()
        self.assertEqual(faster_whisper_options(ct2), ("cuda", "int8_float16"))
        ct2.get_cuda_device_count.return_value = 0
        self.assertEqual(faster_whisper_options(ct2), ("cpu", "int8"))
        ct2.get_cuda_device_count.reset_mock()
        self.assertEqual(faster_whisper_options(ct2, "cpu", "float32"), ("cpu", "float32"))
        ct2.get_cuda_device_count.assert_not_called()
        with patch.dict(os.environ, {"STT_DEVICE": "cpu", "STT_COMPUTE_TYPE": "int8"}):
            self.assertEqual(faster_whisper_options(ct2), ("cpu", "int8"))

    def test_unavailable_cuda_and_unsupported_precision_are_explicit(self):
        ct2, _, _ = self.fake_runtime(count=0)
        with self.assertRaises(ExtractionError) as failure:
            faster_whisper_options(ct2, "cuda")
        self.assertEqual(failure.exception.code, "speech_device_unavailable")
        with self.assertRaises(ExtractionError) as failure:
            faster_whisper_options(ct2, "cpu", "float16")
        self.assertEqual(failure.exception.code, "speech_compute_unsupported")
        ct2.get_cuda_device_count.side_effect = RuntimeError("driver error /private")
        with self.assertRaises(ExtractionError) as failure:
            faster_whisper_options(ct2)
        self.assertEqual(failure.exception.code, "speech_device_unavailable")
        self.assertNotIn("private", str(failure.exception))

    def test_offline_guard_requires_all_ct2_files_before_import(self):
        with tempfile.TemporaryDirectory() as folder:
            model = self.model(folder)
            self.assertEqual(validate_model(model, "faster-whisper"), model)
            for name in ("config.json", "model.bin", "tokenizer.json", "preprocessor_config.json"):
                with self.subTest(name=name):
                    item = model / name
                    contents = item.read_bytes()
                    item.unlink()
                    with (
                        patch("workers.local_subtitles.probe_media", return_value=(Path(folder) / "video.mp4", 3)),
                        self.assertRaises(ExtractionError) as failure,
                    ):
                        transcribe_file("video.mp4", backend="faster-whisper", model=model)
                    self.assertEqual(failure.exception.code, "model_missing")
                    item.write_bytes(contents)
            with self.assertRaises(ExtractionError) as failure:
                validate_model(Path(folder) / "remote-model-name", "faster-whisper")
            self.assertEqual(failure.exception.code, "model_missing")

    def test_ct2_transcription_is_offline_and_preserves_language_timing_and_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            model = self.model(folder)
            ct2, faster, engine = self.fake_runtime()
            with (
                patch.dict(
                    sys.modules, {"ctranslate2": ct2, "faster_whisper": faster, "mlx": None, "mlx_whisper": None}
                ),
                patch("workers.local_subtitles.probe_media", return_value=(Path(folder) / "video.mp4", 4000)),
                patch("workers.local_subtitles.decode_audio_pyav", side_effect=self.decoded_audio_pyav),
            ):
                result = transcribe_file("video.mp4", model=model, backend="faster-whisper", start=3660, duration=3)
            faster.WhisperModel.assert_called_once_with(
                str(model), device="cuda", compute_type="int8_float16", local_files_only=True
            )
            options = engine.transcribe.call_args.kwargs
            self.assertEqual(options["temperature"], 0.0)
            self.assertIs(options["condition_on_previous_text"], False)
            self.assertIs(options["word_timestamps"], True)
            self.assertEqual(result["metadata"]["provider"], "faster-whisper")
            self.assertEqual(result["metadata"]["device"], "cuda")
            self.assertEqual(result["metadata"]["compute_type"], "int8_float16")
            self.assertEqual(result["metadata"]["model"], "model")
            self.assertNotIn("mlx_peak_allocated_bytes", result["metadata"])
            self.assertEqual([cue["text"] for cue in result["segments"]], ["你好。", "再见。"])
            self.assertEqual([cue["provider"] for cue in result["segments"]], ["faster-whisper"] * 2)
            self.assertEqual(result["segments"][0]["start"], 3660.125)
            self.assertIn("contains_low_probability_words", result["segments"][1]["quality_flags"])
            export(result, Path(folder) / "transcript")
            for extension in ("json", "srt", "md"):
                self.assertIn("你好。", (Path(folder) / f"transcript.{extension}").read_text(encoding="utf-8"))

    def test_cpu_inference_reports_cpu_not_gpu(self):
        ct2, faster, _ = self.fake_runtime(count=0)
        with patch.dict(sys.modules, {"ctranslate2": ct2, "faster_whisper": faster}):
            raw, runtime = transcribe_faster_whisper("audio.wav", Path("local-model"), language="zh")
        self.assertEqual(runtime, {"device": "cpu", "compute_type": "int8"})
        self.assertEqual(raw["language"], "zh")
        faster.WhisperModel.assert_called_once_with(
            "local-model", device="cpu", compute_type="int8", local_files_only=True
        )

    def test_lazy_cuda_failure_is_safe_and_does_not_retry_cpu(self):
        ct2, faster, engine = self.fake_runtime()

        def failed_segments():
            raise RuntimeError("cublas64_12.dll CUDA failure in C:/private/user")
            yield  # Make inference fail during lazy iteration.

        engine.transcribe.return_value = (
            failed_segments(),
            types.SimpleNamespace(language="zh", language_probability=1),
        )
        with (
            patch.dict(sys.modules, {"ctranslate2": ct2, "faster_whisper": faster}),
            self.assertRaises(ExtractionError) as failure,
        ):
            transcribe_faster_whisper("audio.wav", Path("local-model"), language="zh")
        self.assertEqual(failure.exception.code, "speech_device_unavailable")
        self.assertNotIn("private", str(failure.exception))
        self.assertEqual(faster.WhisperModel.call_count, 1)

    def test_missing_ct2_runtime_is_actionable(self):
        with (
            patch.dict(sys.modules, {"ctranslate2": None, "faster_whisper": None}),
            self.assertRaises(ExtractionError) as failure,
        ):
            transcribe_faster_whisper("audio.wav", Path("local-model"), language=None)
        self.assertEqual(failure.exception.code, "speech_runtime_missing")

    def test_pyav_probe_reports_streams_and_rejects_invalid_duration(self):
        streams = MagicMock()
        streams.audio, streams.video = [object()], [object()]
        streams.__iter__.return_value = iter([])
        container = MagicMock(duration=2_500_000, streams=streams)
        av = types.SimpleNamespace(open=MagicMock(return_value=container), time_base=1_000_000)
        container.__enter__.return_value = container
        with tempfile.TemporaryDirectory() as folder, patch("workers.local_subtitles.av_module", return_value=av):
            path = Path(folder) / "media.mp4"
            path.touch()
            self.assertEqual(probe_media_pyav(path), {"duration": 2.5, "has_audio": True, "has_video": True})
            streams.audio = []
            with self.assertRaises(ExtractionError) as failure:
                probe_media(path, backend="faster-whisper")
            self.assertEqual(failure.exception.code, "no_audio")
            container.duration = float("nan")
            with self.assertRaises(ExtractionError) as failure:
                probe_media_pyav(path)
            self.assertEqual(failure.exception.code, "media_invalid")

    def test_streamed_pyav_clip_preserves_sample_bounds_and_timestamp_gaps(self):
        class Samples:
            def __init__(self, values):
                self.values = values

            def reshape(self, _):
                return self

            def astype(self, *args, **kwargs):
                return self

            def __len__(self):
                return len(self.values)

            def __getitem__(self, item):
                return Samples(self.values[item])

            def tobytes(self):
                return struct.pack("<" + "h" * len(self.values), *self.values)

        frames = [
            types.SimpleNamespace(pts=0, time_base=Fraction(1, 16000), to_ndarray=lambda: Samples([1, 2, 3, 4])),
            types.SimpleNamespace(pts=8, time_base=Fraction(1, 16000), to_ndarray=lambda: Samples([5, 6, 7, 8])),
        ]
        container = MagicMock(start_time=0, streams=types.SimpleNamespace(audio=[object()]))
        container.__enter__.return_value = container
        container.decode.return_value = iter(frames)
        resampler = types.SimpleNamespace(resample=lambda frame: [frame] if frame is not None else [])
        av = types.SimpleNamespace(
            open=MagicMock(return_value=container),
            time_base=1_000_000,
            AudioResampler=MagicMock(return_value=resampler),
        )
        with tempfile.TemporaryDirectory() as folder, patch("workers.local_subtitles.av_module", return_value=av):
            path = Path(folder) / "clip.wav"
            decode_audio_pyav("source.mp4", path, start=2 / 16000, duration=10 / 16000)
            with wave.open(str(path), "rb") as stream:
                self.assertEqual(stream.getframerate(), 16000)
                self.assertEqual(stream.getnchannels(), 1)
                self.assertEqual(stream.getnframes(), 10)
                self.assertEqual(struct.unpack("<10h", stream.readframes(10)), (3, 4, 0, 0, 0, 0, 5, 6, 7, 8))

    def test_windows_registers_only_existing_dll_dirs_and_keeps_handles(self):
        with tempfile.TemporaryDirectory() as folder:
            nvidia = Path(folder) / "Lib/site-packages/nvidia"
            (nvidia / "cublas/bin").mkdir(parents=True)
            handle = object()
            with (
                patch("workers.local_subtitles.platform.system", return_value="Windows"),
                patch("workers.local_subtitles.sys.prefix", folder),
                patch("workers.local_subtitles.os.add_dll_directory", return_value=handle, create=True) as add,
                patch("workers.local_subtitles._DLL_HANDLES", []) as handles,
            ):
                configure_windows_dlls()
                add.assert_called_once_with(str(nvidia / "cublas/bin"))
                self.assertEqual(handles, [handle])

    def test_runtime_check_does_not_load_a_model_or_download(self):
        ct2, faster, _ = self.fake_runtime()
        with patch.dict(sys.modules, {"ctranslate2": ct2, "faster_whisper": faster}):
            info = check_runtime(backend="faster-whisper", device="cpu", compute_type="int8")
        self.assertEqual(
            info, {"backend": "faster-whisper", "provider": "faster-whisper", "device": "cpu", "compute_type": "int8"}
        )
        faster.WhisperModel.assert_not_called()
        faster.audio.decode_audio.assert_called_once()
        temporary_audio = Path(faster.audio.decode_audio.call_args.args[0])
        self.assertEqual(temporary_audio.name, "synthetic.wav")
        self.assertFalse(temporary_audio.exists())

    def test_runtime_check_rejects_incompatible_decoder_api_and_cleans_synthetic_audio(self):
        ct2, faster, _ = self.fake_runtime()
        faster.audio.decode_audio.side_effect = TypeError("metadata_errors is unsupported /private/user")
        with (
            patch.dict(sys.modules, {"ctranslate2": ct2, "faster_whisper": faster}),
            self.assertRaises(ExtractionError) as failure,
        ):
            check_runtime(backend="faster-whisper", device="cpu")
        self.assertEqual(failure.exception.code, "speech_runtime_missing")
        self.assertNotIn("private", str(failure.exception))
        self.assertFalse(Path(faster.audio.decode_audio.call_args.args[0]).exists())
        faster.WhisperModel.assert_not_called()

    def test_runtime_check_rejects_empty_decoding(self):
        ct2, faster, _ = self.fake_runtime()
        faster.audio.decode_audio.return_value = []
        with (
            patch.dict(sys.modules, {"ctranslate2": ct2, "faster_whisper": faster}),
            self.assertRaises(ExtractionError) as failure,
        ):
            check_runtime(backend="faster-whisper", device="cpu")
        self.assertEqual(failure.exception.code, "speech_runtime_missing")
        faster.WhisperModel.assert_not_called()

    def test_windows_runtime_detects_missing_gpu_dll_but_cpu_needs_none(self):
        ct2, faster, _ = self.fake_runtime()
        with (
            patch.dict(sys.modules, {"ctranslate2": ct2, "faster_whisper": faster}),
            patch("workers.local_subtitles.platform.system", return_value="Windows"),
            patch(
                "workers.local_subtitles.ctypes.WinDLL", side_effect=OSError("missing /private/file"), create=True
            ) as dll,
        ):
            with self.assertRaises(ExtractionError) as failure:
                check_runtime(backend="faster-whisper")
            self.assertEqual(failure.exception.code, "speech_device_unavailable")
            self.assertNotIn("private", str(failure.exception))
            dll.assert_called_once_with("cublas64_12.dll")
            dll.reset_mock()
            self.assertEqual(check_runtime(backend="faster-whisper", device="cpu")["device"], "cpu")
            dll.assert_not_called()
        faster.WhisperModel.assert_not_called()

    def test_mac_keeps_existing_mlx_decoding(self):
        with tempfile.TemporaryDirectory() as folder:
            model = self.model(folder, "mlx")
            mlx = types.ModuleType("mlx")
            core = types.ModuleType("mlx.core")
            core.reset_peak_memory, core.synchronize = MagicMock(), MagicMock()
            core.get_peak_memory = MagicMock(return_value=123)
            mlx.core = core
            whisper = types.ModuleType("mlx_whisper")
            whisper.transcribe = MagicMock(
                return_value={"segments": [{"start": 0, "end": 1, "text": "Mac speech."}], "language": "en"}
            )
            with (
                patch.dict(sys.modules, {"mlx": mlx, "mlx.core": core, "mlx_whisper": whisper, "faster_whisper": None}),
                patch("workers.local_subtitles.platform.system", return_value="Darwin"),
                patch("workers.local_subtitles.probe_media", return_value=(Path(folder) / "video.mp4", 3)),
                patch("workers.local_subtitles.subprocess.run", side_effect=self.decoded_audio),
            ):
                result = transcribe_file("video.mp4", model=model)
            self.assertEqual(
                whisper.transcribe.call_args.kwargs,
                {
                    "path_or_hf_repo": str(model),
                    "language": None,
                    "task": "transcribe",
                    "temperature": 0.0,
                    "word_timestamps": True,
                    "condition_on_previous_text": False,
                    "hallucination_silence_threshold": 2.0,
                    "verbose": None,
                },
            )
            core.reset_peak_memory.assert_called_once()
            core.synchronize.assert_called_once()
            self.assertEqual(result["metadata"]["provider"], "mlx-whisper")
            self.assertEqual(result["metadata"]["mlx_peak_allocated_bytes"], 123)
            self.assertEqual(result["segments"][0]["provider"], "mlx-whisper")


if __name__ == "__main__":
    unittest.main()
