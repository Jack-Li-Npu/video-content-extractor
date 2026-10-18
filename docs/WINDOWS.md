# Windows installation and local speech

The app runs locally on Windows x64. YouTube and Douyin caption retrieval need no speech model. Douyin's optional speech fallback uses faster-whisper/CTranslate2 on NVIDIA CUDA or CPU, with PyAV for media probing and audio decoding. A system FFmpeg installation is not required for that Windows backend.

## Install and launch

Install Git, [uv](https://docs.astral.sh/uv/getting-started/installation/), and Node.js 22.12 or newer with npm. Use Windows PowerShell 5.1 or newer. From PowerShell:

```powershell
git clone https://github.com/Jack-Li-Npu/video-content-extractor.git
cd video-content-extractor
.\setup.cmd
.\Start-Video-Content-Extractor.cmd
```

Setup creates an isolated Python environment under `backend/.venv`, installs locked dependencies, installs Playwright Chromium, and builds the web interface. uv can provision the selected Python version; a global Python command is not required. Existing `.env` is preserved, or created from `.env.example` when missing. No Windows service or automatic startup at sign-in is added by these scripts.

Start opens **http://127.0.0.1:8000** and runs the server in the background. It reuses this installation's healthy server and refuses to replace an unrelated service on port 8000. Use `Stop-Video-Content-Extractor.cmd` to stop the recorded installation. Stop verifies process identity, attempts graceful shutdown, and uses an exact-process fallback if needed; it does not terminate every Python/browser process. Cancel an active extraction before stopping when practical. Forced termination can leave temporary media.

After changing settings, stop and start the app, then refresh the web page. Logs are under `.runtime/server.stdout.log` and `.runtime/server.stderr.log`; generated dependencies, browser binaries, models, and runtime records are ignored by Git.

## Optional local speech

Run once from the project root:

```powershell
.\setup-douyin.cmd
```

The default `-Device auto` queries `nvidia-smi`. A responding NVIDIA GPU/driver enables the CUDA extra; otherwise setup installs CPU dependencies and retains `STT_DEVICE=auto`. This check decides which dependencies to install; runtime readiness and an actual extraction are separate checks.

For an explicit choice:

```powershell
.\setup-douyin.cmd -Device cpu
# Or, with an NVIDIA GPU and working driver:
.\setup-douyin.cmd -Device cuda
```

CUDA setup requires a compatible NVIDIA driver and the Microsoft Visual C++ 2015–2022 x64 runtime. Explicit `-Device cuda` fails before dependency/model downloads if no responding NVIDIA GPU/driver is found. The optional CUDA 12 cuBLAS/cuDNN 9 wheels install inside the isolated speech environment; they add about 1.3 GB, plus other dependencies. A full CUDA Toolkit installation is not required by this packaged runtime. CPU mode omits the CUDA extra and typically takes longer to transcribe.

Speech setup creates `stt/windows/.venv` and downloads pinned [dropbox-dash/faster-whisper-large-v3-turbo](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo) files to `stt/windows/models/whisper-turbo`. `model.bin` is approximately 1.618 GB; tokenizer/configuration files and dependencies need additional space. Setup checks each file's size and SHA-256 before accepting it. Matching files are reused; interrupted partials may resume on a later run. A checksum mismatch is rejected, never loaded as a verified model.

Setup adds only missing `STT_BACKEND`, `STT_DEVICE`, and `STT_COMPUTE_TYPE` keys to `.env`. It preserves existing choices and custom runtime/model paths. Therefore, rerunning with `-Device cuda` does not overwrite a previous `STT_DEVICE=cpu`; edit `.env` when changing the active device:

```dotenv
STT_BACKEND=faster-whisper
STT_DEVICE=auto
STT_COMPUTE_TYPE=auto
```

`auto` resolves to detected NVIDIA CUDA or CPU at runtime. Automatic precision uses `int8_float16` on CUDA and `int8` on CPU. A CUDA inference error is reported rather than silently rerun on CPU; set `STT_DEVICE=cpu` explicitly if desired. Advanced directory overrides are documented in [`.env.example`](../.env.example). Paths copied from a Mac installation must be updated or removed.

After setup, restart the app and refresh the page. In the **Douyin** tab, enable **Allow video download for local speech recognition** only for a job where you want that fallback. Existing timed captions are still preferred. No caption track means the opted-in flow downloads a temporary video, checks its audio, closes the browser, and transcribes locally. Speech wording/timing are estimates; on-screen text is not transcribed.

## Browser and login behavior

The dedicated Douyin browser profile is separate from your everyday Chrome/Edge data. Default profile location is `~/.local/share/efficient-content-extractor/douyin-browser`, under the current user's home directory; `DOUYIN_BROWSER_PROFILE` can override it. Windows folders use inherited Windows access controls.

`DOUYIN_BROWSER_CHANNEL=auto` detects Chrome in standard Windows installation paths, otherwise uses the Playwright Chromium installed by setup. Explicit options are `chrome`, `chromium`, or `msedge`; the selected browser must be installed. Do not point the dedicated profile at an everyday browser profile or remove its lock while a browser is using it.

Public video metadata may be accessible without authentication. Only when extraction pauses for verification should you complete the requested login/site check in the official Douyin window and click **I’ve verified, continue**. This app does not solve CAPTCHA or collect credentials.

The window closes after caption checking, before speech inference, or on failure. A brief popup followed by closure is not proof of a failed login. Read the status in the web app: `captions_not_found` means no timed track was exposed; `speech_setup_required` means the optional fallback was not prepared. Subtitles visible in the picture may be burned-in pixels; this app does not implement OCR.

## Proxy settings

For YouTube, set your own proxy address in `.env` when needed:

```dotenv
YT_PROXY=http://127.0.0.1:YOUR_PORT
```

Replace `YOUR_PORT` with the actual port. Windows setup reads a literal `YT_PROXY` before its first dependency download and fills unset `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, and `YT_PROXY` process values. It does not execute `.env`, expand shell expressions, or overwrite existing shell proxy settings unless `-Proxy` is supplied explicitly:

Installer and model downloads require an **HTTP(S) proxy endpoint**. YouTube extraction can use a SOCKS proxy through its Requests backend, but a `socks5://` address used there cannot be reused for this installer. Use your proxy application's HTTP or mixed port for setup.

```powershell
.\setup.cmd -Proxy http://127.0.0.1:YOUR_PORT
.\setup-douyin.cmd -Proxy http://127.0.0.1:YOUR_PORT
```

The setup scripts restore process environment values afterward and do not alter the system proxy. The Windows Start launcher explicitly loads documented `.env` values into its child server process, overriding same-named inherited values for that launch. Thus `.env` is the durable configuration for the shortcut. Keep the configured proxy running, or remove its settings for direct access. These settings do not configure the dedicated Douyin browser or its media downloader; those have their own normal connection path.

## Validation and readiness

`GET /health` confirms the web service is running. Its `optional-local-speech` mode label does not establish that speech was installed.

`GET /api/capabilities` reports the selected backend and speech readiness. It checks required model-file presence plus runtime imports/device/precision/libraries in a separate process, without loading the model or downloading media. Successful probes are cached briefly. This is not a transcription test, a checksum recheck, or a guarantee of available GPU memory. Setup verifies model checksums; a real extraction tests the complete media-to-transcript path.

Offline checks from the project root:

```powershell
backend/.venv/Scripts/python.exe -m pytest -q
backend/.venv/Scripts/ruff.exe check backend/main.py backend/app backend/workers backend/tests tests stt/download_model.py stt/download_windows_model.py
npm test --prefix frontend
npm run lint --prefix frontend
npm run build --prefix frontend
```

See [VERIFICATION.md](../VERIFICATION.md) for the actual live tests and their limits. Historical Apple Silicon successes do not validate Windows GPU inference or guarantee current Douyin/YouTube access.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| `uv` / Node missing, or setup fails before downloads | Install the required tools, open a new PowerShell window, and rerun setup. Node.js must be at least 22.12. |
| Download timeout or SHA-256 mismatch | Check the proxy/connection and rerun the same speech setup. Verified files are reused; partials can resume. The installer will not accept mismatched model bytes. |
| Browser executable missing | Rerun `setup.cmd`, or select an installed Chrome/Edge with `DOUYIN_BROWSER_CHANNEL`, then restart. |
| Dedicated profile already in use | Close the other dedicated Douyin window or extractor instance; retry without deleting active profile locks. |
| Missing local speech runtime/model | Rerun `setup-douyin.cmd`; inspect custom `.env` paths/backend keys. Restart and refresh afterward. |
| CUDA/DLL/device error | Check the NVIDIA driver, Visual C++ x64 runtime, and CUDA extra; rerun `setup-douyin.cmd -Device cuda`. Check free GPU memory. To use CPU, set `STT_DEVICE=cpu` and `STT_COMPUTE_TYPE=auto` in `.env`, then restart. |
| CPU is selected unexpectedly | Verify `nvidia-smi` works, CUDA dependencies are installed, and `.env` does not retain `STT_DEVICE=cpu`. Read `/api/capabilities` for the resolved mode. |
| Port 8000 belongs to another service | Stop or reconfigure that service yourself, or use a separate manual launch configuration. The shortcut leaves it untouched. |
| Start says already running after settings changed | Use this installation's Stop shortcut, then Start. Reusing the existing process does not reload configuration. |
| Stop reports an identity mismatch | The recorded PID no longer matches. No unrelated process is stopped; inspect the running app/record before retrying. |
| Local player expired | Keep the current extractor/job running. Starting another Douyin job or stopping the server removes its temporary media. |

For caption/verification errors and data-retention details, see the [Douyin guide](DOUYIN.md).
