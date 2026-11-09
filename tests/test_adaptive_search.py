"""Verify adaptive requests with a fake yt-dlp provider, without using live services."""

import copy
from threading import Event
from types import SimpleNamespace

import pytest

from musicmatcher.mapping_manager import save_state
from musicmatcher.youtube_search_agent import SearchCancelled, _search_candidates, search_tracks


@pytest.fixture
def song():
    return {"key": "Song - Artist", "position": 1, "track_id": "one", "title": "Song",
            "artists": ["Artist"], "album": "Album", "duration_ms": 180000, "explicit": False,
            "match": {"status": "pending"}, "authorization": {}, "download": {"status": "pending"}}


@pytest.fixture
def video():
    return {"id": "AAAAAAAAAAA", "title": "Artist - Song (Official Audio)", "track": "Song",
            "artists": ["Artist"], "album": "Album", "duration": 180, "explicit": False,
            "channel": "Artist - Topic"}


def provider(monkeypatch, videos, *, first_empty=False, expansion_entries=None):
    calls = []

    class FakeYoutubeDL:
        def __init__(self, options): self.options = options
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def extract_info(self, url, download=False):
            assert download is False
            calls.append(url)
            if url.startswith("ytsearch"):
                size = int(url.split(":", 1)[0].removeprefix("ytsearch"))
                entries = [{"id": value["id"], "title": value.get("title", "Song")} for value in videos]
                if size == 1 and first_empty:
                    entries = []
                elif size > 1 and expansion_entries is not None:
                    entries = expansion_entries
                return {"entries": entries[:size]}
            value = next(item for item in videos if item["id"] == url.rsplit("=", 1)[-1])
            if "error" in value:
                raise RuntimeError(value["error"])
            return copy.deepcopy(value)

    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYoutubeDL)
    return calls


@pytest.mark.parametrize("mode", ["relaxed", "strict"])
def test_clear_official_match_only_requests_one_video(monkeypatch, song, video, mode):
    calls = provider(monkeypatch, [video, {**video, "id": "BBBBBBBBBBB"}])
    events = []
    result = _search_candidates("Song Artist", 3, track=song, matching_mode=mode, progress=events.append)
    assert len(result) == 1
    assert calls == ["ytsearch1:Song Artist", "https://www.youtube.com/watch?v=AAAAAAAAAAA"]
    assert events[-1]["status"] == "candidate_early_accept"


@pytest.mark.parametrize("changes", [
    {"track": "Other Song", "title": "Artist - Other Song"},
    {"artists": ["Someone Else"], "channel": "Someone Else"},
    {"duration": 190},  # Accepted by relaxed rules, but warrants alternatives.
    {"duration": None},
    {"track": "Song (Live)", "title": "Artist - Song (Live)"},
    {"explicit": True},
    {"title": "Artist - Song", "channel": "Unofficial lyrics channel"},
    {"error": "Sign in to confirm your age"},
])
def test_doubt_expands_without_repeating_the_first_details(monkeypatch, song, video, changes):
    first = {**video, **changes}
    calls = provider(monkeypatch, [first, {**video, "id": "BBBBBBBBBBB"}, {**video, "id": "CCCCCCCCCCC"}])
    events = []
    candidates = _search_candidates("Song Artist", 3, track=song, progress=events.append)
    assert len(candidates) == 3
    searches = [url for url in calls if url.startswith("ytsearch")]
    details = [url for url in calls if not url.startswith("ytsearch")]
    assert searches == ["ytsearch1:Song Artist", "ytsearch3:Song Artist"]
    assert len(details) == len(set(details)) == 3
    assert any(event["status"] == "candidate_expand" for event in events)


def test_missing_collaborator_forces_comparison(monkeypatch, song, video):
    song["artists"].append("Guest")
    calls = provider(monkeypatch, [video, {**video, "id": "BBBBBBBBBBB", "artists": ["Artist", "Guest"]}])
    assert len(_search_candidates("Song Artist Guest", 3, track=song)) == 2
    assert calls[2].startswith("ytsearch3:")


