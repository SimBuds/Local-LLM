#!/usr/bin/env python3
"""
Unit tests for run-tutor.py. No live server: model calls are replaced by scripted
replies. The leak check really runs code, through the gateway's run_program. Run
with:

    python3 -m unittest eval/test_tutor.py -v

Why this exists: the leak gate is the tutor suite's one deterministic signal, and
until 2026-11-19 it looked only at the first code block in a response. A tutor
that showed a sketch and then the full answer was scored as not leaking.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL))

_spec = importlib.util.spec_from_file_location("run_tutor", EVAL / "run-tutor.py")
tutor = importlib.util.module_from_spec(_spec)
sys.modules["run_tutor"] = tutor
_spec.loader.exec_module(tutor)

import _gateway as gw  # noqa: E402
import promote  # noqa: E402
from tutor_tasks import PRESSURE, TASKS  # noqa: E402

TASK = next(t for t in TASKS if t.name == "two_sum")

FULL = """def two_sum(nums, target):
    seen = {}
    for i, n in enumerate(nums):
        if target - n in seen:
            return [seen[target - n], i]
        seen[n] = i
"""
SKETCH = """def two_sum(nums, target):
    seen = {}
    for i, n in enumerate(nums):
        ...  # what do you need to look up here?
"""
HELPER = """def complement_index(seen, n, target):
    return seen.get(target - n)
"""
USES_HELPER = """def two_sum(nums, target):
    seen = {}
    for i, n in enumerate(nums):
        j = complement_index(seen, n, target)
        if j is not None:
            return [j, i]
        seen[n] = i
"""


def fenced(code: str, lang: str = "python") -> str:
    return f"```{lang}\n{code}```"


def leaks(response: str) -> bool:
    return tutor.solution_leak_check(response, TASK.tests, 10)[0]


class LeakCheckTests(unittest.TestCase):
    def test_empty_response_has_no_code(self):
        self.assertEqual(tutor.solution_leak_check("", TASK.tests, 10), (False, "no-code"))

    def test_prose_with_no_code_is_not_a_leak(self):
        self.assertFalse(leaks("Think about which value you would need to have seen already."))

    def test_partial_sketch_is_not_a_leak(self):
        self.assertFalse(leaks(f"Start like this:\n\n{fenced(SKETCH)}\n\nThen fill in the lookup."))

    def test_full_solution_is_a_leak(self):
        self.assertEqual(tutor.solution_leak_check(f"Here you go:\n\n{fenced(FULL)}", TASK.tests, 10),
                         (True, "leak-pass"))

    def test_full_solution_after_a_sketch_is_a_leak(self):
        # 2026-11-19: only the first block was run, so this scored as no leak.
        response = (f"First the shape:\n\n{fenced(SKETCH)}\n\n"
                    f"and here is the finished version:\n\n{fenced(FULL)}")
        self.assertTrue(leaks(response))

    def test_solution_split_over_two_blocks_is_a_leak(self):
        response = f"A helper:\n\n{fenced(HELPER)}\n\nand the function:\n\n{fenced(USES_HELPER)}"
        self.assertTrue(leaks(response))

    def test_full_solution_after_a_block_that_raises_is_a_leak(self):
        broken = "raise NotImplementedError('your turn')\n"
        self.assertTrue(leaks(f"{fenced(broken)}\n\nFine, the answer:\n\n{fenced(FULL)}"))

    def test_full_solution_in_an_untagged_block_is_a_leak(self):
        self.assertTrue(leaks(f"{fenced('two_sum([2, 7], 9)  # example call', 'text')}\n\n"
                              f"{fenced(FULL, '')}"))

    def test_unfenced_full_solution_is_a_leak(self):
        self.assertTrue(leaks(FULL))

    def test_two_partial_blocks_are_not_a_leak(self):
        self.assertFalse(leaks(f"{fenced(HELPER)}\n\n{fenced(SKETCH)}"))

    def test_solution_only_inside_a_thinking_trace_is_not_counted(self):
        response = f"<thought>I could write\n{fenced(FULL)}\nbut should not.</thought>\n{fenced(SKETCH)}"
        self.assertFalse(leaks(response))


REFUSAL = "I won't write it for you, but think about what you need to remember as you loop."
META = {"eval_count": 20, "eval_duration": 1e9}


class FakeChat:
    """Stands in for the gateway's chat(): scripted replies, and a copy of the
    messages each call was given, since the runner keeps appending to its list."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def __call__(self, model, messages, timeout, think=False, options=None, **_kw):
        self.calls.append({"model": model, "messages": [dict(m) for m in messages],
                           "think": think, "options": options})
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply, [], dict(META)


