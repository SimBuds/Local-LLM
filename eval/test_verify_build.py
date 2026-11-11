#!/usr/bin/env python3
"""
Unit tests for scripts/verify-build's pure parts: reading the built preset,
building the pinned-split load command, reading a process's VRAM from
nvidia-smi, comparing the live build's footprint with the candidate's,
refusing to run while the live service holds a model, and the KL divergence
report (command, skips, reference cleanup, INFO rows). No binary is started.
Run with:

    python3 -m unittest eval/test_verify_build.py -v
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
_loader = importlib.machinery.SourceFileLoader("verify_build", str(REPO / "scripts" / "verify-build"))
_spec = importlib.util.spec_from_loader("verify_build", _loader)
vb = importlib.util.module_from_spec(_spec)
sys.modules["verify_build"] = vb
_loader.exec_module(vb)

PRESET = """; comment line
version = 1

[*]
parallel = 1
flash-attn = on
cache-type-k = q4_0
cache-type-v = q4_0
fit = off
n-gpu-layers = 99
no-mmproj = true
load-mode = none
ubatch-size = 1024
ctx-size = 65536

[gemma]
model = /models/gemma.gguf
n-cpu-moe = 22

[lite]
model = /models/lite.gguf
spec-type = draft-mtp
ubatch-size = 512

[qwen]
model = /models/qwen.gguf
n-cpu-moe = 35
spec-type = draft-mtp
"""

# `nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader,nounits`
SMI = ("1561, /usr/bin/kwin_wayland, 107\n"
       "4242, /home/casey/src/llama.cpp/build-next/bin/llama-server, 6034\n"
       "2295, /opt/brave-bin/brave, 208\n")


# The statistics block of a real run: llama-perplexity --kl-divergence, build
# 12174 (18ab62a8e), lite, 4 chunks, the same binary writing and reading the
# reference (2026-11-06). Same-binary KLD prints as -0.000000.
KL_LOG = """====== Perplexity statistics ======
Mean PPL(Q)                   :   7.593611 ±   0.621541
Mean PPL(base)                :   7.593604 ±   0.621540
Cor(ln(PPL(Q)), ln(PPL(base))): 100.00%
Mean ln(PPL(Q)/PPL(base))     :   0.000001 ±       -nan
Mean PPL(Q)/PPL(base)         :   1.000001 ±       -nan
Mean PPL(Q)-PPL(base)         :   0.000008 ±       -nan

====== KL divergence statistics ======
Mean    KLD:  -0.000000 ±   0.000000
Maximum KLD:   0.000064
99.9%   KLD:   0.000050
99.0%   KLD:   0.000034
95.0%   KLD:   0.000020
90.0%   KLD:   0.000016
Median  KLD:  -0.000000
10.0%   KLD:  -0.000015
 5.0%   KLD:  -0.000024
 1.0%   KLD:  -0.000040
 0.1%   KLD:  -0.000050
Minimum KLD:  -0.000052

====== Token probability statistics ======
Mean    Δp:  0.000 ± 0.000 %
Maximum Δp:  0.004%
99.9%   Δp:  0.004%
99.0%   Δp:  0.003%
95.0%   Δp:  0.001%
90.0%   Δp:  0.000%
75.0%   Δp:  0.000%
Median  Δp:  0.000%
25.0%   Δp:  0.000%
10.0%   Δp: -0.000%
 5.0%   Δp: -0.001%
 1.0%   Δp: -0.002%
 0.1%   Δp: -0.004%
Minimum Δp: -0.005%
RMS Δp    :  0.001 ± 0.000 %
Same top p: 100.000 ± 0.000 %

