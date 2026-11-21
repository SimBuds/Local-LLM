#!/usr/bin/env python3
"""
Unit tests for run-pi.py, the runner that works the shell agent tasks through the
Pi coding agent in its VM guest. No VM, no router and no model. Run with:

    python3 -m unittest eval/test_pi_runner.py -v

MOCK, ANNOUNCED: the guest and Pi are both fakes here. The "guest" is `bash -c`
under a scratch HOME on this machine, and "pi" is a small shell script in that
HOME that prints Pi's JSON events and then does what the test's mode file says
(fix the project, do nothing, hang, crash, or plant a link). Everything else is
real: the tar round trip, the wall clock, the sandbox, and the hidden tests.

A mutation run points RUN_PI_SRC at a scratch copy of the runner, so the tracked
file is never rewritten.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL))

_src = Path(os.environ.get("RUN_PI_SRC", EVAL / "run-pi.py"))
_spec = importlib.util.spec_from_file_location("run_pi", _src)
pi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pi)

import promote  # noqa: E402
from agent_tasks import TASKS, AgentTask, DeskTask  # noqa: E402

TASK = TASKS["fix_failing_test"]

FAKE_PI = r"""#!/usr/bin/env bash
# Fake Pi for eval/test_pi_runner.py. Records its arguments, then acts on the mode.
printf '%s\n' "$@" > "$HOME/pi-args"
pwd > "$HOME/pi-cwd"
if [ "$1" = "--version" ]; then echo "9.9.9"; exit 0; fi
mode=$(cat "$HOME/mode")
echo '{"type":"turn_start"}'
echo '{"type":"tool_execution_start","toolName":"bash"}'
echo '{"type":"tool_execution_end","toolName":"bash","isError":true}'
echo '{"type":"turn_start"}'
echo '{"type":"tool_execution_start","toolName":"edit"}'
echo '{"type":"tool_execution_end","toolName":"edit","isError":false}'
echo 'not json at all'
case "$mode" in
  solve)   cp -r "$HOME/solution/." . ;;
  nothing) ;;
  hang)    sleep 30 ;;
  crash)   echo "boom: model gone" >&2; exit 3 ;;
  aborted) cp -r "$HOME/solution/." .; echo '{"type":"agent_settled","aborted":true}'; exit 0 ;;
  link)    cp -r "$HOME/solution/." .; ln -s /etc/hostname stolen ;;
