from __future__ import annotations

import copy
import contextlib
import io
import json
import tarfile
import tempfile
import unittest
from http.client import RemoteDisconnected
from pathlib import Path
from unittest.mock import patch

try:
    import duckdb  # noqa: F401
except ImportError:
    duckdb = None

from song_pick.config import Config, DEFAULT_CONFIG_PATH, Paths, load_config
from song_pick.db import connect, transaction
from song_pick.export_catalog import export_catalog
from song_pick.fetch_popularity import fetch_popularity, request_popularity
from song_pick.import_acousticbrainz import import_acousticbrainz
from song_pick.import_musicbrainz import (
    _copy_with_progress,
    _flush_canonical,
    import_canonical_data,
    import_core_data,
    import_genre_data,
)
from song_pick.score_tempos import score_canonical_tempos, score_source_tempos
from song_pick.select_catalog import select_catalog
from song_pick.validate_catalog import validate_catalog


class PopularityRequestTests(unittest.TestCase):
    def test_remote_disconnect_is_retried(self) -> None:
        mbid = "00000000-0000-4000-8000-000000000001"
        expected = [{"recording_mbid": mbid, "total_user_count": 123}]
        settings = copy.deepcopy(load_config().popularity)
        settings["maximumRetries"] = 1

        with (
            patch(
                "song_pick.fetch_popularity.urlopen",
                side_effect=[
                    RemoteDisconnected("remote end closed connection"),
                    io.BytesIO(json.dumps(expected).encode()),
                ],
            ) as mocked_urlopen,
            patch("song_pick.fetch_popularity.time.sleep") as mocked_sleep,
        ):
            result = request_popularity([mbid], settings)

        self.assertEqual(result, expected)
        self.assertEqual(mocked_urlopen.call_count, 2)
        mocked_sleep.assert_called_once_with(settings["initialBackoffSeconds"])


