#!/usr/bin/env python3
"""
Speed benchmark: measure raw token throughput per model — NOT correctness.

Purpose: quantify the speed tradeoff of CPU-spillover models (e.g. qwen =
qwen3.6:35b-a3b-mtp-q4_K_M, ~22 GB, spills heavily to CPU on a 10 GB GPU)
against the models that fit on-GPU and we actually use daily (gemma).

Allows testing throughput with thinking ON or OFF to evaluate its impact on 
wall-clock and token generation rates across different architectures.

For each model it cold-loads it once through the llama-server router, reads back
the preset's declared GPU/CPU split and the model process's VRAM, and times
generation. Reports generation tok/s, prompt-eval tok/s, load time, and a
slowdown multiple vs the fastest model.

Usage:
  ./eval/run-speed.py                                  # default models + prompts
  ./eval/run-speed.py --models gemma qwen lite
  ./eval/run-speed.py --thinking on                    # force enable thinking
  ./eval/run-speed.py --num-predict 128 --attempts 1   # even faster
  ./eval/run-speed.py --timeout 900                    # for very slow spillover

Output:
  eval/runs/<UTC>/speed/summary.md
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ollama import (  # noqa: E402
    REPO_ROOT, add_seed_arg, generate, get_effective_think, load_model, new_run_dir,
    prompt_tok_per_s, rel_path, resolve_model, tok_per_s,
)

DEFAULT_OUT_ROOT = REPO_ROOT / "eval" / "runs"

# Mixed-length prompts: a short one and a longer one, so we also exercise
# prompt-eval. Content is irrelevant — we measure throughput, and num_predict
# caps how much each model generates so the comparison is apples-to-apples.
PROMPTS = [
    "Write a Python function that reverses a singly linked list, with comments.",
    "Explain how a hash map handles collisions, then sketch one in Python.",
]

def _arg(args: list[str], flag: str) -> str | None:
    """Value following `flag` in a llama-server argv, or None."""
    return args[args.index(flag) + 1] if flag in args[:-1] else None


def declared_offload(args: list[str]) -> str:
    """GPU/CPU split as the preset declares it, read from the router's argv.

    The presets pin the split with `fit = off`, so the declared value is what
    actually loaded — unlike Ollama, which picked a split and reported a percent.
    """
    moe = int(_arg(args, "--n-cpu-moe") or 0)
    split = f"GPU + {moe} MoE layers on CPU" if moe else "all GPU"
    if _arg(args, "--spec-type") == "draft-mtp":
        split += ", MTP"
    return split


def gguf_size(args: list[str]) -> str:
    """On-disk size of the model file named by `--model`, or '?'."""
    path = _arg(args, "--model")
    try:
        return f"{Path(path).stat().st_size / 2**30:.1f} GiB" if path else "?"
    except OSError:
        return "?"


def gpu_mib() -> int | None:
    """VRAM held by llama-server processes, in MiB. None when it cannot be read.

    Summed per process rather than read off the card total, so desktop apps
    sharing the GPU do not inflate the number. None instead of 0 when there is no
    llama-server row, because 0 would read as "used no VRAM".
    """
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    used = [int(cols[2]) for cols in (line.split(", ") for line in proc.stdout.splitlines())
            if len(cols) == 3 and Path(cols[1]).name == "llama-server"]
    return sum(used) if used else None


def time_model(model: str, prompts: list[str], attempts: int, num_predict: int,
               timeout: int, thinking_mode: str, extra_opts: dict | None = None) -> dict:
    name, model_think = resolve_model(model)

    think = get_effective_think(thinking_mode, model_think)

    opts = {"num_predict": num_predict, **(extra_opts or {})}
    gen_tps: list[float] = []
    prompt_tps: list[float] = []
    load_s, args = load_model(name, timeout)
    print(f"    loaded in {load_s:.1f}s ({declared_offload(args)})")
    vram = None
    for pi, prompt in enumerate(prompts):
        for n in range(attempts):
            label = f"    p{pi+1} a{n+1}"
            print(f"{label:<10}", end="", flush=True)
            t0 = time.monotonic()
            try:
                _, meta = generate(name, prompt, timeout, think=think, options=opts)
            except ValueError:
                raise  # a bad --opt is a usage error, not a slow model: abort the run
            except Exception as e:  # noqa: BLE001
                print(f"GEN-FAIL ({time.monotonic()-t0:.1f}s): {e}")
                continue
            elapsed = time.monotonic() - t0
            g, p = tok_per_s(meta), prompt_tok_per_s(meta)
            gen_tps.append(g)
            prompt_tps.append(p)
            # Compute buffers are allocated on the first decode, so VRAM is read
            # after a generation rather than straight after the load.
            if vram is None:
                vram = gpu_mib()
            print(f"{elapsed:6.1f}s  gen {g:6.1f} tok/s  prompt {p:7.1f} tok/s")
    avg = lambda xs: sum(xs) / len(xs) if xs else 0.0  # noqa: E731
    return {"model": model, "thinking": think, "gen_tps": avg(gen_tps), "prompt_tps": avg(prompt_tps),
            "load_ms": load_s * 1000, "size": gguf_size(args), "vram": vram,
            "proc": declared_offload(args), "n": len(gen_tps)}


def write_summary(run_dir: Path, rows: list[dict], num_predict: int,
                  attempts: int, prompts: int, extra_opts: dict | None = None) -> None:
    ranked = sorted([r for r in rows if r["n"]], key=lambda r: -r["gen_tps"])
    top = ranked[0]["gen_tps"] if ranked else 0.0
    L = ["# Speed benchmark", "",
         f"- Prompts: {prompts} × {attempts} attempt(s), capped at "
         f"num_predict={num_predict}."
         + (f" Extra opts: `{extra_opts}`." if extra_opts else ""),
         "- Metric = generation throughput (eval tok/s); slowdown is relative "
         "to the fastest model.",
         "- Load = cold load through the router (model unloaded first; the OS page "
         "cache may still hold the file). Size = GGUF on disk. VRAM = llama-server "
         "process memory from nvidia-smi after the first generation. GPU/CPU split = "
         "the preset's pinned offload (`fit = off`).", ""]
    if ranked:
        f = ranked[0]
        think_tag = "thinking ON" if f.get("thinking") else "thinking OFF"
        L += [f"## 🏆 Fastest: `{f['model']}` — {f['gen_tps']:.0f} tok/s "
              f"({f['proc'] or 'n/a'}, {think_tag})", ""]
    L += ["| Rank | Model | Think | Gen tok/s | Slowdown | Prompt tok/s | Load | Size | VRAM | GPU/CPU split |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(ranked, 1):
        slow = f"{top / r['gen_tps']:.0f}×" if r["gen_tps"] else "—"
        slow = "1.0× (fastest)" if i == 1 else slow
        think_str = "ON" if r.get("thinking") else "OFF"
        vram = f"{r['vram'] / 1024:.1f} GiB" if r.get("vram") is not None else "?"
        L.append(f"| {i} | `{r['model']}` | {think_str} | {r['gen_tps']:.1f} | {slow} | "
                 f"{r['prompt_tps']:.0f} | {r['load_ms']/1000:.1f}s | "
                 f"{r['size'] or '?'} | {vram} | {r['proc'] or '?'} |")
    (run_dir / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nSummary: {rel_path(run_dir / 'summary.md')}")
    if ranked:
        print(f"Fastest: {ranked[0]['model']} ({ranked[0]['gen_tps']:.0f} tok/s)")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", required=True, help="router model names")
    ap.add_argument("--attempts", type=int, default=1, help="attempts per prompt (default 1)")
    ap.add_argument("--num-predict", type=int, default=200,
                    help="cap generated tokens per call (default 200)")
    ap.add_argument("--timeout", type=int, default=600, help="model call timeout (s)")
    ap.add_argument("--thinking", choices=["auto", "on", "off"], default="auto",
                    help="Thinking mode: 'auto' respects suffix configuration, 'on' forces thinking tokens, 'off' strips thinking passes.")
    ap.add_argument("--opt", action="append", default=[], metavar="KEY=VAL",
                    help="extra per-request option, repeatable — e.g. --opt temperature=0 "
                         "--opt top_k=20. Overrides the preset's default for this run "
                         "only. Load-time settings such as num_ctx live in the preset "
                         "and are rejected here.")
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    add_seed_arg(ap)
    args = ap.parse_args()

    extra_opts: dict = {}
    for kv in args.opt:
        k, _, v = kv.partition("=")
        extra_opts[k.strip()] = int(v) if v.strip().lstrip("-").isdigit() else v.strip()
    if args.seed is not None:
        extra_opts.setdefault("seed", args.seed)  # explicit --opt seed=N still wins

    run_dir = new_run_dir(args.out_root) / "speed"
    run_dir.mkdir(parents=True)
    print(f"Run dir: {rel_path(run_dir)}")
    print(f"Models:  {', '.join(args.models)}")
    print(f"Load:    {len(PROMPTS)} prompts × {args.attempts} × "
          f"{args.num_predict} tok cap, thinking mode: {args.thinking.upper()}"
          f"{'  | opts: ' + str(extra_opts) if extra_opts else ''}\n")

    rows = []
    for model in args.models:
        print(f"=== {model} ===")
        rows.append(time_model(model, PROMPTS, args.attempts, args.num_predict,
                               args.timeout, args.thinking, extra_opts))
        r = rows[-1]
        think_status = "thinking ON" if r['thinking'] else "thinking OFF"
        print(f"  -> {r['gen_tps']:.1f} tok/s gen  ({r['proc'] or 'n/a'}, {think_status})\n")

    write_summary(run_dir, rows, args.num_predict, args.attempts, len(PROMPTS),
                  extra_opts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
