#!/usr/bin/env python3
"""
Unit tests for run-json.py's context handling. No live server: the gateway calls
are patched. Run with:

    python3 -m unittest eval/test_json.py -v
"""

from __future__ import annotations

import importlib.util
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

_PATH = Path(__file__).resolve().parent / "run-json.py"
_spec = importlib.util.spec_from_file_location("run_json", _PATH)
rj = importlib.util.module_from_spec(_spec)
sys.modules["run_json"] = rj  # @dataclass resolves its module through sys.modules
_spec.loader.exec_module(rj)

import json_tasks as jt  # noqa: E402  (run-json.py put eval/ on sys.path)


def serving(**ctx_by_model):
    """Patch run-json's served_ctx lookup with a fixed n_ctx per model."""
    return mock.patch.object(rj, "served_ctx", side_effect=lambda m: ctx_by_model[m])


class RequireCtxTests(unittest.TestCase):
    def test_served_equal_to_required_passes(self):
        with serving(lite=65536):
            self.assertEqual(rj.require_ctx(["lite"], 65536), {"lite": 65536})

    def test_served_above_required_passes(self):
        with serving(lite=65536):
            self.assertEqual(rj.require_ctx(["lite"], 32768), {"lite": 65536})

    def test_served_below_required_aborts_with_both_numbers_and_the_fix(self):
        with serving(lite=32768):
            with self.assertRaises(SystemExit) as cm:
                rj.require_ctx(["lite"], 65536)
        msg = str(cm.exception)
        self.assertIn("lite (32768)", msg)
        self.assertIn("65536", msg)
        self.assertIn("ctx-size", msg)

    def test_only_the_short_model_is_named(self):
        with serving(lite=65536, qwen=32768, gemma=65536):
            with self.assertRaises(SystemExit) as cm:
                rj.require_ctx(["gemma", "qwen", "lite"], 65536)
        msg = str(cm.exception)
        self.assertIn("qwen (32768)", msg)
        self.assertNotIn("lite (", msg)
        self.assertNotIn("gemma (", msg)

    def test_think_spec_is_looked_up_by_model_name(self):
        with serving(qwen=65536):
            self.assertEqual(rj.require_ctx(["qwen:think"], 65536), {"qwen:think": 65536})


class PressureTests(unittest.TestCase):
    """`max` fills most of the 131072 context served since 2026-10-30. The older
    levels stay fixed so their past results remain comparable."""

    def test_existing_levels_are_unchanged(self):
        self.assertEqual({k: rj.PRESSURE[k] for k in ("normal", "medium", "high")},
                         {"normal": 1.0, "medium": 4.0, "high": 12.0})

    def test_max_is_the_largest_level(self):
        self.assertGreater(rj.PRESSURE["max"], rj.PRESSURE["high"])

    def test_max_needs_the_full_served_context(self):
        self.assertEqual(rj.min_ctx("max"), 131072)

    def test_other_levels_keep_the_65536_minimum(self):
        for level in ("normal", "medium", "high"):
            self.assertEqual(rj.min_ctx(level), 65536)

    def test_max_runs_against_65536_abort_before_any_attempt(self):
        with serving(lite=65536):
            with self.assertRaises(SystemExit):
                rj.require_ctx(["lite"], rj.min_ctx("max"))

    def test_largest_max_document_stays_under_the_measured_word_bound(self):
        # MAX_WORDS is the largest document measured to fit (TESTING.md,
        # *JSON max pressure*). A higher multiplier or a longer task fails here
        # before a live run truncates against the context.
        tasks = rj.resolve_tasks(list(rj.TASKS.values()), "max", "default")
        longest = max(len(t.context.split()) for t in tasks)
        self.assertLessEqual(longest, rj.MAX_WORDS)


def passes(task, answer) -> list[bool]:
    return [bool(fn(answer)) for _, fn in task.checks]


