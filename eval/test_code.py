#!/usr/bin/env python3
"""
Unit tests for coding_tasks.py: the fresh tasks must prove their own hidden
asserts. No model and no server. Programs really run, through the gateway's
run_program, so these skip visibly where bwrap cannot start. Run with:

    python3 -m unittest eval/test_code.py -v

Why this exists: a hidden test that fails a correct answer, or passes a wrong
one, turns into a wrong published score. Each fresh task carries a reference
solution, a differently written one, and a plausible wrong shortcut, and the
asserts must tell them apart.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL))

import _gateway as gw  # noqa: E402
import coding_tasks as ct  # noqa: E402

CLASSIC = ["two_sum", "valid_parentheses", "merge_intervals", "lru_cache", "edit_distance",
           "calc", "decode_string", "coin_change", "flatten_dict"]

# The one example each fresh task's prompt gives. A cheat has to get this right,
# or it is not a shortcut anyone would take.
PROMPT_EXAMPLE = {
    "split_bill": "assert split_bill(100, [1, 1, 1]) == [34, 33, 33]",
    "paid_minutes": 'assert paid_minutes([("09:00", "17:00")]) == 450',
    "irv_winner": 'assert irv_winner([["A", "B"], ["A"], ["B", "A"]]) == "A"',
    "sort_versions": 'assert sort_versions(["1.10.0", "1.2.0"]) == ["1.2.0", "1.10.0"]',
    "meeting_room": ('room = MeetingRoom()\nassert room.book(60, 120, "ann") is True\n'
                     'assert room.book(90, 150, "bob") is False'),
    "tally_stock": 'assert tally_stock(["IN bolt 5", "OUT bolt 2"]) == {"bolt": 3}',
}


def run(code: str, tests: str) -> tuple[bool, str]:
    """Score `code` the way run-code.py does."""
    return gw.run_program(f"{code}\n\n# --- hidden tests ---\n{tests}\n", 10)


class ClassicTasksTests(unittest.TestCase):
    def test_default_suite_is_still_the_nine_classic_tasks(self):
        self.assertEqual([t.name for t in ct.TASKS], CLASSIC)

    def test_classic_tasks_carry_no_fixtures(self):
        for task in ct.TASKS:
            with self.subTest(task.name):
                self.assertEqual((task.solution, task.alt_solution, task.exploit), ("", "", ""))


class FreshTaskTests(unittest.TestCase):
    """Every fresh task's hidden asserts must be sound in both directions."""

    def setUp(self):
        if not gw._sandbox_works():
            self.skipTest("bwrap is not usable on this machine")

    def test_fresh_tasks_are_the_expected_six(self):
        self.assertEqual([t.name for t in ct.FRESH_TASKS], list(PROMPT_EXAMPLE))
        self.assertFalse({t.name for t in ct.FRESH_TASKS} & set(CLASSIC))

    def test_reference_solution_passes(self):
        for task in ct.FRESH_TASKS:
            with self.subTest(task.name):
                self.assertEqual(run(task.solution, task.tests), (True, "pass"))

    def test_alternate_solution_passes(self):
        for task in ct.FRESH_TASKS:
            with self.subTest(task.name):
                self.assertNotEqual(task.alt_solution.strip(), task.solution.strip())
                self.assertEqual(run(task.alt_solution, task.tests), (True, "pass"))

    def test_cheat_gets_the_prompt_example_right_and_the_hidden_asserts_wrong(self):
        for task in ct.FRESH_TASKS:
            with self.subTest(task.name):
                self.assertEqual(run(task.exploit, PROMPT_EXAMPLE[task.name]), (True, "pass"))
                # A wrong answer, not a crash: a cheat that raises proves nothing.
                self.assertEqual(run(task.exploit, task.tests), (False, "wrong-answer"))

    def test_prompt_example_is_one_the_reference_solution_agrees_with(self):
        for task in ct.FRESH_TASKS:
            with self.subTest(task.name):
                self.assertEqual(run(task.solution, PROMPT_EXAMPLE[task.name]), (True, "pass"))

    def test_code_that_defines_nothing_fails(self):
        for task in ct.FRESH_TASKS:
            with self.subTest(task.name):
                self.assertFalse(run("", task.tests)[0])

    def test_hidden_asserts_are_not_in_the_prompt(self):
        for task in ct.FRESH_TASKS:
            asserts = [ln.strip() for ln in task.tests.splitlines() if ln.startswith("assert")]
            self.assertGreaterEqual(len(asserts), 6, task.name)
            for line in asserts:
                with self.subTest(task.name, line=line):
                    self.assertNotIn(line.removeprefix("assert ").split(" == ")[0], task.prompt)

    def test_prompt_names_the_entrypoint_and_asks_for_code_only(self):
        for task in ct.FRESH_TASKS:
            with self.subTest(task.name):
                self.assertIn(task.entrypoint, task.prompt)
                self.assertIn("Return ONLY one fenced ```python code block", task.prompt)


if __name__ == "__main__":
    unittest.main()
