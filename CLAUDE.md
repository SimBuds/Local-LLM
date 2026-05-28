# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A build pipeline that turns layered Markdown sources into three customized Ollama models, all project-agnostic:

- `qwen-custom` (base `qwen3.5:9b`) — default daily driver, built by `./build-qwen`
- `granite-custom` (base `granite4.1:8b`) — instruction-following / structured output, built by `./build-granite`
- `llama-custom` (base `llama3.1:8b`) — long-form prose, built by `./build-llama`

There is no application code — the "product" is the generated system prompt and `Modelfile` for each. Customization happens via prompt + reference context, never weight fine-tuning. Project-specific overlays (e.g. SEO rules in `~/Apps/SEO-LLM/prompts/seo/`) are injected by the consuming app at request time, not baked into the model.

## Build

```bash
./build-qwen        # writes models/qwen-custom/ and runs `ollama create`
./build-granite     # same, for granite-custom
./build-llama       # same, for llama-custom
```

The three scripts are intentionally mirrored: only the config block at the top differs (`MODEL_NAME`, `BASE_MODEL`, `ROLE`, `EXTRAS`, `PARAMS`). The assembly logic below the `Shared assembly` divider is byte-identical across all three and must be kept in lock-step — when you change one, change them all (or `diff` to verify).

To add a new model: copy one of the existing scripts and edit only the top config block.

There is no test suite, lint, or CI. Validation is interactive: rebuild, then `ollama run <name>` (add `--think false` for `qwen-custom`; the others have no thinking mode).

## Architecture: prompt assembly

Each builder concatenates source Markdown into a single `system.txt` in this order, wrapped in `=== SECTION ===` markers so the model can navigate it:

1. `prompts/system.md` → core directives
2. `prompts/personality.md` → voice
3. `prompts/formatting.md` → output shape
4. `prompts/roles/$ROLE.md` → per-model role overlay (`coding` for qwen/granite, `prose` for llama)
5. `prompts/safety.md` → operational safety
6. `memory/user.md` → durable user profile
7. `knowledge/**/*.md` → sorted, each wrapped in `--- START/END FILE: <rel> ---`, skipped if >100k

The `Modelfile` then embeds `system.txt` literally inside a `SYSTEM """ ... """` block (the body is `cat`-ed, never `$()`-expanded, so `$VAR`/backticks/backslashes in source files are safe). The builders abort if any source contains `"""`.

## Where changes belong

- Behavior rule for all models → `prompts/` (keep terse — every token runs every turn)
- Behavior rule for one role only → `prompts/roles/<role>.md` (`coding` or `prose`)
- Stable fact about Casey → `memory/user.md`
- Reusable technical reference used across conversations → `knowledge/`
- One-off context → not in the build

## Prompt philosophy (important)

Keep the prompt stack slim. When the model misbehaves, **first look at what can be removed** — duplicated rules, overlapping directives, prior additions that didn't help. Adding rules to fix behavior tends to make things worse by giving the model more surface to recursively re-check. Sampler tuning (in each builder's `PARAMS` block) is fair game; rule proliferation is not. Same rule applies to knowledge: if a file under `knowledge/` isn't earning its tokens, remove it.

## Ollama runtime notes

Thinking mode is a runtime flag, not a Modelfile parameter:
- `qc`  → `ollama run --think false qwen-custom` (default, for chitchat/short Qs)
- `qct` → `ollama run qwen-custom` (thinking on, for design/debugging)

`qct` on a greeting will loop — that's the model's design, not a prompt bug to patch.

`--think` is Qwen-only. `granite-custom` and `llama-custom` have no thinking mode; run them with plain `ollama run <name>` (passing `--think false` errors).

Server-level tuning (flash attention, KV cache type, context length, parallel/keep-alive) lives in the systemd drop-in for `ollama.service` — see README.md for the exact values.
