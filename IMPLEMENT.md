# IMPLEMENT.md — Big-model testing: `gemma-big` & `qwen-big`

Active per-phase working plan. **TESTING.md stays the results source of truth**;
this file is the execution log for the current effort and gets folded into
TESTING.md when done.

## Request

Test two bigger BatiAI models across all **3 target categories** — content,
tutor, thinking-driver — the same build-and-benchmark way the original `qwen-big`
(27B) was explored. Decide, per category, whether the extra capacity beats the
on-GPU incumbent by enough to justify the speed drop.

## Models (renamed)

| Name | Base | Arch | Size | Triage |
|---|---|---|---|---|
| `gemma-big` | `batiai/gemma4-26b:iq4` | gemma4 dense | 13 GB | 27.8 tok/s · 57% GPU |
| `qwen-big` | `batiai/qwen3.6-35b:q4` | qwen35**moe** | 18 GB | 13.2 tok/s · 42% GPU · thinking-capable |

(`qwen-big` reuses the name freed when the dead 27B dense was retired.)

## Incumbents to beat

- **Content:** `gemma-content` — 100% clean, ~105 tok/s.
- **Tutor:** `gemma-tutor` 9.7 / `granite-tutor` 9.6 /10, 0 leaks.
- **Thinking driver:** `qwen-custom` (thinking-on experimental).

## Design note — one overlay per build

A build bakes a single role overlay, and the categories need different behavior
(`prose` for content, `tutor` for tutoring with its no-leak gate, `coding`+thinking
for the driver). So each base is rebuilt under its `*-big` name with the overlay
for the category under test, **one category at a time** — these are exploratory
models, not permanent per-role builds, so reusing the name across phases is fine.
`num_ctx` kept spill-aware: start 16384 (gemma's sliding-window can go higher; the
MoE's KV is the constraint to watch).

## Reuse audit

- Builders: copy `build-gemma-content` / `build-qwen` (EXTRAS pattern), edit only
  the config block. `EXTRAS=()` to inherit each GGUF's template (verify per base).
- Evals: `run.py` (content), `run-tutor.py` (tutor, leak-gated), `run-code.py`
  (thinking-driver proxy), `run-speed.py` (fit/speed). No new harness needed.
- Tutor builds reuse the role-gated `memory/learning-profile.md` injection.

## Phases

### Phase 1 — Build harness check
- `ollama show --modelfile` both bases → set `EXTRAS` (expect gemma4 renderer/parser
  for gemma-big; embedded `TEMPLATE` + `EXTRAS=()` for the qwen MoE).
- Build a throwaway and confirm both load + generate via a clean API call.
- **Verify:** both respond; correct template (thinking parses for qwen-big).

### Phase 2 — Content (`gemma-big`, `prose`)
- Build `gemma-big` with `ROLE=prose`.
- `./eval/run.py --models gemma-big gemma-content --attempts 3`.
- **Pass bar:** clean-rate ≥ `gemma-content` (100%) with comparable/better quality.

### Phase 3 — Thinking driver (`qwen-big`, `coding` + thinking)
- Build `qwen-big` with `ROLE=coding`; test thinking-on.
- `./eval/run-code.py --models qwen-big:think qwen-custom:think --attempts 3`.
- **Pass bar:** beats `qwen-custom` on pass@1 / reasoning at a usable ~13 tok/s.

### Phase 4 — Tutor (`gemma-big` + `qwen-big`, `tutor`)
- Rebuild both with `ROLE=tutor` (learning-profile auto-injected).
- `./eval/run-tutor.py --models gemma-big qwen-big --judges gemma-coder granite-coder --attempts 3`.
- **Pass bar:** 0 leaks AND teach score ≥ incumbent tutors (9.6–9.7).

### Phase 5 — Decide + record
- Fold all results + per-category verdicts into TESTING.md.
- Keep only models that clear their pass bar by enough to justify the speed cost;
  retire the rest (as the 27B `qwen-big` was). Delete this IMPLEMENT.md after folding.

## Verification rule

Each phase reports the observed `summary.md` numbers (not predicted), plus the
`run-speed` GPU/CPU split whenever speed is in question.

## Status

- [ ] Phase 1 — harness check
- [ ] Phase 2 — content
- [ ] Phase 3 — thinking driver
- [ ] Phase 4 — tutor
- [ ] Phase 5 — decide + record
