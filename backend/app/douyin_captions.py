"""Discover and parse existing Douyin captions without requesting video media.

Only explicit caption fields are inspected. Descriptions, music metadata, and
arbitrary strings in a playback response are never treated as a transcript.
"""

from __future__ import annotations

import html
import json
import math
import re
from collections.abc import Callable, Iterator
from decimal import Decimal, InvalidOperation

from .transcript_service import ExtractionError

_TIMESTAMP = r"(?:\d{2,}:)?\d{2}:\d{2}[.,]\d{3}"
_CUE_TIMING = re.compile(rf"^({_TIMESTAMP})[ \t]+-->[ \t]+({_TIMESTAMP})(?:[ \t]+(.*))?$")
_CAPTION_MARKUP = re.compile(
    rf"</?(?:b|i|u|c(?:\.[\w-]+)*|v|lang|ruby|rt|font)(?:[ \t][^>\n]*)?>|<{_TIMESTAMP}>",
    re.IGNORECASE,
)
_UNSAFE_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ufffd]")
_NUMERIC = re.compile(r"\d+(?:\.\d+)?")


def _invalid() -> ExtractionError:
    return ExtractionError(
        "captions_invalid",
        "The returned caption track has no usable timed text. Choose another track or use speech recognition.",
        422,
    )


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or _UNSAFE_TEXT.search(value):
        raise _invalid()
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise _invalid() from exc
    return value


def _milliseconds(value: object) -> Decimal:
    # Some caption endpoints encode numeric milliseconds as decimal strings.
    # Booleans, exponent strings, infinities, and missing timing are not times.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise _invalid()
    if isinstance(value, str) and not _NUMERIC.fullmatch(value):
        raise _invalid()
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise _invalid() from exc
    if not number.is_finite() or number < 0:
        raise _invalid()
    return number / 1000


def _record(start: Decimal, end: Decimal, wording: object) -> dict:
    if end <= start:
        raise _invalid()
    start_seconds, duration = float(start), float(end - start)
    if not math.isfinite(start_seconds) or not math.isfinite(duration) or duration <= 0:
        raise _invalid()
    return {"start": start_seconds, "duration": duration, "text": _text(wording)}


def _utterances(data: dict) -> list[dict]:
    cues = data.get("utterances")
    if not isinstance(cues, list) or not cues:
        raise _invalid()
    records = []
    for cue in cues:
        if not isinstance(cue, dict):
            raise _invalid()
        start = _milliseconds(cue.get("start_time"))
        end = _milliseconds(cue.get("end_time"))
        records.append(_record(start, end, cue.get("text")))
    return records


def _seconds(value: str) -> Decimal:
    parts = value.replace(",", ".").split(":")
    hours, minutes, seconds = ("0", *parts) if len(parts) == 2 else parts
    if int(minutes) > 59 or Decimal(seconds) >= 60:
        raise _invalid()
    return Decimal(hours) * 3600 + Decimal(minutes) * 60 + Decimal(seconds)


def _timed_text(value: str) -> list[dict]:
    blocks = re.split(r"\n[ \t]*\n", value.replace("\r\n", "\n").replace("\r", "\n").strip("\n"))
    webvtt = bool(re.match(r"^WEBVTT(?:[ \t\n]|$)", blocks[0]))
    if webvtt:
        if "-->" in blocks[0]:
            raise _invalid()
        blocks = blocks[1:]
    records = []
    for block in blocks:
        if not block.strip():
            continue
        lines = block.split("\n")
        first = lines[0].strip()
        if webvtt and (re.match(r"^NOTE(?:[ \t]|$)", first) or first in {"STYLE", "REGION"}):
            continue
        timing_index = 0 if "-->" in first else 1
        if len(lines) <= timing_index + 1:
            raise _invalid()
        timing = _CUE_TIMING.fullmatch(lines[timing_index].strip())
        if timing is None:
            raise _invalid()
        # Cue layout settings do not change timing or caption text. Reject stray
        # trailing prose rather than interpreting a malformed timing line.
        settings = timing.group(3)
        if settings and any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*:[^\s]+", item) for item in settings.split()):
            raise _invalid()
        wording = html.unescape(_CAPTION_MARKUP.sub("", "\n".join(lines[timing_index + 1 :])))
        records.append(_record(_seconds(timing.group(1)), _seconds(timing.group(2)), wording))
    if not records:
        raise _invalid()
    return records


