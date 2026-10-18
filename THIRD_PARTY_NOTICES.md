# Sources and third-party notices

## Upstream application

This project is a source adaptation of [samueladegoke/yt-transcript-web](https://github.com/samueladegoke/yt-transcript-web), based on commit [`4928098ffe109df932a8f4bae47f31ebcf314a10`](https://github.com/samueladegoke/yt-transcript-web/commit/4928098ffe109df932a8f4bae47f31ebcf314a10).

The original [MIT license](LICENSE), including **Copyright (c) 2026 Sam Ade**, is preserved. The upstream React/FastAPI application and caption-library integration provided the foundation. This adaptation changes retrieval and errors, caption selection, accurate timing, exports, interface layout, local setup, and the separate AI analysis handoff. The `video-brief` skill and future-platform roadmap were added for this project.

The published repository starts with a clean snapshot of the adapted application; its Git history does not reproduce the upstream history. This does not remove the upstream attribution or license obligations.

## Main technologies

Dependencies retain their own licenses. Exact installed versions and transitive dependencies are recorded in `backend/uv.lock`, `frontend/package-lock.json`, `stt/uv.lock`, and `stt/windows/uv.lock`; this table identifies the principal source projects rather than replacing their license files.

| Technology | Role | Public source |
| --- | --- | --- |
| youtube-transcript-api | Primary retrieval of existing YouTube caption tracks | [jdepoix/youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api) |
| yt-dlp | Video metadata and selected-caption fallback, with media downloads disabled | [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) |
| FastAPI | Local Python API | [fastapi/fastapi](https://github.com/fastapi/fastapi) |
| Uvicorn | ASGI server | [encode/uvicorn](https://github.com/encode/uvicorn) |
| React | Frontend components and state | [facebook/react](https://github.com/facebook/react) |
| Vite | Frontend development and production build | [vitejs/vite](https://github.com/vitejs/vite) |
| js-tiktoken | Local text token estimates using the o200k_base reference encoding; no model inference | [dqbd/tiktoken](https://github.com/dqbd/tiktoken) (MIT) |
| Tailwind CSS | CSS tooling inherited from the frontend | [tailwindlabs/tailwindcss](https://github.com/tailwindlabs/tailwindcss) |
| Lucide | Interface icons | [lucide-icons/lucide](https://github.com/lucide-icons/lucide) |
| Geist / Geist Mono | Self-hosted typography | [vercel/geist-font](https://github.com/vercel/geist-font) |
| Fontsource | npm font packaging | [fontsource/fontsource](https://github.com/fontsource/fontsource) |
| uv | Isolated Python environment and locked installation | [astral-sh/uv](https://github.com/astral-sh/uv) |
| Playwright | Dedicated headed browser and normal Douyin playback response observation | [microsoft/playwright-python](https://github.com/microsoft/playwright-python) (Apache-2.0) |
| MLX Whisper / MLX | Optional offline speech recognition on Apple Silicon; locked separately in `stt/uv.lock` | [ml-explore/mlx-examples/whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper), [ml-explore/mlx](https://github.com/ml-explore/mlx) (MIT) |
| faster-whisper / CTranslate2 | Optional Windows local speech backend, with CPU and NVIDIA inference; locked separately in `stt/windows/uv.lock` | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper), [OpenNMT/CTranslate2](https://github.com/OpenNMT/CTranslate2) |
| PyAV | Windows media probing/audio decoding through packaged FFmpeg libraries, without a system FFmpeg executable | [PyAV-Org/PyAV](https://github.com/PyAV-Org/PyAV); packaged FFmpeg libraries retain their own notices |
| Whisper large-v3-turbo | Optional model, installed separately with pinned revision, sizes, and SHA-256 checksums | [openai/whisper](https://github.com/openai/whisper), [MLX conversion](https://huggingface.co/mlx-community/whisper-large-v3-turbo), [dropbox-dash CTranslate2 conversion](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo) |
| NVIDIA cuBLAS / cuDNN runtime wheels | Optional CUDA extra for Windows faster-whisper; downloaded separately, with NVIDIA license terms rather than the app's MIT license | [cuBLAS](https://developer.nvidia.com/cublas), [cuDNN](https://developer.nvidia.com/cudnn) |
| FFmpeg / FFprobe | Apple Silicon media decoding/probing; installed separately | [FFmpeg](https://ffmpeg.org/legal.html) (license depends on the user's build) |

The Windows model conversion is pinned to `dropbox-dash/faster-whisper-large-v3-turbo` revision `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf`. The downloader records expected sizes and SHA-256 checksums in `stt/download_windows_model.py`; setup fetches those model files rather than bundling them in Git. macOS keeps its separate MLX conversion and downloader. Installing the optional CUDA extra installs NVIDIA runtime wheels; users must observe their included licenses and notices.

The public [jiji262/douyin-downloader](https://github.com/jiji262/douyin-downloader) documentation informed the browser-acquisition approach. Its desktop browser bridge and Douzy code are not bundled, and Douzy is not a runtime dependency. The app's Douyin adapter is independently implemented.

Font license copies are included at [Geist OFL](frontend/public/licenses/Geist-OFL.txt) and [Geist Mono OFL](frontend/public/licenses/Geist-Mono-OFL.txt).

Token-estimation license copies are included at [js-tiktoken MIT](frontend/public/licenses/js-tiktoken-MIT.txt) and [base64-js MIT](frontend/public/licenses/base64-js-MIT.txt).

## Development guidance

[Leonxlnx/taste-skill](https://github.com/Leonxlnx/taste-skill), especially its existing-project redesign workflow, guided the frontend redesign. It is development guidance, not a runtime dependency or a bundled copy in this repository.

The original local workspace also installed [zhaoxuya520/reverse-skill](https://github.com/zhaoxuya520/reverse-skill) as separate tooling. The extractor does not depend on it, and that independent checkout is not included in this release. Caption retrieval is performed by the providers identified above.

Video text, thumbnails, names, and platform trademarks belong to their respective owners. This repository distributes application code and instructions, not a collection of third-party video transcripts.
