#!/usr/bin/env python3
"""
`./add-model` writes a build-* script for a GGUF that is already staged. These
tests drive the real script with subprocess against a temporary GGUF_DIR of
synthetic headers, so nothing here reads a real model or starts a server.

The MTP check is the reason this is tested rather than eyeballed: a wrong
spec-type makes the Phase 24 fit probe size the card for the wrong model, and
the error only shows up as a CUDA out-of-memory much later. Real discriminator,
measured 2026-09-15 across the four staged GGUFs: an MTP build carries a
`<arch>.nextn_predict_layers` metadata key and `blk.N.nextn.*` tensors, and a
non-MTP build carries neither. Grepping the file for the string "mtp" does not
work: gemma4-26b (no MTP) has 4 such byte sequences and granite4.2 has 9.

Run with:

    python3 -m unittest eval/test_add_model.py -v
"""

from __future__ import annotations

import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ADD_MODEL = REPO / "add-model"

# GGUF value type ids, from the format spec.
T_UINT32 = 4
T_STRING = 8


def _u64(n: int) -> bytes:
    return struct.pack("<Q", n)


def _gguf_string(s: str) -> bytes:
    raw = s.encode("utf-8")
    return _u64(len(raw)) + raw


def write_gguf(path: Path, arch: str = "testarch", mtp: bool = False) -> None:
    """Write a header-only GGUF: valid magic, version, and metadata, no tensors.

    add-model only ever reads the metadata block, so a file with zero tensors
    exercises the same code path as a 21 GB model.
    """
    kv: list[bytes] = []

    def add_string(key: str, value: str) -> None:
        kv.append(_gguf_string(key) + struct.pack("<I", T_STRING) + _gguf_string(value))

    def add_u32(key: str, value: int) -> None:
        kv.append(_gguf_string(key) + struct.pack("<I", T_UINT32) + struct.pack("<I", value))

    add_string("general.architecture", arch)
    add_string("general.name", path.stem)
    if mtp:
        add_u32(f"{arch}.nextn_predict_layers", 1)

    body = b"GGUF" + struct.pack("<I", 3) + _u64(0) + _u64(len(kv)) + b"".join(kv)
    path.write_bytes(body)