def test_relaxed_missing_structured_metadata_can_still_finish_early(monkeypatch, song, video):
    video.pop("track")
    video.pop("artists")
    calls = provider(monkeypatch, [video])
    assert len(_search_candidates("Song Artist", 3, track=song)) == 1
    assert len(calls) == 2


def test_fallback_block_remains_a_paused_search_error(tmp_path, monkeypatch, song, video):
    path = tmp_path / "estado.json"
    save_state(path, {"schema_version": 1, "tracks": [song]})
    video["duration"] = None
    provider(monkeypatch, [video, {"id": "BBBBBBBBBBB", "error": "HTTP Error 429"}])
    result = search_tracks(path, settings=SimpleNamespace(candidate_limit=3, matching_mode="relaxed"))
    assert result["search_summary"]["paused"]
    assert result["tracks"][0]["match"]["status"] == "search_error"


def test_limit_one_never_expands_even_when_uncertain(monkeypatch, song, video):
    video["duration"] = None
    calls = provider(monkeypatch, [video])
    assert len(_search_candidates("Song Artist", 1, track=song)) == 1
    assert len(calls) == 2


def test_empty_initial_search_can_recover_at_maximum(monkeypatch, song, video):
    calls = provider(monkeypatch, [video], first_empty=True)
    assert len(_search_candidates("Song Artist", 3, track=song)) == 1
    assert calls[:2] == ["ytsearch1:Song Artist", "ytsearch3:Song Artist"]


def test_duplicate_search_entries_do_not_repeat_details(monkeypatch, song, video):
    video["duration"] = None
    calls = provider(monkeypatch, [video, {**video, "id": "BBBBBBBBBBB"}], expansion_entries=[
        {"id": "AAAAAAAAAAA"}, {"id": "AAAAAAAAAAA"}, {"id": "BBBBBBBBBBB"}])
    assert len(_search_candidates("Song Artist", 3, track=song)) == 2
    assert len(calls) == 4


def test_cancel_after_first_does_not_expand(monkeypatch, song, video):
    video["duration"] = None
    calls = provider(monkeypatch, [video])
    stop = Event()

    def progress(event):
        if event["status"] == "candidate_ready":
            stop.set()

    with pytest.raises(SearchCancelled):
        _search_candidates("Song Artist", 3, track=song, progress=progress, stop_event=stop)
    assert len(calls) == 2


def test_resume_preserves_actual_count_and_skips_network(tmp_path, monkeypatch, song, video):
    path = tmp_path / "estado.json"
    save_state(path, {"schema_version": 1, "tracks": [song]})
    calls = provider(monkeypatch, [video])
    settings = SimpleNamespace(candidate_limit=3, matching_mode="relaxed")
    first = search_tracks(path, settings=settings)
    second = search_tracks(path, settings=settings)
    match = first["tracks"][0]["match"]
    assert match["status"] == "accepted"
    assert match["candidate_limit"] == 3 and match["candidate_count"] == 1
    assert match["search_strategy"] == "adaptive-v1"
    assert second["search_summary"]["skipped"] == 1
    assert len(calls) == 2


def test_stricter_mode_expands_early_cached_video_without_refetching_it(tmp_path, monkeypatch, song, video):
    song["explicit"] = True
    video.pop("explicit")
    path = tmp_path / "estado.json"
    save_state(path, {"schema_version": 1, "tracks": [song]})
    calls = provider(monkeypatch, [video, {**video, "id": "BBBBBBBBBBB", "explicit": True}])
    first = search_tracks(path, settings=SimpleNamespace(candidate_limit=3, matching_mode="relaxed"))
    assert first["tracks"][0]["match"]["candidate_count"] == 1
    second = search_tracks(path, settings=SimpleNamespace(candidate_limit=3, matching_mode="strict"))
    assert second["tracks"][0]["match"]["candidate_count"] == 2
    assert calls.count("https://www.youtube.com/watch?v=AAAAAAAAAAA") == 1
    # Equal-score contenders remain ambiguous under the strict profile, even
    # when the second upload has a known explicit flag.
    assert second["tracks"][0]["match"]["status"] == "review"
    assert any(item["eligible"] and item["candidate"]["id"] == "BBBBBBBBBBB"
               for item in second["tracks"][0]["match"]["candidates"])
