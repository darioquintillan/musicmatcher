"""CSV/JSON import without accounts, network requests or AI calls."""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
from typing import Any, Callable
import unicodedata

from pydantic import ValidationError

from .mapping_manager import load_state, save_state, utc_now
from .models import Song, TrackInput, contains_spotify_data


MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_TRACKS = 10000
_ALIASES = {
    "title": "title", "titulo": "title", "cancion": "title", "name": "title",
    "trackname": "title", "nombre": "title", "song": "title",
    "artist": "artists", "artists": "artists", "artista": "artists",
    "artistas": "artists", "artistname": "artists", "artistnames": "artists",
    "album": "album", "albumname": "album",
    "durationms": "duration_ms", "duracionms": "duration_ms",
    "durationseconds": "duration_seconds", "duracionsegundos": "duration_seconds",
    "duration": "duration", "duracion": "duration",
    "spotifyid": "spotify_id", "spotifytrackid": "spotify_id",
    "spotifyurl": "spotify_url", "trackurl": "spotify_url",
    "spotifyuri": "spotify_uri", "trackuri": "spotify_uri", "uri": "spotify_uri",
    "coverurl": "cover_url", "isrc": "isrc", "explicit": "explicit",
    "source": "source", "aiallowed": "ai_allowed", "unavailablereason": "unavailable_reason",
}


def _header(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in value if not unicodedata.combining(c)))


def _identity(track: dict) -> str:
    fields = {name: track.get(name) for name in (
        "title", "artists", "album", "duration_ms", "isrc", "explicit", "spotify_id",
    )}
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _duration(value: Any, *, milliseconds: bool = False) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("duration must be a positive number or mm:ss")
    if isinstance(value, str) and ":" in value:
        if milliseconds:
            raise ValueError("duration_ms must be an integer")
        parts = value.strip().split(":")
        if len(parts) not in (2, 3) or not all(re.fullmatch(r"\d+(?:\.\d+)?", p) for p in parts):
            raise ValueError("duration must use mm:ss or hh:mm:ss")
        numbers = [float(p) for p in parts]
        if any(number >= 60 for number in numbers[1:]):
            raise ValueError("seconds and inner minutes must be less than 60")
        seconds = sum(number * 60 ** index for index, number in enumerate(reversed(numbers)))
        result = seconds * 1000
    else:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("duration must be a positive number or mm:ss") from exc
        result = number if milliseconds else number * 1000
    if not math.isfinite(result) or result <= 0 or result > 2**53:
        raise ValueError("duration must be positive and finite")
    if milliseconds and not result.is_integer():
        raise ValueError("duration_ms must be an integer")
    return round(result)


