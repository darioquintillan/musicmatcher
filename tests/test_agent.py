"""The search orchestrator uses yt-dlp and local deterministic rules only."""

import copy
import json
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from musicmatcher.mapping_manager import load_state, save_state
from musicmatcher.youtube_search_agent import SearchCancelled, _search_candidates, search_tracks


@pytest.fixture
def track():
    return {"key": "Canción - Artista", "track_id": "position-1", "position": 1,
            "title": "Canción", "artists": ["Artista"], "album": "Álbum", "duration_ms": 180000,
            "explicit": False, "source": "user_authorized", "match": {"status": "pending"},
            "authorization": {}, "download": {"status": "pending"}}


@pytest.fixture
def candidates():
    return [{"id": "abcdefghijk", "title": "Artista - Canción (Official Audio)", "track": "Canción",
             "artists": ["Artista"], "album": "Álbum", "duration": 180, "channel": "Artista - Topic"}]


def settings(limit=8):
    return SimpleNamespace(candidate_limit=limit)


def write_state(path, tracks, **extra):
    save_state(path, {"schema_version": 1, "tracks": tracks, **extra})


def test_default_eight_candidates_and_deterministic_result(tmp_path, monkeypatch, track, candidates):
    path = tmp_path / "estado.json"
    write_state(path, [track])
    search = Mock(return_value=candidates)
    monkeypatch.setattr("musicmatcher.youtube_search_agent._search_candidates", search)
    result = search_tracks(path, settings=SimpleNamespace())
    assert search.call_args.args[1] == 8
    assert result["tracks"][0]["match"]["status"] == "accepted"
    assert result["tracks"][0]["match"]["decision"] == "automatic"


@pytest.mark.parametrize("limit", [1, 5, 8, 25])
def test_configured_candidate_quantity(tmp_path, monkeypatch, track, candidates, limit):
    path = tmp_path / "estado.json"
    write_state(path, [track])
    search = Mock(return_value=candidates)
    monkeypatch.setattr("musicmatcher.youtube_search_agent._search_candidates", search)
    search_tracks(path, settings=settings(limit))
    assert search.call_args.args[1] == limit


@pytest.mark.parametrize("limit", [0, 26, -1])
def test_invalid_candidate_quantity_rejected_before_network(tmp_path, monkeypatch, track, limit):
    path = tmp_path / "estado.json"
    write_state(path, [track])
    search = Mock()
    monkeypatch.setattr("musicmatcher.youtube_search_agent._search_candidates", search)
    with pytest.raises(ValueError, match="entre 1 y 25"):
        search_tracks(path, settings=settings(limit))
    search.assert_not_called()


def test_duplicate_resume_and_incremental_exports(tmp_path, monkeypatch, track, candidates):
    second = copy.deepcopy(track)
    second.update(key="Canción - Artista [2]", track_id="position-2", position=2)
    path = tmp_path / "estado.json"
    write_state(path, [track, second])
    search = Mock(return_value=candidates)
    monkeypatch.setattr("musicmatcher.youtube_search_agent._search_candidates", search)
    events = []
    first = search_tracks(path, settings=settings(), progress=events.append)
    search_tracks(path, settings=settings())
    assert search.call_count == 1
    assert first["tracks"][1]["match"]["reused"] is True
    exported = json.loads((tmp_path / "canciones.json").read_text(encoding="utf-8"))
    assert len(exported) == 2
    assert events[-1]["done"] == 2 and events[-1]["status"] == "finished"
    assert events[0]["started_at"] and events[-1]["finished_at"]


@pytest.mark.parametrize("initial_status", ["accepted", "review", "unmatched"])
def test_increasing_limit_repeats_discovery_for_automatic_decisions(tmp_path, monkeypatch, track, candidates, initial_status):
    path = tmp_path / "estado.json"
    write_state(path, [track])
    initial = candidates
    if initial_status == "review":
        initial = [{**candidates[0], "duration": None}]
    elif initial_status == "unmatched":
        initial = []
    search = Mock(side_effect=[initial, candidates])
    monkeypatch.setattr("musicmatcher.youtube_search_agent._search_candidates", search)
    first = search_tracks(path, settings=settings(5))
    assert first["tracks"][0]["match"]["status"] == initial_status
    second = search_tracks(path, settings=settings(8))
    assert [call.args[1] for call in search.call_args_list] == [5, 8]
    assert second["tracks"][0]["match"]["status"] == "accepted"


def test_unavailable_and_manual_decisions_survive_refresh(tmp_path, monkeypatch, track):
    second = copy.deepcopy(track)
    second.update(key="second", track_id="position-2")
    track["match"] = {"status": "unavailable"}
    second["match"] = {"status": "review", "manual": True}
    path = tmp_path / "estado.json"
    write_state(path, [track, second])
    search = Mock()
    monkeypatch.setattr("musicmatcher.youtube_search_agent._search_candidates", search)
    result = search_tracks(path, settings=settings(), refresh=True)
    search.assert_not_called()
    assert result["tracks"][0]["match"]["status"] == "unavailable"
    assert result["tracks"][1]["match"]["manual"] is True


