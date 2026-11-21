#!/usr/bin/env python3
"""
Unit tests for promote.py's choice of which run feeds each leaderboard row.
No router and no real run folder: `promote.RUNS` points at a temp dir. Run with:

    python3 -m unittest eval/test_promote.py -v

Why this exists: the README leaderboard is the newest summary per suite, so
which run counts as "newest" and which rows are flagged as older decides what
the README says.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import promote  # noqa: E402


class RunsDirCase(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.runs = Path(td.name)
        patch = mock.patch.object(promote, "RUNS", self.runs)
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, stamp: str, suite: str, column: str, value: str, head: str = "") -> None:
        d = self.runs / stamp / suite
        d.mkdir(parents=True)
        (d / "summary.md").write_text(
            f"{head}| Rank | Model | {column} |\n|---|---|---|\n| 1 | `lite` | {value} |\n",
            encoding="utf-8")

    def json_run(self, stamp: str, score: str, pressure: str | None) -> None:
        head = "" if pressure is None else (
            f"- Context pressure: **{pressure}** (×1.0 normal doc length), "
            f"needle position: default\n\n")
        self.write(stamp, "json", "Score", score, head)

    def row(self, block: str, label: str) -> str:
        rows = [ln for ln in block.splitlines() if ln.startswith(f"| {label} |")]
        self.assertEqual(len(rows), 1, block)
        return rows[0]


class JsonPressureTests(RunsDirCase):
    """Only a normal-pressure JSON run is the leaderboard's JSON row. A
    `--context-pressure high` pass that finished last used to replace it."""

    LABEL = "JSON / long-context"

    def test_a_newer_high_pressure_run_does_not_replace_the_normal_one(self):
        self.json_run("20261120T010000Z", "90%", "normal")
        self.json_run("20261120T040000Z", "70%", "high")
        self.assertEqual(promote.newest_runs(None)["json"][0], "20261120T010000Z")
        block, _ = promote.build_block(None)
        self.assertIn("90%", self.row(block, self.LABEL))
        self.assertNotIn("70%", block)

    def test_a_newer_medium_pressure_run_does_not_replace_the_normal_one(self):
        self.json_run("20261120T010000Z", "90%", "normal")
        self.json_run("20261120T040000Z", "80%", "medium")
        self.assertEqual(promote.newest_runs(None)["json"][0], "20261120T010000Z")

    def test_the_newest_normal_run_still_wins_among_normal_runs(self):
        self.json_run("20261120T010000Z", "90%", "normal")
        self.json_run("20261120T040000Z", "80%", "normal")
        self.assertEqual(promote.newest_runs(None)["json"][0], "20261120T040000Z")

    def test_with_only_a_pressure_run_the_json_row_says_not_run(self):
        self.write("20261120T010000Z", "speed", "Gen tok/s", "100")
        self.json_run("20261120T040000Z", "70%", "high")
        self.assertNotIn("json", promote.newest_runs(None))
        block, _ = promote.build_block(None)
        self.assertIn("_not run_", self.row(block, self.LABEL))

    def test_a_summary_with_no_pressure_line_counts_as_normal(self):
        self.json_run("20261120T010000Z", "90%", None)
        self.assertEqual(promote.newest_runs(None)["json"][0], "20261120T010000Z")

    def test_another_suite_is_not_filtered_by_a_pressure_line(self):
        self.write("20261120T010000Z", "speed", "Gen tok/s", "100",
                   "- Context pressure: **high**\n\n")
        self.assertIn("speed", promote.newest_runs(None))


class StaleWindowTests(RunsDirCase):
    """A row is flagged as older when its run began more than STALE_AFTER_HOURS
    before the newest run. The old rule compared UTC days, so a pass that ran
    through midnight flagged its own earlier suites."""

    def stale(self) -> list[str]:
        _, warnings = promote.build_block(None)
        hits = [w for w in warnings if w.startswith("stale suites")]
        self.assertLessEqual(len(hits), 1, warnings)
        return hits

    def test_a_pass_that_crosses_midnight_utc_is_one_pass(self):
        self.write("20261119T230000Z", "code", "Passed", "40/45")
        self.write("20261120T010000Z", "speed", "Gen tok/s", "100")
        self.assertEqual(self.stale(), [])
        block, _ = promote.build_block(None)
        self.assertNotIn("⚠", block)

    def test_a_run_thirteen_hours_older_is_flagged_across_days(self):
        self.write("20261119T120000Z", "code", "Passed", "40/45")
        self.write("20261120T010000Z", "speed", "Gen tok/s", "100")
        hits = self.stale()
        self.assertEqual(len(hits), 1)
        self.assertIn("code", hits[0])
        self.assertNotIn("speed", hits[0])
        block, _ = promote.build_block(None)
        self.assertIn("⚠", self.row(block, "Coding"))
        self.assertNotIn("⚠", self.row(block, "Speed"))

    def test_a_run_thirteen_hours_older_is_flagged_on_the_same_day(self):
        self.write("20261120T010000Z", "code", "Passed", "40/45")
        self.write("20261120T140000Z", "speed", "Gen tok/s", "100")
        hits = self.stale()
        self.assertEqual(len(hits), 1)
        self.assertIn("code", hits[0])

    def test_exactly_the_window_is_not_flagged(self):
        self.write("20261120T010000Z", "code", "Passed", "40/45")
        self.write("20261120T130000Z", "speed", "Gen tok/s", "100")
        self.assertEqual(self.stale(), [])

    def test_a_single_run_is_never_flagged(self):
        self.write("20261120T010000Z", "code", "Passed", "40/45")
        self.assertEqual(self.stale(), [])

    def test_the_warning_names_the_window(self):
        self.write("20261119T120000Z", "code", "Passed", "40/45")
        self.write("20261120T010000Z", "speed", "Gen tok/s", "100")
        block, _ = promote.build_block(None)
        self.assertIn(f"more than {promote.STALE_AFTER_HOURS} hours before the newest run", block)


if __name__ == "__main__":
    unittest.main()
