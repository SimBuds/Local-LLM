#!/usr/bin/env python3
"""
Unit tests for `apply-universal.py`, which copies the universal half of this
repo's AGENTS.md into other repos' AGENTS.md files. Every file here lives in a
temporary directory: nothing reads or writes the real repos. Run with:

    python3 -m unittest eval/test_apply_universal.py -v
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
from unittest import mock

# APPLY_UNIVERSAL_SRC points the tests at a scratch copy, so a mutation run
# never rewrites the tracked script (AGENTS.md, project rules).
SCRIPT = Path(os.environ.get("APPLY_UNIVERSAL_SRC")
              or Path(__file__).resolve().parent.parent / "apply-universal.py")
_loader = importlib.machinery.SourceFileLoader("apply_universal", str(SCRIPT))
_spec = importlib.util.spec_from_loader("apply_universal", _loader)
au = importlib.util.module_from_spec(_spec)
_loader.exec_module(au)

PREAMBLE = ("\n## Project-specific rules\n\nRules in this section are tier 1.\n\n"
            "<!-- One bullet per rule. Never in the universal\n     body above. -->\n")


def agents(universal: str, rules: str) -> str:
    """An AGENTS.md shaped like the real ones: body, header, preamble, rules."""
    return f"# AGENTS.md\n\n{universal}\n{PREAMBLE}\n{rules}"


TEMPLATE = agents("Universal rule, version two.", "- Template-only rule.\n")


class Repos:
    """A scratch tree: a template file plus target files written on demand."""

    def __init__(self, tmp: str):
        self.root = Path(tmp)
        self.template = self.write("Local-LLM/AGENTS.md", TEMPLATE)

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path


def isolate(case: unittest.TestCase, root: Path) -> None:
    """Point the script's default search at the scratch tree for this test.

    Without this, any code path that falls back to discovery searches the real
    ~/Apps and ~/Documents/Projects. On 2026-10-20 a mutant that ignored named
    targets did exactly that, and the write-mode tests overwrote the universal
    half of every real AGENTS.md with this file's fixture."""
    patch = mock.patch.object(au, "ROOTS", (root,))
    patch.start()
    case.addCleanup(patch.stop)


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = au.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class SyncTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repos = Repos(self._tmp.name)
        isolate(self, self.repos.root)
        self.t = str(self.repos.template)

    def tearDown(self):
        self._tmp.cleanup()

    def test_default_search_is_the_scratch_tree(self):
        self.assertEqual(au.ROOTS, (self.repos.root,))

    def test_in_sync_target_reports_in_sync_and_is_not_rewritten(self):
        target = self.repos.write("A/AGENTS.md",
                                  agents("Universal rule, version two.", "- A rule.\n"))
        before = (target.read_bytes(), target.stat().st_mtime_ns)
        for args in (("--check", self.t, str(target)), (self.t, str(target))):
            code, out, _ = run(*args)
            self.assertEqual(code, 0, out)
            self.assertIn("in sync", out)
        self.assertEqual((target.read_bytes(), target.stat().st_mtime_ns), before)

    def test_check_prints_diff_for_one_character_drift_and_exits_1(self):
        # The 2026-10-20 drift: one trailing space, same line count, which the
        # old line-count report could not show.
        drifted = agents("Universal rule, version two. ", "- A rule.\n")
        target = self.repos.write("A/AGENTS.md", drifted)
        code, out, _ = run("--check", self.t, str(target))
        self.assertEqual(code, 1, out)
        self.assertIn("-Universal rule, version two. \n", out)
        self.assertIn("+Universal rule, version two.\n", out)
        self.assertEqual(target.read_text(), drifted)

    def test_write_replaces_universal_part_and_keeps_project_rules(self):
        rules = "- A rule.\n\n- Another, with a ``` fence.\n"
        target = self.repos.write("A/AGENTS.md", agents("Old universal rule.", rules))
        target.chmod(0o640)
        code, out, _ = run(self.t, str(target))
        self.assertEqual(code, 0, out)
        text = target.read_text()
        self.assertTrue(text.endswith(rules))
        self.assertIn("Universal rule, version two.", text)
        self.assertNotIn("Old universal rule.", text)
        self.assertNotIn("Template-only rule", text)
        self.assertEqual(target.stat().st_mode & 0o777, 0o640)
        self.assertEqual(run("--check", self.t, str(target))[0], 0)

    def test_target_without_header_blocks_every_write(self):
        good = self.repos.write("A/AGENTS.md", agents("Old universal rule.", "- A.\n"))
        bad = self.repos.write("B/AGENTS.md", "# AGENTS.md\n\nNo section here.\n")
        before = good.read_text()
        code, _, err = run(self.t, str(good), str(bad))
        self.assertEqual(code, 2)
        self.assertIn(str(bad), err)
        self.assertIn("nothing written", err)
        self.assertEqual(good.read_text(), before)

    def test_target_without_preamble_comment_is_reported(self):
        bad = self.repos.write("B/AGENTS.md",
                               "# AGENTS.md\n\n## Project-specific rules\n\n- A.\n")
        code, _, err = run("--check", self.t, str(bad))
        self.assertEqual(code, 2)
        self.assertIn("preamble comment not found", err)

    def test_template_without_header_aborts_before_targets(self):
        self.repos.template.write_text("# AGENTS.md\n\nNo section.\n")
        missing_target = self.repos.root / "nope" / "AGENTS.md"
        code, _, err = run(self.t, str(missing_target))
        self.assertEqual(code, 2)
        self.assertIn(self.t, err)
        self.assertNotIn(str(missing_target), err)

    def test_named_targets_skip_discovery(self):
        elsewhere = self.repos.write("Other/AGENTS.md", agents("Old.", "- O.\n"))
        named = self.repos.write("A/AGENTS.md", agents("Old.", "- A.\n"))
        code, out, _ = run(self.t, str(named))
        self.assertEqual(code, 0, out)
        self.assertIn("Universal rule, version two.", named.read_text())
        self.assertEqual(elsewhere.read_text(), agents("Old.", "- O.\n"))

    def test_no_targets_discovers_under_roots(self):
        drifted = self.repos.write("A/AGENTS.md", agents("Old.", "- A.\n"))
        code, out, _ = run("--check", self.t)
        self.assertEqual(code, 1, out)
        self.assertIn(str(drifted), out)
        self.assertNotIn(f"{self.t}: ", out)


class DiscoverTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repos = Repos(self._tmp.name)
        isolate(self, self.repos.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_finds_root_and_child_files_but_not_template_or_nested_copies(self):
        top = self.repos.write("AGENTS.md", TEMPLATE)
        child = self.repos.write("A/AGENTS.md", TEMPLATE)
        self.repos.write("A/vendor/plugin/AGENTS.md", "third-party")
        (self.repos.root / "Empty").mkdir()
        found, missing = au.discover((self.repos.root,), self.repos.template)
        self.assertEqual(found, [top, child])
        self.assertEqual(missing, [])

    def test_missing_root_is_reported_and_other_roots_still_searched(self):
        child = self.repos.write("A/AGENTS.md", TEMPLATE)
        gone = self.repos.root / "does-not-exist"
        found, missing = au.discover((gone, self.repos.root), self.repos.template)
        self.assertEqual(found, [child])
        self.assertEqual(missing, [gone])
        with mock.patch.object(au, "ROOTS", (gone, self.repos.root)):
            code, _, err = run("--check", str(self.repos.template))
        self.assertEqual(code, 0)
        self.assertIn(str(gone), err)


if __name__ == "__main__":
    unittest.main()
