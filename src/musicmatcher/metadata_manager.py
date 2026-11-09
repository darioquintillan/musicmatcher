"""ID3 y comprobación de MP3 mediante ejecutables externos FFmpeg/ffprobe.

No requiere bibliotecas de etiquetado ni distribuye binarios multimedia.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any


ARTIST_SEPARATOR = "; "
MAX_COVER_BYTES = 15 * 1024 * 1024


class MetadataError(RuntimeError):
    """El archivo multimedia o sus metadatos no superan la comprobación."""


def find_binary(name: str, ffmpeg_location: str | None = None) -> str | None:
    if not ffmpeg_location:
        return shutil.which(name)
    location = Path(ffmpeg_location).expanduser()
    directory = location if location.is_dir() else location.parent
    return shutil.which(name, path=str(directory))


def check_ffmpeg(ffmpeg_location: str | None = None) -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if not find_binary(name, ffmpeg_location)]
    if missing:
        raise MetadataError("Faltan ejecutables externos: " + ", ".join(missing)
                            + ". Instala FFmpeg o indica su carpeta.")


def cover_information(path: Path | None) -> tuple[bytes | None, str | None]:
    """Comprueba una portada local que el usuario haya autorizado para este uso."""
    if path is None:
        return None, None
    path = Path(path)
    if path.stat().st_size > MAX_COVER_BYTES:
        raise MetadataError("La carátula supera el límite de 15 MiB.")
    data = path.read_bytes()
    if data.startswith(b"\xff\xd8\xff"):
        return data, "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data, "image/png"
    raise MetadataError("La carátula autorizada debe ser JPEG o PNG.")


def _run(arguments: list[str], *, stop_event=None, timeout: float = 300) -> str:
    """Ejecuta argumentos separados, sin shell, y permite cancelación cooperativa."""
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("Operación detenida.")
    options: dict[str, Any] = {
        "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
        "stdin": subprocess.DEVNULL,
        "text": True, "encoding": "utf-8", "errors": "replace",
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    process = subprocess.Popen(arguments, **options)
    started = time.monotonic()
    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("Operación detenida.")
            if time.monotonic() - started > timeout:
                raise MetadataError("El proceso multimedia ha superado el tiempo límite.")
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            message = stderr.strip()[-2000:] or f"Código de salida {process.returncode}"
            raise MetadataError(message)
        return stdout
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


def check_declared_audio_size(path: Path) -> None:
    """Detecta MP3 truncados aunque Xing mantenga la duración original.

    Lee únicamente ID3 y la primera cabecera MPEG; no depende de APIs privadas
    ni confía en la duración declarada para saber cuántos bytes hay presentes.
    """
    with Path(path).open("rb") as stream:
        start = stream.read(10)
        offset = 0
        if start[:3] == b"ID3":
            if len(start) != 10 or any(byte & 0x80 for byte in start[6:10]):
                raise MetadataError("Cabecera ID3 no válida.")
            size = 0
            for byte in start[6:10]:
                size = (size << 7) | byte
            offset = 10 + size
        stream.seek(offset)
        beginning = stream.read(4100)
        frame_offset = None
        header = None
        for index in range(max(0, len(beginning) - 3)):
            candidate = beginning[index:index + 4]
            if (candidate[0] == 0xFF and candidate[1] & 0xE0 == 0xE0
                    and (candidate[1] >> 3) & 0x03 != 1
                    and (candidate[1] >> 1) & 0x03 == 1
                    and (candidate[2] >> 4) not in (0, 15)
                    and (candidate[2] >> 2) & 0x03 != 3):
                frame_offset, header = offset + index, candidate
                break
        if frame_offset is None:
            raise MetadataError("No se encontró una cabecera MPEG Layer III válida.")
        mono = header[3] >> 6 == 3
        mpeg1 = (header[1] >> 3) & 0x03 == 3
        xing_offset = (21 if mono else 36) if mpeg1 else (13 if mono else 21)
        stream.seek(frame_offset + xing_offset)
        xing = stream.read(16)
        if len(xing) < 12 or xing[:4] not in (b"Xing", b"Info"):
            return
        flags = int.from_bytes(xing[4:8], "big")
        if not flags & 0x02:
            return
        size_offset = 12 if flags & 0x01 else 8
        if len(xing) < size_offset + 4:
            raise MetadataError("Cabecera Xing/Info truncada.")
        declared = int.from_bytes(xing[size_offset:size_offset + 4], "big")
        available = Path(path).stat().st_size - frame_offset
        if available >= 128:
            stream.seek(-128, os.SEEK_END)
            if stream.read(3) == b"TAG":
                available -= 128
        if declared > available:
            raise MetadataError("Audio truncado: faltan frames MPEG declarados en Xing/Info.")


class MetadataManager:
    def __init__(self, ffmpeg_location: str | None = None):
        self.ffmpeg_location = ffmpeg_location

    def _binary(self, name: str) -> str:
        binary = find_binary(name, self.ffmpeg_location)
        if not binary:
            raise MetadataError(f"No se encuentra el ejecutable externo {name}.")
        return binary

    def probe(self, path: Path, *, stop_event=None) -> dict:
        raw = _run([self._binary("ffprobe"), "-v", "error", "-show_format", "-show_streams",
                    "-of", "json", str(Path(path).resolve())], stop_event=stop_event, timeout=60)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MetadataError("ffprobe no devolvió información JSON válida.") from exc

    def write(self, path: Path, track: dict, fingerprint: str, url: str, bitrate: int,
              cover_path: Path | None = None, *, stop_event=None) -> None:
        path = Path(path).resolve()
        temporary = path.with_name(path.stem + ".tagged.mp3")
        # Remuxear un archivo cortado reescribiría Xing y ocultaría el truncamiento.
        check_declared_audio_size(path)
        cover_information(cover_path)
        arguments = [self._binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                     "-i", str(path)]
        if cover_path is not None:
            arguments.extend(("-i", str(Path(cover_path).resolve())))
        arguments.extend(("-map", "0:a:0"))
        if cover_path is not None:
            arguments.extend(("-map", "1:v:0", "-c:v", "copy", "-disposition:v:0", "attached_pic"))
        arguments.extend(("-c:a", "copy", "-map_metadata", "-1", "-id3v2_version", "4"))
        artists = track.get("artists", [])
        tags = {
            "title": track["title"], "artist": ARTIST_SEPARATOR.join(artists),
            "album": track.get("album") or "", "musicmatcher_fingerprint": fingerprint,
            "musicmatcher_bitrate": str(bitrate), "musicmatcher_youtube_url": url,
            "musicmatcher_artists_json": json.dumps(artists, ensure_ascii=False),
        }
        source_url = track.get("spotify_url") or track.get("source_url")
        if isinstance(source_url, str):
            tags["musicmatcher_source_url"] = source_url
        for name, value in tags.items():
            arguments.extend(("-metadata", f"{name}={value}"))
        arguments.append(str(temporary))
        try:
            _run(arguments, stop_event=stop_event)
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("Operación detenida.")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def validate(self, path: Path, track: dict, fingerprint: str, url: str, bitrate: int,
                 *, stop_event=None) -> dict:
        path = Path(path)
        if not path.is_file() or path.stat().st_size <= 0:
            raise MetadataError("No existe un MP3 con contenido.")
        check_declared_audio_size(path)
        probe = self.probe(path, stop_event=stop_event)
        streams = [s for s in probe.get("streams", []) if s.get("codec_type") == "audio"]
        if not streams or streams[0].get("codec_name") != "mp3":
            raise MetadataError("El archivo no contiene audio MP3.")
        try:
            duration = float(probe.get("format", {}).get("duration", 0))
            sample_rate = int(streams[0].get("sample_rate", 0))
        except (TypeError, ValueError) as exc:
            raise MetadataError("Información de audio inválida.") from exc
        if not math.isfinite(duration) or duration <= 0 or sample_rate <= 0:
            raise MetadataError("El archivo no tiene duración o frecuencia de audio válidas.")
        expected_ms = track.get("duration_ms")
        if expected_ms and expected_ms > 0:
            expected = expected_ms / 1000
            flexible = track.get("match", {}).get("matching_mode") == "relaxed"
            tolerance = max(12, expected * 0.05) if flexible else max(5, expected * 0.02)
            if abs(duration - expected) > tolerance:
                raise MetadataError("Duración incompatible: el MP3 podría estar incompleto.")
        tags = {name.lower(): value for name, value in probe.get("format", {}).get("tags", {}).items()}
        artists = track.get("artists", [])
        required = {
            "title": track["title"], "musicmatcher_fingerprint": fingerprint,
            "musicmatcher_bitrate": str(bitrate), "musicmatcher_youtube_url": url,
            "musicmatcher_artists_json": json.dumps(artists, ensure_ascii=False),
        }
        if artists:
            required["artist"] = ARTIST_SEPARATOR.join(artists)
        if track.get("album"):
            required["album"] = track["album"]
        for name, expected in required.items():
            if tags.get(name) != expected:
                raise MetadataError(f"Metadato ausente o diferente: {name}.")
        return probe
