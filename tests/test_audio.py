"""Sin descargas externas: orquestación simulada y audio de silencio local."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import threading
from unittest.mock import MagicMock

import pytest

from musicmatcher import audio_downloader as audio
from musicmatcher.metadata_manager import MetadataError, MetadataManager, check_declared_audio_size


URL = "https://www.youtube.com/watch?v=abcdefghijk"
OTHER = "https://www.youtube.com/watch?v=12345678901"


def track(key="Canción - Artista", position=1):
    return {
        "key": key, "track_id": f"local-{position}", "position": position,
        "title": "Canción (Álbum)", "artists": ["AC; DC", "Invitado & más"],
        "album": "Álbum original", "duration_ms": 5000, "source": "json",
        "match": {"status": "accepted", "url": URL, "decision": "manual"},
        "authorization": {"audio": True, "url": URL, "note": "Grabación propia"},
        "download": {"status": "pending"},
    }


def inputs(tmp_path, tracks):
    state = tmp_path / "estado.json"
    links = tmp_path / "canciones.json"
    state.write_text(json.dumps({"schema_version": 1, "playlist": {}, "tracks": tracks}), encoding="utf-8")
    links.write_text(json.dumps({t["key"]: URL for t in tracks}), encoding="utf-8")
    return state, links


def fake_audio(_url, stage, _track, _bitrate, _location, **_kwargs):
    path = stage / "audio.mp3"
    path.write_bytes(b"fake audio for orchestration")
    return path


def fake_environment(monkeypatch):
    manager = MagicMock()
    monkeypatch.setattr(audio, "MetadataManager", lambda _location: manager)
    monkeypatch.setattr(audio, "check_ffmpeg", lambda _location: None)
    return manager


def test_exact_local_duplicates_ignore_occurrence_ids_but_not_versions():
    first = track()
    duplicate = track("Repetida", 5)
    assert audio._fingerprint(first, URL, 320) == audio._fingerprint(duplicate, URL, 320)
    duplicate["album"] = "Otra edición"
    assert audio._fingerprint(first, URL, 320) != audio._fingerprint(duplicate, URL, 320)
    assert audio._fingerprint(first, URL, 320) != audio._fingerprint(first, URL, 192)
    assert audio._fingerprint(first, URL, 320) != audio._fingerprint(first, OTHER, 320)


def test_duplicate_download_and_rerun_reuse_valid_mp3(tmp_path, monkeypatch):
    state, links = inputs(tmp_path, [track(), track("Repetida", 2)])
    fake_environment(monkeypatch)
    fetch = MagicMock(side_effect=fake_audio)
    monkeypatch.setattr(audio, "_download_audio", fetch)
    output = tmp_path / "salida"
    first = audio.download_tracks(state, links, output)
    second = audio.download_tracks(state, links, output)
    assert fetch.call_count == 1
    assert all(t["download"]["status"] == "completed" for t in first["tracks"])
    assert all(t["download"]["reused"] for t in second["tracks"])
    references = [line for line in (output / "playlist.m3u8").read_text(encoding="utf-8").splitlines()
                  if line.startswith("./")]
    assert len(references) == 2
    assert references[0] == references[1]


def test_permission_is_bound_to_actual_url_and_note(tmp_path, monkeypatch):
    first = track()
    first["authorization"]["url"] = OTHER
    state, links = inputs(tmp_path, [first])
    fake_environment(monkeypatch)
    fetch = MagicMock()
    monkeypatch.setattr(audio, "_download_audio", fetch)
    result = audio.download_tracks(state, links, tmp_path / "salida")
    assert result["tracks"][0]["download"]["status"] == "authorization_required"
    fetch.assert_not_called()


def test_download_first_ten_does_not_touch_other_authorized_tracks(tmp_path, monkeypatch):
    tracks = [track(f"song-{n}", n) for n in range(1, 13)]
    for n, current in enumerate(tracks, 1):
        current["title"] = f"Song {n}"
    untouched = copy.deepcopy(tracks[10:])
    state, links = inputs(tmp_path, tracks)
    fake_environment(monkeypatch)
    fetch = MagicMock(side_effect=fake_audio)
    monkeypatch.setattr(audio, "_download_audio", fetch)
    result = audio.download_tracks(state, links, tmp_path / "out", track_limit=10)
    assert fetch.call_count == 10
    assert result["last_download"]["total"] == 10
    assert result["tracks"][10:] == untouched


def test_manually_edited_mapping_does_not_override_accepted_match(tmp_path, monkeypatch):
    first = track()
    state, links = inputs(tmp_path, [first])
    links.write_text(json.dumps({first["key"]: OTHER}), encoding="utf-8")
    fake_environment(monkeypatch)
    fetch = MagicMock()
    monkeypatch.setattr(audio, "_download_audio", fetch)
    result = audio.download_tracks(state, links, tmp_path / "salida")
    assert result["tracks"][0]["download"]["status"] == "error"
    fetch.assert_not_called()


def test_invalid_existing_file_is_preserved(tmp_path, monkeypatch):
    first = track()
    state, links = inputs(tmp_path, [first])
    manager = fake_environment(monkeypatch)
    manager.validate.side_effect = MetadataError("dañado")
    output = tmp_path / "salida"
    output.mkdir()
    path = output / audio.output_filename(first, audio._fingerprint(first, URL, 320), 320)
    path.write_bytes(b"do not overwrite")
    fetch = MagicMock()
    monkeypatch.setattr(audio, "_download_audio", fetch)
    result = audio.download_tracks(state, links, output)
    assert result["tracks"][0]["download"]["status"] == "error"
    assert path.read_bytes() == b"do not overwrite"
    fetch.assert_not_called()


def test_progress_hook_reports_actual_percentage_and_can_stop():
    updates = []
    stop = threading.Event()
    current = track()
    hook = audio._progress_hook(current, 1, 3, updates.append, stop)
    hook({"status": "downloading", "downloaded_bytes": 35, "total_bytes": 100})
    assert updates[-1] == {"stage": "download", "done": 1, "total": 3,
                           "message": "Descargando audio…", "current_title": current["title"],
                           "download_percent": 35.0}
    stop.set()
    with pytest.raises(InterruptedError):
        hook({"status": "downloading", "downloaded_bytes": 40, "total_bytes": 100})


def test_stop_keeps_partial_and_publishes_only_completed_playlist(tmp_path, monkeypatch):
    first, second = track(), track("Segunda", 2)
    second["title"] = "Segunda canción"
    state, links = inputs(tmp_path, [first, second])
    fake_environment(monkeypatch)
    stop = threading.Event()
    calls = 0

    def fetch(url, stage, current, bitrate, location, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return fake_audio(url, stage, current, bitrate, location, **kwargs)
        (stage / "audio.webm.part").write_bytes(b"resumable bytes")
        stop.set()
        raise audio.DownloadStopped("detenida")

    monkeypatch.setattr(audio, "_download_audio", fetch)
    output = tmp_path / "salida"
    result = audio.download_tracks(state, links, output, stop_event=stop)
    assert result["tracks"][0]["download"]["status"] == "completed"
    assert result["tracks"][1]["download"]["status"] == "interrupted"
    assert result["last_download"]["status"] == "interrupted"
    assert len(list(output.glob(".partial/*/*.part"))) == 1
    playlist = (output / "playlist.m3u8").read_text(encoding="utf-8")
    assert playlist.count("#EXTINF:") == 1
    assert "Segunda canción" not in playlist


def test_revoked_authorization_removes_stale_playlist_entries(tmp_path, monkeypatch):
    first = track()
    first["authorization"]["audio"] = False
    state, links = inputs(tmp_path, [first])
    fake_environment(monkeypatch)
    output = tmp_path / "salida"
    output.mkdir()
    playlist = output / "playlist.m3u8"
    playlist.write_text("#EXTM3U\n./stale.mp3\n", encoding="utf-8")
    audio.download_tracks(state, links, output)
    assert playlist.read_text(encoding="utf-8") == "#EXTM3U\n"


def test_portable_name_preserves_identity_after_sanitizing():
    first = track()
    first["title"] = "CON." + "¿Á/漢字:%?*" * 60
    name = audio.output_filename(first, "a" * 64, 320)
    assert len(name.encode("utf-8")) <= 180
    assert not any(c in name for c in '<>:"/\\|?*')
    assert name.startswith("_CON.")
    assert name != audio.output_filename(first, "b" * 64, 320)


HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def generate_silence(path: Path):
    subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "anullsrc=r=44100:cl=stereo", "-t", "5", "-c:a", "libmp3lame",
                    "-b:a", "320k", str(path)], check=True, capture_output=True)


@pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg/ffprobe externos no instalados")
def test_ffmpeg_metadata_preserves_originals_and_ambiguous_artist_separator(tmp_path):
    path = tmp_path / "silencio.mp3"
    generate_silence(path)
    first = track()
    first["title"] = "Título $(literal) `literal` (Remix)"
    first["source_url"] = "https://example.org/recording"
    manager = MetadataManager()
    fingerprint = audio._fingerprint(first, URL, 320)
    manager.write(path, first, fingerprint, URL, 320)
    probe = manager.validate(path, first, fingerprint, URL, 320)
    tags = probe["format"]["tags"]
    assert tags["title"] == first["title"]
    assert tags["artist"] == "; ".join(first["artists"])
    assert json.loads(tags["musicmatcher_artists_json"]) == first["artists"]
    assert tags["musicmatcher_source_url"] == first["source_url"]
    wrong = copy.deepcopy(first)
    wrong["title"] = "Título de YouTube"
    with pytest.raises(MetadataError, match="title"):
        manager.validate(path, wrong, fingerprint, URL, 320)


@pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg/ffprobe externos no instalados")
def test_authorized_local_cover_is_attached_with_custom_binary_directory(tmp_path):
    path = tmp_path / "con_portada.mp3"
    cover = tmp_path / "portada.png"
    generate_silence(path)
    subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=c=blue:s=16x16", "-frames:v", "1", str(cover)],
                   check=True, capture_output=True)
    first = track()
    fingerprint = audio._fingerprint(first, URL, 320, cover.read_bytes())
    manager = MetadataManager(str(Path(shutil.which("ffmpeg")).parent))
    manager.write(path, first, fingerprint, URL, 320, cover)
    probe = manager.validate(path, first, fingerprint, URL, 320)
    pictures = [s for s in probe["streams"] if s.get("codec_type") == "video"]
    assert len(pictures) == 1
    assert pictures[0]["disposition"]["attached_pic"] == 1


@pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg/ffprobe externos no instalados")
def test_truncated_xing_file_is_rejected_before_remux_can_hide_it(tmp_path):
    path = tmp_path / "truncado.mp3"
    generate_silence(path)
    first = track()
    fingerprint = audio._fingerprint(first, URL, 320)
    manager = MetadataManager()
    manager.write(path, first, fingerprint, URL, 320)
    manager.validate(path, first, fingerprint, URL, 320)
    before = float(manager.probe(path)["format"]["duration"])
    data = path.read_bytes()
    path.write_bytes(data[:len(data) // 2])
    # ffprobe también puede seguir informando la duración original de Xing.
    assert float(manager.probe(path)["format"]["duration"]) == pytest.approx(before)
    with pytest.raises(MetadataError, match="truncado"):
        check_declared_audio_size(path)
    with pytest.raises(MetadataError, match="truncado"):
        manager.validate(path, first, fingerprint, URL, 320)
    with pytest.raises(MetadataError, match="truncado"):
        manager.write(path, first, fingerprint, URL, 320)
