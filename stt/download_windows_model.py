"""Install pinned CTranslate2 Whisper data, checking every size and SHA-256."""

import argparse
import hashlib
import http.client
import json
import os
import re
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from dotenv import load_dotenv

REPO = "dropbox-dash/faster-whisper-large-v3-turbo"
REVISION = "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
FILES = {
    "model.bin": (1617884929, "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
    "config.json": (2263, "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"),
    "preprocessor_config.json": (340, "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"),
    "tokenizer.json": (2710337, "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"),
    "vocabulary.json": (1068114, "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"),
}
APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATION = APP_ROOT / "stt/windows/models/whisper-turbo"


def matches(path, size, sha):
    if not path.is_file() or path.stat().st_size != size:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == sha


class DownloadError(RuntimeError):
    """Safe diagnostics that do not expose signed redirect or proxy URLs."""


def _digest(path):
    if not path.is_file():
        return 0, "missing"
    with path.open("rb") as stream:
        return path.stat().st_size, hashlib.file_digest(stream, "sha256").hexdigest()


def _strong_etag(value):
    if not isinstance(value, str):
        return ""
    value = (value or "").strip()
    if value.startswith('"') and value.endswith('"') and len(value) <= 512 and not re.search(r"[\r\n]", value):
        return value
    return ""


def _fresh_url(url, attempt):
    if attempt == 0:
        return url
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query.append(("_vce_retry", uuid.uuid4().hex))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def download_file(opener, url, target, size, sha, retries=3, progress=None):
    """Download bounded pinned bytes, resuming interruptions and checking before promotion.

    A retry is one HTTP request, including continuation of a valid short 206.
    Interrupted partials and validator metadata survive for a later invocation.
    """
    target = Path(target)
    if matches(target, size, sha):
        return target
    if retries < 1:
        raise ValueError("retries must be at least 1")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".partial")
    metadata_path = partial.with_suffix(partial.suffix + ".json")
    identity = {"repo": REPO, "revision": REVISION, "size": size, "sha256": sha}
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    if any(metadata.get(key) != value for key, value in identity.items()) or (
        partial.is_file() and partial.stat().st_size > size
    ):
        partial.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        metadata = dict(identity)

    last_error = "download did not complete"
    for attempt in range(retries):
        offset = partial.stat().st_size if partial.is_file() else 0
        if offset == size:
            actual_size, actual_sha = _digest(partial)
            if actual_sha == sha:
                partial.replace(target)
                metadata_path.unlink(missing_ok=True)
                return target
            last_error = f"SHA-256 mismatch: actual bytes={actual_size}, actual sha256={actual_sha}"
            print(f"{target.name}: {last_error}", flush=True)
            partial.replace(target.with_suffix(target.suffix + ".invalid"))
            metadata_path.unlink(missing_ok=True)
            metadata = dict(identity)
            offset = 0
        headers = {"Accept-Encoding": "identity", "Cache-Control": "no-cache"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            validator = _strong_etag(metadata.get("etag"))
            if validator:
                headers["If-Range"] = validator
        request = urllib.request.Request(_fresh_url(url, attempt), headers=headers)
        print(f"{target.name}: request {attempt + 1}/{retries}, starting at {offset}/{size} bytes", flush=True)
        try:
            with opener.open(request, timeout=90) as response:
                status = response.getcode()
                content_range = response.headers.get("Content-Range", "")
                if status == 206:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range.strip())
                    if match is None:
                        raise DownloadError("Missing or malformed Content-Range")
                    start, end, total = map(int, match.groups())
                    if start != offset or total != size or end < start or end >= total:
                        raise DownloadError(f"Unexpected Content-Range (start={start}, end={end}, total={total})")
                    expected_body = end - start + 1
                elif status == 200:
                    # If a server ignores Range/If-Range, overwrite instead of appending.
                    offset = 0
                    expected_body = size
                else:
                    raise DownloadError(f"Unexpected HTTP status {status}")
                length_header = response.headers.get("Content-Length")
                if length_header is not None:
                    try:
                        content_length = int(length_header)
                    except ValueError as exc:
                        raise DownloadError("Invalid Content-Length") from exc
                    if content_length != expected_body:
                        raise DownloadError(f"Unexpected Content-Length: actual={content_length}, expected={expected_body}")
                metadata = {**identity, "etag": _strong_etag(response.headers.get("ETag"))}
                # No URLs or credentials enter resumable metadata.
                metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
                last_report = time.monotonic()
                last_report_bytes = offset
                received = 0
                with partial.open("ab" if offset else "wb") as stream:
                    while chunk := response.read(8 * 1024 * 1024):
                        if received + len(chunk) > expected_body or offset + received + len(chunk) > size:
                            raise DownloadError("Response body exceeds its declared range or pinned model size")
                        stream.write(chunk)
                        received += len(chunk)
                        completed = offset + received
                        if progress is not None:
                            progress(completed, size)
                        elif completed - last_report_bytes >= 64 * 1024 * 1024 or time.monotonic() - last_report >= 5:
                            print(f"{target.name}: {completed}/{size} bytes ({100 * completed / size:.1f}%)", flush=True)
                            last_report = time.monotonic()
                            last_report_bytes = completed
                actual_size = partial.stat().st_size
                if received != expected_body:
                    raise DownloadError(f"Incomplete response: received={received}, expected={expected_body}; partial bytes={actual_size}")
                if actual_size < size:
                    raise DownloadError(f"Partial response retained: actual bytes={actual_size}, expected={size}")
                actual_size, actual_sha = _digest(partial)
                if actual_sha != sha:
                    last_error = f"SHA-256 mismatch: actual bytes={actual_size}, actual sha256={actual_sha}"
                    print(f"{target.name}: {last_error}", flush=True)
                    partial.replace(target.with_suffix(target.suffix + ".invalid"))
                    metadata_path.unlink(missing_ok=True)
                    metadata = dict(identity)
                    raise DownloadError(last_error)
                partial.replace(target)
                metadata_path.unlink(missing_ok=True)
                return target
        except (OSError, DownloadError, http.client.HTTPException) as exc:
            # Preserve partial data on transport failures, short responses and
            # interruptions. Corrupt complete bodies were moved to .invalid.
            last_error = str(exc) if isinstance(exc, DownloadError) else type(exc).__name__
            print(f"{target.name}: {last_error}; retrying with a fresh cache key" if attempt + 1 < retries
                  else f"{target.name}: {last_error}", flush=True)
            if attempt + 1 < retries:
                time.sleep(min(attempt + 1, 3))
    actual_size, actual_sha = _digest(partial)
    raise DownloadError(
        f"Could not verify {target.name} after {retries} requests. {last_error}. "
        f"Retained partial bytes={actual_size}, sha256={actual_sha}; run setup again to resume."
    )


