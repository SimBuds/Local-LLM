#!/usr/bin/env python3
"""
Unit tests for run-json.py's context handling. No live server: the gateway calls
are patched. Run with:

    python3 -m unittest eval/test_json.py -v
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

_PATH = Path(__file__).resolve().parent / "run-json.py"
_spec = importlib.util.spec_from_file_location("run_json", _PATH)
rj = importlib.util.module_from_spec(_spec)
sys.modules["run_json"] = rj  # @dataclass resolves its module through sys.modules
_spec.loader.exec_module(rj)


def serving(**ctx_by_model):
    """Patch run-json's served_ctx lookup with a fixed n_ctx per model."""
    return mock.patch.object(rj, "served_ctx", side_effect=lambda m: ctx_by_model[m])


class RequireCtxTests(unittest.TestCase):
    def test_served_equal_to_required_passes(self):
        with serving(lite=65536):
            self.assertEqual(rj.require_ctx(["lite"], 65536), {"lite": 65536})

    def test_served_above_required_passes(self):
        with serving(lite=65536):
            self.assertEqual(rj.require_ctx(["lite"], 65536), {"lite": 65536})

    def test_served_below_required_aborts_with_both_numbers_and_the_fix(self):
        with serving(lite=65536):
            with self.assertRaises(SystemExit) as cm:
                rj.require_ctx(["lite"], 65536)
        msg = str(cm.exception)
        self.assertIn("lite (65536)", msg)
        self.assertIn("65536", msg)
        self.assertIn("ctx-size", msg)

    def test_only_the_short_model_is_named(self):
        with serving(lite=65536, qwen=65536, gemma=65536):
            with self.assertRaises(SystemExit) as cm:
                rj.require_ctx(["gemma", "qwen", "lite"], 65536)
        msg = str(cm.exception)
        self.assertIn("qwen (65536)", msg)
        self.assertNotIn("lite (", msg)
        self.assertNotIn("gemma (", msg)

    def test_think_spec_is_looked_up_by_model_name(self):
        with serving(qwen=65536):
            self.assertEqual(rj.require_ctx(["qwen:think"], 65536), {"qwen:think": 65536})


class RunAttemptTests(unittest.TestCase):
    def test_request_carries_no_num_ctx(self):
        task = rj.ResolvedTask(key="t", instruction="Extract.", context="doc",
                               schema={"type": "object"}, checks=[])
        meta = {"eval_count": 4, "eval_duration": 1e8, "prompt_eval_count": 3000}
        with mock.patch.object(rj, "generate", return_value=('{"a": 1}', meta)) as gen:
            r = rj.run_attempt("lite", task, 1, 1, timeout=30, thinking_mode="off", seed=7)
        options = gen.call_args.kwargs["options"]
        self.assertNotIn("num_ctx", options)
        self.assertEqual(options, {"temperature": 0.0, "seed": 8})
        self.assertTrue(r["ok"])


if __name__ == "__main__":
    unittest.main()
