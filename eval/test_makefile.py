#!/usr/bin/env python3
"""
`make build` prunes models/<name>/ when build-<name> is gone. No GGUF or server:
the real Makefile runs in a scratch tree with stub builders. Run with:

    python3 -m unittest eval/test_makefile.py -v

Why this exists: deleting a builder left models/unlocked/ behind (audit
2026-11-10). The Makefile already dropped the model from models.ini, but its
generated folder stayed and looked like a live model.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# A mutation run points this at a scratch copy, never the tracked Makefile (AGENTS.md).
MAKEFILE = Path(os.environ.get("MAKEFILE_SRC", REPO / "Makefile"))

STUB_BUILDER = """#!/usr/bin/env bash
set -euo pipefail
name="${0##*build-}"
mkdir -p "models/$name"
printf '[%s]\\nmodel = stub\\n' "$name" > "models/$name/preset.ini"
"""


class MakeBuildPrunes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copy(MAKEFILE, self.root / "Makefile")
        (self.root / "server.ini").write_text("version = 1\n")
        (self.root / "build-common.sh").write_text("")
        for d in ("prompts", "memory", "models"):
            (self.root / d).mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def builder(self, name: str) -> None:
        path = self.root / f"build-{name}"
        path.write_text(STUB_BUILDER)
        path.chmod(0o755)

    def make_build(self) -> subprocess.CompletedProcess:
        result = subprocess.run(["make", "-C", str(self.root), "build"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def models(self) -> list[str]:
        return sorted(p.name for p in (self.root / "models").iterdir())

    def test_no_orphan_removes_nothing(self):
        self.builder("a")
        self.builder("b")
        result = self.make_build()
        self.assertEqual(self.models(), ["a", "b", "models.ini"])
        self.assertNotIn("removing", result.stdout)

    def test_orphan_folder_is_removed_and_named(self):
        self.builder("a")
        old = self.root / "models" / "old"
        old.mkdir()
        (old / "preset.ini").write_text("[old]\n")
        result = self.make_build()
        self.assertEqual(self.models(), ["a", "models.ini"])
        self.assertIn("models/old", result.stdout)

    def test_models_ini_and_loose_files_are_kept(self):
        self.builder("a")
        (self.root / "models" / "notes.txt").write_text("keep")
        self.make_build()
        self.assertEqual((self.root / "models" / "notes.txt").read_text(), "keep")
        self.assertIn("[a]", (self.root / "models" / "models.ini").read_text())

    def test_no_model_folders_is_not_a_wildcard(self):
        # An unmatched models/*/ stays a literal string in sh. The prune must skip
        # it, not hand "*" to rm.
        (self.root / "models" / "notes.txt").write_text("keep")
        self.make_build()
        self.assertEqual(self.models(), ["models.ini", "notes.txt"])

    def test_empty_models_folder_builds(self):
        self.builder("a")
        shutil.rmtree(self.root / "models")
        self.make_build()
        self.assertEqual(self.models(), ["a", "models.ini"])


if __name__ == "__main__":
    unittest.main()
