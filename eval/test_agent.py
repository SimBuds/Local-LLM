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
import desk  # noqa: E402
from agent_tasks import TASKS, AgentTask, DeskTask  # noqa: E402

TASK = TASKS["fix_failing_test"]
SHELL_TASKS = [t for t in TASKS.values() if isinstance(t, AgentTask)]
DESK_TASKS = [t for t in TASKS.values() if isinstance(t, DeskTask)]
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
            wd = workdir or self.seeded()
            out = agent.run_loop("lite", False, TASK.instruction, agent.TOOLS, agent.PROMPT,
                                 lambda call: agent.execute_call(call, wd), {}, 300,
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


class InjectedToolsTests(unittest.TestCase):
    """run_loop takes its tools, prompt, and executor from the caller, so a task
    kind with no sandbox can use the same loop. No bwrap needed here."""

    LOOKUP = [{"type": "function", "function": {
        "name": "lookup", "description": "Look something up.",
        "parameters": {"type": "object", "properties": {"key": {"type": "string"}},
                       "required": ["key"]}}}]

    def call(self, key: str) -> dict:
        return {"id": "k1", "name": "lookup", "arguments": {"key": key},
                "arguments_ok": True, "arguments_raw": json.dumps({"key": key})}

    def loop(self, script: list, execute):
        with mock.patch.object(agent, "chat", side_effect=script) as chat:
            out = agent.run_loop("lite", False, "Find the code.", self.LOOKUP, "DESK PROMPT",
                                 execute, {}, 300, time.monotonic() + 600, N_CTX, 15)
        return out, chat

    def test_loop_offers_the_callers_tools_prompt_and_instruction(self):
        out, chat = self.loop([done("It is 7.")], lambda call: ("unused", True))
        kwargs = chat.call_args.kwargs
        self.assertEqual((kwargs["tools"], kwargs["system"]), (self.LOOKUP, "DESK PROMPT"))
        self.assertEqual(chat.call_args.args[1][0], {"role": "user", "content": "Find the code."})
        self.assertEqual(out["stop"], "done")

    def test_loop_routes_calls_to_the_callers_executor_and_feeds_back_its_text(self):
        seen = []

        def execute(call):
            seen.append(call["arguments"])
            return ("value: 7", True) if call["arguments"]["key"] == "code" else ("no such key", False)

        out, _ = self.loop([turn(self.call("code")), turn(self.call("nope")), done()], execute)
        self.assertEqual(seen, [{"key": "code"}, {"key": "nope"}])
        tools = [m["content"] for m in out["messages"] if m["role"] == "tool"]
        self.assertEqual(tools, ["value: 7", "no such key"])
        self.assertEqual((out["steps"], out["bad_calls"], out["stop"]), (3, 1, "done"))


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
        for task in SHELL_TASKS:
            with self.subTest(task.key):
                self.assertFalse(agent.score(task, self.seeded(task)))

    def test_reference_solution_passes(self):
        for task in SHELL_TASKS:
            with self.subTest(task.key):
                self.assertTrue(task.solution)
                self.assertTrue(agent.score(task, self.seeded(task, task.solution)))

    def test_alternate_solution_passes(self):
        for task in SHELL_TASKS:
            with self.subTest(task.key):
                self.assertTrue(task.alt_solution)
                self.assertNotEqual(task.alt_solution, task.solution)
                self.assertTrue(agent.score(task, self.seeded(task, task.alt_solution)))

    def test_cheating_solution_fails(self):
        for task in SHELL_TASKS:
            with self.subTest(task.key):
                self.assertTrue(task.exploit)
                self.assertFalse(agent.score(task, self.seeded(task, task.exploit)))

    def test_hidden_tests_are_not_in_the_project_files(self):
        for task in SHELL_TASKS:
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


def desk_call(name: str, arguments: dict, call_id: str = "d1") -> dict:
    """A parsed tool call in the shape _gateway.chat() returns."""
    return {"id": call_id, "name": name, "arguments": arguments, "arguments_ok": True,
            "arguments_raw": json.dumps(arguments)}


def replay(calls) -> tuple[dict, list[bool]]:
    """A fresh shop after a sequence of (tool, arguments) calls, and whether each
    call was well formed."""
    state = desk.new_state()
    return state, [desk.dispatch(state, desk_call(name, args))[1] for name, args in calls]


class DeskToolTests(unittest.TestCase):
    """The simulated shop. Pure Python, no sandbox and no model."""

    def run_tool(self, state: dict, name: str, **arguments) -> tuple[str, bool]:
        return desk.dispatch(state, desk_call(name, arguments))

    def test_new_state_is_a_fresh_copy_each_time(self):
        a, b = desk.new_state(), desk.new_state()
        a["orders"]["A101"]["status"] = "cancelled"
        self.assertEqual(b["orders"]["A101"]["status"], "pending")
        self.assertEqual(desk.new_state(), b)

    def test_every_offered_tool_has_a_handler_and_the_reverse(self):
        offered = [t["function"]["name"] for t in desk.TOOLS]
        self.assertEqual(sorted(offered), sorted(desk.HANDLERS))
        self.assertEqual(len(offered), len(set(offered)))

    def test_read_tools_report_the_records_and_change_nothing(self):
        state = desk.new_state()
        text, ok = self.run_tool(state, "find_customer", email="ANA@example.org ")
        self.assertEqual((ok, "C1" in text, "Ana Ruiz" in text), (True, True, True))
        text, ok = self.run_tool(state, "list_orders", customer_id="C1")
        self.assertTrue(ok)
        for order_id in ("A101", "A102", "A103"):
            self.assertIn(order_id, text)
        self.assertNotIn("B201", text)
        text, ok = self.run_tool(state, "get_order", order_id="A103")
        self.assertEqual((ok, "delivered" in text, "5900" in text), (True, True, True))
        self.assertEqual(state, desk.new_state())

    def test_write_tools_change_the_records(self):
        state = desk.new_state()
        self.assertTrue(self.run_tool(state, "cancel_order", order_id="A101")[1])
        self.assertTrue(self.run_tool(state, "update_address", order_id="A101", address="1 New Rd")[1])
        self.run_tool(state, "refund_order", order_id="A103", amount_cents=1000)
        self.run_tool(state, "refund_order", order_id="A103", amount_cents=500)
        order = state["orders"]
        self.assertEqual((order["A101"]["status"], order["A101"]["address"],
                          order["A103"]["refunded_cents"]), ("cancelled", "1 New Rd", 1500))

    def test_tools_do_not_enforce_the_policy(self):
        # The model has to. A tool that refused would do the model's job for it.
        state = desk.new_state()
        self.run_tool(state, "cancel_order", order_id="A102")            # shipped
        self.run_tool(state, "update_address", order_id="A103", address="x")   # delivered
        self.run_tool(state, "refund_order", order_id="A101", amount_cents=99999)  # pending, over
        order = state["orders"]
        self.assertEqual((order["A102"]["status"], order["A103"]["address"],
                          order["A101"]["refunded_cents"]), ("cancelled", "x", 99999))

    def test_unknown_ids_are_reported_and_change_nothing(self):
        state = desk.new_state()
        for name, arguments in (("find_customer", {"email": "nobody@example.org"}),
                                ("list_orders", {"customer_id": "C9"}),
                                ("get_order", {"order_id": "Z1"}),
                                ("cancel_order", {"order_id": "Z1"}),
                                ("update_address", {"order_id": "Z1", "address": "x"}),
                                ("refund_order", {"order_id": "Z1", "amount_cents": 5})):
            with self.subTest(name):
                text, ok = self.run_tool(state, name, **arguments)
                self.assertTrue(text.startswith("error:"), text)
                self.assertTrue(ok, "a well-formed call about a missing record is not malformed")
        self.assertEqual(state, desk.new_state())

    def test_malformed_calls_are_reported_and_never_raise(self):
        state = desk.new_state()
        bad = [desk_call("delete_everything", {}),
               desk_call("cancel_order", {}),
               desk_call("cancel_order", {"order_id": 101}),
               desk_call("cancel_order", {"order_id": ["A101"]}),
               desk_call("update_address", {"order_id": "A101"}),
               desk_call("update_address", {"order_id": "A101", "address": ""}),
               desk_call("refund_order", {"order_id": "A103", "amount_cents": "5900"}),
               desk_call("refund_order", {"order_id": "A103", "amount_cents": -5}),
               desk_call("refund_order", {"order_id": "A103", "amount_cents": 0}),
               desk_call("refund_order", {"order_id": "A103", "amount_cents": True}),
               desk_call("refund_order", {"order_id": "A103", "amount_cents": 12.5}),
               {**desk_call("cancel_order", {}), "arguments_ok": False,
                "arguments_raw": '{"order_id": "A1'},
               {"id": "x", "name": None, "arguments": None, "arguments_ok": True},
               {}]
        for call in bad:
            with self.subTest(call=call):
                text, ok = desk.dispatch(state, call)
                self.assertEqual((text.startswith("error:"), ok), (True, False), text)
        self.assertEqual(state, desk.new_state())

    def test_same_state_ignores_case_spacing_and_end_punctuation_in_text(self):
        a, b = desk.new_state(), desk.new_state()
        a["orders"]["A101"]["address"] = "40 Pine Rd, Leeds"
        b["orders"]["A101"]["address"] = "  40 pine  rd, leeds. "
        self.assertTrue(desk.same_state(a, b))
        b["orders"]["A101"]["address"] = "40 Pine Road, Leeds"
        self.assertFalse(desk.same_state(a, b))

    def test_same_state_sees_any_other_difference(self):
        base = desk.new_state()
        for change in (lambda s: s["orders"]["A103"].update(refunded_cents=1),
                       lambda s: s["orders"]["B201"].update(status="cancelled"),
                       lambda s: s["orders"].pop("C301"),
                       lambda s: s["customers"]["C1"].update(email="x@example.org")):
            other = desk.new_state()
            change(other)
            self.assertFalse(desk.same_state(base, other))
        self.assertTrue(desk.same_state(base, desk.new_state()))

    def test_patched_applies_order_changes_to_a_copy(self):
        start = desk.new_state()
        after = desk.patched(start, {"A101": {"status": "cancelled"}})
        self.assertEqual(after["orders"]["A101"]["status"], "cancelled")
        self.assertEqual(start, desk.new_state())
        with self.assertRaises(KeyError):
            desk.patched(start, {"Z9": {"status": "cancelled"}})
        with self.assertRaises(KeyError):
            desk.patched(start, {"A101": {"colour": "red"}})


class DeskTaskTableTests(unittest.TestCase):
    """Every desk task's expected records must be sound in both directions."""

    def expected(self, task: DeskTask) -> dict:
        return desk.patched(desk.new_state(), task.changes)

    def test_the_six_desk_tasks_follow_the_eight_shell_tasks(self):
        self.assertEqual([t.key for t in DESK_TASKS],
                         ["desk_cancel", "desk_two_changes", "desk_forbidden",
                          "desk_half_allowed", "desk_which_lamp", "desk_not_yours"])
        self.assertEqual(len(SHELL_TASKS), 8)
        self.assertEqual(list(TASKS.values()), SHELL_TASKS + DESK_TASKS)

    def test_some_tasks_need_a_refusal_and_some_need_a_change(self):
        refusals = [t.key for t in DESK_TASKS if not t.changes]
        self.assertEqual(refusals, ["desk_forbidden", "desk_not_yours"])

    def test_every_shortcut_makes_at_least_one_change(self):
        # A shortcut that only reads would leave the shop untouched, which is
        # the right answer for a refusal task.
        writes = {"cancel_order", "update_address", "refund_order"}
        for task in DESK_TASKS:
            with self.subTest(task.key):
                self.assertTrue(any(name in writes for name, _ in task.exploit))

    def test_refusal_solutions_make_no_change(self):
        writes = {"cancel_order", "update_address", "refund_order"}
        for task in DESK_TASKS:
            if not task.changes:
                with self.subTest(task.key):
                    self.assertFalse(any(name in writes for name, _ in
                                         task.solution + task.alt_solution))

    def test_untouched_shop_passes_only_a_task_that_asks_for_no_change(self):
        for task in DESK_TASKS:
            with self.subTest(task.key):
                self.assertEqual(desk.same_state(desk.new_state(), self.expected(task)),
                                 not task.changes)

    def test_reference_calls_reach_the_expected_records(self):
        for task in DESK_TASKS:
            with self.subTest(task.key):
                state, well_formed = replay(task.solution)
                self.assertTrue(all(well_formed))
                self.assertTrue(desk.same_state(state, self.expected(task)))

    def test_alternate_calls_reach_them_too(self):
        for task in DESK_TASKS:
            with self.subTest(task.key):
                self.assertNotEqual(task.alt_solution, task.solution)
                state, well_formed = replay(task.alt_solution)
                self.assertTrue(all(well_formed))
                self.assertTrue(desk.same_state(state, self.expected(task)))

    def test_shortcut_calls_are_well_formed_and_leave_the_wrong_records(self):
        for task in DESK_TASKS:
            with self.subTest(task.key):
                state, well_formed = replay(task.exploit)
                self.assertTrue(all(well_formed), "a shortcut that errors proves nothing")
                self.assertFalse(desk.same_state(state, self.expected(task)))

    def test_request_gives_no_order_id_and_none_of_the_policy(self):
        policy = [ln for ln in agent.DESK_PROMPT.splitlines() if ln[:1].isdigit()]
        self.assertGreaterEqual(len(policy), 4)
        for task in DESK_TASKS:
            with self.subTest(task.key):
                for order_id in desk.new_state()["orders"]:
                    self.assertNotIn(order_id, task.instruction)
                for rule in policy:
                    self.assertNotIn(rule, task.instruction)
                self.assertIn("@example.org", task.instruction)


class DeskAttemptTests(unittest.TestCase):
    """A whole desk attempt with a scripted model. No sandbox is involved."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.task = DESK_TASKS[0]

    def turns(self, calls) -> list:
        return [turn(desk_call(name, args)) for name, args in calls]

    def attempt(self, script: list, max_steps: int = 15):
        with mock.patch.object(agent, "chat", side_effect=script) as chat:
            record = agent.run_attempt("lite", self.task, 1, 1, 300, "off", N_CTX, max_steps,
                                       self.tmp)
        return record, chat

    def test_model_that_makes_the_right_calls_and_stops_passes(self):
        r, chat = self.attempt(self.turns(self.task.solution) + [done("All sorted.")])
        self.assertEqual((r["passed"], r["reason"], r["steps"]),
                         (True, "pass", len(self.task.solution) + 1))
        kwargs = chat.call_args.kwargs
        self.assertEqual((kwargs["tools"], kwargs["system"]), (desk.TOOLS, agent.DESK_PROMPT))
        saved = json.loads((self.tmp / f"{self.task.key}-attempt-1.json").read_text())
        self.assertEqual(saved["reason"], "pass")
        self.assertEqual(saved["messages"][0]["content"], self.task.instruction)

    def test_attempt_stops_at_its_own_wall_clock_limit(self):
        # A limit already spent stops the loop before the first model call, and
        # the same script passes under the default limit.
        script = self.turns(self.task.solution) + [done()]
        with mock.patch.object(agent, "chat", side_effect=list(script)) as chat:
            r = agent.run_attempt("lite", self.task, 1, 1, 300, "off", N_CTX, 15, self.tmp,
                                  wall_s=0)
        self.assertEqual((r["reason"], r["steps"], chat.call_count), ("wall-clock", 0, 0))
        r, _ = self.attempt(list(script))
        self.assertEqual(r["reason"], "pass")

    def test_model_that_replies_without_acting_fails(self):
        r, _ = self.attempt([done("I have cancelled it for you.")])
        self.assertEqual((r["passed"], r["reason"]), (False, "tests-failed"))

    def test_model_that_takes_the_shortcut_fails(self):
        r, _ = self.attempt(self.turns(self.task.exploit) + [done()])
        self.assertEqual((r["passed"], r["reason"]), (False, "tests-failed"))

    def test_right_records_without_ending_the_loop_is_not_a_pass(self):
        script = self.turns(self.task.solution)
        r, _ = self.attempt(script, max_steps=len(script))
        self.assertEqual((r["passed"], r["reason"], r["tests_ok"]), (False, "step-cap", True))

    def test_failed_model_call_is_recorded_and_not_scored(self):
        err = urllib.error.URLError("connection refused")
        r, _ = self.attempt([err])
        self.assertEqual((r["passed"], r["reason"], r["tests_ok"]), (False, "gen-fail", False))
        self.assertIs(r["exc"], err)

    def test_each_attempt_starts_from_a_fresh_shop(self):
        first, _ = self.attempt(self.turns(self.task.solution) + [done()])
        again, _ = self.attempt(self.turns(self.task.solution) + [done()])
        self.assertEqual((first["passed"], again["passed"]), (True, True))
        self.assertEqual(desk.SHOP, desk.new_state())

    def test_tool_results_reach_the_model(self):
        r, _ = self.attempt(self.turns(self.task.solution[:1]) + [done()])
        saved = json.loads((self.tmp / f"{self.task.key}-attempt-1.json").read_text())
        tool = [m["content"] for m in saved["messages"] if m["role"] == "tool"]
        self.assertEqual(len(tool), 1)
        self.assertIn("C1", tool[0])


class SummaryTests(unittest.TestCase):
    def row(self, passed: bool, steps: int, task: str = "fix_failing_test",
            reason: str | None = None, tests_ok: bool | None = None, bad_calls: int = 0,
            prompt_tokens: int = 2000) -> dict:
        return {"task": task, "passed": passed, "steps": steps,
                "elapsed_s": 12.0, "tok_per_s": 100.0, "bad_calls": bad_calls,
                "prompt_tokens": prompt_tokens,
                "tests_ok": passed if tests_ok is None else tests_ok,
                "reason": reason or ("pass" if passed else "step-cap")}

    def summary(self, results: dict, tasks: list | None = None, attempts: int = 2) -> str:
        with tempfile.TemporaryDirectory() as td, mock.patch("builtins.print"):
            agent.write_summary(Path(td), results, tasks or [TASK], attempts, 30)
            return (Path(td) / "summary.md").read_text()

    def section(self, text: str, heading: str) -> str:
        """The body of one `### ` section."""
        self.assertIn(f"### {heading}", text)
        return text.split(f"### {heading}", 1)[1].split("\n### ", 1)[0]

    def test_per_task_matrix_counts_each_task_separately(self):
        triage = TASKS["log_triage"]
        text = self.summary({"lite": [self.row(True, 3), self.row(False, 9),
                                      self.row(True, 5, triage.key), self.row(True, 6, triage.key)]},
                            [TASK, triage])
        body = self.section(text, "Per-task (passed / attempts)")
        self.assertIn("| Model | fix_failing_test | log_triage |", body)
        self.assertIn("| `lite` | 1/2 | 2/2 |", body)
        self.assertIn(f"| `log_triage` | {triage.why} |", self.section(text, "What each task catches"))

    def test_failure_shapes_count_reasons_and_mark_capped_attempts_that_were_correct(self):
        text = self.summary({"lite": [
            self.row(True, 3),
            self.row(False, 4, reason="tests-failed", bad_calls=2),
            self.row(False, 5, reason="tests-failed", bad_calls=1),
            self.row(False, 30, reason="step-cap", tests_ok=True),
            self.row(False, 30, reason="step-cap", tests_ok=False),
            self.row(False, 2, reason="gen-timeout")]}, attempts=6)
        body = self.section(text, "Failure shapes observed")
        self.assertIn("- `lite`: tests-failed ×2, gen-timeout ×1, step-cap ×1, "
                      "step-cap (tests passing) ×1. Malformed or unknown tool calls: 3.", body)

    def test_model_with_no_failures_reports_none(self):
        body = self.section(self.summary({"qwen": [self.row(True, 3), self.row(True, 4)]}),
                            "Failure shapes observed")
        self.assertIn("- `qwen`: none. Malformed or unknown tool calls: 0.", body)

    def test_model_whose_every_call_failed_is_ranked_at_zero_without_error(self):
        dead = [self.row(False, 0, reason="gen-fail", prompt_tokens=0) for _ in range(2)]
        text = self.summary({"qwen": [self.row(True, 3), self.row(True, 4)], "gemma": dead})
        rows = promote.parse_table(text)
        self.assertEqual([(r["Model"], r["Passed"]) for r in rows],
                         [("`qwen`", "2/2"), ("`gemma`", "0/2")])
        self.assertIn("- `gemma`: gen-fail ×2.", self.section(text, "Failure shapes observed"))

    def test_uncertainty_gives_interval_weakest_task_and_small_sample_flag(self):
        triage = TASKS["log_triage"]
        text = self.summary({"lite": [self.row(False, 9), self.row(False, 9),
                                      self.row(True, 5, triage.key), self.row(True, 6, triage.key)]},
                            [TASK, triage])
        body = self.section(text, "Uncertainty")
        self.assertIn(f"- `lite`: 50% (95% CI {gw.ci_str(2, 4)}); weakest `fix_failing_test` "
                      f"at 0%; spread 0–100%; {gw.sample_caveat(4)}", body)

    def test_single_model_gets_no_close_result_note_and_a_tie_does(self):
        alone = self.summary({"lite": [self.row(True, 3), self.row(True, 4)]})
        self.assertNotIn("Close result", alone)
        tied = self.summary({"lite": [self.row(True, 3), self.row(False, 4)],
                             "qwen": [self.row(True, 3), self.row(False, 9)]})
        self.assertIn("Close result", tied)
        self.assertEqual(promote.WINNER_RE.search(tied).group(1), "lite")

    def desk_row(self, passed: bool) -> dict:
        return self.row(passed, 4, task="desk_cancel", reason="pass" if passed else "tests-failed")

    def test_header_says_how_each_kind_of_task_is_scored(self):
        shell = self.summary({"lite": [self.row(True, 3)]})
        desk_only = self.summary({"lite": [self.desk_row(True)]}, [TASKS["desk_cancel"]])
        mixed = self.summary({"lite": [self.row(True, 3), self.desk_row(False)]},
                             [TASK, TASKS["desk_cancel"]])
        self.assertIn("in a sandbox", shell)
        self.assertNotIn("simulated shop", shell)
        self.assertIn("simulated shop", desk_only)
        self.assertNotIn("in a sandbox", desk_only)
        self.assertIn("in a sandbox", mixed)
        self.assertIn("simulated shop", mixed)
        self.assertIn("| `lite` | 1/1 | 0/1 |", mixed)

    def test_table_reports_the_average_final_prompt_size(self):
        text = self.summary({"lite": [self.row(True, 3, prompt_tokens=1000),
                                      self.row(True, 4, prompt_tokens=2500)]})
        self.assertEqual(promote.parse_table(text)[0]["Avg prompt tok"], "1750")

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


class PromoteTests(unittest.TestCase):
    """The README leaderboard is built by promote.py from the newest summary per suite."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.runs = Path(td.name)
        patch = mock.patch.object(promote, "RUNS", self.runs)
        patch.start()
        self.addCleanup(patch.stop)

    def row(self, passed: bool, steps: int) -> dict:
        return {"task": TASK.key, "passed": passed, "steps": steps, "elapsed_s": 12.0,
                "tok_per_s": 100.0, "bad_calls": 0, "prompt_tokens": 2000, "tests_ok": passed,
                "reason": "pass" if passed else "tests-failed"}

    def agent_run(self, results: dict) -> None:
        run_dir = self.runs / "20261119T120000Z" / "agent"
        run_dir.mkdir(parents=True)
        with mock.patch("builtins.print"):
            agent.write_summary(run_dir, results, [TASK], 2, 30)

    def agent_row(self) -> str:
        block, _ = promote.build_block(None)
        rows = [ln for ln in block.splitlines() if ln.startswith("| Agentic tasks |")]
        self.assertEqual(len(rows), 1, block)
        return rows[0]

    def test_agent_summary_becomes_a_leaderboard_row_with_passed_and_steps(self):
        self.agent_run({"lite": [self.row(True, 3), self.row(False, 15)],
                        "qwen": [self.row(True, 4), self.row(True, 6)]})
        self.assertEqual(self.agent_row(), "| Agentic tasks | `qwen` | 2/2, 5.0 | 1/2, 9.0 |")
        block, _ = promote.build_block(None)
        self.assertIn("- Agentic tasks: `eval/runs/20261119T120000Z/agent/summary.md`", block)

    def test_tied_agent_result_reads_as_a_tie(self):
        self.agent_run({"lite": [self.row(True, 3), self.row(False, 5)],
                        "qwen": [self.row(True, 4), self.row(False, 6)]})
        self.assertEqual(self.agent_row(), "| Agentic tasks | tie | 1/2, 4.0 | 1/2, 5.0 |")

    def test_suite_reads_not_run_when_only_other_suites_have_results(self):
        speed = self.runs / "20261119T120000Z" / "speed"
        speed.mkdir(parents=True)
        (speed / "summary.md").write_text(
            "## 🏆 Fastest: `lite` — 130 tok/s\n\n| Rank | Model | Gen tok/s |\n"
            "|---|---|---|\n| 1 | `lite` | 130 |\n", encoding="utf-8")
        self.assertEqual(self.agent_row(), "| Agentic tasks | _not run_ | — |")


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

    def run_main(self, keys: list[str], *extra: str) -> tuple[str, mock.Mock]:
        """main() with a model that replies at once and never calls a tool."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.out_root = Path(td.name)
        argv = ["run-agent.py", "--models", "lite", "--tasks", *keys, "--attempts", "1",
                "--out-root", td.name, *extra]
        with mock.patch.object(agent, "preflight"), \
                mock.patch.object(agent, "require_ctx", return_value={"lite": N_CTX}), \
                mock.patch.object(agent, "require_sandbox") as probe, \
                mock.patch.object(agent, "chat", side_effect=lambda *a, **k: done("Done.")), \
                mock.patch.object(sys, "argv", argv), mock.patch("builtins.print") as out:
            self.assertEqual(agent.main(), 0)
        printed = "\n".join(str(c.args[0]) for c in out.call_args_list if c.args)
        return printed, probe

    def test_desk_only_run_needs_no_sandbox(self):
        printed, probe = self.run_main(["desk_forbidden"])
        probe.assert_not_called()
        self.assertNotIn("Sandbox:", printed)

    def test_run_with_a_shell_task_still_requires_the_sandbox(self):
        if not gw._sandbox_works():
            self.skipTest("bwrap is not usable on this machine")
        printed, probe = self.run_main(["desk_forbidden", "fix_failing_test"])
        probe.assert_called_once_with()
        self.assertIn("Sandbox: bwrap", printed)

    def test_wall_clock_defaults_to_600_seconds(self):
        printed, _ = self.run_main(["desk_forbidden"])
        self.assertIn("600s wall clock", printed)
        summary = next(self.out_root.rglob("summary.md")).read_text(encoding="utf-8")
        self.assertIn("600s wall clock", summary)

    def test_wall_clock_flag_reaches_the_banner_the_summary_and_the_attempt(self):
        with mock.patch.object(agent, "run_attempt", wraps=agent.run_attempt) as attempt:
            printed, _ = self.run_main(["desk_forbidden"], "--wall-clock", "1800")
        self.assertIn("1800s wall clock", printed)
        self.assertNotIn("600s wall clock", printed)
        summary = next(self.out_root.rglob("summary.md")).read_text(encoding="utf-8")
        self.assertIn("1800s wall clock", summary)
        self.assertEqual(attempt.call_args.kwargs["wall_s"], 1800)

    def test_wall_clock_must_be_a_positive_whole_number(self):
        for bad in ("0", "-5", "ten"):
            with self.subTest(bad), mock.patch("sys.stderr"), self.assertRaises(SystemExit):
                with mock.patch.object(sys, "argv", ["run-agent.py", "--models", "lite",
                                                     "--wall-clock", bad]):
                    agent.main()


if __name__ == "__main__":
    unittest.main()
