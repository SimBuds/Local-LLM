#!/usr/bin/env python3
"""
The pure parts of eval/spike-test.py: reading nvidia-smi, sizing the hold,
finding out-of-memory lines, and the PASS or FAIL verdict. No GPU or server.

    python3 -m unittest eval/test_spike_test.py -v

A mutation run sets SPIKE_TEST_SRC to a scratch copy, never the tracked script.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

EVAL = Path(__file__).resolve().parent
SRC = Path(os.environ.get("SPIKE_TEST_SRC", EVAL / "spike-test.py"))
sys.path.insert(0, str(EVAL.parent / "scripts"))

spec = importlib.util.spec_from_file_location("spike_test", SRC)
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)

OK = {"other": 2980, "peak": 9115, "free": 1125, "load": 10.5, "tps": 48.8}


class GpuMemory(unittest.TestCase):
    def test_reads_used_and_total(self):
        self.assertEqual(st.gpu_memory("981, 10240\n"), (981, 10240))

    def test_no_gpu_line_is_an_error(self):
        with self.assertRaises(ValueError):
            st.gpu_memory("")

    def test_two_gpus_is_an_error(self):
        with self.assertRaises(ValueError):
            st.gpu_memory("981, 10240\n500, 8192\n")


class HoldSize(unittest.TestCase):
    def test_tops_up_to_the_target(self):
        self.assertEqual(st.hold_size(981, 3000), 2019)

    def test_at_the_target_refuses(self):
        with self.assertRaises(SystemExit):
            st.hold_size(3000, 3000)

    def test_above_the_target_refuses(self):
        with self.assertRaises(SystemExit):
            st.hold_size(3200, 3000)


class OomLines(unittest.TestCase):
    def test_finds_both_known_markers_in_any_case(self):
        log = ("load ok\n"
               "ggml_backend_cuda_buffer_type_alloc_buffer: allocating 704 MiB: cudaMalloc failed: out of memory\n"
               "CUDA error: Out Of Memory\n"
               "srv done\n")
        self.assertEqual(len(st.oom_lines(log)), 2)

    def test_clean_log_has_none(self):
        self.assertEqual(st.oom_lines("model loaded\nslot released\n"), [])


class Verdict(unittest.TestCase):
    def test_all_cycles_and_clean_log_pass(self):
        ok, line = st.verdict([OK] * 5, [])
        self.assertTrue(ok)
        self.assertIn("5/5", line)

    def test_one_failed_cycle_fails(self):
        ok, line = st.verdict([OK] * 4 + [{"other": 2980, "error": "LoadFailed: x"}], [])
        self.assertFalse(ok)
        self.assertIn("4/5", line)

    def test_oom_line_fails_even_when_every_cycle_answered(self):
        ok, line = st.verdict([OK] * 5, ["cudaMalloc failed: out of memory"])
        self.assertFalse(ok)
        self.assertIn("out of memory", line)

    def test_zero_cycles_fails(self):
        self.assertFalse(st.verdict([], [])[0])


class Table(unittest.TestCase):
    def test_rows_match_the_testing_md_shape(self):
        text = st.table("freedom", [OK, {"other": 2990, "error": "TimeoutError: slow"}])
        self.assertIn("| 1 | 2980 MiB | 9115 MiB | 1125 MiB | 10.5 s | 48.8 tok/s |", text)
        self.assertIn("| 2 | 2990 MiB | failed: TimeoutError: slow |", text)


if __name__ == "__main__":
    unittest.main()
