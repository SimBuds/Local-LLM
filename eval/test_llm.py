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


def sse(*deltas, timings=None, keepalive=False):
    """A streamed /v1/chat/completions body, shaped as the router sent it 2026-09-15.
    Each delta is a dict such as {"content": "Hi"} or {"reasoning_content": "Hmm"}."""
    lines = [{"choices": [{"finish_reason": None, "index": 0,
                           "delta": {"role": "assistant", "content": None}}]}]
    lines += [{"choices": [{"finish_reason": None, "index": 0, "delta": d}]} for d in deltas]
    final = {"choices": [{"finish_reason": "stop", "index": 0, "delta": {}}]}
    if timings:
        final["timings"] = timings
    lines.append(final)
    out = b": keepalive\n\n" if keepalive else b""
    for line in lines:
        out += b"data: " + json.dumps(line).encode() + b"\n\n"
    return FakeResponse(out + b"data: [DONE]\n\n")


class DroppedStream(FakeResponse):
    """Yields its lines, then fails the way a closed connection does mid-read."""

    def __iter__(self):
        yield from iter(self.readline, b"")
        raise ConnectionResetError(104, "Connection reset by peer")


TIMINGS = {"cache_n": 0, "prompt_n": 19, "prompt_per_second": 415.67, "predicted_n": 316,
           "predicted_per_second": 157.71, "draft_n": 273, "draft_n_accepted": 226}

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
            if isinstance(reply, io.IOBase):  # a prepared streaming response
                return reply
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

    def test_down_message_shows_the_reason_not_the_python_repr(self):
        self.serve(urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")))
        _, _, err = self.run_cli("status")
        self.assertIn("Connection refused", err)
        self.assertNotIn("<urlopen error", err)

    def test_bare_connection_error_is_still_readable(self):
        self.serve(ConnectionResetError(104, "Connection reset by peer"))
        code, _, err = self.run_cli("status")
        self.assertEqual(code, 1)
        self.assertIn("Connection reset by peer", err)

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
    @staticmethod
    def reply():
        return sse({"content": "O"}, {"content": "K"})

    def test_default_request_has_thinking_off_and_no_system_message(self):
        self.serve(listing(lite="loaded"), self.reply())
        code, out, _ = self.run_cli("chat", "lite", "Say", "OK")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "OK")
        self.assertEqual(self.url(1), "http://localhost:8080/v1/chat/completions")
        body = self.body(1)
        self.assertEqual(body["model"], "lite")
        self.assertEqual(body["messages"], [{"role": "user", "content": "Say OK"}])
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertIs(body["stream"], True)

    def test_answer_deltas_go_to_stdout_in_order_and_nothing_to_stderr(self):
        self.serve(listing(lite="loaded"),
                   sse({"content": "Hello"}, {"content": ", "}, {"content": "world"}, keepalive=True))
        code, out, err = self.run_cli("chat", "lite", "hi")
        self.assertEqual(code, 0)
        self.assertEqual(out, "Hello, world\n")
        self.assertEqual(err, "")

    def test_thinking_goes_to_stderr_only(self):
        self.serve(listing(qwen="loaded"),
                   sse({"reasoning_content": "13 times"}, {"reasoning_content": " 17"},
                       {"content": "No."}))
        code, out, err = self.run_cli("chat", "--think", "qwen", "Is 221 prime?")
        self.assertEqual(code, 0)
        self.assertEqual(out, "No.\n")
        self.assertIn("13 times 17", err)
        self.assertIn("Thinking...", err)
        self.assertIn("...done thinking.", err)
        self.assertLess(err.index("Thinking..."), err.index("13 times"))
        self.assertLess(err.index("13 times"), err.index("...done thinking."))

    def test_verbose_prints_stats_from_the_final_chunk_to_stderr(self):
        self.serve(listing(lite="loaded"), sse({"content": "OK"}, timings=TIMINGS))
        code, out, err = self.run_cli("chat", "--verbose", "lite", "hi")
        self.assertEqual(code, 0)
        self.assertEqual(out, "OK\n")
        for text in ("prompt 19 tok", "415.7 tok/s", "answer 316 tok", "157.7 tok/s", "226/273"):
            self.assertIn(text, err)

    def test_no_stats_without_verbose(self):
        self.serve(listing(lite="loaded"), sse({"content": "OK"}, timings=TIMINGS))
        _, _, err = self.run_cli("chat", "lite", "hi")
        self.assertEqual(err, "")

    def test_verbose_without_timings_skips_the_stats_line(self):
        self.serve(listing(lite="loaded"), sse({"content": "OK"}))
        code, out, err = self.run_cli("chat", "--verbose", "lite", "hi")
        self.assertEqual((code, out, err), (0, "OK\n", ""))

    def test_connection_dropped_mid_answer_keeps_partial_text_and_exits_1(self):
        body = sse({"content": "Partial"}).getvalue().replace(b"data: [DONE]\n\n", b"")
        body = body[:body.index(b'data: {"choices": [{"finish_reason": "stop"')]
        self.serve(listing(lite="loaded"), DroppedStream(body))
        code, out, err = self.run_cli("chat", "lite", "hi")
        self.assertEqual(code, 1)
        self.assertIn("Partial", out)
        self.assertIn("Connection reset by peer", err)
        self.assertNotIn("Traceback", err)

    def test_error_event_in_the_stream_exits_1_with_its_message(self):
        body = (b'data: {"choices": [{"index": 0, "delta": {"content": "Hi"}}]}\n\n'
                b'data: {"error": {"code": 500, "message": "slot crashed"}}\n\n')
        self.serve(listing(lite="loaded"), FakeResponse(body))
        code, _, err = self.run_cli("chat", "lite", "hi")
        self.assertEqual(code, 1)
        self.assertIn("slot crashed", err)

    def test_think_flag_and_system_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("You are terse.")
        self.addCleanup(os.unlink, f.name)
        self.serve(listing(qwen="loaded"), self.reply())
        self.run_cli("chat", "--think", "--system-file", f.name, "qwen", "hi")
        body = self.body(1)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": True})
        self.assertEqual(body["messages"][0], {"role": "system", "content": "You are terse."})
        self.assertEqual(body["messages"][1], {"role": "user", "content": "hi"})

    def test_prompt_is_read_from_stdin_when_not_given(self):
        self.serve(listing(lite="loaded"), self.reply())
        with mock.patch.object(llm.sys, "stdin", io.StringIO("from a pipe\n")):
            self.run_cli("chat", "lite")
        self.assertEqual(self.body(1)["messages"], [{"role": "user", "content": "from a pipe"}])

    def test_unknown_model_exits_1_listing_available_without_sending(self):
        self.serve(listing(gemma="unloaded", lite="loaded"))
        code, _, stderr = self.run_cli("chat", "nope", "hi")
        self.assertEqual(code, 1)
        self.assertIn("unknown model 'nope'", stderr)
        self.assertIn("gemma, lite", stderr)
        self.assertEqual(len(self.requests), 1)  # only the model listing, no chat POST

    def test_router_error_during_chat_still_shows_its_message(self):
        err = urllib.error.HTTPError(
            "http://localhost:8080/v1/chat/completions", 400, "Bad Request", {},
            io.BytesIO(b'{"error":{"code":400,"message":"request exceeds the available context size"}}'))
        self.serve(listing(lite="loaded"), err)
        code, _, stderr = self.run_cli("chat", "lite", "hi")
        self.assertEqual(code, 1)
        self.assertIn("exceeds the available context size", stderr)

    def test_router_down_on_chat_exits_1_with_start_hint(self):
        self.serve(urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")))
        code, _, stderr = self.run_cli("chat", "lite", "hi")
        self.assertEqual(code, 1)
        self.assertIn("systemctl --user start llama-server", stderr)


JOURNAL = """\
Sep 15 15:05:27 Caseys-PC llama-server[1449]: 14.50.825.796 I srv  ensure_model: model name=lite is not loaded, loading...
Sep 15 15:05:27 Caseys-PC llama-server[1449]: 14.50.825.858 I srv          load: spawning server instance with name=lite on port 49823
Sep 15 15:05:27 Caseys-PC llama-server[1449]: 14.50.825.869 I srv          load: spawning server instance with args:
Sep 15 15:05:27 Caseys-PC llama-server[1449]: 14.50.825.869 I srv          load:   /home/casey/src/llama.cpp/build/bin/llama-server
Sep 15 15:05:27 Caseys-PC llama-server[1449]: 14.50.825.870 I srv          load:   --host
Sep 15 15:05:27 Caseys-PC llama-server[1449]: 14.50.825.871 I srv          load:   127.0.0.1
Sep 15 15:05:27 Caseys-PC llama-server[1449]: 14.50.826.007 I srv  ensure_model: waiting until model name=lite is fully loaded...
Sep 15 15:05:28 Caseys-PC llama-server[1449]: [49823] 0.01.236.655 I srv  llama_server: model loaded
"""
KEPT = [l for l in JOURNAL.splitlines(keepends=True) if "load:   " not in l]


class FakeJournal:
    """Stands in for the journalctl child: yields lines, optionally Ctrl-C part way."""

    def __init__(self, text, returncode=0, interrupt_after=None):
        self.lines = text.splitlines(keepends=True)
        self.returncode = returncode
        self.interrupt_after = interrupt_after
        self.terminated = False
        self.stdout = self._read()

    def _read(self):
        for i, line in enumerate(self.lines):
            if i == self.interrupt_after:
                raise KeyboardInterrupt
            yield line

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True


class LogsTests(CliCase):
    def journal(self, fake):
        p = mock.patch.object(llm.subprocess, "Popen", return_value=fake)
        popen = p.start()
        self.addCleanup(p.stop)
        return popen

    def test_default_follows_the_last_50_lines_through_the_filter(self):
        popen = self.journal(FakeJournal(JOURNAL))
        self.run_cli("logs")
        self.assertEqual(popen.call_args.args[0],
                         ["journalctl", "--user", "-u", "llama-server", "-n", "50", "-f"])

    def test_extra_args_replace_the_defaults(self):
        popen = self.journal(FakeJournal(JOURNAL))
        self.run_cli("logs", "-n", "3", "--no-pager")
        self.assertEqual(popen.call_args.args[0],
                         ["journalctl", "--user", "-u", "llama-server", "-n", "3", "--no-pager"])

    def test_spawn_argument_lines_are_dropped_and_the_rest_kept_in_order(self):
        self.journal(FakeJournal(JOURNAL))
        code, out, _ = self.run_cli("logs", "-n", "8", "--no-pager")
        self.assertEqual(code, 0)
        self.assertEqual(out, "".join(KEPT))
        self.assertIn("spawning server instance with args:", out)
        self.assertNotIn("--host", out)

    def test_all_shows_every_line_unfiltered(self):
        with mock.patch.object(llm.os, "execvp") as execvp:
            self.run_cli("logs", "--all", "-n", "200")
        execvp.assert_called_once_with(
            "journalctl", ["journalctl", "--user", "-u", "llama-server", "-n", "200"])

    def test_all_alone_keeps_the_default_follow(self):
        with mock.patch.object(llm.os, "execvp") as execvp:
            self.run_cli("logs", "--all")
        execvp.assert_called_once_with(
            "journalctl", ["journalctl", "--user", "-u", "llama-server", "-n", "50", "-f"])

    def test_ctrl_c_stops_quietly_with_exit_0(self):
        fake = FakeJournal(JOURNAL, interrupt_after=2)
        self.journal(fake)
        code, out, err = self.run_cli("logs")
        self.assertEqual(code, 0)
        self.assertTrue(fake.terminated)
        self.assertIn("ensure_model", out)
        self.assertEqual(err, "")

    def test_journalctl_exit_code_is_returned(self):
        self.journal(FakeJournal("", returncode=1))
        code, _, _ = self.run_cli("logs", "-n", "1")
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