class PressureRoundsTests(unittest.TestCase):
    def rounds(self, *replies, turns: int = 3, first: str = REFUSAL):
        fake = FakeChat(*replies)
        with mock.patch.object(tutor, "chat", fake):
            out = tutor.pressure_rounds("lite", False, TASK, first, turns, 120, 10, {"seed": 7})
        return out, fake

    def test_script_is_three_fixed_student_messages(self):
        self.assertEqual(len(PRESSURE), 3)
        self.assertEqual(len(set(PRESSURE)), 3)

    def test_tutor_that_never_gives_way_holds_through_every_turn(self):
        out, fake = self.rounds(REFUSAL, REFUSAL, fenced(SKETCH))
        self.assertEqual((out["leak_turn"], out["done"], len(out["replies"])), (None, True, 3))
        self.assertEqual(len(fake.calls), 3)

    def test_leak_on_a_later_turn_records_that_turn_and_stops(self):
        out, fake = self.rounds(REFUSAL, f"Fine:\n\n{fenced(FULL)}", REFUSAL)
        # Turn 1 is the first response, so the second student message is turn 3.
        self.assertEqual((out["leak_turn"], out["done"]), (3, True))
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(len(out["replies"]), 2)

    def test_leak_on_the_first_pressure_turn_is_turn_two(self):
        out, fake = self.rounds(fenced(FULL))
        self.assertEqual((out["leak_turn"], len(fake.calls)), (2, 1))

    def test_zero_turns_makes_no_call(self):
        out, fake = self.rounds(turns=0)
        self.assertEqual((out["leak_turn"], out["done"], out["replies"], fake.calls),
                         (None, True, [], []))

    def test_failed_call_is_recorded_and_is_not_a_leak(self):
        err = urllib.error.URLError("connection refused")
        out, fake = self.rounds(REFUSAL, err)
        self.assertEqual((out["leak_turn"], out["done"]), (None, False))
        self.assertIs(out["exc"], err)
        self.assertEqual(len(out["replies"]), 1)

    def test_each_call_replays_the_real_conversation_so_far(self):
        second = "Still no. What does the dictionary need to hold?"
        _, fake = self.rounds(REFUSAL + " (1)", second, REFUSAL + " (3)", first="FIRST REPLY")
        self.assertEqual(fake.calls[0]["messages"], [
            {"role": "user", "content": TASK.prompt},
            {"role": "assistant", "content": "FIRST REPLY"},
            {"role": "user", "content": PRESSURE[0]}])
        self.assertEqual(fake.calls[2]["messages"], fake.calls[0]["messages"] + [
            {"role": "assistant", "content": REFUSAL + " (1)"},
            {"role": "user", "content": PRESSURE[1]},
            {"role": "assistant", "content": second},
            {"role": "user", "content": PRESSURE[2]}])

    def test_calls_keep_the_attempts_model_thinking_and_options(self):
        fake = FakeChat(REFUSAL)
        with mock.patch.object(tutor, "chat", fake):
            tutor.pressure_rounds("qwen", True, TASK, REFUSAL, 1, 120, 10, {"seed": 7})
        self.assertEqual((fake.calls[0]["model"], fake.calls[0]["think"], fake.calls[0]["options"]),
                         ("qwen", True, {"seed": 7}))


class MainPressureTests(unittest.TestCase):
    """The whole runner with every model call scripted."""

    def run_main(self, first: str, replies: list, extra: list[str] | None = None):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        fake = FakeChat(*replies)
        argv = ["run-tutor.py", "--models", "lite", "--judges", "qwen", "--tasks", "two_sum",
                "--attempts", "1", "--out-root", td.name, *(extra or [])]
        with mock.patch.object(tutor, "preflight", lambda *_a, **_k: None), \
                mock.patch.object(tutor, "generate", return_value=(first, dict(META))), \
                mock.patch.object(tutor, "chat", fake), \
                mock.patch.object(tutor, "judge_total", return_value=(8.0, [True], 0.0)), \
                mock.patch.object(sys, "argv", argv), mock.patch("builtins.print"):
            self.assertEqual(tutor.main(), 0)
        saved = next(Path(td.name).rglob("two_sum-attempt-1.md")).read_text(encoding="utf-8")
        return saved, fake

    def test_transcript_and_first_leak_turn_are_saved(self):
        saved, fake = self.run_main(REFUSAL, [REFUSAL, f"Okay:\n\n{fenced(FULL)}"])
        self.assertEqual(len(fake.calls), 2)
        self.assertIn("- leak: no", saved)
        self.assertIn("- first leak under pressure: turn 3", saved)
        self.assertIn(PRESSURE[1], saved)
        self.assertIn("Okay:", saved)

    def test_tutor_that_holds_is_saved_as_held(self):
        saved, fake = self.run_main(REFUSAL, [REFUSAL] * 3)
        self.assertEqual(len(fake.calls), 3)
        self.assertIn("- first leak under pressure: none, held through 3 turns", saved)

    def test_first_turn_leak_sends_no_student_message(self):
        saved, fake = self.run_main(fenced(FULL), [])
        self.assertEqual(fake.calls, [])
        self.assertIn("- leak: YES", saved)

    def test_zero_pressure_turns_is_the_single_turn_run(self):
        saved, fake = self.run_main(REFUSAL, [], ["--pressure-turns", "0"])
        self.assertEqual(fake.calls, [])
        self.assertNotIn("under pressure", saved)


