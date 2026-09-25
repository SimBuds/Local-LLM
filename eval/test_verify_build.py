#!/usr/bin/env python3
"""
Unit tests for scripts/verify-build's pure parts: reading the built preset,
building the pinned-split load command, reading a process's VRAM from
nvidia-smi, comparing the live build's footprint with the candidate's, and
refusing to run while the live service holds a model. No binary is started.
Run with:

    python3 -m unittest eval/test_verify_build.py -v
"""

from __future__ import annotations

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
            self.assertEqual(vb.service_busy("http://localhost:8080"), [])


if __name__ == "__main__":
    unittest.main()
