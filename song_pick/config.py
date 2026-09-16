from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
    sha256: str
    snapshot: str
    license: str


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
        return Config(json.load(handle), resolved)