class ChainTraceTests(unittest.TestCase):
    """The expected codes are worked out here by following the statements in
    the document, not read back from the generator."""

    ROOT = re.compile(r"The access code for the (.+) site is (\d+)\.")
    LINK = re.compile(r"The (.+) site uses the same access code as the (.+) site\.")

    def traced(self, task) -> dict:
        codes, same_as = {}, {}
        for needle in task.needles:
            if m := self.ROOT.search(needle):
                codes[m.group(1)] = int(m.group(2))
            else:
                m = self.LINK.search(needle)
                same_as[m.group(1)] = m.group(2)
        answer = {}
        for site in same_as:
            end = site
            while end in same_as:
                end = same_as[end]
            key = site.lower().replace(" ", "_")
            if key in task.schema["properties"]:
                answer[key] = codes[end]
        return answer

    def test_same_arguments_build_the_same_document(self):
        a, b = jt.chain_trace_task(4, 3), jt.chain_trace_task(4, 3)
        self.assertEqual(a.needles, b.needles)
        self.assertEqual(jt.build_context(a, 3000), jt.build_context(b, 3000))
        self.assertNotEqual(a.needles, jt.chain_trace_task(4, 2).needles)

    def test_traced_answer_passes_every_check_and_the_schema(self):
        for hops, chains in ((1, 1), (2, 3), (4, 3), (8, 4)):
            with self.subTest(hops=hops, chains=chains):
                task = jt.chain_trace_task(hops, chains)
                self.assertEqual(len(task.needles), chains * (hops + 1))
                answer = self.traced(task)
                self.assertEqual(len(answer), chains)
                self.assertEqual(sorted(answer), sorted(task.schema["required"]))
                self.assertEqual(len(set(answer.values())), chains, "chains share a code")
                self.assertEqual(passes(task, answer), [True] * chains)
                self.assertEqual(rj.schema_errors(answer, task.schema), [])

    def test_one_wrong_code_fails_exactly_its_own_check(self):
        task = jt.chain_trace_task(4, 3)
        answer = self.traced(task)
        keys = list(task.schema["properties"])
        other_chain_code = answer[keys[1]]
        result = passes(task, {**answer, keys[0]: other_chain_code})
        self.assertEqual(result.count(False), 1)
        self.assertEqual(passes(task, None), [False] * 3)

    def test_statements_are_not_in_chain_order(self):
        task = jt.chain_trace_task(4, 3)
        seen, backward = set(), 0
        for needle in task.needles:
            if m := self.LINK.search(needle):
                backward += m.group(2) not in seen
                seen.add(m.group(1))
            else:
                seen.add(self.ROOT.search(needle).group(1))
        self.assertGreater(backward, 0, "every link came after the site it points to")

    def test_sizes_below_one_or_beyond_the_name_pool_are_rejected(self):
        for hops, chains in ((0, 1), (1, 0), (-1, 2), (40, 4)):
            with self.subTest(hops=hops, chains=chains), self.assertRaises(ValueError):
                jt.chain_trace_task(hops, chains)


class LedgerSumTests(unittest.TestCase):
    RECORD = re.compile(r"The (\w+) office spent (\d+) CAD on (\w+) in \w+\.")
    ASKED = re.compile(r"where the (\w+) office spent money on (\w+),")

    def tally(self, task) -> tuple[dict, list[int]]:
        office, category = self.ASKED.search(task.instruction).groups()
        rows = [self.RECORD.search(n).groups() for n in task.needles]
        mine = [int(amount) for o, amount, c in rows if (o, c) == (office, category)]
        others = [int(amount) for o, amount, c in rows if (o, c) != (office, category)]
        return {"matching_records": len(mine), "total_cad": sum(mine)}, others

    def test_same_arguments_build_the_same_document(self):
        a, b = jt.ledger_sum_task(6, 10), jt.ledger_sum_task(6, 10)
        self.assertEqual(a.needles, b.needles)
        self.assertEqual(a.instruction, b.instruction)

    def test_tallied_answer_passes_every_check_and_the_schema(self):
        for records, distractors in ((1, 0), (1, 5), (6, 10), (12, 24)):
            with self.subTest(records=records, distractors=distractors):
                task = jt.ledger_sum_task(records, distractors)
                self.assertEqual(len(task.needles), records + distractors)
                answer, others = self.tally(task)
                self.assertEqual((answer["matching_records"], len(others)), (records, distractors))
                self.assertEqual(passes(task, answer), [True, True])
                self.assertEqual(rj.schema_errors(answer, task.schema), [])
                self.assertNotIn(answer["total_cad"], others)

    def test_miscounting_or_adding_a_distractor_fails(self):
        task = jt.ledger_sum_task(6, 10)
        answer, others = self.tally(task)
        self.assertEqual(passes(task, {**answer, "matching_records": 7}), [False, True])
        self.assertEqual(passes(task, {**answer, "total_cad": answer["total_cad"] + others[0]}),
                         [True, False])
        self.assertEqual(passes(task, []), [False, False])

    def test_distractors_include_near_misses_on_office_and_on_category(self):
        task = jt.ledger_sum_task(6, 10)
        office, category = self.ASKED.search(task.instruction).groups()
        rows = [self.RECORD.search(n).groups() for n in task.needles]
        self.assertTrue(any(o == office and c != category for o, _, c in rows))
        self.assertTrue(any(o != office and c == category for o, _, c in rows))

    def test_matching_records_are_spread_among_the_distractors(self):
        task = jt.ledger_sum_task(6, 10)
        office, category = self.ASKED.search(task.instruction).groups()
        flags = [self.RECORD.search(n).group(1, 3) == (office, category) for n in task.needles]
        self.assertNotEqual(flags, sorted(flags))
        self.assertNotEqual(flags, sorted(flags, reverse=True))

    def test_sizes_below_the_minimum_are_rejected(self):
        for records, distractors in ((0, 5), (-1, 5), (3, -1)):
            with self.subTest(records=records), self.assertRaises(ValueError):
                jt.ledger_sum_task(records, distractors)


