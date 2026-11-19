#!/usr/bin/env python3
"""
Unit tests for run-agent.py and agent_tasks.py.

No live server: `chat` is replaced by a scripted fake that returns what the
gateway would. The sandbox is real, so commands and hidden tests run under
bwrap. Sandbox-backed tests skip, visibly, where bwrap cannot start. Run with:

    python3 -m unittest eval/test_agent.py -v
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL))

_spec = importlib.util.spec_from_file_location("run_agent", EVAL / "run-agent.py")
agent = importlib.util.module_from_spec(_spec)
sys.modules["run_agent"] = agent
_spec.loader.exec_module(agent)

import _gateway as gw  # noqa: E402
import promote  # noqa: E402
from agent_tasks import TASKS, AgentTask  # noqa: E402

TASK = TASKS["fix_failing_test"]
META = {"prompt_eval_count": 500, "eval_count": 20, "eval_duration": 1e9}
FIX = "sed -i 's/qty > BULK_MIN_QTY/qty >= BULK_MIN_QTY/' shop/pricing.py"
N_CTX = 131072


def bash(command: str, call_id: str = "c1") -> dict:
    """A parsed bash tool call in the shape _gateway.chat() returns."""
    return {"id": call_id, "name": "bash", "arguments": {"command": command},
            "arguments_ok": True, "arguments_raw": json.dumps({"command": command})}


def turn(*calls: dict, text: str = "", meta: dict | None = None) -> tuple:
    return text, list(calls), dict(meta or META)


def done(text: str = "Fixed the bulk threshold.") -> tuple:
    return turn(text=text)


class SandboxCase(unittest.TestCase):
    def setUp(self):
        if not gw._sandbox_works():
            self.skipTest("bwrap is not usable on this machine")
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)

    def seeded(self, task: AgentTask = TASK, overlay: dict | None = None) -> str:
        """A project directory holding the task's files, plus an optional overlay."""
        wd = self.tmp / f"wd{len(list(self.tmp.iterdir()))}"
        for rel, content in {**task.files, **(overlay or {})}.items():
            path = wd / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return str(wd)

    def loop(self, script: list, workdir: str | None = None, max_steps: int = 15,
             deadline: float | None = None, n_ctx: int = N_CTX):
        with mock.patch.object(agent, "chat", side_effect=script) as chat:
            out = agent.run_loop("lite", False, TASK, workdir or self.seeded(), {}, 300,
                                 deadline or time.monotonic() + 600, n_ctx, max_steps)
        return out, chat

    def attempt(self, script: list, max_steps: int = 15) -> dict:
        with mock.patch.object(agent, "chat", side_effect=script):
            return agent.run_attempt("lite", TASK, 1, 1, 300, "off", N_CTX, max_steps, self.tmp)


class AttemptTests(SandboxCase):
    def test_model_that_fixes_the_bug_and_stops_passes(self):
        r = self.attempt([turn(bash(FIX)), turn(bash("python3 test_cart.py")), done()])
        self.assertEqual((r["passed"], r["reason"], r["steps"]), (True, "pass", 3))
        saved = json.loads((self.tmp / "fix_failing_test-attempt-1.json").read_text())
        self.assertEqual(saved["reason"], "pass")
        tool_results = [m["content"] for m in saved["messages"] if m["role"] == "tool"]
        self.assertTrue(tool_results[1].startswith("[exit 0]"))
        self.assertIn("OK", tool_results[1])

    def test_model_that_stops_without_acting_fails_the_tests(self):
        r = self.attempt([done("Looks fine to me.")])
        self.assertEqual((r["passed"], r["reason"], r["steps"]), (False, "tests-failed", 1))

    def test_model_that_never_stops_hits_the_step_cap(self):
        r = self.attempt([turn(bash("true"))] * 3, max_steps=3)
        self.assertEqual((r["passed"], r["reason"], r["steps"]), (False, "step-cap", 3))

    def test_fixing_the_bug_but_never_stopping_is_not_a_pass(self):
        r = self.attempt([turn(bash(FIX)), turn(bash("true")), turn(bash("true"))], max_steps=3)
        self.assertEqual((r["passed"], r["reason"], r["tests_ok"]), (False, "step-cap", True))

    def test_failed_model_call_is_recorded_and_not_scored(self):
        err = urllib.error.URLError("connection refused")
        with mock.patch.object(agent, "score") as score:
            r = self.attempt([err])
        self.assertEqual((r["passed"], r["reason"]), (False, "gen-fail"))
        self.assertIs(r["exc"], err)
        score.assert_not_called()

    def test_model_call_carries_the_suite_prompt_tools_and_ceiling(self):
        with mock.patch.object(agent, "chat", side_effect=[done()]) as chat:
            agent.run_attempt("lite", TASK, 1, 1, 300, "off", N_CTX, 15, self.tmp, seed=7)
        kwargs = chat.call_args.kwargs
        self.assertEqual(kwargs["system"], agent.PROMPT)
        self.assertEqual(kwargs["tools"], agent.TOOLS)
        self.assertEqual(kwargs["options"]["num_predict"], agent.NUM_PREDICT)
        self.assertIn("seed", kwargs["options"])
        self.assertIs(kwargs["think"], False)


