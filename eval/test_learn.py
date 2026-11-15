#!/usr/bin/env python3
"""
The learn judge's strict rubric and run-learn.py --rescore. No live server: the
judge's generate call is patched. Run with:

    python3 -m unittest eval/test_learn.py -v

Why this exists: the default and the old strict rubric both scored every
code-passing answer 9.8 to 10.0 (TESTING.md, *Strict-rubric learn run*), so
qwen and gemma could not be told apart. Strict now scores each dimension 0 to 4
and scales to /10. The default must not move.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL))

import _judge  # noqa: E402

_spec = importlib.util.spec_from_file_location("run_learn", EVAL / "run-learn.py")
rl = importlib.util.module_from_spec(_spec)
sys.modules["run_learn"] = rl
_spec.loader.exec_module(rl)

DIMS = ["approach", "complexity", "alternative", "pitfall", "clarity"]


def replies(*texts):
    return mock.patch.object(_judge, "generate", side_effect=[(t, {}) for t in texts])


def scores(**v):
    return "{" + ",".join(f'"{d}":{v.get(d, 0)}' for d in DIMS) + "}"


class JudgeScale(unittest.TestCase):
    def test_default_clamps_to_two(self):
        with replies(scores(approach=4, clarity=2)):
            sc = _judge.judge_scores("qwen", "t", "r", 30, "{topic}{response}", DIMS)
        self.assertEqual((sc["approach"], sc["clarity"]), (2, 2))

    def test_top_four_keeps_four_and_clamps_above(self):
        with replies(scores(approach=4, clarity=7)):
            sc = _judge.judge_scores("qwen", "t", "r", 30, "{topic}{response}", DIMS, top=4)
        self.assertEqual((sc["approach"], sc["clarity"]), (4, 4))

    def test_default_total_is_the_raw_sum(self):
        with replies(scores(approach=2, complexity=2, alternative=1, pitfall=2, clarity=2)):
            total, _flags, _spread = _judge.judge_total("qwen", "t", "r", 30,
                                                        "{topic}{response}", DIMS)
        self.assertEqual(total, 9)
        self.assertIsInstance(total, int)

    def test_top_four_total_is_scaled_to_ten(self):
        with replies(scores(approach=4, complexity=3, alternative=2, pitfall=3, clarity=4)):
            total, _flags, _spread = _judge.judge_total("qwen", "t", "r", 30,
                                                        "{topic}{response}", DIMS, top=4)
        self.assertEqual(total, 8.0)

    def test_top_four_spread_is_on_the_ten_scale(self):
        with replies(scores(approach=4, complexity=4, alternative=4, pitfall=4, clarity=4),
                     scores(approach=2, complexity=2, alternative=2, pitfall=2, clarity=2)):
            _total, _flags, spread = _judge.judge_total("qwen", "t", "r", 30,
                                                        "{topic}{response}", DIMS,
                                                        top=4, repeats=2)
        self.assertEqual(spread, 5.0)


class StrictTemplate(unittest.TestCase):
    def test_strict_selects_the_file_template_at_top_four(self):
        template, top = rl.judge_setup("strict")
        self.assertEqual(top, 4)
        self.assertEqual(template, (EVAL / "prompts" / "learn-judge-strict.md").read_text())

    def test_default_keeps_the_inline_template_at_top_two(self):
        self.assertEqual(rl.judge_setup("default"), (rl.JUDGE_TEMPLATE, 2))

    def test_strict_template_formats_and_anchors_every_dimension(self):
        template, _top = rl.judge_setup("strict")
        text = template.format(topic="binary search", response="RESP")
        self.assertIn("binary search", text)
        self.assertIn("RESP", text)
        for d in rl.RUBRIC:
            self.assertIn(f"- {d}:", text)
            self.assertIn(f'"{d}":N', text)
        self.assertIn("0 to 4", text)


SAVED = """# lite · binary_search · attempt 2

- code: FAIL (AssertionError)
- explanation: 9.0/10  (panel of 3: gemma=9, qwen=9, freedom=9)

---

Answer text.

---

Text after a rule in the answer itself.
"""


class LoadSaved(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name)
        for model in ("lite", "qwen"):
            (self.run / model).mkdir()
        (self.run / "lite" / "binary_search-attempt-2.md").write_text(SAVED)
        (self.run / "qwen" / "binary_search-attempt-1.md").write_text(
            SAVED.replace("lite", "qwen").replace("attempt 2", "attempt 1")
                 .replace("FAIL (AssertionError)", "PASS"))
        (self.run / "summary.md").write_text("old summary")

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_model_task_attempt_code_result_and_full_text(self):
        recs = rl.load_saved(self.run, ["lite"], rl.TASKS)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual((r["model"], r["task"], r["attempt"]), ("lite", "binary_search", 2))
        self.assertEqual((r["passed"], r["reason"]), (False, "AssertionError"))
        self.assertEqual(r["text"], "Answer text.\n\n---\n\nText after a rule in the answer itself.")
        self.assertEqual(r["topic"], next(t.topic for t in rl.TASKS if t.name == "binary_search"))

    def test_pass_has_an_empty_reason(self):
        r = rl.load_saved(self.run, ["qwen"], rl.TASKS)[0]
        self.assertEqual((r["passed"], r["reason"]), (True, ""))

    def test_only_selected_tasks_are_loaded(self):
        other = [t for t in rl.TASKS if t.name != "binary_search"]
        self.assertEqual(rl.load_saved(self.run, ["lite", "qwen"], other), [])

    def test_a_model_with_no_saved_folder_aborts(self):
        with self.assertRaises(SystemExit):
            rl.load_saved(self.run, ["lite", "gemma"], rl.TASKS)

    def test_a_model_folder_with_no_responses_aborts(self):
        (self.run / "gemma").mkdir()
        with self.assertRaises(SystemExit):
            rl.load_saved(self.run, ["gemma"], rl.TASKS)


if __name__ == "__main__":
    unittest.main()
