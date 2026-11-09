"""Persistencia sencilla. Los textos originales nunca se normalizan al guardar."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import parse_qs, urlparse


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, value: object) -> None:
    """El archivo anterior sobrevive si el proceso se interrumpe al escribir."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                # Windows may briefly deny replacement while the UI reads a snapshot.
                time.sleep(0.01 * 2 ** attempt)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_state(path: Path) -> dict:
    with Path(path).open(encoding="utf-8-sig") as stream:
        state = json.load(stream)
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ValueError("Estado incompatible: se esperaba schema_version=1.")
    tracks = state.get("tracks")
    if not isinstance(tracks, list):
        raise ValueError("El estado debe contener una lista tracks.")
    keys = set()
    for track in tracks:
        if not isinstance(track, dict) or not isinstance(track.get("key"), str):
            raise ValueError("Cada canción debe tener una clave de texto.")
        if track["key"] in keys:
            raise ValueError(f"Clave de canción duplicada: {track['key']}")
        keys.add(track["key"])
    return state


def save_state(path: Path, state: dict) -> None:
    state["updated_at"] = utc_now()
    atomic_json(Path(path), state)


def canonical_youtube_url(url: str) -> str:
    """Acepta exclusivamente URLs de vídeo de YouTube, nunca comandos ni playlists."""
    if not isinstance(url, str):
        raise ValueError("La URL de YouTube debe ser texto.")
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError(f"URL de YouTube no válida: {url}")
    host = (parsed.hostname or "").lower()
    if parsed.port not in {None, 80, 443}:
        raise ValueError("No se admiten puertos personalizados en URLs de YouTube.")
    if host == "youtu.be":
        video_id = parsed.path.strip("/")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif re.fullmatch(r"/(?:shorts|embed)/[A-Za-z0-9_-]{11}/?", parsed.path):
            video_id = parsed.path.rstrip("/").split("/")[-1]
        else:
            video_id = ""
    else:
        video_id = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise ValueError(f"Se esperaba un enlace a un vídeo de YouTube: {url}")
    return f"https://www.youtube.com/watch?v={video_id}"


def track_identity(track: dict) -> str:
    """No fusionar homónimos o ediciones distintas por su título."""
    fields = {name: track.get(name) for name in (
        "spotify_id", "title", "artists", "album", "duration_ms", "explicit", "isrc"
    )}
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def export_results(state_path: Path, links_path: Path | None = None) -> None:
    state_path = Path(state_path)
    state = load_state(state_path)
    links = {}
    review = []
    for track in state["tracks"]:
        match = track.get("match", {})
        if match.get("status") == "accepted":
            links[track["key"]] = canonical_youtube_url(match["url"])
        else:
            review.append({
                "key": track["key"], "position": track.get("position"),
                "title": track.get("title"), "artists": track.get("artists"),
                "duration_ms": track.get("duration_ms"),
                "spotify_url": track.get("spotify_url"),
                "unavailable_reason": track.get("unavailable_reason"), "match": match,
            })
    atomic_json(links_path or state_path.parent / "canciones.json", links)
    atomic_json(state_path.parent / "revision.json", review)
    atomic_json(state_path.parent / "mapping.json", build_mapping(state))


def build_mapping(state: dict) -> dict:
    """Portable ordered export. Confidence scores are heuristics, not probabilities."""
    rows = []
    for track in state["tracks"]:
        match = track.get("match", {})
        url = canonical_youtube_url(match["url"]) if match.get("status") == "accepted" else None
        score = match.get("score")
        rows.append({
            "id": track.get("track_id") or track["key"], "key": track["key"],
            "position": track.get("position"), "title": track.get("title"),
            "artists": track.get("artists", []), "album": track.get("album"),
            "duration_ms": track.get("duration_ms"), "source": track.get("source", "unknown"),
            "spotify_id": track.get("spotify_id"), "spotify_url": track.get("spotify_url"),
            "youtube_url": url, "youtube_id": url.rsplit("=", 1)[-1] if url else None,
            "confidence": min(1, max(0, score / 100)) if isinstance(score, (int, float)) else None,
            "confidence_kind": "heuristic", "status": match.get("status", "pending"),
            "reason": match.get("note") or match.get("reasons", []),
            "duplicate_of": track.get("duplicate_of"),
        })
    return {"schema_version": 1, "playlist": state.get("playlist", {}), "tracks": rows}


@contextmanager
def state_lock(path: Path):
    """Bloqueo del sistema operativo: una interrupción libera el bloqueo al salir."""
    lock_path = Path(path).with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Otra ejecución está utilizando este estado.") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
