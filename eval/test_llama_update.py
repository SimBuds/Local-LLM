#!/usr/bin/env python3
"""
Tests for scripts/llama-update, the weekly llama.cpp update flow. Nothing real is
fetched, built, or restarted: each test builds a scratch upstream repo and a clone
of it, and puts stub `cmake` and `systemctl` first on PATH. The stub cmake records
its arguments and "builds" a llama-server that prints a version line. Run with:

    python3 -m unittest eval/test_llama_update.py -v

Why the design is build-beside-then-swap: the router starts every model process
from build/bin/llama-server, so an in-place rebuild would run new-version model
processes under the old router before any restart (confirmed in its log,
2026-09-25).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# LLAMA_UPDATE_SRC points the tests at a scratch copy, so a mutation check never
# rewrites the tracked script (AGENTS.md, project rules).
SCRIPT = Path(os.environ.get("LLAMA_UPDATE_SRC", REPO / "scripts" / "llama-update"))

STUB_CMAKE = r"""#!/usr/bin/env bash
# Records every call, then either configures (-S/-B) or "builds" (--build DIR).
echo "$*" >> "$STUB_LOG"
if [ "${STUB_FAIL:-0}" = 1 ] && [ "$1" = "--build" ]; then echo "stub: build failed" >&2; exit 1; fi
if [ "$1" = "--build" ]; then
  mkdir -p "$2/bin"
  n=$(git -C "$LLAMA_SRC" rev-list --count HEAD); c=$(git -C "$LLAMA_SRC" rev-parse --short=9 HEAD)
  printf '#!/usr/bin/env bash\necho "version: 0.4.1-dev (build %s, commit %s)"\n' "$n" "$c" > "$2/bin/llama-server"
  chmod +x "$2/bin/llama-server"
  exit 0
