"""Small CLI over the same workspace service used by the web interface."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import threading
import time
import webbrowser

from .config_manager import default_data_dir, load_dotenv
from .mapping_manager import build_mapping, export_results
from .service import Workspace


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(description="MusicMatcher: búsqueda musical local con yt-dlp")
    main.add_argument("--data-dir", type=Path, help="Carpeta de trabajo (por defecto ~/.musicmatcher)")
    sub = main.add_subparsers(dest="command", required=True)
    web = sub.add_parser("web", help="Abrir la interfaz web local")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--no-browser", action="store_true")
    imported = sub.add_parser("import", help="Importar CSV o JSON")
    imported.add_argument("file", type=Path)
    imported.add_argument("--replace", action="store_true")
    spotify = sub.add_parser("spotify", help="Importar playlist propia o colaborativa")
    spotify.add_argument("url")
    search = sub.add_parser("search", help="Buscar con yt-dlp y validar candidatos")
    run = sub.add_parser("run", help="Importar archivo opcional, buscar y descargar entradas autorizadas")
    run.add_argument("file", type=Path, nargs="?")
    run.add_argument("--replace", action="store_true")
    for command in (search, run):
        command.add_argument("--candidates", type=int, choices=range(1, 26), metavar="1..25",
                             help="Máximo de vídeos por canción; empieza con 1 y amplía si hay dudas")
        command.add_argument("--refresh", action="store_true")
        command.add_argument("--matching-mode", choices=("strict", "relaxed"))
    review = sub.add_parser("review", help="Mostrar canciones pendientes y candidatos")
    review.add_argument("--key")
    accept = sub.add_parser("accept", help="Asignar manualmente un enlace")
    accept.add_argument("key")
    accept.add_argument("url")
    authorize = sub.add_parser("authorize", help="Registrar permiso de descarga de una canción")
    authorize.add_argument("key")
    authorize.add_argument("--note", required=True)
    authorize.add_argument("--cover")
    download = sub.add_parser("download", help="Descargar enlaces aceptados y autorizados")
    for command in (download, run):
        command.add_argument("--output", type=Path)
        command.add_argument("--bitrate", type=int, choices=(64, 96, 128, 160, 192, 224, 256, 320))
    for command in (search, download, run):
        command.add_argument("--first", type=int, help="Procesar solo las primeras N canciones del catálogo")
    sub.add_parser("export", help="Exportar mapping.json y canciones.json")
    sub.add_parser("doctor", help="Comprobar configuración y ejecutables")
    sub.add_parser("summary", help="Mostrar progreso guardado")
    return main


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parser().parse_args(argv)
    workspace = None
    try:
        load_dotenv()
        workspace = Workspace(args.data_dir or default_data_dir())
        if args.command == "web":
            if not 1024 <= args.port <= 65535:
                raise ValueError("El puerto debe estar entre 1024 y 65535.")
            import uvicorn
            from .web import create_app
            url = f"http://127.0.0.1:{args.port}"
            print(f"MusicMatcher: {url}\nCierra con Ctrl+C. Datos: {workspace.data_dir}", flush=True)
            if not args.no_browser:
                timer = threading.Timer(1.5, lambda: webbrowser.open(url))
                timer.daemon = True
                timer.start()
            uvicorn.run(create_app(workspace=workspace), host="127.0.0.1", port=args.port, log_level="warning",
                        access_log=False)
            return 0
        if args.command == "doctor":
            checks = {name: bool(shutil.which(name)) for name in ("ffmpeg", "ffprobe", "deno")}
            print(json.dumps({"python": sys.version.split()[0], "executables": checks,
                              "spotify_configured": bool(workspace.settings.spotify_client_id),
                              "candidate_limit": workspace.settings.candidate_limit}, indent=2))
            return 0 if all(checks.values()) else 2
        if args.command == "summary":
            print(json.dumps(workspace.snapshot(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "review":
            tracks = workspace.state()["tracks"]
            selected = [t for t in tracks if (t["key"] == args.key if args.key else
                                            t.get("match", {}).get("status") != "accepted")]
            print(json.dumps(selected, ensure_ascii=False, indent=2))
            return 0
        if args.command == "accept":
            workspace.accept(args.key, args.url)
            return 0
        if args.command == "authorize":
            workspace.authorize(args.key, args.note, args.cover)
            return 0
        if args.command == "export":
            export_results(workspace.state_path)
            print(json.dumps(build_mapping(workspace.state()), ensure_ascii=False, indent=2))
            return 0
        changes = {}
        if getattr(args, "candidates", None) is not None:
            changes["candidate_limit"] = args.candidates
        if getattr(args, "matching_mode", None) is not None:
            changes["matching_mode"] = args.matching_mode
        if getattr(args, "output", None) is not None:
            changes["output_dir"] = str(args.output)
        if getattr(args, "bitrate", None) is not None:
            changes["bitrate"] = args.bitrate
        if changes:
            workspace.configure(changes)
        request = {"action": {"import": "import_file", "spotify": "import_spotify"}.get(args.command, args.command)}
        if getattr(args, "file", None):
            if args.file.stat().st_size > 5_000_000:
                raise ValueError("El archivo supera el límite de 5 MB.")
            request.update(content=args.file.read_text(encoding="utf-8-sig"), filename=args.file.name,
                           replace=args.replace)
        if args.command == "spotify":
            request["spotify_url"] = args.url
        if getattr(args, "refresh", False):
            request["refresh"] = True
        if getattr(args, "first", None) is not None:
            if not 1 <= args.first <= 10000:
                raise ValueError("--first debe estar entre 1 y 10000.")
            request["track_limit"] = args.first
        workspace.start(request)
        seen = 0
        while workspace.thread and workspace.thread.is_alive():
            for item in list(workspace.logs):
                if item["id"] > seen:
                    print(item["message"], flush=True)
                    seen = item["id"]
            time.sleep(0.2)
        status = workspace.snapshot()
        print(json.dumps(status["job"], ensure_ascii=False, indent=2))
        return {"completed": 0, "paused": 2, "error": 1}.get(status["job"]["status"], 1)
    except KeyboardInterrupt:
        if workspace:
            workspace.stop()
        print("Pausando. Puedes reanudar el proceso con el mismo comando.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    finally:
        if workspace:
            workspace.close()
