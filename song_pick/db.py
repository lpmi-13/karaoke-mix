from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any


DEFAULT_MEMORY_LIMIT = "4GB"
DEFAULT_CHECKPOINT_THRESHOLD = "1GB"


@contextmanager
def transaction(db: Any) -> Iterator[None]:
    """Run related writes in one transaction and roll back on failure."""
    db.execute("BEGIN TRANSACTION")
    try:
        yield
    except BaseException:
        db.execute("ROLLBACK")
        raise
    else:
        db.execute("COMMIT")


def columnar_parameters(rows: Sequence[Sequence[Any]]) -> list[list[Any]]:
    """Transpose row-oriented batches for DuckDB's set-based UNNEST queries."""
    return [list(column) for column in zip(*rows, strict=True)]


def connect(path: Path, threads: int = 1, memory_limit: str = DEFAULT_MEMORY_LIMIT):
    if threads < 1:
        raise ValueError("threads must be at least 1")
    try:
        import duckdb
    except ImportError as error:
        raise SystemExit(
            "DuckDB is required for catalog builds. Install requirements-catalog.txt first."
        ) from error
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(path))
    # Catalog imports contain large scans and joins. Keep the default
    # deliberately conservative so a build leaves the rest of the machine
    # responsive; callers can opt into more parallelism when appropriate.
    connection.execute("SET threads = ?", [threads])
    # DuckDB otherwise reserves 80% of physical RAM by default. Leave ample
    # headroom for Python and the desktop, and allow blocking operators to
    # spill to the database's adjacent temporary directory instead.
    connection.execute("SET memory_limit = ?", [memory_limit])
    connection.execute("SET preserve_insertion_order = false")
    # Scoring streams from one connection while committing result batches on
    # another. DuckDB's 16 MiB default can trigger an automatic checkpoint
    # while that reader is still open, blocking both sides. The scoring stages
    # explicitly checkpoint after closing their readers instead.
    connection.execute("SET checkpoint_threshold = ?", [DEFAULT_CHECKPOINT_THRESHOLD])
    connection.execute("PRAGMA enable_progress_bar=false")
    initialize(connection)
    return connection


def initialize(db: Any) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS tempo_observation (
          source_recording_mbid UUID NOT NULL,
          submission_id VARCHAR NOT NULL,
          bpm DOUBLE NOT NULL,
          first_peak_bpm DOUBLE,
          second_peak_bpm DOUBLE,
          onset_rate DOUBLE,
          source_snapshot DATE NOT NULL,
          PRIMARY KEY (source_recording_mbid, submission_id)
        );

        CREATE TABLE IF NOT EXISTS source_tempo_candidate (
          source_recording_mbid UUID PRIMARY KEY,
          bpm DOUBLE,
          observation_count INTEGER NOT NULL,
          dominant_cluster_support DOUBLE NOT NULL,
          spread_percent DOUBLE NOT NULL,
          octave_ambiguous BOOLEAN NOT NULL,
          histogram_peak_agreement DOUBLE NOT NULL,
          tempo_quality DOUBLE NOT NULL,
          exclusion_reason VARCHAR
        );

        CREATE TABLE IF NOT EXISTS recording_metadata (
          source_recording_mbid UUID PRIMARY KEY,
          canonical_recording_mbid UUID,
          title VARCHAR NOT NULL,
          artist_credit VARCHAR NOT NULL,
          primary_artist_mbid UUID,
          duration_ms INTEGER,
          first_release_date DATE,
          official_release BOOLEAN NOT NULL DEFAULT false,
          video BOOLEAN NOT NULL DEFAULT false,
          live BOOLEAN NOT NULL DEFAULT false,
          remix BOOLEAN NOT NULL DEFAULT false,
          demo BOOLEAN NOT NULL DEFAULT false,
          instrumental BOOLEAN NOT NULL DEFAULT false,
          karaoke BOOLEAN NOT NULL DEFAULT false,
          spoken_word BOOLEAN NOT NULL DEFAULT false,
          metadata_completeness DOUBLE NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS recording_release (
          source_recording_mbid UUID NOT NULL,
          release_mbid UUID NOT NULL,
          PRIMARY KEY (source_recording_mbid, release_mbid)
        );

        CREATE TABLE IF NOT EXISTS tempo_candidate (
          canonical_recording_mbid UUID PRIMARY KEY,
          selected_source_recording_mbid UUID NOT NULL,
          bpm DOUBLE,
          observation_count INTEGER NOT NULL,
          spread_percent DOUBLE NOT NULL,
          octave_ambiguous BOOLEAN NOT NULL,
          tempo_quality DOUBLE NOT NULL,
          exclusion_reason VARCHAR
        );

        CREATE TABLE IF NOT EXISTS popularity (
          recording_mbid UUID PRIMARY KEY,
          total_listener_count BIGINT,
          total_listen_count BIGINT,
          as_of TIMESTAMP NOT NULL
        );

        CREATE TABLE IF NOT EXISTS popularity_batch (
          batch_key VARCHAR PRIMARY KEY,
          completed_at TIMESTAMP NOT NULL,
          item_count INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS catalog_selection (
          canonical_recording_mbid UUID PRIMARY KEY,
          selected_source_recording_mbid UUID NOT NULL,
          title VARCHAR NOT NULL,
          artist_credit VARCHAR NOT NULL,
          primary_artist_mbid UUID,
          bpm DOUBLE NOT NULL,
          tempo_quality DOUBLE NOT NULL,
          listener_rank INTEGER NOT NULL,
          listener_count BIGINT,
          listen_count BIGINT,
          selection_score DOUBLE NOT NULL,
          artist_cap_override BOOLEAN NOT NULL DEFAULT false,
          flexible_match_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS build_stat (
          stage VARCHAR NOT NULL,
          metric VARCHAR NOT NULL,
          value BIGINT NOT NULL,
          PRIMARY KEY (stage, metric)
        );

        CREATE TABLE IF NOT EXISTS pipeline_stage (
          stage VARCHAR PRIMARY KEY,
          fingerprint VARCHAR NOT NULL,
          completed_at TIMESTAMP NOT NULL
        );
        """
    )


def set_stat(db: Any, stage: str, metric: str, value: int) -> None:
    db.execute(
        """
        INSERT INTO build_stat VALUES (?, ?, ?)
        ON CONFLICT (stage, metric) DO UPDATE SET value = excluded.value
        """,
        [stage, metric, int(value)],
    )
