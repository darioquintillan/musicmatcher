"""Loopback-only web UI. Server-sent events stream real worker progress."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import secrets
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config_manager import default_data_dir
from .mapping_manager import build_mapping, canonical_youtube_url
from .service import BusyError, Workspace, redact


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["import_spotify", "import_file", "search", "download", "run"]
    spotify_url: str | None = Field(default=None, max_length=2000)
    content: str | None = Field(default=None, max_length=5_000_000)
    filename: str = Field(default="playlist.csv", max_length=255)
    replace: bool = False
    refresh: bool = False


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=5000)
    url: str = Field(min_length=1, max_length=2000)
    note: str = Field(default="Coincidencia revisada manualmente", max_length=2000)
    authorize_audio: bool = False
    authorization_note: str = Field(default="", max_length=2000)
    cover_path: str | None = Field(default=None, max_length=4096)


class AuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=5000)
    note: str = Field(min_length=1, max_length=2000)
    cover_path: str | None = Field(default=None, max_length=4096)


def create_app(data_dir: Path | None = None, workspace: Workspace | None = None) -> FastAPI:
    owner = workspace or Workspace(data_dir or default_data_dir())
    token = secrets.token_urlsafe(32)
    static = Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(_app):
        yield
        owner.close()

    app = FastAPI(title="MusicMatcher", version="0.2.0", lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.workspace = owner
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            supplied = request.headers.get("X-MM-Token", "")
            if not secrets.compare_digest(supplied, token):
                return JSONResponse({"detail": "Sesión no válida. Recarga la página."}, status_code=403)
            origin = request.headers.get("origin")
            if origin:
                parsed = urlparse(origin)
                expected = urlparse(str(request.base_url))
                if (parsed.scheme, parsed.hostname, parsed.port) != (expected.scheme, expected.hostname, expected.port):
                    return JSONResponse({"detail": "Origen de petición no permitido."}, status_code=403)
            try:
                if int(request.headers.get("content-length", "0")) > 6_000_000:
                    return JSONResponse({"detail": "El archivo supera el límite de 5 MB."}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Longitud de petición no válida."}, status_code=400)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        return response

    @app.exception_handler(BusyError)
    async def handle_busy(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(ValueError)
    async def handle_invalid(_request, error):
        return JSONResponse({"detail": redact(error)}, status_code=400)

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    @app.get("/api/bootstrap")
    def bootstrap():
        return {"csrf_token": token, "config": owner.settings.public(), "snapshot": owner.snapshot()}

    @app.get("/api/status")
    def status():
        return owner.snapshot()

    @app.get("/api/events")
    async def events(request: Request):
        async def stream():
            previous = None
            heartbeat = 0
            while not await request.is_disconnected():
                # Disk reads stay off the server's event loop.
                snapshot = await asyncio.to_thread(owner.snapshot)
                data = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
                if data != previous:
                    yield f"event: snapshot\ndata: {data}\n\n"
                    previous = data
                elif heartbeat % 20 == 0:
                    yield ": heartbeat\n\n"
                heartbeat += 1
                await asyncio.sleep(0.75)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

    @app.get("/api/tracks")
    def tracks(status: str = "all", q: str = Query(default="", max_length=2000),
               offset: int = Query(default=0, ge=0), limit: int = Query(default=25, ge=1, le=100)):
        return owner.tracks(status, q, offset, limit)

    @app.post("/api/config")
    def configure(changes: dict):
        try:
            return {"config": owner.configure(changes)}
        except ValidationError:
            raise HTTPException(422, "Revisa la configuración: candidatos entre 1 y 25, carpeta y URI válidos.")

    @app.post("/api/jobs", status_code=202)
    def start(request: JobRequest):
        return {"job": owner.start(request.model_dump(exclude_none=True))}

    @app.post("/api/jobs/stop")
    def stop():
        return {"job": owner.stop()}

    @app.post("/api/review")
    def review(request: ReviewRequest):
        owner.accept(**request.model_dump())
        return {"ok": True, "snapshot": owner.snapshot()}

    @app.post("/api/authorize")
    def authorize(request: AuthorizationRequest):
        owner.authorize(**request.model_dump())
        return {"ok": True}

    @app.get("/api/export/{kind}")
    def export(kind: Literal["mapping", "links"]):
        state = owner.state()
        if kind == "mapping":
            content = build_mapping(state)
        else:
            content = {t["key"]: canonical_youtube_url(t["match"]["url"]) for t in state["tracks"]
                       if t.get("match", {}).get("status") == "accepted"}
        return JSONResponse(content, headers={"Content-Disposition": f'attachment; filename="{kind}.json"'})

    @app.delete("/api/catalog")
    def clear():
        owner.clear_catalog()
        return {"ok": True, "snapshot": owner.snapshot()}

    @app.post("/api/spotify/disconnect")
    def disconnect():
        owner.clear_catalog(spotify_only=True)
        return {"ok": True, "snapshot": owner.snapshot()}

    app.mount("/assets", StaticFiles(directory=static), name="assets")
    return app
