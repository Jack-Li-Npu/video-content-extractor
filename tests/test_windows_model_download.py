"""Pinned model download regression cases; all network and payloads are synthetic."""

import hashlib
import importlib.util
import json
from email.message import Message
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request

import pytest


@pytest.fixture
def downloader(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "stt/download_windows_model.py"
    spec = importlib.util.spec_from_file_location(
        "windows_model_downloader_under_test", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "load_dotenv", lambda *_: None)
    if hasattr(module, "time"):
        monkeypatch.setattr(module.time, "sleep", lambda *_: None)
    return module


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


class Response:
    def __init__(self, payload, *, status=200, headers=None, interrupt_after=None):
        self.payload = payload
        self.status = status
        self.headers = Message()
        for name, value in {
            "Content-Length": str(len(payload)),
            **(headers or {}),
        }.items():
            self.headers[name] = value
        self.position = 0
        self.interrupt_after = interrupt_after

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def getcode(self):
        return self.status

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, count=-1):
        if self.interrupt_after is not None and self.position >= self.interrupt_after:
            raise URLError("synthetic interrupted connection")
        limit = len(self.payload) if count < 0 else self.position + count
        if self.interrupt_after is not None:
            limit = min(limit, self.interrupt_after)
        result = self.payload[self.position : limit]
        self.position += len(result)
        return result


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, **kwargs):
        assert kwargs.get("timeout", 1) > 0
        if isinstance(request, Request):
            url = request.full_url
            headers = {key.lower(): value for key, value in request.header_items()}
        else:
            url, headers = request, {}
        self.requests.append((url, headers))
        assert self.responses, "Downloader made an unexpected extra request"
        response = self.responses.pop(0)
        return response(request) if callable(response) else response


def partial_for(target):
    return target.with_suffix(target.suffix + ".partial")


def metadata_for(target):
    partial = partial_for(target)
    return partial.with_suffix(partial.suffix + ".json")


def interrupt_download(
    downloader, target, payload=b"abcdef", *, etag='"strong-fixture-etag"'
):
    response = Response(payload, headers={"ETag": etag}, interrupt_after=3)
    with pytest.raises((OSError, RuntimeError, URLError)):
        downloader.download_file(
            Opener(response),
            "https://example.invalid/model.bin",
            target,
            len(payload),
            sha(payload),
            retries=1,
        )
    assert partial_for(target).read_bytes() == payload[:3]
    assert not target.exists()


def test_matches_requires_size_and_digest_not_only_existing_file(downloader, tmp_path):
    path = tmp_path / "model.bin"
    assert not downloader.matches(path, 6, sha(b"abcdef"))
    path.write_bytes(b"abcdef")
    assert downloader.matches(path, 6, sha(b"abcdef"))
    assert not downloader.matches(path, 7, sha(b"abcdef"))
    assert not downloader.matches(path, 6, sha(b"ABCDEF"))


def test_interrupted_download_resumes_with_validator_and_promotes_only_verified_bytes(
    downloader, tmp_path
):
    target = tmp_path / "model.bin"
    interrupt_download(downloader, target)
    metadata = json.loads(metadata_for(target).read_text(encoding="utf-8"))
    assert metadata["repo"] == downloader.REPO
    assert metadata["revision"] == downloader.REVISION
    assert metadata["size"] == 6 and metadata["sha256"] == sha(b"abcdef")
    assert metadata["etag"] == '"strong-fixture-etag"'
    assert "http" not in json.dumps(metadata)
    opener = Opener(
        Response(
            b"def",
            status=206,
            headers={"Content-Range": "bytes 3-5/6", "ETag": metadata["etag"]},
        )
    )
    downloader.download_file(
        opener,
        "https://example.invalid/model.bin",
        target,
        6,
        sha(b"abcdef"),
        retries=1,
    )
    assert opener.requests[0][1]["range"] == "bytes=3-"
    assert opener.requests[0][1]["if-range"] == metadata["etag"]
    assert target.read_bytes() == b"abcdef"
    assert not partial_for(target).exists() and not metadata_for(target).exists()


def test_server_ignoring_range_restarts_instead_of_appending(downloader, tmp_path):
    target = tmp_path / "model.bin"
    interrupt_download(downloader, target)
    opener = Opener(
        Response(b"abcdef", status=200, headers={"ETag": '"changed-validator"'})
    )
    downloader.download_file(
        opener,
        "https://example.invalid/model.bin",
        target,
        6,
        sha(b"abcdef"),
        retries=1,
    )
    assert opener.requests[0][1]["range"] == "bytes=3-"
    assert target.read_bytes() == b"abcdef"