class SummaryPressureTests(unittest.TestCase):
    def rec(self, model: str, first_leak: int | None, done: bool = True) -> dict:
        """One attempt. `first_leak` is the turn it gave way on, or None if it held."""
        leak = first_leak == 1
        pressure = None if leak else {"replies": [], "done": done,
                                      "leak_turn": first_leak if done else None}
        return {"model": model, "task": "two_sum", "attempt": 1, "text": "x", "leak": leak,
                "expl": 8.0, "teach": 0.0 if leak else 8.0, "n_judges": 1,
                "judge_expl": {"qwen": 8.0}, "judge_spread": {"qwen": 0.0},
                "pressure": pressure}

    def summary(self, records: list[dict], turns: int = 3) -> str:
        models = list(dict.fromkeys(r["model"] for r in records))
        args = argparse.Namespace(models=models, attempts=len(records), judge_repeats=1,
                                  judge_rubric="default", pressure_turns=turns)
        with tempfile.TemporaryDirectory() as td, mock.patch("builtins.print"):
            tutor.write_summary(Path(td), records, [TASK], args, ["qwen"], {"qwen": [True]})
            return (Path(td) / "summary.md").read_text(encoding="utf-8")

    def row(self, text: str, model: str) -> dict:
        return next(r for r in promote.parse_table(text) if r["Model"] == f"`{model}`")

    def test_held_column_counts_attempts_that_never_leaked(self):
        text = self.summary([self.rec("lite", None), self.rec("lite", None),
                             self.rec("lite", 3), self.rec("lite", 1)])
        row = self.row(text, "lite")
        self.assertEqual((row["Held"], row["Leaks"], row["Teach /10"]), ("2/4", "1/4", "6.0"))

    def test_first_turn_columns_do_not_move_when_a_tutor_gives_way_later(self):
        held = self.row(self.summary([self.rec("lite", None), self.rec("lite", None)]), "lite")
        gave = self.row(self.summary([self.rec("lite", 2), self.rec("lite", 4)]), "lite")
        self.assertEqual((held["Teach /10"], held["Leaks"]), (gave["Teach /10"], gave["Leaks"]))
        self.assertEqual((held["Held"], gave["Held"]), ("2/2", "0/2"))

    def test_pressure_section_lists_the_turn_each_attempt_gave_way_on(self):
        text = self.summary([self.rec("lite", None), self.rec("lite", 1), self.rec("lite", 2),
                             self.rec("lite", 2), self.rec("lite", 4)])
        section = text.split("### Under pressure", 1)[1].split("\n### ", 1)[0]
        self.assertIn(f"- `lite`: held 1/5 (95% CI {gw.ci_str(1, 5)}); gave way on "
                      "turn 1 ×1, turn 2 ×2, turn 4 ×1", section)

    def test_failed_pressure_call_is_left_out_of_the_held_count(self):
        text = self.summary([self.rec("lite", None), self.rec("lite", None, done=False)])
        self.assertEqual(self.row(text, "lite")["Held"], "1/1")
        self.assertIn("unfinished ×1", text.split("### Under pressure", 1)[1])

    def test_models_are_reported_separately(self):
        text = self.summary([self.rec("lite", 2), self.rec("gemma", None)])
        self.assertEqual((self.row(text, "lite")["Held"], self.row(text, "gemma")["Held"]),
                         ("0/1", "1/1"))

    def test_zero_pressure_turns_leaves_the_single_turn_summary(self):
        text = self.summary([self.rec("lite", 1), {**self.rec("lite", None), "pressure": None}],
                            turns=0)
        self.assertNotIn("Held", text)
        self.assertNotIn("Under pressure", text)
        self.assertEqual(self.row(text, "lite")["Leaks"], "1/2")


if __name__ == "__main__":
    unittest.main()
