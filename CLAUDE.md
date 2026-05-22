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
./build-qwen        # writes models/<MODEL_NAME>/ and runs `ollama create`
./build-granite     # same, for granite-custom
./build-llama       # same, for llama-custom
```

Env overrides (build-qwen only): `AI_ROOT` (default `~/ai`), `MODEL_NAME` (default `qwen-custom`), `BASE_MODEL` (default `qwen3.5:9b`). The granite/llama builders hardcode their model and base.

There is no test suite, lint, or CI. Validation is interactive: rebuild, then `ollama run --think false qwen-custom`.

## Architecture: prompt assembly

Each builder concatenates source Markdown into a single `system.txt` in this order, wrapped in `=== SECTION ===` markers so the model can navigate it:

1. `prompts/system.md` → core directives
2. `prompts/personality.md` → voice
3. `prompts/formatting.md` → output shape
4. `prompts/safety.md` → operational safety
5. `memory/user.md` → durable user profile
6. `knowledge/**/*.md` → sorted, each wrapped in `--- START/END FILE: <rel> ---`, skipped if >100k

The `Modelfile` then embeds `system.txt` inside a `SYSTEM """ ... """` block. The builders abort if any source contains `"""` (would break Modelfile parsing).

## Where changes belong

- Behavior rule → `prompts/` (keep terse — every token runs every turn)
- Stable fact about Casey → `memory/user.md`
- Reusable technical reference used across conversations → `knowledge/`
- One-off context → not in the build

## Prompt philosophy (important)

Keep the prompt stack slim. When the model misbehaves, **first look at what can be removed** — duplicated rules, overlapping directives, prior additions that didn't help. Adding rules to fix behavior tends to make things worse by giving the model more surface to recursively re-check. Parameter tuning (in each builder's `PARAMETER` lines) is fair game; rule proliferation is not.

## Ollama runtime notes

Thinking mode is a runtime flag, not a Modelfile parameter:
- `qc`  → `ollama run --think false qwen-custom` (default, for chitchat/short Qs)
- `qct` → `ollama run qwen-custom` (thinking on, for design/debugging)

`qct` on a greeting will loop — that's the model's design, not a prompt bug to patch.

Server-level tuning (flash attention, KV cache type, context length, parallel/keep-alive) lives in the systemd drop-in for `ollama.service` — see README.md for the exact values.
