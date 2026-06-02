# Implementation Plan - Tutor Eval Phase 2

Status: phase 3 complete; ready for review.

## Request

Build the tutor evaluation path: rebuild tutor models with the filled learning
profile, add an inverted tutor eval where full solutions fail, run
`gemma-tutor` vs `granite-tutor`, and sync the docs.

## Current State

- `PLAN.md` is absent in this repo; `README.md` and `TESTING.md` currently carry
  the project blueprint and testing source of truth.
- `memory/learning-profile.md` has been filled in, including Casey's preference
  for industry implementation context.
- `models/gemma-tutor/system.txt` and `models/granite-tutor/system.txt` include
  `LEARNING PROFILE`, but do not yet include the newest industry-context line.
- `eval/run-learn.py` rewards runnable code, so it is not suitable for the
  tutor behavior where full solutions should fail.
- `TESTING.md` already records Tutor Phase 2 as the next planned work.
- Phase 3 completed: candidate benchmark run; `gemma-tutor` beat `granite-tutor` 9.7/10 vs 9.6/10 with 0/15 leaks.

## Reuse Audit

Searches run:

- `rg -n "judge_scores|JUDGE_TEMPLATE|RUBRIC|JSON_RE|extract_code|run_program|new_run_dir|write_summary|leak|solution|approach|Socratic|fenced|FENCE_RE|dataclass" eval prompts memory README.md TESTING.md`
- `rg -n "LEARNING PROFILE|Learns better when explanations include|Learning Profile" models prompts memory README.md TESTING.md`

Candidates found:

- `eval/_ollama.py`: reusable `generate`, `resolve_model`, `new_run_dir`,
  `tok_per_s`, `FENCE_RE`, `extract_code`, and `run_program`.
- `eval/run-learn.py`: reusable judge-panel shape, JSON parsing pattern,
  leave-one-out loop, output layout, and summary style.
- `eval/learning_tasks.py`: existing `LearnTask`, but its prompt requires a full
  implementation and explanation.
- `eval/coding_tasks.py`: existing hidden tests for runnable Python problems.
- `prompts/roles/tutor.md`: existing behavior contract for the tutor overlay.

Reuse decisions:

- Reuse `_ollama.generate`, `resolve_model`, `new_run_dir`, `tok_per_s`,
  `FENCE_RE`, and `run_program`.
- Reuse the leave-one-out judge pattern from `run-learn.py`, but not its rubric
  because it scores code-producing explanations.
- Reuse coding task hidden tests where possible for objective leak checks, but
  do not reuse the code-only prompts because the tutor prompt must ask for
  guidance rather than a solution.
- Add a new `TutorTask` shape because neither `Task` nor `LearnTask` represents
  "help me learn without solving the task."

## Phase 1 - Rebuild tutor prompts

Goal: Rebuild tutor model artifacts with the current learning profile.

Files to touch:

- `models/gemma-tutor/system.txt`
- `models/gemma-tutor/Modelfile`
- `models/granite-tutor/system.txt`
- `models/granite-tutor/Modelfile`
- `IMPLEMENT.md`

Functions/classes to add or change:

- None.

Commands:

- `./build-gemma-tutor`
- `./build-granite-tutor`

Verification:

- Confirm both tutor `system.txt` files contain the new industry-context
  learning-profile line.
- Confirm non-tutor model prompts do not contain `LEARNING PROFILE`.
- Confirm both build commands exit successfully.

## Phase 2 - Add documented tutor runner

Goal: Add a documented tutor-specific eval runner for leak-gated judge scoring.

Files to touch:

- `eval/tutor_tasks.py`
- `eval/run-tutor.py`
- `README.md`
- `TESTING.md`
- `IMPLEMENT.md`

Functions/classes to add or change:

- Add `TutorTask` in `eval/tutor_tasks.py`.
- Add `judge_scores` in `eval/run-tutor.py`.
- Add `solution_leak_check` in `eval/run-tutor.py`.
- Add `main` in `eval/run-tutor.py`.
- Add `write_summary` in `eval/run-tutor.py`.

Verification:

- Run `python -m py_compile eval/run-tutor.py eval/tutor_tasks.py`.
- Run `./eval/run-tutor.py --help`.
- Run a one-task smoke eval, for example
  `./eval/run-tutor.py --models gemma-tutor --judges granite-tutor --tasks two_sum --attempts 1`.

## Phase 3 - Run tutor benchmark

Goal: Run the tutor benchmark for candidate comparison.

Files to touch:

- `eval/runs/<UTC>/tutor/**`
- `TESTING.md`
- `IMPLEMENT.md`

Functions/classes to add or change:

- None.

Commands:

- `./eval/run-tutor.py --models gemma-tutor granite-tutor --judges gemma-coder granite-coder --attempts 3`

Verification:

- Report the observed `eval/runs/<UTC>/tutor/summary.md` results.
- Record winner, leak rate, rubric score, and timing in `TESTING.md`.
- Confirm `git status --short` only shows planned files.

