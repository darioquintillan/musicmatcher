import copy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from musicmatcher.mapping_manager import load_state, save_state
from musicmatcher.match_validator import choose_match, evaluate_candidate
from musicmatcher.youtube_search_agent import search_tracks


@pytest.fixture
def song():
    return {"title": "Una canción", "artists": ["Autor", "Invitado"], "album": "Mi álbum",
            "duration_ms": 180000, "explicit": True}


@pytest.fixture
def video():
    return {"id": "AAAAAAAAAAA", "title": "Autor x Invitado - Una canción (Audio Oficial) | Mi álbum",
            "channel": "Autor", "channel_is_verified": True, "duration": 180}


def test_missing_structured_fields_and_explicit_flag_can_be_accepted(song, video):
    before = copy.deepcopy(song)
    assert choose_match(song, [video], mode="strict")["status"] != "accepted"
    result = choose_match(song, [video], mode="relaxed")
    assert result["status"] == "accepted"
    assert result["matching_mode"] == "relaxed"
    assert "explicit_flag_not_independently_verified" in result["candidates"][0]["warnings"]
    assert song == before


def test_production_credit_does_not_invalidate_title(song, video):
    video["title"] = "Autor x Invitado - Una canción (Official Video) Prod. Productor"
    assert choose_match(song, [video], mode="relaxed")["status"] == "accepted"


def test_missing_guest_credit_is_a_warning(song, video):
    video["title"] = "Autor - Una canción (Official Audio)"
    result = choose_match(song, [video], mode="relaxed")
    assert result["status"] == "accepted"
    assert "some_collaborators_not_credited" in result["candidates"][0]["warnings"]


def test_matching_bare_remix_before_album_is_recognized(song, video):
    song["title"] = "Una canción - Remix"
    video["title"] = "Autor ft Invitado - Una canción Remix (Visualizer) | Mi álbum"
    assert choose_match(song, [video], mode="relaxed")["status"] == "accepted"


def test_unsought_bare_remix_is_not_autoaccepted(song, video):
    video["title"] = "Autor ft Invitado - Una canción Remix (Visualizer) | Mi álbum"
    assert choose_match(song, [video], mode="relaxed")["status"] != "accepted"


def test_longer_video_intro_accepted_but_large_duration_difference_rejected(song, video):
    video["duration"] = 188
    assert choose_match(song, [video], mode="relaxed")["status"] == "accepted"
    video["duration"] = 210
    assert evaluate_candidate(song, video, mode="relaxed")["rejected"]


@pytest.mark.parametrize("qualifier", ["Cover", "Live at Venue", "Vivo Calafate", "En directo", "Karaoke", "Acústico", "Remix"])
def test_incompatible_versions_remain_rejected(song, video, qualifier):
    video["title"] += f" ({qualifier})"
    assert evaluate_candidate(song, video, mode="relaxed")["rejected"]


def test_conflicting_structured_performer_is_not_autoaccepted(song, video):
    video["artists"] = ["Otro intérprete"]
    assert choose_match(song, [video], mode="relaxed")["status"] != "accepted"


def test_unavailable_video_stays_rejected(song, video):
    video["availability"] = "needs_auth"
    assert evaluate_candidate(song, video, mode="relaxed")["rejected"]


def test_two_plausible_equivalent_uploads_choose_one(song, video):
    other = {**video, "id": "BBBBBBBBBBB"}
    assert choose_match(song, [video, other], mode="relaxed")["status"] == "accepted"
    assert choose_match(song, [video, other], mode="strict")["status"] != "accepted"


@pytest.mark.parametrize("source,target", [("Symphony No. 40 K. 550", "Symphony No. 41 K. 550"),
                                           ("Rain II", "Rain III")])
def test_numbered_works_are_not_confused_even_in_relaxed_mode(song, video, source, target):
    song["title"] = source
    video.update(title="Autor - " + target, track=target, artists=song["artists"])
    assert evaluate_candidate(song, video, mode="relaxed")["rejected"]


def test_medley_suffix_stays_for_review(song, video):
    video.update(title="Autor - Una canción / Otra canción", track=song["title"], artists=song["artists"])
    assert choose_match(song, [video], mode="relaxed")["status"] != "accepted"


def test_first_two_only_search_does_not_modify_remaining_catalog(tmp_path, monkeypatch, song):
    path = tmp_path / "estado.json"
    tracks = [{**copy.deepcopy(song), "title": f"Canción {n}", "key": f"key-{n}", "position": n,
               "track_id": f"id-{n}", "match": {"status": "pending"}, "authorization": {},
               "download": {"status": "pending"}} for n in range(1, 13)]
    untouched = copy.deepcopy(tracks[2:])
    save_state(path, {"schema_version": 1, "tracks": tracks})
    def discover(query, limit, **kwargs):
        assert limit == 3
        title = query.split(" Autor", 1)[0]
        return [{"id": "AAAAAAAAAAA", "title": "Autor x Invitado - " + title,
                 "channel": "Autor", "duration": 180}]
    mocked = Mock(side_effect=discover)
    monkeypatch.setattr("musicmatcher.youtube_search_agent._search_candidates", mocked)
    result = search_tracks(path, settings=SimpleNamespace(candidate_limit=3, matching_mode="relaxed"), track_limit=2)
    assert mocked.call_count == 2
    assert result["tracks"][2:] == untouched
    assert len(load_state(path)["tracks"]) == 12


def test_mode_change_invalidates_automatic_decision_but_reuses_candidate_metadata(tmp_path, monkeypatch, song, video):
    path = tmp_path / "estado.json"
    track = {**song, "key": "key", "position": 1, "track_id": "id", "match": {"status": "pending"}}
    save_state(path, {"schema_version": 1, "tracks": [track]})
    mocked = Mock(return_value=[video])
    monkeypatch.setattr("musicmatcher.youtube_search_agent._search_candidates", mocked)
    strict = search_tracks(path, settings=SimpleNamespace(candidate_limit=3, matching_mode="strict"))
    assert strict["tracks"][0]["match"]["status"] != "accepted"
    relaxed = search_tracks(path, settings=SimpleNamespace(candidate_limit=3, matching_mode="relaxed"))
    assert relaxed["tracks"][0]["match"]["status"] == "accepted"
    assert mocked.call_count == 1
