"""Descarga aceptada, autorizada y reanudable, independiente del buscador."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import re
import tempfile

from yt_dlp import YoutubeDL

from .mapping_manager import canonical_youtube_url, load_state, save_state, utc_now
from .metadata_manager import MetadataManager, check_ffmpeg, cover_information


LOG = logging.getLogger(__name__)
BITRATES = (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)


class DownloadError(RuntimeError):
    pass


class DownloadStopped(InterruptedError):
    """Cancelación cooperativa desde los hooks de yt-dlp."""


def _stopped(stop_event) -> bool:
    return stop_event is not None and stop_event.is_set()


def _notify(progress, done: int, total: int, message: str, title: str = "",
            percent: float | None = None) -> None:
    if progress is not None:
        progress({"stage": "download", "done": done, "total": total, "message": message,
                  "current_title": title, "download_percent": percent})


def _fingerprint(track: dict, url: str, bitrate: int, cover_data: bytes | None = None) -> str:
    fields = {name: track.get(name) for name in (
        "spotify_id", "title", "artists", "album", "duration_ms", "explicit", "isrc"
    )}
    fields.update(url=url, bitrate=bitrate,
                  cover_sha256=hashlib.sha256(cover_data).hexdigest() if cover_data else None)
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def output_filename(track: dict, fingerprint: str, bitrate: int) -> str:
    label = f"{track['title']} - {', '.join(track.get('artists', []))}"
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label)
    label = re.sub(r"\s+", " ", label).strip(" .") or "Sin título"
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", label, re.I):
        label = "_" + label
    suffix = f" [{fingerprint[:12]}] [{bitrate}k].mp3"
    remaining = 180 - len(suffix.encode("utf-8"))
    return label.encode("utf-8")[:remaining].decode("utf-8", errors="ignore").rstrip(" .") + suffix


class _YTDLPLogger:
    def debug(self, message):
        LOG.debug("yt-dlp: %s", message)

    def warning(self, message):
        LOG.warning("yt-dlp: %s", message)

    def error(self, message):
        LOG.error("yt-dlp: %s", message)


def _progress_hook(track: dict, done: int, total: int, progress, stop_event):
    last_bucket = -1

    def hook(event: dict) -> None:
        nonlocal last_bucket
        if _stopped(stop_event):
            raise DownloadStopped("Descarga detenida; se conservan los archivos parciales.")
        status = event.get("status")
        if status == "finished":
            _notify(progress, done, total, "Audio recibido; convirtiendo…", track["title"], 100.0)
            return
        size = event.get("total_bytes") or event.get("total_bytes_estimate")
        if status == "downloading":
            percent = min(100.0, event.get("downloaded_bytes", 0) * 100 / size) if size else None
            _notify(progress, done, total, "Descargando audio…", track["title"], percent)
            if percent is not None and int(percent // 10) > last_bucket:
                last_bucket = int(percent // 10)
                LOG.info("%s: %.0f %%", track["title"], percent)
    return hook


def _download_audio(url: str, stage: Path, track: dict, bitrate: int,
                    ffmpeg_location: str | None, *, progress=None, stop_event=None,
                    done=0, total=0) -> Path:
    if _stopped(stop_event):
        raise DownloadStopped("Operación detenida.")
    path = stage / "audio.mp3"
    if path.exists():
        # El validador final comprueba audio, cabecera y duración antes de publicarlo.
        if path.stat().st_size:
            return path
        raise DownloadError(f"El MP3 temporal está vacío: {path}. Retíralo antes de reintentar.")
    template = str(stage / "audio").replace("%", "%%") + ".%(ext)s"
    options = {
        "format": "bestaudio/best", "noplaylist": True, "outtmpl": template,
        "continuedl": True, "overwrites": False, "nopart": False,
        "retries": 3, "fragment_retries": 3, "extractor_retries": 2,
        "file_access_retries": 2, "socket_timeout": 20,
        "concurrent_fragment_downloads": 1,
        "retry_sleep_functions": {"http": lambda n: min(2 ** n, 30),
                                  "fragment": lambda n: min(2 ** n, 30)},
        "quiet": True, "noprogress": True, "logger": _YTDLPLogger(),
        "progress_hooks": [_progress_hook(track, done, total, progress, stop_event)],
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                            "preferredquality": str(bitrate)}],
    }
    if ffmpeg_location:
        options["ffmpeg_location"] = str(ffmpeg_location)
    with YoutubeDL(options) as ydl:
        ydl.extract_info(url, download=True)
    if _stopped(stop_event):
        raise DownloadStopped("Operación detenida.")
    if not path.is_file():
        raise DownloadError("FFmpeg no ha generado el MP3 esperado.")
    return path


def _write_playlist(output_dir: Path, completed: list[tuple[int, int, dict, Path]]) -> None:
    if not completed and not (output_dir / "playlist.m3u8").is_file():
        return
    lines = ["#EXTM3U"]
    for _, _, track, audio in sorted(completed, key=lambda row: row[:2]):
        title = re.sub(r"[\r\n]+", " ", f"{track['title']} - {', '.join(track.get('artists', []))}")
        ms = track.get("duration_ms")
        lines.extend((f"#EXTINF:{round(ms / 1000) if ms else -1},{title}", "./" + audio.name))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=output_dir,
                                         prefix=".playlist-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write("\n".join(lines) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output_dir / "playlist.m3u8")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def download_tracks(state_path: Path, links_path: Path, output_dir: Path, bitrate: int = 320,
                    ffmpeg_location: str | None = None, *, progress=None, stop_event=None,
                    track_limit: int | None = None) -> dict:
    if bitrate not in BITRATES:
        raise ValueError("Bitrate MP3 no admitido.")
    state_path, links_path = Path(state_path), Path(links_path)
    output_dir = Path(output_dir).expanduser().resolve()
    state = load_state(state_path)
    if track_limit is not None and (isinstance(track_limit, bool) or not isinstance(track_limit, int) or track_limit < 1):
        raise ValueError("El límite de canciones debe ser un entero positivo.")
    tracks = state["tracks"][:track_limit] if track_limit is not None else state["tracks"]
    with links_path.open(encoding="utf-8-sig") as stream:
        links = json.load(stream)
    if not isinstance(links, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                          for k, v in links.items()):
        raise ValueError("Se esperaba un diccionario de claves y URL de texto.")
    unknown = set(links) - {track["key"] for track in state["tracks"]}
    if unknown:
        raise ValueError("Hay claves sin metadatos en el estado: " + ", ".join(sorted(unknown)))
    metadata = MetadataManager(ffmpeg_location)
    checked = False
    completed = []
    total = sum(track["key"] in links for track in tracks)
    done = 0
    interrupted = False
    _notify(progress, done, total, "Preparando descarga…")
    for index, track in enumerate(tracks):
        if _stopped(stop_event):
            interrupted = True
            break
        key = track["key"]
        if key not in links:
            continue
        try:
            url = canonical_youtube_url(links[key])
            match = track.get("match", {})
            if match.get("status") != "accepted" or canonical_youtube_url(match.get("url", "")) != url:
                raise DownloadError("El enlace no corresponde a una coincidencia aceptada.")
            authorization = track.get("authorization", {})
            if (authorization.get("audio") is not True or not authorization.get("note", "").strip()
                    or authorization.get("url") != url):
                track["download"] = {"status": "authorization_required", "url": url,
                                     "updated_at": utc_now()}
                save_state(state_path, state)
                done += 1
                _notify(progress, done, total, "Falta autorización de descarga.", track["title"])
                continue
            cover_path = None
            if authorization.get("cover_path"):
                cover_path = Path(authorization["cover_path"]).expanduser()
                if not cover_path.is_absolute():
                    cover_path = state_path.resolve().parent / cover_path
            cover_data, _ = cover_information(cover_path)
            fingerprint = _fingerprint(track, url, bitrate, cover_data)
            destination = output_dir / output_filename(track, fingerprint, bitrate)
            result = {"fingerprint": fingerprint, "url": url, "bitrate": bitrate, "file": str(destination)}
            if destination.exists():
                metadata.validate(destination, track, fingerprint, url, bitrate, stop_event=stop_event)
                track["download"] = {**result, "status": "completed", "reused": True,
                                     "completed_at": utc_now()}
                completed.append((track.get("position", index + 1), index, track, destination))
                save_state(state_path, state)
                done += 1
                _notify(progress, done, total, "MP3 existente comprobado.", track["title"], 100.0)
                continue
            if not checked:
                check_ffmpeg(ffmpeg_location)
                checked = True
            stage = output_dir / ".partial" / fingerprint
            stage.mkdir(parents=True, exist_ok=True)
            track["download"] = {**result, "status": "downloading", "updated_at": utc_now()}
            save_state(state_path, state)
            path = _download_audio(url, stage, track, bitrate, ffmpeg_location, progress=progress,
                                   stop_event=stop_event, done=done, total=total)
            metadata.write(path, track, fingerprint, url, bitrate, cover_path, stop_event=stop_event)
            metadata.validate(path, track, fingerprint, url, bitrate, stop_event=stop_event)
            if _stopped(stop_event):
                raise DownloadStopped("Operación detenida.")
            if destination.exists():
                metadata.validate(destination, track, fingerprint, url, bitrate, stop_event=stop_event)
            else:
                os.replace(path, destination)
            track["download"] = {**result, "status": "completed", "reused": False,
                                 "completed_at": utc_now()}
            completed.append((track.get("position", index + 1), index, track, destination))
            save_state(state_path, state)
            done += 1
            _notify(progress, done, total, "MP3 terminado.", track["title"], 100.0)
            try:
                stage.rmdir()
            except OSError:
                pass
        except (InterruptedError, KeyboardInterrupt):
            interrupted = True
            track["download"] = {**track.get("download", {}), "status": "interrupted",
                                 "updated_at": utc_now()}
            save_state(state_path, state)
            break
        except Exception as exc:
            if _stopped(stop_event):
                interrupted = True
                track["download"] = {**track.get("download", {}), "status": "interrupted",
                                     "updated_at": utc_now()}
                save_state(state_path, state)
                break
            track["download"] = {**track.get("download", {}), "status": "error", "error": str(exc),
                                 "updated_at": utc_now()}
            save_state(state_path, state)
            LOG.error("%s: %s", key, exc)
            done += 1
            _notify(progress, done, total, f"Error: {exc}", track["title"])
    _write_playlist(output_dir, completed)
    state["last_download"] = {"status": "interrupted" if interrupted else "finished",
                              "done": done, "total": total, "updated_at": utc_now()}
    save_state(state_path, state)
    _notify(progress, done, total, "Descarga detenida." if interrupted else "Fase de descarga terminada.")
    return state
