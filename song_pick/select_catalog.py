from __future__ import annotations

import bisect
import math
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Iterable

from .config import Config
from .db import columnar_parameters, set_stat, transaction


@dataclass(frozen=True)
class Candidate:
    canonical_mbid: str
    source_mbid: str
    title: str
    artist: str
    primary_artist_mbid: str | None
    bpm: float
    tempo_quality: float
    listeners: int
    listens: int
    metadata_completeness: float
    listener_percentile: float = 0
    listen_percentile: float = 0
    listener_rank: int = 0
    score: float = 0
    cap_override: bool = False

    @property
    def artist_key(self) -> str:
        return self.primary_artist_mbid or f"credit:{_normalise(self.artist)}"

    @property
    def song_key(self) -> str:
        return f"{_normalise(self.title)}|{_normalise(self.artist)}"


def _normalise(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    return "".join(character for character in decomposed if character.isalnum())


def _percentiles(values: list[int]) -> dict[int, float]:
    transformed = sorted(math.log1p(max(0, value)) for value in values)
    denominator = max(1, len(transformed) - 1)
    return {
        value: bisect.bisect_right(transformed, math.log1p(max(0, value)) - 1e-15) / denominator
        for value in set(values)
    }


def score_candidates(candidates: list[Candidate], settings: dict[str, Any]) -> list[Candidate]:
    if not candidates:
        return []
    listener_percentiles = _percentiles([candidate.listeners for candidate in candidates])
    listen_percentiles = _percentiles([candidate.listens for candidate in candidates])
    listener_order = sorted(
        candidates,
        key=lambda candidate: (
            -candidate.listeners,
            -candidate.listens,
            candidate.canonical_mbid,
        ),
    )
    ranks = {candidate.canonical_mbid: index + 1 for index, candidate in enumerate(listener_order)}
    width = float(settings["bucketWidthBpm"])
    buckets = Counter(math.floor(candidate.bpm / width) for candidate in candidates)
    largest_bucket = max(buckets.values())
    bonus_size = float(settings["underrepresentedBucketBonus"])

    output = []
    for candidate in candidates:
        listener_percentile = listener_percentiles[candidate.listeners]
        listen_percentile = listen_percentiles[candidate.listens]
        bucket = math.floor(candidate.bpm / width)
        variety_bonus = bonus_size * (1 - buckets[bucket] / largest_bucket)
        score = (
            0.60 * listener_percentile
            + 0.15 * listen_percentile
            + 0.20 * candidate.tempo_quality
            + 0.05 * candidate.metadata_completeness
            + variety_bonus
        )
        output.append(
            replace(
                candidate,
                listener_percentile=listener_percentile,
                listen_percentile=listen_percentile,
                listener_rank=ranks[candidate.canonical_mbid],
                score=score,
            )
        )
    return sorted(output, key=lambda candidate: (-candidate.score, candidate.canonical_mbid))


def flexible_degrees(candidates: Iterable[Candidate], tolerance_percent: float) -> dict[str, int]:
    ordered = sorted(candidates, key=lambda candidate: (candidate.bpm, candidate.canonical_mbid))
    bpms = [candidate.bpm for candidate in ordered]
    tolerance = tolerance_percent / 100
    degrees: dict[str, int] = {}
    for candidate in ordered:
        lower = bisect.bisect_left(bpms, candidate.bpm * (1 - tolerance))
        upper = bisect.bisect_right(bpms, candidate.bpm * (1 + tolerance))
        degrees[candidate.canonical_mbid] = upper - lower - 1
    return degrees


def _can_add(candidate: Candidate, selected: list[Candidate], minimum: int, tolerance: float) -> bool:
    if minimum == 0:
        return True
    lower = candidate.bpm * (1 - tolerance / 100)
    upper = candidate.bpm * (1 + tolerance / 100)
    return sum(lower <= current.bpm <= upper for current in selected) >= minimum


def select(
    ranked: list[Candidate],
    target: int,
    settings: dict[str, Any],
    allow_under_target: bool = False,
) -> tuple[list[Candidate], dict[str, int]]:
    cap = int(settings["artistSoftCap"])
    minimum = int(settings["minimumFlexibleMatches"])
    tolerance = float(settings["flexibleMatchPercent"])

    # Canonical IDs remove equivalent recordings; this second key removes duplicate
    # song entries that MusicBrainz has not canonicalised together.
    unique: list[Candidate] = []
    seen_songs: set[str] = set()
    for candidate in ranked:
        if candidate.song_key in seen_songs:
            continue
        seen_songs.add(candidate.song_key)
        unique.append(candidate)

    selected: list[Candidate] = []
    artist_counts: Counter[str] = Counter()
    for candidate in unique:
        if artist_counts[candidate.artist_key] >= cap:
            continue
        selected.append(candidate)
        artist_counts[candidate.artist_key] += 1
        if len(selected) == target:
            break

    # The cap is soft. If it alone prevents reaching the target, record each override.
    if len(selected) < target:
        selected_ids = {candidate.canonical_mbid for candidate in selected}
        for candidate in unique:
            if candidate.canonical_mbid in selected_ids:
                continue
            selected.append(replace(candidate, cap_override=True))
            selected_ids.add(candidate.canonical_mbid)
            if len(selected) == target:
                break

    # Remove isolated picks and refill only with candidates already connected to the
    # selected graph. Repeating lets replacements repair the neighbourhood in stages.
    for _ in range(10):
        degrees = flexible_degrees(selected, tolerance)
        bad = {mbid for mbid, degree in degrees.items() if degree < minimum}
        if not bad:
            break
        selected = [candidate for candidate in selected if candidate.canonical_mbid not in bad]
        artist_counts = Counter(candidate.artist_key for candidate in selected if not candidate.cap_override)
        selected_ids = {candidate.canonical_mbid for candidate in selected}
        for candidate in unique:
            if len(selected) >= target:
                break
            if candidate.canonical_mbid in selected_ids:
                continue
            if artist_counts[candidate.artist_key] >= cap:
                continue
            if not _can_add(candidate, selected, minimum, tolerance):
                continue
            selected.append(candidate)
            selected_ids.add(candidate.canonical_mbid)
            artist_counts[candidate.artist_key] += 1

    if len(selected) < target:
        selected_ids = {candidate.canonical_mbid for candidate in selected}
        for candidate in unique:
            if len(selected) >= target:
                break
            if candidate.canonical_mbid in selected_ids:
                continue
            if not _can_add(candidate, selected, minimum, tolerance):
                continue
            selected.append(replace(candidate, cap_override=True))
            selected_ids.add(candidate.canonical_mbid)
    degrees = flexible_degrees(selected, tolerance)
    bad = {mbid for mbid, degree in degrees.items() if degree < minimum}
    if bad:
        raise RuntimeError(f"selection contains {len(bad)} recordings below the match-degree minimum")
    if len(selected) < target and not allow_under_target:
        raise RuntimeError(
            f"only {len(selected):,} recordings satisfy quality, diversity, and match-density rules; "
            "use --allow-under-target only after reviewing the report"
        )
    selected.sort(key=lambda candidate: (candidate.listener_rank, candidate.canonical_mbid))
    return selected, degrees


def _load_eligible(db: Any, config: Config) -> list[Candidate]:
    selection = config.selection
    rows = db.execute(
        """
        SELECT t.canonical_recording_mbid::VARCHAR,
               t.selected_source_recording_mbid::VARCHAR,
               m.title, m.artist_credit, m.primary_artist_mbid::VARCHAR,
               t.bpm, t.tempo_quality,
               COALESCE(p.total_listener_count, 0), COALESCE(p.total_listen_count, 0),
               m.metadata_completeness
        FROM tempo_candidate t
        JOIN recording_metadata m
          ON m.source_recording_mbid = t.selected_source_recording_mbid
        LEFT JOIN popularity p ON p.recording_mbid = t.canonical_recording_mbid
        WHERE t.exclusion_reason IS NULL
          AND trim(m.title) <> '' AND trim(m.artist_credit) <> ''
          AND m.official_release AND NOT m.video AND NOT m.live AND NOT m.remix
          AND NOT m.demo AND NOT m.instrumental AND NOT m.karaoke AND NOT m.spoken_word
          AND (m.duration_ms IS NULL OR m.duration_ms BETWEEN ? AND ?)
        ORDER BY t.canonical_recording_mbid
        """,
        [int(selection["minimumDurationMs"]), int(selection["maximumDurationMs"])],
    ).fetchall()
    return [
        Candidate(
            canonical_mbid=row[0],
            source_mbid=row[1],
            title=row[2],
            artist=row[3],
            primary_artist_mbid=row[4],
            bpm=round(float(row[5]), 1),
            tempo_quality=float(row[6]),
            listeners=int(row[7]),
            listens=int(row[8]),
            metadata_completeness=float(row[9]),
        )
        for row in rows
    ]


def select_catalog(db: Any, config: Config, allow_under_target: bool = False) -> None:
    db.execute("DELETE FROM build_stat WHERE stage = 'selection_rejection'")
    minimum_duration = int(config.selection["minimumDurationMs"])
    maximum_duration = int(config.selection["maximumDurationMs"])
    rejection_rows = db.execute(
        """
        SELECT reason, count(*) FROM (
          SELECT CASE
            WHEN t.exclusion_reason IS NOT NULL THEN t.exclusion_reason
            WHEN m.source_recording_mbid IS NULL THEN 'missing_metadata'
            WHEN trim(m.title) = '' THEN 'empty_title'
            WHEN trim(m.artist_credit) = '' THEN 'empty_artist_credit'
            WHEN NOT m.official_release THEN 'no_official_release'
            WHEN m.video THEN 'video'
            WHEN m.live THEN 'live'
            WHEN m.remix THEN 'remix'
            WHEN m.demo THEN 'demo'
            WHEN m.instrumental THEN 'instrumental'
            WHEN m.karaoke THEN 'karaoke'
            WHEN m.spoken_word THEN 'spoken_word'
            WHEN m.duration_ms IS NOT NULL AND m.duration_ms NOT BETWEEN ? AND ? THEN 'implausible_duration'
            ELSE 'eligible'
          END AS reason
          FROM tempo_candidate t
          LEFT JOIN recording_metadata m
            ON m.source_recording_mbid = t.selected_source_recording_mbid
        ) classified
        GROUP BY reason ORDER BY reason
        """,
        [minimum_duration, maximum_duration],
    ).fetchall()
    for reason, count in rejection_rows:
        if reason != "eligible":
            set_stat(db, "selection_rejection", reason, int(count))
    eligible = _load_eligible(db, config)
    ranked = score_candidates(eligible, config.selection)
    selected, degrees = select(ranked, config.target_size, config.selection, allow_under_target)
    selection_rows = [
        (
            candidate.canonical_mbid,
            candidate.source_mbid,
            candidate.title,
            candidate.artist,
            candidate.primary_artist_mbid,
            candidate.bpm,
            candidate.tempo_quality,
            candidate.listener_rank,
            candidate.listeners,
            candidate.listens,
            candidate.score,
            candidate.cap_override,
            degrees[candidate.canonical_mbid],
        )
        for candidate in selected
    ]
    with transaction(db):
        db.execute("DELETE FROM catalog_selection")
        if selection_rows:
            db.execute(
                """
                WITH batch AS (
                  SELECT unnest(?::VARCHAR[])::UUID AS canonical_recording_mbid,
                         unnest(?::VARCHAR[])::UUID AS selected_source_recording_mbid,
                         unnest(?::VARCHAR[]) AS title,
                         unnest(?::VARCHAR[]) AS artist_credit,
                         unnest(?::VARCHAR[])::UUID AS primary_artist_mbid,
                         unnest(?::DOUBLE[]) AS bpm,
                         unnest(?::DOUBLE[]) AS tempo_quality,
                         unnest(?::INTEGER[]) AS listener_rank,
                         unnest(?::BIGINT[]) AS listener_count,
                         unnest(?::BIGINT[]) AS listen_count,
                         unnest(?::DOUBLE[]) AS selection_score,
                         unnest(?::BOOLEAN[]) AS artist_cap_override,
                         unnest(?::INTEGER[]) AS flexible_match_count
                )
                INSERT INTO catalog_selection SELECT * FROM batch
                """,
                columnar_parameters(selection_rows),
            )
    set_stat(db, "selection", "eligible", len(eligible))
    set_stat(db, "selection", "deduplicated_eligible", len({candidate.song_key for candidate in ranked}))
    set_stat(db, "selection", "selected", len(selected))
    set_stat(db, "selection", "artist_cap_overrides", sum(candidate.cap_override for candidate in selected))
    print(f"selected {len(selected):,} songs from {len(eligible):,} eligible candidates")
