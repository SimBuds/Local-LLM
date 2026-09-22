#!/usr/bin/env python3
"""
Tool-calling benchmark: can the model drive an agent loop?

The other runners score what a model writes. An editor agent (Cline, Continue)
instead fails by calling the wrong function, inventing an argument nobody gave
it, or calling a tool for a question it could have answered itself. This suite
offers each task a small tool list with distractors in it, then scores the
tool calls that come back — deterministically, no judge.

  pass = the expected calls are all present, with matching arguments, and
         nothing extra was called. For a no-call task, pass = no call at all
         plus a non-empty prose answer.

Argument matching is per key with normalization (strings compare case- and
whitespace-insensitively, numbers by value), because "toronto" and "Toronto"
are the same call. A key the tool's own schema does not declare fails: that is
an argument the model made up. See tool_tasks.py for the task table and the
`any_of` / `contains` matchers used for free-text arguments.

Usage:
  ./eval/run-tools.py --models gemma qwen lite
  ./eval/run-tools.py --models gemma --tasks pick_tool parallel_calls
  ./eval/run-tools.py --models gemma qwen lite --attempts 5 --out-root /tmp/scratch

Output:
  eval/runs/<UTC>/tools/
    summary.md
    <model>/<task>-attempt-<n>.json   (calls, answer text, failure reasons)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gateway import (  # noqa: E402
    REPO_ROOT, add_seed_arg, after_failure, attempt_seed, chat, ci_str, close_call_note,
    get_effective_think, new_run_dir, positive_int, preflight, rel_path, resolve_model,
    sample_caveat, seed_opts, spread_note, tok_per_s,
)
from tool_tasks import TASKS, ToolTask  # noqa: E402

DEFAULT_OUT_ROOT = REPO_ROOT / "eval" / "runs"
CLOSE_PTS = 0.05  # pass-rate gaps within 5 points are a tie, not a win


# --- scoring ------------------------------------------------------------------

def _norm(value):
    """Normalize a scalar for comparison: strings case- and space-insensitive,
    numbers by value. bool stays bool so True never equals 1."""
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    return value


def _match_value(expected, actual) -> bool:
    """Compare one argument value against a task's expectation.

    A dict expectation is either a matcher ({"any_of": [...]}, {"contains": "..."})
    or a nested object compared per key, which is how create_event's `start` is
    checked without pinning the keys the task does not care about.
    """
    if isinstance(expected, dict):
        if "any_of" in expected:
            return any(_match_value(alt, actual) for alt in expected["any_of"])
        if "contains" in expected:
            return isinstance(actual, str) and _norm(expected["contains"]) in _norm(actual)
        if not isinstance(actual, dict):
            return False
        return all(k in actual and _match_value(v, actual[k]) for k, v in expected.items())
    return _norm(expected) == _norm(actual)


def _schema_for(task: ToolTask, name: str) -> dict:
    for tool in task.tools:
        if tool["function"]["name"] == name:
            return tool["function"].get("parameters", {})
    return {}


def _call_matches(task: ToolTask, expected: dict, actual: dict) -> bool:
    if actual["name"] != expected["name"]:
        return False
    args = actual.get("arguments") or {}
    return all(k in args and _match_value(v, args[k]) for k, v in expected["args"].items())


def score_calls(task: ToolTask, calls: list[dict], text: str,
                expect: list[dict] | None = None) -> tuple[bool, list[str]]:
    """Score one round of calls. Returns (passed, reasons).

    Reasons are short slugs (`wrong-tool`, `wrong-arg`, `extra-call`, ...) so the
    summary can count failure shapes rather than only a rate: a model that calls
    the right tool with a wrong argument is a different problem from one that
    ignores the tools. `expect` overrides the task's first-round expectation,
    which is how the second round is scored against `expect_after`.
    """
    expect = task.expect if expect is None else expect
    reasons: list[str] = []

    for c in calls:
        if not c.get("arguments_ok", True):
            reasons.append(f"bad-json-args: {c['name']} {c.get('arguments_raw')!r}")

    if not expect:
        for c in calls:
            reasons.append(f"unwanted-call: {c['name']} (task needs no tool)")
        if not calls and not text.strip():
            reasons.append("empty-answer: no call and no prose answer")
        return (not reasons), reasons

    unmatched = list(calls)
    for exp in expect:
        hit = next((c for c in unmatched if _call_matches(task, exp, c)), None)
        if hit is not None:
            unmatched.remove(hit)
            continue
        same_name = [c for c in unmatched if c["name"] == exp["name"]]
        if same_name:
            got = same_name[0].get("arguments")
            reasons.append(f"wrong-arg: {exp['name']} expected {exp['args']}, got {got}")
            unmatched.remove(same_name[0])
        elif calls and not any(c["name"] == exp["name"] for c in calls):
            # The tool was never called at all: the model reached for a neighbour.
            reasons.append(f"wrong-tool: expected {exp['name']}, called "
                           f"{', '.join(c['name'] for c in calls)}")
        else:
            # Either nothing was called, or this tool was called but fewer times
            # than the task expects (one of two parallel calls missing).
            reasons.append(f"no-call: expected {exp['name']}")

    for c in unmatched:
        reasons.append(f"extra-call: {c['name']}")

    # An argument the tool's schema never declared is one the model invented.
    for c in calls:
        props = _schema_for(task, c["name"]).get("properties", {})
        if not props:
            continue
        for key in (c.get("arguments") or {}):
            if key not in props:
                reasons.append(f"unknown-arg: {c['name']}.{key} not in schema")

    # wrong-arg reads before extra-call/unknown-arg for a caller reading reasons[0].
    reasons.sort(key=lambda r: r.split(":")[0] not in ("no-call", "wrong-tool", "wrong-arg",
                                                       "bad-json-args"))
    return (not reasons), reasons


def score_answer(task: ToolTask, text: str) -> tuple[bool, list[str]]:
    """Score the final prose against a task's grounding checks.

    A model that calls the right tool and then ignores what came back is the
    failure this catches, so the checks look for the value the canned result
    carried, and for claims it did not support.
    """
    reasons: list[str] = []
    hay = _norm(text)
    for label, kind, value in task.answer_checks:
        if kind == "has":
            if _norm(value) not in hay:
                reasons.append(f"answer-missing: {label} ({value!r})")
        elif kind == "lacks":
            if _norm(value) in hay:
                reasons.append(f"answer-forbidden: {label} ({value!r})")
        elif kind == "any":
            if not any(_norm(v) in hay for v in value):
                reasons.append(f"answer-missing-any: {label}")
        else:
            raise ValueError(f"unknown answer check kind {kind!r} in task {task.key}")
    return (not reasons), reasons


def result_messages(task: ToolTask, calls: list[dict]) -> list[dict]:
    """Canned tool results for the calls the model just made.

    Keyed by tool name. A call the task has no canned result for still gets a
    reply, as an error, because leaving a tool call unanswered would make the
    second round measure a malformed transcript rather than the model.
    """
    out = []
    for c in calls:
        result = task.results.get(c["name"])
        if result is None:
            result = {"error": f"no such tool result available for {c['name']}"}
        out.append({"role": "tool", "tool_call_id": c.get("id") or "",
                    "content": json.dumps(result)})
    return out


# --- run ----------------------------------------------------------------------

def run_attempt(model: str, task: ToolTask, n: int, total: int, timeout: int,
                thinking_mode: str, seed: int | None = None) -> dict:
    print(f"    {task.key:22s} [{n}/{total}] ", end="", flush=True)
    name, model_think = resolve_model(model)
    think = get_effective_think(thinking_mode, model_think)
    options = seed_opts(attempt_seed(seed, n), {"temperature": 0.0})

    messages = [{"role": "user", "content": task.user}]
    t0 = time.monotonic()
    try:
        text, calls, meta = chat(name, messages, timeout, think=think,
                                 options=options, tools=task.tools)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"FAIL ({time.monotonic()-t0:.1f}s): {e}")
        return {"ok": False, "error": str(e), "exc": e,
                "elapsed_s": time.monotonic() - t0}

    passed, reasons = score_calls(task, calls, text)
    calls_after: list[dict] = []
    # Second round: hand back the canned results and score what the model does
    # with them. Only for tasks that declare results, and only when the first
    # round actually called something to answer.
    if task.results and calls:
        messages += [
            {"role": "assistant", "content": text,
             "tool_calls": [{"type": "function", "id": c["id"],
                             "function": {"name": c["name"],
                                          "arguments": json.dumps(c["arguments"])}}
                            for c in calls]},
            *result_messages(task, calls),
        ]
        try:
            text, calls_after, meta = chat(name, messages, timeout, think=think,
                                           options=options, tools=task.tools)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"FAIL ({time.monotonic()-t0:.1f}s): {e}")
            return {"ok": False, "error": str(e), "exc": e,
                    "elapsed_s": time.monotonic() - t0}
        after_ok, after_reasons = score_calls(task, calls_after, text,
                                              expect=task.expect_after)
        answer_ok, answer_reasons = score_answer(task, text)
        passed = passed and after_ok and answer_ok
        reasons += after_reasons + answer_reasons
    elapsed = time.monotonic() - t0

    tag = "ok" if passed else reasons[0].split(":")[0]
    print(f"{tag:>16}  {elapsed:6.1f}s  {tok_per_s(meta):5.1f} tok/s")
    return {
        "ok": True, "passed": passed, "reasons": reasons,
        "calls": [{"name": c["name"], "arguments": c["arguments"],
                   "arguments_ok": c["arguments_ok"]} for c in calls],
        "calls_after": [{"name": c["name"], "arguments": c["arguments"]}
                        for c in calls_after],
        "text": text, "elapsed_s": elapsed, "tok_per_s": tok_per_s(meta),
        "prompt_tokens": meta.get("prompt_eval_count", 0),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--tasks", nargs="+", default=list(TASKS),
                    help=f"Subset of: {', '.join(TASKS)}")
    ap.add_argument("--attempts", type=positive_int, default=3)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--thinking", choices=["auto", "on", "off"], default="off")
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    add_seed_arg(ap)
    args = ap.parse_args()

    unknown = [t for t in args.tasks if t not in TASKS]
    if unknown:
        print(f"unknown tasks: {unknown}; choose from {list(TASKS)}", file=sys.stderr)
        return 1
    tasks = [TASKS[t] for t in args.tasks]
    # Fail before creating a run dir, not after filling it with failures.
    preflight(list(args.models))

    run_dir = new_run_dir(args.out_root) / "tools"
    run_dir.mkdir(parents=True)
    print(f"Run dir: {rel_path(run_dir)}")
    print(f"Tasks:   {', '.join(t.key for t in tasks)}  ({args.attempts}/model each)")
    print(f"Models:  {', '.join(args.models)}, temperature 0\n")

    summary: dict[str, dict[str, list[dict]]] = {}
    for model in args.models:
        print(f"=== {model} ===")
        mdir = run_dir / model
        mdir.mkdir()
        summary[model] = {}
        streak = 0
        for task in tasks:
            rs = []
            for n in range(1, args.attempts + 1):
                r = run_attempt(model, task, n, args.attempts, args.timeout,
                                args.thinking, args.seed)
                if r.get("ok"):
                    streak = 0
                    (mdir / f"{task.key}-attempt-{n}.json").write_text(
                        json.dumps({"calls": r["calls"],
                                    "calls_after": r.get("calls_after", []),
                                    "text": r["text"], "passed": r["passed"],
                                    "reasons": r["reasons"]},
                                   indent=2), encoding="utf-8")
                else:
                    streak += 1
                    # Stops the run on a crashed model or a dead router, before
                    # the summary can report either as the model's answers.
                    after_failure(resolve_model(model)[0], r["exc"], streak)
                rs.append(r)
            summary[model][task.key] = rs
        print()

    write_summary(run_dir, summary, args, tasks)
    return 0


def write_summary(run_dir, summary, args, tasks) -> None:
    ranked = []
    for model, by_task in summary.items():
        all_r = [r for rs in by_task.values() for r in rs]
        ok = [r for r in all_r if r.get("ok")]
        if not ok:
            ranked.append({"model": model, "rate": -1.0, "n_ok": 0, "total": len(all_r)})
            continue
        n_pass = sum(1 for r in ok if r["passed"])
        shapes: dict[str, int] = {}
        for r in ok:
            for reason in r["reasons"]:
                shapes[reason.split(":")[0]] = shapes.get(reason.split(":")[0], 0) + 1
        ranked.append({
            "model": model, "rate": n_pass / len(ok), "n_pass": n_pass,
            "avg_s": sum(r["elapsed_s"] for r in ok) / len(ok),
            "avg_tps": sum(r["tok_per_s"] for r in ok) / len(ok),
            "shapes": shapes, "n_ok": len(ok), "total": len(all_r), "by_task": by_task,
        })
    ranked.sort(key=lambda r: (-r["rate"], -r.get("avg_tps", 0)))

    L = ["# Tool-calling benchmark", "",
         f"- Tasks: {len(tasks)} ({', '.join(t.key for t in tasks)})",
         f"- Attempts per task: {args.attempts}",
         "- Each task offers a small tool list including distractors; scoring is "
         "deterministic, no judge.",
         "- **Pass** = every expected call present with matching arguments and "
         "nothing extra. For a no-tool task, pass = no call plus a prose answer.",
         "- Arguments match per key, normalized for case, whitespace, and numeric "
         "type. An argument outside the tool's schema fails as invented.", ""]
    best = next((r for r in ranked if r["rate"] >= 0), None)
    if best:
        L += [f"## 🏆 Winner: `{best['model']}` — {best['n_pass']}/{best['n_ok']} "
              f"({best['rate']*100:.0f}%)", ""]
        valid = [r for r in ranked if r["rate"] >= 0]
        runner_up = valid[1]["rate"] if len(valid) > 1 else None
        note = close_call_note(best["rate"], runner_up, CLOSE_PTS,
                               f"{(best['rate'] - (runner_up or 0))*100:.0f} pts")
        if note:
            L += [note, ""]
    L += ["| Rank | Model | Pass rate | Passed | Avg s | Tok/s |",
          "|---|---|---:|---:|---:|---:|"]
    for i, r in enumerate(ranked, 1):
        if r["n_ok"] == 0:
            L.append(f"| {i} | `{r['model']}` | (all failed) | — | — | — |")
            continue
        L.append(f"| {i} | `{r['model']}` | {r['rate']*100:.0f}% | "
                 f"{r['n_pass']}/{r['n_ok']} | {r['avg_s']:.1f} | {r['avg_tps']:.0f} |")

    L += ["", "### Per-task (passed / attempts)", "",
          "| Model | " + " | ".join(t.key for t in tasks) + " |",
          "|---|" + "---|" * len(tasks)]
    for r in ranked:
        if r["n_ok"] == 0:
            continue
        cells = []
        for t in tasks:
            rs = [x for x in r["by_task"][t.key] if x.get("ok")]
            cells.append(f"{sum(1 for x in rs if x['passed'])}/{len(rs)}" if rs else "—")
        L.append(f"| `{r['model']}` | " + " | ".join(cells) + " |")

    multi = [t for t in tasks if t.results]
    if multi:
        L += ["", f"Tasks with a second round (canned tool result fed back): "
              f"{', '.join(t.key for t in multi)}. Those score the follow-up call "
              f"and the final answer as well as the first call.", ""]
    L += ["", "### What each task catches", "",
          "| Task | Failure it catches |", "|---|---|"]
    for t in tasks:
        L.append(f"| `{t.key}` | {t.why} |")

    L += ["", "### Failure shapes observed", "",
          "Counted over failed attempts. `wrong-tool` and `unwanted-call` break an "
          "agent loop outright; `wrong-arg` and `unknown-arg` produce a call that "
          "runs and returns the wrong thing.", ""]
    for r in ranked:
        if r["n_ok"] == 0:
            L.append(f"- `{r['model']}`: all attempts failed to produce output")
            continue
        shapes = ", ".join(f"{k} ×{v}" for k, v in sorted(r["shapes"].items(),
                                                          key=lambda kv: -kv[1]))
        L.append(f"- `{r['model']}`: {shapes or 'none'}")

    L += ["", "### Uncertainty", "",
          "Pass rate with a 95% Wilson CI, the weakest task, and a small-sample "
          "flag. Pass is all-or-nothing per attempt, so intervals are wide at "
          "these counts.", ""]
    for r in ranked:
        if r["n_ok"] == 0:
            continue
        rates = {}
        for t in tasks:
            rs = [x for x in r["by_task"][t.key] if x.get("ok")]
            if rs:
                rates[t.key] = sum(1 for x in rs if x["passed"]) / len(rs)
        bits = [f"{r['rate']*100:.0f}% (95% CI {ci_str(r['n_pass'], r['n_ok'])})"]
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
        print(f"Winner:  {best['model']} ({best['rate']*100:.0f}%)")


if __name__ == "__main__":
    sys.exit(main())