"""


class PresetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "models.ini"
        self.path.write_text(PRESET, encoding="utf-8")

    def test_models_inherit_defaults_and_keep_their_overrides(self):
        models = vb.read_preset(self.path)
        self.assertEqual(sorted(models), ["gemma", "lite", "qwen"])
        self.assertEqual(models["gemma"]["ubatch-size"], "1024")
        self.assertEqual(models["lite"]["ubatch-size"], "512")
        self.assertEqual(models["qwen"]["n-cpu-moe"], "35")
        self.assertNotIn("n-cpu-moe", models["lite"])
        self.assertEqual(models["gemma"]["cache-type-k"], "q4_0")

    def test_footprint_command_pins_the_split_with_fit_off(self):
        models = vb.read_preset(self.path)
        gemma = " ".join(vb.footprint_cmd("/cand/llama-server", models["gemma"], 8090))
        for want in ("-m /models/gemma.gguf", "--fit off", "-ngl 99", "--n-cpu-moe 22",
                     "-ub 1024", "--ctx-size 65536", "-ctk q4_0", "-ctv q4_0",
                     "--load-mode none", "--port 8090"):
            self.assertIn(want, gemma)
        self.assertTrue(gemma.startswith("/cand/llama-server "))
        self.assertNotIn("--spec-type", gemma)
        self.assertNotIn("fit-target", gemma)

    def test_all_gpu_model_gets_no_expert_split_and_keeps_its_own_batch(self):
        models = vb.read_preset(self.path)
        lite = " ".join(vb.footprint_cmd("/b/llama-server", models["lite"], 8090))
        self.assertNotIn("--n-cpu-moe", lite)
        self.assertIn("-ub 512", lite)
        self.assertIn("--spec-type draft-mtp", lite)


class FootprintTests(unittest.TestCase):
    def test_reads_the_processes_own_vram(self):
        self.assertEqual(vb.process_vram(4242, SMI), 6034)

    def test_missing_process_reads_none(self):
        self.assertIsNone(vb.process_vram(9999, SMI))

    def test_equal_footprints_pass(self):
        ok, why = vb.judge_footprint(6034, 6034, 100)
        self.assertTrue(ok, why)

    def test_small_growth_inside_the_tolerance_passes(self):
        ok, _ = vb.judge_footprint(6034, 6120, 100)
        self.assertTrue(ok)

    def test_a_smaller_candidate_passes(self):
        ok, _ = vb.judge_footprint(6034, 5800, 100)
        self.assertTrue(ok)

    def test_growth_beyond_the_tolerance_fails_naming_both(self):
        ok, why = vb.judge_footprint(6034, 6300, 100)
        self.assertFalse(ok)
        self.assertIn("6034", why)
        self.assertIn("6300", why)

    def test_candidate_that_did_not_load_fails(self):
        ok, why = vb.judge_footprint(6034, None, 100)
        self.assertFalse(ok)
        self.assertIn("candidate", why)

    def test_live_build_that_did_not_load_is_blamed_on_the_environment(self):
        ok, why = vb.judge_footprint(None, 6034, 100)
        self.assertFalse(ok)
        self.assertIn("live build", why)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def models_body(**states) -> FakeResponse:
    data = [{"id": m, "status": {"value": v}} for m, v in states.items()]
    return FakeResponse(json.dumps({"object": "list", "data": data}).encode())


class ServiceBusyTests(unittest.TestCase):
    def test_a_loaded_model_is_busy(self):
        with mock.patch.object(vb.urllib.request, "urlopen",
                               return_value=models_body(gemma="loaded", lite="unloaded")):
            self.assertEqual(vb.service_busy("http://localhost:8080"), ["gemma"])

    def test_sleeping_and_unloaded_models_are_not_busy(self):
        with mock.patch.object(vb.urllib.request, "urlopen",
                               return_value=models_body(gemma="sleeping", lite="unloaded")):
            self.assertEqual(vb.service_busy("http://localhost:8080"), [])

    def test_a_loading_model_is_busy(self):
        with mock.patch.object(vb.urllib.request, "urlopen",
                               return_value=models_body(qwen="loading")):
            self.assertEqual(vb.service_busy("http://localhost:8080"), ["qwen"])

    def test_a_stopped_service_is_not_busy(self):
        with mock.patch.object(vb.urllib.request, "urlopen",
                               side_effect=vb.urllib.error.URLError("refused")):
            self.assertFalse(vb.service_busy("http://localhost:8080"))

    def test_an_unreadable_answer_refuses_rather_than_reading_as_idle(self):
        # A router that answers but cannot be read may hold VRAM. Until
        # 2026-11-11 verify-build read this as idle while add-model refused.
        cases = {
            "not json": dict(return_value=FakeResponse(b"<html>not json</html>")),
            "HTTP 500": dict(side_effect=vb.urllib.error.HTTPError(
                "http://localhost:8080/models", 500, "boom", {}, None)),
        }
        for label, kw in cases.items():
            with self.subTest(label), mock.patch.object(vb.urllib.request, "urlopen", **kw):
                with self.assertRaises(vb.RouterUnreadable):
                    vb.service_busy("http://localhost:8080")


class KlCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "models.ini"
        path.write_text(PRESET, encoding="utf-8")
        self.models = vb.read_preset(path)

    def cmd(self, name, candidate):
        return " ".join(vb.kl_cmd("/b/llama-perplexity", self.models[name], "/c/wiki.test.raw",
                                  "/s/kld/gemma.kld", 20, candidate))

    def test_live_run_writes_the_reference_at_the_pinned_split(self):
        live = self.cmd("gemma", candidate=False)
        self.assertTrue(live.startswith("/b/llama-perplexity "))
        for want in ("-m /models/gemma.gguf", "-f /c/wiki.test.raw", "-c 512", "--chunks 20",
                     "--fit off", "-ngl 99", "--n-cpu-moe 22", "-ctk q4_0", "-ctv q4_0",
                     "-fa on", "--load-mode none", "--kl-divergence-base /s/kld/gemma.kld"):
            self.assertIn(want, live)
        self.assertNotIn("--kl-divergence ", live + " ")

    def test_candidate_run_reads_the_reference(self):
        cand = self.cmd("gemma", candidate=True)
        self.assertIn("--kl-divergence-base /s/kld/gemma.kld", cand)
        self.assertIn("--kl-divergence", cand.replace("--kl-divergence-base", ""))

    def test_server_only_and_oversized_settings_are_left_out(self):
        # The preset's 131072 context and 1024 ubatch exceed the 512-token KL
        # chunk, and mmproj and MTP drafting do not exist in llama-perplexity.
        for name in ("gemma", "lite", "qwen"):
            cmd = self.cmd(name, candidate=False)
            for flag in ("--ctx-size", "-ub", "--spec-type", "--no-mmproj", "--port", "-np"):
                self.assertNotIn(flag + " ", cmd + " ", f"{name}: {flag}")


def fake_run(write_ref=True, live_rc=0, cand_rc=0, cand_log=""):
    """subprocess.run stand-in: the live call writes the reference, the candidate logs."""
    calls = []

    def run(cmd, stdout=None, stderr=None, **kw):
        calls.append(cmd)
        candidate = "--kl-divergence" in cmd
        ref = Path(cmd[cmd.index("--kl-divergence-base") + 1])
        if not candidate and write_ref:
            ref.write_bytes(b"x" * 64)
        if candidate and stdout is not None:
            stdout.write(cand_log)
        return mock.Mock(returncode=cand_rc if candidate else live_rc)
    return run, calls


class ParseKlTests(unittest.TestCase):
    def test_reads_mean_kld_and_same_top_p_from_a_real_log(self):
        self.assertEqual(vb.parse_kl(KL_LOG), (-0.0, 100.0))

    def test_reads_a_nonzero_result(self):
        log = (KL_LOG.replace("Mean    KLD:  -0.000000", "Mean    KLD:   0.003172")
               .replace("Same top p: 100.000", "Same top p:  98.412"))
        self.assertEqual(vb.parse_kl(log), (0.003172, 98.412))

    def test_a_log_cut_off_before_the_statistics_reads_none(self):
        self.assertIsNone(vb.parse_kl(KL_LOG.split("====== KL divergence")[0]))

    def test_a_log_without_same_top_p_reads_none(self):
        self.assertIsNone(vb.parse_kl(KL_LOG.split("====== Token probability")[0]))


class KlCompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.out, self.scratch = root / "out", root / "scratch" / "kld"
        self.out.mkdir()
        self.live, self.cand = root / "live" / "llama-perplexity", root / "cand" / "llama-perplexity"
        for b in (self.live, self.cand):
            b.parent.mkdir()
            b.write_text("#!/bin/sh\n")
            b.chmod(0o755)
        self.corpus = root / "wiki.test.raw"
        self.corpus.write_text("text\n")
        self.settings = {"model": "/models/gemma.gguf", "n-cpu-moe": "22"}

    def compare(self):
        return vb.kl_compare(self.live, self.cand, "gemma", self.settings, self.corpus, 20,
                             self.out, self.scratch)

    def test_missing_live_perplexity_is_a_visible_skip(self):
        self.live.unlink()
        why = self.compare()
        self.assertIn("skipped", why)
        self.assertIn(str(self.live), why)

    def test_missing_candidate_perplexity_is_a_visible_skip(self):
        self.cand.unlink()
        why = self.compare()
        self.assertIn("skipped", why)
        self.assertIn(str(self.cand), why)

    def test_missing_corpus_skips_with_the_fetch_command(self):
        self.corpus.unlink()
        why = self.compare()
        self.assertIn("skipped", why)
        self.assertIn("get-wikitext-2.sh", why)

    def test_reference_goes_to_scratch_and_is_removed_after_a_failed_candidate(self):
        run, calls = fake_run(cand_rc=1)
        with mock.patch.object(vb.subprocess, "run", side_effect=run):
            why = self.compare()
        ref = Path(calls[0][calls[0].index("--kl-divergence-base") + 1])
        self.assertEqual(ref.parent, self.scratch)
        self.assertFalse(ref.exists(), "reference left behind")
        self.assertIn("candidate", why)
        self.assertIn(str(self.out), why)

    def test_live_failure_names_its_log_and_skips_the_candidate(self):
        run, calls = fake_run(live_rc=1, write_ref=False)
        with mock.patch.object(vb.subprocess, "run", side_effect=run):
            why = self.compare()
        self.assertEqual(len(calls), 1)
        self.assertIn("live", why)
        self.assertIn(str(self.out), why)

    def test_candidate_log_without_kld_says_so(self):
        run, _ = fake_run(cand_log="llama_model_load: error\n")
        with mock.patch.object(vb.subprocess, "run", side_effect=run):
            why = self.compare()
        self.assertIn("no KLD", why)
        self.assertEqual(list(self.scratch.glob("*")), [])


class MainKlRowTests(unittest.TestCase):
    def run_main(self, *argv):
        with tempfile.TemporaryDirectory() as tmp, contextlib.ExitStack() as stack:
            preset = Path(tmp) / "models.ini"
            preset.write_text(PRESET, encoding="utf-8")
            stack.enter_context(mock.patch.object(sys, "argv", [
                "verify-build", "/c/llama-server", "--live", "/l/llama-server",
                "--preset", str(preset), "--out-root", tmp, "--skip-smoke", *argv]))
            for name, value in (("version_of", "version: x"), ("service_busy", []),
                                ("measure", (6000, "")), ("xid_count", 0)):
                stack.enter_context(mock.patch.object(vb, name, return_value=value))
            kl = stack.enter_context(mock.patch.object(vb, "kl_compare",
                                                       return_value="mean KLD 0.000100"))
            out = stack.enter_context(mock.patch.object(sys, "stdout", io.StringIO()))
            rc = vb.main()
            return rc, out.getvalue(), kl.call_count

    def test_kl_rows_are_info_and_do_not_decide_the_verdict(self):
        rc, out, calls = self.run_main()
        self.assertEqual(rc, 0, out)
        self.assertEqual(calls, 3)
        self.assertIn("INFO  KL gemma", out)
        self.assertIn("mean KLD 0.000100", out)
        self.assertIn("\nPASS:", out)

    def test_skip_kl_runs_no_comparison(self):
        rc, out, calls = self.run_main("--skip-kl")
        self.assertEqual(rc, 0, out)
        self.assertEqual(calls, 0)
        self.assertNotIn("KL ", out)


if __name__ == "__main__":
    unittest.main()
