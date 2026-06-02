#!/usr/bin/env python3
"""
Speed benchmark: measure raw token throughput per model — NOT correctness.

Purpose: quantify the speed tradeoff of CPU-spillover models (e.g. qwen-big =
qwen3.6:27b, ~17 GB, runs ~⅔ on CPU on a 10 GB GPU) against the models that fit
on-GPU and we actually use daily (qwen-custom, granite-coder, gemma-content).

Deliberately small so a heavily-spilling model finishes in minutes, not all day:
a few short prompts, output capped via num_predict, 1 attempt each, thinking OFF
(thinking would balloon both token count and wall-clock, especially on CPU).

For each model it loads it once, captures the actual GPU/CPU split from
`ollama ps`, and times generation. Reports generation tok/s, prompt-eval tok/s,
load time, and a slowdown multiple vs the fastest model.

Usage:
  ./eval/run-speed.py                                  # default models + prompts
  ./eval/run-speed.py --models qwen-big qwen-custom
  ./eval/run-speed.py --num-predict 128 --attempts 1   # even faster
  ./eval/run-speed.py --timeout 900                    # for very slow spillover

Output:
  eval/runs/<UTC>/speed/summary.md
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ollama import (  # noqa: E402
    REPO_ROOT, generate, new_run_dir, prompt_tok_per_s, resolve_model, tok_per_s,
)

DEFAULT_OUT_ROOT = REPO_ROOT / "eval" / "runs"

# Spillover candidates first, then the three on-GPU models we use daily. All run
# thinking-OFF (no :think) — this is a throughput test, not a reasoning test.
DEFAULT_MODELS = ["qwen-custom", "granite-coder", "gemma-content"]

# Mixed-length prompts: a short one and a longer one, so we also exercise
# prompt-eval. Content is irrelevant — we measure throughput, and num_predict
# caps how much each model generates so the comparison is apples-to-apples.
PROMPTS = [
    "Write a Python function that reverses a singly linked list, with comments.",
    "Explain how a hash map handles collisions, then sketch one in Python.",
]

# Match "24 GB" and "69%/31% CPU/GPU" | "100% GPU" | "100% CPU" out of an
# `ollama ps` row.
_SIZE_RE = re.compile(r"\b(\d+(?:\.\d+)?\s*[KMG]B)\b")
_PROC_RE = re.compile(r"(\d+%(?:/\d+%)?\s+(?:CPU/GPU|GPU|CPU))")


def ps_info(model: str) -> tuple[str, str]:
    """Return (size, processor) for a loaded model from `ollama ps`, or ('','')."""
    try:
        out = subprocess.run(["ollama", "ps"], capture_output=True, text=True,
                             timeout=15).stdout
    except Exception:  # noqa: BLE001
        return "", ""
    base = model.split(":")[0]
    for line in out.splitlines():
        if line.split(" ", 1)[0].split(":")[0] == base:
            size = _SIZE_RE.search(line)
            proc = _PROC_RE.search(line)
            return (size.group(1) if size else "",
                    proc.group(1) if proc else "")
    return "", ""


def time_model(model: str, prompts: list[str], attempts: int, num_predict: int,
               timeout: int, extra_opts: dict | None = None) -> dict:
    name, think = resolve_model(model)
    opts = {"num_predict": num_predict, **(extra_opts or {})}
    gen_tps: list[float] = []
    prompt_tps: list[float] = []
    load_ms = 0.0
    size = proc = ""
    for pi, prompt in enumerate(prompts):
        for n in range(attempts):
            label = f"    p{pi+1} a{n+1}"
            print(f"{label:<10}", end="", flush=True)
            t0 = time.monotonic()
            try:
                _, meta = generate(name, prompt, timeout, think=think, options=opts)
            except Exception as e:  # noqa: BLE001
                print(f"GEN-FAIL ({time.monotonic()-t0:.1f}s): {e}")
                continue
            elapsed = time.monotonic() - t0
            g, p = tok_per_s(meta), prompt_tok_per_s(meta)
            gen_tps.append(g)
            prompt_tps.append(p)
            # First call loads the model; capture load time + the GPU/CPU split.
            if not size:
                load_ms = meta.get("load_duration", 0) / 1e6
                size, proc = ps_info(name)
            print(f"{elapsed:6.1f}s  gen {g:6.1f} tok/s  prompt {p:7.1f} tok/s")
    avg = lambda xs: sum(xs) / len(xs) if xs else 0.0  # noqa: E731
    return {"model": model, "gen_tps": avg(gen_tps), "prompt_tps": avg(prompt_tps),
            "load_ms": load_ms, "size": size, "proc": proc, "n": len(gen_tps)}


def write_summary(run_dir: Path, rows: list[dict], num_predict: int,
                  attempts: int, prompts: int, extra_opts: dict | None = None) -> None:
    ranked = sorted([r for r in rows if r["n"]], key=lambda r: -r["gen_tps"])
    top = ranked[0]["gen_tps"] if ranked else 0.0
    L = ["# Speed benchmark", "",
         f"- Prompts: {prompts} × {attempts} attempt(s), capped at "
         f"num_predict={num_predict}, thinking OFF."
         + (f" Extra opts: `{extra_opts}`." if extra_opts else ""),
         "- Metric = generation throughput (eval tok/s); slowdown is relative "
         "to the fastest model. GPU/CPU split from `ollama ps` at load.", ""]
    if ranked:
        f = ranked[0]
        L += [f"## 🏆 Fastest: `{f['model']}` — {f['gen_tps']:.0f} tok/s "
              f"({f['proc'] or 'n/a'})", ""]
    L += ["| Rank | Model | Gen tok/s | Slowdown | Prompt tok/s | Load | Size | GPU/CPU split |",
          "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(ranked, 1):
        slow = f"{top / r['gen_tps']:.0f}×" if r["gen_tps"] else "—"
        slow = "1.0× (fastest)" if i == 1 else slow
        L.append(f"| {i} | `{r['model']}` | {r['gen_tps']:.1f} | {slow} | "
                 f"{r['prompt_tps']:.0f} | {r['load_ms']/1000:.1f}s | "
                 f"{r['size'] or '?'} | {r['proc'] or '?'} |")
    (run_dir / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nSummary: {(run_dir / 'summary.md').relative_to(REPO_ROOT)}")
    if ranked:
        print(f"Fastest: {ranked[0]['model']} ({ranked[0]['gen_tps']:.0f} tok/s)")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--attempts", type=int, default=1, help="attempts per prompt (default 1)")
    ap.add_argument("--num-predict", type=int, default=200,
                    help="cap generated tokens per call (default 200)")
    ap.add_argument("--timeout", type=int, default=600, help="model call timeout (s)")
    ap.add_argument("--opt", action="append", default=[], metavar="KEY=VAL",
                    help="extra Ollama option, repeatable — e.g. --opt num_ctx=4096 "
                         "--opt num_thread=12. Overrides the model's Modelfile value "
                         "for this run only (forces a reload).")
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    args = ap.parse_args()

    extra_opts: dict = {}
    for kv in args.opt:
        k, _, v = kv.partition("=")
        extra_opts[k.strip()] = int(v) if v.strip().lstrip("-").isdigit() else v.strip()

    run_dir = new_run_dir(args.out_root) / "speed"
    run_dir.mkdir(parents=True)
    print(f"Run dir: {run_dir.relative_to(REPO_ROOT)}")
    print(f"Models:  {', '.join(args.models)}")
    print(f"Load:    {len(PROMPTS)} prompts × {args.attempts} × "
          f"{args.num_predict} tok cap, thinking off"
          f"{'  | opts: ' + str(extra_opts) if extra_opts else ''}\n")

    rows = []
    for model in args.models:
        print(f"=== {model} ===")
        rows.append(time_model(model, PROMPTS, args.attempts, args.num_predict,
                               args.timeout, extra_opts))
        r = rows[-1]
        print(f"  -> {r['gen_tps']:.1f} tok/s gen  ({r['proc'] or 'n/a'})\n")

    write_summary(run_dir, rows, args.num_predict, args.attempts, len(PROMPTS),
                  extra_opts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
