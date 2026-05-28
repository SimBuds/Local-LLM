# PLAN.md — Blueprint

## What this is

A build pipeline that compiles layered Markdown into three customized Ollama models. There is no application code; the product is the generated `system.txt` + `Modelfile` per model.

## Core idea

Customize base models via **prompt + reference context only** — never weight fine-tuning. Edit Markdown, run a builder, `ollama create` produces a local model.

## Models

| Name             | Base            | Role                                              |
|------------------|-----------------|---------------------------------------------------|
| `qwen-custom`    | `qwen3.5:9b`    | Daily driver — terse technical assistant; runtime thinking via `qct` |
| `granite-custom` | `granite4.1:8b` | Instruction-following / structured output         |
| `llama-custom`   | `llama3.1:8b`   | Long-form prose                                   |

All three share one neutral prompt stack plus a per-model **role overlay** (`coding` for qwen/granite, `prose` for llama) selected by each builder's `ROLE`. Project-specific overlays (e.g. SEO rules) are injected at request time by the consuming app, not baked in.

## Architecture

Builders concatenate sources into `models/<name>/system.txt` in fixed order, wrapped in `=== SECTION ===` markers:

1. `prompts/system.md`
2. `prompts/personality.md`
3. `prompts/formatting.md`
4. `prompts/roles/$ROLE.md` (per-model overlay: `coding` for qwen/granite, `prose` for llama)
5. `prompts/safety.md`
6. `memory/user.md`
7. `knowledge/**/*.md` (sorted, `--- START/END FILE ---` wrappers, skip > 100k)

The `Modelfile` embeds `system.txt` literally inside `SYSTEM """ ... """` (no shell expansion). Builders abort if any source contains `"""`.

## Build scripts

Three scripts, intentionally mirrored. Only the top config block differs (`MODEL_NAME`, `BASE_MODEL`, `ROLE`, `EXTRAS`, `PARAMS`). The assembly logic below the `Shared assembly` divider is byte-identical and must stay in lock-step.

New model = copy a script, edit the config block.

## Where changes belong

- Behavior rule (all models) → `prompts/`
- Behavior rule (one role only) → `prompts/roles/<role>.md`
- Stable fact about Casey → `memory/user.md`
- Reusable cross-conversation reference → `knowledge/`
- One-off context → not in the build

## Prompt philosophy

Slim is correct. When a model misbehaves, first try to **remove** rules, not add them. Same for `knowledge/` files. Sampler tuning (`PARAMS` block) is fair game; rule proliferation is not.

## Scope boundary

Out of scope for the baseline: tool calling, runners, transcript capture. A future `tools/` folder is reserved but not built.
