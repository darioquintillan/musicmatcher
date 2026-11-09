"""Conservative, explainable metadata matching; never modifies source titles."""

from __future__ import annotations

import re
import math
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from .mapping_manager import canonical_youtube_url

ALGORITHM_VERSION = "2.0"
ACCEPT_SCORE = 92.0
MIN_MARGIN = 5.0
_MULTI_TRACK = re.compile(r"\b(?:medley|popurri|multiple tracks|multiple songs|two songs|two tracks|tracklist|track list)\b")


def normalize(value: str | None) -> str:
    """A comparison copy: preserve the original value for filenames and ID3."""
    decomposed = unicodedata.normalize("NFKD", value or "").casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", "".join(
        char for char in decomposed if not unicodedata.combining(char)
    )).replace("_", " ").split())


_PROMO = re.compile(
    r"^(?:official\s+)?(?:audio|video|music video|lyric video|lyrics|visuali[sz]er)"
    r"(?:\s+official)?$|^(?:official|hd|hq|4k|1080p|720p)$"
)
_VERSION_WORDS = {
    "remix": r"\bremix(?:ed)?\b",
    "live": r"\blive(?:\s+(?:at|from|in|version|performance))?\b|\bin concert\b",
    "cover": r"\bcover\b",
    "acoustic": r"\bacoustic\b|\bunplugged\b",
    "instrumental": r"\binstrumental\b",
    "karaoke": r"\bkaraoke\b",
    "sped_up": r"\bsped up\b|\bspeed up\b|\bnightcore\b",
    "slowed": r"\bslowed\b|\bslowed down\b",
    "remaster": r"\bremaster(?:ed)?\b",
    "rerecorded": r"\bre recorded\b|\brerecorded\b|\btaylor s version\b",
    "radio_edit": r"\bradio edit\b|\bradio version\b",
    "extended": r"\bextended\b",
    "demo": r"\bdemo\b",
    "mono": r"\bmono\b",
    "stereo": r"\bstereo\b",
    "clean": r"\bclean\b|\bcensored\b",
    "explicit": r"\bexplicit\b|\buncensored\b",
}
_SUFFIX = re.compile(
    r"\s+(?:live|cover|acoustic|instrumental|karaoke|remix|remastered|demo|"
    r"sped up|slowed(?: down)?|radio edit|extended(?: mix)?|clean(?: version)?|"
    r"explicit(?: version)?)$", re.IGNORECASE
)


def _parts(title: str, reference_title: str = "") -> tuple[str, dict[str, list[str]]]:
    """Only classify contextual qualifiers, not song words such as Live Forever."""
    versions: dict[str, list[str]] = {}
    reference = normalize(reference_title)

    def consume(fragment: str) -> bool:
        normalized = normalize(fragment)
        if normalized and normalized == reference:
            return False
        if _PROMO.fullmatch(normalized):
            return True
        found = False
        for kind, pattern in _VERSION_WORDS.items():
            if re.search(pattern, normalized):
                versions.setdefault(kind, []).append(normalized)
                found = True
        return found

    def parenthetical(match: re.Match[str]) -> str:
        return " " if consume(match.group(1)) else match.group(0)

    core = re.sub(r"[\[(]([^\[\]()]+)[\])]", parenthetical, title or "")
    segments = re.split(r"\s+[-–—|]\s+", core)
    # The first segment might be the artist. Only suffixes can be qualifiers.
    kept = [segments[0]]
    for segment in segments[1:]:
        if not consume(segment):
            kept.append(segment)
    core = " - ".join(kept)

    # Bare suffixes need an actual preceding title. Do not classify a title
    # such as "Take Cover" if it is also the Spotify title.
    suffix = _SUFFIX.search(core)
    normalized_core = normalize(core)
    if suffix and normalized_core != reference and not (
        reference and normalized_core.endswith(" " + reference)
    ):
        consume(suffix.group(0))
        core = core[:suffix.start()]
    return normalize(core), versions


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def _artists(candidate: dict[str, Any]) -> list[str]:
    values = candidate.get("artists") or candidate.get("artist") or []
    if isinstance(values, str):
        values = [values]
    return [normalize(str(value)) for value in values if value]


def _version_detail(values: list[str], kind: str) -> str:
    details = set()
    for value in values:
        result = re.sub(_VERSION_WORDS[kind], " ", value)
        result = re.sub(r"\b(?:version|mix|by|the|official|audio|video)\b", " ", result)
        details.add(" ".join(result.split()))
    return " | ".join(sorted(details))


