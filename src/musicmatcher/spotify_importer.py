"""Extrae metadatos de Spotify; nunca solicita ni descarga su audio.

Spotipy se ocupa únicamente de OAuth PKCE. Las peticiones de datos utilizan
el endpoint actual /items para no depender de los nombres antiguos del SDK.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import math
import os
import re
import secrets
import socket
import time
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse
import webbrowser

import requests
from spotipy.cache_handler import CacheFileHandler
from spotipy.exceptions import SpotifyOauthError
from spotipy.oauth2 import SpotifyPKCE

from .mapping_manager import atomic_json, load_state, save_state, utc_now


class PrivateCacheHandler(CacheFileHandler):
    """Atomic temporary files use private permissions for renewable OAuth tokens."""

    def save_token_to_cache(self, token_info):
        atomic_json(Path(self.cache_path), token_info)


LOGGER = logging.getLogger(__name__)
API_BASE = "https://api.spotify.com/v1"
SCOPES = "playlist-read-private playlist-read-collaborative"
MAX_ATTEMPTS = 4
MAX_RETRY_WAIT = 30.0
REQUEST_TIMEOUT = (10, 30)
AUTHORIZATION_TIMEOUT = 180.0


class SpotifyError(RuntimeError):
    """Error recuperable de autorización, acceso o extracción de Spotify."""


def parse_playlist_id(url: str) -> str:
    """Acepta una URL de playlist o su URI spotify:playlist:ID."""
    value = url.strip()
    uri_match = re.fullmatch(r"spotify:playlist:([A-Za-z0-9]{22})", value)
    if uri_match:
        return uri_match.group(1)
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "open.spotify.com"
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Introduce una URL HTTPS de playlist de open.spotify.com.")
    match = re.fullmatch(
        r"/(?:intl-[A-Za-z-]+/)?playlist/([A-Za-z0-9]{22})/?", parsed.path
    )
    if not match:
        raise ValueError("La URL no contiene un identificador válido de playlist.")
    return match.group(1)


def _fingerprint(track: dict) -> str:
    # Las posiciones, claves y direcciones temporales de portada no cambian
    # la identidad musical. Estos campos sí pueden distinguir otra versión.
    fields = (
        "title", "artists", "album", "duration_ms", "isrc", "explicit",
        "unavailable_reason",
    )
    metadata = {field: track.get(field) for field in fields}
    payload = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _optional_string(value) -> str | None:
    return value if isinstance(value, str) and value else None


def build_tracks(items: list, previous_tracks: list | None = None) -> list[dict]:
    """Normaliza elementos paginados sin perder su orden ni sus repeticiones.

    Solo reutiliza decisiones anteriores si coinciden Spotify ID y metadatos.
    La normalización necesaria para buscar se hará en otro módulo: aquí se
    conserva el título exacto recibido de Spotify.
    """
    previous = {}
    for old in previous_tracks or []:
        if isinstance(old, dict) and old.get("spotify_id"):
            previous.setdefault((old["spotify_id"], _fingerprint(old)), []).append(old)

    result = []
    used_keys = set()
    first_key_by_id = {}
    occurrence_by_id = {}
    previous_used = {}
    for position, entry in enumerate(items, start=1):
        wrapper = entry if isinstance(entry, dict) else {}
        # Si existe item=null, no recuperamos por accidente un track obsoleto.
        raw = wrapper.get("item") if "item" in wrapper else wrapper.get("track")
        raw = raw if isinstance(raw, dict) else {}
        title = raw.get("name") if isinstance(raw.get("name"), str) else "Elemento no disponible"
        raw_artists = raw.get("artists")
        raw_artists = raw_artists if isinstance(raw_artists, list) else []
        artists = [
            artist["name"] for artist in raw_artists
            if isinstance(artist, dict) and isinstance(artist.get("name"), str)
        ]
        spotify_id = _optional_string(raw.get("id"))
        album = raw.get("album") if isinstance(raw.get("album"), dict) else {}
        images = album.get("images") if isinstance(album.get("images"), list) else []
        cover_url = next((
            image["url"] for image in images
            if isinstance(image, dict) and _optional_string(image.get("url"))
        ), None)
        duration = raw.get("duration_ms")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
            duration = None
        external_ids = raw.get("external_ids") or {}
        external_ids = external_ids if isinstance(external_ids, dict) else {}
        external_urls = raw.get("external_urls") or {}
        external_urls = external_urls if isinstance(external_urls, dict) else {}

        reason = None
        if not raw:
            reason = "missing_item"
        elif wrapper.get("is_local") or raw.get("is_local"):
            reason = "local_file"
        elif raw.get("type") == "episode":
            reason = "episode"
        elif raw.get("type") not in (None, "track"):
            reason = "unsupported_type"
        elif not spotify_id:
            reason = "missing_spotify_id"
        elif not title or not artists:
            reason = "incomplete_metadata"

        base_key = f"{title} - {', '.join(artists) or 'Artista desconocido'}"
        occurrence = occurrence_by_id.get(spotify_id, 0) + 1
        occurrence_by_id[spotify_id] = occurrence
        key = base_key
        suffix = occurrence
        while key in used_keys:
            key = f"{base_key} [{spotify_id or 'sin-id'}:{suffix}]"
            suffix += 1
        used_keys.add(key)

        duplicate_of = first_key_by_id.get(spotify_id) if spotify_id else None
        if spotify_id:
            first_key_by_id.setdefault(spotify_id, key)
        track = {
            "key": key,
            "track_id": f"spotify:{spotify_id or _fingerprint(raw)[:24]}:{occurrence}",
            "spotify_id": spotify_id,
            "spotify_url": _optional_string(external_urls.get("spotify")) or (
                f"https://open.spotify.com/track/{spotify_id}" if spotify_id else None
            ),
            "position": position,
            "title": title,
            "artists": artists,
            "album": _optional_string(album.get("name")),
            "duration_ms": duration,
            "isrc": _optional_string(external_ids.get("isrc")),
            "explicit": raw.get("explicit") if isinstance(raw.get("explicit"), bool) else None,
            "cover_url": cover_url,
            "duplicate_of": duplicate_of,
            "match": {"status": "pending"},
            "download": {"status": "pending"},
            "authorization": {},
            "source": "spotify",
            "ai_allowed": False,
        }
        if reason:
            track["unavailable_reason"] = reason
            track["match"] = {"status": "unavailable"}
        else:
            identity = (spotify_id, _fingerprint(track))
            candidates = previous.get(identity, [])
            if candidates:
                index = min(previous_used.get(identity, 0), len(candidates) - 1)
                previous_used[identity] = index + 1
                for field in ("match", "download", "authorization"):
                    if isinstance(candidates[index].get(field), dict):
                        track[field] = copy.deepcopy(candidates[index][field])
        result.append(track)
    return result


def _validate_api_url(url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc not in ("api.spotify.com", "api.spotify.com:443")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.path.startswith("/v1/")
    ):
        raise SpotifyError("Spotify devolvió una dirección de paginación no válida.")


def _check_cancelled(stop_event) -> None:
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("Spotify import cancelled; the previous catalogue was preserved")


class _CancelablePKCE(SpotifyPKCE):
    """Bounded loopback OAuth, usable from the web app's worker thread."""

    def __init__(self, *args, stop_event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.stop_event = stop_event

    def get_authorization_code(self, response=None):
        redirect = urlparse(self.redirect_uri)
        if redirect.scheme != "http" or redirect.hostname not in {"127.0.0.1", "::1"} or not redirect.port:
            raise SpotifyError("Local OAuth requires http://127.0.0.1:PORT/callback (or [::1]).")
        expected_state = secrets.token_urlsafe(32)
        self.state = expected_state
        outcome = {}
        expected_path = redirect.path or "/"

        class CallbackHandler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def log_message(self, _format, *args):
                pass  # Callback URLs contain authorization codes.

            def do_GET(self):
                request = urlparse(self.path)
                query = parse_qs(request.query)
                valid = (
                    request.path == expected_path
                    and len(query.get("state", [])) == 1
                    and hmac.compare_digest(query["state"][0].encode("utf-8"), expected_state.encode("utf-8"))
                    and (len(query.get("code", [])) == 1 or len(query.get("error", [])) == 1)
                )
                if valid:
                    if query.get("error"):
                        outcome["error"] = True
                    else:
                        outcome["code"] = query["code"][0]
                body = ("Authorization received. Return to MusicMatcher." if valid else
                        "Invalid authorization callback.").encode("utf-8")
                self.send_response(200 if valid else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(body)

        class LoopbackServer(HTTPServer):
            address_family = socket.AF_INET6 if redirect.hostname == "::1" else socket.AF_INET

        _check_cancelled(self.stop_event)
        try:
            with LoopbackServer((redirect.hostname, redirect.port), CallbackHandler) as server:
                server.timeout = 0.5
                auth_url = self.get_authorize_url(state=expected_state)
                try:
                    opened = webbrowser.open(auth_url)
                except webbrowser.Error as exc:
                    raise SpotifyError("Cannot open the authorization browser. Configure a default browser and retry.") from exc
                if not opened:
                    raise SpotifyError("Cannot open the authorization browser. Configure a default browser and retry.")
                deadline = time.monotonic() + AUTHORIZATION_TIMEOUT
                while not outcome:
                    _check_cancelled(self.stop_event)
                    if time.monotonic() >= deadline:
                        raise SpotifyError("Spotify authorization timed out after 180 seconds. Retry the import.")
                    server.handle_request()
        except OSError as exc:
            if isinstance(exc, InterruptedError):
                raise
            raise SpotifyError("Cannot listen on the Spotify callback port. Check the redirect URI and port availability.") from exc
        if outcome.get("error"):
            raise SpotifyError("Spotify authorization was declined. Retry if you wish to connect.")
        return outcome["code"]


class _SpotifyClient:
    def __init__(self, auth, session=None, sleep=None, stop_event=None):
        self.auth = auth
        self.session = session if session is not None else requests.Session()
        self.sleep = sleep if sleep is not None else time.sleep
        self.refreshed = False
        self.stop_event = stop_event
        _check_cancelled(stop_event)
        try:
            self.token = auth.get_access_token()
        except (SpotifyOauthError, requests.RequestException) as exc:
            raise SpotifyError("No se ha podido autorizar Spotify mediante PKCE.") from exc

    def close(self):
        self.session.close()

    def _refresh(self):
        if self.refreshed:
            raise SpotifyError("Spotify rechazó el token después de renovarlo (401).")
        self.refreshed = True
        cached = self.auth.cache_handler.get_cached_token() or {}
        if not cached.get("refresh_token"):
            raise SpotifyError("El token ha caducado y no hay token de renovación de Spotify.")
        try:
            self.token = self.auth.refresh_access_token(cached["refresh_token"])["access_token"]
        except (SpotifyOauthError, requests.RequestException, KeyError) as exc:
            raise SpotifyError("No se ha podido renovar la autorización de Spotify.") from exc

    def get(self, url: str, params=None) -> dict:
        _validate_api_url(url)
        waited = 0.0
        for attempt in range(MAX_ATTEMPTS):
            _check_cancelled(self.stop_event)
            response = None
            try:
                response = self.session.get(
                    url, params=params, headers={"Authorization": f"Bearer {self.token}"},
                    timeout=REQUEST_TIMEOUT, allow_redirects=False,
                )
            except requests.RequestException as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    raise SpotifyError("Error de conexión con Spotify tras varios intentos.") from exc
            if response is not None:
                status = response.status_code
                if status == 401:
                    self._refresh()
                    continue
                if status == 403:
                    raise SpotifyError(
                        "Spotify deniega el acceso (403). Comprueba Premium del propietario "
                        "de la app, usuarios autorizados y permisos. La playlist debe ser "
                        "propia o colaborativa con el endpoint /items actual."
                    )
                if status == 404:
                    raise SpotifyError("Spotify no encuentra la playlist o no permite acceder a ella (404).")
                if 200 <= status < 300:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise SpotifyError("Spotify devolvió una respuesta JSON no válida.") from exc
                    if not isinstance(payload, dict):
                        raise SpotifyError("Spotify devolvió una estructura de datos inesperada.")
                    return payload
                if status != 429 and not 500 <= status < 600:
                    raise SpotifyError(f"Spotify devolvió un error HTTP {status}.")
                if status == 429:
                    try:
                        error = response.json().get("error", {})
                    except (ValueError, AttributeError):
                        error = {}
                    if isinstance(error, dict) and error.get("reason") == "QUOTA_EXCEEDED":
                        raise SpotifyError(
                            "Cuota de Spotify agotada (QUOTA_EXCEEDED). El estado anterior "
                            "se conserva; vuelve a ejecutar la fase cuando se restablezca."
                        )
                if attempt == MAX_ATTEMPTS - 1:
                    raise SpotifyError(f"Spotify sigue devolviendo HTTP {status} tras varios intentos.")

            delay = min(2.0 ** attempt, 5.0)
            if response is not None and response.status_code == 429:
                try:
                    delay = float(response.headers.get("Retry-After", delay))
                except (TypeError, ValueError):
                    pass
                if not math.isfinite(delay) or delay < 0:
                    delay = min(2.0 ** attempt, 5.0)
            if waited + delay > MAX_RETRY_WAIT:
                raise SpotifyError(
                    "Spotify requiere una espera superior al límite de reintentos (30 s). "
                    "El estado se conserva; vuelve a ejecutar más tarde."
                )
            waited += delay
            LOGGER.warning("Reintentando Spotify en %.1f s (%s/%s).", delay, attempt + 1, MAX_ATTEMPTS)
            if self.stop_event is not None:
                if self.stop_event.wait(delay):
                    _check_cancelled(self.stop_event)
            else:
                self.sleep(delay)
        raise SpotifyError("No se ha podido completar la petición a Spotify.")


def _create_auth(state_path: Path, client_id: str | None = None, redirect_uri: str | None = None,
                 stop_event=None):
    client_id = (client_id if client_id is not None else os.getenv("SPOTIPY_CLIENT_ID", "")).strip()
    if not client_id:
        raise SpotifyError("Configura SPOTIPY_CLIENT_ID antes de extraer la playlist.")
    redirect_uri = redirect_uri if redirect_uri is not None else os.getenv(
        "SPOTIPY_REDIRECT_URI", "http://127.0.0.1:8888/callback"
    )
    parsed_redirect = urlparse(redirect_uri)
    if parsed_redirect.hostname == "localhost":
        raise SpotifyError("Spotify no admite localhost: usa http://127.0.0.1:8888/callback.")
    if (
        parsed_redirect.scheme != "http"
        or parsed_redirect.hostname not in {"127.0.0.1", "::1"}
        or not parsed_redirect.port
        or parsed_redirect.username is not None
        or parsed_redirect.password is not None
        or parsed_redirect.fragment
    ):
        raise SpotifyError("Local OAuth requires http://127.0.0.1:PORT/callback (or [::1]).")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    cache_suffix = hashlib.sha256(client_id.encode("utf-8")).hexdigest()[:12]
    return _CancelablePKCE(
        client_id=client_id, redirect_uri=redirect_uri, scope=SCOPES,
        requests_timeout=REQUEST_TIMEOUT,
        cache_handler=PrivateCacheHandler(cache_path=str(state_path.parent / f".spotify-cache-{cache_suffix}")),
        stop_event=stop_event,
    )


def extract_playlist(url: str, state_path: Path, refresh: bool = False, *,
                     client_id: str | None = None, redirect_uri: str | None = None,
                     progress: Callable[[dict], None] | None = None, stop_event=None) -> dict:
    """Extrae una versión consistente y publica el estado solo al terminar."""
    playlist_id = parse_playlist_id(url)
    _check_cancelled(stop_event)
    state_path = Path(state_path)
    previous = load_state(state_path) if state_path.exists() else {}
    previous_playlist = previous.get("playlist", {})
    if not isinstance(previous_playlist, dict):
        raise SpotifyError("Los metadatos de playlist del archivo de estado no son válidos.")
    if previous and previous_playlist.get("id") != playlist_id:
        raise SpotifyError("El archivo de estado pertenece a otra playlist; utiliza otro archivo.")
    if progress:
        progress({"stage": "import", "done": 0, "total": 0, "message": "Authorizing Spotify"})
    client = _SpotifyClient(_create_auth(state_path, client_id=client_id, redirect_uri=redirect_uri,
                                        stop_event=stop_event),
                            stop_event=stop_event)
    metadata_url = f"{API_BASE}/playlists/{playlist_id}"
    metadata_params = {"fields": "id,name,snapshot_id,external_urls,owner(id)"}
    try:
        metadata = client.get(metadata_url, params=metadata_params)
        snapshot = metadata.get("snapshot_id")
        if not isinstance(snapshot, str) or not snapshot:
            raise SpotifyError("Spotify no ha proporcionado snapshot_id; no se publicará una extracción incompleta.")
        if (
            not refresh and previous_playlist.get("snapshot_id") == snapshot
            and isinstance(previous.get("tracks"), list)
        ):
            LOGGER.info("La playlist no ha cambiado; se reutilizan sus metadatos.")
            # Migrate old state safely: Spotify metadata never becomes AI input.
            seen = {}
            for track in previous["tracks"]:
                identity = track.get("spotify_id") or _fingerprint(track)[:24]
                seen[identity] = seen.get(identity, 0) + 1
                track["track_id"] = f"spotify:{identity}:{seen[identity]}"
                track["source"] = "spotify"
                track["ai_allowed"] = False
            previous["playlist"]["source"] = "spotify"
            save_state(state_path, previous)
            if progress:
                progress({"stage": "import", "done": len(previous["tracks"]),
                          "total": len(previous["tracks"]), "message": "Reused unchanged Spotify playlist"})
            return previous

        items = []
        page_url = f"{metadata_url}/items"
        params = {"limit": 50, "additional_types": "track,episode"}
        visited = set()
        expected_total = None
        while page_url:
            _check_cancelled(stop_event)
            _validate_api_url(page_url)
            if page_url in visited:
                raise SpotifyError("Spotify devolvió una paginación circular; no se guardará la extracción.")
            visited.add(page_url)
            page = client.get(page_url, params=params)
            params = None  # `next` ya contiene su propia query y offset.
            if not isinstance(page.get("items"), list):
                raise SpotifyError("Spotify no devolvió los elementos de la playlist; comprueba el acceso.")
            if "total" in page:
                total = page["total"]
                if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                    raise SpotifyError("Spotify devolvió un total de elementos no válido.")
                if expected_total is not None and total != expected_total:
                    raise SpotifyError("El número de elementos cambió durante la extracción; vuelve a ejecutarla.")
                expected_total = total
            items.extend(page["items"])
            if progress:
                progress({"stage": "import", "done": len(items), "total": expected_total or len(items),
                          "message": f"Imported {len(items)} Spotify items"})
            page_url = page.get("next")
            if page_url is not None and not isinstance(page_url, str):
                raise SpotifyError("Spotify devolvió un enlace de paginación no válido.")

        if expected_total is not None and len(items) != expected_total:
            raise SpotifyError("La extracción está incompleta; no se ha modificado el estado anterior.")

        after = client.get(metadata_url, params=metadata_params)
        if after.get("snapshot_id") != snapshot:
            raise SpotifyError(
                "La playlist cambió durante la extracción. No se ha modificado el estado: "
                "vuelve a ejecutar esta fase."
            )
        state = copy.deepcopy(previous) if previous else {"schema_version": 1}
        external_urls = metadata.get("external_urls") or {}
        external_urls = external_urls if isinstance(external_urls, dict) else {}
        owner = metadata.get("owner") or {}
        owner = owner if isinstance(owner, dict) else {}
        state["playlist"] = {
            "id": playlist_id,
            "name": metadata.get("name", playlist_id),
            "url": external_urls.get("spotify", f"https://open.spotify.com/playlist/{playlist_id}"),
            "snapshot_id": snapshot,
            "owner_id": owner.get("id"),
            "extracted_at": utc_now(),
            "source": "spotify",
        }
        state["tracks"] = build_tracks(items, previous.get("tracks", []))
        _check_cancelled(stop_event)
        save_state(state_path, state)
        if progress:
            progress({"stage": "import", "done": len(items), "total": len(items),
                      "message": "Spotify playlist imported; AI processing disabled"})
        LOGGER.info("Extraídos %s elementos, conservando orden y duplicados.", len(items))
        return state
    finally:
        client.close()