def caption_records(text_or_dict: str | dict) -> list[dict]:
    """Read utterance JSON (milliseconds) or SRT/WebVTT into exact timed cues.

    Input order, repeated/overlapping cues, and fractional durations are retained.
    A partial or malformed track fails as a whole instead of silently losing text.
    """
    if isinstance(text_or_dict, dict):
        return _utterances(text_or_dict)
    if not isinstance(text_or_dict, str):
        raise _invalid()
    value = text_or_dict.removeprefix("\ufeff")
    _text(value)
    if value.lstrip().startswith(("{", "[")):
        try:
            data = json.loads(value)
        except (ValueError, RecursionError) as exc:
            raise _invalid() from exc
        if not isinstance(data, dict):
            raise _invalid()
        return _utterances(data)
    return _timed_text(value)


def _entries(value: object) -> Iterator[dict]:
    if isinstance(value, list):
        yield from (entry for entry in value if isinstance(entry, dict))


def _caption_entries(data: dict) -> Iterator[dict]:
    video = data.get("video")
    video = video if isinstance(video, dict) else {}
    yield from _entries(data.get("subtitle_infos"))
    yield from _entries(video.get("subtitleInfos"))
    subtitles = video.get("subtitle")
    if isinstance(subtitles, dict):
        yield from _entries(subtitles.get("subtitleInfos"))
    cla_info = video.get("cla_info")
    if isinstance(cla_info, dict):
        yield from _entries(cla_info.get("caption_infos"))
    for sticker in _entries(data.get("interaction_stickers")):
        info = sticker.get("auto_video_caption_info")
        if isinstance(info, dict):
            yield from _entries(info.get("auto_captions"))


def _addresses(entry: dict) -> Iterator[str]:
    for key in ("Url", "url", "UrlList", "url_list"):
        address = entry.get(key)
        if isinstance(address, str):
            yield address
        elif isinstance(address, dict):
            for list_key in ("url_list", "UrlList"):
                candidates = address.get(list_key)
                if isinstance(candidates, list):
                    yield from (url for url in candidates if isinstance(url, str))
        elif isinstance(address, list):
            yield from (url for url in address if isinstance(url, str))


def _label(entry: dict, keys: tuple[str, ...], default: str) -> str:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, str) and value.strip() and len(value) <= 120:
            try:
                return _text(value).strip()
            except ExtractionError:
                continue
    return default


def discover_caption_tracks(data: dict, url_validator: Callable[[str], bool]) -> list[dict]:
    """Return public track choices with private safe URL or validated inline cues.

    ``_inline`` uses the same explicit ``utterances`` schema as ``caption_records``.
    An inline-only choice omits ``_url``. Private fields must be removed from API
    responses by the caller, just as signed media addresses are removed.
    """
    if not isinstance(data, dict):
        return []
    tracks, seen_urls, seen_inline = [], set(), set()
    for entry in _caption_entries(data):
        addresses = list(dict.fromkeys(url for url in _addresses(entry) if url_validator(url)))
        inline, records = None, None
        if "utterances" in entry:
            try:
                records = caption_records({"utterances": entry["utterances"]})
                inline = {
                    "utterances": [
                        {key: cue[key] for key in ("start_time", "end_time", "text")} for cue in entry["utterances"]
                    ]
                }
            except ExtractionError:
                pass
        if not addresses and inline is None:
            continue
        language = _label(entry, ("LanguageCodeName", "languageCodeName", "lang", "language"), "und")
        inline_key = (language, tuple((cue["start"], cue["duration"], cue["text"]) for cue in records or []))
        if any(url in seen_urls for url in addresses) or (inline is not None and inline_key in seen_inline):
            continue
        track = {
            "id": f"platform:{len(tracks)}",
            "language": language,
            "name": _label(entry, ("LanguageName", "languageName", "name"), language),
            "source": "platform",
        }
        if addresses:
            track["_url"] = addresses[0]
            seen_urls.update(addresses)
        if inline is not None:
            track["_inline"] = inline
            seen_inline.add(inline_key)
        tracks.append(track)
    return tracks
