from __future__ import annotations

import contextlib
import csv
import re
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from .archive import open_tar, regular_member_file
from .config import Config, Paths
from .db import columnar_parameters, set_stat, transaction


BATCH_SIZE = 20_000
PROGRESS_ROW_INTERVAL = 1_000_000
CORE_PROGRESS_INTERVAL_SECONDS = 30.0
COPY_CHUNK_SIZE = 1024 * 1024
UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", re.I)
TITLE_QUALIFIER = re.compile(
    r"\s*[\[(](?:[^\])]*\b)?(live|demo|karaoke|instrumental|spoken[ -]word|interview|podcast|audiobook|remix|(?:club|dance|dub|extended) mix)\b[^\])]*[\])]\s*$",
    re.I,
)
CORE_MEMBERS = {
    "recording",
    "release",
    "release_status",
    "release_group_secondary_type",
    "release_group_secondary_type_join",
    "release_country",
    "release_unknown_country",
}


def _format_duration(seconds: float) -> str:
    remaining = max(0, round(seconds))
    days, remaining = divmod(remaining, 24 * 60 * 60)
    hours, remaining = divmod(remaining, 60 * 60)
    minutes, seconds = divmod(remaining, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _format_bytes(value: float) -> str:
    size = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


@contextlib.contextmanager
def _core_step(label: str) -> Iterator[None]:
    """Report long core-import operations even without a row-based counter."""
    started = time.monotonic()
    finished = threading.Event()
    print(f"core import: starting {label}", flush=True)

    def report_heartbeat() -> None:
        while not finished.wait(CORE_PROGRESS_INTERVAL_SECONDS):
            elapsed = _format_duration(time.monotonic() - started)
            print(
                f"core import: {label} still running ({elapsed} elapsed)",
                flush=True,
            )

    reporter = threading.Thread(target=report_heartbeat, daemon=True)
    reporter.start()
    try:
        yield
    except BaseException:
        elapsed = _format_duration(time.monotonic() - started)
        print(f"core import: {label} failed after {elapsed}", flush=True)
        raise
    else:
        elapsed = _format_duration(time.monotonic() - started)
        print(f"core import: completed {label} ({elapsed})", flush=True)
    finally:
        finished.set()
        reporter.join()


def _copy_with_progress(
    source: BinaryIO,
    output: BinaryIO,
    label: str,
    total_bytes: int,
) -> None:
    started = time.monotonic()
    last_reported = started
    copied = 0
    while chunk := source.read(COPY_CHUNK_SIZE):
        output.write(chunk)
        copied += len(chunk)
        now = time.monotonic()
        if now - last_reported < CORE_PROGRESS_INTERVAL_SECONDS:
            continue
        elapsed = max(now - started, 0.001)
        rate = copied / elapsed
        percentage = 100.0 if total_bytes == 0 else 100 * copied / total_bytes
        eta = _format_duration((total_bytes - copied) / rate) if rate else "unknown"
        print(
            f"core extraction: {label} {_format_bytes(copied)}/{_format_bytes(total_bytes)} "
            f"({percentage:.1f}%), {_format_bytes(rate)}/s, ETA {eta}",
            flush=True,
        )
        last_reported = now


def _title_flags(title: str) -> dict[str, bool]:
    qualifier = TITLE_QUALIFIER.search(title)
    value = qualifier.group(1).casefold() if qualifier else ""
    return {
        "live": value == "live",
        "remix": "remix" in value or " mix" in value,
        "demo": value == "demo",
        "instrumental": value == "instrumental",
        "karaoke": value == "karaoke",
        "spoken_word": value in {"spoken word", "spoken-word", "interview", "podcast", "audiobook"},
    }


def _first_uuid(value: str | None) -> str | None:
    match = UUID_PATTERN.search(value or "")
    return match.group(0) if match else None


def _flush_canonical(
    db: Any,
    metadata_rows: list[tuple[object, ...]],
    release_rows: list[tuple[str, str]],
    recovery: bool = False,
) -> int:
    if not metadata_rows and not release_rows:
        return 0
    metadata_parameters = columnar_parameters(metadata_rows)
    release_parameters = columnar_parameters(release_rows)
    target_join = (
        "JOIN _canonical_recovery_target target "
        "ON target.canonical_recording_mbid = grouped.canonical_recording_mbid::UUID"
        if recovery
        else "JOIN source_tempo_candidate target "
        "ON target.source_recording_mbid = grouped.source_recording_mbid::UUID"
    )
    source_expression = (
        "target.source_recording_mbid" if recovery else "grouped.source_recording_mbid::UUID"
    )
    canonical_expression = (
        "target.canonical_recording_mbid"
        if recovery
        else "grouped.canonical_recording_mbid::UUID"
    )
    with transaction(db):
        imported = 0
        if metadata_rows:
            imported = len(
                db.execute(
                    f"""
                WITH incoming AS (
                  SELECT unnest(?::VARCHAR[]) AS source_recording_mbid,
                         unnest(?::VARCHAR[]) AS canonical_recording_mbid,
                         unnest(?::VARCHAR[]) AS title,
                         unnest(?::VARCHAR[]) AS artist_credit,
                         unnest(?::VARCHAR[]) AS primary_artist_mbid,
                         unnest(?::BOOLEAN[]) AS live,
                         unnest(?::BOOLEAN[]) AS remix,
                         unnest(?::BOOLEAN[]) AS demo,
                         unnest(?::BOOLEAN[]) AS instrumental,
                         unnest(?::BOOLEAN[]) AS karaoke,
                         unnest(?::BOOLEAN[]) AS spoken_word
                ),
                grouped AS (
                  SELECT source_recording_mbid,
                         any_value(canonical_recording_mbid) AS canonical_recording_mbid,
                         any_value(title) AS title,
                         any_value(artist_credit) AS artist_credit,
                         any_value(primary_artist_mbid) AS primary_artist_mbid,
                         bool_or(live) AS live,
                         bool_or(remix) AS remix,
                         bool_or(demo) AS demo,
                         bool_or(instrumental) AS instrumental,
                         bool_or(karaoke) AS karaoke,
                         bool_or(spoken_word) AS spoken_word
                  FROM incoming
                  GROUP BY source_recording_mbid
                )
                INSERT INTO recording_metadata (
                  source_recording_mbid, canonical_recording_mbid, title, artist_credit,
                  primary_artist_mbid, live, remix, demo, instrumental, karaoke, spoken_word
                )
                SELECT {source_expression}, {canonical_expression},
                       grouped.title, grouped.artist_credit,
                       grouped.primary_artist_mbid::UUID, grouped.live, grouped.remix,
                       grouped.demo, grouped.instrumental, grouped.karaoke,
                       grouped.spoken_word
                FROM grouped
                {target_join}
                ON CONFLICT (source_recording_mbid) DO UPDATE SET
                  live = recording_metadata.live OR excluded.live,
                  remix = recording_metadata.remix OR excluded.remix,
                  demo = recording_metadata.demo OR excluded.demo,
                  instrumental = recording_metadata.instrumental OR excluded.instrumental,
                  karaoke = recording_metadata.karaoke OR excluded.karaoke,
                  spoken_word = recording_metadata.spoken_word OR excluded.spoken_word
                RETURNING source_recording_mbid
                """,
                    metadata_parameters,
                ).fetchall()
            )
        if release_rows:
            release_join = (
                "JOIN _canonical_recovery_target target "
                "ON target.canonical_recording_mbid = incoming.source_recording_mbid::UUID"
                if recovery
                else "JOIN source_tempo_candidate target "
                "ON target.source_recording_mbid = incoming.source_recording_mbid::UUID"
            )
            release_source = (
                "target.source_recording_mbid"
                if recovery
                else "incoming.source_recording_mbid::UUID"
            )
            db.execute(
                f"""
                WITH incoming AS (
                  SELECT unnest(?::VARCHAR[]) AS source_recording_mbid,
                         unnest(?::VARCHAR[]) AS release_mbid
                )
                INSERT OR IGNORE INTO recording_release
                SELECT DISTINCT {release_source}, incoming.release_mbid::UUID
                FROM incoming
                {release_join}
                WHERE incoming.release_mbid <> ''
                """,
                release_parameters,
            )
    metadata_rows.clear()
    release_rows.clear()
    return imported


def _flush_redirects(db: Any, rows: list[tuple[str, str]]) -> int:
    if not rows:
        return 0
    with transaction(db):
        imported = len(
            db.execute(
                """
                WITH incoming AS (
                  SELECT unnest(?::VARCHAR[]) AS source_recording_mbid,
                         unnest(?::VARCHAR[]) AS canonical_recording_mbid
                )
                INSERT INTO _candidate_canonical_redirect
                SELECT incoming.source_recording_mbid::UUID,
                       incoming.canonical_recording_mbid::UUID
                FROM incoming
                JOIN source_tempo_candidate candidate
                  ON candidate.source_recording_mbid = incoming.source_recording_mbid::UUID
                RETURNING source_recording_mbid
                """,
                columnar_parameters(rows),
            ).fetchall()
        )
    rows.clear()
    return imported


def _report_scan(label: str, scanned: int, matched: int) -> None:
    print(
        f"{label}: scanned {scanned:,} rows; matched {matched:,} candidates",
        flush=True,
    )


def _append_canonical_row(
    row: dict[str, str],
    source_recording: str,
    canonical_recording: str,
    metadata_rows: list[tuple[object, ...]],
    release_rows: list[tuple[str, str]],
) -> None:
    title = (row.get("recording_name") or "").strip()
    flags = _title_flags(title)
    metadata_rows.append(
        (
            source_recording,
            canonical_recording,
            title,
            (row.get("artist_credit_name") or "").strip(),
            _first_uuid(row.get("artist_mbids")),
            flags["live"],
            flags["remix"],
            flags["demo"],
            flags["instrumental"],
            flags["karaoke"],
            flags["spoken_word"],
        )
    )
    release = row.get("release_mbid", "")
    if release:
        release_rows.append((source_recording, release))


def import_canonical_data(db: Any, config: Config, paths: Paths) -> None:
    source = config.source("musicbrainz-canonical")
    archive_path = paths.raw / source.filename
    if not archive_path.exists():
        raise SystemExit(f"missing {archive_path}; run `python -m song_pick download` first")
    wanted_count = int(
        db.execute("SELECT count(*) FROM source_tempo_candidate").fetchone()[0]
    )
    print(
        f"canonical import: matching archive rows against {wanted_count:,} candidate IDs",
        flush=True,
    )
    with transaction(db):
        db.execute("DELETE FROM recording_release")
        db.execute("DELETE FROM recording_metadata")
    db.execute(
        """
        CREATE OR REPLACE TEMP TABLE _candidate_canonical_redirect (
          source_recording_mbid UUID,
          canonical_recording_mbid UUID
        )
        """
    )
    metadata_rows: list[tuple[object, ...]] = []
    release_rows: list[tuple[str, str]] = []
    redirect_rows: list[tuple[str, str]] = []
    metadata_hits = 0
    redirects = 0
    found_metadata = False
    found_redirects = False
    metadata_scanned = 0
    redirects_scanned = 0

    with open_tar(archive_path) as archive:
        for member in archive:
            basename = Path(member.name).name
            if basename not in {"canonical_musicbrainz_data.csv", "canonical_recording_redirect.csv"}:
                continue
            binary = regular_member_file(archive, member)
            if binary is None:
                continue
            with binary:
                reader = csv.DictReader(line.decode("utf-8") for line in binary)
                if basename == "canonical_musicbrainz_data.csv":
                    found_metadata = True
                    for row in reader:
                        metadata_scanned += 1
                        recording = row.get("recording_mbid", "")
                        if recording:
                            _append_canonical_row(
                                row, recording, recording, metadata_rows, release_rows
                            )
                            if len(metadata_rows) >= BATCH_SIZE:
                                metadata_hits += _flush_canonical(
                                    db, metadata_rows, release_rows
                                )
                        if metadata_scanned % PROGRESS_ROW_INTERVAL == 0:
                            _report_scan(
                                "canonical metadata", metadata_scanned, metadata_hits
                            )
                    metadata_hits += _flush_canonical(
                        db, metadata_rows, release_rows
                    )
                    if metadata_scanned % PROGRESS_ROW_INTERVAL:
                        _report_scan("canonical metadata", metadata_scanned, metadata_hits)
                else:
                    found_redirects = True
                    for row in reader:
                        redirects_scanned += 1
                        recording = row.get("recording_mbid", "")
                        canonical = row.get("canonical_recording_mbid", "")
                        if recording and canonical:
                            redirect_rows.append((recording, canonical))
                            if len(redirect_rows) >= BATCH_SIZE:
                                redirects += _flush_redirects(db, redirect_rows)
                        if redirects_scanned % PROGRESS_ROW_INTERVAL == 0:
                            _report_scan(
                                "canonical redirects", redirects_scanned, redirects
                            )
                    redirects += _flush_redirects(db, redirect_rows)
                    if redirects_scanned % PROGRESS_ROW_INTERVAL:
                        _report_scan("canonical redirects", redirects_scanned, redirects)

    if not found_metadata or not found_redirects:
        missing = []
        if not found_metadata:
            missing.append("canonical_musicbrainz_data.csv")
        if not found_redirects:
            missing.append("canonical_recording_redirect.csv")
        raise RuntimeError(f"missing canonical archive member(s): {', '.join(missing)}")

    db.execute(
        """
        CREATE OR REPLACE TEMP TABLE _candidate_canonical_redirect_unique AS
        SELECT source_recording_mbid,
               min(canonical_recording_mbid) AS canonical_recording_mbid
        FROM _candidate_canonical_redirect
        GROUP BY source_recording_mbid
        """
    )
    db.execute("DROP TABLE _candidate_canonical_redirect")
    redirects = int(
        db.execute(
            "SELECT count(*) FROM _candidate_canonical_redirect_unique"
        ).fetchone()[0]
    )
    with transaction(db):
        db.execute(
            """
            UPDATE recording_metadata AS metadata
            SET canonical_recording_mbid = redirect.canonical_recording_mbid
            FROM _candidate_canonical_redirect_unique AS redirect
            WHERE metadata.source_recording_mbid = redirect.source_recording_mbid
            """
        )
    db.execute(
        """
        CREATE OR REPLACE TEMP TABLE _canonical_recovery_target AS
        SELECT redirect.source_recording_mbid, redirect.canonical_recording_mbid
        FROM _candidate_canonical_redirect_unique AS redirect
        ANTI JOIN recording_metadata AS metadata
          ON metadata.source_recording_mbid = redirect.source_recording_mbid
        """
    )
    recovery_target_count = int(
        db.execute("SELECT count(*) FROM _canonical_recovery_target").fetchone()[0]
    )
    if recovery_target_count:
        # The canonical metadata CSV precedes the redirect CSV in the archive. A
        # second pass is therefore necessary only for AcousticBrainz IDs that
        # have since been merged into a current recording.
        recovery_scanned = 0
        recovery_hits = 0
        with open_tar(archive_path) as archive:
            for member in archive:
                if Path(member.name).name != "canonical_musicbrainz_data.csv":
                    continue
                binary = regular_member_file(archive, member)
                if binary is None:
                    continue
                with binary:
                    reader = csv.DictReader(line.decode("utf-8") for line in binary)
                    for row in reader:
                        recovery_scanned += 1
                        canonical = row.get("recording_mbid", "")
                        if canonical:
                            _append_canonical_row(
                                row, canonical, canonical, metadata_rows, release_rows
                            )
                            if len(metadata_rows) >= BATCH_SIZE:
                                imported = _flush_canonical(
                                    db, metadata_rows, release_rows, recovery=True
                                )
                                metadata_hits += imported
                                recovery_hits += imported
                        if recovery_scanned % PROGRESS_ROW_INTERVAL == 0:
                            _report_scan(
                                "canonical metadata recovery",
                                recovery_scanned,
                                recovery_hits,
                            )
                    imported = _flush_canonical(
                        db, metadata_rows, release_rows, recovery=True
                    )
                    metadata_hits += imported
                    recovery_hits += imported
                    if recovery_scanned % PROGRESS_ROW_INTERVAL:
                        _report_scan(
                            "canonical metadata recovery", recovery_scanned, recovery_hits
                        )
                break

    db.execute("DROP TABLE _candidate_canonical_redirect_unique")
    db.execute("DROP TABLE _canonical_recovery_target")

    metadata_count = int(db.execute("SELECT count(*) FROM recording_metadata").fetchone()[0])
    release_count = int(db.execute("SELECT count(*) FROM recording_release").fetchone()[0])
    set_stat(db, "musicbrainz", "canonical_metadata_rows", metadata_hits)
    set_stat(db, "musicbrainz", "candidate_recordings_with_metadata", metadata_count)
    set_stat(db, "musicbrainz", "candidate_recording_releases", release_count)
    set_stat(db, "musicbrainz", "canonical_redirects", redirects)
    set_stat(db, "musicbrainz", "missing_candidate_metadata", wanted_count - metadata_count)
    print("canonical import: checkpointing completed batches...", flush=True)
    db.execute("CHECKPOINT")
    print("canonical import: checkpoint complete", flush=True)
    print(f"imported {metadata_count:,} candidate recordings and applied {redirects:,} canonical redirects")


def _extract_core_tables(archive_path: Path, work: Path) -> dict[str, Path]:
    paths = {name: work / f"musicbrainz-{name}.tsv" for name in CORE_MEMBERS}
    found: set[str] = set()
    print(
        f"core extraction: scanning {archive_path.name} for {len(CORE_MEMBERS)} tables",
        flush=True,
    )
    with open_tar(archive_path) as archive:
        for member in archive:
            basename = Path(member.name).name
            if basename not in CORE_MEMBERS or not member.isfile():
                continue
            binary = regular_member_file(archive, member)
            if binary is None:
                continue
            temporary = paths[basename].with_suffix(".tsv.tmp")
            print(
                f"core extraction: extracting {basename} ({_format_bytes(member.size)})",
                flush=True,
            )
            with binary, temporary.open("wb") as output:
                _copy_with_progress(binary, output, basename, member.size)
            temporary.replace(paths[basename])
            found.add(basename)
            print(
                f"core extraction: extracted {basename} "
                f"({len(found)}/{len(CORE_MEMBERS)} tables)",
                flush=True,
            )
    missing = CORE_MEMBERS.difference(found)
    if missing:
        raise RuntimeError(f"missing core MusicBrainz table(s): {', '.join(sorted(missing))}")
    return paths


def _load_table(
    db: Any,
    name: str,
    path: Path,
    columns: list[str],
    selected: list[str],
    where: str = "true",
) -> None:
    definitions = ", ".join(f"'{column}':'VARCHAR'" for column in columns)
    projection = ", ".join(selected)
    with _core_step(f"loading filtered {name} table"):
        db.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE mb_{name} AS
            SELECT {projection} FROM read_csv(
              ?, delim='\t', header=false, auto_detect=false,
              columns={{{definitions}}}, nullstr='\\N', quote='', escape=''
            )
            WHERE {where}
            """,
            [str(path)],
        )


def import_core_data(db: Any, config: Config, paths: Paths) -> None:
    source = config.source("musicbrainz-core")
    archive_path = paths.raw / source.filename
    if not archive_path.exists():
        raise SystemExit(f"missing {archive_path}; run `python -m song_pick download` first")
    with _core_step("scanning and extracting the core archive"):
        tables = _extract_core_tables(archive_path, paths.work)
    schemas = {
        "recording": ["id", "gid", "name", "artist_credit", "length", "comment", "edits_pending", "last_updated", "video"],
        "release": ["id", "gid", "name", "artist_credit", "release_group", "status", "packaging", "language", "script", "barcode", "comment", "edits_pending", "quality", "last_updated"],
        "release_status": ["id", "name", "parent", "child_order", "description", "gid"],
        "release_group_secondary_type": ["id", "name", "parent", "child_order", "description", "gid"],
        "release_group_secondary_type_join": ["release_group", "secondary_type", "created"],
        "release_country": ["release", "country", "date_year", "date_month", "date_day"],
        "release_unknown_country": ["release", "date_year", "date_month", "date_day"],
    }
    _load_table(
        db,
        "recording",
        tables["recording"],
        schemas["recording"],
        ["gid", "length", "video"],
        "try_cast(gid AS UUID) IN ("
        "SELECT source_recording_mbid FROM recording_metadata UNION "
        "SELECT canonical_recording_mbid FROM recording_metadata)",
    )
    _load_table(
        db,
        "release",
        tables["release"],
        schemas["release"],
        ["id", "gid", "release_group", "status"],
        "try_cast(gid AS UUID) IN (SELECT release_mbid FROM recording_release)",
    )
    _load_table(
        db,
        "release_status",
        tables["release_status"],
        schemas["release_status"],
        ["id", "name"],
    )

    with _core_step("resolving recording duration and video metadata"):
        db.execute(
            """
            UPDATE recording_metadata AS metadata SET
              duration_ms = resolved.length,
              video = resolved.video
            FROM (
              WITH recording_lookup AS (
                SELECT source_recording_mbid,
                       source_recording_mbid AS recording_mbid,
                       1 AS priority
                FROM recording_metadata
                UNION ALL
                SELECT source_recording_mbid,
                       canonical_recording_mbid AS recording_mbid,
                       2 AS priority
                FROM recording_metadata
                WHERE canonical_recording_mbid IS NOT NULL
                  AND canonical_recording_mbid <> source_recording_mbid
              )
              SELECT lookup.source_recording_mbid,
                     try_cast(recording.length AS INTEGER) AS length,
                     COALESCE(try_cast(recording.video AS BOOLEAN), false) AS video,
                     row_number() OVER (
                       PARTITION BY lookup.source_recording_mbid
                       ORDER BY lookup.priority
                     ) AS priority
              FROM recording_lookup lookup
              JOIN mb_recording recording
                ON try_cast(recording.gid AS UUID) = lookup.recording_mbid
            ) resolved
            WHERE metadata.source_recording_mbid = resolved.source_recording_mbid
              AND resolved.priority = 1
            """
        )
    with _core_step("matching candidate recordings to releases"):
        db.execute(
            """
            CREATE OR REPLACE TEMP TABLE candidate_release AS
            SELECT rr.source_recording_mbid,
                   try_cast(release.id AS INTEGER) AS release_id,
                   try_cast(release.release_group AS INTEGER) AS release_group_id,
                   lower(status.name) AS status
            FROM recording_release rr
            JOIN mb_release release ON rr.release_mbid = try_cast(release.gid AS UUID)
            LEFT JOIN mb_release_status status
              ON try_cast(status.id AS INTEGER) = try_cast(release.status AS INTEGER)
            """
        )
    with _core_step("marking recordings with official releases"):
        db.execute("UPDATE recording_metadata SET official_release = false")
        db.execute(
            """
            UPDATE recording_metadata SET official_release = true
            FROM (SELECT DISTINCT source_recording_mbid FROM candidate_release WHERE status = 'official') official
            WHERE recording_metadata.source_recording_mbid = official.source_recording_mbid
            """
        )
    _load_table(
        db,
        "release_group_secondary_type_join",
        tables["release_group_secondary_type_join"],
        schemas["release_group_secondary_type_join"],
        ["release_group", "secondary_type"],
        "try_cast(release_group AS INTEGER) IN (SELECT release_group_id FROM candidate_release)",
    )
    _load_table(
        db,
        "release_group_secondary_type",
        tables["release_group_secondary_type"],
        schemas["release_group_secondary_type"],
        ["id", "name"],
        "try_cast(id AS INTEGER) IN (SELECT try_cast(secondary_type AS INTEGER) FROM mb_release_group_secondary_type_join)",
    )
    with _core_step("matching secondary release types"):
        db.execute(
            """
            CREATE OR REPLACE TEMP TABLE candidate_secondary_type AS
            SELECT candidate.source_recording_mbid, lower(secondary.name) AS name
            FROM candidate_release candidate
            JOIN mb_release_group_secondary_type_join type_join
              ON try_cast(type_join.release_group AS INTEGER) = candidate.release_group_id
            JOIN mb_release_group_secondary_type secondary
              ON try_cast(secondary.id AS INTEGER) = try_cast(type_join.secondary_type AS INTEGER)
            """
        )
    _load_table(
        db,
        "release_country",
        tables["release_country"],
        schemas["release_country"],
        ["release", "date_year", "date_month", "date_day"],
        "try_cast(release AS INTEGER) IN (SELECT release_id FROM candidate_release)",
    )
    _load_table(
        db,
        "release_unknown_country",
        tables["release_unknown_country"],
        schemas["release_unknown_country"],
        ["release", "date_year", "date_month", "date_day"],
        "try_cast(release AS INTEGER) IN (SELECT release_id FROM candidate_release)",
    )
    with _core_step("applying secondary release flags"):
        db.execute(
            """
            UPDATE recording_metadata AS metadata SET
              live = metadata.live OR flags.live,
              remix = metadata.remix OR flags.remix,
              demo = metadata.demo OR flags.demo,
              spoken_word = metadata.spoken_word OR flags.spoken_word
            FROM (
              SELECT source_recording_mbid,
                     bool_or(name = 'live') AS live,
                     bool_or(name IN ('remix', 'dj-mix')) AS remix,
                     bool_or(name = 'demo') AS demo,
                     bool_or(name IN ('spokenword', 'interview', 'audiobook', 'audio drama')) AS spoken_word
              FROM candidate_secondary_type GROUP BY source_recording_mbid
            ) flags
            WHERE metadata.source_recording_mbid = flags.source_recording_mbid
            """
        )
    with _core_step("resolving first official release dates"):
        db.execute(
            """
            CREATE OR REPLACE TEMP TABLE candidate_release_date AS
            SELECT candidate.source_recording_mbid,
                   min(make_date(
                     try_cast(dates.date_year AS INTEGER),
                     COALESCE(try_cast(dates.date_month AS INTEGER), 1),
                     COALESCE(try_cast(dates.date_day AS INTEGER), 1)
                   )) AS first_release_date
            FROM candidate_release candidate
            JOIN (
              SELECT release, date_year, date_month, date_day FROM mb_release_country
              UNION ALL
              SELECT release, date_year, date_month, date_day FROM mb_release_unknown_country
            ) dates ON try_cast(dates.release AS INTEGER) = candidate.release_id
            WHERE candidate.status = 'official'
              AND try_cast(dates.date_year AS INTEGER) BETWEEN 1000 AND 9999
            GROUP BY candidate.source_recording_mbid
            """
        )
        db.execute(
            """
            UPDATE recording_metadata AS metadata
            SET first_release_date = dates.first_release_date
            FROM candidate_release_date dates
            WHERE metadata.source_recording_mbid = dates.source_recording_mbid
            """
        )
    with _core_step("computing metadata completeness"):
        db.execute(
            """
            UPDATE recording_metadata SET metadata_completeness =
              (CASE WHEN duration_ms IS NOT NULL THEN 0.25 ELSE 0 END) +
              (CASE WHEN first_release_date IS NOT NULL THEN 0.25 ELSE 0 END) +
              (CASE WHEN primary_artist_mbid IS NOT NULL THEN 0.25 ELSE 0 END) +
              (CASE WHEN official_release THEN 0.25 ELSE 0 END)
            """
        )
    for path in tables.values():
        path.unlink()

    official = int(db.execute("SELECT count(*) FROM recording_metadata WHERE official_release").fetchone()[0])
    videos = int(db.execute("SELECT count(*) FROM recording_metadata WHERE video").fetchone()[0])
    set_stat(db, "musicbrainz", "official_recordings", official)
    set_stat(db, "musicbrainz", "video_recordings", videos)
    print(f"resolved core metadata: {official:,} official recordings; {videos:,} videos")
