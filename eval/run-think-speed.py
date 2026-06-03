#!/usr/bin/env python3
"""
Thinking-aware speed gate — for thinking models (e.g. qwen-moe:think) where the
plain run-speed.py is misleading.

Why this exists: run-speed.py runs thinking-OFF and caps num_predict=200, which
truncates a reasoning trace and never measures what actually hurts a thinking
model on a spilling box — the long trace emitted *before* the answer. This runner
runs thinking-ON, does NOT cap output (let trace + answer complete), and reports:

  - gen tok/s            (raw generation rate — the 15 tok/s GATE metric)
  - total wall seconds   (felt time until the full answer is in hand)
  - thinking vs answer   (token split — how much output is "overhead")
  - est. seconds thinking (gen_seconds × thinking-share — when the answer starts)

A model PASSES if gen tok/s >= the floor (default 15). The wall-clock and
thinking-share columns are the context: a model can pass the rate gate yet still
feel slow because it thinks for 1500 tokens before answering.

Usage:
  ./eval/run-think-speed.py                                  # qwen-moe:think
  ./eval/run-think-speed.py --models qwen-moe:think --floor 15
  ./eval/run-think-speed.py --num-predict 4096 --timeout 600

Output: eval/runs/<UTC>/think-speed/summary.md
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ollama import (  # noqa: E402
    REPO_ROOT, generate, new_run_dir, prompt_tok_per_s, resolve_model, tok_per_s,
)

DEFAULT_OUT_ROOT = REPO_ROOT / "eval" / "runs"
FLOOR_DEFAULT = 15.0

# Realistic prompts that genuinely trigger a reasoning trace (coding + reasoning),
# matching how a thinking driver would actually be used.
PROMPTS = [
    "Implement an LRU cache class in Python with O(1) get and put, then briefly "
    "explain the key design choices.",
    "Given a list of meeting time intervals, write a Python function that returns "
    "the minimum number of rooms required to hold all meetings, and walk through "
    "your reasoning.",
]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=["qwen-moe:think"])
    ap.add_argument("--floor", type=float, default=FLOOR_DEFAULT,
                    help="gen tok/s pass threshold (default 15)")
    ap.add_argument("--num-predict", type=int, default=8192,
                    help="output cap (default 8192 — generous so traces complete; "
                         "flagged if hit)")
    ap.add_argument("--timeout", type=int, default=600, help="per-call timeout (s)")
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    args = ap.parse_args()

    run_dir = new_run_dir(args.out_root) / "think-speed"
    run_dir.mkdir(parents=True)
    print(f"Run dir: {run_dir.relative_to(REPO_ROOT)}")
    print(f"Floor:   {args.floor:.0f} tok/s gen   | {len(PROMPTS)} prompts, "
          f"thinking ON, num_predict={args.num_predict}\n")

    rows: list[dict] = []
    for model in args.models:
        name, think = resolve_model(model)
        if not think:
            print(f"WARNING: {model} has no :think suffix — thinking is OFF for it.")
        print(f"=== {model} ===")
        gen_rates, walls, ttas, capped = [], [], [], False
        for i, prompt in enumerate(PROMPTS, 1):
            t0 = time.monotonic()
            try:
                text, meta = generate(name, prompt, args.timeout, think=think,
                                      options={"num_predict": args.num_predict})
            except Exception as e:  # noqa: BLE001
                print(f"  p{i}  GEN-FAIL ({time.monotonic()-t0:.0f}s): {e}")
                continue
            wall = time.monotonic() - t0
            gen = tok_per_s(meta)
            eval_count = meta.get("eval_count", 0) or 0
            gen_secs = (meta.get("eval_duration", 0) or 0) / 1e9
            thinking = meta.get("thinking") or ""
            answer = text or ""
            tchars, achars = len(thinking), len(answer)
            tot = tchars + achars or 1
            think_share = tchars / tot
            think_tokens = round(eval_count * think_share)
            tta = gen_secs * think_share  # est. seconds spent thinking before answer
            if eval_count >= args.num_predict:
                capped = True
            gen_rates.append(gen); walls.append(wall); ttas.append(tta)
            print(f"  p{i}  gen {gen:5.1f} tok/s | wall {wall:5.0f}s | "
                  f"{eval_count:5d} tok ({think_tokens} thinking) | "
                  f"~{tta:.0f}s thinking before answer"
                  + ("  [HIT num_predict CAP]" if eval_count >= args.num_predict else ""))
        if not gen_rates:
            continue
        avg = lambda xs: sum(xs) / len(xs)  # noqa: E731
        rows.append({
            "model": model, "gen": avg(gen_rates), "wall": avg(walls),
            "tta": avg(ttas), "capped": capped,
            "pass": avg(gen_rates) >= args.floor,
        })
        r = rows[-1]
        verdict = "PASS" if r["pass"] else "FAIL"
        print(f"  -> {r['gen']:.1f} tok/s gen  [{verdict} vs {args.floor:.0f} floor]"
              f"   avg wall {r['wall']:.0f}s\n")

    # summary.md
    L = ["# Thinking-aware speed gate", "",
         f"- Floor: **{args.floor:.0f} tok/s** gen rate (PASS/FAIL). Thinking ON, "
         f"{len(PROMPTS)} prompts, num_predict={args.num_predict}.",
         "- `gen tok/s` is the gate. `wall` = total time to the full answer. "
         "`~thinking` = est. seconds emitting the trace before the answer starts.", "",
         "| Model | Gen tok/s | Gate | Avg wall | ~Thinking before answer |",
         "|---|---|---|---|---|"]
    for r in rows:
        gate = "✅ PASS" if r["pass"] else "❌ FAIL"
        cap = " ⚠️ cap hit" if r["capped"] else ""
        L.append(f"| `{r['model']}` | {r['gen']:.1f} | {gate}{cap} | "
                 f"{r['wall']:.0f}s | {r['tta']:.0f}s |")
    (run_dir / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"Summary: {(run_dir / 'summary.md').relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