class LoopTests(SandboxCase):
    def test_passed_deadline_stops_before_any_model_call(self):
        out, chat = self.loop([done()], deadline=time.monotonic() - 1)
        self.assertEqual((out["stop"], out["steps"]), ("wall-clock", 0))
        chat.assert_not_called()

    def test_model_timeout_is_a_gen_timeout(self):
        out, _ = self.loop([TimeoutError("timed out")])
        self.assertEqual(out["stop"], "gen-timeout")
        self.assertIn("exc", out)

    def test_prompt_near_the_context_limit_stops_without_another_call(self):
        near = {**META, "prompt_eval_count": N_CTX - 100}
        out, chat = self.loop([turn(bash("true"), meta=near), done()])
        self.assertEqual((out["stop"], out["steps"]), ("context", 1))
        self.assertEqual(chat.call_count, 1)

    def test_malformed_arguments_are_reported_and_nothing_runs(self):
        bad = {"id": "c1", "name": "bash", "arguments": {}, "arguments_ok": False,
               "arguments_raw": '{"command": "touch made-it'}
        wd = self.seeded()
        out, _ = self.loop([turn(bad), done()], workdir=wd)
        self.assertEqual((out["stop"], out["bad_calls"]), ("done", 1))
        self.assertTrue(out["messages"][2]["content"].startswith("error:"))
        self.assertFalse((Path(wd) / "made-it").exists())

    def test_unknown_tool_is_reported_to_the_model(self):
        call = {**bash("ls"), "name": "python"}
        out, _ = self.loop([turn(call), done()])
        self.assertEqual(out["bad_calls"], 1)
        self.assertIn("unknown tool 'python'", out["messages"][2]["content"])

    def test_two_calls_in_one_turn_get_two_results_in_order(self):
        out, _ = self.loop([turn(bash("echo first", "a"), bash("echo second", "b")), done()])
        tools = [m for m in out["messages"] if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tools], ["a", "b"])
        self.assertEqual([m["content"] for m in tools], ["[exit 0]\nfirst\n", "[exit 0]\nsecond\n"])
        self.assertEqual([c["id"] for c in out["messages"][1]["tool_calls"]], ["a", "b"])


class ExecuteCallTests(SandboxCase):
    def test_command_timeout_is_reported(self):
        text, ok = agent.execute_call(bash("sleep 5"), self.seeded(), cmd_timeout=1)
        self.assertTrue(text.startswith("[timed out after 1s]"))
        self.assertTrue(ok)

    def test_long_output_is_clipped_with_a_marker(self):
        text, _ = agent.execute_call(bash("seq 1 5000"), self.seeded())
        self.assertIn("characters omitted", text)
        self.assertLess(len(text), agent.OUTPUT_CAP + 100)
        self.assertIn("\n5000\n", text)


class ClipTests(unittest.TestCase):
    def test_short_text_is_unchanged(self):
        self.assertEqual(agent.clip("abc", 10), "abc")
        self.assertEqual(agent.clip("a" * 10, 10), "a" * 10)

    def test_long_text_keeps_head_and_tail_and_counts_the_gap(self):
        out = agent.clip("H" * 5 + "m" * 90 + "T" * 5, 10)
        self.assertTrue(out.startswith("HHHHH\n"))
        self.assertTrue(out.endswith("\nTTTTT"))
        self.assertIn("[... 90 characters omitted ...]", out)


