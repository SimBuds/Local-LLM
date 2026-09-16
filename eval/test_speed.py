#!/usr/bin/env python3
"""
Unit tests for the pure helpers in run-speed.py. No live server, no GPU needed:
nvidia-smi is patched with output captured from this box on 2026-09-14. Run with:

    python3 -m unittest eval/test_speed.py -v
"""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PATH = Path(__file__).resolve().parent / "run-speed.py"
_spec = importlib.util.spec_from_file_location("run_speed", _PATH)
speed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(speed)

BASE_ARGS = ["/home/casey/src/llama.cpp/build/bin/llama-server", "--host", "127.0.0.1",
             "--ctx-size", "65536", "--n-gpu-layers", "99", "--parallel", "1"]

# `nvidia-smi --query-compute-apps=pid,process_name,used_memory
#  --format=csv,noheader,nounits` with gemma loaded and a browser open.
SMI_ROWS = ("18063, /opt/brave-bin/brave, 54\n"
            "80244, /home/casey/src/llama.cpp/build/bin/llama-server, 7400\n"
            "1616, /usr/bin/kwin_wayland, 91\n")


class DeclaredOffloadTests(unittest.TestCase):
    def test_no_expert_offload_is_all_gpu(self):
        self.assertEqual(speed.declared_offload(BASE_ARGS), "all GPU")

    def test_zero_expert_layers_is_all_gpu(self):
        self.assertEqual(speed.declared_offload(BASE_ARGS + ["--n-cpu-moe", "0"]), "all GPU")

    def test_expert_layers_on_cpu_are_named(self):
        self.assertEqual(speed.declared_offload(BASE_ARGS + ["--n-cpu-moe", "18"]),
                         "GPU + 18 MoE layers on CPU")

    def test_mtp_is_appended(self):
        args = BASE_ARGS + ["--n-cpu-moe", "32", "--spec-type", "draft-mtp"]
        self.assertEqual(speed.declared_offload(args), "GPU + 32 MoE layers on CPU, MTP")


class GgufSizeTests(unittest.TestCase):
    def test_size_of_the_model_file_in_gib(self):
        with tempfile.NamedTemporaryFile() as f:
            f.truncate(3 * 2**30 // 2)  # sparse 1.5 GiB
            self.assertEqual(speed.gguf_size(BASE_ARGS + ["--model", f.name]), "1.5 GiB")

    def test_missing_model_arg_or_file_is_unknown(self):
        self.assertEqual(speed.gguf_size(BASE_ARGS), "?")
        self.assertEqual(speed.gguf_size(BASE_ARGS + ["--model", "/nonexistent.gguf"]), "?")


class GpuMibTests(unittest.TestCase):
    def smi(self, stdout="", returncode=0):
        return mock.patch.object(
            speed.subprocess, "run",
            return_value=subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=""))

    def test_sums_only_llama_server_processes(self):
        rows = SMI_ROWS + "80999, /home/casey/src/llama.cpp/build/bin/llama-server, 600\n"
        with self.smi(rows):
            self.assertEqual(speed.gpu_mib(), 8000)

    def test_no_llama_server_row_is_unknown_not_zero(self):
        with self.smi("1616, /usr/bin/kwin_wayland, 91\n"):
            self.assertIsNone(speed.gpu_mib())

    def test_nvidia_smi_failing_is_unknown(self):
        with self.smi(SMI_ROWS, returncode=9):
            self.assertIsNone(speed.gpu_mib())

    def test_nvidia_smi_missing_is_unknown(self):
        with mock.patch.object(speed.subprocess, "run", side_effect=FileNotFoundError("nvidia-smi")):
            self.assertIsNone(speed.gpu_mib())


if __name__ == "__main__":
    unittest.main()
