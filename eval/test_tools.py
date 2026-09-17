#!/usr/bin/env python3
"""
Unit tests for the scorer in run-tools.py. No live server: every case feeds
`score_calls` the parsed tool calls the gateway would have returned. Run with:

    python3 -m unittest eval/test_tools.py -v
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

EVAL = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL))

_spec = importlib.util.spec_from_file_location("run_tools", EVAL / "run-tools.py")
tools = importlib.util.module_from_spec(_spec)
sys.modules["run_tools"] = _spec.loader and _spec.loader.exec_module(tools) or tools

from tool_tasks import TASKS, ToolTask, GET_WEATHER, SEND_EMAIL, WEB_SEARCH  # noqa: E402


def call(name: str, args: dict, ok: bool = True, raw: str | None = None) -> dict:
    """A parsed tool call in the shape _gateway.chat() returns."""
    return {"id": "x", "name": name, "arguments": args, "arguments_ok": ok,
            "arguments_raw": raw if raw is not None else str(args)}


WEATHER_TASK = ToolTask(
    key="t", user="weather in Toronto?", tools=[GET_WEATHER, WEB_SEARCH],
    expect=[{"name": "get_weather", "args": {"city": "Toronto"}}])
NO_CALL_TASK = ToolTask(
    key="n", user="what is 12*12?", tools=[GET_WEATHER], expect=[])


class ExpectedCallTests(unittest.TestCase):
    def test_right_tool_and_args_pass(self):
        ok, reasons = tools.score_calls(WEATHER_TASK, [call("get_weather", {"city": "Toronto"})], "")
        self.assertTrue(ok, reasons)
        self.assertEqual(reasons, [])

    def test_wrong_tool_fails_naming_both(self):
        ok, reasons = tools.score_calls(WEATHER_TASK, [call("web_search", {"query": "Toronto"})], "")
        self.assertFalse(ok)
        self.assertIn("wrong-tool", reasons[0])

    def test_missing_call_fails(self):
        ok, reasons = tools.score_calls(WEATHER_TASK, [], "Sure, one moment.")
        self.assertFalse(ok)
        self.assertIn("no-call", reasons[0])

    def test_wrong_argument_value_fails(self):
        ok, reasons = tools.score_calls(WEATHER_TASK, [call("get_weather", {"city": "Ottawa"})], "")
        self.assertFalse(ok)
        self.assertIn("wrong-arg", reasons[0])

    def test_missing_required_argument_fails(self):
        ok, reasons = tools.score_calls(WEATHER_TASK, [call("get_weather", {})], "")
        self.assertFalse(ok)
        self.assertIn("wrong-arg", reasons[0])

    def test_extra_call_fails_even_when_the_expected_one_is_present(self):
        ok, reasons = tools.score_calls(
            WEATHER_TASK,
            [call("get_weather", {"city": "Toronto"}), call("web_search", {"query": "x"})], "")
        self.assertFalse(ok)
        self.assertIn("extra-call", reasons[0])

    def test_malformed_arguments_fail_without_raising(self):
        ok, reasons = tools.score_calls(
            WEATHER_TASK, [call("get_weather", {}, ok=False, raw="{city: Toronto")], "")
        self.assertFalse(ok)
        self.assertIn("bad-json-args", reasons[0])

    def test_argument_outside_the_tool_schema_fails(self):
        ok, reasons = tools.score_calls(
            WEATHER_TASK, [call("get_weather", {"city": "Toronto", "when": "now"})], "")
        self.assertFalse(ok)
        self.assertIn("unknown-arg", reasons[0])

    def test_optional_argument_the_task_does_not_pin_is_allowed(self):
        ok, reasons = tools.score_calls(
            WEATHER_TASK, [call("get_weather", {"city": "Toronto", "unit": "c"})], "")
        self.assertTrue(ok, reasons)


class NormalizationTests(unittest.TestCase):
    def test_string_case_and_padding_are_ignored(self):
        ok, _ = tools.score_calls(WEATHER_TASK, [call("get_weather", {"city": " toronto "})], "")
        self.assertTrue(ok)

    def test_number_compares_by_value_across_int_and_float(self):
        task = ToolTask(key="t", user="", tools=[GET_WEATHER],
                        expect=[{"name": "get_weather", "args": {"city": "X", "unit": 2}}])
        ok, _ = tools.score_calls(task, [call("get_weather", {"city": "X", "unit": 2.0})], "")
        self.assertTrue(ok)

    def test_numeric_string_does_not_match_a_number(self):
        task = ToolTask(key="t", user="", tools=[GET_WEATHER],
                        expect=[{"name": "get_weather", "args": {"city": "X", "unit": 2}}])
        ok, _ = tools.score_calls(task, [call("get_weather", {"city": "X", "unit": "two"})], "")
        self.assertFalse(ok)

    def test_contains_matcher_accepts_a_superset_query(self):
        task = ToolTask(key="s", user="", tools=[WEB_SEARCH],
                        expect=[{"name": "web_search",
                                 "args": {"query": {"contains": "llama.cpp"}}}])
        ok, _ = tools.score_calls(
            task, [call("web_search", {"query": "latest LLAMA.CPP release notes"})], "")
        self.assertTrue(ok)

    def test_contains_matcher_rejects_a_query_missing_the_subject(self):
        task = ToolTask(key="s", user="", tools=[WEB_SEARCH],
                        expect=[{"name": "web_search",
                                 "args": {"query": {"contains": "llama.cpp"}}}])
        ok, reasons = tools.score_calls(task, [call("web_search", {"query": "release notes"})], "")
        self.assertFalse(ok)
        self.assertIn("wrong-arg", reasons[0])

    def test_any_of_matcher_accepts_either_value(self):
        task = ToolTask(key="a", user="", tools=[GET_WEATHER],
                        expect=[{"name": "get_weather",
                                 "args": {"city": {"any_of": ["Toronto", "toronto, ca"]}}}])
        ok, _ = tools.score_calls(task, [call("get_weather", {"city": "Toronto, CA"})], "")
        self.assertTrue(ok)

    def test_nested_object_argument_matches_per_key(self):
        from tool_tasks import CREATE_EVENT
        task = ToolTask(key="e", user="", tools=[CREATE_EVENT],
                        expect=[{"name": "create_event",
                                 "args": {"title": "Dentist",
                                          "start": {"date": "2026-10-02", "time": "14:30"}}}])
        ok, _ = tools.score_calls(
            task, [call("create_event", {"title": "dentist",
                                         "start": {"date": "2026-10-02", "time": "14:30"},
                                         "duration_minutes": 45})], "")
        self.assertTrue(ok)

    def test_nested_object_with_a_wrong_field_fails(self):
        from tool_tasks import CREATE_EVENT
        task = ToolTask(key="e", user="", tools=[CREATE_EVENT],
                        expect=[{"name": "create_event",
                                 "args": {"start": {"date": "2026-10-02", "time": "14:30"}}}])
        ok, reasons = tools.score_calls(
            task, [call("create_event", {"start": {"date": "2026-10-02", "time": "09:00"}})], "")
        self.assertFalse(ok)
        self.assertIn("wrong-arg", reasons[0])


class NoCallTests(unittest.TestCase):
    def test_answering_in_prose_passes(self):
        ok, reasons = tools.score_calls(NO_CALL_TASK, [], "144")
        self.assertTrue(ok, reasons)

    def test_any_call_fails(self):
        ok, reasons = tools.score_calls(NO_CALL_TASK, [call("get_weather", {"city": "X"})], "")
        self.assertFalse(ok)
        self.assertIn("unwanted-call", reasons[0])

    def test_empty_answer_with_no_call_fails(self):
        ok, reasons = tools.score_calls(NO_CALL_TASK, [], "   ")
        self.assertFalse(ok)
        self.assertIn("empty-answer", reasons[0])

    def test_parallel_calls_match_in_any_order(self):
        task = TASKS["parallel_calls"]
        calls = [call("get_weather", {"city": "Oslo", "unit": "c"}),
                 call("get_weather", {"city": "Toronto", "unit": "c"})]
        ok, reasons = tools.score_calls(task, calls, "")
        self.assertTrue(ok, reasons)

    def test_one_of_two_parallel_calls_missing_fails(self):
        task = TASKS["parallel_calls"]
        ok, reasons = tools.score_calls(
            task, [call("get_weather", {"city": "Toronto", "unit": "c"})], "")
        self.assertFalse(ok)
        self.assertIn("no-call", reasons[0])


class AnswerCheckTests(unittest.TestCase):
    """Grounding checks: what the model says after it gets a tool result."""

    TASK = ToolTask(
        key="g", user="", tools=[WEB_SEARCH],
        expect=[{"name": "web_search", "args": {"query": {"contains": "vexil"}}}],
        results={"web_search": {"results": []}},
        answer_checks=[("states version", "has", "7.3.1"),
                       ("no stale claim", "lacks", "7.2.0"),
                       ("admits nothing found", "any", ["no results", "nothing"])])

    def test_all_checks_satisfied_passes(self):
        ok, reasons = tools.score_answer(self.TASK, "Version 7.3.1; nothing else was found.")
        self.assertTrue(ok, reasons)

    def test_missing_required_substring_fails(self):
        ok, reasons = tools.score_answer(self.TASK, "Nothing was found.")
        self.assertFalse(ok)
        self.assertIn("answer-missing", reasons[0])
        self.assertIn("states version", reasons[0])

    def test_forbidden_substring_fails(self):
        ok, reasons = tools.score_answer(
            self.TASK, "Version 7.3.1, up from 7.2.0. No results beyond that.")
        self.assertFalse(ok)
        self.assertTrue(any("answer-forbidden" in r for r in reasons))

    def test_any_check_needs_one_alternative(self):
        ok, reasons = tools.score_answer(self.TASK, "Version 7.3.1 is current.")
        self.assertFalse(ok)
        self.assertTrue(any("answer-missing-any" in r for r in reasons))

    def test_checks_are_case_and_whitespace_insensitive(self):
        ok, _ = tools.score_answer(self.TASK, "VERSION   7.3.1 — NO   RESULTS otherwise.")
        self.assertTrue(ok)

    def test_task_without_checks_passes_on_any_text(self):
        ok, reasons = tools.score_answer(TASKS["pick_tool"], "")
        self.assertTrue(ok, reasons)


class SecondRoundTests(unittest.TestCase):
    """expect_after scores the calls made once the tool result is in hand."""

    TASK = TASKS["chain_lookup"]

    def test_second_round_call_using_the_returned_value_passes(self):
        ok, reasons = tools.score_calls(
            self.TASK,
            [call("send_email", {"to": "dana.whitfield@example.com",
                                 "subject": "Q3 summary",
                                 "body": "Revenue was up 12 percent."})],
            "", expect=self.TASK.expect_after)
        self.assertTrue(ok, reasons)

    def test_invented_address_fails(self):
        ok, reasons = tools.score_calls(
            self.TASK,
            [call("send_email", {"to": "dana@example.com", "subject": "Q3",
                                 "body": "up 12 percent"})],
            "", expect=self.TASK.expect_after)
        self.assertFalse(ok)
        self.assertIn("wrong-arg", reasons[0])

    def test_stopping_after_the_lookup_fails(self):
        ok, reasons = tools.score_calls(self.TASK, [], "I found Dana's address.",
                                        expect=self.TASK.expect_after)
        self.assertFalse(ok)
        self.assertIn("no-call", reasons[0])

    def test_tool_result_message_carries_the_call_id_and_canned_json(self):
        made = call("web_search", {"query": "vexil"})
        made["id"] = "call-1"
        msgs = tools.result_messages(TASKS["grounded_answer"], [made])
        self.assertEqual(msgs[0]["role"], "tool")
        self.assertEqual(msgs[0]["tool_call_id"], "call-1")
        self.assertIn("7.3.1", msgs[0]["content"])

    def test_unexpected_tool_gets_an_error_result_not_a_crash(self):
        made = call("send_email", {"to": "x@example.com"})
        made["id"] = "call-9"
        msgs = tools.result_messages(TASKS["grounded_answer"], [made])
        self.assertEqual(msgs[0]["tool_call_id"], "call-9")
        self.assertIn("error", msgs[0]["content"].lower())


class TaskTableTests(unittest.TestCase):
    def test_every_task_offers_the_tools_its_expected_calls_name(self):
        for key, task in TASKS.items():
            offered = {t["function"]["name"] for t in task.tools}
            for exp in task.expect:
                self.assertIn(exp["name"], offered, f"{key} expects an unoffered tool")

    def test_every_expected_argument_exists_in_that_tools_schema(self):
        for key, task in TASKS.items():
            schemas = {t["function"]["name"]: t["function"]["parameters"] for t in task.tools}
            for exp in task.expect:
                props = schemas[exp["name"]].get("properties", {})
                for arg in exp["args"]:
                    self.assertIn(arg, props, f"{key}: {exp['name']}.{arg} not in schema")


    def test_multi_turn_tasks_declare_results_for_a_tool_they_offer(self):
        for key, task in TASKS.items():
            if not task.results:
                continue
            offered = {t["function"]["name"] for t in task.tools}
            for name in task.results:
                self.assertIn(name, offered, f"{key}: canned result for unoffered {name}")

    def test_answer_checks_only_appear_on_multi_turn_tasks(self):
        for key, task in TASKS.items():
            if task.answer_checks:
                self.assertTrue(task.results, f"{key}: answer checks but no tool result")

    def test_send_email_task_expects_no_call(self):
        self.assertEqual(TASKS["missing_required_arg"].expect, [])
        self.assertIn(SEND_EMAIL, TASKS["missing_required_arg"].tools)


if __name__ == "__main__":
    unittest.main()