def main():
    load_dotenv(APP_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path(os.environ.get("STT_MODEL_DIR") or DEFAULT_DESTINATION))
    parser.add_argument("--verify-only", action="store_true", help="Verify existing files without network requests.")
    parser.add_argument("--retries", type=int, default=4, help="Maximum HTTP requests per file, including resume requests.")
    args = parser.parse_args()
    destination = args.destination.expanduser()
    if not destination.is_absolute():
        destination = APP_ROOT / destination
    if not args.verify_only:
        destination.mkdir(parents=True, exist_ok=True)
    proxy = os.environ.get("YT_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if proxy and not args.verify_only:
        try:
            proxy_scheme = urllib.parse.urlsplit(proxy).scheme.lower()
        except ValueError:
            proxy_scheme = ""
        if proxy_scheme not in {"http", "https"}:
            raise DownloadError("Model download requires an HTTP(S) proxy. Use its HTTP(S) proxy port or clear the proxy for a direct download; SOCKS URLs are not supported here.")
    proxy_settings = {} if args.verify_only else ({"https": proxy} if proxy else None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxy_settings))
    for name, (size, sha) in FILES.items():
        target = destination / name
        if matches(target, size, sha):
            print(f"Verified existing {name}", flush=True)
            continue
        if args.verify_only:
            raise RuntimeError(f"Missing or invalid pinned model file: {name}")
        print(f"Downloading {name} ({size / 1e9:.3f} GB)", flush=True)
        download_file(opener, f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}", target, size, sha,
                      retries=args.retries)
        print(f"Verified {name}", flush=True)


if __name__ == "__main__":
    main()