def test_unsolicited_partial_from_zero_completes_with_bounded_followup(
    downloader, tmp_path
):
    target = tmp_path / "model.bin"
    opener = Opener(
        Response(
            b"abc",
            status=206,
            headers={"Content-Range": "bytes 0-2/6", "ETag": '"chunked"'},
        ),
        Response(
            b"def",
            status=206,
            headers={"Content-Range": "bytes 3-5/6", "ETag": '"chunked"'},
        ),
    )
    downloader.download_file(
        opener,
        "https://example.invalid/model.bin",
        target,
        6,
        sha(b"abcdef"),
        retries=2,
    )
    assert len(opener.requests) == 2
    assert opener.requests[1][1]["range"] == "bytes=3-"
    assert target.read_bytes() == b"abcdef"


def test_weak_validator_never_becomes_if_range_but_final_digest_is_required(
    downloader, tmp_path
):
    target = tmp_path / "model.bin"
    interrupt_download(downloader, target, etag='W/"weak-fixture"')
    opener = Opener(
        Response(b"def", status=206, headers={"Content-Range": "bytes 3-5/6"})
    )
    downloader.download_file(
        opener,
        "https://example.invalid/model.bin",
        target,
        6,
        sha(b"abcdef"),
        retries=1,
    )
    assert opener.requests[0][1]["range"] == "bytes=3-"
    assert "if-range" not in opener.requests[0][1]
    assert target.read_bytes() == b"abcdef"


@pytest.mark.parametrize(
    "content_range", ["bytes 2-4/6", "bytes 3-5/12", "bytes 3-9/6", "not-a-range"]
)
def test_malformed_or_wrong_content_range_is_never_promoted(
    downloader, tmp_path, content_range
):
    target = tmp_path / "model.bin"
    interrupt_download(downloader, target)
    opener = Opener(
        Response(b"def", status=206, headers={"Content-Range": content_range})
    )
    with pytest.raises((OSError, RuntimeError, URLError, ValueError)):
        downloader.download_file(
            opener,
            "https://example.invalid/model.bin",
            target,
            6,
            sha(b"abcdef"),
            retries=1,
        )
    assert not target.exists()
    assert len(opener.requests) == 1


def test_body_above_remaining_size_is_rejected(downloader, tmp_path):
    target = tmp_path / "model.bin"
    interrupt_download(downloader, target)
    opener = Opener(
        Response(
            b"defEXTRA",
            status=206,
            headers={"Content-Range": "bytes 3-5/6", "Content-Length": "3"},
        )
    )
    with pytest.raises((OSError, RuntimeError, URLError, ValueError)):
        downloader.download_file(
            opener,
            "https://example.invalid/model.bin",
            target,
            6,
            sha(b"abcdef"),
            retries=1,
        )
    assert not target.exists()
    assert not partial_for(target).exists() or partial_for(target).stat().st_size <= 6


def test_hash_mismatch_reports_actual_digest_then_retries_fresh_cache_busted_request(
    downloader, tmp_path, capsys
):
    target = tmp_path / "model.bin"

    def correct_response(_):
        assert not target.exists()
        assert (
            not partial_for(target).exists() or partial_for(target).stat().st_size == 0
        )
        return Response(b"abcdef")

    opener = Opener(Response(b"ABCDEF"), correct_response)
    downloader.download_file(
        opener,
        "https://example.invalid/model.bin",
        target,
        6,
        sha(b"abcdef"),
        retries=2,
    )
    assert target.read_bytes() == b"abcdef"
    assert len(opener.requests) == 2
    assert opener.requests[0][0] != opener.requests[1][0]
    assert "range" not in opener.requests[1][1]
    diagnostics = capsys.readouterr()
    assert sha(b"ABCDEF") in diagnostics.out + diagnostics.err
    assert "6" in diagnostics.out + diagnostics.err


def test_changed_manifest_discards_old_partial_instead_of_resuming(
    downloader, tmp_path
):
    target = tmp_path / "model.bin"
    interrupt_download(downloader, target)
    opener = Opener(Response(b"uvwxyz"))
    downloader.download_file(
        opener,
        "https://example.invalid/model.bin",
        target,
        6,
        sha(b"uvwxyz"),
        retries=1,
    )
    assert "range" not in opener.requests[0][1]
    assert target.read_bytes() == b"uvwxyz"


