#!/usr/bin/env python3
"""
Unit tests for the model-call gateway in _gateway.py.

No live server: urlopen is patched, and the response bodies below are trimmed
captures from the llama-server router (build 10968, commit 41abbfd59) taken on
2026-09-14. Run with:

    python3 -m unittest eval/test_gateway.py -v
"""

from __future__ import annotations

import argparse
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

import _gateway as gw  # noqa: E402

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
            gw.generate("lite", "hi", timeout=30, options={"num_ctx": 65536})
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


# POST /v1/chat/completions with two tools, model "lite", captured 2026-09-17
# from build 11022 (f172be756). All three models answered this shape.
TOOL_CALL_RESPONSE = {
    "choices": [{"finish_reason": "tool_calls", "index": 0,
                 "message": {"role": "assistant", "content": "",
                             "tool_calls": [
                                 {"type": "function", "id": "KMKmfqx78KuU6MbldYL8kWvevehBPLZI",
                                  "function": {"name": "get_weather",
                                               "arguments": "{\"city\":\"Toronto\",\"unit\":\"c\"}"}},
                                 {"type": "function", "id": "ZNH7bx4fGv4Tm3Vg0RivoWGAQ0Tqwyj5",
                                  "function": {"name": "get_weather",
                                               "arguments": "{\"city\":\"Oslo\",\"unit\":\"c\"}"}}]}}],
    "model": "lite",
    "object": "chat.completion",
    "timings": {"prompt_n": 900, "prompt_ms": 300.0, "predicted_n": 40, "predicted_ms": 350.0},
}

WEATHER_TOOL = {
    "type": "function",
    "function": {"name": "get_weather", "description": "Current weather for a city",
                 "parameters": {"type": "object",
                                "properties": {"city": {"type": "string"},
                                               "unit": {"type": "string", "enum": ["c", "f"]}},
                                "required": ["city"]}},
}


