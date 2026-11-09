"""Validated song input/state contracts and provenance guards."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Source = Literal["spotify", "user_authorized", "unknown"]
_SPOTIFY_REFERENCE = re.compile(
    r"(?:https?://(?:open|play)\.spotify\.com(?:[/:?#]|$)|spotify:(?:track|playlist|album|artist):)",
    re.IGNORECASE,
)


def contains_spotify_data(value: Any) -> bool:
    """Detect provenance markers; file extensions never erase their origin.

    Runtime results and audio permissions are not source metadata. A YouTube
    description linking to Spotify must not relabel independently supplied data.
    """
    if isinstance(value, dict):
        if str(value.get("source", "")).casefold() == "spotify":
            return True
        if any(value.get(name) for name in ("spotify_id", "spotify_url", "spotify_uri")):
            return True
        return any(contains_spotify_data(item) for name, item in value.items()
                   if name not in {"match", "download", "authorization"})
    if isinstance(value, (list, tuple)):
        return any(contains_spotify_data(item) for item in value)
    return isinstance(value, str) and bool(_SPOTIFY_REFERENCE.search(value))


class TrackInput(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=False)

    title: str = Field(min_length=1, max_length=2000)
    artists: list[str] = Field(min_length=1, max_length=100)
    album: str | None = Field(default=None, max_length=2000)
    duration_ms: int | None = Field(default=None, gt=0, strict=True)
    spotify_id: str | None = None
    spotify_url: str | None = None
    cover_url: str | None = None
    isrc: str | None = None
    explicit: bool | None = Field(default=None, strict=True)

    @field_validator("title")
    @classmethod
    def title_must_have_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must contain text")
        return value

    @field_validator("artists")
    @classmethod
    def artists_must_have_text(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("artists must contain non-empty names")
        return values


class Song(TrackInput):
    """State rows remain compatible with the original project's dictionaries."""

    model_config = ConfigDict(extra="allow")

    key: str = Field(min_length=1)
    track_id: str = Field(min_length=1)
    position: int = Field(ge=1)
    duplicate_of: str | None = None
    source: Source = "unknown"
    ai_allowed: bool = False
    match: dict[str, Any] = Field(default_factory=lambda: {"status": "pending"})
    download: dict[str, Any] = Field(default_factory=lambda: {"status": "pending"})
    authorization: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_provenance(self) -> "Song":
        # This application has no AI providers. Retain the legacy field only
        # for compatibility; imported data cannot activate model processing.
        self.ai_allowed = False
        if contains_spotify_data(self.model_dump()):
            self.source = "spotify"
        return self


