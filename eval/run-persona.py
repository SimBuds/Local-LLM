#!/usr/bin/env python3
"""
Prompt-stack benchmark: does the assembled system prompt actually get obeyed?

Every other runner measures the base model *through* the stack. This one measures
the stack itself. Each task in eval/persona_tasks.py targets a specific rule from
`prompts/`, or `memory/` and fails when the response breaks it —
identity leaks, Familiar skills upgraded to Core, invented figures, missing
`Unverified:` marks, missing `Fields:` echoes, `$`-prefixed shell commands.

This is the regression suite for prompt edits. Compress `prompts/system.md` and
the identity rule stops holding, and nothing else in this repo notices.

Scoring is deterministic regex (no LLM judge), so runs are comparable. An attempt
is "clean" iff every rule its task checks holds. The summary groups failures by
the stack rule they implicate, so a red row points at the file to fix.

Usage:
  ./eval/run-persona.py --models gemma qwen lite
  ./eval/run-persona.py --models gemma --tasks identity familiar_skill
  ./eval/run-persona.py --models gemma --attempts 5

  # measure the stack's contribution: same tasks, generic system prompt
  ./eval/run-persona.py --models gemma --system-mode baseline

`--system-mode baseline` replaces the built system prompt with a generic
assistant line, so the delta against `stacked` is what the prompt stack is
actually buying you. A high baseline score means the base model would have
complied anyway and that rule is earning nothing; a large gap means the rule is
load-bearing.

Output:
  eval/runs/<UTC>/persona/
    summary.md
    <model>/<task>-attempt-<n>.md
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gateway import (  # noqa: E402
    REPO_ROOT, add_seed_arg, after_failure, attempt_seed, ci_str, close_call_note,
    generate, get_effective_think, new_run_dir, positive_int, preflight, rel_path,
    resolve_model, sample_caveat, seed_opts, spread_note, tok_per_s,
)
from persona_tasks import TASKS, PersonaTask  # noqa: E402

DEFAULT_OUT_ROOT = REPO_ROOT / "eval" / "runs"
CLOSE_PTS = 0.05  # clean-rate gaps within 5 points are a tie, not a quality win

# Deliberately bland: the point of baseline mode is to strip the project's rules
# while leaving a system prompt in place, so the comparison isolates the stack
# rather than the presence/absence of any system prompt at all.
BASELINE_SYSTEM = "You are a helpful assistant."


def run_attempt(model: str, task: PersonaTask, n: int, total: int, timeout: int,
                thinking_mode: str, system: str | None,
                seed: int | None = None) -> dict:
    print(f"    {task.key:16s} [{n}/{total}] ", end="", flush=True)
    name, model_think = resolve_model(model)
    think = get_effective_think(thinking_mode, model_think)

    t0 = time.monotonic()
    try:
        text, meta = generate(name, task.prompt, timeout, think=think,
                              system=system, options=seed_opts(attempt_seed(seed, n)))
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"FAIL ({time.monotonic()-t0:.1f}s): {e}")
        return {"ok": False, "error": str(e), "exc": e,
                "elapsed_s": time.monotonic() - t0}
    elapsed = time.monotonic() - t0
    s = task.evaluate(text)
    tag = "clean" if s["clean"] else "VIOLATION"
    print(f"{tag:<10} {elapsed:5.1f}s  {tok_per_s(meta):5.1f} tok/s  [{s['flags']}]")
    return {"ok": True, "task": task.key, "clean": s["clean"], "flags": s["flags"],
            "elapsed_s": elapsed, "tok_per_s": tok_per_s(meta), "text": text}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", required=True, help="Ollama model names")
    ap.add_argument("--attempts", type=positive_int, default=5, help="attempts per task (default 5)")
    ap.add_argument("--tasks", nargs="+", default=None,
                    help=f"subset of: {', '.join(TASKS)} (default: all)")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--thinking", choices=["auto", "on", "off"], default="off",
                    help="default off: thinking traces change format compliance, "
                         "which is what this suite measures")
    ap.add_argument("--system-mode", choices=["stacked", "baseline"], default="stacked",
                    help="stacked (default) uses the model's built-in prompt stack; "
                         "baseline substitutes a generic assistant prompt so the "
                         "delta shows what the stack contributes")
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    add_seed_arg(ap)
    args = ap.parse_args()

    unknown = [t for t in (args.tasks or []) if t not in TASKS]
    if unknown:
        print(f"unknown tasks: {unknown}; choose from {list(TASKS)}", file=sys.stderr)
        return 1
    tasks = [TASKS[k] for k in (args.tasks or list(TASKS))]
    system = BASELINE_SYSTEM if args.system_mode == "baseline" else None

    # Fail before creating a run dir, not after filling it with zeros.
    preflight(list(args.models))

    run_dir = new_run_dir(args.out_root) / "persona"
    run_dir.mkdir(parents=True)
    print(f"Run dir: {rel_path(run_dir)}")
    print(f"Tasks:   {', '.join(t.key for t in tasks)}  ({args.attempts}/model each)")
    print(f"Models:  {', '.join(args.models)}")
    print(f"System:  {args.system_mode}"
          + (f" (\"{BASELINE_SYSTEM}\")" if system else " (built prompt stack)") + "\n")

    summary: dict[str, list[dict]] = {}
    for model in args.models:
        print(f"=== {model} ===")
        mdir = run_dir / model
        mdir.mkdir()
        rs: list[dict] = []
        streak = 0  # consecutive failed calls; see after_failure()
        for task in tasks:
            for n in range(1, args.attempts + 1):
                r = run_attempt(model, task, n, args.attempts, args.timeout,
                                args.thinking, system, args.seed)
                streak = streak + 1 if "exc" in r else 0
                if "exc" in r:
                    after_failure(resolve_model(model)[0], r["exc"], streak)
                if r.get("ok"):
                    body = (f"# {model} · {task.key} · attempt {n}\n\n"
                            f"- rule: {task.rule}\n"
                            f"- clean: {'yes' if r['clean'] else 'NO'}\n"
                            f"- flags: {r['flags']}\n"
                            f"- system mode: {args.system_mode}\n\n"
                            f"## Prompt\n\n{task.prompt}\n\n---\n\n{r['text']}\n")
                    (mdir / f"{task.key}-attempt-{n}.md").write_text(body, encoding="utf-8")
                rs.append(r)
        summary[model] = rs
        nclean = sum(1 for r in rs if r.get("clean"))
        print(f"  -> {nclean}/{len(rs)} clean\n")

    write_summary(run_dir, summary, tasks, args)
    return 0


def write_summary(run_dir: Path, summary: dict[str, list[dict]],
                  tasks: list[PersonaTask], args: argparse.Namespace) -> None:
    task_keys = [t.key for t in tasks]
    attempts = args.attempts
    ranked = []
    for model, rs in summary.items():
        ok = [r for r in rs if r.get("ok")]
        n_clean = sum(1 for r in ok if r["clean"])
        bytask = {tk: sum(1 for r in ok if r["task"] == tk and r["clean"])
                  for tk in task_keys}
        # Every distinct violation flag seen, for the diagnosis section.
        flags: dict[str, int] = {}
        for r in ok:
            if not r["clean"]:
                for f in r["flags"].split(","):
                    flags[f] = flags.get(f, 0) + 1
        ranked.append({
            "model": model,
            "clean_rate": n_clean / len(ok) if ok else -1.0,
            "n_clean": n_clean, "n_ok": len(ok), "total": len(rs),
            "avg_s": sum(r["elapsed_s"] for r in ok) / len(ok) if ok else 0.0,
            "avg_tps": sum(r["tok_per_s"] for r in ok) / len(ok) if ok else 0.0,
            "bytask": bytask, "flags": flags,
        })
    ranked.sort(key=lambda r: (-r["clean_rate"], -r["avg_tps"]))

    L = ["# Prompt-stack (persona) benchmark", "",
         f"- Tasks: {len(tasks)} ({', '.join(task_keys)})",
         f"- Attempts per task: {attempts}",
         f"- System mode: **{args.system_mode}**"
         + (f" — generic prompt `{BASELINE_SYSTEM}` substituted for the built stack"
            if args.system_mode == "baseline" else " — the model's built prompt stack"),
         "- **Clean** = the response obeys every stack rule its task checks. "
         "Deterministic regex, no judge.",
         "- This suite measures the prompt stack in `prompts/` and `memory/` "
         " — not base-model capability. A failure names the file to fix.", ""]
    best = next((r for r in ranked if r["clean_rate"] >= 0), None)
    if best:
        L += [f"## 🏆 Most compliant: `{best['model']}` — "
              f"{best['n_clean']}/{best['n_ok']} clean "
              f"({best['clean_rate']*100:.0f}%)", ""]
        valid = [r for r in ranked if r["clean_rate"] >= 0]
        runner_up = valid[1]["clean_rate"] if len(valid) > 1 else None
        note = close_call_note(best["clean_rate"], runner_up, CLOSE_PTS,
                              f"{(best['clean_rate'] - (runner_up or 0))*100:.0f} pts")
        if note:
            L += [note, ""]
    L += ["| Rank | Model | Clean rate | Clean | Avg s | Tok/s |",
          "|---|---|---:|---:|---:|---:|"]
    for i, r in enumerate(ranked, 1):
        if r["n_ok"] == 0:
            L.append(f"| {i} | `{r['model']}` | (all failed) | 0/{r['total']} | — | — |")
            continue
        L.append(f"| {i} | `{r['model']}` | {r['clean_rate']*100:.0f}% | "
                 f"{r['n_clean']}/{r['n_ok']} | {r['avg_s']:.1f} | {r['avg_tps']:.0f} |")

    # per-task matrix
    L += ["", f"### Per-task (clean / {attempts})", "",
          "| Model | " + " | ".join(task_keys) + " |",
          "|---|" + "---:|" * len(task_keys)]
    for r in ranked:
        if r["n_ok"] == 0:
            continue
        cells = " | ".join(f"{r['bytask'][tk]}/{attempts}" for tk in task_keys)
        L.append(f"| `{r['model']}` | {cells} |")

    # which stack rule each task enforces — turns a red cell into an action
    L += ["", "### Rule each task enforces", "",
          "| Task | Stack rule |", "|---|---|"]
    for t in tasks:
        L.append(f"| `{t.key}` | {t.rule} |")

    # observed violations, so a failure is diagnosable without opening every file
    L += ["", "### Violations observed", ""]
    any_flags = False
    for r in ranked:
        if not r.get("flags"):
            continue
        any_flags = True
        items = ", ".join(f"`{f}` ×{c}" for f, c in
                          sorted(r["flags"].items(), key=lambda kv: -kv[1]))
        L.append(f"- `{r['model']}`: {items}")
    if not any_flags:
        L.append("- None — every response obeyed every rule checked.")

    # uncertainty
    L += ["", "### Uncertainty", "",
          "Clean rate with a 95% Wilson CI, the weakest task, and a small-sample "
          "flag. Clean is all-or-nothing per attempt. Note the scorers are "
          "regex: they catch the violation shapes written into them and will miss "
          "novel phrasings, so a clean score is evidence of compliance, not proof.", ""]
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
    print(f"Summary: {rel_path(run_dir / 'summary.md')}")
    if best:
        print(f"Most compliant: {best['model']} ({best['clean_rate']*100:.0f}% clean)")


if __name__ == "__main__":
    sys.exit(main())
