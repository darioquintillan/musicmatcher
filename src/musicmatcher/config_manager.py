"""Validated local search, audio output and Spotify configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .mapping_manager import atomic_json


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    candidate_limit: int = Field(default=8, ge=1, le=25)
    matching_mode: Literal["strict", "relaxed"] = "relaxed"
    output_dir: str = str(Path.home() / "Music" / "MusicMatcher")
    bitrate: Literal[64, 96, 128, 160, 192, 224, 256, 320] = 320
    spotify_client_id: str = Field(default="", max_length=100)
    spotify_redirect_uri: str = "http://127.0.0.1:8888/callback"
    ffmpeg_location: str | None = None

    @field_validator("spotify_redirect_uri")
    @classmethod
    def validate_redirect(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
            raise ValueError("Use a registered loopback URI such as http://127.0.0.1:8888/callback.")
        return value

    @field_validator("output_dir")
    @classmethod
    def validate_output_dir(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Choose a valid output folder.")
        return str(Path(value).expanduser().resolve())

    def public(self) -> dict:
        return self.model_dump()


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key.replace("_", "a").isalnum() or key[0].isdigit():
            raise ValueError("Invalid .env line. Expected NAME=value.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if value:
            os.environ.setdefault(key, value)


def default_data_dir() -> Path:
    value = os.getenv("MUSICMATCHER_DATA_DIR", "").strip()
    return Path(value).expanduser().resolve() if value else Path.home() / ".musicmatcher"


def load_settings(data_dir: Path) -> Settings:
    config_path = data_dir / "settings.json"
    values = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    fields = {
        "candidate_limit": "MUSICMATCHER_CANDIDATE_LIMIT", "output_dir": "MUSICMATCHER_OUTPUT_DIR",
        "matching_mode": "MUSICMATCHER_MATCHING_MODE",
        "spotify_client_id": "SPOTIPY_CLIENT_ID", "spotify_redirect_uri": "SPOTIPY_REDIRECT_URI",
    }
    for field, env_name in fields.items():
        if os.getenv(env_name, "").strip():
            values[field] = os.environ[env_name].strip()
    return Settings.model_validate(values)


def save_settings(data_dir: Path, settings: Settings) -> None:
    atomic_json(data_dir / "settings.json", settings.model_dump())


def update_settings(current: Settings, changes: dict) -> Settings:
    values = current.model_dump()
    values.update(changes)
    return Settings.model_validate(values)