class AddModelTestCase(unittest.TestCase):
    """Base: a temp GGUF_DIR and a temp repo root holding copies of the builders."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.gguf_dir = root / "gguf"
        self.gguf_dir.mkdir()
        self.repo = root / "repo"
        self.repo.mkdir()

        # A working tree that looks like the real one: the script under test,
        # the shared assembly it references, and one builder that claims a GGUF.
        for name in ("add-model", "build-common.sh"):
            src = REPO / name
            if src.exists():
                dst = self.repo / name
                dst.write_bytes(src.read_bytes())
                dst.chmod(0o755)

        write_gguf(self.gguf_dir / "claimed.gguf")
        write_gguf(self.gguf_dir / "free-plain.gguf")
        write_gguf(self.gguf_dir / "free-mtp.gguf", arch="qwen35", mtp=True)
        write_gguf(self.gguf_dir / "vision.mmproj.gguf")

        (self.repo / "build-taken").write_text(
            '#!/usr/bin/env bash\nset -euo pipefail\nMODEL_NAME="taken"\n'
            'BASE_MODEL="claimed.gguf"\nLOAD=()\n'
            'source "$(dirname "${BASH_SOURCE[0]}")/build-common.sh"\n'
        )
        (self.repo / "build-taken").chmod(0o755)
        self.addCleanup(self.tmp.cleanup)

    def run_add_model(self, stdin: str = "", args: list[str] | None = None,
                      probe: bool = False, env_extra: dict[str, str] | None = None):
        """Run the script. Probing is off unless a test asks for it.

        The fixtures are header-only GGUFs with no tensors, so a real probe would
        start a real llama-server against a file with no weights. Tests that want
        the probe path put a fake llama-server on PATH and pass probe=True.
        """
        args = list(args or [])
        if not probe and "--no-probe" not in args:
            args = ["--no-probe", *args]
        env = dict(os.environ, GGUF_DIR=str(self.gguf_dir), **(env_extra or {}))
        return subprocess.run(
            [str(self.repo / "add-model"), *args],
            input=stdin, capture_output=True, text=True, env=env, cwd=self.repo, timeout=60,
        )

    def builder(self, name: str) -> str:
        return (self.repo / f"build-{name}").read_text()


class MenuTests(AddModelTestCase):
    def test_menu_offers_unclaimed_ggufs_only(self):
        r = self.run_add_model(stdin="1\nnewmodel\n")
        self.assertIn("free-plain.gguf", r.stdout)
        self.assertIn("free-mtp.gguf", r.stdout)
        self.assertNotIn("claimed.gguf", r.stdout)

    def test_menu_hides_mmproj_projectors(self):
        r = self.run_add_model(stdin="1\nnewmodel\n")
        self.assertNotIn("mmproj", r.stdout)

    def test_filename_argument_skips_the_menu(self):
        r = self.run_add_model(stdin="newmodel\n", args=["free-plain.gguf"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("1)", r.stdout)
        self.assertIn('BASE_MODEL="free-plain.gguf"', self.builder("newmodel"))

    def test_unknown_filename_argument_exits_naming_the_path(self):
        r = self.run_add_model(args=["nope.gguf"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nope.gguf", r.stderr)

    def test_empty_gguf_dir_exits_naming_the_directory(self):
        for f in self.gguf_dir.glob("*.gguf"):
            f.unlink()
        r = self.run_add_model()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(str(self.gguf_dir), r.stderr)

    def test_missing_gguf_dir_exits_naming_the_directory(self):
        """A missing GGUF_DIR is reported as missing, not as an empty one.

        Asserting only "exits non-zero naming the path" does not discriminate:
        without the -d check, find fails on the missing directory, the candidate
        list comes back empty, and the 'no unclaimed GGUF in <dir>' message also
        exits non-zero and also names the path. The message has to say the
        directory is absent, or the operator goes looking for the wrong problem.
        """
        missing = str(self.gguf_dir) + "-gone"
        env = dict(os.environ, GGUF_DIR=missing)
        r = subprocess.run([str(self.repo / "add-model")], input="", capture_output=True,
                           text=True, env=env, cwd=self.repo, timeout=30)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(missing, r.stderr)
        self.assertIn("does not exist", r.stderr)


class NameValidationTests(AddModelTestCase):
    def test_invalid_name_is_rejected_then_reprompts(self):
        r = self.run_add_model(stdin="Bad-Name\n9lead\ngoodname\n", args=["free-plain.gguf"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.repo / "build-goodname").exists())
        self.assertGreaterEqual(r.stdout.count("Router name"), 3)

    def test_name_colliding_with_an_existing_builder_is_rejected(self):
        r = self.run_add_model(stdin="taken\nfresh\n", args=["free-plain.gguf"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("build-taken", r.stdout + r.stderr)
        self.assertIn('MODEL_NAME="taken"', self.builder("taken"))  # untouched
        self.assertTrue((self.repo / "build-fresh").exists())

    def test_lineup_names_are_rejected_naming_the_contract(self):
        r = self.run_add_model(stdin="gemma\nqwen\nlite\nfresh\n", args=["free-plain.gguf"])
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("contract", out.lower())
        self.assertTrue((self.repo / "build-fresh").exists())

    def test_existing_builder_file_is_never_overwritten(self):
        before = self.builder("taken")
        self.run_add_model(stdin="taken\nfresh\n", args=["free-plain.gguf"])
        self.assertEqual(before, self.builder("taken"))


class MtpDetectionTests(AddModelTestCase):
    def test_mtp_gguf_emits_spec_type(self):
        r = self.run_add_model(stdin="speedy\n", args=["free-mtp.gguf"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("spec-type = draft-mtp", self.builder("speedy"))

    def test_plain_gguf_emits_no_spec_type(self):
        r = self.run_add_model(stdin="plain\n", args=["free-plain.gguf"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("spec-type", self.builder("plain"))

    def test_mtp_detection_does_not_match_the_filename(self):
        """A file named *mtp* without the metadata key must not be treated as MTP."""
        write_gguf(self.gguf_dir / "liar-mtp.gguf", mtp=False)
        r = self.run_add_model(stdin="liar\n", args=["liar-mtp.gguf"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("spec-type", self.builder("liar"))


class GeneratedBuilderTests(AddModelTestCase):
    def test_generated_builder_is_valid_bash(self):
        self.run_add_model(stdin="checked\n", args=["free-plain.gguf"])
        r = subprocess.run(["bash", "-n", str(self.repo / "build-checked")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_generated_builder_is_executable(self):
        self.run_add_model(stdin="checked\n", args=["free-plain.gguf"])
        self.assertTrue(os.access(self.repo / "build-checked", os.X_OK))

    def test_generated_builder_declares_no_params(self):
        """PARAMS is the shared baseline in build-common.sh since Phase 21."""
        self.run_add_model(stdin="checked\n", args=["free-plain.gguf"])
        self.assertNotIn("PARAMS=(", self.builder("checked"))

    def test_generated_builder_sources_build_common(self):
        self.run_add_model(stdin="checked\n", args=["free-plain.gguf"])
        self.assertIn("build-common.sh", self.builder("checked"))

    def test_load_has_no_n_cpu_moe_and_says_so(self):
        """Phase 23 does not probe fit, so the builder must not invent a split.

        Asserted against active lines only. The builder deliberately mentions
        n-cpu-moe in a comment telling the reader to derive one, and a bare
        substring check matches that comment and passes on a file that really
        does pin a split.
        """
        r = self.run_add_model(stdin="checked\n", args=["free-plain.gguf"])
        active = [ln for ln in self.builder("checked").splitlines()
                  if ln.strip() and not ln.lstrip().startswith("#")]
        self.assertFalse([ln for ln in active if "n-cpu-moe" in ln],
                         f"builder pins a split it never measured: {active}")
        self.assertIn("n-cpu-moe", "\n".join(
            ln for ln in self.builder("checked").splitlines() if ln.lstrip().startswith("#")))
        self.assertIn("n-cpu-moe", r.stdout)  # told the user it still has to be derived


if __name__ == "__main__":
    unittest.main()


FAKE_SERVER = r"""#!/usr/bin/env bash
# Stands in for llama-server during probe tests. Records its argv, emits a canned
# log, and then behaves like the real thing: stays alive until it is killed.
printf '%s\n' "$@" > "$FAKE_ARGS"
case "$FAKE_MODE" in
  spill)
    echo "I llama_model_loader: loading model"
    echo "I common_params_fit_impl:   - CUDA0 (NVIDIA GeForce RTX 3080): 31 layers (21 overflowing),   6461 MiB used,   2082 MiB free"
    echo "I srv  llama_server: model loaded"
    ;;
  fits)
    echo "I load_tensors: offloaded 34/34 layers to GPU"
    echo "I srv  llama_server: model loaded"
    ;;
  crash)
    echo "I llama_model_loader: loading model"
    echo "E error: failed to allocate CUDA0 buffer"
    exit 1
    ;;
  silent)
    ;;  # prints nothing, never becomes ready: the timeout case
