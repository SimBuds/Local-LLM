# AGENTS.md — Workflow contract for this project

Workflow contract for any AI coding agent (Claude Code, Codex, Cursor, Aider, Cline, Copilot, …) operating in this repo. This is a small, single-author prompt-stack project — no `PLAN.md`/`IMPLEMENT.md` phase ceremony. The rules below are the whole contract.

## Documentation architecture

- **`AGENTS.md`** (this file): source of truth for agent behavior and project rules.
- **`README.md`**: what the project is, the model lineup, how to build and run.
- **`TESTING.md`**: the eval suite, leaderboards, the model-selection decision, and testing plans.

Those three are the only durable docs. Don't create `PLAN.md`, `IMPLEMENT.md`, `CLAUDE.md`, or similar — keep the doc set slim.

## How to work

- Restate the request in one sentence. If it has ≥ 2 reasonable interpretations that change the diff, ask before proceeding.
- Read the code/markdown paths involved before editing. Don't guess at file contents.
- **Reuse first.** Before adding a utility, helper, prompt block, or build script, `grep`/`rg` for an existing one and reuse or extend it. The `build-*` scripts in particular are mirrored — only the top config block differs; the assembly below the divider must stay byte-identical across all of them.
- **One thing per change.** No drive-by refactors or "while I'm here" fixes bundled into another change — split them.
- **Keep prompts terse.** Every token in `prompts/` is spent every turn. When a model misbehaves, *remove* rules before adding; sampler tuning (`PARAMS`) is fair game.
- After a change, report what changed, what you verified, and anything deferred — briefly.

## Stop and ask when

- The request is ambiguous in a way that affects the diff.
- A naming / data-shape / API-shape decision will be load-bearing later.
- The action is destructive or hard to reverse: `rm -rf`, `git reset --hard`, force-push, dependency upgrades, or `ollama` model deletes. Confirm before each.

You may proceed without asking on trivial, single-`git revert`-reversible changes, or anything already decided this session.

## Tone

Keep responses tight. State results and decisions directly; don't narrate internal deliberation.

## Project-specific rules

- **Testing** is owned by [`TESTING.md`](TESTING.md) — read it before any eval/benchmark work and record results there. Models are built by the `build-*` scripts; see [`README.md`](README.md) for the prompt-stack architecture and the current model lineup.
