#!/usr/bin/env python3
"""
Pi harness benchmark: the shell tasks from run-agent.py, worked by the Pi coding
agent inside its VM guest instead of by this repo's own tool loop. The project
is copied into the guest, `pi -p` works on it there, the project is copied back,
and the same hidden tests decide the result.

This compares harnesses, not models alone. Under Pi the model gets Pi's system
prompt and four tools (read, bash, edit, write), thinks if its template says so,
and has prompt caching on. run-agent.py gives it one bash tool, this repo's
prompt, thinking off and caching off. The samplers are the same: Pi sends none,
so the server.ini baseline applies.

The model calls are Pi's own, made from the guest through an SSH reverse tunnel.
They do not go through eval/_gateway.py (recorded exception in AGENTS.md). The
gateway still does the preflight and the context check, and the hidden tests run
in its sandbox on the host.

Usage:
  ./eval/run-pi.py --models gemma qwen lite
  ./eval/run-pi.py --models qwen --tasks fix_failing_test --attempts 1
  ./eval/run-pi.py --models qwen --out-root /tmp/check    # results outside eval/runs/

Needs the Pi VM running (README, *Use With Pi*) and a key login to it.

Output:
  eval/runs/<UTC>/pi/
    summary.md
    <model>/<task>-attempt-<n>.json     # Pi's events, stop reason, score

promote.py does not read the `pi` folder, so this never reaches the leaderboard.

SAFETY: model-written commands run inside the guest, never on the host. What
comes back is unpacked with Python's "data" tar filter, which refuses links and
paths that leave the folder, and the hidden tests then run under bubblewrap.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _gateway import (  # noqa: E402
    LLM_URL, REPO_ROOT, new_run_dir, positive_int, preflight, rel_path,
)
from agent_tasks import TASKS, AgentTask  # noqa: E402

# run-agent.py owns the scoring and the checks. Its name has a hyphen, so it is
# loaded by path, the way eval/test_agent.py loads it.
_spec = importlib.util.spec_from_file_location(
    "run_agent", Path(__file__).resolve().parent / "run-agent.py")
agent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(agent)

DEFAULT_OUT_ROOT = REPO_ROOT / "eval" / "runs"
SHELL_TASKS = {k: t for k, t in TASKS.items() if isinstance(t, AgentTask)}
PROVIDER = "local-llm"           # the provider name in pi/models.json
PI_BIN = "$HOME/.pi/agent/bin/pi"  # not on PATH for a non-interactive ssh command
GUEST_ROOT = "pi-run"            # under the guest's home, one folder per attempt
GUEST_TUNNEL_PORT = 8080         # the port pi/models.json points at
SSH_SLACK_S = 60                 # copy and login time allowed on top of the wall clock
KILL_AFTER_S = 5                 # timeout's grace before SIGKILL
MAX_ERRORS_IN_A_ROW = 3          # a dead guest or model stops the model's run
STDERR_KEEP = 2000


class Guest:
    """Runs a shell script in the guest. `argv` is everything before the script:
    the ssh command for the real guest, or `bash -c` under a scratch HOME in the
    unit tests."""

    def __init__(self, argv: list[str]):
        self.argv = argv

    def run(self, script: str, stdin: bytes = b"", timeout: int = 60) -> tuple[int | None, bytes, str]:
        """(exit code or None on a host-side timeout, stdout, stderr text)."""
        try:
            p = subprocess.run(self.argv + [script], input=stdin, capture_output=True,
                               timeout=timeout)
        except subprocess.TimeoutExpired as e:
            return None, e.stdout or b"", (e.stderr or b"").decode("utf-8", "replace")
        return p.returncode, p.stdout, p.stderr.decode("utf-8", "replace")


def ssh_guest(host: str, port: int, key: Path, router_port: int) -> Guest:
    return Guest([
        "ssh", "-p", str(port), "-i", str(key),
        "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        # Another session may already hold the tunnel. The forward then fails
        # with a warning and the existing one is used, which check_guest proves.
        "-R", f"{GUEST_TUNNEL_PORT}:localhost:{router_port}",
        host,
    ])


def check_guest(guest: Guest) -> None:
    """Stop before any work when the guest, Pi, or the tunnel is not there."""
    script = (f"{PI_BIN} --version && "
              f"wget -q -T 10 -t 1 -O /dev/null http://localhost:{GUEST_TUNNEL_PORT}/v1/models")
    code, out, err = guest.run(script, timeout=60)
    if code != 0:
        raise SystemExit(
            "ERROR: the Pi guest is not ready.\n"
            f"  exit {code}: {(err or out.decode('utf-8', 'replace')).strip()[-500:]}\n"
            "  Boot the VM and check the key login and the tunnel (README, Use With Pi).")


def pack(task: AgentTask) -> bytes:
    """A tar of one attempt: project/<the task's files> and instruction.txt beside it."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        def add(name: str, text: str) -> None:
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
        project = tarfile.TarInfo("project")
        project.type = tarfile.DIRTYPE
        project.mode = 0o755
        tar.addfile(project)
        for rel, content in task.files.items():
            add(f"project/{rel}", content)
        add("instruction.txt", task.instruction)
    return buf.getvalue()