class ChatTests(GatewayCase):
    """chat() is the message-list entry point the tool suite needs."""

    def test_tools_are_sent_and_calls_come_back_parsed(self):
        self.serve(TOOL_CALL_RESPONSE)
        text, calls, meta = gw.chat(
            "lite", [{"role": "user", "content": "Weather in Toronto and Oslo, celsius?"}],
            timeout=30, tools=[WEATHER_TOOL])

        body = self.sent()
        self.assertEqual(body["tools"], [WEATHER_TOOL])
        self.assertEqual(body["messages"], [
            {"role": "system", "content": "STACK PROMPT\n"},
            {"role": "user", "content": "Weather in Toronto and Oslo, celsius?"},
        ])
        self.assertEqual(text, "")
        self.assertEqual([c["name"] for c in calls], ["get_weather", "get_weather"])
        self.assertEqual(calls[0]["arguments"], {"city": "Toronto", "unit": "c"})
        self.assertEqual(calls[1]["arguments"], {"city": "Oslo", "unit": "c"})
        self.assertEqual(calls[0]["id"], "KMKmfqx78KuU6MbldYL8kWvevehBPLZI")
        self.assertIs(calls[0]["arguments_ok"], True)
        self.assertEqual(meta["eval_count"], 40)

    def test_multi_turn_messages_pass_through_with_the_stack_first(self):
        self.serve(CHAT_RESPONSE)
        turns = [
            {"role": "user", "content": "Weather in Oslo?"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"type": "function", "id": "abc",
                 "function": {"name": "get_weather", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "abc", "content": "{\"temp_c\": 7}"},
        ]
        gw.chat("lite", turns, timeout=30)
        self.assertEqual(self.sent()["messages"][0],
                         {"role": "system", "content": "STACK PROMPT\n"})
        self.assertEqual(self.sent()["messages"][1:], turns)

    def test_caller_supplied_system_message_is_not_duplicated(self):
        self.serve(CHAT_RESPONSE)
        gw.chat("lite", [{"role": "system", "content": "OWN"},
                         {"role": "user", "content": "hi"}], timeout=30)
        roles = [m["role"] for m in self.sent()["messages"]]
        self.assertEqual(roles, ["system", "user"])
        self.assertEqual(self.sent()["messages"][0]["content"], "OWN")

    def test_no_tool_calls_gives_an_empty_list_not_none(self):
        self.serve(CHAT_RESPONSE)
        text, calls, _ = gw.chat("lite", [{"role": "user", "content": "hi"}], timeout=30)
        self.assertEqual(text, "2")
        self.assertEqual(calls, [])

    def test_unparseable_arguments_are_kept_raw_and_flagged(self):
        broken = json.loads(json.dumps(TOOL_CALL_RESPONSE))
        broken["choices"][0]["message"]["tool_calls"] = [
            {"type": "function", "id": "x",
             "function": {"name": "get_weather", "arguments": "{city: Toronto"}}]
        self.serve(broken)
        _, calls, _ = gw.chat("lite", [{"role": "user", "content": "hi"}], timeout=30)
        self.assertIs(calls[0]["arguments_ok"], False)
        self.assertEqual(calls[0]["arguments_raw"], "{city: Toronto")
        self.assertEqual(calls[0]["arguments"], {})

    def test_tools_are_omitted_when_none_are_given(self):
        self.serve(CHAT_RESPONSE)
        gw.chat("lite", [{"role": "user", "content": "hi"}], timeout=30)
        self.assertNotIn("tools", self.sent())

    def test_options_and_thinking_reach_the_request(self):
        self.serve(CHAT_RESPONSE)
        gw.chat("lite", [{"role": "user", "content": "hi"}], timeout=30,
                think=True, options={"num_predict": 64, "seed": 7})
        body = self.sent()
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": True})
        self.assertEqual(body["max_tokens"], 64)
        self.assertEqual(body["seed"], 7)

    def test_num_ctx_is_rejected_before_any_request(self):
        self.serve(CHAT_RESPONSE)
        with self.assertRaises(ValueError):
            gw.chat("lite", [{"role": "user", "content": "hi"}], timeout=30,
                    options={"num_ctx": 4096})
        self.assertEqual(self.requests, [])

    def test_dropped_connection_becomes_urlerror(self):
        self.serve(http.client.RemoteDisconnected("closed"))
        with self.assertRaises(urllib.error.URLError):
            gw.chat("lite", [{"role": "user", "content": "hi"}], timeout=30)


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

    def test_sleeping_model_is_unloaded_before_a_timed_load(self):
        # server.ini sets sleep-idle-seconds, so an idle model reads `sleeping`
        # (observed 2026-09-15). Timing a wake from sleep would report ~1s as a
        # "cold load", so it must be unloaded first like a loaded model.
        self.serve(router_listing("gemma", "sleeping"), SUCCESS,
                   router_listing("gemma", "unloaded"), SUCCESS,
                   router_listing("gemma", "loaded", args=LOADED_ARGS))
        seconds, args = gw.load_model("gemma", timeout=60)

        self.assertEqual(self.url(1), "http://localhost:8080/models/unload")
        self.assertEqual(self.url(3), "http://localhost:8080/models/load")
        self.assertEqual(args, LOADED_ARGS)

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
    # n_ctx is the served 65536 since 2026-09-16. A global replace had left it
    # as 3276553668, which failed against the 65536 the assertion expects.
    PROPS = {"build_info": "b10968-41abbfd59", "is_sleeping": False,
             "default_generation_settings": {"n_ctx": 65536, "params": {"temperature": 0.2}}}

    def test_reads_n_ctx_from_props(self):
        self.serve(self.PROPS)
        self.assertEqual(gw.served_ctx("lite"), 65536)
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


def http_500() -> urllib.error.HTTPError:
    """What a runner sees when the router proxies to a model instance that died."""
    return urllib.error.HTTPError(gw.CHAT_URL, 500, "Internal Server Error", {}, io.BytesIO(b""))


class AfterFailureTests(GatewayCase):
    """after_failure() tells a crashed model apart from a model that answered badly.

    The router records a crashed instance as `unloaded` with `failed: true` and
    its exit code (server-models.cpp, on_child_exit), and clears that when the
    model reloads. The error can reach the client before the exit is recorded, so
    a server-side failure is followed by a short poll rather than a single read.
    """

    def setUp(self):
        super().setUp()
        self.clock = 0.0

        def sleep(dt):
            self.clock += dt

        for name, fake in (("sleep", sleep), ("monotonic", lambda: self.clock)):
            patcher = mock.patch.object(gw.time, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_crashed_model_aborts_naming_the_model_and_exit_code(self):
        self.serve(router_listing("gemma", "unloaded", failed=True, exit_code=134))
        with self.assertRaises(gw.ModelCrashed) as cm:
            gw.after_failure("gemma", http_500(), streak=1)
        self.assertIn("gemma", str(cm.exception))
        self.assertIn("134", str(cm.exception))

    def test_crash_recorded_after_the_error_is_still_caught(self):
        # 2026-09-17 04:11: the 500s went out while the core dumped, and the
        # router recorded the exit about 5 s later.
        self.serve(router_listing("gemma", "loaded"), router_listing("gemma", "loaded"),
                   router_listing("gemma", "unloaded", failed=True, exit_code=1))
        with self.assertRaises(gw.ModelCrashed):
            gw.after_failure("gemma", http_500(), streak=1)

    def test_model_that_stays_loaded_counts_as_its_own_failure(self):
        steady = router_listing("gemma", "loaded")
        polls = int(gw.CRASH_SETTLE_S / gw.CRASH_POLL_S) + 5
        self.serve(*[steady] * polls)
        gw.after_failure("gemma", http_500(), streak=1)  # no exception
        self.assertLessEqual(self.clock, gw.CRASH_SETTLE_S + gw.CRASH_POLL_S)

    def test_model_mid_reload_counts_as_its_own_failure(self):
        loading = router_listing("gemma", "loading")
        polls = int(gw.CRASH_SETTLE_S / gw.CRASH_POLL_S) + 5
        self.serve(*[loading] * polls)
        gw.after_failure("gemma", http_500(), streak=1)

    def test_timeout_is_counted_without_asking_the_router(self):
        self.serve()  # any request would pop from an empty list and fail the test
        gw.after_failure("gemma", TimeoutError("timed out"), streak=1)
        self.assertEqual(self.requests, [])

    def test_router_down_below_the_streak_continues(self):
        self.serve(urllib.error.URLError("connection refused"))
        gw.after_failure("gemma", urllib.error.URLError("refused"),
                         streak=gw.DEAD_SERVER_STREAK - 1)

    def test_router_down_at_the_streak_aborts_as_dead_server(self):
        self.serve(urllib.error.URLError("connection refused"),
                   urllib.error.URLError("connection refused"))
        with self.assertRaises(gw.DeadServer):
            gw.after_failure("gemma", urllib.error.URLError("refused"),
                             streak=gw.DEAD_SERVER_STREAK)

    def test_model_missing_from_the_listing_aborts_instead_of_guessing(self):
        self.serve(router_listing(None))
        with self.assertRaises(gw.ModelCrashed) as cm:
            gw.after_failure("gemma", http_500(), streak=1)
        self.assertIn("gemma", str(cm.exception))

    def test_abort_types_are_system_exits_so_runners_do_not_count_them(self):
        self.assertTrue(issubclass(gw.ModelCrashed, SystemExit))


class PositiveIntTests(unittest.TestCase):
    """--attempts 0 used to run nothing and write a summary reading "(all failed)"."""

    def test_one_is_accepted(self):
        self.assertEqual(gw.positive_int("1"), 1)

    def test_larger_values_are_accepted(self):
        self.assertEqual(gw.positive_int("12"), 12)

    def test_zero_is_rejected_with_a_clear_message(self):
        with self.assertRaises(argparse.ArgumentTypeError) as cm:
            gw.positive_int("0")
        self.assertIn("at least 1", str(cm.exception))

    def test_negative_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            gw.positive_int("-3")

    def test_non_integer_is_rejected(self):
        with self.assertRaises(argparse.ArgumentTypeError) as cm:
            gw.positive_int("two")
        self.assertIn("'two'", str(cm.exception))

    def test_argparse_turns_it_into_a_usage_error(self):
        ap = argparse.ArgumentParser(prog="run-x.py")
        ap.add_argument("--attempts", type=gw.positive_int, default=3)
        with mock.patch.object(sys, "stderr", io.StringIO()) as err, \
                self.assertRaises(SystemExit) as cm:
            ap.parse_args(["--attempts", "0"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("--attempts", err.getvalue())


if __name__ == "__main__":
    unittest.main()
