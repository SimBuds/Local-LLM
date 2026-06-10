#!/usr/bin/env python3
"""
Profile wrapper: run a named benchmark profile instead of remembering six
command lines. Profiles are the documented way to run routine comparisons;
the underlying runners stay independently usable for targeted sweeps.

Profiles:
  smoke     fast sanity check after a model rebuild or runner change (~5-10 min)
  standard  the routine full comparison; trims code/content to 3 attempts so the
            expanded task set stays under an hour
  deep      several-hour confidence run: full attempts everywhere plus medium
            and high context-pressure sweeps

Usage:
  ./eval/run-profile.py smoke --models gemma qwen
  ./eval/run-profile.py standard --models gemma qwen
  ./eval/run-profile.py deep --models gemma qwen
  ./eval/run-profile.py standard --models gemma qwen --dry-run   # show commands

Each underlying runner still writes its own eval/runs/<UTC>/<suite>/ directory;
the wrapper lists every summary.md the profile produced at the end. A failed
step does not abort the profile; failures are reported and set the exit code.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
RUNS = EVAL_DIR / "runs"

# Each step: runner script + profile-specific flags (--models is appended).
PROFILES: dict[str, list[list[str]]] = {
    "smoke": [
        ["run-speed.py", "--num-predict", "128"],
        ["run-code.py", "--tasks", "two_sum", "calc", "--attempts", "2"],
        ["run-content.py", "--tasks", "seo_product", "--attempts", "2"],
        ["run-json.py", "--tasks", "jd_extract", "--attempts", "2"],
    ],
    "standard": [
        ["run-speed.py"],
        ["run-code.py", "--attempts", "3"],
        ["run-content.py", "--attempts", "3"],
        ["run-json.py"],
        ["run-learn.py"],
        ["run-tutor.py"],
    ],
    "deep": [
        ["run-speed.py"],
        ["run-code.py", "--attempts", "5"],
        ["run-content.py", "--attempts", "5"],
        ["run-json.py", "--attempts", "5"],
        ["run-json.py", "--context-pressure", "medium", "--attempts", "2"],
        ["run-json.py", "--context-pressure", "high", "--attempts", "1"],
        ["run-learn.py", "--attempts", "5"],
        ["run-tutor.py", "--attempts", "5"],
    ],
}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile", choices=list(PROFILES))
    ap.add_argument("--models", nargs="+", required=True, help="Ollama model names")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the underlying commands without running them")
    args = ap.parse_args()

    cmds = [[sys.executable, str(EVAL_DIR / step[0]), "--models", *args.models,
             *step[1:]] for step in PROFILES[args.profile]]
    if args.dry_run:
        for c in cmds:
            print(" ".join(c[1:2] + c[2:]))  # skip the interpreter for readability
        return 0

    before = {p.name for p in RUNS.iterdir()} if RUNS.is_dir() else set()
    t0 = time.monotonic()
    failures: list[str] = []
    for i, (step, cmd) in enumerate(zip(PROFILES[args.profile], cmds), 1):
        label = " ".join(step)
        print(f"\n##### [{i}/{len(cmds)}] {label}\n", flush=True)
        rc = subprocess.run(cmd).returncode
        if rc != 0:
            failures.append(f"{label} (exit {rc})")
            print(f"##### step failed (exit {rc}) — continuing\n", file=sys.stderr)

    mins = (time.monotonic() - t0) / 60
    print(f"\n===== profile '{args.profile}' finished in {mins:.1f} min =====")
    new = sorted({p.name for p in RUNS.iterdir()} - before) if RUNS.is_dir() else []
    print("Summaries:")
    for d in new:
        for s in sorted((RUNS / d).glob("*/summary.md")):
            print(f"  {s.relative_to(EVAL_DIR.parent)}")
    if failures:
        print(f"FAILED steps: {'; '.join(failures)}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
