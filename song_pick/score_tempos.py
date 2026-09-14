from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .config import Config
from .db import columnar_parameters, set_stat, transaction


FETCH_SIZE = 100_000
INSERT_BATCH_SIZE = 10_000
PROGRESS_INTERVAL_SECONDS = 30.0
SOURCE_BATCH_INSERT = """
    WITH batch AS (
      SELECT unnest(?::UUID[]) AS source_recording_mbid,
             unnest(?::DOUBLE[]) AS bpm,
             unnest(?::INTEGER[]) AS observation_count,
             unnest(?::DOUBLE[]) AS dominant_cluster_support,
             unnest(?::DOUBLE[]) AS spread_percent,
             unnest(?::BOOLEAN[]) AS octave_ambiguous,
             unnest(?::DOUBLE[]) AS histogram_peak_agreement,
             unnest(?::DOUBLE[]) AS tempo_quality,
             unnest(?::VARCHAR[]) AS exclusion_reason
    )
    INSERT INTO source_tempo_candidate SELECT * FROM batch
"""
CANONICAL_BATCH_INSERT = """
    WITH batch AS (
      SELECT unnest(?::UUID[]) AS canonical_recording_mbid,
             unnest(?::UUID[]) AS selected_source_recording_mbid,
             unnest(?::DOUBLE[]) AS bpm,
             unnest(?::INTEGER[]) AS observation_count,
             unnest(?::DOUBLE[]) AS spread_percent,
             unnest(?::BOOLEAN[]) AS octave_ambiguous,
             unnest(?::DOUBLE[]) AS tempo_quality,
             unnest(?::VARCHAR[]) AS exclusion_reason
    )
    INSERT INTO tempo_candidate SELECT * FROM batch
"""


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