fi
while [ $# -gt 0 ]; do [ "$1" = "-B" ] && mkdir -p "$2" && touch "$2/CMakeCache.txt"; shift; done
"""

STUB_SYSTEMCTL = """#!/usr/bin/env bash
echo "$*" >> "$SYSTEMCTL_LOG"
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class UpdateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.up = root / "upstream"          # plays github.com/ggml-org/llama.cpp
        self.src = root / "llama.cpp"        # plays ~/src/llama.cpp
        self.bin = root / "bin"
        self.bin.mkdir()
        self.log = root / "cmake.log"
        self.syslog = root / "systemctl.log"
        for name, body in (("cmake", STUB_CMAKE), ("systemctl", STUB_SYSTEMCTL)):
            p = self.bin / name
            p.write_text(body, encoding="utf-8")
            p.chmod(0o755)

        self.up.mkdir()
        git(self.up, "init", "-q", "-b", "master")
        git(self.up, "config", "user.email", "t@example.com")
        git(self.up, "config", "user.name", "t")
        (self.up / "CMakeLists.txt").write_text("# stub\n", encoding="utf-8")
        (self.up / ".gitignore").write_text("/build*\n", encoding="utf-8")
        git(self.up, "add", ".")
        git(self.up, "commit", "-q", "-m", "initial")
        subprocess.run(["git", "clone", "-q", str(self.up), str(self.src)], check=True)
        # An installed build, as the README recipe leaves it.
        self.run_script("build", allow_fail=True)  # up to date: builds nothing
        self._install_current()

    def _install_current(self):
        env = self.env()
        subprocess.run([str(self.bin / "cmake"), "--build", str(self.src / "build")],
                       env=env, check=True)
        self.log.unlink(missing_ok=True)

    def env(self, **extra) -> dict:
        e = dict(os.environ)
        e.update(PATH=f"{self.bin}:{e['PATH']}", LLAMA_SRC=str(self.src),
                 STUB_LOG=str(self.log), SYSTEMCTL_LOG=str(self.syslog))
        e.update(extra)
        return e

    def upstream_commit(self, subject: str, fname: str = "f.txt"):
        with open(self.up / fname, "a", encoding="utf-8") as f:
            f.write(subject + "\n")
        git(self.up, "add", ".")
        git(self.up, "commit", "-q", "-m", subject)

    def run_script(self, *args, allow_fail=False, **env):
        r = subprocess.run([str(SCRIPT), *args], env=self.env(**env),
                           capture_output=True, text=True)
        if not allow_fail and r.returncode != 0:
            self.fail(f"llama-update {' '.join(args)} exited {r.returncode}:\n{r.stdout}\n{r.stderr}")
        return r

    def version(self, folder: str) -> str:
        return subprocess.run([str(self.src / folder / "bin" / "llama-server"), "--version"],
                              capture_output=True, text=True).stdout.strip()


class StatusTests(UpdateCase):
    def test_up_to_date(self):
        out = self.run_script("status").stdout
        self.assertIn("build 1", out)
        self.assertIn("up to date", out.lower())

    def test_behind_counts_commits_and_lists_cuda_and_server_changes(self):
        self.upstream_commit("docs: typo")
        self.upstream_commit("CUDA: fix illegal memory access in mmq")
        self.upstream_commit("server: router reloads models on crash")
        out = self.run_script("status").stdout
        self.assertIn("3 new commits", out)
        self.assertIn("CUDA: fix illegal memory access", out)
        self.assertIn("server: router reloads", out)
        self.assertNotIn("docs: typo", out.split("CUDA and server")[-1])


class BuildTests(UpdateCase):
    def test_up_to_date_builds_nothing(self):
        r = self.run_script("build")
        self.assertIn("up to date", r.stdout.lower())
        self.assertFalse((self.src / "build-next").exists())
        self.assertFalse(self.log.exists(), "cmake ran with nothing to build")

    def test_build_goes_beside_the_live_build(self):
        self.upstream_commit("CUDA: something")
        before = self.version("build")
        r = self.run_script("build")
        self.assertEqual(self.version("build"), before, "the live build folder changed")
        self.assertIn("build 2", self.version("build-next"))
        self.assertIn("build 2", r.stdout)
        self.assertIn("known-good-b1", git(self.src, "branch", "--list", "known-good-*"))
        self.assertIn("llama-update swap", r.stdout)
        self.assertFalse(self.syslog.exists(), "build must never touch the service")

    def test_configure_always_uses_fresh(self):
        # 2026-09-17: a stale CMakeCache pinned a g++-15 that no longer existed.
        self.upstream_commit("x")
        self.run_script("build")
        configure = [ln for ln in self.log.read_text().splitlines() if "-S" in ln]
        self.assertTrue(configure and all("--fresh" in ln for ln in configure), configure)
        self.assertTrue(all("build-next" in ln for ln in configure), configure)

    def test_dirty_tree_is_refused(self):
        self.upstream_commit("x")
        (self.src / "CMakeLists.txt").write_text("# local edit\n", encoding="utf-8")
        r = self.run_script("build", allow_fail=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("uncommitted", (r.stdout + r.stderr).lower())
        self.assertFalse((self.src / "build-next").exists())

    def test_diverged_history_is_refused(self):
        self.upstream_commit("upstream change")
        git(self.src, "config", "user.email", "t@example.com")
        git(self.src, "config", "user.name", "t")
        (self.src / "local.txt").write_text("x\n", encoding="utf-8")
        git(self.src, "add", ".")
        git(self.src, "commit", "-q", "-m", "local commit")
        r = self.run_script("build", allow_fail=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("fast-forward", (r.stdout + r.stderr).lower())
        self.assertFalse((self.src / "build-next").exists())

    def test_failed_build_leaves_nothing_swappable(self):
        self.upstream_commit("x")
        r = self.run_script("build", allow_fail=True, STUB_FAIL="1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("build 1", self.version("build"))
        swap = self.run_script("swap", allow_fail=True)
        self.assertNotEqual(swap.returncode, 0, "a failed build was swappable")


class SwapAndRollbackTests(UpdateCase):
    def test_swap_without_a_build_next_is_refused(self):
        r = self.run_script("swap", allow_fail=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("llama-update build", r.stdout + r.stderr)

    def test_swap_installs_the_new_build_and_prints_the_restart(self):
        self.upstream_commit("x")
        self.run_script("build")
        r = self.run_script("swap")
        self.assertIn("build 2", self.version("build"))
        self.assertIn("build 1", self.version("build-prev"))
        self.assertFalse((self.src / "build-next").exists())
        self.assertIn("systemctl --user restart llama-server", r.stdout)
        self.assertFalse(self.syslog.exists(), "swap must print the restart, not run it")

    def test_rollback_without_a_build_prev_is_refused(self):
        r = self.run_script("rollback", allow_fail=True)
        self.assertNotEqual(r.returncode, 0)

    def test_rollback_restores_the_previous_build(self):
        self.upstream_commit("x")
        self.run_script("build")
        self.run_script("swap")
        r = self.run_script("rollback")
        self.assertIn("build 1", self.version("build"))
        self.assertIn("build 2", self.version("build-prev"))
        self.assertIn("systemctl --user restart llama-server", r.stdout)
        self.assertFalse(self.syslog.exists())

    def test_help_and_bad_commands_work_without_a_checkout(self):
        for args, want_rc in ((["--help"], 0), (["frob"], 64)):
            r = subprocess.run([str(SCRIPT), *args], capture_output=True, text=True,
                               env=self.env(LLAMA_SRC="/nonexistent/llama.cpp"))
            self.assertEqual(r.returncode, want_rc, args)
            self.assertIn("rollback", r.stdout + r.stderr)
            self.assertNotIn("not a llama.cpp checkout", r.stdout + r.stderr)

    def test_unknown_command_names_the_valid_ones(self):
        r = self.run_script("upgrade", allow_fail=True)
        self.assertNotEqual(r.returncode, 0)
        for cmd in ("status", "build", "swap", "rollback"):
            self.assertIn(cmd, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
