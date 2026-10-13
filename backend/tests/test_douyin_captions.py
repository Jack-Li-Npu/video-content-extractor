import json

import pytest

from app.douyin_captions import caption_records, discover_caption_tracks
from app.transcript_service import ExtractionError


def safe_url(url):
    return url.startswith("https://captions.douyinvod.com/")


CAPTION_URL = "https://captions.douyinvod.com/track"
UTTERANCES = [
    {"start_time": 3601125, "end_time": 3605500, "text": "Hello 世界"},
    {"start_time": 3602000.5, "end_time": 3604000.75, "text": "An overlapping caption."},
    {"start_time": 125, "end_time": 1875, "text": "  Repeated words, repeated words.\n次の行  "},
]


@pytest.mark.parametrize(
    "data",
    [
        {"subtitle_infos": [{"url": CAPTION_URL, "lang": "zh"}]},
        {"video": {"subtitleInfos": [{"Url": CAPTION_URL, "LanguageCodeName": "zh"}]}},
        {"video": {"subtitle": {"subtitleInfos": [{"url": CAPTION_URL, "languageCodeName": "zh"}]}}},
        {"video": {"cla_info": {"caption_infos": [{"url": CAPTION_URL, "lang": "zh", "Format": "srt"}]}}},
        {
            "interaction_stickers": [
                {"auto_video_caption_info": {"auto_captions": [{"url": {"url_list": [CAPTION_URL]}, "language": "zh"}]}}
            ]
        },
    ],
)
def test_discovers_explicit_caption_fields_from_each_public_response_shape(data):
    assert discover_caption_tracks(data, safe_url) == [
        {"id": "platform:0", "language": "zh", "name": "zh", "source": "platform", "_url": CAPTION_URL}
    ]


@pytest.mark.parametrize(
    "address",
    [
        {"Url": {"UrlList": [None, "http://127.0.0.1/private", CAPTION_URL]}},
        {"url": {"url_list": ["https://wrong.test/track", CAPTION_URL]}},
        {"UrlList": [None, CAPTION_URL]},
        {"url_list": [False, "file:///etc/passwd", CAPTION_URL]},
        {"Url": "https://wrong.test/track", "url": CAPTION_URL},
    ],
)
def test_first_safe_alternate_url_is_used(address):
    tracks = discover_caption_tracks({"subtitle_infos": [address]}, safe_url)
    assert tracks[0]["_url"] == CAPTION_URL
    assert tracks[0]["language"] == "und"


def test_deduplicates_shared_urls_and_ignores_malformed_containers():
    data = {
        "subtitle_infos": [None, 0, [], {"url": {"url_list": "not a list"}}, {"url": [CAPTION_URL]}],
        "video": {"cla_info": ["not a dictionary"], "subtitle": "wrong shape"},
        "interaction_stickers": [
            None,
            {"auto_video_caption_info": 1},
            {"auto_video_caption_info": {"auto_captions": [{"url": CAPTION_URL}]}},
        ],
    }
    tracks = discover_caption_tracks(data, safe_url)
    assert len(tracks) == 1
    assert tracks[0]["id"] == "platform:0"
    assert discover_caption_tracks({"video": None, "subtitle_infos": {}}, safe_url) == []
    assert discover_caption_tracks(None, safe_url) == []


def test_inline_captions_require_valid_timed_utterances_on_explicit_caption_entry():
    entry = {
        "language": "zh-CN",
        "LanguageName": "Chinese",
        "utterances": [{**UTTERANCES[0], "private_extra": "discard"}],
    }
    data = {
        "desc": "A description is not a transcript",
        "utterances": UTTERANCES,
        "video": {"cla_info": {"caption_infos": [entry, entry.copy()]}},
    }
    tracks = discover_caption_tracks(data, safe_url)
    assert len(tracks) == 1
    assert "_url" not in tracks[0]
    assert tracks[0]["name"] == "Chinese"
    assert tracks[0]["language"] == "zh-CN"
    assert tracks[0]["_inline"] == {"utterances": [UTTERANCES[0]]}
    assert caption_records(tracks[0]["_inline"])[0]["text"] == "Hello 世界"
    assert discover_caption_tracks({"desc": "text", "utterances": UTTERANCES}, safe_url) == []


def test_invalid_inline_captions_do_not_hide_valid_url_or_create_empty_choice():
    data = {
        "subtitle_infos": [
            {"utterances": [{"text": "untimed text"}]},
            {"url": CAPTION_URL, "utterances": [], "lang": {"unexpected": "object"}, "name": "\ud800"},
        ]
    }
    assert discover_caption_tracks(data, safe_url) == [
        {"id": "platform:0", "language": "und", "name": "und", "source": "platform", "_url": CAPTION_URL}
    ]


