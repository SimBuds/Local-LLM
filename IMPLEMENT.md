# IMPLEMENT.md — Execution state

Granular, phase-by-phase tracker. Update this file as part of every commit per the Definition of Done in `AGENTS.md`.

## Current state

The pipeline is built and working, consolidated to **3 purpose-built models** (benchmark-driven): `gemma-custom` (content, `prose` overlay), `granite-custom` (coding assistant + tutor, `coding` overlay), `qwen-custom` (general + thinking-on experimental). All sit 100% on a 10 GB GPU. An eval suite (`eval/`) ranks models for content, coding correctness, and tutoring; results in `eval/RESULTS.md`. `llama-custom` + `ministral-custom` removed. Next: define the coding-tutor feature spec to unblock Phase 19.

## Completed (baseline)

- [x] **P0 — Prompt stack scaffolding**: `prompts/{system,personality,formatting,safety}.md`, `memory/user.md`, `knowledge/` in place.
- [x] **P1 — `build-qwen`**: assembles `models/qwen-custom/{system.txt,Modelfile}` and runs `ollama create`.
- [x] **P2 — `build-granite`**: mirror of `build-qwen` with granite-tuned `PARAMS`.
- [x] **P3 — `build-llama`**: mirror of `build-qwen` with llama-tuned `PARAMS`.
- [x] **P4 — README**: documents structure, build, prompt-assembly order, systemd tuning, thinking-mode helpers.
- [x] **P5 — AGENTS.md workflow contract**: portable 4-pillar contract added.
- [x] **P6 — PLAN.md + IMPLEMENT.md bootstrap**: this phase. Adds the missing two pillars.

## Completed — base-prompt audit → per-model role overlays

**Base-prompt audit → per-model role overlays.** Audit found two gaps in the shared stack:
(1) it's written in agentic actor-voice ("inspect files", "verify the path", "execute steps",
"mark ✓done") but the runtime is a no-tools `ollama run` chat model — those directives are inert
or invite hallucinated actions; (2) the stack is coding-first/brevity-first, which underserves and
actively fights `llama-custom`'s long-form prose role. Decisions taken this session: (a) serve the
prose role via a **per-model prompt overlay** selected in each builder's config block; (b) **rewrite
the agentic directives to advisor-voice**. Phases below.

