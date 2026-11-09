"""Review-only expansion preserves the catalogue and never repeats known details."""

import copy
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from musicmatcher.mapping_manager import load_state, save_state
from musicmatcher.service import Workspace
from musicmatcher.config_manager import Settings, load_settings, save_settings
from musicmatcher.youtube_search_agent import _fingerprint, search_tracks


def song(key="one", status="review"):
    return {"key": key, "track_id": key, "position": 1, "title": "Song", "artists": ["Artist"],
            "album": "Album", "duration_ms": 180000, "explicit": False,
            "authorization": {}, "download": {"status": "pending"}, "match": {"status": status}}


def video(index, **changes):
    return {"id": str(index) * 11, "title": "Artist - Song (Official Audio)", "track": "Song",
            "artists": ["Artist"], "channel": "Artist - Topic", "duration": 180, **changes}


def reviewed(track):
    track["match"].update(metadata_fingerprint=_fingerprint(track), candidate_limit=3,
                          candidates=[{"candidate": video(i, duration=None)} for i in range(1, 4)])
    return track


def fake_provider(monkeypatch, *, available=8):
    calls = []

    class YoutubeDL:
        def __init__(self, options): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download=False):
            assert not download
            calls.append(url)
            if url.startswith("ytsearch"):
                return {"entries": [{"id": str(i) * 11} for i in range(1, available + 1)]}
            return video(int(url.rsplit("=", 1)[-1][0]))

    monkeypatch.setattr("yt_dlp.YoutubeDL", YoutubeDL)
    return calls


def test_three_to_eight_only_fetches_five_new_videos_and_preserves_other_rows(tmp_path, monkeypatch):
    path = tmp_path / "estado.json"
    review = reviewed(song())
    accepted = song("accepted", "accepted")
    accepted["match"]["url"] = "https://www.youtube.com/watch?v=99999999999"
    accepted["authorization"] = {"audio": True, "url": "https://www.youtube.com/watch?v=99999999999"}
    accepted["download"] = {"status": "completed", "file": "existing.mp3"}
    manual = reviewed(song("manual"))
    manual["match"]["manual"] = True
    untouched = [accepted, manual, song("unmatched", "unmatched"), song("error", "search_error")]
    original = copy.deepcopy(untouched)
    save_state(path, {"schema_version": 1, "tracks": [review, *untouched], "search_summary": {"searched": 1007}})
    calls = fake_provider(monkeypatch)
    settings = SimpleNamespace(candidate_limit=8, matching_mode="relaxed")
    result = search_tracks(path, settings=settings, review_only=True)
    match = result["tracks"][0]["match"]
    assert match["status"] == "accepted"
    assert match["candidate_count"] == 8
    assert match["review_expansion"]["previous_count"] == 3
    assert match["review_expansion"]["added_count"] == 5
    assert calls[0] == "ytsearch8:Song Artist"
    assert len(calls) == 6
    assert all(url.rsplit("=", 1)[-1] not in {"11111111111", "22222222222", "33333333333"}
               for url in calls[1:])
    assert result["tracks"][1:] == original
    assert result["search_summary"] == {"searched": 1007}
    assert result["review_search_summary"]["searched"] == 1
    # Returning to the regular three-candidate workflow must reuse the more
    # thorough decision and retain existing files/permissions.
    accepted_only_path = tmp_path / "resume.json"
    save_state(accepted_only_path, {"schema_version": 1, "tracks": [result["tracks"][0]]})
    resumed = search_tracks(accepted_only_path, settings=SimpleNamespace(candidate_limit=3, matching_mode="relaxed"))
    assert resumed["search_summary"]["skipped"] == 1
    search_tracks(path, settings=settings, review_only=True)
    assert len(calls) == 6


def test_short_provider_response_keeps_existing_three_without_faking_eight(tmp_path, monkeypatch):
    path = tmp_path / "estado.json"
    save_state(path, {"schema_version": 1, "tracks": [reviewed(song())]})
    calls = fake_provider(monkeypatch, available=2)
    result = search_tracks(path, settings=SimpleNamespace(candidate_limit=8), review_only=True)
    assert result["tracks"][0]["match"]["candidate_count"] == 3
    assert result["tracks"][0]["match"]["review_expansion"]["added_count"] == 0
    assert len(calls) == 1
    search_tracks(path, settings=SimpleNamespace(candidate_limit=8), review_only=True)
    assert len(calls) == 1


def test_bounded_review_pass_keeps_later_review_rows_unchanged(tmp_path, monkeypatch):
    path = tmp_path / "estado.json"
    first, second = reviewed(song()), reviewed(song("two"))
    untouched = copy.deepcopy(second)
    save_state(path, {"schema_version": 1, "tracks": [first, second]})
    fake_provider(monkeypatch)
    result = search_tracks(path, settings=SimpleNamespace(candidate_limit=8), review_only=True, track_limit=1)
    assert result["tracks"][1] == untouched


