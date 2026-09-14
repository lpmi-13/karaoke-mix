from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BulkWriteSafetyTests(unittest.TestCase):
    def test_production_avoids_row_at_a_time_executemany(self) -> None:
        failures: list[str] = []
        for path in sorted((ROOT / "song_pick").glob("*.py")):
            for line_number, line in enumerate(path.read_text().splitlines(), start=1):
                if ".executemany(" in line:
                    failures.append(f"{path.relative_to(ROOT)}:{line_number}")

        self.assertEqual(
            failures,
            [],
            "production bulk writes must use set-based column batches: "
            + ", ".join(failures),
        )


if __name__ == "__main__":
    unittest.main()