@pytest.mark.parametrize("as_json", [False, True])
def test_utterances_preserve_complete_text_order_overlap_and_fractional_timing(as_json):
    data = {"utterances": UTTERANCES}
    records = caption_records(json.dumps(data) if as_json else data)
    assert records == [
        {"start": 3601.125, "duration": 4.375, "text": "Hello 世界"},
        {"start": 3602.0005, "duration": 2.00025, "text": "An overlapping caption."},
        {"start": 0.125, "duration": 1.75, "text": "  Repeated words, repeated words.\n次の行  "},
    ]


def test_numeric_milliseconds_strings_preserve_actual_duration():
    assert caption_records({"utterances": [{"start_time": "125.5", "end_time": "250.75", "text": "Text"}]}) == [
        {"start": 0.1255, "duration": 0.12525, "text": "Text"}
    ]


def test_srt_preserves_overlaps_long_timestamps_and_multiline_caption_content():
    assert caption_records(
        "\ufeff1\r\n01:00:01,125 --> 01:00:05,500\r\nHello 世界\r\nLine two.\r\n\r\n"
        "2\r\n01:00:02,000 --> 01:00:03,250\r\n  <b>Keep</b> &amp; repeat.  \r\n"
    ) == [
        {"start": 3601.125, "duration": 4.375, "text": "Hello 世界\nLine two."},
        {"start": 3602.0, "duration": 1.25, "text": "  Keep & repeat.  "},
    ]


def test_webvtt_header_layout_notes_and_cue_identifiers_do_not_become_captions():
    value = (
        "WEBVTT Example\nKind: captions\nLanguage: zh\n\n"
        "STYLE\n::cue { color: white; }\n\n"
        "REGION\nid:main\n\n"
        "NOTE platform metadata\nThis is not spoken content.\n\n"
        "first-cue\n01:01.125 --> 01:05.500 align:start position:10%\n"
        "<v Narrator><c.green>你好</c> &amp; <00:01:02.000>世界\n\n"
        "01:01:00.125 --> 01:01:01.375\n5 < 10 > 3\n"
    )
    assert caption_records(value) == [
        {"start": 61.125, "duration": 4.375, "text": "你好 & 世界"},
        {"start": 3660.125, "duration": 1.25, "text": "5 < 10 > 3"},
    ]


@pytest.mark.parametrize(
    "value",
    [
        None,
        b"WEBVTT",
        "",
        "A title or description is not timed text.",
        "<html>Verification page</html>",
        "[]",
        '{"utterances":',
        {},
        {"utterances": []},
        {"utterances": "text"},
        {"utterances": [None]},
        {"utterances": [{"start": 0, "duration": 2, "text": "Wrong schema"}]},
        {"utterances": [{"start_time": 0, "end_time": 2000, "text": 123}]},
        {"utterances": [{"start_time": False, "end_time": 2000, "text": "Text"}]},
        {"utterances": [{"start_time": -1, "end_time": 2000, "text": "Text"}]},
        {"utterances": [{"start_time": 1, "end_time": 1, "text": "Text"}]},
        {"utterances": [{"start_time": 2000, "end_time": 1, "text": "Text"}]},
        {"utterances": [{"start_time": float("nan"), "end_time": 2000, "text": "Text"}]},
        {"utterances": [{"start_time": 0, "end_time": float("inf"), "text": "Text"}]},
        {"utterances": [{"start_time": "1e3", "end_time": 2000, "text": "Text"}]},
        {"utterances": [{"start_time": 0, "end_time": 2000, "text": "\ud800"}]},
        {"utterances": [{"start_time": 0, "end_time": 2000, "text": "Lost \ufffd text"}]},
        {"utterances": [{"start_time": 0, "end_time": 2000, "text": "\x00"}]},
        "WEBVTT\n\nNOTE No captions",
        "1\n00:00:00,000 --> 00:00:00,000\nZero duration",
        "1\n00:60:00,000 --> 01:01:00,000\nBad minute",
        "1\n00:00:60,000 --> 00:01:01,000\nBad second",
        "1\n00:00:01 --> 00:00:02\nMissing milliseconds",
        "1\n00:00:01,000 --> 00:00:02,000 stray prose\nText",
        "1\n00:00:01,000 --> 00:00:02,000\n   ",
        "1\n00:00:01,000 --> 00:00:02,000\n\ud800",
        "WEBVTT\n00:00:01.000 --> 00:00:02.000\nMissing header separator",
    ],
)
def test_malformed_payloads_fail_with_public_caption_error(value):
    with pytest.raises(ExtractionError) as error:
        caption_records(value)
    assert error.value.code == "captions_invalid"
    assert error.value.status == 422


@pytest.mark.parametrize(
    "value",
    [
        {"utterances": [UTTERANCES[0], {"start_time": 0, "end_time": 1000}]},
        "1\n00:00:00,125 --> 00:00:01,875\nValid\n\n2\nInvalid timestamp\nMust not disappear",
    ],
)
def test_partial_track_is_rejected_instead_of_silently_discarding_caption_text(value):
    with pytest.raises(ExtractionError) as error:
        caption_records(value)
    assert error.value.code == "captions_invalid"
