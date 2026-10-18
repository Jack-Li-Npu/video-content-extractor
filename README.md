# Video Content Extractor

**Find the moments worth watching.**

Retrieve existing YouTube or Douyin captions, bring a timestamped transcript to Codex, and find the moments worth watching. Both channels default to captions only. Douyin uses a dedicated browser for any manual verification; downloading a video for local speech recognition is a separate opt-in fallback.

[Quick start](#quick-start) &nbsp; / &nbsp; [Douyin guide](docs/DOUYIN.md) &nbsp; / &nbsp; [See the demo](#demo-from-captions-to-a-timestamped-brief) &nbsp; / &nbsp; [Roadmap](ROADMAP.md) &nbsp; / &nbsp; [Attribution](#attribution)

![The YouTube channel in the local app, with its own caption-only form and a separate Douyin tab](docs/screenshots/youtube-channel.jpg)

Built on [samueladegoke/yt-transcript-web](https://github.com/samueladegoke/yt-transcript-web), with its MIT license preserved. Runs locally on macOS or Windows; analysis happens in your own AI chat.

## Purpose and workflow

A long interview, lecture, or news program becomes easier to navigate when you can read a brief and open the passages that matter.

1. **Choose a channel.** Open **YouTube** or **Douyin**, paste a link, and retrieve an exposed caption track. Douyin may first require manual verification in its dedicated browser.
2. **Bring it to Codex.** Copy or download one complete Markdown transcript with every retrieved caption and its timestamps.
3. **Choose what to watch.** Use the included `video-brief` skill for an overview, key points, and selected claims worth checking, with links back to supporting passages.

You choose when and where to submit the transcript. The app makes no AI-service requests or automatic uploads; the Markdown also works with other assistants.

## Two channels, one handoff

| Channel | What happens after you paste a link |
| --- | --- |
| **YouTube** | Check existing caption tracks, choose the language when needed, and retrieve the transcript. No separate browser window or speech model is used. |
| **Douyin** | Open the official page in a dedicated local browser. Complete any requested check when extraction pauses, then click **I’ve verified, continue**. The app checks timed captions while blocking player media. Public metadata can be accessible without login; choose a track when several are exposed. |

Each channel keeps its own link, transcript, selected language, search, and errors while the page stays open. Switching to YouTube leaves an active Douyin job running. The Douyin tab shows **Needs verification**, **Extracting**, or **Ready**, so you can see when to return. Pasting a link into the wrong channel shows a message before any extraction request is sent.

![The separate Douyin channel with verification guidance, optional local speech, a restored 237-segment result and the complete Codex handoff](docs/screenshots/douyin-channel.jpg)

This historical screenshot restores the result from the [7-minute math video](https://www.douyin.com/video/7631965839184432424) tested on 2026-10-11. Search filters the view; exports still include all **237 segments**. This result used video download and local speech recognition, with estimated wording and timing; it does not demonstrate direct caption retrieval. The current interface starts with the download fallback off. Source imagery and captions belong to their creators.

The page opens on YouTube. After a reload, open **Douyin** to restore its current job while the server is running. YouTube drafts and results stay in page memory and do not survive a reload. The **+** beside a channel heading starts a new transcript in that channel. Starting another Douyin extraction replaces its previous temporary video; switching channels does not. The interface follows your system's light or dark appearance and respects reduced motion.

## Demo: from captions to a timestamped brief

### Read the brief. Open the passage.

This earlier YouTube demonstration extracts **685 caption segments** from a Bloomberg Tech video. A separate Codex chat then turns that source into a timestamped brief.

![Historical YouTube extraction with 685 caption segments and the complete Codex handoff](docs/screenshots/transcript-extraction.png)

![Codex displaying an overview and key points with clickable YouTube timestamps beside the exported source transcript](docs/screenshots/codex-timestamped-brief.png)

The transcript stays available beside the analysis. Follow a timestamp link to check the original context or watch only a relevant section.

<details>
<summary>About this example</summary>

Both screenshots show *Anthropic Goes Big on Compute, Microsoft Rethinks AI* by Bloomberg Tech. Video imagery and caption excerpts belong to their respective owners. The summary is produced in a separate Codex chat, not inside the extractor. Current timestamp links open YouTube; seeking inside the active player is planned.

</details>

### Douyin: from manual verification to a complete handoff

Individual Douyin links use a dedicated local browser. Complete any login or identity check yourself on the official Douyin page, then click **I’ve verified, continue** in the extractor. The app checks the matching video's metadata and retrieves only exposed timed text; several tracks prompt a choice. If none is exposed, the default flow reports that limitation without downloading media. You can explicitly enable **Allow video download for local speech recognition** and retry. A working saved session can proceed without another check. **Douzy is not required.**

![Successful Douyin extraction showing the complete Codex handoff, local speech warning, timestamp links, and estimated input token count](docs/screenshots/douyin-codex-handoff.png)

This historical screenshot is from the successful **2026-10-08** speech test of a **31-minute** video by **毕英杰Johnathan**: **466 speech-caption segments** and approximately **19,301 input tokens**, including metadata, timestamps, and the prompt. It demonstrates the video-download fallback and handoff, not directly retrieved subtitles or a completed illustrated AI brief. The full transcript and downloaded video are not distributed with this repository. [Source video](https://www.douyin.com/video/7685972770793999667) · [Setup, privacy, troubleshooting, and validation](docs/DOUYIN.md).

## What works today

| What you need | What the app provides |
| --- | --- |
| YouTube source text | Uploaded or automatic captions, explicit track selection, and full-text search |
| Douyin source text | Existing timed JSON/SRT/VTT captions after normal browser access; explicit opt-in video download and local speech when no track is exposed |
| A complete AI handoff | One Markdown document with all retrieved captions, source metadata, and millisecond timings |
| Source context | Caption provenance and timing; speech-generated text is clearly labelled as estimated |
| Control over analysis | Copy/download for your own assistant, with an optional `video-brief` skill |

Copy and download always include the entire retrieved transcript, even when search filters the display. Uploaded original-language captions are preferred when that language can be determined.

### Where this is going

Both channels first retrieve existing caption tracks when platform access permits. Douyin keeps its browser verification and job progress, with a restricted metadata refresh and caption requests in that same browser. Caption-only results retain source timings but do not create a local player or guarantee exact seeking on Douyin's public page. See [Douyin setup and verification](docs/DOUYIN.md) for the optional speech fallback and tested boundaries.

The [2026-10-11 system check](VERIFICATION.md#system-check--2026-10-11) added two successful Douyin videos with matching copy/download exports and verified local seeks. All four YouTube test links received a bot-check response through the tested proxy; those runs did not retrieve captions. Offline tests and historical successes do not guarantee current live access.

| Direction | Status |
| --- | --- |
| Douyin direct timed captions | Implemented for exposed tracks; availability and browser access vary. See [current verification](VERIFICATION.md). |
| Douyin opt-in video download + local speech | Historical success on four public videos; these were speech tests, not evidence of direct-caption availability |
| TikTok and Bilibili caption adapters | Planned |
| Browser extension beside the video player | Planned |
| Click a key point to seek the active player | Planned |

See the [Roadmap](ROADMAP.md) for scope, proposed interfaces, and contribution priorities. TikTok, Bilibili, and the extension are not implemented yet.

## Quick start

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and Node.js 22.12+ with npm. The setup scripts create isolated Python environments and build the web interface. Existing captions need no speech model.

**macOS:**

```bash
git clone https://github.com/Jack-Li-Npu/video-content-extractor.git
cd video-content-extractor
./setup.sh
./start.command
```

Open **http://127.0.0.1:8000**. Keep the terminal open while using the app; press Control-C to stop it. On subsequent macOS runs, you can double-click `start.command` in Finder.

**Windows x64**, from PowerShell in your chosen project folder:

```powershell
git clone https://github.com/Jack-Li-Npu/video-content-extractor.git
cd video-content-extractor
.\setup.cmd
.\Start-Video-Content-Extractor.cmd
```

The Start shortcut opens the browser and keeps the server in the background. Use `Stop-Video-Content-Extractor.cmd` to stop this installation. It does not install a Windows service or enable automatic sign-in startup. See the [Windows guide](docs/WINDOWS.md) for speech setup, proxies, logs, and troubleshooting.

### Use Douyin captions without a speech model

1. Open **Douyin** and paste an individual video, `jingxuan?modal_id=…`, or `v.douyin.com` share link. Keep **Allow video download for local speech recognition** off.
2. Click **Extract transcript**. Complete any requested login, CAPTCHA, or identity check yourself in the dedicated official-page browser, then click **I’ve verified, continue** when the extractor asks.
3. Choose a caption track if several are available, then click **Use selected captions**. The app reads the selected timed text into memory while blocking player video/audio requests.
4. Copy or download the complete handoff. If no usable track is exposed, the app reports it; the title/description and text burned into the picture cannot substitute for timed subtitles.

This mode does not download audio/video or speech models. It preserves caption timestamps, but public Douyin links do not provide reliable exact seeking. Local playback is available only when the optional fallback below acquired media.

### Enable the optional Douyin speech fallback once

Existing YouTube captions need no speech model. For Douyin videos without an exposed timed track, install the optional local speech runtime for your platform:

| Platform | Setup | Inference |
| --- | --- | --- |
| Apple Silicon Mac | Install FFmpeg, then run `./setup-douyin.sh` | Existing MLX Whisper backend |
| Windows x64 | Run `.\setup-douyin.cmd` | faster-whisper/CTranslate2 on NVIDIA CUDA or CPU; PyAV handles media without a system FFmpeg installation |

Apple Silicon setup:

```bash
brew install ffmpeg
./setup-douyin.sh
```

Both setups use isolated dependencies and pinned Whisper large-v3-turbo model files checked by size and SHA-256. Model weights need about **1.6 GB**, plus dependencies and temporary media. Windows uses the pinned [dropbox-dash CTranslate2 conversion](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo); its `model.bin` is approximately **1.618 GB**. Windows `-Device auto` installs the CUDA extra only when a responding NVIDIA GPU/driver is detected; those runtime wheels add about **1.3 GB**, plus dependencies. `-Device cpu` avoids CUDA dependencies; explicit `-Device cuda` requires a working NVIDIA driver. See [Windows setup](docs/WINDOWS.md) for Visual C++ requirements and switching devices.

Restart the server after setup and reload the page. `/health` confirms that the app is running; `/api/capabilities` separately checks speech runtime imports, device/precision support, required model-file presence, and relevant runtime libraries. It does not load the model or guarantee enough GPU memory for inference. Intel Mac/Linux speech installation is not covered by the supplied setup guides.

To opt in to speech recognition:

1. Open the **Douyin** tab and enable **Allow video download for local speech recognition** before starting or retrying extraction.
2. Click **Extract transcript**. Keep the dedicated browser open. If asked, complete Douyin's login, CAPTCHA, or identity verification yourself there; the extractor does not collect identity documents or solve challenges.
3. Return to the extractor and click **I’ve verified, continue** when it asks. The remaining steps run automatically. Public video metadata can be readable without login, so an automatic continuation does not prove authentication. The dedicated browser closes when caption checking, media acquisition, or a failure ends its work; speech runs afterward without that window.
4. If no caption track is exposed, the app downloads a temporary video with audio and runs local speech recognition. When **Ready for Codex** appears, search the text, inspect the local video at a timestamp, or copy/download the complete handoff. Check speech-recognition warnings before relying on names or numbers. If existing captions were retrieved instead, no local video is created.

Keep the server running while Codex uses local video links. Starting another Douyin extraction or stopping the server removes the previous local video; downloaded transcript files remain on your computer. For retry behavior, configuration paths, and access failures, see the [Douyin guide](docs/DOUYIN.md).

### Update an existing installation

Stop the server, then run the following from your clone. Preserve any local source changes before pulling; Git will stop if they conflict.

```bash
git pull --ff-only
./setup.sh
./start.command
```

The repository was renamed from `youtube-content-extractor` to `video-content-extractor`. Existing clones can keep their local folder name. GitHub redirects the former URL; to use the current address directly, run:

```bash
git remote set-url origin https://github.com/Jack-Li-Npu/video-content-extractor.git
```

On Windows, stop the app, run `git pull --ff-only`, then `.\setup.cmd` and `.\Start-Video-Content-Extractor.cmd` from PowerShell instead. If you enable or update the optional speech runtime, run the corresponding `setup-douyin.sh` or `setup-douyin.cmd` before launching. Matching model files are verified and reused. Windows downloads preserve resumable partials when possible and reject checksum mismatches. Setup preserves existing `.env` keys; compare `.env.example` for new settings. Changing `-Device` installs different dependencies but does not overwrite an existing `STT_DEVICE`; edit that key when switching a configured installation.

<details>
<summary>Setup details and download troubleshooting</summary>

Setup creates an isolated Python environment in `backend/.venv`, installs backend dependencies from `backend/uv.lock`, installs frontend dependencies with `npm ci`, and builds React for FastAPI to serve. Setup copies `.env.example` to `.env` only when `.env` does not already exist. Run setup again after changing dependencies or frontend code.

Use a regular browser for file downloads. In testing, the Codex embedded browser displayed and copied transcripts successfully but did not complete Blob downloads; **Copy for Codex** provides a complete handoff there.

</details>

### Caption selection

Paste a watch, youtu.be, Shorts, embed, live/replay link, or a video ID and click **Get transcript**. Playlist-only links and active live broadcasts are unsupported. If no original-language track is recommended, choose a caption track and submit again. Machine-translated tracks are excluded.

### Proxy configuration

The default example configuration does not force a local proxy. If your network needs one, edit `.env`:

```dotenv
YT_PROXY=http://127.0.0.1:7890
```

Use your own proxy address and keep that proxy running. Restart the app after changes. `YT_PROXY` takes priority over `HTTPS_PROXY`, `HTTP_PROXY`, and `SOCKS5_PROXY`. For direct access, leave `YT_PROXY` empty and unset those other variables. Direct Python/macOS dotenv loading preserves existing shell variables. The Windows Start launcher explicitly loads documented `.env` values into its child process, so those file values take priority for that launch; it restores the calling shell afterward.

This setting covers backend YouTube requests. Windows setup also reads a literal `YT_PROXY` from `.env` before installing dependencies and models, filling only unset shell proxy values; `-Proxy` explicitly overrides them for that setup run. You can also set `HTTPS_PROXY` before setup. YouTube extraction does not read browser cookies. Douyin uses its own dedicated browser profile; these settings do not configure that browser or its media downloader.

Windows installer/model downloads require an HTTP(S) proxy endpoint. YouTube extraction also supports SOCKS through its Requests backend; for setup, use the proxy application's HTTP or mixed port instead of a `socks5://` URL.

## Use the video-brief skill

Click **Copy for Codex** and paste into your Codex chat. The copied text includes the complete transcript and a prompt that asks Codex to reuse `video-brief` if installed, or use `$skill-installer` to install it from this repository when missing. The prompt then asks Codex to read the installed instructions and continue with the transcript in the same chat. If installation is unavailable, it requests the same analysis workflow and an explicit limitation instead of assuming the skill exists.

**Download for Codex** includes the same prompt at the beginning of the `.transcript.md` file. Attach it and ask Codex to follow its opening instructions. You do not need to copy a separate setup prompt for each video. The web app itself does not install anything into Codex.

For manual installation, copy `skills/video-brief/` into `~/.agents/skills/video-brief/`. Keep only one installation; an existing copy under `~/.codex/skills/video-brief/` can also be used. Codex detects installed skills automatically; restart if it does not appear. See [Codex skill documentation](https://learn.chatgpt.com/docs/build-skills).

The brief combines captions with screenshots when they refer to charts, diagrams, or demonstrations. Visual-dependent takeaways include readable visual details and a link to a verified clear view, with a separate narration link when useful. Screenshot inspection requires available browser/screenshot tools and access to the source video; a skill provides workflow instructions, not those tools. When visual inspection is unavailable, the prompt asks for a caption-based brief that identifies the unverified visuals.

The brief also selects claims worth checking to help you judge the video quickly. Each entry identifies the speaker's claim, its grounds and limits, the strongest plausible alternative when useful, what to inspect, and the precise supporting source timestamp. Alternatives are labelled as hypotheses unless stated in the video or independently verified. The assistant avoids forced opposing opinions and unsupported factual verdicts; outside research is not required by default.

You can add a preferred language or viewing-time budget. For another assistant, provide the skill's `SKILL.md` as workflow instructions alongside the source.

The skill instructs the assistant to read every segment, verify supporting passages, attribute claims to speakers, and distinguish complete retrieved captions from complete audiovisual coverage. It cannot guarantee the accuracy of captions, visual interpretation, or an AI-generated summary.

## Token usage before you send

Each retrieved transcript shows an **estimated Codex input token count** beside the copy/download controls, with separate counts for the transcript (including metadata and timestamp formatting) and the setup prompt. Counting runs locally in a browser worker using `js-tiktoken` with the `o200k_base` reference encoding. It makes no AI-service request and uses the same complete text as copy and download, regardless of the search filter. Your selected model may tokenize it differently; this is not a bill, context-limit check, or prediction of total task usage.

Extraction makes no AI-service requests. Optional Douyin speech recognition uses local compute, not a cloud token allowance. Once you submit the handoff, Codex's existing context, skill instructions, reasoning, tool results, screenshots, and generated brief—including claim checks—can add usage across multiple steps. Generated output is outside the displayed input estimate. Model and plan also matter; see [official Codex usage guidance](https://learn.chatgpt.com/docs/pricing). The app cannot infer your remaining allowance or turn this text estimate into a reliable price.

For a lighter first pass, add **“Give me a caption-only brief; skip screenshots.”** This still reads all captions but skips visual inspection. Attaching the downloaded file instead of pasting includes the same text and does not reduce its token count. For a very long transcript, check the selected model's context limit before submitting it.

## Source format and architecture

<details>
<summary>Explore the transcript format, project structure, and API</summary>

YouTube exports use `youtube-transcript/v1`; Douyin exports use `video-transcript/v1`. Both include a JSON metadata block with title, canonical URL, channel, language, caption source, provider, video duration, segment count, and caption coverage. Numbered headings retain start/end times as `HH:MM:SS.mmm`; text is fenced separately. Existing captions preserve their source durations and wording. Speech exports explicitly identify estimated word alignment and review flags. Unicode, line breaks, repeated captions, and overlapping cues are preserved.

```text
backend/                 FastAPI API, YouTube retrieval, Douyin jobs and speech worker
frontend/                React interface, Markdown export, locked npm dependencies
stt/                     Optional Apple Silicon and Windows speech runtimes/model setup
skills/video-brief/       Portable analysis instructions for a separate AI assistant
scripts/                 Export verification utility
docs/ARCHITECTURE.md      Current boundaries and proposed extension points
ROADMAP.md               Platform and browser-extension plans
```

### API

- `POST /api/video-info` with `{"url":"https://www.youtube.com/watch?v=Hrbq66XqtCo"}` returns metadata, available tracks, and a recommended track ID.
- `POST /api/extract` with `{"url":"Hrbq66XqtCo","track_id":"uploaded:en"}` retrieves the chosen track. Use a track ID returned by video-info.
- `POST /api/douyin/jobs` starts a single-link browser job; status, manual confirmation, cancellation and local playback routes are described in [docs/DOUYIN.md](docs/DOUYIN.md).
- `GET /health` reports service health; its mode label does not mean speech is installed.
- `GET /api/capabilities` reports the configured speech backend and runtime readiness without loading its model or downloading media.

Transcript records include `text`, fractional `start` and `duration`, plus display `timestamp` and legacy `seconds`. The response also identifies language, source, provider, and track. Extraction failures are explicit rather than being hidden behind successful metadata retrieval.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the proposed common platform adapter and player bridge. These are design directions, not APIs implemented today.

</details>

## Limitations and data handling

The YouTube path retrieves existing captions only and does not download media or transcribe speech. Douyin also defaults to captions only, blocking player media during metadata/caption probing and keeping retrieved text in memory. Explicitly enabling its local speech fallback permits a temporary video download when no timed track is exposed; separate speech setup downloads a pinned 1.6 GB model. Neither path translates captions, uploads media, or requires an AI API key.

YouTube supports accessible public videos with existing captions. Douyin is limited to individual videos whose normal browser playback exposes usable metadata; manual login or verification may still not resolve platform restrictions. Private, removed, restricted, sign-in-required, or captionless videos may fail. Errors distinguish invalid links, unavailable videos, missing or empty captions, blocked requests, and connection failures. YouTube can change caption access; the app does not bypass authentication or access restrictions.

The server binds to `127.0.0.1` and checks local hosts/origins. It is intended for personal local use, not public hosting. YouTube metadata and signed caption URLs are cached in process memory for up to five minutes, with a maximum of eight videos. YouTube transcript results stay in browser memory until copied, downloaded, replaced, or the page is closed. Thumbnails are loaded from YouTube. No transcript library or automatic upload is maintained. Douyin keeps the current job and caption result in server memory until replaced or normal shutdown. Optional speech keeps acquired media locally for playback and retry; cancelled jobs and failures without validated audio are cleared. Page reload restores the current Douyin job while the server stays running. Its dedicated login profile persists separately from jobs, under the user's home directory by default or an explicitly configured location. A forced crash can leave temporary files; see the Douyin guide for cleanup.

Captions may contain mistakes, omit visuals, or leave gaps. Follow timestamp links to check the original context before relying on an important claim. Users remain responsible for how they use and share source material.

## Development and verification

<details>
<summary>Development commands, tests, and contribution notes</summary>

After setup, run these checks from the repository root:

```bash
backend/.venv/bin/python -m pytest -q
backend/.venv/bin/ruff check backend/main.py backend/app backend/workers backend/tests tests stt/download_model.py stt/download_windows_model.py
npm test --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
```

On Windows, use `backend/.venv/Scripts/python.exe -m pytest -q` and `backend/.venv/Scripts/ruff.exe check backend/main.py backend/app backend/workers backend/tests tests stt/download_model.py stt/download_windows_model.py`; the npm commands are the same. See [Windows validation](docs/WINDOWS.md#validation-and-readiness) for offline and live-test boundaries.

For frontend development, keep the backend running and run `npm run dev --prefix frontend -- --host 127.0.0.1`. Vite forwards local API calls to port 8000. Production uses the same origin as FastAPI.

Tests cover URL formats, selected languages and sources, provider fallback, explicit errors, timing, complete exports, source-fence handling, and disabled media downloads. See [VERIFICATION.md](VERIFICATION.md) for results and limitations. GitHub Actions runs the offline regression checks and frontend build; live YouTube access is not a CI requirement.

Contributions are welcome, particularly caption-provider adapters, player seeking, accessibility, and regression cases. See [CONTRIBUTING.md](CONTRIBUTING.md) before adding a new platform.

</details>

## Attribution

- **Application foundation:** [samueladegoke/yt-transcript-web](https://github.com/samueladegoke/yt-transcript-web), by Samuel Adegoke / Sam Ade, adapted from commit [`4928098`](https://github.com/samueladegoke/yt-transcript-web/commit/4928098ffe109df932a8f4bae47f31ebcf314a10) under the [MIT license](LICENSE). This adaptation retains its React/FastAPI foundation and caption extraction library, and changes retrieval behavior, timing, export, layout, and the AI handoff workflow. The original upstream AI/MCP services and deployment configuration are not part of this version.
- **Caption retrieval:** [jdepoix/youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api).
- **Metadata and caption fallback:** [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp), used with media downloads disabled.
- **Douyin browser workflow:** [Playwright](https://github.com/microsoft/playwright-python), independently implemented with an official-page browser, restricted metadata refresh, and caption-only requests. Public [jiji262/douyin-downloader](https://github.com/jiji262/douyin-downloader) documentation and local architectural study of Douzy informed the persistent-browser, narrow-operation and worker-state design. Douzy is not a dependency; no proprietary Douzy source or assets are included.
- **Douyin caption formats:** The public [yt-dlp TikTok/Douyin extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/tiktok.py) documents the explicit caption fields and millisecond `utterances` format used as references for our independent discovery/parser. Supported schemas do not imply every Douyin video exposes a track. See [acquisition boundaries](docs/DOUYIN.md#acquisition-boundaries).
- **Optional local speech:** [MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) / [MLX](https://github.com/ml-explore/mlx) on Apple Silicon; [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [CTranslate2](https://github.com/OpenNMT/CTranslate2), and [PyAV](https://github.com/PyAV-Org/PyAV) on Windows. Model conversions and optional NVIDIA runtime notices are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
- **Frontend design guidance:** [Leonxlnx/taste-skill](https://github.com/Leonxlnx/taste-skill), especially its existing-project redesign workflow.
- **Implementation stack:** React, Vite, Tailwind CSS, FastAPI, Uvicorn, Lucide icons, and self-hosted Geist fonts. Source links and license notes are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

The original copyright notice is preserved unchanged. Local adaptations and the video-brief skill are maintained by [Jack-Li-Npu](https://github.com/Jack-Li-Npu). This repository starts from a clean source snapshot; the upstream commit is recorded in this section rather than republishing its historical deployment files. This is an independent project, not an official YouTube, TikTok, Douyin, Bilibili, or OpenAI product.
