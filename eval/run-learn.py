#!/usr/bin/env python3
"""
Learning/tutor benchmark: which local model best ASSISTS with coding + learning,
not just which writes correct code. Each task asks for working code PLUS a
teaching explanation. Scoring is two-part:

  1. Execution gate — the model's code block must pass the hidden asserts.
  2. Explanation score — graded 0–10 on a fixed rubric (approach, complexity,
     alternative, pitfall, clarity) by a LEAVE-ONE-OUT JUDGE PANEL: every
     response is scored by all judge models EXCEPT the one that wrote it, and
     the scores are averaged. This removes the self-grading bias a single judge
     would introduce.

A "teach score" per attempt = explanation score when the code passes, else 0
(a great explanation of broken code doesn't help you learn correct coding).
Models are ranked by mean teach score; the summary also reports raw pass rate
and raw explanation score so you can see both halves.

Runs in two phases to avoid model thrash under OLLAMA_MAX_LOADED_MODELS=1:
generate every response first (each model loaded once), then judge by looping
over the panel (each judge loaded once).

Usage:
  ./eval/run-learn.py                              # all models; panel = all models
  ./eval/run-learn.py --judges granite-custom gemma-content   # fixed panel
  ./eval/run-learn.py --models qwen-custom gemma-content
  ./eval/run-learn.py --tasks lru_cache edit_distance --attempts 5

Output:
  eval/runs/<UTC>/learn/
    summary.md
    <model>/<task>-attempt-<n>.md     # full response + per-judge panel scores
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
    DEFAULT_MODELS, REPO_ROOT, extract_code, generate, new_run_dir,
    resolve_model, run_program, tok_per_s,
)
from learning_tasks import TASKS, LearnTask  # noqa: E402

DEFAULT_OUT_ROOT = REPO_ROOT / "eval" / "runs"
RUBRIC = ["approach", "complexity", "alternative", "pitfall", "clarity"]  # 0–2 each
JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

JUDGE_TEMPLATE = """You are grading a coding explanation written for someone learning to code.
The task was: {topic}.

Score ONLY the explanation prose below (ignore whether the code runs — that is
checked separately). Rate each dimension 0, 1, or 2:
- approach: is the algorithm/approach explained clearly? (0 none, 1 vague, 2 clear)
- complexity: are BOTH time and space complexity stated and correct? (0 neither, 1 one/partly, 2 both correct)
- alternative: is a real alternative approach + its tradeoff given? (0 none, 1 named only, 2 named with tradeoff)
- pitfall: is an edge case or pitfall called out? (0 none, 1 trivial, 2 substantive)
- clarity: is it well-structured and genuinely useful for learning? (0 poor, 1 ok, 2 excellent)

Return ONLY a JSON object, no other text, exactly these keys:
{{"approach":N,"complexity":N,"alternative":N,"pitfall":N,"clarity":N}}

