#!/usr/bin/env python3
"""
Every runner must stop when the model it is calling crashes, and must keep going
when a single call merely fails. No live server: each runner's model call is
patched to fail, and the router's status read is patched to report the model as
the router does after a crash (`unloaded`, `failed: true`, exit code), per
llama.cpp's server-models.cpp. Run with:

    python3 -m unittest eval/test_failure_paths.py -v

Why this exists: on 2026-09-17 qwen crashed mid-run (CUDA error, Xid 31), the
router kept answering, and run-tutor.py recorded 53 failed judge calls as
unparseable and exited 0 with a summary that read as the model's own scores.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL))

import _gateway as gw  # noqa: E402
import _judge  # noqa: E402

# Runners that call generate(); run-tools.py calls chat() and is covered below.
GENERATE_RUNNERS = ["run-code.py", "run-content.py", "run-json.py", "run-learn.py",
                    "run-persona.py", "run-speed.py", "run-tutor.py"]

CRASHED = {"value": "unloaded", "failed": True, "exit_code": 134}
LOADED = {"value": "loaded"}


def load_runner(filename: str):
    name = filename.replace("-", "_").removesuffix(".py") + "_fp"
    spec = importlib.util.spec_from_file_location(name, EVAL / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # @dataclass resolves its module through sys.modules
    spec.loader.exec_module(module)
    return module


def http_500() -> urllib.error.HTTPError:
    return urllib.error.HTTPError(gw.CHAT_URL, 500, "Internal Server Error", {},
                                  io.BytesIO(b""))


class FailurePathCase(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.TemporaryDirectory()
        self.addCleanup(self.out.cleanup)
        # Polling sleeps would make each crash test take CRASH_SETTLE_S.
        self._patch(gw.time, "sleep", lambda _s: None)

    def _patch(self, target, attr, new):
        p = mock.patch.object(target, attr, new)
        p.start()
        self.addCleanup(p.stop)

    def drive(self, filename: str, failure: BaseException, status: dict):
        """Run a runner's main() with every model call failing with `failure`
        and the router reporting `status` for the model. Returns the runner."""
        runner = load_runner(filename)

        def fail(*_a, **_k):
            raise failure

        self._patch(runner, "preflight", lambda *_a, **_k: None)
        for attr in ("generate", "chat"):
            if hasattr(runner, attr):
                self._patch(runner, attr, fail)
        self._patch(_judge, "generate", fail)
        self._patch(gw, "_router_status", lambda _m: dict(status))
        if hasattr(runner, "require_ctx"):
            self._patch(runner, "require_ctx", lambda models, _n: {m: 65536 for m in models})
        if hasattr(runner, "load_model"):
            self._patch(runner, "load_model", lambda *_a, **_k: (1.0, []))
        # Two models: learn and tutor refuse a lineup with no second model to judge it.
        argv = [filename, "--models", "lite", "qwen", "--attempts", "1",
                "--out-root", self.out.name]
        self._patch(sys, "argv", argv)
        self._patch(sys, "stdout", io.StringIO())
        return runner

    def summaries(self) -> list[Path]:
        return list(Path(self.out.name).rglob("summary.md"))


class CrashedModelStopsTheRun(FailurePathCase):
    def test_every_generate_runner_aborts_on_a_crashed_model(self):
        for filename in GENERATE_RUNNERS:
            with self.subTest(runner=filename):
                runner = self.drive(filename, http_500(), CRASHED)
                with self.assertRaises(gw.ModelCrashed) as cm:
                    runner.main()
                self.assertIn("exit code 134", str(cm.exception))
                self.assertEqual(self.summaries(), [], f"{filename} wrote a summary")

    def test_run_tools_aborts_on_a_crashed_model(self):
        runner = self.drive("run-tools.py", http_500(), CRASHED)
        with self.assertRaises(gw.ModelCrashed):
            runner.main()
        self.assertEqual(self.summaries(), [])


class SingleFailureIsRecorded(FailurePathCase):
    def test_timeouts_are_counted_and_the_run_finishes(self):
        for filename in GENERATE_RUNNERS + ["run-tools.py"]:
            with self.subTest(runner=filename):
                runner = self.drive(filename, TimeoutError("timed out"), LOADED)
                runner.main()
                self.assertTrue(self.summaries(), f"{filename} wrote no summary")
                for s in self.summaries():
                    s.unlink()


class JudgeFailurePaths(FailurePathCase):
    """The grading phase is where the 2026-09-17 crash hit."""

    ARGS = ("qwen", "topic", "response", 30, "{topic} {response}", ["clarity"])

    def test_crashed_judge_aborts(self):
        self._patch(gw.time, "sleep", lambda _s: None)
        self._patch(_judge, "generate", mock.Mock(side_effect=http_500()))
        self._patch(gw, "_router_status", lambda _m: dict(CRASHED))
        with self.assertRaises(gw.ModelCrashed):
            _judge.judge_scores(*self.ARGS)

    def test_garbled_judge_reply_is_unparsed_not_fatal(self):
        self._patch(_judge, "generate", mock.Mock(return_value=("no json here", {})))
        scores = _judge.judge_scores(*self.ARGS)
        self.assertIs(scores["_parsed"], False)

    def test_judge_timeout_is_unparsed_not_fatal(self):
        self._patch(_judge, "generate", mock.Mock(side_effect=TimeoutError("slow")))
        scores = _judge.judge_scores(*self.ARGS)
        self.assertIs(scores["_parsed"], False)


if __name__ == "__main__":
    unittest.main()
