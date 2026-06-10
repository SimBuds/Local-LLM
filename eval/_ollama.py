"""
Shared plumbing for the eval runners (run-content.py = content, run-code.py = coding).
Stdlib-only; talks to the local Ollama HTTP API. No task scoring lives here —
each runner owns its own scorer and summary. This file is the transport plus the
helpers the runners share, including the uncertainty-reporting helpers (Wilson
confidence interval, small-sample caveats, close-result notes, per-task spread)
that keep small runs from being over-read. Those helpers are scoring-agnostic:
they format counts/rates a runner already computed, they don't decide pass/fail.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/generate"
REPO_ROOT = Path(__file__).resolve().parent.parent
# A ```lang fenced block (group 1 = body). Greedy-safe, handles missing lang.
FENCE_RE = re.compile(r"```[ \t]*([a-zA-Z0-9_+-]*)[ \t]*\n(.*?)```", re.DOTALL)
THOUGHT_RE = re.compile(r"<thought>.*?</thought>", re.DOTALL | re.IGNORECASE)


def resolve_model(spec: str) -> tuple[str, bool]:
    """Map a leaderboard spec to (ollama_name, think).

    A trailing `:think` selects thinking mode while keeping the real Ollama
    model name intact, so a model and its `:think` variant can be ranked as
    separate entries. Thinking is a runtime flag, not a Modelfile setting. The
    current lineup (gemma) doesn't support it and errors on `--think`,
    so don't tag them — the hook stays for re-evaluating thinking-capable bases.
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


def generate(model: str, prompt: str, timeout: int, think: bool = False,
             options: dict | None = None, fmt: dict | str | None = None,
             system: str | None = None) -> tuple[str, dict]:
    """Single non-streaming call. Returns (response_text, raw_meta).

    `model` is the real Ollama name (resolve a `:think` spec first). `think`
    toggles Qwen thinking mode; it is ignored by non-Qwen models. `options`, if
    given, is merged into the request as Ollama generate options (e.g.
    `{"num_predict": 256}` to cap output length) — used by run-speed.py to bound
    generation so CPU-spillover models finish quickly. `fmt` sets Ollama's
    `format` field for structured output: `"json"` for free JSON, or a JSON
    schema object for schema-constrained decode (mirrors how jobhunt's gateway
    constrains output) — used by run-json.py. `system` overrides the system
    prompt for this call.
    """
    body_obj = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": think,
    }
    if options:
        body_obj["options"] = options
    if fmt is not None:
        body_obj["format"] = fmt
    if system is not None:
        body_obj["system"] = system
    payload = json.dumps(body_obj).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("response", ""), body


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


def run_program(source: str, exec_timeout: int) -> tuple[bool, str]:
    """Execute `source` in a throwaway temp dir. Returns (passed, short_reason).

    SAFETY: runs model-generated code in a subprocess with a wall-clock timeout
    and a fresh CWD, but it is NOT containerized. Only run trusted models/tasks.
    """
    with tempfile.TemporaryDirectory() as td:
        prog = Path(td) / "sol.py"
        prog.write_text(source, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, str(prog)],
                cwd=td, capture_output=True, text=True, timeout=exec_timeout,
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
