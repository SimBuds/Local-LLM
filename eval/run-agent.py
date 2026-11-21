#!/usr/bin/env python3
"""
Agent benchmark: each model works a multi-step task in a real loop. It gets one
tool, `bash`, running in a sandboxed copy of a small project. The loop feeds every
command's output back until the model replies without a tool call, then hidden
tests run against the project it left behind.

This is the suite that measures what run-tools.py cannot: run-tools.py scores the
shape of a single tool call against canned results, and nothing there executes.
Here the commands really run, and only the final state of the project is scored.

A pass needs both: the hidden tests pass, and the model ended the loop itself.
A model that fixes the bug but never stops is reported as `step-cap`.

Usage:
  ./eval/run-agent.py --models gemma qwen lite
  ./eval/run-agent.py --models lite --tasks fix_failing_test --attempts 1
  ./eval/run-agent.py --models lite --out-root /tmp/check    # results outside eval/runs/

Output:
  eval/runs/<UTC>/agent/
    summary.md
    <model>/<task>-attempt-<n>.json     # full transcript, stop reason, score

SAFETY: model-written shell commands are executed, always under bubblewrap:
read-only /usr, no network, no $HOME, no host environment, one writable project
directory. Unlike run-code.py there is no unsandboxed fallback. If bwrap cannot
start, the run stops before any model call.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import tempfile
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gateway import (  # noqa: E402
    REPO_ROOT, SandboxUnavailable, add_seed_arg, after_failure, attempt_seed, avg_tps, chat,
    ci_str, close_call_note, get_effective_think, new_run_dir, positive_int, preflight,
    rel_path, resolve_model, sample_caveat, sandbox_exec, seed_opts, served_ctx, spread_note,
    tok_per_s,
)
import desk  # noqa: E402
from agent_tasks import TASKS, AgentTask, DeskTask  # noqa: E402

DEFAULT_OUT_ROOT = REPO_ROOT / "eval" / "runs"
PROMPT = (Path(__file__).resolve().parent / "prompts" / "agent-system.md").read_text(encoding="utf-8")
DESK_PROMPT = (Path(__file__).resolve().parent / "prompts" / "desk-system.md").read_text(encoding="utf-8")

CLOSE_PTS = 0.05       # pass-rate gaps within 5 points are a tie
WALL_S = 600           # default wall clock for one attempt's loop (--wall-clock)
CMD_TIMEOUT = 30       # one bash command (stated in the system prompt)
EXEC_TIMEOUT = 30      # the hidden tests
NUM_PREDICT = 2048     # generation ceiling per turn
OUTPUT_CAP = 4000      # characters of command output fed back per call
REQUIRED_CTX = 65536   # served context a model needs for a full-length loop
CHARS_PER_TOKEN = 3    # deliberately low, so the context guard overestimates

TOOLS = [{
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Run a shell command in the project directory. "
                       "Returns the exit code and the combined stdout and stderr.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "The command to run."}},
            "required": ["command"],
        },
    },
}]


def clip(text: str, cap: int = OUTPUT_CAP) -> str:
    """Keep the head and tail of long output, with a marker naming what was cut."""
    if len(text) <= cap:
        return text
    half = cap // 2
    return (f"{text[:half]}\n[... {len(text) - 2 * half} characters omitted ...]\n"
            f"{text[-half:]}")


def require_sandbox() -> None:
    """Stop the run when bwrap cannot start. Model-written shell never runs bare."""
    try:
        with tempfile.TemporaryDirectory() as td:
            sandbox_exec(["true"], td, 10)
    except SandboxUnavailable as e:
        raise SystemExit(f"ERROR: run-agent.py needs a working sandbox.\n  {e}")


def require_ctx(models: list[str], required: int) -> dict[str, int]:
    """Served n_ctx per model spec. Aborts when any model serves less than `required`."""
    served = {spec: served_ctx(resolve_model(spec)[0]) for spec in models}
    short = [f"{spec} ({ctx})" for spec, ctx in served.items() if ctx < required]
    if short:
        raise SystemExit(
            f"ERROR: served context below the {required} this suite needs: {', '.join(short)}\n"
            f"  Raise ctx-size in server.ini, then `make build` and restart the router.")
    return served


def execute_call(call: dict, workdir: str, cmd_timeout: int = CMD_TIMEOUT) -> tuple[str, bool]:
    """Run one tool call. Returns (text for the model, whether the call was well formed)."""
    if call.get("name") != "bash":
        return f"error: unknown tool {call.get('name')!r}. The only tool is bash.", False
    command = call.get("arguments", {}).get("command")
    if not call.get("arguments_ok") or not isinstance(command, str):
        return ('error: the arguments must be a JSON object with a string "command". '
                "Nothing was run."), False
    code, out = sandbox_exec(["bash", "-c", command], workdir, cmd_timeout)
    head = f"[timed out after {cmd_timeout}s]" if code is None else f"[exit {code}]"
    return f"{head}\n{clip(out)}", True


def _timed_out(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)


def run_loop(name: str, think: bool, instruction: str, tools: list[dict], system: str,
             execute, options: dict, timeout: int, deadline: float, n_ctx: int,
             max_steps: int) -> dict:
    """Drive one attempt until the model stops, a limit is hit, or a call fails.

    The caller supplies the tools on offer, the system prompt, and `execute`,
    which takes one parsed tool call and returns (text for the model, whether the
    call was well formed). The loop itself knows nothing about what a tool does.

    `stop` is one of: done, step-cap, wall-clock, context, gen-timeout, gen-fail.
    """
    messages: list[dict] = [{"role": "user", "content": instruction}]
    out = {"stop": "step-cap", "steps": 0, "bad_calls": 0, "prompt_tokens": 0,
           "tok_per_s": 0.0, "messages": messages}
    speeds = []
    for _ in range(max_steps):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            out["stop"] = "wall-clock"
            break
        try:
            text, calls, meta = chat(name, messages, max(1, min(timeout, int(remaining))),
                                     think=think, options=options, tools=tools, system=system)
        except (urllib.error.URLError, TimeoutError) as e:
            if _timed_out(e) and time.monotonic() >= deadline:
                out["stop"] = "wall-clock"      # the attempt's own deadline cut the call short
            else:
                out["stop"] = "gen-timeout" if _timed_out(e) else "gen-fail"
                out["exc"] = e
            break
        out["steps"] += 1
        out["prompt_tokens"] = meta.get("prompt_eval_count", 0)
        speeds.append({"tok_per_s": tok_per_s(meta)})
        if not calls:
            messages.append({"role": "assistant", "content": text})
            out["stop"] = "done"
            break
        messages.append({
            "role": "assistant", "content": text,
            "tool_calls": [{"type": "function", "id": c.get("id") or "",
                            "function": {"name": c.get("name") or "",
                                         "arguments": json.dumps(c.get("arguments", {}))}}
                           for c in calls]})
        fed = 0
        for c in calls:
            result, well_formed = execute(c)
            out["bad_calls"] += 0 if well_formed else 1
            fed += len(result)
            messages.append({"role": "tool", "tool_call_id": c.get("id") or "", "content": result})
        # Stop before a prompt that could not fit, instead of letting the server
        # truncate or reject it. The estimate errs high on purpose.
        next_prompt = (meta.get("prompt_eval_count", 0) + meta.get("eval_count", 0)
                       + fed // CHARS_PER_TOKEN)
        if next_prompt + NUM_PREDICT > n_ctx:
            out["stop"] = "context"
            break
    out["tok_per_s"] = avg_tps(speeds)
    return out


def score(task: AgentTask, workdir: str, exec_timeout: int = EXEC_TIMEOUT) -> bool:
    """Run the hidden tests against the project as the model left it.

    The tests go in on stdin and never touch the disk. A fresh nonce must be the
    last line printed, so code that exits 0 early does not count as passing.
    """
    nonce = secrets.token_hex(8)
    code, out = sandbox_exec([sys.executable, "-"], workdir, exec_timeout,
                             stdin=f"{task.tests}\nprint({nonce!r})\n")
    return code == 0 and out.strip().splitlines()[-1:] == [nonce]


def reason_for(stop: str, tests_ok: bool) -> str:
    if stop != "done":
        return stop
    return "pass" if tests_ok else "tests-failed"


def run_attempt(model: str, task: AgentTask | DeskTask, n: int, total: int, timeout: int,
                thinking_mode: str, n_ctx: int, max_steps: int, out_dir: Path,
                seed: int | None = None, wall_s: int = WALL_S) -> dict:
    print(f"    {task.key:22s} [{n}/{total}] ", end="", flush=True)
    name, model_think = resolve_model(model)
    think = get_effective_think(thinking_mode, model_think)
    options = seed_opts(attempt_seed(seed, n), {"num_predict": NUM_PREDICT})

    t0 = time.monotonic()
    if isinstance(task, DeskTask):
        # No sandbox: the tools are Python functions over one in-memory shop,
        # and the score is whether its records ended up as the task requires.
        shop = desk.new_state()
        loop = run_loop(name, think, task.instruction, desk.TOOLS, DESK_PROMPT,
                        lambda call: desk.dispatch(shop, call), options, timeout,
                        t0 + wall_s, n_ctx, max_steps)
        tests_ok = "exc" not in loop and desk.same_state(
            shop, desk.patched(desk.new_state(), task.changes))
    else:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workdir:
            # Seed files are written before the model acts. Nothing on the host
            # writes into this directory afterwards: a model-made symlink could
            # redirect a later host-side write outside the sandbox.
            for rel, content in task.files.items():
                path = Path(workdir) / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            loop = run_loop(name, think, task.instruction, TOOLS, PROMPT,
                            lambda call: execute_call(call, workdir), options, timeout,
                            t0 + wall_s, n_ctx, max_steps)
            tests_ok = False if "exc" in loop else score(task, workdir)
    elapsed = time.monotonic() - t0

    reason = reason_for(loop["stop"], tests_ok)
    record = {
        "task": task.key, "model": model, "attempt": n, "passed": reason == "pass",
        "reason": reason, "stop": loop["stop"], "tests_ok": tests_ok,
        "steps": loop["steps"], "bad_calls": loop["bad_calls"],
        "prompt_tokens": loop["prompt_tokens"], "elapsed_s": elapsed,
        "tok_per_s": loop["tok_per_s"],
    }
    # A call that failed before the model said anything has no transcript worth
    # keeping, and an empty one would stop an aborted run's folder being pruned.
    if loop["steps"]:
        (out_dir / f"{task.key}-attempt-{n}.json").write_text(
            json.dumps({**record, "messages": loop["messages"]}, indent=2), encoding="utf-8")
    print(f"{reason:>12}  {loop['steps']:2d} steps  {elapsed:6.1f}s  "
          f"{loop['tok_per_s']:5.1f} tok/s  prompt~{loop['prompt_tokens']}tok")
    if "exc" in loop:
        record["exc"] = loop["exc"]
    return record


def write_summary(run_dir: Path, results: dict[str, list[dict]],
                  tasks: list[AgentTask], attempts: int, max_steps: int,
                  wall_s: int = WALL_S) -> None:
    ranked = []
    for model, rs in results.items():
        npass = sum(1 for r in rs if r["passed"])
        ranked.append({
            "model": model, "npass": npass, "total": len(rs),
            "rate": npass / len(rs) if rs else 0.0,
            "avg_steps": sum(r["steps"] for r in rs) / len(rs) if rs else 0.0,
            "avg_s": sum(r["elapsed_s"] for r in rs) / len(rs) if rs else 0.0,
            "avg_tps": avg_tps(rs),
            "avg_prompt": sum(r["prompt_tokens"] for r in rs) / len(rs) if rs else 0.0,
            "rs": rs,
        })
    ranked.sort(key=lambda r: (-r["rate"], r["avg_steps"], -r["avg_tps"]))

    L = ["# Agent benchmark", "",
         f"- Tasks: {len(tasks)} ({', '.join(t.key for t in tasks)})",
         f"- Attempts per task: {attempts}",
         f"- Limits: {max_steps} steps, {wall_s}s wall clock, {CMD_TIMEOUT}s per command",
         "- Score = fraction of attempts where the task's check passed and the model "
         "ended the loop itself."]
    if any(isinstance(t, AgentTask) for t in tasks):
        L += ["- Shell tasks: the model runs real commands in a sandbox, and hidden tests "
              "it never sees decide the result."]
    if any(isinstance(t, DeskTask) for t in tasks):
        L += ["- Desk tasks: the model works a simulated shop through function tools "
              "under a written policy the tools do not enforce, and the shop's final "
              "records decide the result."]
    L += [""]
    if ranked:
        w = ranked[0]
        L += [f"## 🏆 Winner: `{w['model']}` — "
              f"{w['npass']}/{w['total']} ({w['rate']*100:.0f}%) "
              f"in {w['avg_steps']:.1f} steps", ""]
        runner_up = ranked[1]["rate"] if len(ranked) > 1 else None
        note = close_call_note(w["rate"], runner_up, CLOSE_PTS,
                               f"{(w['rate'] - (runner_up or 0))*100:.0f} pts")
        if note:
            L += [note, ""]
    L += ["| Rank | Model | Pass rate | Passed | Avg steps | Avg s | Tok/s | Avg prompt tok |",
          "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(ranked, 1):
        L.append(f"| {i} | `{r['model']}` | {r['rate']*100:.0f}% | {r['npass']}/{r['total']} | "
                 f"{r['avg_steps']:.1f} | {r['avg_s']:.1f} | {r['avg_tps']:.0f} | "
                 f"{r['avg_prompt']:.0f} |")
    L += ["", "`Avg prompt tok` is the size of the last prompt in an attempt, so it shows "
          "how much context the work used."]

    def task_counts(rs: list[dict], key: str) -> tuple[int, int]:
        mine = [x for x in rs if x["task"] == key]
        return sum(1 for x in mine if x["passed"]), len(mine)

    L += ["", "### Per-task (passed / attempts)", "",
          "| Model | " + " | ".join(t.key for t in tasks) + " |",
          "|---|" + "---|" * len(tasks)]
    for r in ranked:
        cells = ["%d/%d" % task_counts(r["rs"], t.key) for t in tasks]
        L.append(f"| `{r['model']}` | " + " | ".join(cells) + " |")

    L += ["", "### What each task catches", "", "| Task | Failure it catches |", "|---|---|"]
    L += [f"| `{t.key}` | {t.why} |" for t in tasks]

    L += ["", "### Failure shapes observed", "",
          "Counted over failed attempts. `tests-failed` means the model said it was done "
          "and the hidden tests, or a desk task's final records, disagreed. `step-cap`, `wall-clock`, and `context` mean it "
          "never ended the loop, and `(tests passing)` marks the ones where the work was "
          "already correct. `gen-timeout` and `gen-fail` are model calls that did not return.", ""]
    for r in ranked:
        shapes: dict[str, int] = {}
        for x in r["rs"]:
            if not x["passed"]:
                shape = x["reason"] + (" (tests passing)" if x["tests_ok"] else "")
                shapes[shape] = shapes.get(shape, 0) + 1
        listed = ", ".join(f"{k} ×{v}" for k, v in sorted(shapes.items(),
                                                          key=lambda kv: (-kv[1], kv[0])))
        L.append(f"- `{r['model']}`: {listed or 'none'}. Malformed or unknown tool calls: "
                 f"{sum(x['bad_calls'] for x in r['rs'])}.")

    L += ["", "### Uncertainty", "",
          "Pass rate with a 95% Wilson CI, the weakest task, and a small-sample flag. "
          "A pass is all-or-nothing per attempt, so intervals are wide at these counts.", ""]
    for r in ranked:
        rates = {}
        for t in tasks:
            npass, n = task_counts(r["rs"], t.key)
            if n:
                rates[t.key] = npass / n
        bits = [f"{r['rate']*100:.0f}% (95% CI {ci_str(r['npass'], r['total'])})",
                spread_note(rates), sample_caveat(r["total"])]
        L.append(f"- `{r['model']}`: {'; '.join(b for b in bits if b)}")
    (run_dir / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"Summary: {rel_path(run_dir / 'summary.md')}")
    if ranked:
        print(f"Winner:  {ranked[0]['model']} ({ranked[0]['rate']*100:.0f}% pass)")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", required=True, help="router model names (the preset sections)")
    ap.add_argument("--tasks", nargs="+", default=None, help="subset of task keys (default: all)")
    ap.add_argument("--attempts", type=positive_int, default=2, help="attempts per task (default 2)")
    ap.add_argument("--timeout", type=int, default=300, help="per model call timeout (s)")
    ap.add_argument("--max-steps", type=positive_int, default=30,
                    help="model calls allowed per attempt (default 30)")
    ap.add_argument("--wall-clock", type=positive_int, default=WALL_S, metavar="SECONDS",
                    help=f"wall clock allowed per attempt (default {WALL_S}). Raise it for "
                         "a slow model, so slowness is not scored as failure. It applies "
                         "to every model in the run and is printed in the summary.")
    ap.add_argument("--thinking", choices=["auto", "on", "off"], default="off",
                    help="Thinking mode (default off)")
    add_seed_arg(ap)
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    args = ap.parse_args()

    keys = args.tasks or list(TASKS)
    unknown = [k for k in keys if k not in TASKS]
    if unknown:
        print(f"unknown tasks: {', '.join(unknown)}. Known: {', '.join(TASKS)}", file=sys.stderr)
        return 1
    tasks = [TASKS[k] for k in keys]

    # Fail before creating a run dir, not after filling it with zeros.
    preflight(list(args.models))
    needs_sandbox = any(isinstance(t, AgentTask) for t in tasks)
    if needs_sandbox:
        require_sandbox()
    served = require_ctx(list(args.models), REQUIRED_CTX)

    run_dir = new_run_dir(args.out_root) / "agent"
    run_dir.mkdir(parents=True)
    total = len(tasks) * args.attempts
    print(f"Run dir: {rel_path(run_dir)}")
    print(f"Tasks:   {', '.join(keys)}  ({len(tasks)} × {args.attempts} = {total}/model)")
    print(f"Models:  {', '.join(args.models)}")
    print(f"Limits:  {args.max_steps} steps, {args.wall_clock}s wall clock, "
          f"{CMD_TIMEOUT}s per command")
    if needs_sandbox:
        print("Sandbox: bwrap (read-only /usr, no network, no $HOME, no host environment)")
    print()

    results: dict[str, list[dict]] = {}
    for model in args.models:
        print(f"=== {model} ===")
        mdir = run_dir / model
        mdir.mkdir()
        rs: list[dict] = []
        streak = 0  # consecutive failed calls; see after_failure()
        for task in tasks:
            for n in range(1, args.attempts + 1):
                r = run_attempt(model, task, n, args.attempts, args.timeout, args.thinking,
                                served[model], args.max_steps, mdir, args.seed,
                                wall_s=args.wall_clock)
                streak = streak + 1 if "exc" in r else 0
                if "exc" in r:
                    after_failure(resolve_model(model)[0], r["exc"], streak)
                rs.append(r)
        results[model] = rs
        print(f"  -> {sum(1 for r in rs if r['passed'])}/{len(rs)} passed\n")

    write_summary(run_dir, results, tasks, args.attempts, args.max_steps, args.wall_clock)
    return 0


if __name__ == "__main__":
    sys.exit(main())