--- EXPLANATION TO GRADE ---
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


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--judges", nargs="+", default=None,
                    help="judge panel (default: all --models, leave-one-out). "
                         "Each response is graded by every judge except the model "
                         "that wrote it, and the scores are averaged.")
    ap.add_argument("--attempts", type=int, default=3, help="attempts per task (default 3)")
    ap.add_argument("--tasks", nargs="+", default=None)
    ap.add_argument("--timeout", type=int, default=120, help="model call timeout (s); culls runaway thinking traces")
    ap.add_argument("--exec-timeout", type=int, default=10)
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    args = ap.parse_args()

    tasks = TASKS if not args.tasks else [t for t in TASKS if t.name in set(args.tasks)]
    if not tasks:
        print(f"no tasks matched {args.tasks}", file=sys.stderr)
        return 1

    judges = args.judges or list(args.models)

    run_dir = new_run_dir(args.out_root) / "learn"
    run_dir.mkdir(parents=True)
    print(f"Run dir: {run_dir.relative_to(REPO_ROOT)}")
    print(f"Tasks:   {', '.join(t.name for t in tasks)}  ({len(tasks)} × {args.attempts}/model)")
    print(f"Models:  {', '.join(args.models)}")
    print(f"Judges:  {', '.join(judges)}  (leave-one-out: no model grades itself)\n")

    # --- Phase 1: generate + execution gate (each model loaded once) ---
    records: list[dict] = []
    for model in args.models:
        print(f"=== generate: {model} ===")
        name, think = resolve_model(model)
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
                code = extract_code(text, "python")
                src = f"{code}\n\n# --- hidden tests ---\n{task.tests}\n"
                passed, reason = run_program(src, args.exec_timeout) if code else (False, "no-code")
                print(f"code={'PASS' if passed else 'FAIL:'+reason:<12}  {elapsed:5.1f}s  {tok_per_s(meta):5.1f} tok/s")
                records.append({"model": model, "task": task.name, "topic": task.topic,
                                "attempt": n, "text": text, "passed": passed,
                                "reason": reason, "elapsed": elapsed,
                                "eval_count": meta.get("eval_count", 0)})
        print()

    # --- Phase 2: judge explanations, leave-one-out panel ---
    # Loop BY judge (each loaded once) over the responses it may grade — a judge
    # never grades a response written by its own model. Collect each judge's
    # explanation total per record, then average across judges.
    for rec in records:
        rec["judge_expl"] = {}   # judge spec -> 0–10 total (parsed judges only)
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

    # Average across the judges that scored each record; gate by code pass.
    for rec in records:
        scores = list(rec["judge_expl"].values())
        rec["expl"] = sum(scores) / len(scores) if scores else 0.0
        rec["n_judges"] = len(scores)
        rec["teach"] = rec["expl"] if rec["passed"] else 0.0
        breakdown = ", ".join(f"{j.split('-')[0]}={v}" for j, v in rec["judge_expl"].items()) or "none"
        body = (f"# {rec['model']} · {rec['task']} · attempt {rec['attempt']}\n\n"
                f"- code: {'PASS' if rec['passed'] else 'FAIL ('+rec['reason']+')'}\n"
                f"- explanation: {rec['expl']:.1f}/10  (panel of {rec['n_judges']}: {breakdown})\n\n"
                f"---\n\n{rec['text']}\n")
        (run_dir / rec["model"] / f"{rec['task']}-attempt-{rec['attempt']}.md").write_text(
            body, encoding="utf-8")

    write_summary(run_dir, records, tasks, args, judges)
    return 0


def write_summary(run_dir, records, tasks, args, judges) -> None:
    task_names = [t.name for t in tasks]
    ranked = []
    for model in args.models:
        rs = [r for r in records if r["model"] == model]
        if not rs:
            continue
        npass = sum(1 for r in rs if r["passed"])
        mean_teach = sum(r["teach"] for r in rs) / len(rs)
        mean_expl = sum(r["expl"] for r in rs) / len(rs)
        passed_rs = [r for r in rs if r["passed"]]
        mean_expl_pass = (sum(r["expl"] for r in passed_rs) / len(passed_rs)
                          if passed_rs else 0.0)
        ranked.append({"model": model, "teach": mean_teach, "expl": mean_expl,
                       "expl_pass": mean_expl_pass, "npass": npass, "n": len(rs)})
    ranked.sort(key=lambda r: -r["teach"])

    L = ["# Learning / tutor benchmark", "",
         f"- Tasks: {len(tasks)} ({', '.join(task_names)})",
         f"- Attempts per task: {args.attempts}",
         f"- Judges: {', '.join(f'`{j}`' for j in judges)} "
         f"(leave-one-out panel — no model grades its own output; rubric 0–2 each: "
         f"{', '.join(RUBRIC)} → /10, averaged across judges)",
         "- **Teach score** = explanation (/10) counted only when the code passes "
         "execution; mean over all attempts. This is the ranking metric.", ""]
    if ranked:
        w = ranked[0]
        L += [f"## 🏆 Best tutor: `{w['model']}` — teach {w['teach']:.1f}/10 "
              f"(code {w['npass']}/{w['n']}, explanation {w['expl']:.1f}/10)", ""]
    L += ["| Rank | Model | Teach /10 | Code pass | Explanation /10 | Expl. when correct |",
          "|---|---|---|---|---|---|"]
    for i, r in enumerate(ranked, 1):
        L.append(f"| {i} | `{r['model']}` | {r['teach']:.1f} | "
                 f"{r['npass']}/{r['n']} | {r['expl']:.1f} | {r['expl_pass']:.1f} |")
    (run_dir / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"Summary: {(run_dir / 'summary.md').relative_to(REPO_ROOT)}")
    if ranked:
        print(f"Best tutor: {ranked[0]['model']} (teach {ranked[0]['teach']:.1f}/10)")


if __name__ == "__main__":
    sys.exit(main())