def test_cooperative_stop_preserves_remaining_pending(tmp_path, monkeypatch, track, candidates):
    second = copy.deepcopy(track)
    second.update(key="second", track_id="position-2", title="Another")
    path = tmp_path / "estado.json"
    write_state(path, [track, second])
    stopped = Event()
    monkeypatch.setattr("musicmatcher.youtube_search_agent._search_candidates", Mock(return_value=candidates))
    def progress(event):
        if event["done"] == 1:
            stopped.set()
    result = search_tracks(path, settings=settings(), progress=progress, stop_event=stopped)
    assert result["search_summary"]["paused"] is True
    assert load_state(path)["tracks"][0]["match"]["status"] == "accepted"
    assert load_state(path)["tracks"][1]["match"]["status"] == "pending"


def test_keyboard_interrupt_persists_and_propagates(tmp_path, monkeypatch, track):
    path = tmp_path / "estado.json"
    write_state(path, [track])
    monkeypatch.setattr("musicmatcher.youtube_search_agent._search_candidates", Mock(side_effect=KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        search_tracks(path, settings=settings())
    assert load_state(path)["search_summary"]["paused"] is True


def test_manual_decision_reused_for_identical_occurrence_without_search(tmp_path, monkeypatch, track):
    second = copy.deepcopy(track)
    second.update(key="second", track_id="position-2", position=2)
    track["match"] = {"status": "accepted", "url": "https://www.youtube.com/watch?v=abcdefghijk", "manual": True}
    path = tmp_path / "estado.json"
    write_state(path, [track, second])
    search = Mock()
    monkeypatch.setattr("musicmatcher.youtube_search_agent._search_candidates", search)
    result = search_tracks(path, settings=settings(), refresh=True)
    search.assert_not_called()
    assert result["tracks"][1]["match"]["manual"] is True


def test_candidate_discovery_emits_events_and_stops_between_candidates(monkeypatch):
    stopped = Event()
    events, calls = [], []
    class FakeYoutubeDL:
        def __init__(self, options): self.options = options
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def extract_info(self, url, download=False):
            calls.append((url, download))
            if url.startswith("ytsearch"):
                return {"entries": [{"id": "abcdefghijk"}, {"id": "12345678901"}]}
            return {"id": "abcdefghijk", "title": "Song", "duration": 180}
    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYoutubeDL)
    def progress(event):
        events.append(event)
        if event.get("candidate_done") == 1:
            stopped.set()
    with pytest.raises(SearchCancelled):
        _search_candidates("Song Artist", 8, progress=progress, stop_event=stopped)
    assert len(calls) == 2
    assert all(download is False for _, download in calls)
    assert any(event["status"] == "candidate_lookup" for event in events)
    assert any(event["status"] == "candidate_ready" for event in events)


def test_network_errors_and_rate_limit_are_not_no_match(tmp_path, monkeypatch, track):
    second = copy.deepcopy(track)
    second.update(key="second", track_id="position-2", title="Another")
    path = tmp_path / "estado.json"
    write_state(path, [track, second])
    search = Mock(side_effect=RuntimeError("HTTP Error 429"))
    monkeypatch.setattr("musicmatcher.youtube_search_agent._search_candidates", search)
    result = search_tracks(path, settings=settings())
    assert search.call_count == 1
    assert result["tracks"][0]["match"]["status"] == "search_error"
    assert result["tracks"][1]["match"]["status"] == "pending"
    assert result["search_summary"]["paused"] is True


def test_spotify_source_uses_same_deterministic_path(tmp_path, monkeypatch, track, candidates):
    track.update(source="spotify", spotify_id="original-id")
    path = tmp_path / "estado.json"
    write_state(path, [track], playlist={"source": "spotify"})
    monkeypatch.setattr("musicmatcher.youtube_search_agent._search_candidates", Mock(return_value=candidates))
    result = search_tracks(path, settings=settings())
    assert result["tracks"][0]["match"]["status"] == "accepted"
    assert "ai" not in result["tracks"][0]["match"]


@pytest.mark.parametrize("message", [
    "Sign in to confirm your age. This video may be inappropriate for some users.",
    "Private video. Sign in if you have been granted access to this video.",
    "Video unavailable. This video has been removed by the uploader.",
])
def test_individual_unavailable_candidate_does_not_abort_other_results(tmp_path, monkeypatch, track, candidates, message):
    path = tmp_path / "estado.json"
    write_state(path, [track])
    events = []
    class FakeYoutubeDL:
        def __init__(self, options): self.options = options
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def extract_info(self, url, download=False):
            if url.startswith("ytsearch"):
                return {"entries": [{"id": "12345678901", "title": "Canción"}, {"id": "abcdefghijk", "title": "Canción"}]}
            if "12345678901" in url:
                raise RuntimeError(message)
            return candidates[0]
    monkeypatch.setattr("yt_dlp.YoutubeDL", FakeYoutubeDL)
    result = search_tracks(path, settings=settings(), progress=events.append)
    match = result["tracks"][0]["match"]
    assert match["status"] == "accepted"
    assert match["url"] == "https://www.youtube.com/watch?v=abcdefghijk"
    assert match["candidate_errors"][0]["id"] == "12345678901"
    assert result["search_summary"]["paused"] is False
    assert any(event["status"] == "candidate_error" for event in events)


def test_bot_confirmation_still_pauses_globally(tmp_path, monkeypatch, track):
    path = tmp_path / "estado.json"
    write_state(path, [track])
    monkeypatch.setattr("musicmatcher.youtube_search_agent._search_candidates", Mock(side_effect=RuntimeError("Sign in to confirm you're not a bot")))
    result = search_tracks(path, settings=settings())
    assert result["search_summary"]["paused"] is True
