from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from .config import Config


def _source_fingerprint(source: object) -> dict[str, Any]:
    # The checksum already identifies the exact input bytes. Download-size pins
    # are a transport safeguard and must not invalidate completed import stages.
    return {
        key: value
        for key, value in vars(source).items()
        if key != "size"
    }


def fingerprint(config: Config, stage: str) -> str:
    source_names = {
        "import-acousticbrainz": ("acousticbrainz-rhythm",),
        "import-musicbrainz-canonical": ("musicbrainz-canonical",),
        "import-musicbrainz-core": ("musicbrainz-core",),
        "import-musicbrainz-genres": ("musicbrainz-derived",),
    }.get(stage, ())
    relevant: dict[str, Any] = {
        "stage": stage,
        "sources": [_source_fingerprint(config.source(name)) for name in source_names],
    }
    if stage in {"import-acousticbrainz", "score-source-tempos", "score-canonical-tempos"}:
        relevant["tempo"] = config.tempo
    if stage == "import-musicbrainz-core":
        # Increment when the extracted tables or derived metadata contract changes.
        relevant["coreImportVersion"] = 2
    if stage == "import-musicbrainz-genres":
        relevant["genreImportVersion"] = 1
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()


def _compatible_fingerprints(config: Config, stage: str) -> set[str]:
    """Recognize reusable v1 imports while migrating the pipeline to v2."""
    if config.version != 2 or stage == "import-musicbrainz-core":
        return set()
    legacy = {
        "stage": stage,
        "catalogVersion": 1,
        "generatedAt": config.generated_at,
        "tempo": config.tempo,
        "selection": config.selection,
        "sources": [_source_fingerprint(source) for source in config.sources],
    }
    return {hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()}


def run_stage(
    db: Any,
    config: Config,
    stage: str,
    operation: Callable[[], None],
    force: bool = False,
) -> None:
    expected = fingerprint(config, stage)
    row = db.execute(
        "SELECT fingerprint FROM pipeline_stage WHERE stage = ?", [stage]
    ).fetchone()
    if row and row[0] in ({expected} | _compatible_fingerprints(config, stage)) and not force:
        if row[0] != expected:
            db.execute(
                "UPDATE pipeline_stage SET fingerprint = ?, completed_at = current_timestamp WHERE stage = ?",
                [expected, stage],
            )
        print(f"resume: {stage} already complete", flush=True)
        return
    print(f"starting: {stage}", flush=True)
    started = time.monotonic()
    operation()
    db.execute(
        """
        INSERT INTO pipeline_stage VALUES (?, ?, current_timestamp)
        ON CONFLICT (stage) DO UPDATE SET
          fingerprint = excluded.fingerprint, completed_at = excluded.completed_at
        """,
        [stage, expected],
    )
    elapsed = timedelta(seconds=round(time.monotonic() - started))
    print(f"complete: {stage} ({elapsed})", flush=True)
