#!/usr/bin/env python3
"""
Unit tests for `scripts/llm`, the standalone terminal helper for the llama-server
router. No live server: urlopen, the clock, nvidia-smi, stdin, and execvp are
patched. Request and response shapes match the router as observed 2026-09-15.
Run with:

    python3 -m unittest eval/test_llm.py -v
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "llm"
_loader = importlib.machinery.SourceFileLoader("llm_cli", str(SCRIPT))
_spec = importlib.util.spec_from_loader("llm_cli", _loader)
llm = importlib.util.module_from_spec(_spec)
_loader.exec_module(llm)


def listing(**states):
    """GET /models body. Each kwarg is model id -> status value (or a status dict)."""
    data = []
    for model_id, status in states.items():
        status = status if isinstance(status, dict) else {"value": status}
        data.append({"id": model_id, "status": status})
    return {"object": "list", "data": data}


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


SMI_ROWS = ("1631, /usr/bin/kwin_wayland, 63\n"
            "18804, /home/casey/src/llama.cpp/build/bin/llama-server, 6226\n")


class CliCase(unittest.TestCase):
    def setUp(self):
        self.requests: list = []
        self.clock = 0.0

        def sleep(dt):
            self.clock += dt

        for target, name, fake in ((llm.time, "sleep", sleep),
                                   (llm.time, "monotonic", lambda: self.clock)):
            p = mock.patch.object(target, name, side_effect=fake)
            p.start()
            self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("LLM_URL", None)

    def serve(self, *replies):
        queue = list(replies)

        def fake_urlopen(req, timeout=None):
            self.requests.append(req)
            reply = queue.pop(0)
            if isinstance(reply, BaseException):
                raise reply
            return FakeResponse(json.dumps(reply).encode())

        p = mock.patch.object(llm.urllib.request, "urlopen", side_effect=fake_urlopen)
        p.start()
        self.addCleanup(p.stop)

    def smi(self, stdout=SMI_ROWS, returncode=0, missing=False):
        kw = ({"side_effect": FileNotFoundError("nvidia-smi")} if missing else
              {"return_value": subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")})
        p = mock.patch.object(llm.subprocess, "run", **kw)
        p.start()
        self.addCleanup(p.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = llm.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def url(self, i):
        req = self.requests[i]
        return req if isinstance(req, str) else req.full_url

    def body(self, i):
        return json.loads(self.requests[i].data.decode())


class StatusTests(CliCase):
    def test_lists_each_model_status_and_llama_server_vram(self):
        self.serve(listing(gemma="unloaded", lite="sleeping", qwen="loaded"))
        self.smi()
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        for text in ("gemma", "unloaded", "lite", "sleeping", "qwen", "loaded", "6226 MiB"):
            self.assertIn(text, out)
        self.assertEqual(self.url(0), "http://localhost:8080/models")

    def test_vram_is_unknown_when_nvidia_smi_is_missing(self):
        self.serve(listing(lite="loaded"))
        self.smi(missing=True)
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("unknown", out)

    def test_server_down_exits_1_with_url_and_start_hint(self):
        self.serve(urllib.error.URLError("Connection refused"))
        code, _, err = self.run_cli("status")
        self.assertEqual(code, 1)
        self.assertIn("http://localhost:8080", err)
        self.assertIn("systemctl --user start llama-server", err)

    def test_llm_url_overrides_the_default(self):
        os.environ["LLM_URL"] = "http://localhost:8081/"
        self.serve(listing(lite="unloaded"))
        self.smi()
        self.run_cli("status")
        self.assertEqual(self.url(0), "http://localhost:8081/models")


class LoadTests(CliCase):
    def test_polls_until_loaded_and_reports_time(self):
        self.serve(listing(lite="unloaded"), {"success": True},
                   listing(lite="loading"), listing(lite="loaded"))
        code, out, _ = self.run_cli("load", "lite")
        self.assertEqual(code, 0)
        self.assertEqual(self.url(1), "http://localhost:8080/models/load")
        self.assertEqual(self.body(1), {"model": "lite"})
        self.assertIn("loaded", out)
        self.assertEqual(len(self.requests), 4)

    def test_already_loaded_is_reported_without_loading(self):
        self.serve(listing(lite="loaded"))
        code, out, _ = self.run_cli("load", "lite")
        self.assertEqual(code, 0)
        self.assertIn("already loaded", out)
        self.assertEqual(len(self.requests), 1)

    def test_sleeping_model_is_reported_as_waking_on_next_request(self):
        self.serve(listing(lite="sleeping"))
        code, out, _ = self.run_cli("load", "lite")
        self.assertEqual(code, 0)
        self.assertIn("sleeping", out)
        self.assertEqual(len(self.requests), 1)

    def test_unknown_model_exits_1_listing_available(self):
        self.serve(listing(gemma="unloaded", lite="unloaded"))
        code, _, err = self.run_cli("load", "nope")
        self.assertEqual(code, 1)
        self.assertIn("nope", err)
        self.assertIn("gemma, lite", err)

    def test_failed_load_exits_1_with_exit_code(self):
        self.serve(listing(lite="unloaded"), {"success": True},
                   listing(lite={"value": "unloaded", "failed": True, "exit_code": 1}))
        code, _, err = self.run_cli("load", "lite")
        self.assertEqual(code, 1)
        self.assertIn("exit code 1", err)


class UnloadTests(CliCase):
    """POST /models/unload returns before the model stops (seen live 2026-09-15):
    a status read straight after still says `loaded`, and requests routed there get
    HTTP 500. So unload is only reported once the router says `unloaded`."""

    def test_named_model_is_unloaded_and_waited_for(self):
        self.serve(listing(lite="loaded"), {"success": True},
                   listing(lite="loaded"), listing(lite="unloaded"))
        code, out, _ = self.run_cli("unload", "lite")
        self.assertEqual(code, 0)
        self.assertEqual(self.url(1), "http://localhost:8080/models/unload")
        self.assertEqual(self.body(1), {"model": "lite"})
        self.assertEqual(len(self.requests), 4)  # kept polling until `unloaded`
        self.assertIn("unloaded lite", out)

    def test_no_name_unloads_every_resident_model_including_sleeping(self):
        self.serve(listing(gemma="unloaded", lite="sleeping", qwen="loaded"),
                   {"success": True}, listing(gemma="unloaded", lite="unloaded", qwen="loaded"),
                   {"success": True}, listing(gemma="unloaded", lite="unloaded", qwen="unloaded"))
        code, _, _ = self.run_cli("unload")
        self.assertEqual(code, 0)
        posts = [self.body(i)["model"] for i, r in enumerate(self.requests) if not isinstance(r, str)]
        self.assertEqual(posts, ["lite", "qwen"])
        self.assertEqual(len(self.requests), 5)  # a status poll after each unload

    def test_unload_that_never_finishes_exits_1(self):
        self.serve(listing(lite="loaded"), {"success": True},
                   *[listing(lite="loaded") for _ in range(200)])
        code, _, err = self.run_cli("unload", "lite")
        self.assertEqual(code, 1)
        self.assertIn("still", err)

    def test_nothing_resident_says_so(self):
        self.serve(listing(gemma="unloaded", lite="unloaded"))
        code, out, _ = self.run_cli("unload")
        self.assertEqual(code, 0)
        self.assertIn("nothing loaded", out)
        self.assertEqual(len(self.requests), 1)


class ChatTests(CliCase):
    REPLY = {"choices": [{"message": {"role": "assistant", "content": "OK"}}]}

    def test_default_request_has_thinking_off_and_no_system_message(self):
        self.serve(self.REPLY)
        code, out, _ = self.run_cli("chat", "lite", "Say", "OK")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "OK")
        self.assertEqual(self.url(0), "http://localhost:8080/v1/chat/completions")
        body = self.body(0)
        self.assertEqual(body["model"], "lite")
        self.assertEqual(body["messages"], [{"role": "user", "content": "Say OK"}])
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})

    def test_think_flag_and_system_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("You are terse.")
        self.addCleanup(os.unlink, f.name)
        self.serve(self.REPLY)
        self.run_cli("chat", "--think", "--system-file", f.name, "qwen", "hi")
        body = self.body(0)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": True})
        self.assertEqual(body["messages"][0], {"role": "system", "content": "You are terse."})
        self.assertEqual(body["messages"][1], {"role": "user", "content": "hi"})

    def test_prompt_is_read_from_stdin_when_not_given(self):
        self.serve(self.REPLY)
        with mock.patch.object(llm.sys, "stdin", io.StringIO("from a pipe\n")):
            self.run_cli("chat", "lite")
        self.assertEqual(self.body(0)["messages"], [{"role": "user", "content": "from a pipe"}])

    def test_unknown_model_exits_1_with_server_message(self):
        err = urllib.error.HTTPError(
            "http://localhost:8080/v1/chat/completions", 400, "Bad Request", {},
            io.BytesIO(b'{"error":{"code":400,"message":"model \'nope\' not found"}}'))
        self.serve(err)
        code, _, stderr = self.run_cli("chat", "nope", "hi")
        self.assertEqual(code, 1)
        self.assertIn("model 'nope' not found", stderr)


class LogsTests(CliCase):
    def test_default_follows_the_last_50_lines(self):
        with mock.patch.object(llm.os, "execvp") as execvp:
            self.run_cli("logs")
        execvp.assert_called_once_with(
            "journalctl", ["journalctl", "--user", "-u", "llama-server", "-n", "50", "-f"])

    def test_extra_args_replace_the_defaults(self):
        with mock.patch.object(llm.os, "execvp") as execvp:
            self.run_cli("logs", "-n", "3", "--no-pager")
        execvp.assert_called_once_with(
            "journalctl", ["journalctl", "--user", "-u", "llama-server", "-n", "3", "--no-pager"])


if __name__ == "__main__":
    unittest.main()
