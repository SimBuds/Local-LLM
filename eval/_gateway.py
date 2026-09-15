"""
Shared plumbing for the eval runners (run-content.py = content, run-code.py = coding).
Stdlib-only; talks to the local llama-server router started by `make serve`.
No task scoring lives here — each runner owns its own scorer and summary. This
file is the transport plus the
helpers the runners share, including the uncertainty-reporting helpers (Wilson
confidence interval, small-sample caveats, close-result notes, per-task spread)
that keep small runs from being over-read. Those helpers are scoring-agnostic:
they format counts/rates a runner already computed, they don't decide pass/fail.
"""

from __future__ import annotations

import http.client
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LLM_URL = os.environ.get("LLM_URL", "http://localhost:8080").rstrip("/")
CHAT_URL = f"{LLM_URL}/v1/chat/completions"
MODELS_URL = f"{LLM_URL}/models"
REPO_ROOT = Path(__file__).resolve().parent.parent
# Built by `make build`: models/<name>/prompt.txt is the assembled prompt stack.
MODELS_DIR = REPO_ROOT / "models"
# Prompt caching reuses KV from the previous request, and the server docs warn the
# logits are then not bit-identical to an uncached run. Reproducibility is already
# the weak spot here (see TESTING.md, Reproducibility), so every call re-processes
# its whole prompt. Costs the ~3k-token system prompt on each call; flip this to
# trade determinism for speed.
CACHE_PROMPT = False
# Ollama option names the runners use, mapped to llama-server request fields.
OPTION_FIELDS = {
    "num_predict": "max_tokens",
    "seed": "seed",
    "temperature": "temperature",
    "top_p": "top_p",
    "top_k": "top_k",
    "min_p": "min_p",
    "repeat_penalty": "repeat_penalty",
    "presence_penalty": "presence_penalty",
}
# A ```lang fenced block (group 1 = body). Greedy-safe, handles missing lang.
FENCE_RE = re.compile(r"```[ \t]*([a-zA-Z0-9_+-]*)[ \t]*\n(.*?)```", re.DOTALL)
THOUGHT_RE = re.compile(r"<thought>.*?</thought>", re.DOTALL | re.IGNORECASE)


def resolve_model(spec: str) -> tuple[str, bool]:
    """Map a leaderboard spec to (model_name, think).

    A trailing `:think` selects thinking mode while keeping the real router
    model name intact, so a model and its `:think` variant can be ranked as
    separate entries. Thinking is a runtime flag, not a Modelfile setting. The
    current bases (gemma4, qwen3.6) both support it; runners default it off for
    deterministic, faster scoring, so tag a spec `:think` only when you want the
    thinking variant ranked separately.
    """
    if spec.endswith(":think"):
        return spec[: -len(":think")], True
    return spec, False


def get_effective_think(mode: str, model_default: bool) -> bool:
    """Determine if thinking should be enabled based on CLI flag and model default."""
    if mode == "on":
        return True
    if mode == "off":
        return False
    return model_default


def _map_options(options: dict | None) -> dict:
    """Translate the runners' Ollama-style option names into request fields.

    Raises ValueError rather than dropping what it cannot send. `num_ctx` is the
    important case: llama.cpp fixes context size when the model loads (it lives
    in the preset), so a silently ignored per-call `num_ctx` would let a long
    prompt truncate while the caller believes it asked for room.
    """
    fields = {}
    for key, value in (options or {}).items():
        if key == "num_ctx":
            raise ValueError(
                "num_ctx cannot be set per request on llama-server; context size "
                "is fixed at load time by ctx-size in the model's preset")
        if key not in OPTION_FIELDS:
            raise ValueError(f"unsupported generate option {key!r}; "
                             f"known: {', '.join(sorted(OPTION_FIELDS))}")
        fields[OPTION_FIELDS[key]] = value
    return fields


