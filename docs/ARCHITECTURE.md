# Architecture and extension points

## Current implementation

```text
React interface
  ├─ /api/video-info → yt-dlp metadata and caption choices
  ├─ /api/extract → youtube-transcript-api
  │                  └─ selected-track yt-dlp caption fallback
  ├─ /api/douyin/jobs → dedicated browser + manual verification when needed
  │                     ├─ matching metadata + narrow same-page detail refresh
  │                     ├─ exposed timed captions → selected JSON/SRT/VTT in memory
  │                     │    └─ player media blocked throughout caption probe
  │                     └─ absent captions + explicit speech opt-in
  │                          └─ temporary video → offline MLX Whisper worker
  └─ complete timestamped Markdown / clipboard
                       ↓ user-controlled handoff
                  external AI assistant + video-brief
                       ↓
                  brief with source links or available current-job local video links
```

`backend/app/transcript_service.py` handles YouTube URL parsing, metadata, caption selection, retrieval, normalization, and error classification. `backend/main.py` serves those two API routes, the Douyin job routes and the built React app. The frontend keeps results in memory and generates the handoff document in `frontend/src/lib/transcript.js`.

`backend/app/douyin_service.py` manages one background job, a dedicated headed Playwright profile, manual browser confirmation and exact video identity. Both API and interface default to `allow_asr=false`. Metadata may arrive through observed page responses or `RENDER_DATA`; `backend/app/douyin_browser.py` can refresh only the requested video's detail route in the official page context. It does not generate signatures or expose an arbitrary request proxy. Browser media requests are blocked and autoplay is paused during caption probing, with service workers disabled for the extraction context.

`backend/app/douyin_captions.py` discovers explicit caption fields and normalizes timed JSON utterances, SRT and WebVTT. It preserves order, repetition, overlap, fractional start/end times and line breaks, and rejects malformed tracks as a whole. Valid inline captions need no asset request. External caption text is fetched through the same browser into bounded memory; signed URLs and private fields are removed from public responses. Multiple tracks pause in `waiting_track`; `/confirm` carries the selected `track_id`. Missing captions remain an explicit failure in the default mode. Descriptions, music metadata, AI summaries and burned-in picture text are not treated as transcripts.

Explicit speech opt-in permits allowlisted video acquisition only when no track is exposed. Optional speech runs in a separate Python 3.12 worker (`backend/workers/local_subtitles.py`) with locked MLX dependencies in `stt/`; the API process does not import MLX. Speech orchestration and safe worker diagnostics live in `backend/app/speech_service.py`. A `music.playUrl` is not assumed to contain complete video narration, so it cannot substitute for the validated source audio.

Current-job media, when acquired, is served locally with range support and fractional hash seeking. Caption-only jobs have no local viewer, and public Douyin links are not assumed to support exact seeks. The primary button handles manual confirmation, track selection or speech retry; other stages continue automatically. Current-job status restores progress/results on browser reload. A failed job with validated audio retains media for direct speech retry; cancellation or replacement/shutdown clears it. Completed media lasts until replacement or normal shutdown. See [DOUYIN.md](DOUYIN.md) for acquisition validation and data handling.

The Douyin workflow applies general architectural lessons from Douzy: persistent manual verification, narrow page operations, and explicit worker state transitions. These are independently implemented with public dependencies; the application does not call Douzy or include recovered proprietary code/assets. The public [yt-dlp TikTok/Douyin extractor](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/tiktok.py) supplies reference caption field formats, not a guarantee of live availability. Historical successful Douyin speech tests do not validate native captions.

`skills/video-brief/SKILL.md` contains analysis instructions; it is not executed by the web server. It asks the assistant to read all segments, cite source intervals, flag caption uncertainty, and selectively examine consequential claims with labelled alternative explanations and links to their support or qualifications. This review helps the user assess the argument; it does not independently verify facts or automatically request external research. There is no model-service integration in the current app.

## Proposed platform boundary (not yet implemented)

Move platform-specific behavior behind a provider selected from a validated URL. A provider should support metadata inspection, track listing, selected-track retrieval, and timestamp-link generation. Share the normalized segment representation (`start`, `duration`, `text`) and common errors, but retain source-specific identifiers such as a Bilibili part.

YouTube documents retain `youtube-transcript/v1`. Douyin uses `video-transcript/v1` with explicit platform, canonical identity/source URL, caption provenance, acquisition provider, requested caption-only mode, actual media-download status, estimated/platform timing, review-flag counts and an optional local player URL. Numbered headings remain segment IDs. The analysis skill uses platform-appropriate links; a public Douyin seek parameter is not assumed. Future adapters should preserve this distinction and add video-part identity when required.

## Proposed player boundary (not yet implemented)

The browser extension would contain a platform-specific player bridge responsible for identifying the active video, reading its current playback position, and seeking to a validated timestamp. Keep retrieval independent of the player bridge so a transcript can still be exported without an extension.

A brief import should have a source identity and bounded, finite time intervals. Validate them against the active video before seeking. Account for route changes, multiple videos, and unsupported player states. Seek only in response to a user action; a highlighted takeaway need not automatically start playback.

If the extension uses the local FastAPI service, design a narrow, explicit connection and permissions model. Existing host/origin checks are intentionally limited to the local web app; an extension is not supported by simply adding a wildcard origin. No such bridge or permission mechanism is shipped today.

## Stable behavior to preserve

- YouTube uses existing captions only, with no media/model download or translation.
- Douyin defaults to exposed timed captions in memory with player media blocked during probing; local speech requires explicit video-download permission and separate setup.
- Multiple Douyin tracks require a user selection; missing or malformed captions produce explicit errors.
- Native caption-only Douyin timestamps do not imply a local viewer or reliable public-page seeking.
- Keep original caption wording separate from estimated speech text and its review flags.
- Fractional start times and actual durations, including overlap and long videos.
- Explicit extraction errors separate from metadata success.
- Search affects the view; full-document handoff includes every retrieved segment.
- Analysis happens only after the user chooses to send source text to an assistant.

## Independent frontend channels

The interface mounts one `TranscriptWorkspace` per platform and hides the inactive tab without unmounting it. Each instance owns its URL, selected track, transcript, search, errors and pending request state. YouTube requests therefore cannot overwrite a Douyin result, and Douyin polling continues when its tab is hidden. Only the Douyin workspace restores `/api/douyin/current-job`; neither switching tabs nor clearing YouTube sends a Douyin job mutation.

`ChannelTabs` provides labelled tab panels and Arrow Left/Right, Home and End keyboard navigation. Channel mismatches are blocked before submission, while backend URL validation remains authoritative. Token counting starts only when the result's channel is visible and caches the completed estimate for that result. Results are not duplicated into local storage. A reload clears YouTube page state; the current Douyin job can be restored from the running server.