def test_review_pass_uses_duplicate_metadata_once(tmp_path, monkeypatch):
    path = tmp_path / "estado.json"
    save_state(path, {"schema_version": 1, "tracks": [reviewed(song()), reviewed(song("duplicate"))]})
    calls = fake_provider(monkeypatch)
    result = search_tracks(path, settings=SimpleNamespace(candidate_limit=8), review_only=True)
    assert len(calls) == 6
    assert result["review_search_summary"]["reused"] == 1


def test_automatic_second_pass_starts_after_main_search_and_keeps_primary_limit(tmp_path):
    workspace = Workspace(tmp_path / "data", Settings(candidate_limit=3, output_dir=str(tmp_path / "audio")))
    save_state(workspace.state_path, {"schema_version": 1, "tracks": [song()]})
    steps = []

    def search(path, *, settings, **kwargs):
        steps.append((settings.candidate_limit, kwargs.get("review_only", False)))
        return load_state(path)

    try:
        with patch("musicmatcher.youtube_search_agent.search_tracks", side_effect=search):
            workspace.start({"action": "search"})
            workspace.thread.join(timeout=3)
        assert steps == [(3, False), (8, True)]
        assert workspace.settings.candidate_limit == 3
        assert workspace.job["status"] == "completed"
        assert workspace.job["candidate_limit"] == 8
    finally:
        workspace.close()


def test_pause_during_main_search_does_not_launch_review_pass(tmp_path):
    workspace = Workspace(tmp_path / "data", Settings(candidate_limit=3, output_dir=str(tmp_path / "audio")))
    save_state(workspace.state_path, {"schema_version": 1, "tracks": [song()], "search_summary": {"paused": True}})
    mocked = Mock(return_value=workspace.state())
    try:
        with patch("musicmatcher.youtube_search_agent.search_tracks", mocked):
            workspace.start({"action": "search"})
            workspace.thread.join(timeout=3)
        assert mocked.call_count == 1
        assert workspace.job["status"] == "paused"
    finally:
        workspace.close()


def test_stop_signal_preserves_all_review_rows(tmp_path, monkeypatch):
    path = tmp_path / "estado.json"
    track = reviewed(song())
    save_state(path, {"schema_version": 1, "tracks": [track]})
    stop = Event()
    stop.set()
    network = Mock()
    monkeypatch.setattr("musicmatcher.youtube_search_agent._search_candidates", network)
    result = search_tracks(path, settings=SimpleNamespace(candidate_limit=8), review_only=True, stop_event=stop)
    assert result["review_search_summary"]["paused"]
    assert result["tracks"][0] == track
    network.assert_not_called()


def test_failed_expansion_keeps_saved_review_candidates_and_can_resume(tmp_path, monkeypatch):
    path = tmp_path / "estado.json"
    track = reviewed(song())
    saved = copy.deepcopy(track["match"]["candidates"])
    save_state(path, {"schema_version": 1, "tracks": [track]})
    settings = SimpleNamespace(candidate_limit=8, matching_mode="relaxed")
    with monkeypatch.context() as blocked:
        blocked.setattr("musicmatcher.youtube_search_agent._search_candidates", Mock(side_effect=RuntimeError("HTTP 429")))
        first = search_tracks(path, settings=settings, review_only=True)
    assert first["review_search_summary"]["paused"]
    assert first["tracks"][0]["match"]["status"] == "review"
    assert first["tracks"][0]["match"]["candidates"] == saved
    calls = fake_provider(monkeypatch)
    second = search_tracks(path, settings=settings, review_only=True)
    assert second["tracks"][0]["match"]["candidate_count"] == 8
    assert len(calls) == 6


def test_cli_review_only_maximum_does_not_replace_primary_settings(tmp_path, monkeypatch):
    from musicmatcher.cli import main

    save_settings(tmp_path, Settings(candidate_limit=3, output_dir=str(tmp_path / "audio")))
    path = tmp_path / "estado.json"
    save_state(path, {"schema_version": 1, "tracks": [reviewed(song())]})
    mocked = Mock(return_value=load_state(path))
    monkeypatch.setattr("musicmatcher.youtube_search_agent.search_tracks", mocked)
    assert main(["--data-dir", str(tmp_path), "search", "--review-only", "--candidates", "8"]) == 0
    assert mocked.call_args.kwargs["settings"].candidate_limit == 8
    assert load_settings(tmp_path).candidate_limit == 3
