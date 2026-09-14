from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from .config import Config


def fingerprint(config: Config, stage: str) -> str:
    relevant = {
        "stage": stage,
        "catalogVersion": config.version,
        "generatedAt": config.generated_at,
        "tempo": config.tempo,
        "selection": config.selection,
        "sources": [source.__dict__ for source in config.sources],
    }
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()


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
    if row and row[0] == expected and not force:
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