class _Progress:
    def __init__(self, label: str, expected: int):
        self.label = label
        self.expected = expected
        self.started = time.monotonic()
        self.last_reported = self.started

    def report(self, completed: int, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_reported < PROGRESS_INTERVAL_SECONDS:
            return
        elapsed = max(now - self.started, 0.001)
        rate = completed / elapsed
        percentage = 100.0 if self.expected == 0 else 100 * completed / self.expected
        eta = _format_duration((self.expected - completed) / rate) if rate else "unknown"
        print(
            f"{self.label}: {completed:,}/{self.expected:,} "
            f"({percentage:.1f}%), {rate:,.0f}/s, ETA {eta}",
            flush=True,
        )
        self.last_reported = now


def _insert_batch(db: Any, statement: str, rows: list[tuple[object, ...]]) -> None:
    """Insert a columnar batch with one set-based statement."""
    if not rows:
        return
    with transaction(db):
        db.execute(statement, columnar_parameters(rows))


def _checkpoint(db: Any, label: str) -> None:
    print(f"{label}: checkpointing completed batches...", flush=True)
    started = time.monotonic()
    db.execute("CHECKPOINT")
    print(
        f"{label}: checkpoint complete ({_format_duration(time.monotonic() - started)})",
        flush=True,
    )


@dataclass(frozen=True)
class TempoResult:
    bpm: float | None
    observation_count: int
    dominant_support: float
    spread_percent: float
    octave_ambiguous: bool
    histogram_agreement: float
    quality: float
    exclusion_reason: str | None


@dataclass
class _Cluster:
    values: list[float]

    @property
    def center(self) -> float:
        return statistics.median(self.values)


def _relative_difference(left: float, right: float) -> float:
    return abs(left - right) / right


def _cluster(values: Iterable[float], tolerance: float) -> list[_Cluster]:
    clusters: list[_Cluster] = []
    for value in sorted(values):
        if clusters and _relative_difference(value, clusters[-1].center) <= tolerance:
            clusters[-1].values.append(value)
        else:
            clusters.append(_Cluster([value]))
    return clusters


def _octave_related(left: float, right: float, tolerance: float) -> bool:
    return min(_relative_difference(left * factor, right) for factor in (0.5, 1.0, 2.0)) <= tolerance


def _families(clusters: Sequence[_Cluster], tolerance: float) -> list[list[_Cluster]]:
    parents = list(range(len(clusters)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for left in range(len(clusters)):
        for right in range(left + 1, len(clusters)):
            if _octave_related(clusters[left].center, clusters[right].center, tolerance):
                union(left, right)

    grouped: dict[int, list[_Cluster]] = {}
    for index, cluster in enumerate(clusters):
        grouped.setdefault(find(index), []).append(cluster)
    return list(grouped.values())


def _normalise_octave(value: float, minimum: float, maximum: float) -> float | None:
    options = [value * factor for factor in (1.0, 0.5, 2.0) if minimum <= value * factor <= maximum]
    if not options:
        return None
    midpoint = (minimum + maximum) / 2
    return min(options, key=lambda option: (option != value, abs(option - midpoint), option))


def _align(value: float, target: float) -> tuple[float, float]:
    aligned = min(
        (value * factor for factor in (0.5, 1.0, 2.0)),
        key=lambda option: (_relative_difference(option, target), option),
    )
    return aligned, _relative_difference(aligned, target)


def aggregate_tempo(
    observations: Sequence[float],
    first_peaks: Sequence[float | None],
    second_peaks: Sequence[float | None],
    settings: dict[str, Any],
) -> TempoResult:
    raw_min = float(settings["rawMinBpm"])
    raw_max = float(settings["rawMaxBpm"])
    values = [value for value in observations if math.isfinite(value) and raw_min <= value <= raw_max]
    count = len(values)
    minimum_count = int(settings["minimumObservations"])
    tolerance = float(settings["clusterTolerancePercent"]) / 100
    minimum = float(settings["minBpm"])
    maximum = float(settings["maxBpm"])

    if not values:
        return TempoResult(None, 0, 0, 100, False, 0, 0, "no_usable_observations")

    clusters = _cluster(values, tolerance)
    families = _families(clusters, tolerance)
    dominant = min(
        families,
        key=lambda family: (-sum(len(cluster.values) for cluster in family), min(cluster.center for cluster in family)),
    )
    representative = min(
        dominant,
        key=lambda cluster: (-len(cluster.values), cluster.center),
    )
    target = _normalise_octave(representative.center, minimum, maximum)
    if target is None:
        return TempoResult(None, count, 0, 100, False, 0, 0, "bpm_out_of_range")

    aligned_with_distance = [_align(value, target) for value in values]
    supported = [aligned for aligned, distance in aligned_with_distance if distance <= tolerance]
    support = len(supported) / count
    bpm = statistics.median(supported) if supported else target
    absolute_deviations = [abs(value - bpm) for value in supported]
    mad = statistics.median(absolute_deviations) if absolute_deviations else math.inf
    spread = 100 * mad / bpm if bpm else 100

    cluster_sizes = sorted((len(cluster.values) for cluster in dominant), reverse=True)
    ambiguous = (
        len(cluster_sizes) > 1
        and cluster_sizes[1] / cluster_sizes[0] >= float(settings["octaveAmbiguityRatio"])
    )

    histogram_values = [
        value
        for value in (*first_peaks, *second_peaks)
        if value is not None and math.isfinite(value) and value > 0
    ]
    histogram_tolerance = float(settings["histogramTolerancePercent"]) / 100
    histogram_agreement = (
        sum(_align(value, bpm)[1] <= histogram_tolerance for value in histogram_values)
        / len(histogram_values)
        if histogram_values
        else 0.5
    )

    maximum_spread = float(settings["maximumSpreadPercent"])
    spread_score = max(0.0, 1 - spread / maximum_spread) if maximum_spread else float(spread == 0)
    observation_score = min(count / int(settings["observationCountCap"]), 1.0)
    quality = min(
        1.0,
        max(0.0, 0.45 * support + 0.30 * spread_score + 0.15 * observation_score + 0.10 * histogram_agreement),
    )

    reason: str | None = None
    if count < minimum_count:
        reason = "insufficient_observations"
    elif support < float(settings["minimumDominantSupport"]):
        reason = "insufficient_dominant_support"
    elif spread > maximum_spread:
        reason = "excessive_spread"
    elif ambiguous:
        reason = "octave_ambiguous"
    elif not minimum <= bpm <= maximum:
        reason = "bpm_out_of_range"

    return TempoResult(
        round(bpm, 6),
        count,
        support,
        spread,
        ambiguous,
        histogram_agreement,
        quality,
        reason,
    )


def score_source_tempos(db: Any, config: Config) -> None:
    db.execute("DELETE FROM source_tempo_candidate")
    db.execute("DELETE FROM build_stat WHERE stage = 'tempo_rejection'")
    expected = int(
        db.execute(
            "SELECT count(DISTINCT source_recording_mbid) FROM tempo_observation"
        ).fetchone()[0]
    )
    progress = _Progress("source tempo scoring", expected)
    cursor = db.cursor()
    print(
        f"scoring {expected:,} source recordings with a bounded-memory ordered scan...",
        flush=True,
    )
    cursor.execute(
        """
        SELECT source_recording_mbid, bpm, first_peak_bpm, second_peak_bpm
        FROM tempo_observation
        ORDER BY source_recording_mbid
        """
    )
    batch: list[tuple[object, ...]] = []
    reasons: dict[str, int] = {}
    total = 0
    current_mbid: object | None = None
    observations: list[float] = []
    first_peaks: list[float | None] = []
    second_peaks: list[float | None] = []

    def score_recording() -> None:
        nonlocal total
        if current_mbid is None:
            return
        result = aggregate_tempo(observations, first_peaks, second_peaks, config.tempo)
        batch.append(
            (
                current_mbid,
                result.bpm,
                result.observation_count,
                result.dominant_support,
                result.spread_percent,
                result.octave_ambiguous,
                result.histogram_agreement,
                result.quality,
                result.exclusion_reason,
            )
        )
        total += 1
        if result.exclusion_reason:
            reasons[result.exclusion_reason] = reasons.get(result.exclusion_reason, 0) + 1
        if len(batch) >= INSERT_BATCH_SIZE:
            _insert_batch(
                db,
                SOURCE_BATCH_INSERT,
                batch,
            )
            batch.clear()
            progress.report(total)

    while rows := cursor.fetchmany(FETCH_SIZE):
        for mbid, bpm, first_peak, second_peak in rows:
            if current_mbid is not None and mbid != current_mbid:
                score_recording()
                observations.clear()
                first_peaks.clear()
                second_peaks.clear()
            current_mbid = mbid
            observations.append(bpm)
            first_peaks.append(first_peak)
            second_peaks.append(second_peak)
    score_recording()
    if batch:
        _insert_batch(
            db,
            SOURCE_BATCH_INSERT,
            batch,
        )
        batch.clear()
    cursor.close()
    progress.report(total, force=True)
    _checkpoint(db, "source tempo scoring")

    set_stat(db, "tempo", "source_candidates", total)
    set_stat(db, "tempo", "accepted_source_candidates", total - sum(reasons.values()))
    for reason, value in reasons.items():
        set_stat(db, "tempo_rejection", reason, value)
    print(f"scored {total:,} source recordings; {sum(reasons.values()):,} rejected")


def score_canonical_tempos(db: Any, config: Config) -> None:
    db.execute("DELETE FROM tempo_candidate")
    expected_sources = int(
        db.execute("SELECT count(*) FROM source_tempo_candidate").fetchone()[0]
    )
    progress = _Progress("canonical tempo scoring", expected_sources)
    print(
        f"grouping {expected_sources:,} source candidates into canonical recordings...",
        flush=True,
    )
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT COALESCE(m.canonical_recording_mbid, s.source_recording_mbid) AS canonical_mbid,
               s.source_recording_mbid, s.bpm, s.observation_count,
               s.spread_percent, s.octave_ambiguous, s.tempo_quality,
               s.exclusion_reason, COALESCE(p.total_listener_count, 0),
               m.source_recording_mbid IS NOT NULL
                 AND trim(m.title) <> '' AND trim(m.artist_credit) <> ''
                 AND m.official_release AND NOT m.video AND NOT m.live AND NOT m.remix
                 AND NOT m.demo AND NOT m.instrumental AND NOT m.karaoke AND NOT m.spoken_word
                 AND (m.duration_ms IS NULL OR m.duration_ms BETWEEN ? AND ?) AS suitable
        FROM source_tempo_candidate s
        LEFT JOIN recording_metadata m USING (source_recording_mbid)
        LEFT JOIN popularity p ON p.recording_mbid = s.source_recording_mbid
        ORDER BY canonical_mbid, s.source_recording_mbid
        """,
        [
            int(config.selection["minimumDurationMs"]),
            int(config.selection["maximumDurationMs"]),
        ],
    )

    output: list[tuple[object, ...]] = []
    conflicts = 0
    total = 0
    processed_sources = 0
    current_canonical: object | None = None
    sources: list[object] = []
    bpms: list[float | None] = []
    counts: list[int] = []
    spreads: list[float] = []
    ambiguities: list[bool] = []
    qualities: list[float] = []
    exclusion_reasons: list[str | None] = []
    listeners: list[int] = []
    suitable: list[bool] = []

    def score_group() -> None:
        nonlocal conflicts, processed_sources, total
        if current_canonical is None:
            return
        accepted = [
            index
            for index, reason in enumerate(exclusion_reasons)
            if reason is None and bpms[index] is not None
        ]
        suitable_accepted = [index for index in accepted if suitable[index]]
        pool = suitable_accepted or accepted or list(range(len(sources)))
        maximum_listeners = max((math.log1p(listeners[index]) for index in pool), default=0)

        def provenance_score(index: int) -> float:
            popularity = math.log1p(listeners[index]) / maximum_listeners if maximum_listeners else 0
            return 0.7 * qualities[index] + 0.3 * popularity

        selected = min(
            pool,
            key=lambda index: (-provenance_score(index), str(sources[index])),
        )
        reason = exclusion_reasons[selected]
        bpm = bpms[selected]
        if accepted and bpm is not None:
            tolerance = float(config.tempo["canonicalConflictPercent"]) / 100
            if any(_align(bpms[index], bpm)[1] > tolerance for index in accepted if index != selected):
                reason = "canonical_tempo_conflict"
                conflicts += 1
        output.append(
            (
                current_canonical,
                sources[selected],
                bpm,
                sum(counts[index] for index in accepted) if accepted else counts[selected],
                max((spreads[index] for index in accepted), default=spreads[selected]),
                any(ambiguities[index] for index in accepted) if accepted else ambiguities[selected],
                qualities[selected],
                reason,
            )
        )
        total += 1
        processed_sources += len(sources)
        if len(output) >= INSERT_BATCH_SIZE:
            _insert_batch(
                db,
                CANONICAL_BATCH_INSERT,
                output,
            )
            output.clear()
            progress.report(processed_sources)

    while rows := cursor.fetchmany(FETCH_SIZE):
        for row in rows:
            canonical = row[0]
            if current_canonical is not None and canonical != current_canonical:
                score_group()
                sources.clear()
                bpms.clear()
                counts.clear()
                spreads.clear()
                ambiguities.clear()
                qualities.clear()
                exclusion_reasons.clear()
                listeners.clear()
                suitable.clear()
            current_canonical = canonical
            sources.append(row[1])
            bpms.append(row[2])
            counts.append(row[3])
            spreads.append(row[4])
            ambiguities.append(row[5])
            qualities.append(row[6])
            exclusion_reasons.append(row[7])
            listeners.append(row[8])
            suitable.append(row[9])
    score_group()
    if output:
        _insert_batch(
            db,
            CANONICAL_BATCH_INSERT,
            output,
        )
        output.clear()
    cursor.close()
    progress.report(processed_sources, force=True)
    _checkpoint(db, "canonical tempo scoring")

    accepted_count = int(
        db.execute(
            "SELECT count(*) FROM tempo_candidate WHERE exclusion_reason IS NULL"
        ).fetchone()[0]
    )
    set_stat(db, "tempo", "canonical_candidates", total)
    set_stat(db, "tempo", "accepted_canonical_candidates", accepted_count)
    set_stat(db, "tempo_rejection", "canonical_tempo_conflict", conflicts)
    print(f"formed {total:,} canonical tempo groups; {accepted_count:,} accepted")
