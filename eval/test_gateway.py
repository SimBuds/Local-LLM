#!/usr/bin/env python3
"""
Unit tests for the model-call gateway in _ollama.py.

No live server: urlopen is patched, and the response bodies below are trimmed
captures from the llama-server router (build 10968, commit 41abbfd59) taken on
2026-09-14. Run with:

    python3 -m unittest eval/test_gateway.py -v
"""

from __future__ import annotations

import http.client
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _ollama as gw  # noqa: E402

# POST /v1/chat/completions, model "lite", thinking off, max_tokens 16.
CHAT_RESPONSE = {
    "choices": [{"finish_reason": "stop", "index": 0,
                 "message": {"role": "assistant", "content": "2"}}],
    "model": "lite",
    "object": "chat.completion",
    "usage": {"completion_tokens": 2, "prompt_tokens": 26, "total_tokens": 28},
    "timings": {"cache_n": 0, "prompt_n": 26, "prompt_ms": 119.027,
                "predicted_n": 2, "predicted_ms": 46.019,
                "draft_n": 3, "draft_n_accepted": 3},
}

# GET /models, trimmed to the fields the gateway reads.
MODELS_RESPONSE = {
    "object": "list",
    "data": [
        {"id": "gemma", "status": {"value": "unloaded"}},
        {"id": "lite", "status": {"value": "loaded"}},
        {"id": "qwen", "status": {"value": "unloaded"}},
    ],
}


class FakeResponse(io.BytesIO):
    def __init__(self, body: dict, status: int = 200):
        super().__init__(json.dumps(body).encode("utf-8"))
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class DroppedBodyResponse(FakeResponse):
    """Status line arrived, then the server died while the body was being read."""

    def read(self, *args):
        raise ConnectionResetError(104, "Connection reset by peer")