def _json_safe(value: Any) -> Any:
    """Provider snapshots must remain serializable by strict JSON writers."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def evaluate_candidate(track: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Return score, evidence and hard rejections; scores are not probabilities."""
    reasons: list[str] = []
    warnings: list[str] = []
    rejected = False
    review_required = False
    original_title = str(track.get("title") or "")
    expected, expected_versions = _parts(original_title, original_title)
    title_version_kinds = set(expected_versions)
    original_album = str(track.get("album") or "")
    _, expected_album_versions = _parts(original_album, original_album)
    displayed, displayed_versions = _parts(str(candidate.get("title") or ""), original_title)
    musical_title = str(candidate.get("track") or "")
    observed, actual_versions = _parts(musical_title, original_title) if musical_title else (
        displayed, dict(displayed_versions)
    )
    for kind, values in displayed_versions.items():
        actual_versions[kind] = sorted(set(actual_versions.get(kind, []) + values))
    candidate_album = str(candidate.get("album") or "")
    _, actual_album_versions = _parts(candidate_album, candidate_album)
    for kind, values in expected_album_versions.items():
        expected_versions[kind] = sorted(set(expected_versions.get(kind, []) + values))
    for kind, values in actual_album_versions.items():
        actual_versions[kind] = sorted(set(actual_versions.get(kind, []) + values))

    # Remove an artist prefix/suffix only when the expected artist is present.
    source_artists = [normalize(str(artist)) for artist in track.get("artists", []) if artist]
    def without_artist_prefix(value: str) -> str:
        for artist in source_artists:
            if value.startswith(artist + " "):
                value = value[len(artist):].strip()
            elif value.endswith(" " + artist):
                value = value[:-len(artist)].strip()
        return value

    if not musical_title:
        observed = without_artist_prefix(observed)
    elif expected != without_artist_prefix(displayed):
        reasons.append("display_title_conflicts_with_structured_track")
        review_required = True
    title_similarity = _similarity(expected, observed)
    if title_similarity < 0.82:
        reasons.append("title_mismatch")
        rejected = True
    elif expected != observed:
        reasons.append("title_not_exact_enough")
        review_required = True
    else:
        reasons.append("title_matches")

    # Original qualifiers are not discarded. Missing and different qualifiers
    # are both unsafe for unattended selection.
    for kind in sorted(set(expected_versions) | set(actual_versions)):
        if kind in {"clean", "explicit"}:
            continue  # Compare these labels with Spotify's explicit flag below.
        expected_values = expected_versions.get(kind)
        actual_values = actual_versions.get(kind)
        if bool(expected_values) != bool(actual_values):
            if expected_values and not actual_values and kind not in title_version_kinds:
                reasons.append(f"album_version_requires_review:{kind}")
                review_required = True
            else:
                reasons.append(f"version_mismatch:{kind}")
                rejected = True
        elif kind in {"remix", "live", "remaster", "rerecorded", "extended", "radio_edit"}:
            expected_detail = _version_detail(expected_values or [], kind)
            actual_detail = _version_detail(actual_values or [], kind)
            if expected_detail != actual_detail:
                reasons.append(f"version_detail_uncertain:{kind}")
                if expected_detail and actual_detail:
                    rejected = True
                else:
                    review_required = True
    if "clean" in actual_versions and track.get("explicit") is True:
        reasons.append("explicit_version_contradiction")
        rejected = True
    if "explicit" in actual_versions and track.get("explicit") is False:
        reasons.append("explicit_version_contradiction")
        rejected = True
    if "explicit" in candidate and isinstance(candidate["explicit"], bool):
        if isinstance(track.get("explicit"), bool) and candidate["explicit"] != track["explicit"]:
            reasons.append("explicit_version_contradiction")
            rejected = True
    elif isinstance(track.get("explicit"), bool) and not (
        {"explicit", "clean"} & actual_versions.keys()
    ):
        warnings.append("explicit_flag_not_independently_verified")
        if track["explicit"]:
            reasons.append("explicit_version_requires_review")
            review_required = True
    if "clean" in expected_versions and "clean" not in actual_versions and candidate.get("explicit") is not False:
        reasons.append("clean_version_requires_review")
        review_required = True
    if "explicit" in expected_versions and "explicit" not in actual_versions and candidate.get("explicit") is not True:
        reasons.append("explicit_version_requires_review")
        review_required = True

    actual_artists = _artists(candidate)
    artist_similarity = 0.0
    if source_artists and actual_artists:
        artist_similarity = min(max(_similarity(artist, observed_artist)
                                    for observed_artist in actual_artists) for artist in source_artists)
        if artist_similarity < 0.82:
            reasons.append("artist_mismatch")
            rejected = True
        elif any(artist not in actual_artists for artist in source_artists):
            reasons.append("artist_uncertain")
            review_required = True
        else:
            reasons.append("structured_artists_match")
        # An extra credited performer can identify a cover or different remix.
        if any(artist not in source_artists for artist in actual_artists):
            reasons.append("additional_artist_requires_review")
            review_required = True
    else:
        textual = normalize(" ".join(str(candidate.get(field) or "") for field in (
            "title", "channel", "uploader"
        )))
        artist_similarity = 0.8 if source_artists and all(
            re.search(r"(?<!\w)" + re.escape(artist) + r"(?!\w)", textual)
            for artist in source_artists
        ) else 0.0
        reasons.append("structured_artist_metadata_missing")
        review_required = True

    duration = candidate.get("duration")
    expected_ms = track.get("duration_ms")
    duration_difference = None
    duration_similarity = 0.0
    if (isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration > 0
            and isinstance(expected_ms, (int, float)) and not isinstance(expected_ms, bool)
            and math.isfinite(expected_ms) and expected_ms > 0):
        expected_duration = expected_ms / 1000
        duration_difference = abs(duration - expected_duration)
        tolerance = max(3.0, expected_duration * 0.01)
        duration_similarity = max(0.0, 1 - duration_difference / (tolerance * 3))
        if duration_difference > tolerance:
            reasons.append("duration_mismatch")
            rejected = True
        else:
            reasons.append("duration_matches")
    else:
        reasons.append("duration_unknown")
        review_required = True

    if _MULTI_TRACK.search(normalize(str(candidate.get("description") or ""))) and not _MULTI_TRACK.search(normalize(original_title)):
        reasons.append("description_suggests_multiple_tracks")
        review_required = True

    if candidate.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
        reasons.append("live_stream_or_upcoming")
        rejected = True
    if candidate.get("availability") in {"private", "premium_only", "subscriber_only", "needs_auth", "unavailable"}:
        reasons.append("video_unavailable_or_requires_authentication")
        rejected = True

    url = None
    try:
        raw_url = candidate.get("webpage_url") or candidate.get("url")
        if candidate.get("id"):
            raw_url = "https://www.youtube.com/watch?v=" + str(candidate["id"])
        url = canonical_youtube_url(raw_url or "")
    except (TypeError, ValueError):
        reasons.append("invalid_youtube_url")
        rejected = True

    official_hint = bool(candidate.get("channel_is_verified")) or bool(
        re.search(r"\b(?:official audio|official video|topic)\b", normalize(
            str(candidate.get("title") or "") + " " + str(candidate.get("channel") or "")
        ))
    )
    # Structured title is a stronger signal than an upload's display title.
    evidence_score = (3 if musical_title else 0) + (2 if official_hint else 0)
    score = round(45 * title_similarity + 30 * artist_similarity + 20 * duration_similarity + evidence_score, 2)
    if not musical_title:
        reasons.append("structured_track_metadata_missing")
        review_required = True
    if official_hint:
        reasons.append("official_channel_hint_only")
    return {
        "score": score,
        "eligible": not rejected and not review_required,
        "rejected": rejected,
        "review_required": review_required,
        "reasons": reasons,
        "warnings": warnings,
        "url": url,
        "title_similarity": round(title_similarity, 4),
        "artist_similarity": round(artist_similarity, 4),
        "duration_difference_seconds": round(duration_difference, 3) if duration_difference is not None else None,
    }


