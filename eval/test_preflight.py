#!/usr/bin/env python3
"""
Every benchmark runner must preflight the router and its models before it creates
a run directory. Without that, an unbuilt or unrouted model is caught per attempt
and scored as a failure, and the run writes a summary that reads as a model that
cannot do the task. No live server: `preflight` and `new_run_dir` are patched.
Run with:

    python3 -m unittest eval/test_preflight.py -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL))

import _gateway as gw  # noqa: E402

RUNNERS = ["run-code.py", "run-content.py", "run-json.py", "run-learn.py",
           "run-persona.py", "run-speed.py", "run-tutor.py"]


def load_runner(filename: str):
    name = filename.replace("-", "_").removesuffix(".py")
    spec = importlib.util.spec_from_file_location(name, EVAL / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # @dataclass resolves its module through sys.modules
    spec.loader.exec_module(module)
    return module


class PreflightBeforeRunDirTests(unittest.TestCase):
    """A down router must stop the run before anything is written."""

    def assert_preflights_first(self, filename: str, **extra_patches):
        runner = load_runner(filename)
        with tempfile.TemporaryDirectory() as out:
            argv = [filename, "--models", "lite", "gemma:think", "--out-root", out]
            patches = [
                mock.patch.object(runner, "preflight", create=True,
                                  side_effect=SystemExit("ERROR: no llama-server router")),
                mock.patch.object(runner, "new_run_dir",
                                  side_effect=AssertionError("run dir created before preflight")),
                mock.patch.object(sys, "argv", argv),
                *[mock.patch.object(runner, attr, side_effect=AssertionError(msg))
                  for attr, msg in extra_patches.items()],
            ]
            for p in patches:
                p.start()
                self.addCleanup(p.stop)
            with self.assertRaises(SystemExit) as cm:
                runner.main()
            self.assertEqual(str(cm.exception), "ERROR: no llama-server router")
            runner.preflight.assert_called_once_with(["lite", "gemma:think"])
            runner.new_run_dir.assert_not_called()
            self.assertEqual(os.listdir(out), [])

    def test_run_code(self):
        self.assert_preflights_first("run-code.py")

    def test_run_content(self):
        self.assert_preflights_first("run-content.py")

    def test_run_json_preflights_before_reading_served_context(self):
        self.assert_preflights_first("run-json.py",
                                     served_ctx="served_ctx called before preflight")

    def test_run_speed(self):
        self.assert_preflights_first("run-speed.py")


class RunnersUseGatewayPreflightTests(unittest.TestCase):
    """The patched tests above would pass even if a runner never imported preflight."""

    def test_every_runner_imports_the_gateway_preflight(self):
        for filename in RUNNERS:
            with self.subTest(runner=filename):
                self.assertIs(getattr(load_runner(filename), "preflight", None), gw.preflight)


if __name__ == "__main__":
    unittest.main()