class GatewayCase(unittest.TestCase):
    """Points MODELS_DIR at a temp dir holding a built prompt for `lite`."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        models_dir = Path(self._tmp.name)
        (models_dir / "lite").mkdir()
        (models_dir / "lite" / "prompt.txt").write_text("STACK PROMPT\n", encoding="utf-8")
        patcher = mock.patch.object(gw, "MODELS_DIR", models_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)
        self.requests: list = []

    def serve(self, *bodies):
        """Patch urlopen to record each request and answer with `bodies` in order."""
        replies = list(bodies)

        def fake_urlopen(req, timeout=None):
            self.requests.append(req)
            reply = replies.pop(0)
            if isinstance(reply, BaseException):
                raise reply
            if isinstance(reply, FakeResponse):
                return reply
            return FakeResponse(reply)

        patcher = mock.patch.object(gw.urllib.request, "urlopen", side_effect=fake_urlopen)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sent(self, i: int = 0) -> dict:
        return json.loads(self.requests[i].data.decode("utf-8"))

    def url(self, i: int = 0) -> str:
        """urlopen gets a Request for POSTs and a plain URL string for GETs."""
        req = self.requests[i]
        return req if isinstance(req, str) else req.full_url


class GenerateTests(GatewayCase):
    def test_plain_call_shapes_request_and_normalizes_meta(self):
        self.serve(CHAT_RESPONSE)
        text, meta = gw.generate("lite", "Name one prime number.", timeout=30)

        self.assertEqual(self.url(0), "http://localhost:8080/v1/chat/completions")
        body = self.sent()
        self.assertEqual(body["model"], "lite")
        self.assertEqual(body["messages"], [
            {"role": "system", "content": "STACK PROMPT\n"},
            {"role": "user", "content": "Name one prime number."},
        ])
        self.assertIs(body["stream"], False)
        self.assertIs(body["cache_prompt"], False)
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
        self.assertNotIn("response_format", body)

        self.assertEqual(text, "2")
        self.assertEqual(meta["eval_count"], 2)
        self.assertAlmostEqual(meta["eval_duration"], 46.019e6)
        self.assertEqual(meta["prompt_eval_count"], 26)
        self.assertAlmostEqual(meta["prompt_eval_duration"], 119.027e6)
        self.assertNotIn("load_duration", meta)
        self.assertEqual(meta["raw"], CHAT_RESPONSE)
        self.assertAlmostEqual(gw.tok_per_s(meta), 2 / 0.046019)

    def test_system_override_replaces_the_stack_prompt(self):
        self.serve(CHAT_RESPONSE)
        gw.generate("lite", "hi", timeout=30, system="You are a helpful assistant.")
        self.assertEqual(self.sent()["messages"][0],
                         {"role": "system", "content": "You are a helpful assistant."})

    def test_system_override_does_not_need_a_built_prompt(self):
        self.serve(CHAT_RESPONSE)
        gw.generate("unbuilt", "hi", timeout=30, system="baseline")
        self.assertEqual(self.sent()["messages"][0]["content"], "baseline")

    def test_think_on_is_sent_as_template_kwarg(self):
        self.serve(CHAT_RESPONSE)
        gw.generate("lite", "hi", timeout=30, think=True)
        self.assertEqual(self.sent()["chat_template_kwargs"], {"enable_thinking": True})

    def test_schema_uses_the_openai_json_schema_shape(self):
        # The top-level {"type": "json_schema", "schema": ...} shape shown in the
        # server README was silently ignored on this build (it returned "{}");
        # only this nested shape constrained decode when probed 2026-09-14.
        schema = {"type": "object", "properties": {"prime": {"type": "integer"}}}
        self.serve(CHAT_RESPONSE)
        gw.generate("lite", "hi", timeout=30, fmt=schema)
        self.assertEqual(self.sent()["response_format"],
                         {"type": "json_schema", "json_schema": {"name": "output", "schema": schema}})

    def test_free_json_uses_json_object(self):
        self.serve(CHAT_RESPONSE)
        gw.generate("lite", "hi", timeout=30, fmt="json")
        self.assertEqual(self.sent()["response_format"], {"type": "json_object"})

    def test_options_are_mapped_to_llama_server_fields(self):
        self.serve(CHAT_RESPONSE)
        gw.generate("lite", "hi", timeout=30,
                    options={"num_predict": 200, "seed": 1003, "temperature": 0.0})
        body = self.sent()
        self.assertEqual(body["max_tokens"], 200)
        self.assertEqual(body["seed"], 1003)
        self.assertEqual(body["temperature"], 0.0)
        self.assertNotIn("num_predict", body)
        self.assertNotIn("options", body)

    def test_num_ctx_is_rejected_before_any_request(self):
        self.serve(CHAT_RESPONSE)
        with self.assertRaisesRegex(ValueError, "num_ctx"):
            gw.generate("lite", "hi", timeout=30, options={"num_ctx": 32768})
        self.assertEqual(self.requests, [])

    def test_unknown_option_is_rejected_before_any_request(self):
        self.serve(CHAT_RESPONSE)
        with self.assertRaisesRegex(ValueError, "mirostat"):
            gw.generate("lite", "hi", timeout=30, options={"mirostat": 2})
        self.assertEqual(self.requests, [])

    def test_response_without_timings_gives_zero_counts(self):
        body = {k: v for k, v in CHAT_RESPONSE.items() if k != "timings"}
        self.serve(body)
        text, meta = gw.generate("lite", "hi", timeout=30)
        self.assertEqual(text, "2")
        self.assertEqual(meta["eval_count"], 0)
        self.assertEqual(meta["prompt_eval_count"], 0)
        self.assertEqual(gw.tok_per_s(meta), 0.0)
        self.assertEqual(gw.prompt_tok_per_s(meta), 0.0)

    def test_missing_built_prompt_raises_instead_of_sending_no_stack(self):
        self.serve(CHAT_RESPONSE)
        with self.assertRaises(FileNotFoundError):
            gw.generate("unbuilt", "hi", timeout=30)
        self.assertEqual(self.requests, [])


class TransportErrorTests(GatewayCase):
    """Connection-level failures must reach runners as URLError.

    run-persona, run-content, and run-json catch only (URLError, TimeoutError).
    Stopping `make serve` mid-request raised http.client.RemoteDisconnected on
    2026-09-14, which is neither, so the run crashed instead of counting a
    connection failure toward check_alive()'s dead-server abort.
    """

    def test_disconnect_before_status_line_becomes_urlerror(self):
        dropped = http.client.RemoteDisconnected("Remote end closed connection without response")
        self.serve(dropped)
        with self.assertRaises(urllib.error.URLError) as cm:
            gw.generate("lite", "hi", timeout=30)
        self.assertIs(cm.exception.__cause__, dropped)

    def test_reset_while_reading_body_becomes_urlerror(self):
        self.serve(DroppedBodyResponse(CHAT_RESPONSE))
        with self.assertRaises(urllib.error.URLError) as cm:
            gw.generate("lite", "hi", timeout=30)
        self.assertIsInstance(cm.exception.__cause__, ConnectionResetError)

    def test_timeout_is_not_reclassified(self):
        self.serve(TimeoutError("timed out"))
        with self.assertRaises(TimeoutError) as cm:
            gw.generate("lite", "hi", timeout=30)
        self.assertNotIsInstance(cm.exception, urllib.error.URLError)

    def test_http_error_is_not_reclassified(self):
        bad = urllib.error.HTTPError(gw.CHAT_URL, 400, "model 'nope' not found", {}, None)
        self.serve(bad)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            gw.generate("lite", "hi", timeout=30)
        self.assertIs(cm.exception, bad)


class PreflightTests(GatewayCase):
    def test_server_down_aborts(self):
        self.serve(urllib.error.URLError("connection refused"))
        with self.assertRaises(SystemExit) as cm:
            gw.preflight(["lite"])
        self.assertIn("make serve", str(cm.exception))

    def test_model_missing_from_router_aborts_and_lists_available(self):
        self.serve(MODELS_RESPONSE, MODELS_RESPONSE)
        with self.assertRaises(SystemExit) as cm:
            gw.preflight(["lite", "mystery"])
        msg = str(cm.exception)
        self.assertIn("mystery", msg)
        self.assertIn("gemma, lite, qwen", msg)
        self.assertEqual(self.url(0), "http://localhost:8080/models")

    def test_model_without_built_prompt_aborts(self):
        self.serve(MODELS_RESPONSE, MODELS_RESPONSE)
        with self.assertRaises(SystemExit) as cm:
            gw.preflight(["gemma"])  # in the router, but no models/gemma/prompt.txt
        self.assertIn("prompt.txt", str(cm.exception))

    def test_think_spec_resolves_before_lookup_and_passes(self):
        self.serve(MODELS_RESPONSE, MODELS_RESPONSE)
        gw.preflight(["lite:think"])  # no exception


def router_listing(model: str | None, value: str = "unloaded", **status) -> dict:
    """GET /models body holding `model` in state `value` (None: model absent).

    Shapes captured from the router 2026-09-14: a load returns immediately, the
    status moves loading -> loaded, and a failed load reads unloaded with
    `failed: true` and an `exit_code`.
    """
    data = [{"id": "qwen", "status": {"value": "unloaded"}}]
    if model:
        data.append({"id": model, "status": {"value": value, **status}})
    return {"object": "list", "data": data}


LOADED_ARGS = ["/home/casey/src/llama.cpp/build/bin/llama-server",
               "--model", "/home/casey/models/gguf/gemma4-26b-a4b-it-qat.gguf",
               "--n-cpu-moe", "18", "--n-gpu-layers", "99"]
SUCCESS = {"success": True}


class LoadModelTests(GatewayCase):
    """load_model() must always time a cold load through the router."""

    def setUp(self):
        super().setUp()
        self.clock = 0.0

        def sleep(dt):
            self.clock += dt

        for name, fake in (("sleep", sleep), ("monotonic", lambda: self.clock)):
            patcher = mock.patch.object(gw.time, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_unloaded_model_is_loaded_and_timed(self):
        self.serve(router_listing("gemma", "unloaded"), SUCCESS,
                   router_listing("gemma", "loading"),
                   router_listing("gemma", "loaded", args=LOADED_ARGS))
        seconds, args = gw.load_model("gemma", timeout=60)

        self.assertAlmostEqual(seconds, gw.LOAD_POLL_S)
        self.assertEqual(args, LOADED_ARGS)
        self.assertEqual(self.url(1), "http://localhost:8080/models/load")
        self.assertEqual(self.sent(1), {"model": "gemma"})
        self.assertEqual([self.url(i) for i in (0, 2, 3)],
                         ["http://localhost:8080/models"] * 3)

    def test_loaded_model_is_unloaded_first_and_unload_wait_is_not_counted(self):
        self.serve(router_listing("gemma", "loaded"), SUCCESS,
                   router_listing("gemma", "loaded"),
                   router_listing("gemma", "unloaded"), SUCCESS,
                   router_listing("gemma", "loaded", args=LOADED_ARGS))
        seconds, _ = gw.load_model("gemma", timeout=60)

        self.assertEqual(self.url(1), "http://localhost:8080/models/unload")
        self.assertEqual(self.sent(1), {"model": "gemma"})
        self.assertEqual(self.url(4), "http://localhost:8080/models/load")
        self.assertGreater(self.clock, 0.0)  # time did pass while unloading
        self.assertEqual(seconds, 0.0)       # none of it counted as load time

    def test_failed_load_raises_with_exit_code(self):
        self.serve(router_listing("gemma", "unloaded"), SUCCESS,
                   router_listing("gemma", "unloaded", failed=True, exit_code=1))
        with self.assertRaisesRegex(gw.LoadFailed, "exit code 1"):
            gw.load_model("gemma", timeout=60)

    def test_model_that_never_loads_times_out(self):
        self.serve(router_listing("gemma", "unloaded"), SUCCESS,
                   *[router_listing("gemma", "loading") for _ in range(50)])
        with self.assertRaises(TimeoutError):
            gw.load_model("gemma", timeout=1)

    def test_model_absent_from_router_raises_without_loading(self):
        self.serve(router_listing(None))
        with self.assertRaisesRegex(gw.LoadFailed, "gemma"):
            gw.load_model("gemma", timeout=60)
        self.assertEqual(len(self.requests), 1)


class ServedCtxTests(GatewayCase):
    """served_ctx() reads the context the router actually serves, not a declared value."""

    # GET /props?model=lite, trimmed (captured 2026-09-14; the call autoloads).
    PROPS = {"build_info": "b10968-41abbfd59", "is_sleeping": False,
             "default_generation_settings": {"n_ctx": 32768, "params": {"temperature": 0.2}}}

    def test_reads_n_ctx_from_props(self):
        self.serve(self.PROPS)
        self.assertEqual(gw.served_ctx("lite"), 32768)
        self.assertEqual(self.url(0), "http://localhost:8080/props?model=lite")

    def test_model_name_is_url_encoded(self):
        self.serve(self.PROPS)
        gw.served_ctx("my model")
        self.assertEqual(self.url(0), "http://localhost:8080/props?model=my%20model")

    def test_props_without_n_ctx_raises(self):
        self.serve({"build_info": "b10968-41abbfd59"})
        with self.assertRaisesRegex(gw.LoadFailed, "n_ctx"):
            gw.served_ctx("lite")


class CheckAliveTests(GatewayCase):
    def test_below_streak_does_not_probe(self):
        self.serve()  # any request would pop from an empty list and fail the test
        gw.check_alive(gw.DEAD_SERVER_STREAK - 1)
        self.assertEqual(self.requests, [])

    def test_at_streak_with_server_down_aborts(self):
        self.serve(urllib.error.URLError("connection refused"))
        with self.assertRaises(gw.DeadServer) as cm:
            gw.check_alive(gw.DEAD_SERVER_STREAK)
        self.assertIn("make serve", str(cm.exception))

    def test_at_streak_with_server_up_continues(self):
        self.serve(MODELS_RESPONSE)
        gw.check_alive(gw.DEAD_SERVER_STREAK)  # no exception


if __name__ == "__main__":
    unittest.main()
