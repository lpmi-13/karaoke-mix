from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .network import (
    LISTENBRAINZ_TOKEN_ENVIRONMENT,
    MAX_SOURCE_DOWNLOAD_BYTES,
    validate_popularity_url,
    validate_source_url,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.v2.json")


@dataclass(frozen=True)
class Paths:
    root: Path
    raw: Path
    work: Path
    reports: Path
    public_catalog: Path
    database: Path

    @classmethod
    def under(cls, root: Path = ROOT) -> "Paths":
        data = root / "data"
        return cls(
            root=root,
            raw=data / "raw",
            work=data / "work",
            reports=data / "reports",
            public_catalog=root / "public" / "catalog",
            database=data / "work" / "catalog.duckdb",
        )

    def create(self) -> None:
        for path in (self.raw, self.work, self.reports, self.public_catalog):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    filename: str
    size: int
    sha256: str
    snapshot: str
    license: str

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 100:
            raise ValueError("source name must contain 1-100 characters")
        if (
            not self.filename
            or self.filename in {".", ".."}
            or Path(self.filename).name != self.filename
            or "/" in self.filename
            or "\\" in self.filename
        ):
            raise ValueError(f"source {self.name!r} filename must be a plain filename")
        validate_source_url(self.url)
        if isinstance(self.size, bool) or not isinstance(self.size, int):
            raise ValueError(f"source {self.name!r} size must be an integer")
        if not 0 < self.size <= MAX_SOURCE_DOWNLOAD_BYTES:
            raise ValueError(
                f"source {self.name!r} size must be between 1 and "
                f"{MAX_SOURCE_DOWNLOAD_BYTES} bytes"
            )
        if not re.fullmatch(r"[0-9a-fA-F]{64}", self.sha256):
            raise ValueError(f"source {self.name!r} sha256 must be 64 hexadecimal characters")
        if not self.snapshot or not self.license:
            raise ValueError(f"source {self.name!r} snapshot and license are required")


@dataclass(frozen=True)
class Config:
    raw: dict[str, Any]
    path: Path

    @property
    def version(self) -> int:
        return int(self.raw["catalogVersion"])

    @property
    def generated_at(self) -> str:
        return str(self.raw["generatedAt"])

    @property
    def target_size(self) -> int:
        return int(self.raw["targetSize"])

    @property
    def tempo(self) -> dict[str, Any]:
        return self.raw["tempo"]

    @property
    def selection(self) -> dict[str, Any]:
        return self.raw["selection"]

    @property
    def popularity(self) -> dict[str, Any]:
        return self.raw["popularity"]

    @property
    def sources(self) -> list[Source]:
        return [Source(**item) for item in self.raw["sources"]]

    def source(self, name: str) -> Source:
        try:
            return next(source for source in self.sources if source.name == name)
        except StopIteration as error:
            raise KeyError(f"source {name!r} is missing from {self.path}") from error


def load_config(path: Path | None = None) -> Config:
    resolved = (path or DEFAULT_CONFIG_PATH).resolve()
    with resolved.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    config = Config(raw, resolved)
    _validate_config(config)
    return config


def _bounded_integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer between {minimum} and {maximum}")
    return value


def _validate_config(config: Config) -> None:
    if not isinstance(config.raw, dict):
        raise ValueError(f"configuration in {config.path} must be an object")
    _bounded_integer(config.raw.get("catalogVersion"), "catalogVersion", 1, 1_000_000)
    _bounded_integer(config.raw.get("targetSize"), "targetSize", 1, 1_000_000)
    if not isinstance(config.raw.get("generatedAt"), str) or not config.raw["generatedAt"]:
        raise ValueError("generatedAt must be a non-empty string")
    for section in ("tempo", "selection", "popularity"):
        if not isinstance(config.raw.get(section), dict):
            raise ValueError(f"{section} must be an object")

    raw_sources = config.raw.get("sources")
    if not isinstance(raw_sources, list) or not 0 < len(raw_sources) <= 100:
        raise ValueError("sources must contain between 1 and 100 entries")
    sources = config.sources
    names = [source.name for source in sources]
    filenames = [source.filename for source in sources]
    if len(set(names)) != len(names):
        raise ValueError("source names must be unique")
    if len(set(filenames)) != len(filenames):
        raise ValueError("source filenames must be unique")

    popularity = config.popularity
    validate_popularity_url(str(popularity.get("url") or ""))
    _bounded_integer(popularity.get("batchSize"), "popularity.batchSize", 1, 10_000)
    _bounded_integer(
        popularity.get("maximumRetries"), "popularity.maximumRetries", 0, 10
    )
    backoff = popularity.get("initialBackoffSeconds")
    if isinstance(backoff, bool) or not isinstance(backoff, (int, float)) or not 0 <= backoff <= 60:
        raise ValueError("popularity.initialBackoffSeconds must be between 0 and 60")
    if popularity.get("tokenEnvironment") != LISTENBRAINZ_TOKEN_ENVIRONMENT:
        raise ValueError(
            f"popularity.tokenEnvironment must be {LISTENBRAINZ_TOKEN_ENVIRONMENT!r}"
        )
    user_agent = popularity.get("userAgent")
    if not isinstance(user_agent, str) or not user_agent or len(user_agent) > 512:
        raise ValueError("popularity.userAgent must contain 1-512 characters")
