from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .archive import open_tar, regular_member_file
from .config import Config, Paths
from .db import set_stat


def _extract_rhythm_csv(archive_path: Path, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    found = False
    with open_tar(archive_path) as archive:
        for member in archive:
            if "rhythm" not in member.name.lower() or not member.isfile():
                continue
            binary = regular_member_file(archive, member)
            if binary is None:
                continue
            found = True
            with binary, temporary.open("wb") as output:
                shutil.copyfileobj(binary, output, length=1024 * 1024)
            temporary.replace(destination)
            break
    if not found:
        raise RuntimeError(f"no rhythm CSV found in {archive_path}")


def import_acousticbrainz(db: Any, config: Config, paths: Paths) -> None:
    source = config.source("acousticbrainz-rhythm")
    archive_path = paths.raw / source.filename
    if not archive_path.exists():
        raise SystemExit(f"missing {archive_path}; run `python -m song_pick download` first")

    csv_path = paths.work / "acousticbrainz-rhythm.csv"
    _extract_rhythm_csv(archive_path, csv_path)
    db.execute("DELETE FROM tempo_observation")
    db.execute("DELETE FROM source_tempo_candidate")
    db.execute("DELETE FROM tempo_candidate")
    raw_min = float(config.tempo["rawMinBpm"])
    raw_max = float(config.tempo["rawMaxBpm"])
    relation = """
      read_csv(?, header=true, auto_detect=false, columns={
        'mbid':'VARCHAR', 'submission_offset':'VARCHAR', 'bpm':'VARCHAR',
        'bpm_histogram_first_peak_bpm_mean':'VARCHAR',
        'bpm_histogram_first_peak_bpm_median':'VARCHAR',
        'bpm_histogram_second_peak_bpm_mean':'VARCHAR',
        'bpm_histogram_second_peak_bpm_median':'VARCHAR',
        'danceability':'VARCHAR', 'onset_rate':'VARCHAR'
      })
    """
    parsed = f"""
      SELECT try_cast(mbid AS UUID) AS mbid,
             submission_offset,
             try_cast(bpm AS DOUBLE) AS bpm,
             try_cast(bpm_histogram_first_peak_bpm_median AS DOUBLE) AS first_peak,
             try_cast(bpm_histogram_second_peak_bpm_median AS DOUBLE) AS second_peak,
             try_cast(onset_rate AS DOUBLE) AS onset_rate
      FROM {relation}
    """
    db.execute(
        f"""
        INSERT OR REPLACE INTO tempo_observation
        SELECT mbid, submission_offset, bpm, first_peak, second_peak, onset_rate, ?::DATE
        FROM ({parsed}) source
        WHERE mbid IS NOT NULL AND isfinite(bpm) AND bpm BETWEEN ? AND ?
        """,
        [source.snapshot, str(csv_path), raw_min, raw_max],
    )
    inserted = int(db.execute("SELECT count(*) FROM tempo_observation").fetchone()[0])
    rejected = int(
        db.execute(
            f"""
            SELECT count(*) FROM ({parsed}) source
            WHERE mbid IS NULL OR bpm IS NULL OR NOT isfinite(bpm) OR bpm NOT BETWEEN ? AND ?
            """,
            [str(csv_path), raw_min, raw_max],
        ).fetchone()[0]
    )
    csv_path.unlink()
    set_stat(db, "acousticbrainz", "usable_observations", inserted)
    set_stat(db, "acousticbrainz", "implausible_observations", rejected)
    print(f"imported {inserted:,} AcousticBrainz observations ({rejected:,} rejected)")
