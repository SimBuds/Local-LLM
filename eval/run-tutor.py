#!/usr/bin/env python3
"""
Tutor benchmark: models should teach without leaking full solutions. Any response
that contains a complete Python solution passing the hidden tests is treated as
a leak and scores 0. Valid responses are graded by a leave-one-out judge panel
on scaffolding, concept, calibration, pitfalls, and clarity.

Usage:
  ./eval/run-tutor.py
  ./eval/run-tutor.py --models gemma --judges gemma
  ./eval/run-tutor.py --tasks two_sum --attempts 1

Output:
  eval/runs/<UTC>/tutor/
    summary.md
    <model>/<task>-attempt-<n>.md   # full response + leak/judge metadata

SAFETY: model-generated code is executed locally. It runs in a subprocess with
an execution timeout and a fresh temp CWD, but it is NOT containerized.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ollama import (  # noqa: E402
    REPO_ROOT, extract_code, generate, get_effective_think, new_run_dir,
    resolve_model, run_program, tok_per_s,
)
from tutor_tasks import TASKS, TutorTask  # noqa: E402

DEFAULT_OUT_ROOT = REPO_ROOT / "eval" / "runs"
RUBRIC = ["scaffolding", "concept", "calibration", "pitfalls", "clarity"]
JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

JUDGE_TEMPLATE = """You are grading a coding tutor response for someone learning
this task. The task was: {topic}.

Score ONLY the prose and teaching guidance, not whether the model wrote runnable
code. Good tutoring should avoid full solutions and should help the learner
reason through the next steps.

Rate each dimension 0, 1, or 2:
- scaffolding: does the response break the work into clear, actionable steps?
- concept: does it explain the right algorithm or data structure accurately?
- calibration: is it pitched at a capable learner without being too terse or too
  hand-holdy?
- pitfalls: does it surface a real edge case, bug pattern, or trap to avoid?
- clarity: is the guidance well-organized and genuinely useful for learning?

Return ONLY a JSON object, no other text, exactly these keys:
{{"scaffolding":N,"concept":N,"calibration":N,"pitfalls":N,"clarity":N}}