esac
echo '{"type":"agent_settled","aborted":false}'
"""


class FakeGuestCase(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(td.cleanup)
        self.home = Path(td.name) / "home"
        bindir = self.home / ".pi" / "agent" / "bin"
        bindir.mkdir(parents=True)
        (bindir / "pi").write_text(FAKE_PI, encoding="utf-8")
        (bindir / "pi").chmod(0o755)
        for rel, content in TASK.solution.items():
            path = self.home / "solution" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.out = Path(td.name) / "out"
        self.out.mkdir()
        # The fake guest: the same scripts, run by bash here instead of over ssh.
        self.guest = pi.Guest(["env", f"HOME={self.home}", "bash", "-c"])

    def attempt(self, mode: str, wall_s: int = 20, model: str = "qwen") -> dict:
        (self.home / "mode").write_text(mode, encoding="utf-8")
        with mock.patch("builtins.print"):
            return pi.run_attempt(self.guest, model, TASK, 1, 1, self.out, wall_s, "RUNID")


class AttemptTests(FakeGuestCase):
    def test_a_fixed_project_passes(self):
        r = self.attempt("solve")
        self.assertEqual((r["reason"], r["passed"], r["stop"]), ("pass", True, "done"))

    def test_the_task_files_arrive_in_the_guest_before_pi_runs(self):
        self.attempt("nothing")
        cwd = (self.home / "pi-cwd").read_text(encoding="utf-8").strip()
        self.assertTrue(cwd.endswith("/project"), cwd)
        self.assertIn("RUNID-qwen-fix_failing_test-1", cwd)

    def test_pi_gets_the_instruction_the_model_and_the_isolating_flags(self):
        self.attempt("nothing", model="gemma")
        args = (self.home / "pi-args").read_text(encoding="utf-8").splitlines()
        for flag in ("--offline", "--no-session", "--no-context-files"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--mode") + 1], "json")
        self.assertEqual(args[args.index("--model") + 1], "local-llm/gemma")
        # The instruction is one argument, whatever quotes or newlines it holds.
        said = "\n".join(args[args.index("-p") + 1:])
        self.assertEqual(said, TASK.instruction.rstrip("\n"))

    def test_an_unfixed_project_fails_the_hidden_tests(self):
        r = self.attempt("nothing")
        self.assertEqual((r["reason"], r["passed"], r["tests_ok"]), ("tests-failed", False, False))

    def test_a_run_past_the_wall_clock_is_killed_and_not_a_pass(self):
        r = self.attempt("hang", wall_s=2)
        self.assertEqual((r["reason"], r["passed"]), ("wall-clock", False))
        self.assertLess(r["elapsed_s"], 20)

    def test_a_crashed_pi_is_an_error_with_its_stderr_kept(self):
        r = self.attempt("crash")
        self.assertEqual((r["reason"], r["passed"], r["pi_exit"]), ("error", False, 3))
        self.assertIn("boom: model gone", r["stderr"])

    def test_a_fixed_project_from_an_aborted_run_is_not_a_pass(self):
        r = self.attempt("aborted")
        self.assertEqual((r["reason"], r["passed"]), ("error", False))

    def test_turns_and_tool_calls_are_counted_from_pis_events(self):
        r = self.attempt("solve")
        self.assertEqual((r["turns"], r["tool_calls"], r["tool_errors"]), (2, 2, 1))

    def test_a_link_out_of_the_project_is_refused_not_followed(self):
        r = self.attempt("link")
        self.assertEqual((r["reason"], r["passed"]), ("error", False))
        self.assertIn("archive", r["stderr"])

    def test_the_guest_folder_is_removed_afterwards(self):
        self.attempt("solve")
        self.assertEqual(list((self.home / pi.GUEST_ROOT).iterdir()), [])

    def test_the_transcript_is_written_per_attempt(self):
        self.attempt("solve")
        self.assertTrue((self.out / "fix_failing_test-attempt-1.json").is_file())


class GuestCheckTests(FakeGuestCase):
    def test_a_guest_with_no_route_to_the_router_stops_the_run(self):
        # Nothing listens on the tunnel port under the fake guest, so wget fails.
        with self.assertRaises(SystemExit) as cm:
            pi.check_guest(pi.Guest(["env", f"HOME={self.home}", "PATH=/nonexistent", "/bin/bash", "-c"]))
        self.assertIn("not ready", str(cm.exception))

    def test_a_guest_without_pi_stops_the_run(self):
        (self.home / ".pi" / "agent" / "bin" / "pi").unlink()
        with self.assertRaises(SystemExit):
            pi.check_guest(self.guest)


class ArchiveTests(unittest.TestCase):
    def test_pack_holds_the_project_and_the_instruction_beside_it(self):
        with tarfile.open(fileobj=io.BytesIO(pi.pack(TASK)), mode="r") as tar:
            names = set(tar.getnames())
            said = tar.extractfile("instruction.txt").read().decode("utf-8")
        self.assertEqual(names, {"project", "instruction.txt"} | {f"project/{rel}" for rel in TASK.files})
        self.assertEqual(said, TASK.instruction)

    def test_pack_does_not_carry_the_hidden_tests_or_the_solution(self):
        raw = pi.pack(TASK)
        self.assertNotIn(TASK.tests.encode("utf-8")[:200], raw)

    def test_unpack_refuses_a_path_that_leaves_the_folder(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo("../escaped.txt")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        with tempfile.TemporaryDirectory() as td:
            inner = Path(td) / "inner"
            inner.mkdir()
            with self.assertRaises(tarfile.TarError):
                pi.unpack(buf.getvalue(), str(inner))
            self.assertFalse((Path(td) / "escaped.txt").exists())


class ScopeTests(unittest.TestCase):
    def test_only_the_shell_tasks_are_offered(self):
        self.assertEqual(set(pi.SHELL_TASKS), {k for k, t in TASKS.items() if isinstance(t, AgentTask)})
        self.assertFalse(any(isinstance(t, DeskTask) for t in pi.SHELL_TASKS.values()))
        self.assertEqual(len(pi.SHELL_TASKS), 8)

    def test_asking_for_a_desk_task_is_refused_before_any_work(self):
        with mock.patch.object(sys, "argv", ["run-pi.py", "--models", "qwen", "--tasks", "desk_cancel"]), \
                mock.patch.object(pi, "preflight") as preflight, mock.patch("builtins.print"):
            self.assertEqual(pi.main(), 1)
        preflight.assert_not_called()

    def test_the_tunnel_points_the_guest_port_at_the_router(self):
        argv = pi.ssh_guest("pi@localhost", 22222, Path("/k"), 8081).argv
        self.assertEqual(argv[argv.index("-R") + 1], "8080:localhost:8081")
        self.assertEqual(argv[-1], "pi@localhost")
        self.assertIn("BatchMode=yes", argv)


class SummaryTests(unittest.TestCase):
    def rec(self, task: str, passed: bool, reason: str) -> dict:
        return {"task": task, "attempt": 1, "passed": passed, "reason": reason, "stop": "done",
                "turns": 4, "tool_calls": 6, "tool_errors": 1, "elapsed_s": 10.0}

    def test_the_summary_ranks_by_passes_and_lists_failures(self):
        tasks = [TASKS["fix_failing_test"], TASKS["log_triage"]]
        results = {
            "lite": [self.rec("fix_failing_test", True, "pass"), self.rec("log_triage", False, "wall-clock")],
            "qwen": [self.rec("fix_failing_test", True, "pass"), self.rec("log_triage", True, "pass")],
        }
        with tempfile.TemporaryDirectory() as td:
            pi.write_summary(Path(td), results, tasks, 1, 600, "1.6.0")
            text = (Path(td) / "summary.md").read_text(encoding="utf-8")
        self.assertIn("| 1 | `qwen` | 100% | 2/2 | 4.0 | 6.0 | 2 | 10.0 |", text)
        self.assertIn("| 2 | `lite` | 50% | 1/2 |", text)
        self.assertIn("| `lite` | 1/1 | 0/1 |", text)
        self.assertIn("- `lite` log_triage attempt 1: wall-clock", text)
        self.assertIn("Pi 1.6.0", text)
        self.assertIn("Not like for like", text)

    def test_the_leaderboard_does_not_read_the_pi_folder(self):
        self.assertNotIn("pi", promote.SUITES)


if __name__ == "__main__":
    unittest.main()
