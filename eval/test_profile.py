#!/usr/bin/env python3
"""
Unit tests for run-profile.py's --out-root passthrough. No runner is started:
dry runs print the commands, and the one real-run case patches subprocess.run.
Run with:

    python3 -m unittest eval/test_profile.py -v

Why this exists: without the passthrough a verification smoke run writes into
eval/runs/, and promote.py then publishes it as the README leaderboard.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

EVAL = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("run_profile", EVAL / "run-profile.py")
profile = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(profile)


def run_main(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with mock.patch.object(sys, "argv", ["run-profile.py", *argv]), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = profile.main()
    return rc, out.getvalue()


class DryRunTests(unittest.TestCase):
    def test_without_out_root_commands_are_unchanged(self):
        _, text = run_main(["smoke", "--models", "lite", "--dry-run"])
        expected = [" ".join([str(EVAL / step[0]), "--models", "lite", *step[1:]])
                    for step in profile.PROFILES["smoke"]]
        self.assertEqual(text.splitlines(), expected)
        self.assertNotIn("--out-root", text)

    def test_out_root_reaches_every_step(self):
        with tempfile.TemporaryDirectory() as d:
            _, text = run_main(["standard", "--models", "lite", "qwen",
                                "--out-root", d, "--dry-run"])
        lines = text.splitlines()
        self.assertEqual(len(lines), len(profile.PROFILES["standard"]))
        for line in lines:
            self.assertTrue(line.endswith(f"--out-root {d}"), line)

    def test_relative_out_root_is_made_absolute_once(self):
        with tempfile.TemporaryDirectory() as d:
            old = os.getcwd()
            os.chdir(d)
            try:
                _, text = run_main(["smoke", "--models", "lite",
                                    "--out-root", "scratch/v", "--dry-run"])
            finally:
                os.chdir(old)
            want = str(Path(d).resolve() / "scratch" / "v")
        for line in text.splitlines():
            self.assertTrue(line.endswith(f"--out-root {want}"), line)


class AgentSuiteTests(unittest.TestCase):
    """The agent suite runs in smoke and deep. `standard` waits on a measured cost."""

    def agent_lines(self, name: str, *extra: str) -> list[str]:
        _, text = run_main([name, "--models", "lite", "qwen", *extra, "--dry-run"])
        return [ln for ln in text.splitlines() if ln.startswith(str(EVAL / "run-agent.py"))]

    def test_smoke_runs_one_agent_task_once_and_passes_out_root_through(self):
        with tempfile.TemporaryDirectory() as d:
            lines = self.agent_lines("smoke", "--out-root", d)
            self.assertEqual(lines, [f"{EVAL / 'run-agent.py'} --models lite qwen "
                                     f"--tasks fix_failing_test --attempts 1 --out-root {d}"])

    def test_deep_runs_the_whole_agent_suite_at_its_default_attempts(self):
        self.assertEqual(self.agent_lines("deep"),
                         [f"{EVAL / 'run-agent.py'} --models lite qwen"])

    def test_standard_does_not_run_the_agent_suite(self):
        self.assertEqual(self.agent_lines("standard"), [])

    def test_smoke_task_exists_in_the_task_table(self):
        sys.path.insert(0, str(EVAL))
        from agent_tasks import TASKS
        step = next(s for s in profile.PROFILES["smoke"] if s[0] == "run-agent.py")
        self.assertIn(step[step.index("--tasks") + 1], TASKS)


class RealRunTests(unittest.TestCase):
    def test_summaries_are_listed_from_the_out_root_and_eval_runs_is_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            def fake_run(cmd, *a, **k):
                # Stand in for a runner: honour --out-root like the real ones do.
                root = Path(cmd[cmd.index("--out-root") + 1])
                stamp = root / "20260925T000000Z" / Path(cmd[1]).stem.removeprefix("run-")
                stamp.mkdir(parents=True, exist_ok=True)
                (stamp / "summary.md").write_text("# fake\n", encoding="utf-8")
                return mock.Mock(returncode=0)

            before = sorted(p.name for p in profile.RUNS.iterdir()) if profile.RUNS.is_dir() else []
            with mock.patch.object(profile.subprocess, "run", side_effect=fake_run):
                rc, text = run_main(["smoke", "--models", "lite", "--out-root", d])
            after = sorted(p.name for p in profile.RUNS.iterdir()) if profile.RUNS.is_dir() else []

        self.assertEqual(rc, 0)
        self.assertEqual(before, after, "a run with --out-root created something in eval/runs/")
        listed = [ln.strip() for ln in text.splitlines() if ln.strip().endswith("summary.md")]
        self.assertEqual(len(listed), len(profile.PROFILES["smoke"]))
        for path in listed:
            self.assertTrue(path.startswith(str(Path(d).resolve())), path)


if __name__ == "__main__":
    unittest.main()