def _boolean(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes", "si", "sí"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    raise ValueError("explicit must be true or false")


def _normalize_row(row: dict, position: int) -> dict:
    mapped = {}
    for key, value in row.items():
        if not isinstance(key, str):
            raise ValueError(f"Row {position}: missing or invalid column name")
        destination = _ALIASES.get(_header(key), key)
        if destination in mapped:
            raise ValueError(f"Row {position}: repeated field {destination}")
        mapped[destination] = value
    artists = mapped.get("artists")
    if isinstance(artists, str):
        # A comma may be part of a band's name. Use | or ; for collaborators.
        artists = [part.strip() for part in re.split(r"[|;]", artists) if part.strip()]
    if isinstance(artists, list):
        artists = [artist["name"] if isinstance(artist, dict) and "name" in artist else artist
                   for artist in artists]
    mapped["artists"] = artists
    supplied = []
    for name in ("duration_ms", "duration_seconds", "duration"):
        if mapped.get(name) not in (None, ""):
            supplied.append(_duration(mapped[name], milliseconds=name == "duration_ms"))
    if supplied and any(value != supplied[0] for value in supplied):
        raise ValueError(f"Row {position}: conflicting duration values")
    mapped["duration_ms"] = supplied[0] if supplied else None
    mapped["explicit"] = _boolean(mapped.get("explicit"))
    for field in ("album", "spotify_id", "spotify_url", "cover_url", "isrc"):
        if mapped.get(field) == "":
            mapped[field] = None
    if mapped.get("spotify_uri"):
        uri = str(mapped["spotify_uri"])
        match = re.fullmatch(r"spotify:track:([A-Za-z0-9]{22})", uri)
        if match:
            mapped.setdefault("spotify_id", match.group(1))
            mapped.setdefault("spotify_url", f"https://open.spotify.com/track/{match.group(1)}")
    return mapped


def _read_rows(content: str, filename: str) -> tuple[list, dict]:
    extension = Path(filename).suffix.casefold()
    if extension == ".json":
        try:
            value = json.loads(content.lstrip("\ufeff"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON at line {exc.lineno}, column {exc.colno}") from exc
        if isinstance(value, list):
            return value, {}
        if isinstance(value, dict) and isinstance(value.get("tracks"), list):
            playlist = value.get("playlist") or {}
            if not isinstance(playlist, dict):
                raise ValueError("playlist must be an object")
            context = dict(playlist)
            if value.get("source"):
                context.setdefault("source", value["source"])
            return value["tracks"], context
        raise ValueError("JSON must contain an array or an object with a tracks array")
    if extension != ".csv":
        raise ValueError("Select a UTF-8 .csv or .json file")
    text = content.lstrip("\ufeff")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text, newline=""), dialect=dialect)
    if not reader.fieldnames or not all(reader.fieldnames):
        raise ValueError("CSV must have a header row containing title and artists")
    if len({_ALIASES.get(_header(name), name) for name in reader.fieldnames}) != len(reader.fieldnames):
        raise ValueError("CSV contains repeated columns")
    rows = []
    for row in reader:
        if None in row:
            raise ValueError("CSV row has more values than columns; quote values containing the delimiter")
        if any(value not in (None, "") for value in row.values()):
            rows.append(row)
        if len(rows) > MAX_TRACKS:
            raise ValueError(f"Import is limited to {MAX_TRACKS} tracks")
    return rows, {}


def import_text(content: str, filename: str, state_path: Path, *, ai_allowed: bool = False,
                source: str = "unknown", replace: bool = False,
                progress: Callable[[dict], None] | None = None, stop_event=None) -> dict:
    """Validate the whole input before publishing; never trust imported grants.

    ai_allowed is accepted for legacy call compatibility but always disabled:
    this project contains no AI integration.
    """
    if source not in {"spotify", "user_authorized", "unknown"}:
        raise ValueError("Invalid source provenance")
    if len(content.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise ValueError("Import is limited to 10 MiB")
    rows, context = _read_rows(content, filename)
    if not rows:
        raise ValueError("The file contains no tracks")
    if len(rows) > MAX_TRACKS:
        raise ValueError(f"Import is limited to {MAX_TRACKS} tracks")
    state_path = Path(state_path)
    old = load_state(state_path) if state_path.exists() else {}
    previous = {}
    for track in old.get("tracks", []):
        previous.setdefault((_identity(track), track.get("source", "unknown"),
                             track.get("ai_allowed", False)), []).append(track)
    tracks, used_keys, occurrences, first_keys = [], set(), {}, {}
    for position, row in enumerate(rows, 1):
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Import cancelled; the previous catalogue was preserved")
        if not isinstance(row, dict):
            raise ValueError(f"Row {position} must be an object")
        try:
            mapped = _normalize_row(row, position)
            # Unavailable legacy Spotify rows still occupy their original slot.
            unavailable = mapped.get("unavailable_reason")
            spotify = source == "spotify" or contains_spotify_data(context) or contains_spotify_data(mapped)
            if unavailable and spotify:
                mapped.setdefault("title", "Unavailable Spotify item")
                if not mapped.get("artists"):
                    mapped["artists"] = ["Unknown artist"]
            validated = TrackInput.model_validate(mapped).model_dump()
        except (ValueError, ValidationError) as exc:
            raise ValueError(f"Row {position}: {exc}") from exc
        origin = "spotify" if spotify else mapped.get("source", context.get("source", source))
        if not isinstance(origin, str) or origin not in {"spotify", "user_authorized", "unknown"}:
            origin = "unknown"
        permitted = False
        identity = _identity(validated)
        occurrence = occurrences.get(identity, 0) + 1
        occurrences[identity] = occurrence
        base = f"{validated['title']} - {', '.join(validated['artists'])}"
        key, suffix = base, occurrence
        while key in used_keys:
            key = f"{base} [{identity[:10]}:{suffix}]"
            suffix += 1
        used_keys.add(key)
        track = {
            **validated, "key": key, "track_id": f"song:{identity[:24]}:{occurrence}",
            "position": position, "duplicate_of": first_keys.get(identity),
            "source": origin, "ai_allowed": permitted,
            "match": {"status": "unavailable" if unavailable else "pending"},
            "download": {"status": "pending"}, "authorization": {},
        }
        if unavailable:
            track["unavailable_reason"] = unavailable
        first_keys.setdefault(identity, key)
        candidates = previous.get((identity, origin, permitted), [])
        if candidates and not unavailable:
            prior = candidates[min(occurrence - 1, len(candidates) - 1)]
            for field in ("match", "download", "authorization"):
                if isinstance(prior.get(field), dict):
                    track[field] = copy.deepcopy(prior[field])
        tracks.append(Song.model_validate(track).model_dump())
        if progress:
            progress({"stage": "import", "done": position, "total": len(rows),
                      "message": f"Imported {position}/{len(rows)} tracks"})

    fingerprint = hashlib.sha256(json.dumps([_identity(track) for track in tracks]).encode()).hexdigest()
    old_fingerprint = hashlib.sha256(json.dumps([_identity(track) for track in old.get("tracks", [])]).encode()).hexdigest()
    if old.get("tracks") and fingerprint != old_fingerprint and not replace:
        raise ValueError("A different catalogue already exists. Use --replace or clear it first")
    origins = {track["source"] for track in tracks}
    playlist_source = "spotify" if contains_spotify_data(context) else (
        next(iter(origins)) if len(origins) == 1 else "unknown"
    )
    state = {"schema_version": 1, "playlist": {
        "id": f"import:{fingerprint[:24]}", "name": context.get("name") or Path(filename).stem,
        "source": playlist_source, "imported_at": utc_now(), "dataset_fingerprint": fingerprint,
    }, "tracks": tracks}
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("Import cancelled; the previous catalogue was preserved")
    save_state(state_path, state)
    return state


def import_file(path: Path, state_path: Path, *, ai_allowed: bool = False,
                source: str = "unknown", replace: bool = False,
                progress: Callable[[dict], None] | None = None, stop_event=None) -> dict:
    path = Path(path)
    if path.stat().st_size > MAX_IMPORT_BYTES:
        raise ValueError("Import is limited to 10 MiB")
    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Save the input file as UTF-8 before importing it") from exc
    return import_text(content, path.name, state_path, ai_allowed=ai_allowed, source=source,
                       replace=replace, progress=progress, stop_event=stop_event)
