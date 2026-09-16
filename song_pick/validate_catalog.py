from __future__ import annotations

import hashlib
import json
import math
import uuid
from pathlib import Path
from typing import Any

from .config import Config, Paths


class ValidationError(RuntimeError):
    pass


def validate_document(
    document: dict[str, Any], config: Config, allow_under_target: bool = False
) -> list[str]:
    errors: list[str] = []
    if document.get("version") != config.version:
        errors.append(f"version must be {config.version}")
    if document.get("generatedAt") != config.generated_at:
        errors.append(f"generatedAt must be {config.generated_at}")
    songs = document.get("songs")
    if not isinstance(songs, list):
        return ["songs must be an array"]
    if len(songs) != config.target_size and not (allow_under_target and len(songs) < config.target_size):
        errors.append(f"catalog must contain exactly {config.target_size} songs, found {len(songs)}")

    seen: set[str] = set()
    minimum = float(config.tempo["minBpm"])
    maximum = float(config.tempo["maxBpm"])
    for index, song in enumerate(songs):
        prefix = f"songs[{index}]"
        identifier = song.get("id")
        try:
            uuid.UUID(str(identifier))
        except (ValueError, TypeError, AttributeError):
            errors.append(f"{prefix}.id is not a valid UUID")
        identifier_key = str(identifier)
        if identifier_key in seen:
            errors.append(f"{prefix}.id is duplicated")
        seen.add(identifier_key)
        if not isinstance(song.get("title"), str) or not song["title"].strip():
            errors.append(f"{prefix}.title is empty")
        if not isinstance(song.get("artist"), str) or not song["artist"].strip():
            errors.append(f"{prefix}.artist is empty")
        genres = song.get("genres")
        if (
            not isinstance(genres, list)
            or len(genres) > 3
            or any(not isinstance(genre, str) or not genre.strip() for genre in genres)
            or len(set(genres)) != len(genres)
        ):
            errors.append(f"{prefix}.genres must contain up to three unique non-empty strings")
        bpm = song.get("bpm")
        if isinstance(bpm, bool) or not isinstance(bpm, (int, float)) or not math.isfinite(bpm) or not minimum <= bpm <= maximum:
            errors.append(f"{prefix}.bpm is outside [{minimum}, {maximum}]")
        quality = song.get("tempoQuality")
        if isinstance(quality, bool) or not isinstance(quality, (int, float)) or not math.isfinite(quality) or not 0 <= quality <= 1:
            errors.append(f"{prefix}.tempoQuality is outside [0, 1]")
        rank = song.get("listenerRank")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
            errors.append(f"{prefix}.listenerRank is not a positive integer")

    expected_order = sorted(
        songs,
        key=lambda song: (
            song.get("listenerRank") if isinstance(song.get("listenerRank"), int) else math.inf,
            str(song.get("id")),
        ),
    )
    if songs != expected_order:
        errors.append("songs are not sorted by listenerRank and recording ID")

    tolerance = float(config.selection["flexibleMatchPercent"]) / 100
    minimum_matches = int(config.selection["minimumFlexibleMatches"])
    for song in songs:
        bpm = song.get("bpm")
        if not isinstance(bpm, (int, float)) or bpm <= 0:
            continue
        degree = sum(
            other is not song
            and not isinstance(other.get("bpm"), bool)
            and isinstance(other.get("bpm"), (int, float))
            and abs((other["bpm"] - bpm) / bpm) <= tolerance
            for other in songs
        )
        if degree < minimum_matches:
            errors.append(f"{song.get('id')} has only {degree} flexible matches")
    return errors


def validate_catalog(
    config: Config,
    paths: Paths,
    db: Any | None = None,
    allow_under_target: bool = False,
) -> None:
    catalog_path = paths.public_catalog / f"songs.v{config.version}.json"
    metadata_path = paths.public_catalog / f"songs.v{config.version}.meta.json"
    if not catalog_path.exists():
        raise ValidationError(f"missing catalog: {catalog_path}")
    with catalog_path.open(encoding="utf-8") as handle:
        document = json.load(handle)
    errors = validate_document(document, config, allow_under_target)
    content = catalog_path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    if metadata_path.exists():
        with metadata_path.open(encoding="utf-8") as handle:
            expected = json.load(handle).get("catalogSha256")
        if expected != checksum:
            errors.append(f"catalog checksum is {checksum}, metadata says {expected}")
    else:
        errors.append(f"missing metadata: {metadata_path}")

    if db is not None:
        excluded = db.execute(
            """
            SELECT count(*) FROM catalog_selection s
            JOIN tempo_candidate t USING (canonical_recording_mbid)
            WHERE t.exclusion_reason IS NOT NULL
            """
        ).fetchone()[0]
        if excluded:
            errors.append(f"{excluded} selected recordings have an exclusion reason")
        unrecorded_caps = db.execute(
            """
            SELECT count(*) FROM (
              SELECT COALESCE(primary_artist_mbid::VARCHAR, artist_credit) AS artist,
                     count(*) AS songs, bool_or(artist_cap_override) AS recorded_override
              FROM catalog_selection GROUP BY artist
              HAVING count(*) > ? AND NOT bool_or(artist_cap_override)
            )
            """,
            [int(config.selection["artistSoftCap"])],
        ).fetchone()[0]
        if unrecorded_caps:
            errors.append(f"{unrecorded_caps} artists exceed the cap without a recorded override")
    if errors:
        raise ValidationError("catalog validation failed:\n- " + "\n- ".join(errors[:50]))
    print(f"validated {len(document['songs']):,} songs ({checksum})")
