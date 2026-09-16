from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .config import Config, Paths


def catalog_document(db: Any, config: Config) -> dict[str, object]:
    rows = db.execute(
        """
        SELECT selection.canonical_recording_mbid::VARCHAR, selection.title,
               selection.artist_credit,
               COALESCE((
                 SELECT list(genre ORDER BY specificity DESC, vote_count DESC, lower(genre), genre)
                 FROM recording_genre_metadata genres
                 WHERE genres.source_recording_mbid = selection.selected_source_recording_mbid
               ), []::VARCHAR[]) AS genres,
               selection.bpm, selection.tempo_quality, selection.listener_rank
        FROM catalog_selection selection
        ORDER BY selection.listener_rank, selection.canonical_recording_mbid
        """
    ).fetchall()
    return {
        "version": config.version,
        "generatedAt": config.generated_at,
        "songs": [
            {
                "id": row[0],
                "title": row[1],
                "artist": row[2],
                "genres": row[3],
                "bpm": round(float(row[4]), 1),
                "tempoQuality": round(float(row[5]), 4),
                "listenerRank": int(row[6]),
            }
            for row in rows
        ],
    }


def serialize(document: dict[str, object]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "minimum": None, "p25": None, "median": None, "p75": None, "maximum": None}
    ordered = sorted(values)

    def quantile(fraction: float) -> float:
        return ordered[round((len(ordered) - 1) * fraction)]

    return {
        "count": len(ordered),
        "minimum": ordered[0],
        "p25": quantile(0.25),
        "median": quantile(0.5),
        "p75": quantile(0.75),
        "maximum": ordered[-1],
    }


def _sources(config: Config, paths: Paths) -> list[dict[str, object]]:
    manifest = paths.raw / "source-manifest.json"
    if manifest.exists():
        with manifest.open(encoding="utf-8") as handle:
            recorded = {item["name"]: item for item in json.load(handle).get("sources", [])}
    else:
        recorded = {}
    result = []
    for source in config.sources:
        item = {
            "name": source.name,
            "url": source.url,
            "snapshot": source.snapshot,
            "license": source.license,
            "sha256": source.sha256,
        }
        if source.name in recorded:
            item["size"] = recorded[source.name].get("size")
        result.append(item)
    result.append(
        {
            "name": "listenbrainz-popularity",
            "url": config.popularity["url"],
            "snapshot": "cached per row in catalog.duckdb",
            "license": "CC0-1.0",
        }
    )
    return result


def build_report(db: Any, config: Config, paths: Paths, checksum: str) -> dict[str, object]:
    stats: dict[str, dict[str, int]] = {}
    for stage, metric, value in db.execute(
        "SELECT stage, metric, value FROM build_stat ORDER BY stage, metric"
    ).fetchall():
        stats.setdefault(stage, {})[metric] = int(value)
    rows = db.execute(
        """
        SELECT bpm, tempo_quality, listener_count, listen_count,
               flexible_match_count, artist_credit
        FROM catalog_selection
        ORDER BY canonical_recording_mbid
        """
    ).fetchall()
    bpm_values = [float(row[0]) for row in rows]
    quality_values = [float(row[1]) for row in rows]
    listeners = [float(row[2] or 0) for row in rows]
    listens = [float(row[3] or 0) for row in rows]
    degrees = [float(row[4]) for row in rows]
    width = float(config.selection["bucketWidthBpm"])
    histogram = Counter(int(value // width) * width for value in bpm_values)
    artists = Counter(str(row[5]) for row in rows)
    rejection_rows = db.execute(
        """
        SELECT stage, metric, value FROM build_stat
        WHERE stage LIKE '%rejection'
        ORDER BY stage, metric
        """
    ).fetchall()
    return {
        "catalogVersion": config.version,
        "generatedAt": config.generated_at,
        "sources": _sources(config, paths),
        "configuration": config.raw,
        "rowCounts": stats,
        "rejections": {
            stage: {
                reason: int(value)
                for row_stage, reason, value in rejection_rows
                if row_stage == stage
            }
            for stage in sorted({row[0] for row in rejection_rows})
        },
        "distributions": {
            "bpm": _distribution(bpm_values),
            "tempoQuality": _distribution(quality_values),
            "listenerCount": _distribution(listeners),
            "listenCount": _distribution(listens),
            "flexibleMatchDegree": _distribution(degrees),
        },
        "tempoHistogram": [
            {"fromBpm": bucket, "toBpm": bucket + width, "count": histogram[bucket]}
            for bucket in sorted(histogram)
        ],
        "artistConcentration": {
            "distinctArtists": len(artists),
            "songsPerArtist": [
                {"artist": artist, "count": count}
                for artist, count in sorted(artists.items(), key=lambda item: (-item[1], item[0]))
            ],
        },
        "finalCatalog": {"rowCount": len(rows), "sha256": checksum},
    }


def export_catalog(db: Any, config: Config, paths: Paths) -> tuple[Path, str]:
    paths.create()
    document = catalog_document(db, config)
    content = serialize(document)
    checksum = hashlib.sha256(content).hexdigest()
    filename = f"songs.v{config.version}.json"
    output_path = paths.public_catalog / filename
    metadata_path = paths.public_catalog / f"songs.v{config.version}.meta.json"
    if output_path.exists() and output_path.read_bytes() != content:
        development_fixture = False
        if metadata_path.exists():
            try:
                development_fixture = bool(json.loads(metadata_path.read_text(encoding="utf-8")).get("developmentFixture"))
            except (json.JSONDecodeError, OSError):
                development_fixture = False
        if not development_fixture:
            raise RuntimeError(
                f"refusing to change immutable catalog version {config.version}; bump catalogVersion first"
            )
    _atomic_write(output_path, content)

    sources = _sources(config, paths)
    popularity_as_of = db.execute("SELECT max(as_of)::VARCHAR FROM popularity").fetchone()[0]
    metadata = {
        "version": config.version,
        "generatedAt": config.generated_at,
        "catalogFile": filename,
        "catalogSha256": checksum,
        "songCount": len(document["songs"]),
        "popularityAsOf": popularity_as_of,
        "sources": sources,
        "configuration": {"tempo": config.tempo, "selection": config.selection},
    }
    _atomic_write(
        metadata_path,
        (json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
    )
    report = build_report(db, config, paths, checksum)
    _atomic_write(
        paths.reports / "catalog-build.json",
        (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
    )
    print(f"exported {len(document['songs']):,} songs to {output_path} ({checksum})")
    return output_path, checksum