class ScoreTests(SandboxCase):
    def test_early_exit_zero_does_not_count_as_passing(self):
        task = AgentTask("t", "", "", {"m.py": "import sys\nsys.exit(0)\n"},
                         "import m\nassert False\n", {}, {}, {})
        self.assertFalse(agent.score(task, self.seeded(task)))

    def test_passing_tests_score_true(self):
        task = AgentTask("t", "", "", {"m.py": "X = 1\n"}, "import m\nassert m.X == 1\n", {}, {}, {})
        self.assertTrue(agent.score(task, self.seeded(task)))


class TaskTableTests(SandboxCase):
    """Every task's hidden tests must be sound in both directions."""

    def test_seed_project_fails_the_hidden_tests(self):
        for task in TASKS.values():
            with self.subTest(task.key):
                self.assertFalse(agent.score(task, self.seeded(task)))

    def test_reference_solution_passes(self):
        for task in TASKS.values():
            with self.subTest(task.key):
                self.assertTrue(task.solution)
                self.assertTrue(agent.score(task, self.seeded(task, task.solution)))

    def test_alternate_solution_passes(self):
        for task in TASKS.values():
            with self.subTest(task.key):
                self.assertTrue(task.alt_solution)
                self.assertNotEqual(task.alt_solution, task.solution)
                self.assertTrue(agent.score(task, self.seeded(task, task.alt_solution)))

    def test_cheating_solution_fails(self):
        for task in TASKS.values():
            with self.subTest(task.key):
                self.assertTrue(task.exploit)
                self.assertFalse(agent.score(task, self.seeded(task, task.exploit)))

    def test_hidden_tests_are_not_in_the_project_files(self):
        for task in TASKS.values():
            asserts = [ln for ln in task.tests.splitlines() if ln.startswith("assert")]
            self.assertTrue(asserts)
            for content in task.files.values():
                for line in asserts:
                    with self.subTest(task.key, line=line):
                        self.assertNotIn(line, content)

    def test_fix_failing_test_exploit_does_satisfy_the_visible_test(self):
        code, _ = gw.sandbox_exec([sys.executable, "test_cart.py"],
                                  self.seeded(TASK, TASK.exploit), 30)
        self.assertEqual(code, 0)
        code, _ = gw.sandbox_exec([sys.executable, "test_cart.py"], self.seeded(TASK), 30)
        self.assertNotEqual(code, 0)

    def test_cheats_that_have_a_visible_test_to_satisfy_do_satisfy_it(self):
        for key, visible in (("refactor_keep_tests", "test_inventory.py"),
                             ("implement_spec", "test_examples.py"),
                             ("flaky_test", "test_inbox.py"),
                             ("flaky_test", "run_reversed.py")):
            with self.subTest(key):
                task = TASKS[key]
                code, out = gw.sandbox_exec([sys.executable, visible],
                                            self.seeded(task, task.exploit), 30)
                self.assertEqual(code, 0, out)


class SummaryTests(unittest.TestCase):
    def row(self, passed: bool, steps: int) -> dict:
        return {"task": "fix_failing_test", "passed": passed, "steps": steps,
                "elapsed_s": 12.0, "tok_per_s": 100.0, "reason": "pass" if passed else "step-cap"}

    def test_summary_is_readable_by_promote(self):
        with tempfile.TemporaryDirectory() as td:
            results = {"lite": [self.row(True, 3), self.row(False, 15)],
                       "qwen": [self.row(True, 4), self.row(True, 6)]}
            with mock.patch("builtins.print"):
                agent.write_summary(Path(td), results, [TASK], 2, 15)
            text = (Path(td) / "summary.md").read_text()
        self.assertEqual(promote.WINNER_RE.search(text).group(1), "qwen")
        rows = promote.parse_table(text)
        self.assertEqual([(r["Model"], r["Passed"], r["Pass rate"]) for r in rows],
                         [("`qwen`", "2/2", "100%"), ("`lite`", "1/2", "50%")])
        self.assertEqual(rows[0]["Avg steps"], "5.0")


class MainTests(unittest.TestCase):
    def test_unknown_task_exits_before_preflight_or_any_run_dir(self):
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(agent, "preflight") as preflight, \
                mock.patch("sys.stderr"), \
                mock.patch.object(sys, "argv", ["run-agent.py", "--models", "lite",
                                                "--tasks", "nope", "--out-root", td]):
            self.assertEqual(agent.main(), 1)
            preflight.assert_not_called()
            self.assertEqual(list(Path(td).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
