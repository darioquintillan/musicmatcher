"""Importer, provenance and local OAuth tests; no external requests."""

import json
import io
import threading
from unittest.mock import MagicMock, patch

import pytest

from musicmatcher.mapping_manager import load_state, save_state
from musicmatcher.playlist_importer import import_file, import_text
from musicmatcher.spotify_importer import (
    SpotifyError, _CancelablePKCE, _create_auth, build_tracks, extract_playlist,
)


def rows(**overrides):
    row = {"title": "Canción exacta (Live)", "artists": ["Artista Ñ"],
           "album": "Álbum", "duration_ms": 180000}
    row.update(overrides)
    return [row]


def imported(tmp_path, values=None, **kwargs):
    return import_text(json.dumps(values or rows()), "songs.json", tmp_path / "state.json", **kwargs)


def test_csv_bom_spanish_headers_and_collaborators(tmp_path):
    content = '\ufeffTítulo;Artistas;Álbum;Duración\n"Canción";"Artista Ñ|Otra";"Álbum";03:30\n'
    state = import_text(content, "songs.csv", tmp_path / "state.json")
    track = state["tracks"][0]
    assert track["title"] == "Canción"
    assert track["artists"] == ["Artista Ñ", "Otra"]
    assert track["album"] == "Álbum"
    assert track["duration_ms"] == 210000
    assert track["source"] == "unknown"
    assert track["ai_allowed"] is False


def test_comma_in_single_artist_name_is_not_split(tmp_path):
    content = 'title,artist,duration_seconds\nSong,"Earth, Wind & Fire",180\n'
    state = import_text(content, "songs.csv", tmp_path / "state.json")
    assert state["tracks"][0]["artists"] == ["Earth, Wind & Fire"]


@pytest.mark.parametrize("extension", ["json", "csv"])
def test_duplicates_keep_order_unique_ids_and_original_text(tmp_path, extension):
    data = rows() * 2 + rows(title="Otra")
    if extension == "json":
        text = json.dumps(data)
    else:
        text = "title,artists,duration_ms\n" + "\n".join(
            f"{row['title']},{row['artists'][0]},{row['duration_ms']}" for row in data
        )
    state = import_text(text, f"songs.{extension}", tmp_path / "state.json")
    assert [track["position"] for track in state["tracks"]] == [1, 2, 3]
    assert len({track["key"] for track in state["tracks"]}) == 3
    assert len({track["track_id"] for track in state["tracks"]}) == 3
    assert state["tracks"][1]["duplicate_of"] == state["tracks"][0]["key"]


@pytest.mark.parametrize("allowed,source,expected", [
    (False, "user_authorized", False), (True, "unknown", False),
    (True, "user_authorized", False), (True, "spotify", False),
])
def test_legacy_ai_parameter_never_enables_model_processing(tmp_path, allowed, source, expected):
    state = imported(tmp_path, ai_allowed=allowed, source=source)
    track = state["tracks"][0]
    assert track["ai_allowed"] is expected


@pytest.mark.parametrize("marker", [
    {"spotify_id": "some-id"},
    {"spotify_url": "https://open.spotify.com/track/0123456789ABCDEFGHIJKL"},
    {"uri": "spotify:track:0123456789ABCDEFGHIJKL"},
    {"source": "spotify"},
])
def test_spotify_rows_cannot_be_laundered_through_json_or_opt_in(tmp_path, marker):
    state = imported(tmp_path, rows(**marker), source="user_authorized", ai_allowed=True)
    assert state["tracks"][0]["source"] == "spotify"
    assert state["tracks"][0]["ai_allowed"] is False


def test_legacy_spotify_state_marks_all_rows_and_does_not_import_permissions(tmp_path):
    raw = {"schema_version": 1, "playlist": {"id": "spotify-list", "name": "Original",
           "url": "https://open.spotify.com/playlist/0123456789ABCDEFGHIJKL"}, "tracks": rows()}
    raw["tracks"][0].update(match={"status": "accepted", "url": "https://youtu.be/abcdefghijk"},
                            authorization={"audio": True}, download={"status": "completed"})
    state = imported(tmp_path, raw, source="user_authorized", ai_allowed=True)
    track = state["tracks"][0]
    assert state["playlist"]["source"] == "spotify"
    assert track["source"] == "spotify" and not track["ai_allowed"]
    assert track["match"]["status"] == "pending"
    assert track["authorization"] == {}
    assert track["download"]["status"] == "pending"


def test_declared_unknown_source_and_denial_cannot_be_elevated(tmp_path):
    state = imported(tmp_path, rows(source="unknown", ai_allowed=False),
                     source="user_authorized", ai_allowed=True)
    assert not state["tracks"][0]["ai_allowed"]


def test_reimport_preserves_local_decisions_but_does_not_restore_ai_grants(tmp_path):
    state = imported(tmp_path, source="user_authorized", ai_allowed=True)
    original_id = state["tracks"][0]["track_id"]
    state["tracks"][0]["match"] = {"status": "accepted", "manual": True, "url": "https://youtu.be/abcdefghijk"}
    save_state(tmp_path / "state.json", state)
    again = imported(tmp_path, source="user_authorized", ai_allowed=True)
    assert again["tracks"][0]["track_id"] == original_id
    assert again["tracks"][0]["match"]["manual"] is True
    denied = imported(tmp_path, source="user_authorized", ai_allowed=False)
    assert denied["tracks"][0]["ai_allowed"] is False
    assert denied["tracks"][0]["match"]["manual"] is True


def test_different_catalogue_requires_explicit_replace(tmp_path):
    imported(tmp_path)
    before = (tmp_path / "state.json").read_bytes()
    with pytest.raises(ValueError, match="different catalogue"):
        imported(tmp_path, rows(title="Another"))
    assert (tmp_path / "state.json").read_bytes() == before
    state = imported(tmp_path, rows(title="Another"), replace=True)
    assert state["tracks"][0]["title"] == "Another"


