"""One local worker shared by the CLI and web UI; business logic stays in modules."""

from __future__ import annotations

from collections import Counter, deque
from copy import deepcopy
import json
import logging
from pathlib import Path
import re
import shutil
import threading
import time

from .config_manager import Settings, load_settings, save_settings, update_settings
from .mapping_manager import (
    atomic_json, canonical_youtube_url, export_results, load_state, save_state,
    state_lock, track_identity, utc_now,
)


class BusyError(RuntimeError):
    pass


def redact(message: object) -> str:
    value = str(message)
    value = re.sub(r"(?i)(access_token|refresh_token|client_secret|authorization|code)=([^&\s]+)",
                   r"\1=[redacted]", value)
    return re.sub(r"(?i)Bearer\s+\S+", "Bearer [redacted]", value)[:3000]


class _LiveHandler(logging.Handler):
    def __init__(self, owner: "Workspace"):
        super().__init__(logging.INFO)
        self.owner = owner

    def emit(self, record: logging.LogRecord) -> None:
        self.owner.record(redact(record.getMessage()), record.levelname.lower())


class Workspace:
    def __init__(self, data_dir: Path, settings: Settings | None = None):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state_path = self.data_dir / "estado.json"
        self.links_path = self.data_dir / "canciones.json"
        self.settings = settings or load_settings(self.data_dir)
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.logs: deque[dict] = deque(maxlen=100)
        self.log_id = 0
        self.started = 0.0
        self.baseline = 0
        self.job = {"status": "idle", "stage": "idle", "done": 0, "total": 0,
                    "percent": 0, "message": "Todo listo para importar tu música.", "eta_seconds": None,
                    "elapsed_seconds": 0, "current_title": "", "candidate_done": 0, "candidate_total": 0}
        job_path = self.data_dir / "job.json"
        if job_path.is_file():
            try:
                self.job.update(json.loads(job_path.read_text(encoding="utf-8")))
                if self.job["status"] == "running":
                    self.job.update(status="paused", message="La sesión anterior se interrumpió. Puedes reanudar.")
            except (OSError, ValueError, TypeError):
                pass
        self._handler = _LiveHandler(self)
        self._logger = logging.getLogger("musicmatcher")
        self._logger.setLevel(logging.INFO)
        self._logger.addHandler(self._handler)

    def close(self) -> None:
        self.stop_event.set()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=3)
        self._logger.removeHandler(self._handler)
        self._handler.close()

    def record(self, message: str, level: str = "info") -> None:
        with self.lock:
            self.log_id += 1
            item = {"id": self.log_id, "time": utc_now(), "level": level, "message": redact(message)}
            self.logs.append(item)
            with (self.data_dir / "events.log").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")

    def require_idle(self) -> None:
        if self.job["status"] == "running":
            raise BusyError("Hay un proceso en marcha. Páusalo antes de cambiar el catálogo o la configuración.")

    def configure(self, changes: dict) -> dict:
        with self.lock:
            self.require_idle()
            updated = update_settings(self.settings, changes)
            destination = Path(updated.output_dir).resolve()
            if destination == self.data_dir or self.data_dir.is_relative_to(destination):
                raise ValueError("Selecciona una carpeta de audio distinta de la carpeta de trabajo.")
            self.settings = updated
            save_settings(self.data_dir, self.settings)
            return self.settings.public()

    def state(self) -> dict:
        try:
            return load_state(self.state_path)
        except FileNotFoundError:
            return {"schema_version": 1, "playlist": {}, "tracks": []}

    def snapshot(self) -> dict:
        state = self.state()
        tracks = state["tracks"]
        with self.lock:
            job = deepcopy(self.job)
            logs = list(self.logs)
        if job["status"] == "running" and self.started:
            job["elapsed_seconds"] = max(0, round(time.monotonic() - self.started, 1))
        done = int(job.get("done") or 0)
        total = int(job.get("total") or 0)
        job["percent"] = min(100, round(100 * done / total, 1)) if total else 0
        newly_done = max(0, done - self.baseline)
        if job["status"] == "running" and newly_done >= 2 and total > done:
            job["eta_seconds"] = round(job["elapsed_seconds"] / newly_done * (total - done))
        elif total and done >= total:
            job["eta_seconds"] = 0
        return {
            "job": job,
            "catalog": {"total": len(tracks), "name": state.get("playlist", {}).get("name", "Mi biblioteca"),
                        "counts": dict(Counter(t.get("match", {}).get("status", "pending") for t in tracks)),
                        "download_counts": dict(Counter(t.get("download", {}).get("status", "pending") for t in tracks)),
                        "updated_at": state.get("updated_at")},
            "logs": logs[-50:],
            "dependencies": {name: bool(shutil.which(name)) for name in ("ffmpeg", "ffprobe", "deno")},
        }

    def progress(self, event: dict) -> None:
        with self.lock:
            previous_stage = self.job.get("stage")
            for name in ("stage", "done", "total", "message", "current_title", "candidate_done", "candidate_total",
                         "candidate_limit", "download_percent"):
                if name in event:
                    self.job[name] = redact(event[name]) if isinstance(event[name], str) else event[name]
            if previous_stage != self.job.get("stage"):
                self.started = time.monotonic()
                self.baseline = int(event.get("done") or 0)
            self.job["elapsed_seconds"] = round(time.monotonic() - self.started, 1) if self.started else 0
            atomic_json(self.data_dir / "job.json", self.job)

    def start(self, request: dict) -> dict:
        action = request.get("action")
        if action not in {"import_spotify", "import_file", "search", "download", "run"}:
            raise ValueError("Acción desconocida.")
        with self.lock:
            self.require_idle()
            self.stop_event.clear()
            self.started = time.monotonic()
            state = self.state()
            selected_tracks = state["tracks"][:request["track_limit"]] if request.get("track_limit") else state["tracks"]
            if action in {"search", "download", "run"} and not state["tracks"] and not (
                request.get("spotify_url") or request.get("content") is not None
            ):
                raise ValueError("Importa primero una playlist de Spotify o un archivo CSV/JSON.")
            stage = "import" if action.startswith("import") else "search" if action in {"search", "run"} else "download"
            self.baseline = (sum(t.get("match", {}).get("status") in {"accepted", "review", "unmatched", "unavailable"}
                                 for t in selected_tracks) if stage == "search" else 0)
            self.job.update(status="running", stage=stage, done=self.baseline, total=len(selected_tracks),
                            message="Preparando el proceso…", current_title="", started_at=utc_now(),
                            elapsed_seconds=0, eta_seconds=None, error=None, candidate_done=0, candidate_total=0,
                            candidate_limit=self.settings.candidate_limit,
                            track_limit=request.get("track_limit"), finished_at=None)
            atomic_json(self.data_dir / "job.json", self.job)
            config = self.settings.model_copy(deep=True)
            self.thread = threading.Thread(target=self._work, args=(deepcopy(request), config), daemon=True,
                                           name="musicmatcher-worker")
            self.thread.start()
            return deepcopy(self.job)

    def stop(self) -> dict:
        self.stop_event.set()
        with self.lock:
            if self.job["status"] == "running":
                self.job["message"] = "Pausando y guardando el progreso…"
            return deepcopy(self.job)

    def _work(self, request: dict, settings: Settings) -> None:
        try:
            with state_lock(self.state_path):
                action = request["action"]
                if action == "import_spotify" or (action == "run" and request.get("spotify_url")):
                    from .spotify_importer import extract_playlist
                    self.progress({"stage": "import", "done": 0, "total": 0,
                                   "message": "Autoriza Spotify en el navegador que se abrirá."})
                    extract_playlist(request.get("spotify_url", ""), self.state_path,
                                     client_id=settings.spotify_client_id, redirect_uri=settings.spotify_redirect_uri,
                                     progress=self.progress, stop_event=self.stop_event)
                elif action == "import_file" or (action == "run" and request.get("content") is not None):
                    from .playlist_importer import import_text
                    import_text(request.get("content", ""), request.get("filename", "playlist.csv"), self.state_path,
                                replace=bool(request.get("replace")), progress=self.progress, stop_event=self.stop_event)
                if self.stop_event.is_set():
                    raise InterruptedError("Proceso pausado.")
                if action in {"search", "run"}:
                    from .youtube_search_agent import search_tracks
                    state = search_tracks(self.state_path, settings=settings, progress=self.progress,
                                          stop_event=self.stop_event, refresh=bool(request.get("refresh")),
                                          **({"track_limit": request["track_limit"]} if request.get("track_limit") else {}))
                    if state.get("search_summary", {}).get("paused"):
                        self.stop_event.set()
                if action in {"download", "run"} and not self.stop_event.is_set():
                    from .audio_downloader import download_tracks
                    export_results(self.state_path)
                    download_tracks(self.state_path, self.links_path, Path(settings.output_dir), settings.bitrate,
                                    settings.ffmpeg_location, progress=self.progress, stop_event=self.stop_event,
                                    **({"track_limit": request["track_limit"]} if request.get("track_limit") else {}))
                if self.state_path.is_file():
                    export_results(self.state_path)
            with self.lock:
                paused = self.stop_event.is_set()
                message = "Proceso terminado. Revisa las canciones pendientes cuando quieras."
                if not paused and action in {"download", "run"}:
                    selected = self.state()["tracks"]
                    if request.get("track_limit"):
                        selected = selected[:request["track_limit"]]
                    missing = sum(t.get("download", {}).get("status") == "authorization_required" for t in selected)
                    if missing:
                        message = f"Búsqueda terminada. {missing} canciones necesitan permiso de descarga."
                self.job.update(status="paused" if paused else "completed",
                                message="Proceso pausado. Puedes reanudar sin perder resultados." if paused else
                                        message,
                                finished_at=utc_now())
                if not paused and action.startswith("import"):
                    total = len(self.state()["tracks"])
                    self.job.update(done=total, total=total)
        except (InterruptedError, KeyboardInterrupt):
            with self.lock:
                self.job.update(status="paused", message="Proceso pausado. El trabajo guardado se conserva.")
        except Exception as error:
            with self.lock:
                self.job.update(status="error", error=redact(error), message=redact(error))
            self.record(redact(error), "error")
        finally:
            with self.lock:
                self.job["elapsed_seconds"] = round(time.monotonic() - self.started, 1)
                atomic_json(self.data_dir / "job.json", self.job)

    def tracks(self, status: str = "all", query: str = "", offset: int = 0, limit: int = 25) -> dict:
        tracks = self.state()["tracks"]
        query = query.casefold().strip()
        selected = [t for t in tracks if (status == "all" or t.get("match", {}).get("status") == status)
                    and (not query or query in (t["title"] + " " + " ".join(t.get("artists", []))).casefold())]
        return {"tracks": selected[offset:offset + limit], "total": len(selected), "offset": offset, "limit": limit}

    def accept(self, key: str, url: str, note: str = "Coincidencia revisada manualmente", *,
               authorize_audio: bool = False, authorization_note: str = "", cover_path: str | None = None) -> None:
        url = canonical_youtube_url(url)
        if authorize_audio and not authorization_note.strip():
            raise ValueError("Indica el permiso o la procedencia que autoriza la descarga.")
        with self.lock:
            self.require_idle()
            with state_lock(self.state_path):
                state = self.state()
                selected = next((t for t in state["tracks"] if t["key"] == key), None)
                if not selected:
                    raise ValueError("La canción no existe en este catálogo.")
                if selected.get("unavailable_reason"):
                    raise ValueError("Este elemento de origen no está disponible.")
                for track in state["tracks"]:
                    if track_identity(track) != track_identity(selected):
                        continue
                    if track.get("match", {}).get("url") != url:
                        track["authorization"] = {}
                        track["download"] = {"status": "pending"}
                    track["match"] = {**track.get("match", {}), "status": "accepted", "url": url,
                                      "manual": True, "decision": "manual", "note": note, "decided_at": utc_now()}
                    if authorize_audio:
                        track["authorization"] = self._permission(url, authorization_note, cover_path)
                save_state(self.state_path, state)
                export_results(self.state_path)

    @staticmethod
    def _permission(url: str, note: str, cover_path: str | None = None) -> dict:
        if not note.strip():
            raise ValueError("Indica el permiso o la procedencia de la autorización.")
        permission = {"audio": True, "url": url, "note": note.strip(), "recorded_at": utc_now()}
        if cover_path:
            path = Path(cover_path).expanduser().resolve()
            if not path.is_file():
                raise ValueError("No existe la carátula local indicada.")
            permission["cover_path"] = str(path)
        return permission

    def authorize(self, key: str, note: str, cover_path: str | None = None) -> None:
        with self.lock:
            self.require_idle()
            with state_lock(self.state_path):
                state = self.state()
                selected = next((t for t in state["tracks"] if t["key"] == key), None)
                if not selected or selected.get("match", {}).get("status") != "accepted":
                    raise ValueError("Selecciona primero una coincidencia aceptada.")
                url = canonical_youtube_url(selected["match"]["url"])
                permission = self._permission(url, note, cover_path)
                for track in state["tracks"]:
                    if track.get("match", {}).get("url") == url and track["match"].get("status") == "accepted":
                        track["authorization"] = deepcopy(permission)
                save_state(self.state_path, state)

    def clear_catalog(self, spotify_only: bool = False) -> None:
        with self.lock:
            self.require_idle()
            if spotify_only:
                for path in self.data_dir.glob(".spotify-cache*"):
                    if path.is_file():
                        path.unlink()
                state = self.state()
                if not any(t.get("source") == "spotify" or t.get("spotify_id") for t in state["tracks"]):
                    return
                remaining = [t for t in state["tracks"] if t.get("source") != "spotify" and not t.get("spotify_id")]
                if remaining:
                    with state_lock(self.state_path):
                        state["tracks"] = remaining
                        state["playlist"] = {"name": "Biblioteca local", "source": "file"}
                        keys = {t["key"] for t in remaining}
                        for track in remaining:
                            if track.get("duplicate_of") not in keys:
                                track["duplicate_of"] = None
                        save_state(self.state_path, state)
                        export_results(self.state_path)
                        (self.data_dir / "events.log").unlink(missing_ok=True)
                        self.logs.clear()
                        self.job.update(status="idle", stage="idle", done=0, total=0, current_title="", percent=0,
                                        message="Spotify desconectado. Se conservan las canciones de otros orígenes.")
                    return
            with state_lock(self.state_path):
                for name in ("estado.json", "mapping.json", "canciones.json", "revision.json", "events.log", "job.json"):
                    (self.data_dir / name).unlink(missing_ok=True)
                self.logs.clear()
                self.job.update(status="idle", stage="idle", done=0, total=0, current_title="", percent=0,
                                message="Catálogo eliminado. Puedes importar una lista nueva.")
