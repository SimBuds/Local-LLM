# AGENTS.md — Workflow contract for this project

This file is the workflow contract for any AI coding agent (Claude Code, Codex, Cursor, Aider, Cline, Copilot, or any other tool that reads `AGENTS.md`) operating in this repository. It is project-agnostic and intentionally portable — drop this file into the root of any repo and it applies as-is. Project-specific rules go in a `## Project-specific rules` section appended at the bottom.

## Precedence

1. Anything under the `## Project-specific rules` section of *this* file.
2. The workflow contract in the rest of *this* file.
3. The agent's built-in defaults.

If a project-specific rule conflicts with the workflow contract, the project-specific rule wins for that topic only — the rest of the contract still applies. The agent must not silently relax a contract rule; if a project rule isn't explicit, the contract holds.

---

## The Core Documentation Architecture

This project strictly adheres to a 4-pillar documentation system. You must read from and write to these files continuously to maintain context and prevent hallucination. Do not rely on conversational memory.

1. **`AGENTS.md`**: The absolute source of truth for agent behavior, workflow constraints, and project-specific rules. (You are reading it now).
2. **`PLAN.md`**: The high-level blueprint. Contains the full idea of the application, core features, architecture decisions, and scope.
3. **`README.md`**: The developer-facing and user-facing entry point. Explains what the application is, how it works, and how to run it.
4. **`IMPLEMENT.md`**: The execution engine. Contains the granular, phase-by-phase breakdown of tasks, checkboxes for progress, and current state. 

---

## The Workflow Contract

All non-trivial work runs through these five phases. "Non-trivial" is defined by the Blast-radius tiers section below; trivial-tier work skips to Phase 4.

### Phase 1 — Understand & Sync
- Restate the user's request in one sentence.
- **Mandatory Read:** Read `PLAN.md` (to understand the broader feature) and `IMPLEMENT.md` (to see where we are in the execution). Do not guess at project state.
- Identify ambiguity. If the request has ≥ 2 reasonable interpretations, ask before proceeding.
- Read the code paths involved. Do not guess at file contents.

### Phase 2 — Plan & Document
- Update or create `IMPLEMENT.md`. The plan is **never** left just in the conversational context.
- The `IMPLEMENT.md` file must list the **phases**, each with: a goal sentence, files to touch, functions to add/change, and verification steps.
- Surface the **reuse audit** (see Reuse-first rule) for every new function/class/component proposed.
- Ask the user to approve the updated `IMPLEMENT.md` before any code is written.

### Phase 3 — Phase the Work (Context Anchoring)
- Break the plan into phases that each pass the Phase-sizing rules below.
- At the start of Phase 3 — and at the start of *every* subsequent phase — check `IMPLEMENT.md` to verify current state.
- Re-state in 3–6 bullets:
  - The inherited decisions (every choice the user has made so far in this session).
  - The current state based on `IMPLEMENT.md`: phases done, phase in progress, phases remaining.

### Phase 4 — Execute One Phase
- One phase at a time. No look-ahead edits into later phases.
- Honor the Surface-first audit. Touching a file or function not explicitly listed in the current phase of `IMPLEMENT.md` is a fatal scope error.
- If a decision arises mid-phase that wasn't covered by the plan, stop and ask. Do not silently choose.

### Phase 5 — Verify & Hand Back
- Run the verification listed in `IMPLEMENT.md` for this phase. Report observed output, not predicted output.
- Satisfy the **Definition of Done** (below) before claiming completion.
- End the turn with the literal handoff line, and **no tool calls after it**:

  > `Phase <N> complete. Do I have approval to begin Phase <N+1>?`

  On the final phase, use:

  > `Phase <N> complete. Do I have approval to mark this work complete?`

  Pauses without this line count as incomplete phases. This is the only sanctioned way to yield control.

---

## Phase-Sizing Rules

A phase is "small enough" only if **all** of the following hold. If any fails, split the phase in `IMPLEMENT.md`.