### Phase 7 — Wire a per-model role overlay into the build pipeline (walking skeleton)
**Files**: `build-qwen`, `build-granite`, `build-llama`, `prompts/roles/coding.md`, `prompts/roles/prose.md`
**Changes**: add `ROLE` to each config block (`coding` for qwen + granite, `prose` for llama); add one
`emit_section "ROLE GUIDANCE" "$AI_ROOT/prompts/roles/$ROLE.md"` to the shared assembly block, placed
after OUTPUT SHAPE and before OPERATIONAL SAFETY, kept byte-identical across all three. Create the two
overlay files as non-empty stubs so the section is emitted (not silently skipped) end-to-end.
**Reuse audit**: searched `ROLE=`, `emit_section`, `overlay`, `prompts/roles`. Found existing
`emit_section()` in all three builders (no-ops on missing file); reusing it — no new helper, no new
public interface. No prior role/overlay mechanism exists.
**Verification**: `bash -n build-{qwen,granite,llama}` clean; shared-assembly region byte-identical
across all three (diff); `./build-qwen` and `./build-llama` produce `system.txt` containing
`=== ROLE GUIDANCE ===` with coding vs prose stub content respectively.
**Status**: [x] done
**Report**: Added `ROLE` to each config block (`coding` for qwen/granite, `prose` for llama) and one
`emit_section "ROLE GUIDANCE" "$AI_ROOT/prompts/roles/$ROLE.md"` to the shared block (slotted after
OUTPUT SHAPE, before OPERATIONAL SAFETY); reused existing `emit_section()`. Created non-empty stub
overlays `prompts/roles/{coding,prose}.md`. Verified: `bash -n` clean ×3; shared-assembly region
byte-identical across all three; rebuilt qwen + llama (exit 0), `system.txt` section list shows ROLE
GUIDANCE at line 73 with coding vs prose content. Deferred: 4-pillar doc sync for the new assembly
step → new Phase 10 (kept out of this phase's file surface).

### Phase 8 — Relocate coding bias + agentic directives into coding.md (advisor-voice); slim the shared core
**Files**: `prompts/roles/coding.md`, `prompts/system.md`, `prompts/formatting.md`
**Changes**: move the coding-first framing (`system.md:5-9`) and the "Agentic behavior" block
(`system.md:23-29`) into `coding.md`, rewriting the agentic items from actor-voice to advisor-voice
(model has no tools: it cannot read files or run commands; it asks Casey to paste file contents /
command output and never claims a command succeeded). Fold the `formatting.md:15` step-outcome line
into the same advisor form. Replace the moved paragraphs in `system.md` with a neutral one-line role
statement; keep the honesty/uncertainty operating principles and "What you don't do" in the shared core.
**Reuse audit**: no new helpers; content relocation only.
**Verification**: rebuild `qwen-custom` + `granite-custom`; assembled `system.txt` carries the coding
guidance under ROLE GUIDANCE, the shared core no longer contains "inspect the relevant files" /
"verify the current path" actor phrasing, and core word-count drops.
**Status**: [x] done
**Report**: Relocated the coding-first paragraph and the entire "## Agentic behavior" block out of
`system.md` (36→26 lines) and the step-outcome line out of `formatting.md` (16→15) into the 16-line
`coding.md` overlay, rewritten in advisor-voice around the no-tools reality (can't read files / run
commands / see output → ask Casey to paste; don't narrate steps as if executed). `system.md`'s role
paragraph replaced with a neutral one-liner pointing at ROLE GUIDANCE. Verified: rebuilt qwen + granite
(exit 0); shared core grep-clean of all four actor-voice phrases; coding guidance present under ROLE
GUIDANCE in both; shared core −11 net lines (3 ins/14 del). Note: assembled total ticked up ~60 words
vs the Phase 7 stub since the real overlay is more explicit than the placeholder — but llama's core
now drops the coding bias (visible after Phase 9 rebuild). Honesty/uncertainty rules and "What you
don't do" remain in the shared core as planned.

### Phase 9 — Author prose.md long-form writing guidance for llama-custom
**Files**: `prompts/roles/prose.md`
**Changes**: replace the stub with prose guidance — for writing tasks, override the brevity-first /
answer-first defaults; calibrate audience and tone; give structure cues for essays / emails / docs;
say when to expand vs. stay tight; keep the no-fabrication rule (no invented facts, quotes, or citations).
**Reuse audit**: none; single new content file.
**Verification**: rebuild `llama-custom`; `system.txt` shows the prose section; `ollama run --think
false llama-custom` on a short writing prompt returns developed prose rather than a terse bullet answer.
**Status**: [x] done
**Report**: Wrote `prose.md` (override terse defaults / craft / still-honest). Rebuilt `llama-custom`
(exit 0); `system.txt` carries the Long-Form Writing section; llama core now has 0 coding-bias markers.
Live check: `ollama run llama-custom "Write a short welcome note…"` returned a developed multi-paragraph
note in connected prose, signed in Casey's voice — prose behavior confirmed. Incidental finding:
`--think false` exits 1 on `llama-custom` (llama3.1 has no thinking mode); plain `ollama run` is correct
— fold a README caveat into Phase 10.

### Phase 10 — Sync the 4-pillar docs to the role-overlay assembly step
**Files**: `PLAN.md`, `README.md`, `CLAUDE.md`
**Changes**: add the ROLE GUIDANCE step (and `prompts/roles/` layer) to the assembly-order lists in
`PLAN.md` and `README.md`; document `ROLE` as the per-model config knob and "Behavior rule, role-specific
→ `prompts/roles/<role>.md`" in CLAUDE.md's "Where changes belong". Deferred from Phase 7 so the docs
describe the layer only once its stubs are populated (Phases 8–9).
**Reuse audit**: none; documentation only.
**Verification**: assembly-order lists in all three docs include ROLE GUIDANCE in the correct position;
`grep -n ROLE` shows the knob documented.
**Status**: [x] done
**Report**: Synced all three pillars. PLAN.md, README.md, CLAUDE.md: added `prompts/roles/$ROLE.md`
as step 4 of the assembly order (renumbered 5–7); added `ROLE` to the config-block lists; added the
role split to "where changes belong"; README structure tree now shows `prompts/roles/{coding,prose}.md`
and the Layer Responsibilities note. Also fixed a latent doc bug surfaced in Phase 9: documented that
`--think`/`--think false` is Qwen-only (errors on granite/llama) in both CLAUDE.md and README. Verified
via grep that the `roles/$ROLE` line, the `ROLE` config knob, and the thinking caveat are present in
each target doc. No code touched. Role-overlay feature complete.

## Completed — model expansion + evaluation suite

### Phase 11 — Add `build-ministral` and `build-gemma` (two new models)
**Files**: `build-ministral`, `build-gemma`, `eval/run.py`
**Changes**: copied `build-qwen` as the baseline, edited only the config block (`MODEL_NAME`, `BASE_MODEL`, `ROLE=coding`, `EXTRAS=()`, `PARAMS`); added both to the eval `DEFAULT_MODELS`.
**Reuse audit**: searched `MODEL_NAME`, `EXTRAS`, builder scripts; reused the mirrored builder structure verbatim — no new mechanism.
**Verification**: `./build-ministral`/`./build-gemma` exit 0; both appear in `ollama list`.
**Status**: [x] done
**Report**: Built `ministral-custom` (`ministral-3:8b`) and `gemma-custom`. Shared-assembly region byte-identical to the other builders. Added both to `DEFAULT_MODELS`.

### Phase 12 — Tune every model to fit 100% on a 10 GB GPU
**Files**: `build-granite`, `build-ministral`, `build-gemma`
**Changes**: trimmed `num_ctx` 16384→12288 on granite + ministral (KV cache spilled to CPU); swapped gemma base `gemma4:e4b`→`gemma4:e2b` (9.6 GB→7.2 GB weights) then set its `num_ctx` to 32768 (light weights + sliding-window KV leave headroom).
**Reuse audit**: none; config-block edits only.
**Verification**: `ollama ps` shows `100% GPU` for all five after loading.
**Status**: [x] done
**Report**: Confirmed via `ollama ps`: granite 8.5 GB, ministral 9.0 GB, gemma 8.0 GB @ 32768 — all 100% GPU. KV-cache quant (`q5_0`) + flash attention already on at the server; `num_ctx` was the only per-model lever.

### Phase 13 — Build the evaluation suite (content + coding correctness)
**Files**: `eval/_ollama.py`, `eval/run.py`, `eval/coding_tasks.py`, `eval/run-code.py`
**Changes**: shared transport/helpers in `_ollama.py`; `run.py` scores SEO content (structure, keyword, no HTML/hedging) with a ranked leaderboard; `run-code.py` extracts the code block and executes it against hidden asserts (real pass@1) over a 6-task battery.
**Reuse audit**: searched `run.py`, `generate`, `score`; factored existing transport into `_ollama.py` rather than duplicate.
**Verification**: smoke runs pass; summaries rank models and name a winner.
**Status**: [x] done
**Report**: Content winner `gemma-custom` (5/5 clean, ~175 tok/s). Coding winner `granite-custom` (29/30 @ 5 attempts); `calc` is the field's discriminator (only granite/ministral clear it).

### Phase 14 — Add the learning/tutor tier (code + explanation, LLM-judged)
**Files**: `eval/learning_tasks.py`, `eval/run-learn.py`, `eval/_ollama.py` (shared `run_program`)
**Changes**: tasks ask for working code **plus** a teaching explanation; two-part score = execution gate + local-judge rubric (approach, complexity, alternative, pitfall, clarity → /10); "teach score" counts explanation only when code passes; two-phase (generate-all then judge-all) to avoid model thrash under `MAX_LOADED_MODELS=1`.
**Reuse audit**: moved `run_program` into `_ollama.py` so coding + learning share one copy.
**Verification**: smoke run parses judge JSON and scores; full run ranks models.
**Status**: [x] done
**Report**: Best tutor `granite-custom` (10.0). `qwen-custom` has the best explanations (10.0) but lowest code-pass (8/12) with thinking off — motivates Phase 15. Caveat logged: judge is itself a model (granite), so scores carry self-bias.

### Phase 15 — Thinking-mode A/B support (`:think` spec) + verdict
**Files**: `eval/_ollama.py`, `eval/run-code.py`, `eval/run-learn.py`
**Changes**: `generate()` gains a `think` arg (default off); new `resolve_model()` maps a `:think` suffix to `(name, think=True)` so `qwen-custom` and `qwen-custom:think` rank as separate entries; runners resolve specs and thread `think` through. Also lowered default `--timeout` 300→120s to cull runaway thinking traces.
**Reuse audit**: single shared helper; no per-runner duplication.
**Verification**: `resolve_model` unit checks pass; A/B run on coding.
**Status**: [x] done
**Report**: Coding A/B — `qwen-custom:think` 87% vs `qwen-custom` 83%, but at **44s/call vs 2.4s (~18×)** and it regressed `lru_cache` 5→4. Verdict: **thinking not worth the latency** for these tasks — `granite-custom` beats thinking-qwen on accuracy (90–97%) *and* is ~24× faster. Thinking kept as a deliberate niche lever (e.g. `calc`), not a default. Default runs use thinking off.

### Phase 16 — Debias the tutor judge with a leave-one-out panel
**Files**: `eval/run-learn.py`
**Changes**: replaced single `--judge` with `--judges` panel (default = all `--models`); each response graded by every judge except the model that wrote it, scores averaged. Judging loops by judge (each loaded once) to avoid thrash; per-judge breakdown persisted per response.
**Reuse audit**: reused `judge_scores` + `resolve_model`; no new transport.
**Verification**: 2-model smoke (each judged only by the other); full 5-model panel run.
**Status**: [x] done
**Report**: Debias **confirmed** granite rather than overturning it — panel-judged best tutor `granite-custom` 9.9/10 (vs 10.0 self-included). Final crowns (debiased, thinking-off): **content → `gemma-custom`** (5/5, 180 tok/s); **coding → `granite-custom`** (90%, 1.7s/call); **teaching → `granite-custom`** (9.9/10). Note: qwen/gemma explain as well as granite (9.8–9.9 when correct) but lose on the code-pass gate.

## Consolidation to 3 purpose-built models

Goal: collapse the 5-model lineup into 3 task models — content, coding-assistant,
coding-tutor — keeping the best base per role (a base may back two roles via
overlays). Decisions taken this session: structure = best-base-per-role; tutor =
deferred until its feature spec exists; assistant = correctness-first. Full
benchmark data + rationale in `eval/RESULTS.md`.

### Phase 17 — Record the benchmark-driven model-selection decision (docs only)
**Files**: `eval/RESULTS.md` (new), `IMPLEMENT.md`
**Changes**: write the canonical results doc — three target roles, the decision (content→gemma, assistant→granite, tutor→deferred/granite-frontrunner), full leaderboards, keep/remove reasoning, open items.
**Reuse audit**: none; documentation only.
**Verification**: `eval/RESULTS.md` lists the 3 roles, the 2 confirmed keepers, and the removal recommendation with reasoning.
**Status**: [x] done
**Report**: Keepers = `gemma-custom` (content), `granite-custom` (coding assistant; tutor frontrunner). Removals recommended: `llama-custom` (wins no axis), `ministral-custom` (redundant #2), with `qwen-custom` flagged to KEEP as the general daily-driver/jobhunt model rather than remove. No models or build scripts deleted yet — destructive removal pending user confirmation (Phase 18).

### Phase 17b — Content overlay switch + qwen thinking-on retention
**Files**: `build-gemma`, `eval/_ollama.py`, `eval/run.py`, `eval/RESULTS.md`, `PLAN.md`, `README.md`
**Changes**: A/B'd gemma content on `coding` vs `prose` overlay (both 5/5 clean, same speed) → switched `gemma-custom` to `prose` (better fit for human-like writing). Kept `qwen-custom`: set `DEFAULT_MODELS` to `qwen-custom:think` so eval tests it thinking-on; added `resolve_model` to `run.py` so it honors `:think`.
**Reuse audit**: reused `resolve_model`; temp builder `build-gemma-prose` created then deleted.
**Verification**: `gemma-custom/system.txt` carries the Long-Form Writing section; overlay A/B summary 5/5 both; temp model `ollama rm`'d.
**Status**: [x] done
**Report**: gemma-custom rebuilt on prose overlay; temp `gemma-prose-test` model + `build-gemma-prose` script removed. qwen retained as the thinking-on experimental model. Docs synced (overlay assignment lists, qwen note, RESULTS open items).

### Phase 18 — Execute removals: `llama-custom` + `ministral-custom` (DESTRUCTIVE — pending confirmation)
**Files**: `build-llama`, `build-ministral` (delete), `eval/_ollama.py` (`DEFAULT_MODELS`), `PLAN.md`, `README.md`, `eval/RESULTS.md`
**Changes**: `ollama rm llama-custom ministral-custom`; delete their build scripts; trim `DEFAULT_MODELS`; sync model tables. (qwen-custom confirmed KEEP — not removed.)
**Reuse audit**: n/a.
**Verification**: `ollama list` no longer shows the two; `git status` shows deleted scripts; docs list only survivors (qwen-think, granite, gemma).
**Status**: [x] done
**Report**: User `ollama rm`'d `llama-custom` + `ministral-custom` (and their bases). Repo cleanup executed: deleted `build-llama`/`build-ministral` and their `models/` dirs; trimmed `DEFAULT_MODELS` to `["qwen-custom:think", "granite-custom", "gemma-custom"]`; fixed the lock-step header comment in the 3 surviving builders; synced model tables/overlay lists/context notes in README, PLAN, AGENTS, RESULTS. `qwen-custom` kept as the thinking-on experimental model.

### Phase 18b — Tune `build-qwen` sampler for thinking quality
**Files**: `build-qwen`, `README.md`
**Changes**: confirmed thinking is on by default at the model level (generate call w/o `think` returns a populated thinking field — no Modelfile knob exists for it). Retuned PARAMS for thinking: `presence_penalty 1.5 → 1.0` (Qwen warns high presence penalty degrades thinking / mixes languages; 1.0 still curbs runaway loops), added `num_predict 8192` (trace + answer headroom within num_ctx 16384). Kept temp 0.6 / top_p 0.95 / top_k 20 / min_p 0 (Qwen's recommended thinking sampling). Documented in the builder comments + README Tuning.
**Reuse audit**: none; PARAMS edit only.
**Verification**: rebuilt — Modelfile carries `presence_penalty 1.0` + `num_predict 8192`; `ollama ps` shows 9.0 GB @ 100% GPU, ctx 16384; default generate still returns a populated thinking field.
**Status**: [x] done
**Report**: qwen-custom rebuilt as the thinking-tuned experimental model. Sets up further thinking benchmarking (eval lineup already pins `qwen-custom:think`).

### Phase 19 — Build the coding-tutor role overlay + benchmark (BLOCKED on tutor spec)
**Files**: `prompts/roles/tutor.md` (new), a tutor builder, `eval/run-learn.py` (feature-specific scoring)
**Changes**: author the `tutor` overlay to the spec; build `granite`(+others) on it; extend the learning benchmark to score the defined teaching features; pick the winner.
**Reuse audit**: reuse `run-learn.py` panel harness.
**Verification**: tutor model built; benchmark ranks tutor candidates on the new features.
**Status**: [ ] blocked — needs the tutor feature definition
**Report**: (pending)

## Backlog (no phase scheduled)

- [ ] **Tools layer**: future `tools/` folder for shell/files/web/git/docker tool calling via the Ollama native API. Out of scope for baseline; promote to a phase when started.
- [ ] **Sessions/transcripts**: `sessions/` directory is reserved but unused.

## Conventions for new work

When starting a non-trivial change:

1. Add a new `## Phase N — <one-sentence goal>` section below.
2. List: files to touch, functions to add/change, verification steps, reuse audit.
3. Get approval before writing code (per `AGENTS.md` Phase 2).
4. Check the box and append a phase report when done.

## Phase template

```
## Phase N — <goal in one declarative sentence, no "and">

**Files**: <paths>
**Changes**: <functions/sections to add or modify>
**Reuse audit**: searched `<terms>`; found `<candidates>`; not reused because `<reason>`.
**Verification**: <≤3 bullets — observed output, not predicted>
**Status**: [ ] pending / [x] done
**Report**: <what changed, what was tested, what docs were updated, what was deferred>
```
