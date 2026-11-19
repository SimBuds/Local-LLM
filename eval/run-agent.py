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
  ./eval/run-agent.py --models gemma qwen lite freedom
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
    close_call_note, get_effective_think, new_run_dir, positive_int, preflight, rel_path,
    resolve_model, sandbox_exec, seed_opts, served_ctx, tok_per_s,
)
from agent_tasks import TASKS, AgentTask  # noqa: E402

DEFAULT_OUT_ROOT = REPO_ROOT / "eval" / "runs"
PROMPT = (Path(__file__).resolve().parent / "prompts" / "agent-system.md").read_text(encoding="utf-8")

CLOSE_PTS = 0.05       # pass-rate gaps within 5 points are a tie
WALL_S = 600           # wall clock for one attempt's loop
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


def run_loop(name: str, think: bool, task: AgentTask, workdir: str, options: dict,
             timeout: int, deadline: float, n_ctx: int, max_steps: int) -> dict:
    """Drive one attempt until the model stops, a limit is hit, or a call fails.

    `stop` is one of: done, step-cap, wall-clock, context, gen-timeout, gen-fail.
    """
    messages: list[dict] = [{"role": "user", "content": task.instruction}]
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
                                     think=think, options=options, tools=TOOLS, system=PROMPT)
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
            result, well_formed = execute_call(c, workdir)
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


def run_attempt(model: str, task: AgentTask, n: int, total: int, timeout: int,
                thinking_mode: str, n_ctx: int, max_steps: int, out_dir: Path,
                seed: int | None = None) -> dict:
    print(f"    {task.key:22s} [{n}/{total}] ", end="", flush=True)
    name, model_think = resolve_model(model)
    think = get_effective_think(thinking_mode, model_think)
    options = seed_opts(attempt_seed(seed, n), {"num_predict": NUM_PREDICT})

    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workdir:
        # Seed files are written before the model acts. Nothing on the host
        # writes into this directory afterwards: a model-made symlink could
        # redirect a later host-side write outside the sandbox.
        for rel, content in task.files.items():
            path = Path(workdir) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        loop = run_loop(name, think, task, workdir, options, timeout,
                        t0 + WALL_S, n_ctx, max_steps)
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
                  tasks: list[AgentTask], attempts: int, max_steps: int) -> None:
    ranked = []
    for model, rs in results.items():
        npass = sum(1 for r in rs if r["passed"])
        ranked.append({
            "model": model, "npass": npass, "total": len(rs),
            "rate": npass / len(rs) if rs else 0.0,
            "avg_steps": sum(r["steps"] for r in rs) / len(rs) if rs else 0.0,
            "avg_s": sum(r["elapsed_s"] for r in rs) / len(rs) if rs else 0.0,
            "avg_tps": avg_tps(rs),
        })
    ranked.sort(key=lambda r: (-r["rate"], r["avg_steps"], -r["avg_tps"]))

    L = ["# Agent benchmark", "",
         f"- Tasks: {len(tasks)} ({', '.join(t.key for t in tasks)})",
         f"- Attempts per task: {attempts}",
         f"- Limits: {max_steps} steps, {WALL_S}s wall clock, {CMD_TIMEOUT}s per command",
         "- Score = fraction of attempts where the hidden tests passed and the model "
         "ended the loop itself. Commands really run, in a sandbox.", ""]
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
    L += ["| Rank | Model | Pass rate | Passed | Avg steps | Avg s | Tok/s |",
          "|---|---|---|---|---|---|---|"]
    for i, r in enumerate(ranked, 1):
        L.append(f"| {i} | `{r['model']}` | {r['rate']*100:.0f}% | {r['npass']}/{r['total']} | "
                 f"{r['avg_steps']:.1f} | {r['avg_s']:.1f} | {r['avg_tps']:.0f} |")
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
    require_sandbox()
    served = require_ctx(list(args.models), REQUIRED_CTX)

    run_dir = new_run_dir(args.out_root) / "agent"
    run_dir.mkdir(parents=True)
    total = len(tasks) * args.attempts
    print(f"Run dir: {rel_path(run_dir)}")
    print(f"Tasks:   {', '.join(keys)}  ({len(tasks)} × {args.attempts} = {total}/model)")
    print(f"Models:  {', '.join(args.models)}")
    print(f"Limits:  {args.max_steps} steps, {WALL_S}s wall clock, {CMD_TIMEOUT}s per command")
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
                                served[model], args.max_steps, mdir, args.seed)
                streak = streak + 1 if "exc" in r else 0
                if "exc" in r:
                    after_failure(resolve_model(model)[0], r["exc"], streak)
                rs.append(r)
        results[model] = rs
        print(f"  -> {sum(1 for r in rs if r['passed'])}/{len(rs)} passed\n")

    write_summary(run_dir, results, tasks, args.attempts, args.max_steps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