@pytest.mark.parametrize(
    "metadata", ["[]", "null", '"unexpected string"', "{truncated"]
)
def test_malformed_resume_metadata_starts_a_verified_fresh_download(
    downloader, tmp_path, metadata
):
    target = tmp_path / "model.bin"
    partial_for(target).write_bytes(b"abc")
    metadata_for(target).write_text(metadata, encoding="utf-8")
    opener = Opener(Response(b"abcdef"))
    downloader.download_file(
        opener,
        "https://example.invalid/model.bin",
        target,
        6,
        sha(b"abcdef"),
        retries=1,
    )
    assert "range" not in opener.requests[0][1]
    assert target.read_bytes() == b"abcdef"


def test_main_respects_custom_destination(downloader, tmp_path, monkeypatch):
    destination = tmp_path / "chosen-assets"
    monkeypatch.setattr(downloader, "FILES", {"model.bin": (6, sha(b"abcdef"))})
    monkeypatch.setattr(
        "sys.argv", ["download_windows_model.py", "--destination", str(destination)]
    )
    opener = Opener(Response(b"abcdef"))
    monkeypatch.setattr(downloader.urllib.request, "build_opener", lambda *_: opener)
    downloader.main()
    assert (destination / "model.bin").read_bytes() == b"abcdef"


def test_verify_only_is_offline_and_keeps_invalid_files_for_diagnostics(
    downloader, tmp_path, monkeypatch
):
    target = tmp_path / "model.bin"
    target.write_bytes(b"abcdef")
    monkeypatch.setattr(downloader, "FILES", {"model.bin": (6, sha(b"abcdef"))})
    monkeypatch.setattr(
        "sys.argv",
        ["download_windows_model.py", "--destination", str(tmp_path), "--verify-only"],
    )
    opener = Opener()
    monkeypatch.setattr(downloader.urllib.request, "build_opener", lambda *_: opener)
    downloader.main()
    assert not opener.requests
    target.write_bytes(b"BAD")
    with pytest.raises((RuntimeError, SystemExit)):
        downloader.main()
    assert not opener.requests
    assert target.read_bytes() == b"BAD"


@pytest.mark.parametrize(
    "proxy", ["socks5://user:secret@example.invalid:1080", "http://user:secret@[bad"]
)
def test_model_download_rejects_unsupported_or_malformed_proxy_safely(
    downloader, tmp_path, monkeypatch, capsys, proxy
):
    monkeypatch.setenv("YT_PROXY", proxy)
    monkeypatch.setattr(downloader, "FILES", {"model.bin": (6, sha(b"abcdef"))})
    monkeypatch.setattr(
        "sys.argv", ["download_windows_model.py", "--destination", str(tmp_path)]
    )
    monkeypatch.setattr(
        downloader.urllib.request,
        "build_opener",
        lambda *_: pytest.fail("Invalid proxy must fail before network setup"),
    )
    with pytest.raises(downloader.DownloadError) as failure:
        downloader.main()
    assert "HTTP(S)" in str(failure.value)
    diagnostics = capsys.readouterr()
    combined = str(failure.value) + diagnostics.out + diagnostics.err
    assert "secret" not in combined and proxy not in combined
    assert not (tmp_path / "model.bin").exists()


@pytest.mark.parametrize(
    "proxy", ["socks5://user:secret@example.invalid:1080", "http://user:secret@[bad"]
)
def test_offline_verification_ignores_invalid_proxy_and_disables_environment_proxy(
    downloader, tmp_path, monkeypatch, proxy
):
    (tmp_path / "model.bin").write_bytes(b"abcdef")
    monkeypatch.setenv("YT_PROXY", proxy)
    monkeypatch.setattr(downloader, "FILES", {"model.bin": (6, sha(b"abcdef"))})
    monkeypatch.setattr(
        "sys.argv",
        ["download_windows_model.py", "--destination", str(tmp_path), "--verify-only"],
    )
    settings = []
    monkeypatch.setattr(
        downloader.urllib.request, "ProxyHandler", lambda value: settings.append(value)
    )
    opener = Opener()
    monkeypatch.setattr(downloader.urllib.request, "build_opener", lambda *_: opener)
    downloader.main()
    assert settings == [{}]
    assert not opener.requests
    assert (tmp_path / "model.bin").read_bytes() == b"abcdef"
