from __future__ import annotations

import copy
import unittest

from song_pick.config import Config, DEFAULT_CONFIG_PATH, load_config
from song_pick.score_tempos import aggregate_tempo
from song_pick.select_catalog import Candidate, flexible_degrees, score_candidates, select
from song_pick.validate_catalog import validate_document


class TempoAggregationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = load_config().tempo

    def test_accepts_a_tight_cluster(self) -> None:
        result = aggregate_tempo(
            [120.0, 120.1, 119.9],
            [120.0, 120.0, 120.0],
            [60.0, 60.0, 60.0],
            self.settings,
        )
        self.assertIsNone(result.exclusion_reason)
        self.assertAlmostEqual(result.bpm or 0, 120.0)
        self.assertGreater(result.quality, 0.8)

    def test_rejects_equal_half_and_double_tempo_support(self) -> None:
        result = aggregate_tempo(
            [60.0, 60.1, 120.0, 120.1],
            [None] * 4,
            [None] * 4,
            self.settings,
        )
        self.assertTrue(result.octave_ambiguous)
        self.assertEqual(result.exclusion_reason, "octave_ambiguous")

    def test_requires_two_observations(self) -> None:
        result = aggregate_tempo([100.0], [100.0], [50.0], self.settings)
        self.assertEqual(result.exclusion_reason, "insufficient_observations")


class SelectionTests(unittest.TestCase):
    def _candidate(self, index: int, bpm: float, artist: int) -> Candidate:
        suffix = f"{index:012d}"
        return Candidate(
            canonical_mbid=f"00000000-0000-4000-8000-{suffix}",
            source_mbid=f"10000000-0000-4000-8000-{suffix}",
            title=f"Song {index}",
            artist=f"Artist {artist}",
            primary_artist_mbid=f"20000000-0000-4000-8000-{artist:012d}",
            bpm=bpm,
            tempo_quality=0.8 + index / 100,
            listeners=1000 - index,
            listens=5000 - index,
            metadata_completeness=1,
        )

    def test_selection_is_deterministic_and_dense(self) -> None:
        candidates = [self._candidate(index, 100 + index * 0.1, index // 2) for index in range(6)]
        settings = copy.deepcopy(load_config().selection)
        settings.update({"artistSoftCap": 2, "minimumFlexibleMatches": 2})
        ranked = score_candidates(candidates, settings)
        first, degrees = select(ranked, 6, settings)
        second, _ = select(ranked, 6, settings)
        self.assertEqual([item.canonical_mbid for item in first], [item.canonical_mbid for item in second])
        self.assertTrue(all(degrees[item.canonical_mbid] >= 2 for item in first))

    def test_flexible_degree_uses_relative_two_percent_window(self) -> None:
        candidates = [self._candidate(0, 100, 0), self._candidate(1, 102, 1), self._candidate(2, 102.1, 2)]
        degrees = flexible_degrees(candidates, 2)
        self.assertEqual(degrees[candidates[0].canonical_mbid], 1)

    def test_listener_rank_uses_listens_as_the_secondary_signal(self) -> None:
        first = self._candidate(0, 100, 0)
        second = self._candidate(1, 100.1, 1)
        first = Candidate(**{**first.__dict__, "listeners": 100, "listens": 200})
        second = Candidate(**{**second.__dict__, "listeners": 100, "listens": 300})
        ranked = score_candidates([first, second], load_config().selection)
        ranks = {candidate.canonical_mbid: candidate.listener_rank for candidate in ranked}
        self.assertEqual(ranks[second.canonical_mbid], 1)


class ValidationTests(unittest.TestCase):
    def test_validates_a_small_catalog(self) -> None:
        raw = copy.deepcopy(load_config().raw)
        raw["targetSize"] = 3
        raw["selection"]["minimumFlexibleMatches"] = 2
        config = Config(raw, DEFAULT_CONFIG_PATH)
        songs = [
            {
                "id": f"00000000-0000-4000-8000-{index:012d}",
                "title": f"Song {index}",
                "artist": "Artist",
                "genres": ["rock"],
                "bpm": 100 + index * 0.1,
                "tempoQuality": 0.9,
                "listenerRank": index + 1,
            }
            for index in range(3)
        ]
        self.assertEqual(
            validate_document(
                {"version": 2, "generatedAt": "2026-09-12T00:00:00Z", "songs": songs},
                config,
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