def unpack(data: bytes, workdir: str) -> None:
    """Unpack the project the guest sent back. The "data" filter refuses absolute
    paths, `..`, device files, and links that point outside `workdir`."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        tar.extractall(workdir, filter="data")


def parse_events(stdout: bytes) -> dict:
    """Counts from `pi --mode json`: one JSON event per line."""
    turns = tools = tool_errors = 0
    settled = None
    kept = []
    for line in stdout.decode("utf-8", "replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "turn_start":
            turns += 1
        elif kind == "tool_execution_start":
            tools += 1
        elif kind == "tool_execution_end" and event.get("isError"):
            tool_errors += 1
        elif kind == "agent_settled":
            settled = not event.get("aborted", False)
        # message_update repeats the growing message on every token, and the
        # start, end and agent_end events repeat whole messages again.
        if kind in ("tool_execution_start", "tool_execution_end", "turn_end", "agent_settled"):
            kept.append(event)
    return {"turns": turns, "tools": tools, "tool_errors": tool_errors,
            "settled": settled, "events": kept}


def stop_reason(code: int | None, settled: bool | None) -> str:
    if code is None or code in (124, 137):
        return "wall-clock"
    if code == 0 and settled:
        return "done"
    return "error"


def reason_for(stop: str, tests_ok: bool) -> str:
    if stop != "done":
        return stop
    return "pass" if tests_ok else "tests-failed"


def run_attempt(guest: Guest, model: str, task: AgentTask, n: int, total: int,
                out_dir: Path, wall_s: int, run_id: str) -> dict:
    print(f"    {task.key:22s} [{n}/{total}] ", end="", flush=True)
    # Under $HOME by name: an ssh command starts there, but nothing else has to.
    folder = '"$HOME"/' + shlex.quote(f"{GUEST_ROOT}/{run_id}-{model}-{task.key}-{n}")
    setup = f"rm -rf {folder} && mkdir -p {folder} && tar -x -C {folder}"
    # stdin is closed for Pi: with an open pipe it waits for input and never starts.
    work = (f"cd {folder}/project && timeout -k {KILL_AFTER_S} {wall_s} {PI_BIN} "
            f"--offline --no-session --no-context-files --mode json "
            f"--model {shlex.quote(f'{PROVIDER}/{model}')} "
            f'-p "$(cat ../instruction.txt)" < /dev/null')
    fetch = f"tar -c -C {folder}/project ."
    cleanup = f"rm -rf {folder}"

    t0 = time.monotonic()
    code, out, err = guest.run(setup, stdin=pack(task), timeout=SSH_SLACK_S)
    if code != 0:
        stop, parsed, tests_ok = "error", parse_events(b""), False
        err = f"setup failed, exit {code}: {err}"
    else:
        code, out, err = guest.run(work, timeout=wall_s + SSH_SLACK_S)
        parsed = parse_events(out)
        stop = stop_reason(code, parsed["settled"])
        tests_ok = False
        if stop != "error":
            fcode, tarball, ferr = guest.run(fetch, timeout=SSH_SLACK_S)
            if fcode != 0:
                stop, err = "error", f"copy back failed, exit {fcode}: {ferr}"
            else:
                with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workdir:
                    try:
                        unpack(tarball, workdir)
                    except (tarfile.TarError, OSError) as e:
                        stop, err = "error", f"unsafe or unreadable project archive: {e}"
                    else:
                        tests_ok = agent.score(task, workdir)
    elapsed = time.monotonic() - t0
    guest.run(cleanup, timeout=SSH_SLACK_S)

    reason = reason_for(stop, tests_ok)
    record = {
        "task": task.key, "model": model, "attempt": n, "passed": reason == "pass",
        "reason": reason, "stop": stop, "tests_ok": tests_ok, "pi_exit": code,
        "turns": parsed["turns"], "tool_calls": parsed["tools"],
        "tool_errors": parsed["tool_errors"], "elapsed_s": elapsed,
    }
    if stop == "error":
        record["stderr"] = err[-STDERR_KEEP:]
    (out_dir / f"{task.key}-attempt-{n}.json").write_text(
        json.dumps({**record, "events": parsed["events"]}, indent=2), encoding="utf-8")
    print(f"{reason:>12}  {parsed['turns']:2d} turns  {parsed['tools']:2d} tool calls  "
          f"{elapsed:6.1f}s")
    return record


def write_summary(run_dir: Path, results: dict[str, list[dict]], tasks: list[AgentTask],
                  attempts: int, wall_s: int, pi_version: str) -> None:
    def avg(rs: list[dict], key: str) -> float:
        return sum(r[key] for r in rs) / len(rs) if rs else 0.0

    ranked = sorted(results.items(),
                    key=lambda kv: (-sum(r["passed"] for r in kv[1]), avg(kv[1], "turns")))
    L = ["# Pi harness benchmark", "",
         f"- Harness: Pi {pi_version}, in the VM guest, one `pi -p` run per attempt",
         f"- Tasks: {len(tasks)} ({', '.join(t.key for t in tasks)})",
         f"- Attempts per task: {attempts}",
         f"- Limit: {wall_s}s wall clock per attempt. Pi sets its own step and command limits.",
         "- Score = fraction of attempts where the hidden tests passed and Pi ended the "
         "run itself.",
         "- Not like for like with `run-agent.py`: Pi uses its own prompt and four tools, "
         "leaves thinking at the template default, and has prompt caching on.",
         "- The first attempt for each model includes the model's load time.",
         "",
         "| Rank | Model | Pass rate | Passed | Avg turns | Avg tool calls | Tool errors | Avg s |",
         "|---|---|---|---|---|---|---|---|"]
    for i, (model, rs) in enumerate(ranked, 1):
        npass = sum(r["passed"] for r in rs)
        rate = npass / len(rs) if rs else 0.0
        L.append(f"| {i} | `{model}` | {rate*100:.0f}% | {npass}/{len(rs)} | "
                 f"{avg(rs, 'turns'):.1f} | {avg(rs, 'tool_calls'):.1f} | "
                 f"{sum(r['tool_errors'] for r in rs)} | {avg(rs, 'elapsed_s'):.1f} |")
    L += ["", "### Per-task (passed / attempts)", "",
          "| Model | " + " | ".join(t.key for t in tasks) + " |",
          "|---|" + "---|" * len(tasks)]
    for model, rs in ranked:
        cells = []
        for t in tasks:
            mine = [r for r in rs if r["task"] == t.key]
            cells.append(f"{sum(r['passed'] for r in mine)}/{len(mine)}")
        L.append(f"| `{model}` | " + " | ".join(cells) + " |")
    failures = [(m, r) for m, rs in ranked for r in rs if not r["passed"]]
    if failures:
        L += ["", "### Failures", ""]
        L += [f"- `{m}` {r['task']} attempt {r['attempt']}: {r['reason']}" for m, r in failures]
    (run_dir / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", required=True, help="router model names (the preset sections)")
    ap.add_argument("--tasks", nargs="+", default=None,
                    help="subset of the shell task keys (default: all eight)")
    ap.add_argument("--attempts", type=positive_int, default=2, help="attempts per task (default 2)")
    ap.add_argument("--wall-clock", type=positive_int, default=agent.WALL_S, metavar="SECONDS",
                    help=f"wall clock allowed per attempt (default {agent.WALL_S})")
    ap.add_argument("--guest", default="pi@localhost", help="ssh destination of the Pi guest")
    ap.add_argument("--ssh-port", type=positive_int, default=22222)
    ap.add_argument("--ssh-key", type=Path, default=Path.home() / ".ssh" / "pi_vm_ed25519")
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    args = ap.parse_args()

    keys = args.tasks or list(SHELL_TASKS)
    unknown = [k for k in keys if k not in SHELL_TASKS]
    if unknown:
        print(f"unknown or non-shell tasks: {', '.join(unknown)}. "
              f"Known: {', '.join(SHELL_TASKS)}", file=sys.stderr)
        return 1
    tasks = [SHELL_TASKS[k] for k in keys]

    # Fail before creating a run dir, not after filling it with zeros.
    preflight(list(args.models))
    agent.require_sandbox()
    agent.require_ctx(list(args.models), agent.REQUIRED_CTX)
    router_port = urllib.parse.urlparse(LLM_URL).port or 80
    guest = ssh_guest(args.guest, args.ssh_port, args.ssh_key, router_port)
    check_guest(guest)
    _, version_out, _ = guest.run(f"{PI_BIN} --version", timeout=60)
    pi_version = version_out.decode("utf-8", "replace").strip() or "unknown"

    run_root = new_run_dir(args.out_root)
    run_dir = run_root / "pi"
    run_dir.mkdir(parents=True)
    total = len(tasks) * args.attempts
    print(f"Run dir: {rel_path(run_dir)}")
    print(f"Harness: Pi {pi_version} at {args.guest}:{args.ssh_port}")
    print(f"Tasks:   {', '.join(keys)}  ({len(tasks)} × {args.attempts} = {total}/model)")
    print(f"Models:  {', '.join(args.models)}")
    print(f"Limit:   {args.wall_clock}s wall clock per attempt\n")

    results: dict[str, list[dict]] = {}
    for model in args.models:
        print(f"=== {model} ===")
        mdir = run_dir / model
        mdir.mkdir()
        rs: list[dict] = []
        streak = 0
        for task in tasks:
            for n in range(1, args.attempts + 1):
                r = run_attempt(guest, model, task, n, args.attempts, mdir,
                                args.wall_clock, run_root.name)
                rs.append(r)
                streak = streak + 1 if r["stop"] == "error" else 0
                if streak >= MAX_ERRORS_IN_A_ROW:
                    raise SystemExit(
                        f"ERROR: {streak} attempts in a row ended in an error for {model}. "
                        f"Last stderr:\n{r.get('stderr', '')}\n"
                        f"  The partial results are in {rel_path(run_dir)} and are not a score.")
        results[model] = rs
        print(f"  -> {sum(1 for r in rs if r['passed'])}/{len(rs)} passed\n")

    write_summary(run_dir, results, tasks, args.attempts, args.wall_clock, pi_version)
    print(f"Summary: {rel_path(run_dir / 'summary.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