esac
while true; do sleep 1; done
"""


class ProbeFitTests(AddModelTestCase):
    """The fit probe, driven against a fake llama-server.

    The real one is exercised once by hand (see the phase report); these pin the
    parsing, the flags, and the failure modes, none of which need a GPU.
    """

    def setUp(self) -> None:
        super().setUp()
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        fake = self.bin / "llama-server"
        fake.write_text(FAKE_SERVER)
        fake.chmod(0o755)
        self.args_file = Path(self.tmp.name) / "argv.txt"

    def probe_run(self, mode: str, stdin: str, gguf: str, extra: dict | None = None):
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "FAKE_MODE": mode,
            "FAKE_ARGS": str(self.args_file),
            "FIT_TIMEOUT": "10",
            **(extra or {}),
        }
        return self.run_add_model(stdin=stdin, args=[gguf], probe=True, env_extra=env)

    def spawned_args(self) -> list[str]:
        return self.args_file.read_text().split("\n")

    def test_overflow_count_becomes_n_cpu_moe(self):
        r = self.probe_run("spill", "spilly\n", "free-plain.gguf")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("'n-cpu-moe = 21'", self.builder("spilly"))

    def test_model_that_fits_entirely_gets_no_n_cpu_moe(self):
        r = self.probe_run("fits", "fitty\n", "free-plain.gguf")
        self.assertEqual(r.returncode, 0, r.stderr)
        active = [ln for ln in self.builder("fitty").splitlines()
                  if ln.strip() and not ln.lstrip().startswith("#")]
        self.assertFalse([ln for ln in active if "n-cpu-moe" in ln])

    def test_crashed_server_is_reported_with_the_log_path_not_read_as_zero(self):
        r = self.probe_run("crash", "crashy\n", "free-plain.gguf")
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn("n-cpu-moe = 0", r.stdout + r.stderr)
        self.assertRegex(r.stderr, r"/\S+\.log")
        self.assertFalse((self.repo / "build-crashy").exists(),
                         "a failed probe must not leave a half-written builder")

    def test_probe_that_never_reports_times_out_with_the_log_path(self):
        r = self.probe_run("silent", "slowy\n", "free-plain.gguf")
        self.assertNotEqual(r.returncode, 0)
        self.assertRegex(r.stderr, r"/\S+\.log")
        self.assertIn("timed out", r.stderr.lower())

    def test_probe_sends_the_two_gb_fit_target(self):
        self.probe_run("spill", "flagged\n", "free-plain.gguf")
        self.assertIn("--fit-target", self.spawned_args())
        self.assertIn("2048", self.spawned_args())

    def test_probe_sends_spec_type_only_for_an_mtp_gguf(self):
        self.probe_run("spill", "withmtp\n", "free-mtp.gguf")
        self.assertIn("--spec-type", self.spawned_args())
        self.assertIn("draft-mtp", self.spawned_args())
        self.args_file.unlink()
        self.probe_run("spill", "nomtp\n", "free-plain.gguf")
        self.assertNotIn("--spec-type", self.spawned_args())

    def test_probe_leaves_no_server_running_on_success(self):
        self.probe_run("spill", "tidy\n", "free-plain.gguf")
        left = subprocess.run(["pgrep", "-f", "FAKE_MODE|llama-server -m"],
                              capture_output=True, text=True)
        self.assertNotIn(str(self.bin), left.stdout)

    def test_no_probe_flag_skips_the_probe_entirely(self):
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "FAKE_MODE": "spill",
               "FAKE_ARGS": str(self.args_file)}
        r = self.run_add_model(stdin="skipped\n", args=["--no-probe", "free-plain.gguf"],
                               probe=True, env_extra=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.args_file.exists(), "llama-server was started despite --no-probe")
