#!/usr/bin/env python3
"""
Coding benchmark: drive each custom model through eval/coding_tasks.py, N
attempts per task, and score by ACTUAL EXECUTION — the model's code block is
extracted, the task's hidden asserts are appended, and the whole thing runs in
a sandboxed subprocess. A task attempt passes iff that process exits 0.

This is a real pass@1 measurement, not a regex proxy. The summary ranks models
by overall pass rate (tie-break: speed) and declares a winner.

Usage:
  ./eval/run-code.py                         # all models, 3 attempts/task
  ./eval/run-code.py --attempts 5
  ./eval/run-code.py --models gemma
  ./eval/run-code.py --tasks two_sum lru_cache
  ./eval/run-code.py --exec-timeout 10       # per-program wall clock

Output:
  eval/runs/<UTC>/code/
    summary.md
    <model>/<task>-attempt-<n>.py     # extracted code + appended tests

SAFETY: model-generated code is executed locally. It runs in a subprocess with
a wall-clock timeout and a fresh temp CWD, but it is NOT containerized. Only run
against models/tasks you trust.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ollama import (  # noqa: E402
    DEFAULT_MODELS, REPO_ROOT, extract_code, generate, new_run_dir,
    resolve_model, run_program, tok_per_s,
)
from coding_tasks import TASKS, Task  # noqa: E402

DEFAULT_OUT_ROOT = REPO_ROOT / "eval" / "runs"


def run_attempt(model: str, task: Task, n: int, total: int, timeout: int,
                exec_timeout: int, out_dir: Path) -> dict:
    print(f"    [{n}/{total}] {task.name:<18}", end="", flush=True)
    name, think = resolve_model(model)
    t0 = time.monotonic()
    try:
        text, meta = generate(name, task.prompt, timeout, think=think)
    except Exception as e:  # noqa: BLE001 — surface any transport error as a fail
        print(f"GEN-FAIL ({time.monotonic()-t0:.1f}s): {e}")
        return {"task": task.name, "passed": False, "reason": "gen-fail",
                "elapsed_s": time.monotonic() - t0, "eval_count": 0}
    elapsed = time.monotonic() - t0

    code = extract_code(text, prefer_lang="python")
    fenced = "```" in text
    source = f"{code}\n\n# --- hidden tests ---\n{task.tests}\n"
    passed, reason = run_program(source, exec_timeout)

    (out_dir / f"{task.name}-attempt-{n}.py").write_text(source, encoding="utf-8")
    mark = "PASS" if passed else f"FAIL:{reason}"
    print(f"{mark:<16} {elapsed:5.1f}s  {tok_per_s(meta):5.1f} tok/s"
          f"{'' if fenced else '  [no-fence]'}")
    return {"task": task.name, "passed": passed, "reason": reason,
            "elapsed_s": elapsed, "eval_count": meta.get("eval_count", 0),
            "tok_per_s": tok_per_s(meta), "fenced": fenced}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--attempts", type=int, default=5, help="attempts per task (default 5)")
    ap.add_argument("--tasks", nargs="+", default=None,
                    help="subset of task names (default: all)")
    ap.add_argument("--timeout", type=int, default=120, help="model call timeout (s); culls runaway thinking traces")
    ap.add_argument("--exec-timeout", type=int, default=10, help="per-program timeout (s)")
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    args = ap.parse_args()

    tasks = TASKS if not args.tasks else [t for t in TASKS if t.name in set(args.tasks)]
    if not tasks:
        print(f"no tasks matched {args.tasks}", file=sys.stderr)
        return 1

    run_dir = new_run_dir(args.out_root) / "code"
    run_dir.mkdir(parents=True)
    per_task = args.attempts
    total = len(tasks) * per_task
    print(f"Run dir: {run_dir.relative_to(REPO_ROOT)}")
    print(f"Tasks:   {', '.join(t.name for t in tasks)}  ({len(tasks)} × {per_task} = {total}/model)")
    print(f"Models:  {', '.join(args.models)}")
    print()

    results: dict[str, list[dict]] = {}
    for model in args.models:
        print(f"=== {model} ===")
        mdir = run_dir / model
        mdir.mkdir()
        rs: list[dict] = []
        i = 0
        for task in tasks:
            for n in range(1, per_task + 1):
                i += 1
                rs.append(run_attempt(model, task, n, per_task, args.timeout,
                                      args.exec_timeout, mdir))
        results[model] = rs
        npass = sum(1 for r in rs if r["passed"])
        print(f"  -> {npass}/{len(rs)} passed\n")

    write_summary(run_dir, results, tasks, args.attempts)
    return 0


def write_summary(run_dir: Path, results: dict[str, list[dict]],
                  tasks: list[Task], attempts: int) -> None:
    task_names = [t.name for t in tasks]
    ranked = []
    for model, rs in results.items():
        npass = sum(1 for r in rs if r["passed"])
        rate = npass / len(rs) if rs else 0.0
        ok = [r for r in rs if r.get("eval_count")]
        avg_tps = sum(r.get("tok_per_s", 0) for r in ok) / len(ok) if ok else 0.0
        avg_s = sum(r["elapsed_s"] for r in rs) / len(rs) if rs else 0.0
        # per-task pass count (out of `attempts`)
        bytask = {tn: sum(1 for r in rs if r["task"] == tn and r["passed"])
                  for tn in task_names}
        ranked.append({"model": model, "rate": rate, "npass": npass,
                       "total": len(rs), "avg_tps": avg_tps, "avg_s": avg_s,
                       "bytask": bytask})
    ranked.sort(key=lambda r: (-r["rate"], -r["avg_tps"]))

    L = ["# Coding benchmark", "",
         f"- Tasks: {len(tasks)} ({', '.join(task_names)})",
         f"- Attempts per task: {attempts}",
         "- Score = fraction of (task × attempt) runs whose code passed all "
         "hidden asserts (real execution).", ""]
    if ranked:
        w = ranked[0]
        L += [f"## 🏆 Winner: `{w['model']}` — "
              f"{w['npass']}/{w['total']} ({w['rate']*100:.0f}%) "
              f"@ {w['avg_tps']:.0f} tok/s", ""]
    # leaderboard
    L += ["| Rank | Model | Pass rate | Passed | Avg s | Tok/s |",
          "|---|---|---|---|---|---|"]
    for i, r in enumerate(ranked, 1):
        L.append(f"| {i} | `{r['model']}` | {r['rate']*100:.0f}% | "
                 f"{r['npass']}/{r['total']} | {r['avg_s']:.1f} | {r['avg_tps']:.0f} |")
    # per-task matrix
    L += ["", f"### Per-task (passed / {attempts})", "",
          "| Model | " + " | ".join(task_names) + " |",
          "|---|" + "---|" * len(task_names)]
    for r in ranked:
        cells = " | ".join(f"{r['bytask'][tn]}/{attempts}" for tn in task_names)
        L.append(f"| `{r['model']}` | {cells} |")
    (run_dir / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"Summary: {(run_dir / 'summary.md').relative_to(REPO_ROOT)}")
    if ranked:
        print(f"Winner:  {ranked[0]['model']} "
              f"({ranked[0]['rate']*100:.0f}% pass)")


if __name__ == "__main__":
    sys.exit(main())