def choose_match(track: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Accept only a strong, unambiguous candidate; preserve all decision evidence."""
    seen: set[str] = set()
    scored: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate = _json_safe(candidate)
        evaluation = evaluate_candidate(track, candidate)
        identity = evaluation["url"] or str(candidate.get("id") or candidate.get("title"))
        if identity in seen:
            continue
        seen.add(identity)
        scored.append({"candidate": candidate, **evaluation})
    scored.sort(key=lambda item: item["score"], reverse=True)
    result: dict[str, Any] = {
        "status": "unmatched", "url": None, "score": None,
        "algorithm_version": ALGORITHM_VERSION,
        "candidates": scored, "reasons": ["no_candidates" if not scored else "all_candidates_rejected"],
        "decision": "automatic",
    }
    plausible = [item for item in scored if not item["rejected"]]
    if not plausible:
        return result
    result["status"] = "review"
    best = plausible[0]
    result["score"] = best["score"]
    result["reasons"] = list(best["reasons"])
    if not best["eligible"] or best["score"] < ACCEPT_SCORE:
        result["reasons"].append("insufficient_evidence_for_automatic_selection")
        return result
    if len(plausible) > 1 and best["score"] - plausible[1]["score"] < MIN_MARGIN:
        result["reasons"].append("ambiguous_candidates")
        return result
    result.update(status="accepted", url=best["url"], reasons=["strong_metadata_match"])
    return result