@unittest.skipIf(duckdb is None, "catalog requirements are not installed")
class DuckDbPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.paths = Paths.under(self.root)
        self.paths.create()
        raw = copy.deepcopy(load_config().raw)
        raw["targetSize"] = 6
        raw["selection"]["minimumFlexibleMatches"] = 5
        raw["selection"]["artistSoftCap"] = 8
        self.config = Config(raw, DEFAULT_CONFIG_PATH)
        self.db = connect(self.paths.database)

    def tearDown(self) -> None:
        self.db.close()
        self.temporary.cleanup()

    def test_database_defaults_to_one_worker_thread(self) -> None:
        threads = self.db.execute("SELECT current_setting('threads')").fetchone()[0]
        self.assertEqual(threads, 1)
        memory_limit = self.db.execute(
            "SELECT current_setting('memory_limit')"
        ).fetchone()[0]
        self.assertEqual(memory_limit, "3.7 GiB")
        preserve_order = self.db.execute(
            "SELECT current_setting('preserve_insertion_order')"
        ).fetchone()[0]
        self.assertFalse(preserve_order)
        checkpoint_threshold = self.db.execute(
            "SELECT current_setting('checkpoint_threshold')"
        ).fetchone()[0]
        self.assertEqual(checkpoint_threshold, "953.6 MiB")

    def test_database_worker_thread_limit_is_configurable(self) -> None:
        other = connect(
            self.paths.work / "parallel.duckdb", threads=2, memory_limit="512MiB"
        )
        try:
            threads = other.execute("SELECT current_setting('threads')").fetchone()[0]
            self.assertEqual(threads, 2)
            memory_limit = other.execute(
                "SELECT current_setting('memory_limit')"
            ).fetchone()[0]
            self.assertEqual(memory_limit, "512.0 MiB")
        finally:
            other.close()

    def test_transaction_commits_and_rolls_back_batches(self) -> None:
        self.db.execute("CREATE TABLE transaction_test (value INTEGER)")
        with transaction(self.db):
            self.db.executemany(
                "INSERT INTO transaction_test VALUES (?)", [(1,), (2,)]
            )
        with self.assertRaisesRegex(RuntimeError, "rollback sentinel"):
            with transaction(self.db):
                self.db.executemany(
                    "INSERT INTO transaction_test VALUES (?)", [(3,), (4,)]
                )
                raise RuntimeError("rollback sentinel")
        self.assertEqual(
            self.db.execute(
                "SELECT value FROM transaction_test ORDER BY value"
            ).fetchall(),
            [(1,), (2,)],
        )

    def _tar_zstd(self, path: Path, members: dict[str, bytes]) -> None:
        import zstandard

        archive_bytes = io.BytesIO()
        with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
            for name, content in members.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        path.write_bytes(zstandard.ZstdCompressor().compress(archive_bytes.getvalue()))

    def _set_source_filename(self, name: str, filename: str) -> None:
        next(source for source in self.config.raw["sources"] if source["name"] == name)["filename"] = filename

    def _tar_bz2(self, path: Path, members: dict[str, bytes]) -> None:
        with tarfile.open(path, mode="w:bz2") as archive:
            for name, content in members.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))

    def test_acousticbrainz_import_bulk_loads_and_filters_rows(self) -> None:
        filename = "rhythm-test.tar.zst"
        self._set_source_filename("acousticbrainz-rhythm", filename)
        header = (
            "mbid,submission_offset,bpm,bpm_histogram_first_peak_bpm_mean,"
            "bpm_histogram_first_peak_bpm_median,bpm_histogram_second_peak_bpm_mean,"
            "bpm_histogram_second_peak_bpm_median,danceability,onset_rate\n"
        )
        mbid = "00000000-0000-4000-8000-000000000001"
        content = header + (
            f"{mbid},0,100,100,100,50,50,0.8,2.0\n"
            f"{mbid},1,100.1,100,100,50,50,0.8,2.1\n"
            f"{mbid},2,nan,100,100,50,50,0.8,2.1\n"
        )
        self._tar_zstd(self.paths.raw / filename, {"features/rhythm.csv": content.encode()})
        import_acousticbrainz(self.db, self.config, self.paths)
        self.assertEqual(self.db.execute("SELECT count(*) FROM tempo_observation").fetchone()[0], 2)
        self.assertEqual(
            self.db.execute(
                "SELECT value FROM build_stat WHERE stage='acousticbrainz' AND metric='implausible_observations'"
            ).fetchone()[0],
            1,
        )

    def test_musicbrainz_import_combines_recording_and_canonical_data(self) -> None:
        source_mbid = "00000000-0000-4000-8000-000000000001"
        canonical_mbid = "00000000-0000-4000-8000-000000000002"
        artist_mbid = "20000000-0000-4000-8000-000000000001"
        release_mbid = "30000000-0000-4000-8000-000000000001"
        self.db.execute(
            "INSERT INTO source_tempo_candidate VALUES (?, 100, 2, 1, 0, false, 1, 0.9, NULL)",
            [source_mbid],
        )
        canonical_filename = "canonical-test.tar.zst"
        self._set_source_filename("musicbrainz-canonical", canonical_filename)
        metadata = (
            "id,artist_credit_id,artist_mbids,artist_credit_name,release_mbid,release_name,"
            "recording_mbid,recording_name,combined_lookup,score\n"
            f"1,1,{artist_mbid},An Artist,{release_mbid},Release,"
            f"{canonical_mbid},A Song,anartistasong,1\n"
        ).encode()
        redirects = (
            "recording_mbid,canonical_recording_mbid,canonical_release_mbid\n"
            f"{source_mbid},{canonical_mbid},{release_mbid}\n"
        ).encode()
        self._tar_zstd(
            self.paths.raw / canonical_filename,
            {
                "canonical/canonical_musicbrainz_data.csv": metadata,
                "canonical/canonical_recording_redirect.csv": redirects,
            },
        )

        transactions = 0

        @contextlib.contextmanager
        def counted_transaction(db):
            nonlocal transactions
            transactions += 1
            with transaction(db):
                yield

        output = io.StringIO()
        with (
            patch("song_pick.import_musicbrainz.transaction", counted_transaction),
            contextlib.redirect_stdout(output),
        ):
            import_canonical_data(self.db, self.config, self.paths)
        self.assertGreaterEqual(transactions, 3)
        self.assertIn(
            "canonical import: matching archive rows against 1 candidate IDs",
            output.getvalue(),
        )
        self.assertIn("canonical metadata recovery: scanned 1 rows", output.getvalue())
        core_filename = "core-test.tar.bz2"
        self._set_source_filename("musicbrainz-core", core_filename)
        timestamp = "2026-09-12 00:00:00+00"
        status_gid = "40000000-0000-4000-8000-000000000001"
        release_group_gid = "50000000-0000-4000-8000-000000000001"
        secondary_gid = "60000000-0000-4000-8000-000000000001"
        genre_gid = "70000000-0000-4000-8000-000000000001"
        core_members = {
            "mbdump/artist": f"30\t{artist_mbid}\tAn Artist\tArtist, An\t\\N\t\\N\t\\N\t\\N\t\\N\t\\N\t\\N\t\\N\t\\N\t\t0\t{timestamp}\tf\t\\N\t\\N\n".encode(),
            "mbdump/artist_tag": f"30\t7\t5\t{timestamp}\n".encode(),
            "mbdump/genre": f"9\t{genre_gid}\thip hop\t\t0\t{timestamp}\n".encode(),
            "mbdump/recording": f"1\t{canonical_mbid}\tA Song\t1\t180000\t\t0\t{timestamp}\tf\n".encode(),
            "mbdump/recording_tag": f"1\t7\t3\t{timestamp}\n".encode(),
            "mbdump/release": f"10\t{release_mbid}\tRelease\t1\t20\t1\t\\N\t\\N\t\\N\t\\N\t\t0\t-1\t{timestamp}\n".encode(),
            "mbdump/release_status": f"1\tOfficial\t\\N\t0\tOfficial release\t{status_gid}\n".encode(),
            "mbdump/release_group": f"20\t{release_group_gid}\tRelease group\t1\t1\t\t0\t{timestamp}\n".encode(),
            "mbdump/release_group_secondary_type": f"2\tLive\t\\N\t0\tLive release\t{secondary_gid}\n".encode(),
            "mbdump/release_group_secondary_type_join": f"20\t2\t{timestamp}\n".encode(),
            "mbdump/release_group_tag": f"20\t7\t4\t{timestamp}\n".encode(),
            "mbdump/release_country": b"10\t222\t2020\t1\t2\n",
            "mbdump/release_unknown_country": b"10\t2020\t1\t2\n",
            "mbdump/tag": b"7\thip hop\t20\n",
        }
        self._tar_bz2(self.paths.raw / core_filename, core_members)
        core_output = io.StringIO()
        with contextlib.redirect_stdout(core_output):
            import_core_data(self.db, self.config, self.paths)
        self.assertIn(
            "core extraction: extracting recording",
            core_output.getvalue(),
        )
        self.assertIn(
            "core import: starting resolving recording duration and video metadata",
            core_output.getvalue(),
        )
        self.assertIn(
            "core import: completed computing metadata completeness",
            core_output.getvalue(),
        )
        derived_filename = "derived-test.tar.bz2"
        self._set_source_filename("musicbrainz-derived", derived_filename)
        self._tar_bz2(
            self.paths.raw / derived_filename,
            {
                "mbdump/artist_tag": core_members["mbdump/artist_tag"],
                "mbdump/recording_tag": core_members["mbdump/recording_tag"],
                "mbdump/release_group_tag": core_members["mbdump/release_group_tag"],
                "mbdump/tag": core_members["mbdump/tag"],
            },
        )
        import_genre_data(self.db, self.config, self.paths)
        score_canonical_tempos(self.db, self.config)
        row = self.db.execute(
            "SELECT canonical_recording_mbid::VARCHAR, official_release, live, duration_ms, first_release_date::VARCHAR FROM recording_metadata"
        ).fetchone()
        self.assertEqual(row, (canonical_mbid, True, True, 180000, "2020-01-02"))
        self.assertEqual(
            self.db.execute("SELECT canonical_recording_mbid::VARCHAR FROM tempo_candidate").fetchone()[0],
            canonical_mbid,
        )
        self.assertEqual(
            self.db.execute(
                "SELECT genre, specificity, vote_count FROM recording_genre_metadata"
            ).fetchone(),
            ("hip hop", 3, 12),
        )

    def test_core_archive_copy_reports_progress(self) -> None:
        source = io.BytesIO(b"progress")
        destination = io.BytesIO()
        output = io.StringIO()

        with (
            patch("song_pick.import_musicbrainz.CORE_PROGRESS_INTERVAL_SECONDS", 0),
            patch("song_pick.import_musicbrainz.COPY_CHUNK_SIZE", 2),
            contextlib.redirect_stdout(output),
        ):
            _copy_with_progress(source, destination, "recording", 8)

        self.assertEqual(destination.getvalue(), b"progress")
        self.assertIn("core extraction: recording", output.getvalue())
        self.assertIn("100.0%", output.getvalue())
        self.assertIn("ETA 0s", output.getvalue())

    def test_canonical_import_flushes_a_full_batch_set_at_once(self) -> None:
        mbids = [
            f"00000000-0000-4000-8000-{index:012x}" for index in range(20_000)
        ]
        self.db.execute(
            """
            WITH candidates AS (
              SELECT unnest(?::VARCHAR[])::UUID AS source_recording_mbid
            )
            INSERT INTO source_tempo_candidate
            SELECT source_recording_mbid, 100, 2, 1, 0, false, 1, 0.9, NULL
            FROM candidates
            """,
            [mbids],
        )
        metadata_rows = [
            (
                mbid,
                mbid,
                f"Song {index}",
                "An Artist",
                None,
                False,
                False,
                False,
                False,
                False,
                False,
            )
            for index, mbid in enumerate(mbids)
        ]
        release_rows = [
            (mbid, f"30000000-0000-4000-8000-{index:012x}")
            for index, mbid in enumerate(mbids)
        ]

        imported = _flush_canonical(self.db, metadata_rows, release_rows)

        self.assertEqual(imported, 20_000)
        self.assertEqual(metadata_rows, [])
        self.assertEqual(release_rows, [])
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM recording_metadata").fetchone()[0],
            20_000,
        )
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM recording_release").fetchone()[0],
            20_000,
        )

    def test_source_scoring_writes_a_candidate(self) -> None:
        mbid = "00000000-0000-4000-8000-000000000001"
        other_mbid = "00000000-0000-4000-8000-000000000002"
        self.db.executemany(
            "INSERT INTO tempo_observation VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (mbid, "0", 100.0, 100.0, 50.0, 2.0, "2022-06-23"),
                (mbid, "1", 100.1, 100.0, 50.0, 2.0, "2022-06-23"),
                (other_mbid, "0", 120.0, 120.0, 60.0, 2.0, "2022-06-23"),
                (other_mbid, "1", 120.1, 120.0, 60.0, 2.0, "2022-06-23"),
            ],
        )
        # One-row fetch/insert batches exercise boundaries and transactions
        # while the ordered reader is still active.
        output = io.StringIO()
        with (
            patch("song_pick.score_tempos.FETCH_SIZE", 1),
            patch("song_pick.score_tempos.INSERT_BATCH_SIZE", 1),
            patch("song_pick.score_tempos.PROGRESS_INTERVAL_SECONDS", 0),
            contextlib.redirect_stdout(output),
        ):
            score_source_tempos(self.db, self.config)
        rows = self.db.execute(
            "SELECT bpm, exclusion_reason FROM source_tempo_candidate ORDER BY source_recording_mbid"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[0][0], 100.05)
        self.assertAlmostEqual(rows[1][0], 120.05)
        self.assertTrue(all(row[1] is None for row in rows))
        self.assertIn("source tempo scoring: 2/2 (100.0%)", output.getvalue())

    def test_popularity_upsert_uses_a_columnar_batch(self) -> None:
        mbid = "00000000-0000-4000-8000-000000000001"
        self.db.execute(
            "INSERT INTO source_tempo_candidate VALUES (?, 100, 2, 1, 0, false, 1, 0.9, NULL)",
            [mbid],
        )
        response = [
            {
                "recording_mbid": mbid,
                "total_user_count": 123,
                "total_listen_count": 456,
            }
        ]
        output = io.StringIO()
        with (
            patch(
                "song_pick.fetch_popularity.request_popularity", return_value=response
            ),
            contextlib.redirect_stdout(output),
        ):
            fetch_popularity(self.db, self.config)
        self.assertEqual(
            self.db.execute(
                "SELECT total_listener_count, total_listen_count FROM popularity"
            ).fetchone(),
            (123, 456),
        )
        self.assertEqual(
            self.db.execute("SELECT item_count FROM popularity_batch").fetchone()[0],
            1,
        )
        self.assertIn(
            "popularity batch 1 out of 1: checkpointed 1 recordings",
            output.getvalue(),
        )

    def test_selection_export_and_validation_are_reproducible(self) -> None:
        metadata = []
        tempos = []
        popularity = []
        for index in range(6):
            mbid = f"00000000-0000-4000-8000-{index:012d}"
            artist_mbid = f"20000000-0000-4000-8000-{index:012d}"
            metadata.append(
                (
                    mbid, mbid, f"Song {index}", f"Artist {index}", artist_mbid,
                    180_000, "2020-01-01", True, False, False, False, False,
                    False, False, False, 1.0,
                )
            )
            tempos.append((mbid, mbid, 100 + index * 0.1, 3, 0.1, False, 0.9, None))
            popularity.append((mbid, 1000 - index, 5000 - index, "2026-09-12T00:00:00Z"))
        self.db.executemany(
            "INSERT INTO recording_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            metadata,
        )
        self.db.executemany("INSERT INTO tempo_candidate VALUES (?, ?, ?, ?, ?, ?, ?, ?)", tempos)
        self.db.executemany("INSERT INTO popularity VALUES (?, ?, ?, ?)", popularity)

        select_catalog(self.db, self.config)
        _, first_checksum = export_catalog(self.db, self.config, self.paths)
        _, second_checksum = export_catalog(self.db, self.config, self.paths)
        self.assertEqual(first_checksum, second_checksum)
        validate_catalog(self.config, self.paths, self.db)


if __name__ == "__main__":
    unittest.main()