class MultiHopRegistrationTests(unittest.TestCase):
    def test_generators_are_not_in_the_default_task_set_yet(self):
        self.assertEqual(list(jt.TASKS), ["jd_extract", "needle_recall", "decline_guard",
                                          "conflicting_correction", "enum_classify",
                                          "multi_extract", "no_infer"])

    def test_largest_sizes_fit_the_measured_word_bound_at_max_pressure(self):
        for task in (jt.chain_trace_task(8, 4), jt.ledger_sum_task(12, 24)):
            with self.subTest(task.key):
                built = rj.resolve_tasks([task], "max", "default")[0]
                self.assertLessEqual(len(built.context.split()), rj.MAX_WORDS)
                for needle in task.needles:
                    self.assertIn(needle, built.context)


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

    SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"},
                                               "b": {"type": "integer"}},
              "required": ["a", "b"]}

    def scored(self, reply: str) -> dict:
        """One attempt against a two-check task, with the model's reply given."""
        def explodes(parsed):
            raise KeyError("c")
        task = rj.ResolvedTask(key="t", instruction="Extract.", context="doc", schema=self.SCHEMA,
                               checks=[("a=1", lambda p: p["a"] == 1),
                                       ("b=2", lambda p: p["b"] == 2),
                                       ("c present", explodes if "boom" in reply else
                                        lambda p: True)])
        meta = {"eval_count": 4, "eval_duration": 1e8, "prompt_eval_count": 3000}
        with mock.patch.object(rj, "generate", return_value=(reply.replace("boom", ""), meta)), \
                mock.patch("builtins.print"):
            return rj.run_attempt("lite", task, 1, 1, timeout=30, thinking_mode="off")

    def test_every_check_passing_is_a_correct_answer(self):
        r = self.scored('{"a": 1, "b": 2}')
        self.assertEqual((r["facts_ok"], r["n_pass"], r["n_checks"]), (True, 3, 3))

    def test_schema_valid_answer_with_a_wrong_fact_is_not_correct(self):
        # 2026-11-19: n_pass counted every check, passed or not, so any
        # schema-valid JSON scored as fully correct. Present since 2026-06-09.
        r = self.scored('{"a": 1, "b": 99}')
        self.assertTrue(r["schema_ok"])
        self.assertEqual((r["facts_ok"], r["n_pass"], r["n_checks"]), (False, 2, 3))

    def test_every_fact_wrong_counts_only_the_checks_that_passed(self):
        r = self.scored('{"a": 7, "b": 99}')
        self.assertEqual((r["facts_ok"], r["n_pass"]), (False, 1))

    def test_check_that_raises_counts_as_failed(self):
        r = self.scored('{"a": 1, "b": 2}boom')
        self.assertEqual((r["facts_ok"], r["n_pass"], r["n_checks"]), (False, 2, 3))

    def test_reply_that_is_not_json_passes_no_check(self):
        r = self.scored("not json")
        self.assertEqual((r["valid_json"], r["facts_ok"], r["n_pass"], r["n_checks"]),
                         (False, False, 0, 3))


if __name__ == "__main__":
    unittest.main()
