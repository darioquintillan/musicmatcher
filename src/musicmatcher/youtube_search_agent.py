"""YouTube discovery with yt-dlp, deterministic validation and resumable state."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from .mapping_manager import canonical_youtube_url, export_results, load_state, save_state, utc_now
from .match_validator import ALGORITHM_VERSION, _json_safe, choose_match, first_candidate_is_confident

logger = logging.getLogger(__name__)
SEARCH_STRATEGY = "adaptive-v1"
_CANDIDATE_FIELDS = (
    "id", "webpage_url", "title", "track", "artists", "artist", "album", "channel", "uploader",
    "channel_id", "channel_is_verified", "duration", "description", "availability", "live_status", "explicit",
    "unavailable_reason", "metadata_error",
)


class SearchCancelled(Exception):
    pass


def _check_stop(stop_event: Any) -> None:
    if stop_event is not None and stop_event.is_set():
        raise SearchCancelled()


def _fingerprint(track: dict[str, Any]) -> str:
    value = {field: track.get(field) for field in (
        "spotify_id", "title", "artists", "album", "duration_ms", "isrc", "explicit", "source",
        "spotify_url", "spotify_uri",
    )}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _cache_fingerprint(track: dict[str, Any], settings: Any) -> str:
    identity = {"metadata": _fingerprint(track), "algorithm": ALGORITHM_VERSION,
                "candidate_limit": getattr(settings, "candidate_limit", 8),
                "matching_mode": getattr(settings, "matching_mode", "relaxed"), "search_strategy": SEARCH_STRATEGY}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def _manual(match: dict[str, Any]) -> bool:
    return bool(match.get("manual")) or any(match.get(field) == "manual" for field in ("decision", "decision_source", "source"))


def _compatible_match(track: dict[str, Any], settings: Any) -> bool:
    match = track.get("match") or {}
    if match.get("cache_fingerprint") == _cache_fingerprint(track, settings):
        return True
    previous_limit = match.get("candidate_limit")
    return (isinstance(previous_limit, int) and not isinstance(previous_limit, bool)
            and previous_limit >= getattr(settings, "candidate_limit", 8)
            and match.get("metadata_fingerprint") == _fingerprint(track)
            and match.get("algorithm_version") == ALGORITHM_VERSION
            and match.get("search_strategy") == SEARCH_STRATEGY
            and match.get("matching_mode") == getattr(settings, "matching_mode", "relaxed"))


def _blocked(message: str) -> bool:
    return bool(re.search(r"\b429\b", message)) or any(fragment in message.casefold() for fragment in (
        "too many requests", "confirm you're not a bot", "confirm you’re not a bot", "rate limit", "request limit",
    ))


def _individual_unavailable(message: str) -> str | None:
    lowered = message.casefold()
    if any(fragment in lowered for fragment in ("confirm your age", "age-restricted", "age restricted", "age verification")):
        return "age_restricted"
    if any(fragment in lowered for fragment in ("private video", "video is private", "this video is private")):
        return "private"
    if any(fragment in lowered for fragment in (
        "video unavailable", "video has been removed", "this video is unavailable", "video is not available",
        "deleted video", "removed by the uploader", "copyright", "members-only", "members only", "join this channel",
    )):
        return "unavailable"
    return None


def _apply_match(track: dict[str, Any], decision: dict[str, Any]) -> None:
    previous = track.get("match") or {}
    if previous.get("status") == "accepted" and (
        decision.get("status") != "accepted" or previous.get("url") != decision.get("url")
    ):
        track["authorization"] = {}
        track["download"] = {"status": "pending"}
    track["match"] = decision


def _persist(path: Path, state: dict[str, Any]) -> None:
    save_state(path, state)
    export_results(path)


class _QuietLogger:
    def debug(self, message: str) -> None: pass
    def warning(self, message: str) -> None: pass
    def error(self, message: str) -> None: pass


def _search_candidates(query: str, limit: int, *, progress=None, stop_event=None,
                       track: dict[str, Any] | None = None, matching_mode: str = "relaxed",
                       cached_candidates: list[dict[str, Any]] | None = None,
                       expand_saved: bool = False) -> list[dict[str, Any]]:
    """Inspect one result first, expanding to the maximum only when evidence is weak."""
    from yt_dlp import YoutubeDL

    _check_stop(stop_event)
    options = {"quiet": True, "no_warnings": True, "logger": _QuietLogger(), "skip_download": True,
               "socket_timeout": 20, "retries": 2, "extractor_retries": 2, "fragment_retries": 2,
               "retry_sleep_functions": {"http": lambda n: min(2 ** n, 20), "extractor": lambda n: min(2 ** n, 20)},
               "ignoreerrors": False}
    candidates = copy.deepcopy(cached_candidates or [])[:limit]
    if expand_saved:
        if len(candidates) >= limit:
            return candidates
        stages = [limit]
    elif cached_candidates is not None:
        # A previously complete result set needs no new network requests. A
        # single early result may need expansion after changing matching mode.
        if (track is None or len(candidates) != 1 or limit == 1
                or first_candidate_is_confident(track, candidates[0], mode=matching_mode)):
            return candidates
        stages = [limit]
    else:
        stages = [1, limit] if track is not None and limit > 1 else [limit]
    seen = {canonical_youtube_url(item["webpage_url"] if item.get("webpage_url") else
                                  "https://www.youtube.com/watch?v=" + str(item["id"]))
            for item in candidates}
    with YoutubeDL({**options, "noplaylist": True}) as detail:
        for stage_limit in stages:
            _check_stop(stop_event)
            if progress:
                expanded = stage_limit > 1 and track is not None
                progress({"message": (f"Ampliando los candidatos guardados hasta {limit}" if expand_saved else
                                      f"Hay dudas en el primer candidato; ampliando hasta {limit}" if expanded else
                                      "Consultando el primer resultado de YouTube"),
                          "status": "candidate_expand" if expanded else "discovering",
                          "candidate_done": len(candidates), "candidate_total": stage_limit,
                          "candidate_limit": limit})
            with YoutubeDL({**options, "extract_flat": True}) as search:
                results = search.extract_info(f"ytsearch{stage_limit}:{query}", download=False)
                if not isinstance(results, dict):
                    raise RuntimeError("YouTube no devolvió resultados válidos.")
                entries = list(results.get("entries") or [])[:stage_limit]
            for entry in entries:
                _check_stop(stop_event)
                if len(candidates) >= stage_limit:
                    break
                if not isinstance(entry, dict):
                    raise RuntimeError("YouTube devolvió un candidato incompleto.")
                raw_url = ("https://www.youtube.com/watch?v=" + str(entry["id"])) if entry.get("id") else (
                    entry.get("webpage_url") or entry.get("url") or ""
                )
                url = canonical_youtube_url(raw_url)
                if url in seen:
                    continue
                seen.add(url)
                if progress:
                    progress({"message": f"Consultando candidato {len(candidates) + 1}/{stage_limit}",
                              "status": "candidate_lookup", "candidate_done": len(candidates),
                              "candidate_total": stage_limit})
                try:
                    info = detail.extract_info(url, download=False)
                except Exception as error:
                    message = str(error)
                    reason = None if _blocked(message) else _individual_unavailable(message)
                    if reason is None:
                        raise
                    candidates.append({
                        "id": url.rsplit("=", 1)[-1], "webpage_url": url,
                        "title": str(entry.get("title") or "Vídeo no disponible"),
                        "availability": "needs_auth" if reason == "age_restricted" else reason,
                        "unavailable_reason": reason, "metadata_error": message,
                    })
                    logger.info("Candidato %s omitido: %s", url.rsplit("=", 1)[-1], reason)
                    if progress:
                        progress({"message": f"Candidato {len(candidates)}/{stage_limit} no disponible ({reason})",
                                  "status": "candidate_error", "candidate_done": len(candidates),
                                  "candidate_total": stage_limit})
                    continue
                if not isinstance(info, dict):
                    raise RuntimeError("No se obtuvieron los metadatos completos de un candidato.")
                snapshot = _json_safe({field: info[field] for field in _CANDIDATE_FIELDS if field in info})
                snapshot.update(webpage_url=url, id=url.rsplit("=", 1)[-1])
                candidates.append(snapshot)
                if progress:
                    progress({"message": f"Candidato {len(candidates)}/{stage_limit} consultado",
                              "status": "candidate_ready", "candidate_done": len(candidates),
                              "candidate_total": stage_limit})
            _check_stop(stop_event)
            if (track is not None and stage_limit == 1 and len(candidates) == 1
                    and first_candidate_is_confident(track, candidates[0], mode=matching_mode)):
                if progress:
                    progress({"message": "El primer candidato encaja con claridad; no es necesario ampliar",
                              "status": "candidate_early_accept", "candidate_done": 1, "candidate_total": 1})
                break
    _check_stop(stop_event)
    return candidates


def search_tracks(state_path, *, settings=None, progress=None, stop_event=None, retry_failed=True, refresh=False,
                  track_limit: int | None = None, review_only: bool = False) -> dict[str, Any]:
    if settings is None:
        from .config_manager import Settings
        settings = Settings()
    limit = int(getattr(settings, "candidate_limit", 8))
    if not 1 <= limit <= 25:
        raise ValueError("El número de candidatos debe estar entre 1 y 25.")
    path = Path(state_path)
    state = load_state(path)
    if track_limit is not None and (isinstance(track_limit, bool) or not isinstance(track_limit, int) or track_limit < 1):
        raise ValueError("El límite de canciones debe ser un entero positivo.")
    tracks = state["tracks"][:track_limit] if track_limit is not None else state["tracks"]
    if review_only:
        tracks = [track for track in tracks if track.get("match", {}).get("status") == "review"
                  and not _manual(track.get("match") or {})]
    started = utc_now()
    summary = {"searched": 0, "reused": 0, "skipped": 0, "errors": 0, "paused": False, "started_at": started}
    state["review_search_summary" if review_only else "search_summary"] = summary
    summary["review_only"] = review_only

    def emit(done: int, message: str, title: str = "", **extras):
        if progress:
            progress({"stage": "search", "done": done, "total": len(tracks), "message": message,
                      "current_title": title, "started_at": started, "timestamp": utc_now(), **extras})

    cache: dict[str, dict[str, Any]] = {}
    for track in tracks:
        match = track.get("match") or {}
        fingerprint = _cache_fingerprint(track, settings)
        if _manual(match):
            cache[fingerprint] = copy.deepcopy(match)
        elif not refresh and _compatible_match(track, settings) and match.get("status") in {"accepted", "review", "unmatched"}:
            cache[fingerprint] = copy.deepcopy(match)
    emit(0, "Iniciando búsqueda", status="started")
    done = 0
    for index, track in enumerate(tracks):
        title = str(track.get("title") or "")
        try:
            _check_stop(stop_event)
            old_match = track.get("match") or {}
            fingerprint = _cache_fingerprint(track, settings)
            if old_match.get("status") == "unavailable" or track.get("unavailable_reason") or _manual(old_match):
                summary["skipped"] += 1
            elif not refresh and _compatible_match(track, settings) and old_match.get("status") in {"accepted", "review", "unmatched"}:
                summary["skipped"] += 1
            elif old_match.get("status") == "search_error" and not (retry_failed or refresh):
                summary["skipped"] += 1
            elif fingerprint in cache:
                decision = copy.deepcopy(cache[fingerprint])
                decision["reused"] = True
                _apply_match(track, decision)
                summary["reused"] += 1
            elif not title or not track.get("artists"):
                _apply_match(track, {"status": "unmatched", "url": None, "reasons": ["missing_track_metadata"],
                                     "algorithm_version": ALGORITHM_VERSION, "cache_fingerprint": fingerprint})
            else:
                emit(index, "Buscando candidatos", title, status="looking_up", candidate_done=0,
                     candidate_total=1, candidate_limit=limit)
                query = f"{title} {' '.join(track['artists'])}"
                mode = getattr(settings, "matching_mode", "relaxed")
                saved = old_match.get("candidates")
                cached_candidates = None
                if ((review_only or not refresh) and old_match.get("metadata_fingerprint") == _fingerprint(track)
                        and (review_only or old_match.get("candidate_limit") == limit) and isinstance(saved, list)):
                    cached_candidates = [entry.get("candidate", entry) for entry in saved]
                    emit(index, "Reutilizando candidatos guardados", title, status="candidate_cache")
                if cached_candidates is not None and (len(cached_candidates) >= limit or (not review_only and (
                        len(cached_candidates) != 1 or limit == 1
                        or first_candidate_is_confident(track, cached_candidates[0], mode=mode)))):
                    candidates = cached_candidates
                    emit(index, "Candidatos recuperados de la caché", title, status="candidate_cache",
                         candidate_done=len(candidates), candidate_total=len(candidates), candidate_limit=limit)
                else:
                    candidates = _search_candidates(query, limit, stop_event=stop_event, track=track,
                                                    matching_mode=mode, cached_candidates=cached_candidates,
                                                    expand_saved=review_only,
                                                    progress=lambda event: emit(index, title=title, **event))
                candidates = _json_safe(candidates)
                _check_stop(stop_event)
                decision = choose_match(track, candidates, mode=getattr(settings, "matching_mode", "relaxed"))
                candidate_errors = [{"id": item.get("id"), "reason": item.get("unavailable_reason"),
                                     "message": item["metadata_error"]}
                                    for item in candidates if item.get("metadata_error")]
                if candidate_errors:
                    decision["candidate_errors"] = candidate_errors
                decision.update(query=query, updated_at=utc_now(), metadata_fingerprint=_fingerprint(track),
                                cache_fingerprint=fingerprint, candidate_limit=limit, candidate_count=len(candidates),
                                search_strategy=SEARCH_STRATEGY)
                if review_only:
                    decision["review_expansion"] = {
                        "previous_count": len(cached_candidates or []), "target_limit": limit,
                        "added_count": max(0, len(candidates) - len(cached_candidates or [])),
                        "updated_at": utc_now(),
                    }
                _apply_match(track, decision)
                cache[fingerprint] = copy.deepcopy(decision)
                summary["searched"] += 1
                logger.info("Coincidencia %s: %s", track["key"], decision["status"])
            done = index + 1
            _persist(path, state)
            emit(done, "Canción procesada", title, status=track.get("match", {}).get("status", "pending"))
        except (SearchCancelled, KeyboardInterrupt) as error:
            summary.update(paused=True, reason="interrupted")
            _persist(path, state)
            if isinstance(error, KeyboardInterrupt):
                raise
            break
        except Exception as error:
            # Search errors are distinct from a successfully searched missing match.
            message = str(error)
            decision = {"status": "search_error", "url": None, "error": message, "updated_at": utc_now(),
                        "algorithm_version": ALGORITHM_VERSION}
            if review_only:
                # Keep the existing review evidence retryable after a network
                # failure; never turn a failed expansion into a lost match.
                decision = copy.deepcopy(old_match)
                decision["review_expansion"] = {"target_limit": limit, "error": message, "updated_at": utc_now()}
            _apply_match(track, decision)
            cache[_cache_fingerprint(track, settings)] = copy.deepcopy(decision)
            summary["errors"] += 1
            done = index + 1
            logger.warning("Error al consultar YouTube para %s: %s", track["key"], message)
            if _blocked(message):
                summary.update(paused=True, reason="youtube_rate_limit_or_authentication_block")
            _persist(path, state)
            emit(done, "Error al buscar candidatos", title, status="search_error")
            if summary["paused"]:
                break
    summary["finished_at"] = utc_now()
    _persist(path, state)
    emit(done, "Búsqueda pausada" if summary["paused"] else "Búsqueda terminada", status="paused" if summary["paused"] else "finished", finished_at=summary["finished_at"])
    return state