- **One-sentence test.** The phase's goal fits in one declarative sentence with no "and". If you need "and", that's two phases.
- **Diff-surface budget.** ≤ ~300 lines changed, ≤ 5 files touched, ≤ 1 new public interface. Defaults, not hard limits — exceeding any requires an explicit note in the plan justifying why splitting is worse.
- **Single test plan.** Verification fits in ≤ 3 bullets. If you need 5 bullets to describe what to test, the phase is doing too much.
- **Atomic revert.** The phase is a single commit that can be reverted without breaking the build or leaving the repo half-done.
- **Walking-skeleton bias.** The first phase delivers the thinnest possible end-to-end path, even if shallow. Later phases thicken. Don't build all of layer A before any of layer B.
- **Surface-first audit (hard stop).** Before writing code, list the files you will touch and the functions you will add/change. Touching anything outside that list is a fatal scope error: immediately revert the unplanned change, pause execution, and ask for permission to expand the surface.
- **No piggybacking.** A phase does its one thing. Refactors, drive-by cleanups, "while I'm here" fixes go into their own phases.

---

## Reuse-First Rule

Before introducing a new utility, class, component, or helper, run a concrete search (`grep`, `rg`, or equivalent) for existing implementations in the project and in any referenced shared libraries. In the plan, state:
- (a) the exact search terms used,
- (b) the candidates found,
- (c) why each candidate cannot be reused.
"I didn't see one" is not a valid answer. The search itself must be shown.

---

## Definition of Done (Per Phase)

A phase is strictly incomplete until **all** of the following are true:

1. The code change matches the planned diff surface in `IMPLEMENT.md` — no extras.
2. New behavior has at least one test that fails without the change and passes with it (or manual E2E output is reported).
3. Existing tests still pass, or you have explicitly enumerated which broke and why.
4. **The 4-Pillar Documentation Check (Critical Step):** - **`IMPLEMENT.md`** must be updated to check off the current phase and log any deferred work as new phases.
   - **`PLAN.md`** must be updated if the architecture, core data structures, or feature scope changed during the phase.
   - **`README.md`** must be updated if running instructions, env vars, or developer/user-facing APIs changed.
   - *Code shipped without updating the relevant markdown files fails the Definition of Done.*
5. You have posted a phase report: *what changed, what was tested, what docs were updated, what was deferred*. (Deferred items go into `IMPLEMENT.md` as follow-up phases, never as `TODO` comments in code).
6. The user has approved before the next phase begins.

---

## Decision Gates — When to Stop and Ask

You **MUST** ask, not assume, when:
- The user's request has ≥ 2 reasonable interpretations and the choice affects the diff.
- A naming, data-shape, or API-shape decision will be load-bearing for later phases.
- The change crosses into the **risky** blast-radius tier.
- You discover mid-phase that the `IMPLEMENT.md` plan was wrong. Surface the discovery and re-plan; don't silently adapt.

You **MAY** proceed without asking when:
- The change is trivial-tier and reversible by a single `git revert`.
- The user has already answered the same question in this session or in this `AGENTS.md` file.

When in doubt, present the options as a multiple-choice question with a recommended default and the tradeoff for each. Do not invent a single path forward when a meaningful fork exists.

---

## Blast-Radius Tiers

- **Trivial** — single-file, ≤ 20 lines, no public API change, no shared-state effect. Examples: typo fixes, comment edits, renaming a local variable. Proceed and report in one sentence.
- **Standard** — multi-file or new function, but contained to one module. Tests run locally. Use the full phase contract: plan → execute one phase → verify → update docs → hand back for approval.
- **Risky** — destructive ops (`rm -rf`, `git reset --hard`, force-push), schema/migration changes, dependency upgrades, CI/CD edits, modifications to shared infrastructure. Stop and ask before *each* such action, even within an approved plan.

---

## Anti-Patterns (Strictly Prohibited)

- "While I was in there I also…" — scope creep. Defer or split.
- "I'll add a TODO for that" — silent debt. Put it in `IMPLEMENT.md` as a phase.
- "The tests probably still pass" — run them.
- "I'll mock this for now" — say so loudly; mocks default to phase-end removal.
- "I'll document it later" — updating `PLAN.md`, `README.md`, and `IMPLEMENT.md` is part of the code commit. 
- Ending a phase without the literal handoff line.
- Bundling a refactor into a bugfix, or a bugfix into a feature.

---

## Notes on Tone

Keep responses tight. State results and decisions directly. Don't narrate internal deliberation. The phase report and the handoff line are the contract — everything else is optional.

---

## Project-Specific Rules

*Everything above this line is the shared workflow contract and should not be edited per-project. Add project-specific guidance below — stack, build/test commands, conventions, paths to other docs, domain rules.*