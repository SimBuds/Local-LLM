#!/usr/bin/env python3
"""
Unit tests for `check-agents.py`, the read-only size and repetition report for
AGENTS.md. Every file checked here is written to a temporary directory, apart
from one read of the tracked AGENTS.md. Run with:

    python3 -m unittest eval/test_check_agents.py -v
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# CHECK_AGENTS_SRC points the tests at a scratch copy, so a mutation run never
# rewrites the tracked script (AGENTS.md, project rules).
SCRIPT = Path(os.environ.get("CHECK_AGENTS_SRC") or ROOT / "check-agents.py")
_loader = importlib.machinery.SourceFileLoader("check_agents", str(SCRIPT))
_spec = importlib.util.spec_from_loader("check_agents", _loader)
ca = importlib.util.module_from_spec(_spec)
_loader.exec_module(ca)

PROJECT = """
## Project-specific rules

Rules in this section are tier 1.

<!-- Add this repo's rules below. Replace everything above this header to update
     the universal body above. -->

- **Locked: the port is 8080.** Three other apps call it.
- **A project rule of nine words in total here.**
"""

FIXTURE = """# AGENTS.md

Intro of five words here.

## Alpha rules

- **Run the tests.** Report observed output, never a guess.
- **A lead-in that runs over
  two lines.** Its body.

```markdown
## Not a section

- **Not a rule.** This sits in a fenced template.
```

### Alpha details

Seen to fail first, always.

## Beta rules

- **Keep it small.** A test is seen to fail first here too.
- Plain bullet with no lead-in.
""" + PROJECT


def run(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = ca.main(list(args))
    return code, out.getvalue(), err.getvalue()


class CheckAgentsCase(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.path = Path(td.name) / "AGENTS.md"
        self.path.write_text(FIXTURE, encoding="utf-8")

    def universal_words(self) -> int:
        return len(FIXTURE.split("\n## Project-specific rules\n")[0].split())


class ReportTests(CheckAgentsCase):
    def test_counts_split_the_universal_body_from_the_project_section(self):
        report = ca.measure(FIXTURE, "fixture")
        self.assertEqual(report["universal_words"], self.universal_words())
        self.assertEqual(report["project_rules"], 2)
        self.assertGreater(report["project_words"], 0)

    def test_sections_are_top_level_headings_outside_fenced_blocks(self):
        names = [s["name"] for s in ca.measure(FIXTURE, "fixture")["sections"]]
        self.assertEqual(names, ["Alpha rules", "Beta rules"])

    def test_section_words_include_their_subsections_and_fenced_text(self):
        sections = {s["name"]: s for s in ca.measure(FIXTURE, "fixture")["sections"]}
        alpha = FIXTURE.split("## Alpha rules\n")[1].split("## Beta rules\n")[0]
        self.assertEqual(sections["Alpha rules"]["words"], len(alpha.split()))
        self.assertEqual(sections["Alpha rules"]["lines"], len(alpha.splitlines()))

    def test_report_prints_sizes_and_the_largest_section_first(self):
        code, out, _ = run(str(self.path))
        self.assertEqual(code, 0)
        self.assertIn(f"universal body {self.universal_words()} words in 2 sections", out)
        self.assertLess(out.index("Alpha rules"), out.index("Beta rules"))

    def test_rule_stated_in_two_sections_is_listed_under_both(self):
        concepts = {"fail first": r"seen to fail first", "tests": r"Run the tests"}
        found = ca.concept_map(FIXTURE.split("\n## Project-specific rules\n")[0], concepts)
        self.assertEqual(found["fail first"], {"Alpha details": 1, "Beta rules": 1})
        self.assertEqual(found["tests"], {"Alpha rules": 1})

    def test_concept_inside_a_fenced_block_is_not_counted(self):
        found = ca.concept_map(FIXTURE, {"fenced": r"fenced template"})
        self.assertEqual(found["fenced"], {})

    def test_default_report_lists_only_rules_found_in_more_than_one_section(self):
        code, out, _ = run(str(self.path))
        repeated = out.split("Rules mentioned in more than one section", 1)[1]
        self.assertIn("fail first", repeated)
        self.assertNotIn("handoff line", repeated)


class RulesTests(CheckAgentsCase):
    def test_rules_lists_every_bold_lead_in_sorted_one_per_line(self):
        code, out, _ = run(str(self.path), "--rules")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines(), [
            "A lead-in that runs over two lines.",
            "Keep it small.",
            "Run the tests.",
            "[project] A project rule of nine words in total here.",
            "[project] Locked: the port is 8080.",
        ])


class BudgetTests(CheckAgentsCase):
    def test_at_or_under_the_budget_exits_zero(self):
        words = self.universal_words()
        self.assertEqual(run(str(self.path), "--max-words", str(words))[0], 0)
        self.assertEqual(run(str(self.path), "--max-words", str(words + 500))[0], 0)

    def test_over_the_budget_exits_one_and_says_by_how_much(self):
        code, _, err = run(str(self.path), "--max-words", str(self.universal_words() - 7))
        self.assertEqual(code, 1)
        self.assertIn("over the budget", err)
        self.assertIn("by 7 words", err)

    def test_project_section_does_not_count_against_the_budget(self):
        bigger = FIXTURE + "- **Another project rule.** " + "word " * 500 + "\n"
        self.path.write_text(bigger, encoding="utf-8")
        self.assertEqual(run(str(self.path), "--max-words", str(self.universal_words()))[0], 0)


class FailureTests(CheckAgentsCase):
    def test_file_without_the_project_header_exits_two_with_a_message(self):
        self.path.write_text(FIXTURE.split("\n## Project-specific rules\n")[0], encoding="utf-8")
        code, out, err = run(str(self.path))
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no '## Project-specific rules' header", err)

    def test_missing_file_exits_two_with_a_message_and_no_traceback(self):
        code, _, err = run(str(self.path.parent / "nope.md"))
        self.assertEqual(code, 2)
        self.assertIn("nope.md", err)
        self.assertNotIn("Traceback", err)


class ReadOnlyTests(CheckAgentsCase):
    def test_no_mode_changes_the_file_or_its_folder(self):
        before = (self.path.read_bytes(), self.path.stat().st_mtime_ns,
                  sorted(os.listdir(self.path.parent)))
        for args in ((), ("--rules",), ("--max-words", "1")):
            run(str(self.path), *args)
        after = (self.path.read_bytes(), self.path.stat().st_mtime_ns,
                 sorted(os.listdir(self.path.parent)))
        self.assertEqual(before, after)

    def test_tracked_agents_md_is_well_formed(self):
        tracked = ROOT / "AGENTS.md"
        before = tracked.read_bytes()
        code, out, err = run(str(tracked))
        self.assertEqual((code, err), (0, ""))
        self.assertIn("universal body", out)
        self.assertEqual(tracked.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
