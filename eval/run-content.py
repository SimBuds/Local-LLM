#!/usr/bin/env python3
"""
Content benchmark: drive a battery of short writing prompts (eval/content_tasks.py)
against each custom model, N attempts per task, and score format +
instruction-following with cheap deterministic regex signals (no LLM judge). Each
task owns its own scorer; an attempt is "clean" iff it obeys every rule that task
checks. The summary ranks models by overall clean rate (tie-break: speed),
reports a per-task matrix, and declares a winner.

Usage:
  ./eval/run-content.py --models gemma qwen          # all content tasks
  ./eval/run-content.py --models gemma --tasks seo_product
  ./eval/run-content.py --models gemma --attempts 3
  ./eval/run-content.py --models gemma --prompt-file eval/prompts/x.md  # ad-hoc SEO prompt

Output:
  eval/runs/<UTC>/content/
    summary.md
    <model>/<task>-attempt-<n>.md

For the coding benchmark, see run-code.py.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ollama import (  # noqa: E402
    REPO_ROOT, ci_str, close_call_note, generate, get_effective_think,
    new_run_dir, resolve_model, sample_caveat, spread_note, tok_per_s,
)
from content_tasks import TASKS, ContentTask, seo_task_from_prompt  # noqa: E402

DEFAULT_OUT_ROOT = REPO_ROOT / "eval" / "runs"
CLOSE_PTS = 0.05  # clean-rate gaps within 5 points are a tie, not a quality win


def run_attempt(model: str, task: ContentTask, n: int, total: int, timeout: int,
                thinking_mode: str) -> dict:
    print(f"    {task.key:14s} [{n}/{total}] ", end="", flush=True)
    name, model_think = resolve_model(model)
    think = get_effective_think(thinking_mode, model_think)

    t0 = time.monotonic()
    try:
        text, meta = generate(name, task.prompt, timeout, think=think)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"FAIL ({time.monotonic()-t0:.1f}s): {e}")
        return {"ok": False, "error": str(e), "elapsed_s": time.monotonic() - t0}
    elapsed = time.monotonic() - t0
    s = task.evaluate(text)
    print(f"{'ok ' if s['clean'] else 'OK*'}  {elapsed:5.1f}s  "
          f"{tok_per_s(meta):5.1f} tok/s  {s['words']:4d} words  [{s['flags']}]")
    return {"ok": True, "task": task.key, "clean": s["clean"],
            "words": s["words"], "elapsed_s": elapsed,
            "tok_per_s": tok_per_s(meta), "text": text}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", required=True, help="Ollama model names")
    ap.add_argument("--attempts", type=int, default=5)
    ap.add_argument("--tasks", nargs="+", default=None,
                    help=f"subset of: {', '.join(TASKS)} (default: all)")
    ap.add_argument("--prompt-file", type=Path, default=None,
                    help="ad-hoc mode: score a single SEO-style prompt from this file")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--thinking", choices=["auto", "on", "off"], default="auto",
                    help="Thinking mode: 'auto' respects suffix configuration, 'on' forces thinking tokens, 'off' strips thinking passes.")
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    ap.add_argument("--keyword", type=str, default=None,
                    help="override the SEO target keyword (ad-hoc mode)")
    args = ap.parse_args()

    if args.prompt_file is not None:
        if not args.prompt_file.is_file():
            print(f"prompt file not found: {args.prompt_file}", file=sys.stderr)
            return 1
        prompt = args.prompt_file.read_text(encoding="utf-8").strip()
        tasks = [seo_task_from_prompt(prompt, args.keyword)]
    else:
        unknown = [t for t in (args.tasks or []) if t not in TASKS]
        if unknown:
            print(f"unknown tasks: {unknown}; choose from {list(TASKS)}", file=sys.stderr)
            return 1
        keys = args.tasks or list(TASKS)
        tasks = [TASKS[k] for k in keys]

    run_dir = new_run_dir(args.out_root) / "content"
    run_dir.mkdir(parents=True)
    print(f"Run dir: {run_dir.relative_to(REPO_ROOT)}")
    print(f"Tasks:   {', '.join(t.key for t in tasks)}  ({args.attempts}/model each)")
    print(f"Models:  {', '.join(args.models)}\n")

    summary: dict[str, list[dict]] = {}
    for model in args.models:
        print(f"=== {model} ===")
        mdir = run_dir / model
        mdir.mkdir()
        rs: list[dict] = []
        for task in tasks:
            for n in range(1, args.attempts + 1):
                r = run_attempt(model, task, n, args.attempts, args.timeout, args.thinking)
                if r.get("ok"):
                    (mdir / f"{task.key}-attempt-{n}.md").write_text(
                        r["text"], encoding="utf-8")
                rs.append(r)
        summary[model] = rs
        nclean = sum(1 for r in rs if r.get("clean"))
        print(f"  -> {nclean}/{len(rs)} clean\n")

    write_summary(run_dir, summary, tasks, args.attempts)
    return 0


def write_summary(run_dir, summary, tasks, attempts) -> None:
    task_keys = [t.key for t in tasks]
    ranked = []
    for model, rs in summary.items():
        ok = [r for r in rs if r.get("ok")]
        n_clean = sum(1 for r in ok if r["clean"])
        bytask = {tk: sum(1 for r in ok if r["task"] == tk and r["clean"])
                  for tk in task_keys}
        ranked.append({
            "model": model,
            "clean_rate": n_clean / len(ok) if ok else -1.0,
            "n_clean": n_clean, "n_ok": len(ok), "total": len(rs),
            "avg_s": sum(r["elapsed_s"] for r in ok) / len(ok) if ok else 0.0,
            "avg_tps": sum(r["tok_per_s"] for r in ok) / len(ok) if ok else 0.0,
            "avg_w": sum(r["words"] for r in ok) / len(ok) if ok else 0.0,
            "bytask": bytask,
        })
    ranked.sort(key=lambda r: (-r["clean_rate"], -r["avg_tps"]))

    L = ["# Content benchmark", "",
         f"- Tasks: {len(tasks)} ({', '.join(task_keys)})",
         f"- Attempts per task: {attempts}",
         "- **Clean** = the attempt obeys every format + structure + "
         "instruction rule its task checks (each task scores itself).", ""]
    best = next((r for r in ranked if r["clean_rate"] >= 0), None)
    if best:
        L += [f"## 🏆 Winner: `{best['model']}` — "
              f"{best['n_clean']}/{best['n_ok']} clean "
              f"({best['clean_rate']*100:.0f}%) @ {best['avg_tps']:.0f} tok/s", ""]
        valid = [r for r in ranked if r["clean_rate"] >= 0]
        runner_up = valid[1]["clean_rate"] if len(valid) > 1 else None
        note = close_call_note(best["clean_rate"], runner_up, CLOSE_PTS,
                               f"{(best['clean_rate'] - (runner_up or 0))*100:.0f} pts")
        if note:
            L += [note, ""]
    L += ["| Rank | Model | Clean rate | Clean | Avg s | Tok/s | Avg words |",
          "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(ranked, 1):
        if r["n_ok"] == 0:
            L.append(f"| {i} | `{r['model']}` | (all failed) | 0/{r['total']} | — | — | — |")
            continue
        L.append(f"| {i} | `{r['model']}` | {r['clean_rate']*100:.0f}% | "
                 f"{r['n_clean']}/{r['n_ok']} | {r['avg_s']:.1f} | {r['avg_tps']:.0f} | "
                 f"{r['avg_w']:.0f} |")
    # per-task matrix
    L += ["", f"### Per-task (clean / {attempts})", "",
          "| Model | " + " | ".join(task_keys) + " |",
          "|---|" + "---|" * len(task_keys)]
    for r in ranked:
        cells = " | ".join(f"{r['bytask'][tk]}/{attempts}" for tk in task_keys)
        L.append(f"| `{r['model']}` | {cells} |")
    # uncertainty: CI on the headline clean rate + the task each model is worst at
    L += ["", "### Uncertainty", "",
          "Clean rate with a 95% Wilson CI, the weakest task, and a small-sample "
          "flag. Clean is all-or-nothing per attempt, so at these counts the "
          "interval is wide; a one-attempt edge is noise.", ""]
    for r in ranked:
        if r["n_ok"] == 0:
            L.append(f"- `{r['model']}`: all attempts failed to produce output")
            continue
        rates = {tk: r["bytask"][tk] / attempts for tk in task_keys}
        bits = [f"{r['clean_rate']*100:.0f}% (95% CI {ci_str(r['n_clean'], r['n_ok'])})"]
        spread = spread_note(rates)
        if spread:
            bits.append(spread)
        caveat = sample_caveat(r["n_ok"])
        if caveat:
            bits.append(caveat)
        L.append(f"- `{r['model']}`: {'; '.join(bits)}")
    (run_dir / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"Summary: {(run_dir / 'summary.md').relative_to(REPO_ROOT)}")
    if best:
        print(f"Winner:  {best['model']} ({best['clean_rate']*100:.0f}% clean)")


if __name__ == "__main__":
    sys.exit(main())
