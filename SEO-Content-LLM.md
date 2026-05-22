# SEO Content LLM — Source of Truth

Local Ollama stack for SEO long-form content generation. Three parallel build targets share the same prompt overlay so prose quality can be A/B-tested on real outputs.

Last reviewed: 2026-05-21.

## Objective

Generate unique, E-E-A-T-aligned long-form content that avoids common "AI-isms" while maintaining factual density and SEO structural compliance (May 2026 Google Helpful Content era).

## Hardware (Arch Workstation)

- **GPU:** RTX 3080, 9GB usable VRAM (1GB host reserve)
- **CPU/RAM:** Ryzen 5900X / 32GB — used for 70B partial offload (~2–4 t/s)

## Build Targets

| Script | Model | Base | Alias | Role |
| :--- | :--- | :--- | :--- | :--- |
| `build-llama` | `llama-seo` | `llama3.1:8b` | `lc` | **Primary** SEO drafting. Strong long-form prose at 9GB. |
| `build-granite` | `granite-seo` | `granite4.1:8b` | `gn` | A/B candidate. IBM, tuned for instruction-following + structured JSON (meta tags, schema.org JSON-LD, FAQ blocks). Apache 2.0. |

The general technical agent (`build-qwen` / `qwen-custom`) is a separate project — not in scope here.

For high-authority "money site" content, use cloud-offload `llama3.3:70b` at Q4_K_M via:

```bash
alias lct='ollama run llama3.3:70b'
```

Expect 2–4 t/s on 32GB RAM. Reserve for final drafts, not iteration.

## Prompt Architecture

```
ai/
├── prompts/seo/            # SEO overlay (build-llama + build-granite only)
│   ├── system.md           # Durable mandate: E-E-A-T concept, honesty, burstiness
│   ├── google-rules.md     # Volatile: current banned phrases + Google posture (quarterly review)
│   ├── personality.md      # Journalistic/authoritative voice
│   └── formatting.md       # Inverted Pyramid + H1-H3 hierarchy
├── prompts/safety.md       # Inherited (shared root file)
├── memory/user.md          # Inherited: Casey profile
├── knowledge/              # Inherited: project + domain reference
└── models/<name>/          # Generated build output
```

Both SEO builds inherit `safety.md`, `memory/user.md`, and the full `knowledge/` tree from the repo root. The SEO-specific overlay lives entirely under `prompts/seo/` — keep it that way to preserve separation from the general-agent project.

### Durable vs. volatile guardrails

The SEO overlay splits along a deliberate seam:

- **`system.md`** — durable principles. E-E-A-T as a concept, honesty rules, burstiness. These don't change when Google updates.
- **`google-rules.md`** — volatile rules. Current banned phrases, current algorithmic emphases, dated "last reviewed" header. **Review quarterly** or after any Google core update. This is the only file that should change with the SEO weather.

When the downstream CLI tool ships, it can also write to `google-rules.md` programmatically (e.g., to inject keyword-specific bans per article) without touching the durable layer.

## SEO Model Parameters

Both SEO builds use a 16k context window and 2k predict budget. Sampler differs:

| Param | llama-seo | granite-seo | Rationale |
| :--- | :--- | :--- | :--- |
| `temperature` | 0.92 | 0.8 | Granite is tightly instruction-tuned; less heat avoids drift |
| `top_p` | 0.9 | 0.92 | |
| `top_k` | 40 | 40 | |
| `repeat_penalty` | 1.25 | 1.15 | Llama loops more than Granite |
| `repeat_last_n` | 256 | 256 | |
| `num_predict` | 2048 | 2048 | |
| `num_ctx` | 16384 | 16384 | Both 8B-class, fit 9GB at 16k |

## Ollama Systemd Configuration

```ini
[Service]
Environment="OLLAMA_KV_CACHE_TYPE=q4_0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_KEEP_ALIVE=10m"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

`q4_0` KV cache cuts VRAM ~50%, keeping 16k ctx viable on the 9GB partition.

## Build & Run

```bash
ollama pull llama3.1:8b
ollama pull granite4.1:8b
./build-llama
./build-granite

lc "Write a 500-word introduction for: <keyword>"
gn "Write a 500-word introduction for: <keyword>"
```

Shell aliases (add to `~/.bashrc`):

```bash
alias lc='ollama run llama-seo'
alias gn='ollama run granite-seo'
alias lct='ollama run llama3.3:70b'
```

## Evaluation Criteria

1. **Vocabulary diversity** — absence of banned phrases (see `prompts/seo/system.md` negative-constraint list).
2. **Instruction following** — correct H1–H3 hierarchy, natural keyword integration, meta-tag length compliance.
3. **Topical depth** — concrete experience/expertise details, not generic summaries.
4. **Throughput** — tokens/sec vs. quality tradeoff for bulk drafting.

## Maintenance Workflow

1. Edit `prompts/seo/*.md` (behavior) or `memory/user.md` (Casey facts).
2. Rebuild: `./build-llama` and/or `./build-granite`.
3. Test with the same prompt across both aliases.
4. Adjust the smallest responsible layer. Per repo philosophy: **remove rules before adding them**.

## Banned Vocabulary

Maintained in `prompts/seo/system.md`. The list targets words/phrases statistically correlated with low-effort AI prose in 2026 (delve, landscape, testament, "in conclusion", "leverage" as verb, etc.). Update the file, not this doc.

## Observation Log

- *2026-05-21:* Established SEO stack on `llama3.1:8b` (only 8B Llama in registry). Initially paired with `gemma3:12b` for A/B but Gemma 12B spilled ~25% to CPU at 9GB partition even with q4_0 KV cache. Swapped to `granite4.1:8b` — fresher IBM model, tuned for structured-output workflows that the downstream SEO CLI will need (meta tags, schema.org JSON-LD). Both SEO builds now share the `prompts/seo/` overlay with durable/volatile guardrail split. Promoted this file to source of truth; deprecated `custom-llama.md`.