--- RESPONSE TO GRADE ---
{response}
--- END ---"""


def judge_scores(judge_model: str, topic: str, response: str, timeout: int) -> dict:
    """Ask the judge for rubric scores. Returns dict of dim->int (0 on parse fail)."""
    prompt = JUDGE_TEMPLATE.format(topic=topic, response=response)
    jname, jthink = resolve_model(judge_model)
    try:
        text, _ = generate(jname, prompt, timeout, think=jthink)
    except Exception:  # noqa: BLE001
        return {d: 0 for d in RUBRIC} | {"_parsed": False}
    m = JSON_RE.search(text)
    if not m:
        return {d: 0 for d in RUBRIC} | {"_parsed": False}
    try:
        raw = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {d: 0 for d in RUBRIC} | {"_parsed": False}
    out = {}
    for d in RUBRIC:
        v = raw.get(d, 0)
        out[d] = max(0, min(2, int(v))) if isinstance(v, (int, float)) else 0
    out["_parsed"] = True
    return out


def solution_leak_check(response: str, tests: str, exec_timeout: int) -> tuple[bool, str]:
    """Return (leak_detected, short_reason)."""
    code = extract_code(response, prefer_lang="python")
    if not code.strip():
        return False, "no-code"
    source = f"{code}\n\n# --- hidden tests ---\n{tests}\n"
    passed, reason = run_program(source, exec_timeout)
    if passed:
        return True, "leak-pass"
    return False, reason or "leak-fail"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", required=True, help="Ollama model names")
    ap.add_argument("--judges", nargs="+", default=None,
                    help="judge panel (default: all --models, leave-one-out)."
                         " Each response is graded by every judge except the model "
                         "that wrote it, and the scores are averaged.")
    ap.add_argument("--attempts", type=int, default=3,
                    help="attempts per task (default 3)")
    ap.add_argument("--tasks", nargs="+", default=None)
    ap.add_argument("--timeout", type=int, default=120,
                    help="model call timeout (s); culls runaway thinking traces")
    ap.add_argument("--thinking", choices=["auto", "on", "off"], default="auto",
                    help="Thinking mode: 'auto' respects suffix configuration, 'on' forces thinking tokens, 'off' strips thinking passes.")
    ap.add_argument("--exec-timeout", type=int, default=10,
                    help="per-program timeout (s)")
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    args = ap.parse_args()

    tasks = TASKS if not args.tasks else [t for t in TASKS if t.name in set(args.tasks)]
    if not tasks:
        print(f"no tasks matched {args.tasks}", file=sys.stderr)
        return 1

    judges = args.judges or list(args.models)

    run_dir = new_run_dir(args.out_root) / "tutor"
    run_dir.mkdir(parents=True)
    print(f"Run dir: {run_dir.relative_to(REPO_ROOT)}")
    print(f"Tasks:   {', '.join(t.name for t in tasks)}  ({len(tasks)} × {args.attempts}/model)")
    print(f"Models:  {', '.join(args.models)}")
    print(f"Judges:  {', '.join(judges)}  (leave-one-out: no model grades itself)\n")

    records: list[dict] = []
    for model in args.models:
        print(f"=== generate: {model} ===")
        name, model_think = resolve_model(model)

        think = get_effective_think(args.thinking, model_think)

        mdir = run_dir / model
        mdir.mkdir()
        for task in tasks:
            for n in range(1, args.attempts + 1):
                print(f"    {task.name:<16} [{n}/{args.attempts}] ", end="", flush=True)
                t0 = time.monotonic()
                try:
                    text, meta = generate(name, task.prompt, args.timeout, think=think)
                except Exception as e:  # noqa: BLE001
                    print(f"GEN-FAIL: {e}")
                    text, meta = "", {}
                elapsed = time.monotonic() - t0
                leak, leak_reason = solution_leak_check(text, task.tests, args.exec_timeout)
                status = "LEAK" if leak else "OK"
                print(f"{status:<8} {elapsed:5.1f}s  {tok_per_s(meta):5.1f} tok/s")
                records.append({
                    "model": model,
                    "task": task.name,
                    "topic": task.topic,
                    "attempt": n,
                    "text": text,
                    "leak": leak,
                    "leak_reason": leak_reason,
                    "elapsed": elapsed,
                    "eval_count": meta.get("eval_count", 0),
                    "judge_expl": {},
                })
        print()

    for judge in judges:
        jname = resolve_model(judge)[0]
        eligible = [r for r in records if resolve_model(r["model"])[0] != jname]
        print(f"=== judge: {judge}  ({len(eligible)} responses, skipping its own) ===")
        for i, rec in enumerate(eligible, 1):
            sc = judge_scores(judge, rec["topic"], rec["text"], args.timeout)
            if sc.get("_parsed"):
                rec["judge_expl"][judge] = sum(sc[d] for d in RUBRIC)
            if i % 15 == 0 or i == len(eligible):
                print(f"    {i}/{len(eligible)}")
    print()

    for rec in records:
        scores = list(rec["judge_expl"].values())
        rec["expl"] = sum(scores) / len(scores) if scores else 0.0
        rec["n_judges"] = len(scores)
        rec["teach"] = 0.0 if rec["leak"] else rec["expl"]
        breakdown = ", ".join(f"{j.split('-')[0]}={v}" for j, v in rec["judge_expl"].items()) or "none"
        body = (
            f"# {rec['model']} · {rec['task']} · attempt {rec['attempt']}\n\n"
            f"- leak: {'YES' if rec['leak'] else 'no'}\n"
            f"- leak reason: {rec['leak_reason']}\n"
            f"- explanation score: {rec['expl']:.1f}/10\n"
            f"- judges: {rec['n_judges']} ({breakdown})\n\n"
            f"---\n\n{rec['text']}\n"
        )
        (run_dir / rec["model"] / f"{rec['task']}-attempt-{rec['attempt']}.md").write_text(
            body, encoding="utf-8")

    write_summary(run_dir, records, tasks, args, judges)
    return 0


def write_summary(run_dir: Path, records: list[dict], tasks: list[TutorTask],
                  args: argparse.Namespace, judges: list[str]) -> None:
    task_names = [t.name for t in tasks]
    ranked = []
    for model in args.models:
        rs = [r for r in records if r["model"] == model]
        if not rs:
            continue
        nleak = sum(1 for r in rs if r["leak"])
        mean_teach = sum(r["teach"] for r in rs) / len(rs)
        mean_expl = sum(r["expl"] for r in rs) / len(rs)
        nonleak = [r for r in rs if not r["leak"]]
        mean_expl_nonleak = (sum(r["expl"] for r in nonleak) / len(nonleak)) if nonleak else 0.0
        ranked.append({
            "model": model,
            "teach": mean_teach,
            "expl": mean_expl,
            "expl_nonleak": mean_expl_nonleak,
            "nleak": nleak,
            "n": len(rs),
        })
    ranked.sort(key=lambda r: -r["teach"])

    L = ["# Tutor benchmark", "",
         f"- Tasks: {len(tasks)} ({', '.join(task_names)})",
         f"- Attempts per task: {args.attempts}",
         f"- Judges: {', '.join(f'`{j}`' for j in judges)} "
         f"(leave-one-out panel — no model grades its own output; rubric 0–2 each: "
         f"{', '.join(RUBRIC)} → /10, averaged across judges)",
         "- **Gate:** any response containing a complete solution that passes the "
         "hidden tests is treated as a leak and scores 0.", ""]
    if ranked:
        w = ranked[0]
        L += [
            f"## 🏆 Best tutor: `{w['model']}` — teach {w['teach']:.1f}/10 "
            f"(leaks {w['nleak']}/{w['n']}, explanation {w['expl']:.1f}/10, "
            f"non-leak explanation {w['expl_nonleak']:.1f}/10)", ""
        ]
    L += ["| Rank | Model | Teach /10 | Leaks | Explanation /10 | Explanation (no leaks) /10 |",
          "|---|---|---|---|---|---|"]
    for i, r in enumerate(ranked, 1):
        L.append(f"| {i} | `{r['model']}` | {r['teach']:.1f} | {r['nleak']}/{r['n']} | "
                 f"{r['expl']:.1f} | {r['expl_nonleak']:.1f} |")
    (run_dir / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"Summary: {(run_dir / 'summary.md').relative_to(REPO_ROOT)}")
    if ranked:
        print(f"Best tutor: {ranked[0]['model']} (teach {ranked[0]['teach']:.1f}/10)")


if __name__ == "__main__":
    sys.exit(main())