@pytest.mark.parametrize("content,filename", [
    ('[{"title":"No artist"}]', "songs.json"),
    ('[{"title":"Song","artists":["Artist"],"duration_ms":-1}]', "songs.json"),
    ('title,artists,duration\nSong,Artist,2:75\n', "songs.csv"),
    ('title,artists,duration\nSong,Artist,NaN\n', "songs.csv"),
    ('title,title,artists\nSong,Other,Artist\n', "songs.csv"),
    ('title,artists\nSong,Artist,Extra\n', "songs.csv"),
    ('{"tracks": null}', "songs.json"),
])
def test_invalid_input_does_not_publish_partial_state(tmp_path, content, filename):
    with pytest.raises(ValueError):
        import_text(content, filename, tmp_path / "state.json")
    assert not (tmp_path / "state.json").exists()


def test_import_file_and_progress_with_cancel_preserve_previous_state(tmp_path):
    path = tmp_path / "songs.csv"
    path.write_text("title,artists\nFirst,Artist\nSecond,Artist\n", encoding="utf-8-sig")
    event = threading.Event()
    progress = []

    def callback(value):
        progress.append(value)
        event.set()

    with pytest.raises(InterruptedError):
        import_file(path, tmp_path / "state.json", progress=callback, stop_event=event)
    assert progress[0]["stage"] == "import" and progress[0]["total"] == 2
    assert not (tmp_path / "state.json").exists()


def test_spotify_build_and_unchanged_snapshot_always_disable_ai(tmp_path):
    item = {"item": {"id": "track-a", "type": "track", "name": "Song",
                     "artists": [{"name": "Artist"}], "duration_ms": 180000}}
    tracks = build_tracks([item, item])
    assert len({track["track_id"] for track in tracks}) == 2
    assert all(track["source"] == "spotify" and not track["ai_allowed"] for track in tracks)
    playlist_id = "0123456789ABCDEFGHIJKL"
    path = tmp_path / "state.json"
    old = {"schema_version": 1, "playlist": {"id": playlist_id, "snapshot_id": "same"}, "tracks": tracks}
    old["tracks"][0].update(source="user_authorized", ai_allowed=True)
    save_state(path, old)
    with patch("musicmatcher.spotify_importer._create_auth"), patch("musicmatcher.spotify_importer._SpotifyClient") as client:
        client.return_value.get.return_value = {"snapshot_id": "same"}
        state = extract_playlist(f"https://open.spotify.com/playlist/{playlist_id}", path)
    assert not state["tracks"][0]["ai_allowed"]
    assert state["playlist"]["source"] == "spotify"
    assert load_state(path)["tracks"][0]["source"] == "spotify"


def test_explicit_spotify_config_does_not_mutate_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SPOTIPY_CLIENT_ID", "environment-id")
    monkeypatch.setenv("SPOTIPY_REDIRECT_URI", "http://127.0.0.1:9999/callback")
    auth = _create_auth(tmp_path / "state.json", client_id="configured-id",
                        redirect_uri="http://127.0.0.1:8888/callback")
    assert auth.client_id == "configured-id"
    assert auth.redirect_uri == "http://127.0.0.1:8888/callback"
    import os
    assert os.environ["SPOTIPY_CLIENT_ID"] == "environment-id"
    assert os.environ["SPOTIPY_REDIRECT_URI"] == "http://127.0.0.1:9999/callback"
    other = _create_auth(tmp_path / "state.json", client_id="other-id")
    assert auth.cache_handler.cache_path != other.cache_handler.cache_path


def test_pkce_browser_wait_is_bounded_and_cancellable():
    auth = _CancelablePKCE(client_id="configured-id", redirect_uri="http://127.0.0.1:8888/callback")
    server = MagicMock()
    server.__enter__.return_value = server
    with patch("musicmatcher.spotify_importer.HTTPServer.__init__", return_value=None), \
         patch("musicmatcher.spotify_importer.HTTPServer.__enter__", return_value=server), \
         patch("musicmatcher.spotify_importer.HTTPServer.__exit__", return_value=False), \
         patch("musicmatcher.spotify_importer.webbrowser.open", return_value=True), \
         patch("musicmatcher.spotify_importer.time.monotonic", side_effect=[0.0, 181.0]):
        with pytest.raises(SpotifyError, match="timed out"):
            auth.get_authorization_code()
    event = threading.Event()
    event.set()
    auth.stop_event = event
    with pytest.raises(InterruptedError):
        auth.get_authorization_code()


def test_pkce_rejects_wrong_state_then_accepts_exact_callback_without_logging_codes():
    auth = _CancelablePKCE(client_id="configured-id", redirect_uri="http://127.0.0.1:8888/callback")
    responses = []

    def initialize(server, _address, handler):
        server.RequestHandlerClass = handler

    def handle(server):
        handler = object.__new__(server.RequestHandlerClass)
        state = "forged" if not responses else auth.state
        handler.path = f"/callback?code=not-a-real-code&state={state}"
        handler.send_response = lambda status: responses.append(status)
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = io.BytesIO()
        handler.do_GET()

    with patch("musicmatcher.spotify_importer.HTTPServer.__init__", initialize), \
         patch("musicmatcher.spotify_importer.HTTPServer.handle_request", handle), \
         patch("musicmatcher.spotify_importer.HTTPServer.server_close"), \
         patch("musicmatcher.spotify_importer.webbrowser.open", return_value=True):
        assert auth.get_authorization_code() == "not-a-real-code"
    assert responses == [400, 200]