def _normalize_meta(body: dict) -> dict:
    """Express llama-server `timings` in the Ollama meta keys the runners read.

    Counts are tokens and durations nanoseconds, as Ollama reported them, so
    tok_per_s() and every runner's `meta.get("eval_count")` work unchanged. A
    response without `timings` yields zeros. `load_duration` is left out rather
    than invented: the chat response does not report it. The full body stays
    available under `raw`.
    """
    t = body.get("timings") or {}
    return {
        "eval_count": t.get("predicted_n", 0),
        "eval_duration": t.get("predicted_ms", 0) * 1e6,
        "prompt_eval_count": t.get("prompt_n", 0),
        "prompt_eval_duration": t.get("prompt_ms", 0) * 1e6,
        "raw": body,
    }


def generate(model: str, prompt: str, timeout: int, think: bool = False,
             options: dict | None = None, fmt: dict | str | None = None,
             system: str | None = None) -> tuple[str, dict]:
    """Single non-streaming call. Returns (response_text, meta).

    `model` is the router model name (resolve a `:think` spec first). `think`
    sets the chat template's `enable_thinking`; thoughts come back separately
    from the answer, so the returned text is the answer only. `options` takes
    Ollama-style names (e.g. `{"num_predict": 256}` to cap output length), see
    _map_options(). `fmt` requests structured output: `"json"` for free JSON, or
    a JSON schema object for schema-constrained decode (mirrors how jobhunt's
    gateway constrains output) — used by run-json.py. `system` replaces the
    built prompt stack for this call (persona baseline mode); otherwise the
    stack is read from models/<model>/prompt.txt, and a missing file raises
    instead of silently sending no stack.
    """
    fields = _map_options(options)
    if system is None:
        system = (MODELS_DIR / model / "prompt.txt").read_text(encoding="utf-8")
    body_obj = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "cache_prompt": CACHE_PROMPT,
        "chat_template_kwargs": {"enable_thinking": think},
        **fields,
    }
    if isinstance(fmt, dict):
        # Nested OpenAI shape. The top-level `"schema"` shape in the server README
        # was accepted but ignored on build 10968 (decode came back "{}").
        body_obj["response_format"] = {
            "type": "json_schema", "json_schema": {"name": "output", "schema": fmt}}
    elif fmt == "json":
        body_obj["response_format"] = {"type": "json_object"}
    payload = json.dumps(body_obj).encode("utf-8")
    req = urllib.request.Request(
        CHAT_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (ConnectionError, http.client.HTTPException) as e:
        # A server that dies mid-request raises RemoteDisconnected or a reset, not
        # URLError, and several runners catch only (URLError, TimeoutError): the
        # run crashed instead of counting a connection failure toward
        # check_alive(). TimeoutError and HTTPError are neither class, so a slow
        # model and a rejected request keep their own classification.
        raise urllib.error.URLError(e) from e
    choices = body.get("choices") or [{}]
    text = (choices[0].get("message") or {}).get("content") or ""
    return text, _normalize_meta(body)


# --- server liveness ----------------------------------------------------------
# Every runner catches per-call errors and records the attempt as a failure, which
# is right for a model that times out on one prompt and wrong for a server that is
# simply down: the run completes, exits 0, and writes a summary reporting that
# every model scored 0. That happened on 2026-07-28 when `systemctl restart ollama`
# landed mid-run and three suites produced authoritative-looking zeros. A summary
# that cannot tell "the model failed" from "nothing was listening" is worse than no
# summary, so the runners now preflight the server and abort once it is clearly
# gone rather than grinding out a fiction.

# Consecutive connection-level failures before a runner gives up. Above 1 so a
# single dropped socket or a model reload stall doesn't kill a long run.
DEAD_SERVER_STREAK = 5


def server_up(timeout: int = 5) -> bool:
    """True if the llama-server router answers. Used for preflight and abort checks."""
    try:
        with urllib.request.urlopen(MODELS_URL, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001
        return False


def preflight(models: list[str]) -> None:
    """Abort before generating if the server is down or a model is not servable.

    A model must be in the router's preset and have a built prompt stack.
    Mirrors build-common.sh's GGUF preflight: fail loudly and early rather than
    leaving artifacts that look like a completed run.
    """
    if not server_up():
        raise SystemExit(
            f"ERROR: no llama-server router at {LLM_URL}.\n"
            f"  Start it:  make serve")
    try:
        with urllib.request.urlopen(MODELS_URL, timeout=10) as resp:
            have = {m.get("id", "") for m in
                    json.loads(resp.read().decode("utf-8")).get("data", [])}
    except Exception:  # noqa: BLE001
        return  # server answered once; don't block a run on a flaky model listing
    names = [resolve_model(s)[0] for s in models]
    missing = [m for m in names if m not in have]
    if missing:
        raise SystemExit(
            f"ERROR: model(s) not in the router preset: {', '.join(missing)}\n"
            f"  Build them:  make build, then restart make serve\n"
            f"  Available:   {', '.join(sorted(have)) or '(none)'}")
    unbuilt = [m for m in names if not (MODELS_DIR / m / "prompt.txt").is_file()]
    if unbuilt:
        raise SystemExit(
            f"ERROR: no built prompt stack (models/<name>/prompt.txt) for: "
            f"{', '.join(unbuilt)}\n"
            f"  Build them:  make build")


class DeadServer(SystemExit):
    """Raised when consecutive connection failures mean the server is gone."""


# --- router model control -----------------------------------------------------
# run-speed.py reports load time. The router loads a model on its first request,
# so timing that request would read near zero whenever the model was already
# resident from an earlier run — a fake win. load_model() always unloads first and
# times an explicit load instead. The router's load call returns immediately; the
# model's status then moves loading -> loaded, so the time comes from polling.

LOAD_POLL_S = 0.1


class LoadFailed(RuntimeError):
    """The router could not load a model (not in the preset, or the load died)."""


def _router_status(model: str) -> dict:
    with urllib.request.urlopen(MODELS_URL, timeout=10) as resp:
        listing = json.loads(resp.read().decode("utf-8")).get("data", [])
    for entry in listing:
        if entry.get("id") == model:
            return entry.get("status") or {}
    raise LoadFailed(f"model {model!r} is not in the router preset; "
                     f"available: {', '.join(sorted(e.get('id', '') for e in listing))}")


def _router_post(path: str, model: str) -> None:
    req = urllib.request.Request(
        f"{LLM_URL}{path}", data=json.dumps({"model": model}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def _wait_for(model: str, value: str, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        status = _router_status(model)
        if status.get("failed"):
            raise LoadFailed(f"router failed to load {model!r} "
                             f"(exit code {status.get('exit_code')})")
        if status.get("value") == value:
            return status
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{model!r} not {value} after {timeout}s "
                               f"(last status {status.get('value')!r})")
        time.sleep(LOAD_POLL_S)


def load_model(model: str, timeout: int) -> tuple[float, list[str]]:
    """Cold-load `model` in the router. Returns (seconds to loaded, server args).

    The model is unloaded first if it is resident, and that wait is not counted.
    The args are the resolved llama-server command line the router started, which
    is where the preset's declared offload can be read back from.
    """
    if _router_status(model).get("value") != "unloaded":
        _router_post("/models/unload", model)
        _wait_for(model, "unloaded", timeout)
    t0 = time.monotonic()
    _router_post("/models/load", model)
    status = _wait_for(model, "loaded", timeout)
    return time.monotonic() - t0, status.get("args", [])


def served_ctx(model: str, timeout: int = 300) -> int:
    """Context size (n_ctx) the router is actually serving for `model`.

    llama.cpp fixes context at load time, so callers that need a minimum context
    check it here instead of requesting one per call. The router loads the model
    to answer, which is why the timeout is a load-sized one.
    """
    url = f"{LLM_URL}/props?model={urllib.parse.quote(model)}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        props = json.loads(resp.read().decode("utf-8"))
    n_ctx = (props.get("default_generation_settings") or {}).get("n_ctx")
    if not isinstance(n_ctx, int):
        raise LoadFailed(f"router /props for {model!r} reported no n_ctx")
    return n_ctx


def check_alive(streak: int) -> None:
    """Abort the run when `streak` consecutive calls have failed to connect.

    Called by runners from their per-attempt exception handler. A model that is
    merely slow raises TimeoutError against a live server, so this only fires when
    the server itself stops answering.
    """
    if streak >= DEAD_SERVER_STREAK and not server_up():
        raise DeadServer(
            f"ERROR: aborting — {streak} consecutive connection failures and the "
            f"llama-server router at {LLM_URL} is not responding.\n"
            f"  Nothing was written for this run; results so far would have been "
            f"all-zero and misleading.\n"
            f"  Restart it:  make serve")


def tok_per_s(meta: dict) -> float:
    n = meta.get("eval_count", 0)
    return n / (meta.get("eval_duration", 1) / 1e9) if n else 0.0


def prompt_tok_per_s(meta: dict) -> float:
    n = meta.get("prompt_eval_count", 0)
    d = meta.get("prompt_eval_duration", 0)
    return n / (d / 1e9) if n and d else 0.0


def new_run_dir(out_root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_root / stamp
    n = 1
    while run_dir.exists():  # back-to-back runs can share a second (e.g. ctx sweeps)
        run_dir = out_root / f"{stamp}-{n}"
        n += 1
    run_dir.mkdir(parents=True)
    return run_dir


def rel_path(p: Path) -> str:
    """Repo-relative path for display, falling back to the absolute path.

    `Path.relative_to` raises rather than degrading when the target is outside
    REPO_ROOT — which `--out-root /tmp/...` legitimately is. Every runner prints
    its run dir this way, so a bare `relative_to` turns a supported flag into a
    crash.
    """
    try:
        return str(p.relative_to(REPO_ROOT))
    except ValueError:
        return str(p)


def seed_opts(seed: int | None, base: dict | None = None) -> dict | None:
    """Merge `seed` into generate options, or return `base` unchanged.

    llama-server uses `seed` as the sampling seed, which is what lets a run be
    replayed; how far that holds across server restarts is tracked in TESTING.md
    under Reproducibility. Returns None when there is nothing to send, so callers
    can pass the result straight through to `generate(options=...)`.
    """
    opts = dict(base or {})
    if seed is not None:
        opts["seed"] = seed
    return opts or None


def attempt_seed(seed: int | None, attempt: int) -> int | None:
    """Derive a per-attempt seed from the run seed.

    A single fixed seed reused across attempts makes every attempt of a task
    byte-identical, so an N-attempt pass rate would report one sample as though
    it were N — the confidence intervals would be fiction. Offsetting by the
    attempt number keeps the whole run reproducible while preserving the
    between-attempt variance these suites are trying to measure.
    """
    return None if seed is None else seed + attempt


def add_seed_arg(ap) -> None:
    """Register the shared --seed flag on a runner's ArgumentParser."""
    ap.add_argument("--seed", type=int, default=None,
                    help="fix the decode seed so the run is reproducible "
                         "(default: unset, so the server samples freshly each call)")


def extract_code(text: str, prefer_lang: str = "python") -> str:
    """
    Pull source out of a model response. Prefers a fenced block tagged with
    `prefer_lang`, then any fenced block, then (last resort) the raw text —
    a model that ignored the 'code only' instruction still gets executed.
    """
    # Strip thinking traces which often leak into the response text
    clean_text = THOUGHT_RE.sub("", text).strip()

    blocks = FENCE_RE.findall(clean_text)
    if blocks:
        for lang, body in blocks:
            if lang.lower() == prefer_lang:
                return body.strip("\n")
        return blocks[0][1].strip("\n")  # first fenced block, whatever the tag

    # If no fences, use the text stripped of thought blocks
    # Note: We return clean_text even if it's empty to allow the runner
    # to catch 'no-code' failures properly.
    return clean_text


# --- sandboxing ---------------------------------------------------------------
# run-code/run-learn/run-tutor execute model-generated Python. Where bubblewrap is
# available we confine it: read-only /usr, no network, no host PID/IPC namespace,
# no access to $HOME, and write access only to the throwaway CWD. Without bwrap we
# fall back to a bare subprocess — the historical behavior, which has a timeout and
# a fresh CWD but nothing stopping the code reading ~/.ssh or phoning home. The
# fallback is reported by sandbox_note() rather than assumed, because "we ran it
# unsandboxed" must never be silent.

BWRAP = shutil.which("bwrap")
_SANDBOX_OK: bool | None = None


def _bwrap_argv(workdir: str) -> list[str]:
    """bwrap prefix confining a program to `workdir`."""
    argv = [
        BWRAP,
        "--ro-bind", "/usr", "/usr",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",          # before the bind below, so the bind lands inside it
        "--bind", workdir, workdir,
        "--chdir", workdir,
        "--unshare-all",            # no network, no host PID/IPC/UTS
        "--new-session",            # no terminal injection back into the runner
        "--die-with-parent",
    ]
    # Mirror the host's top-level layout: merged-/usr systems (Arch) have these as
    # symlinks into /usr; split layouts need real read-only binds.
    for top in ("/lib", "/lib64", "/bin", "/sbin"):
        p = Path(top)
        if p.is_symlink():
            argv += ["--symlink", os.readlink(top), top]
        elif p.is_dir():
            argv += ["--ro-bind", top, top]
    # A venv/uv interpreter lives outside /usr and needs its own bind.
    base = sys.base_prefix
    if not base.startswith("/usr"):
        argv += ["--ro-bind", base, base]
    for extra in ("/etc/ld.so.cache", "/etc/ld.so.conf"):
        if Path(extra).exists():
            argv += ["--ro-bind", extra, extra]
    return argv


def _sandbox_works() -> bool:
    """Probe bwrap once with a trivial program, caching the result.

    Necessary because a bwrap that cannot set up its namespaces (unprivileged
    userns disabled, for instance) exits nonzero — which would otherwise be
    misread as every model's code failing.
    """
    global _SANDBOX_OK
    if _SANDBOX_OK is not None:
        return _SANDBOX_OK
    _SANDBOX_OK = False
    if BWRAP:
        try:
            with tempfile.TemporaryDirectory() as td:
                proc = subprocess.run(
                    _bwrap_argv(td) + [sys.executable, "-c", "print('ok')"],
                    cwd=td, capture_output=True, text=True, timeout=30,
                )
            _SANDBOX_OK = proc.returncode == 0 and proc.stdout.strip() == "ok"
        except Exception:  # noqa: BLE001
            _SANDBOX_OK = False
    return _SANDBOX_OK


def sandbox_note() -> str:
    """One-line description of how model code will be executed, for run banners."""
    if _sandbox_works():
        return "Sandbox: bwrap (read-only /usr, no network, no $HOME, writable CWD only)"
    if BWRAP:
        return ("Sandbox: NONE — bwrap found but failed to start; model code runs "
                "with your user's full access. Trusted models only.")
    return ("Sandbox: NONE — bwrap not installed; model code runs with your user's "
            "full access. Trusted models only.")


def run_program(source: str, exec_timeout: int) -> tuple[bool, str]:
    """Execute `source` in a throwaway temp dir. Returns (passed, short_reason).

    SAFETY: model-generated code is confined by bubblewrap when it is available
    (see sandbox_note()) — read-only /usr, no network, no $HOME, writable CWD
    only. When bwrap is unavailable this degrades to a bare subprocess with just a
    wall-clock timeout and a fresh CWD, which is NOT isolation: run trusted
    models/tasks only. Runners print sandbox_note() so the active mode is on the
    record for every run.
    """
    with tempfile.TemporaryDirectory() as td:
        prog = Path(td) / "sol.py"
        prog.write_text(source, encoding="utf-8")
        argv = [sys.executable, str(prog)]
        if _sandbox_works():
            argv = _bwrap_argv(td) + argv
        try:
            proc = subprocess.run(
                argv, cwd=td, capture_output=True, text=True, timeout=exec_timeout,
            )
        except subprocess.TimeoutExpired:
            return False, "timeout"
    if proc.returncode == 0:
        return True, "pass"
    err = (proc.stderr or proc.stdout).strip().splitlines()
    last = err[-1] if err else f"exit {proc.returncode}"
    if "AssertionError" in last:
        return False, "wrong-answer"
    if "SyntaxError" in last:
        return False, "syntax"
    return False, last[:60]


# --- uncertainty reporting ----------------------------------------------------
# These keep small personal runs from being over-read. The default attempt
# counts here (3–5/task) are tiny: at n=5 a single flipped attempt moves the
# rate 20 points, so a bare "80% vs 100%" headline invites false confidence.
# The helpers below attach a confidence interval, a small-sample flag, and a
# "this was basically a tie" note so the summaries report uncertainty instead of
# hiding it. They format numbers the runner already computed; they do not score.

SMALL_SAMPLE_N = 10  # below this, one attempt swings the rate enough to mislead


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion k/n (z=1.96).

    Returns (low, high) as fractions in [0, 1]. n == 0 returns (0.0, 1.0): no
    data, no constraint. Wilson is used instead of the normal approximation
    because these runs live at small n and extreme p (often 0% or 100%), where
    the normal interval collapses to zero width and lies about certainty.
    """
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = (z * math.sqrt((p * (1 - p) + z2 / (4 * n)) / n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def ci_str(k: int, n: int) -> str:
    """Wilson 95% CI as a compact percent range, e.g. '44–100%' (em dash '—' if n==0)."""
    if n <= 0:
        return "—"
    lo, hi = wilson_interval(k, n)
    return f"{lo*100:.0f}–{hi*100:.0f}%"


def sample_caveat(n: int, threshold: int = SMALL_SAMPLE_N) -> str:
    """Short warning label for an untrustworthy sample size, else ''."""
    if n <= 0:
        return "⚠ no data"
    if n < threshold:
        return f"⚠ small n={n}"
    return ""


def close_call_note(top: float, runner_up: float | None, threshold: float,
                    margin_str: str) -> str:
    """Note when the winner's margin over the runner-up is within `threshold`.

    `top`/`runner_up`/`threshold` are in whatever units the metric uses (0–1
    fractions, /10 scores, …); `margin_str` is the caller-formatted margin for
    display. Returns '' when there is no runner-up or the gap is real. The point
    is to stop a noise-sized lead from reading as a quality win — close results
    should break on speed and other signals, not the headline metric.
    """
    if runner_up is None or (top - runner_up) > threshold:
        return ""
    lead = (f"is tied with the runner-up" if (top - runner_up) < 1e-9
            else f"leads by only {margin_str}")
    return (f"⚠ **Close result:** winner {lead}, within the tie threshold — "
            f"treat the top entries as tied and break on speed or other signals, "
            f"not this metric.")


def spread_note(rates: dict[str, float], scale: float = 100.0, suffix: str = "%") -> str:
    """One-line per-task reliability note: weakest task + min–max spread.

    `rates` maps task name -> rate (any scale; `scale`/`suffix` control display,
    default percent). Surfaces the worst task a model-level average hides — a
    model that is 100% on two tasks and 40% on a third is not a 'mostly 100%'
    model for the thing it is bad at. Returns '' if there is nothing to compare.
    """
    items = [(k, v) for k, v in rates.items() if v is not None]
    if not items:
        return ""
    wk, wv = min(items, key=lambda kv: kv[1])
    vals = [v for _, v in items]
    note = f"weakest `{wk}` at {wv*scale:.0f}{suffix}"
    if len(items) > 1:
        note += f"; spread {min(vals)*scale:.0f}–{max(vals)*scale:.0f}{suffix}"
    return note
