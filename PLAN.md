# PLAN.md — Blueprint

## What this is

A build pipeline that compiles layered Markdown into customized Ollama models,
plus an evaluation suite that decides which model to use for which job. There is
no application code; the products are (1) the generated `system.txt` + `Modelfile`
per model, and (2) the eval harness under `eval/`.

## Core idea

Customize base models via **prompt + reference context only** — never weight
fine-tuning. Edit Markdown, run a builder, `ollama create` produces a local
model. Then measure each model empirically rather than guessing which is best.

## Models

Three purpose-built models (one per task, chosen by benchmark — see
`eval/RESULTS.md`), all built from one neutral prompt stack plus a per-model
**role overlay** (`coding` or `prose`) selected by each builder's `ROLE`.

| Name             | Base            | num_ctx | Role / best-for                              |
|------------------|-----------------|---------|----------------------------------------------|
| `gemma-custom`   | `gemma4:e2b`    | 32768   | **Content generation** (`prose` overlay); best content scorer, ~175 tok/s, large context |
| `granite-custom` | `granite4.1:8b` | 12288   | **Coding assistant + tutor** (`coding` overlay); top code-correctness + tutor scorer |
| `qwen-custom`    | `qwen3.5:9b`    | 16384   | General daily driver + **thinking-on experimental** model; only model with runtime thinking mode |

Removed after benchmarking (won no role): `llama-custom`, `ministral-custom`.
Project-specific overlays (e.g. SEO rules) are injected at request time by the
consuming app, not baked in.

### Why these three, and why these settings

- **Hardware constraint: one RTX 3080, 10 GB VRAM.** Every model is tuned to sit
  **100% on GPU** (no CPU spill). The lever is `num_ctx` (KV cache scales with
  context); `OLLAMA_KV_CACHE_TYPE=q5_0` + flash attention are already on at the
  server, so context length is the remaining per-model knob.
- `granite-custom` overflowed 10 GB at 16384 → trimmed to **12288** to fit.
  `qwen-custom` already fits at 16384.
- `gemma-custom` originally used `gemma4:e4b` (9.6 GB weights — too big to fully
  offload). **Swapped to `gemma4:e2b`** (7.2 GB); its light weights + Gemma 3n
  sliding-window attention make KV cheap, so it runs at **32768** and still fits.

## Evaluation — the two questions this repo answers

The eval suite exists to pick the right model for two real jobs:

1. **Best content/SEO model** — `eval/run.py`. Scores format + instruction
   discipline (structure, keyword use, no HTML/hedging) on an SEO prompt.
   **Finding: `gemma-custom`** wins repeatably (5/5 clean) *and* is ~2× fastest.
2. **Best local coding + learning assistant** — split across two runners because
   "writes correct code" and "teaches well" are different skills:
   - `eval/run-code.py` — **correctness**: extracts the code block, runs it
     against hidden asserts (real pass@1). **Finding: `granite-custom`** (97%).
   - `eval/run-learn.py` — **tutoring**: working code *plus* an explanation,
     graded by a local LLM judge (rubric: approach, complexity, alternative,
     pitfall, clarity). **Finding: `granite-custom`** (10/10); `qwen-custom` has
     the best explanations but fails the code gate with thinking off.

All runners produce a ranked leaderboard and declare a winner.

### Thinking-mode caveat

Thinking is a Qwen-only runtime flag, not a Modelfile setting. The runners
default `think=False`, so `qwen-custom` is normally tested with its defining
feature off — which understates it on reasoning-heavy tasks. A model spec can
carry a `:think` suffix (`qwen-custom:think`) to A/B thinking on vs off; used on
the coding and learning runs, skipped for content (where it only adds latency).

## Architecture (prompt assembly)

Builders concatenate sources into `models/<name>/system.txt` in fixed order,
wrapped in `=== SECTION ===` markers:

1. `prompts/system.md`
2. `prompts/personality.md`
3. `prompts/formatting.md`
4. `prompts/roles/$ROLE.md` (per-model overlay: `coding` for qwen/granite, `prose` for gemma)
5. `prompts/safety.md`
6. `memory/user.md`
7. `knowledge/**/*.md` (sorted, `--- START/END FILE ---` wrappers, skip > 100k)

The `Modelfile` embeds `system.txt` literally inside `SYSTEM """ ... """` (no
shell expansion). Builders abort if any source contains `"""`.

## Build scripts

Three scripts, intentionally mirrored. Only the top config block differs
(`MODEL_NAME`, `BASE_MODEL`, `ROLE`, `EXTRAS`, `PARAMS`). The assembly logic
below the `Shared assembly` divider is byte-identical and must stay in lock-step.

New model = copy a script, edit the config block. (Copy `build-qwen` if you need
`TEMPLATE`/`RENDERER`/`PARSER` extras; any other otherwise.)

## Where changes belong

- Behavior rule (all models) → `prompts/`
- Behavior rule (one role only) → `prompts/roles/<role>.md`
- Stable fact about Casey → `memory/user.md`
- Reusable cross-conversation reference → `knowledge/`
- A new eval task → `eval/coding_tasks.py` or `eval/learning_tasks.py`
- One-off context → not in the build

## Prompt philosophy

Slim is correct. When a model misbehaves, first try to **remove** rules, not add
them. Same for `knowledge/` files. Sampler tuning (`PARAMS` block) is fair game;
rule proliferation is not.

## Scope boundary

Out of scope for the baseline: tool calling, runners, transcript capture. A
future `tools/` folder is reserved but not built.
