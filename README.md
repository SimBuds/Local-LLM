# AI Context Stack

Layered-Markdown → customized Ollama models, plus an eval suite that picks the
right model per job. No fine-tuning: customization is prompt + reference context
only. Edit Markdown, run a builder, `ollama create` produces a local model.

## Models

Purpose-built models from one shared prompt stack (`prompts/` + `memory/user.md`)
and a per-model role overlay, picked by benchmark ([TESTING.md](TESTING.md)).
`num_ctx` is tuned per-model for a 10 GB GPU (the gemma Q6 builds fit 100% on-GPU
at the full 131k window; `granite-coder` spills a little — splits in
[TESTING.md](TESTING.md)).

| Model            | Base            | ctx   | Role / best-for                                       |
|------------------|-----------------|-------|-------------------------------------------------------|
| `gemma-content`  | `batiai/gemma4-e4b:q6` | 131072 | **Content generation** (`prose` overlay) — best content scorer; Q6 imatrix, 100% on-GPU, ~105 tok/s. |
| `gemma-coder`    | `batiai/gemma4-e4b:q6` | 131072 | **Coding** (`coding` overlay) — same Q6 base; challenging `granite-coder` on pass@1. |
| `granite-coder` | `granite4.1:8b` | 12288 | **Coding assistant + tutor** (`coding` overlay) — top code-correctness and tutor scorer. |
| `qwen-custom`    | `qwen3.5:9b`    | 16384 | General daily driver + **thinking-on experimental** model; only model with thinking mode. |

Eval winners and full leaderboards: **[TESTING.md](TESTING.md)**.

## Quickstart

```bash
./build-qwen                              # build one model
ollama run --think false qwen-custom      # run it (--think is qwen-only)
```

Each builder assembles the prompt stack, writes
`models/<name>/{system.txt,Modelfile}`, and runs `ollama create <name>`.

## Build

```bash
./build-qwen  ./build-granite-coder  ./build-gemma-content  ./build-gemma-coder
./build-gemma-tutor  ./build-granite-tutor          # tutor candidates (eval pending)
```

The scripts are mirrored: only the top config block differs (`MODEL_NAME`,
`BASE_MODEL`, `ROLE`, `EXTRAS`, `PARAMS`); the assembly logic below the divider
is byte-identical and must stay in lock-step (incl. the role-gated `LEARNING
PROFILE` injection — emitted only when `ROLE=tutor`). **New model:** copy a script
(`build-qwen` if you need `TEMPLATE`/`RENDERER`/`PARSER`; any other otherwise)
and edit only the config block.

## Structure

```
ai/
├── prompts/              # behavior controls (terse, durable — runs every turn)
│   ├── system.md         # core directives
│   ├── personality.md    # voice
│   ├── formatting.md     # output shape
│   ├── safety.md         # operational safety
│   └── roles/            # per-model overlays (selected by $ROLE)
│       ├── coding.md     # qwen, granite, gemma-coder
│       ├── prose.md      # gemma-content
│       └── tutor.md      # gemma-tutor, granite-tutor (teaches; never full solutions)
├── memory/user.md        # durable user profile
├── memory/learning-profile.md  # tutor-only: level/gaps/goals (role-gated inject)
├── knowledge/**/*.md     # reusable reference context
├── eval/                 # evaluation suite (see below)
├── models/<name>/        # generated: system.txt + Modelfile
└── build-*               # one builder per model
```

## Prompt assembly order

`system.md` → `personality.md` → `formatting.md` → `roles/$ROLE.md` →
`safety.md` → `memory/user.md` → `knowledge/**/*.md` (sorted; each wrapped in
`--- START/END FILE ---`; files >100k skipped). Each block is wrapped in
`=== HEADING ===` markers. The `Modelfile` embeds `system.txt` literally inside
`SYSTEM """ ... """` (no shell expansion); builders abort if a source contains `"""`.

## Where changes belong

| Change | Goes in |
|---|---|
| Behavior rule, all models | `prompts/` |
| Behavior rule, one role | `prompts/roles/<role>.md` |
| Stable fact about Casey | `memory/user.md` |
| Reusable technical reference | `knowledge/` |
| New eval task | `eval/coding_tasks.py` / `eval/learning_tasks.py` |
| One-off context | not in the build |

Keep prompts terse — every token is spent every turn. When a model misbehaves,
**remove** rules before adding; sampler tuning (`PARAMS`) is fair game.

## Evaluation

The eval suite, current leaderboards, the 3-model decision, and testing plans
live in **[TESTING.md](TESTING.md)** — the testing source of truth. Five runners
under `eval/`: `run.py` (content/SEO), `run-code.py` (coding pass@1),
`run-learn.py` (tutoring + code/explanation), `run-tutor.py` (tutor leak-gated
guidance), `run-speed.py` (tok/s + GPU/CPU split). Output lands in
`eval/runs/<UTC>/`.

> Safety: `run-code.py`/`run-learn.py` execute model-generated code in a
> subprocess with a timeout, but it is **not** containerized. Trusted models only.

## Tuning

**Sampler params** live in each builder's `PARAMS` block. `qwen-custom` uses
Qwen3's recommended thinking-mode sampling (`temperature 0.6`, `top_p 0.95`,
`top_k 20`, `min_p 0`, `presence_penalty 1.5`); thinking is on by default at the
model level. Two attempted thinking tweaks (pp→1.0, adding `num_predict 8192`)
both *regressed* coding pass@1 from 87%, so the config stayed put — see the
`build-qwen` PARAMS comment. Thinking-qwen is high-variance (67–87% run-to-run).
granite/gemma run hotter — see each `PARAMS` for values.

**Context** (`num_ctx`) is set per-model targeting a 10 GB GPU: qwen `16384`,
granite `12288`, both gemma builds `131072` (the Q6 base fits its full window
on-GPU — sliding-window attention keeps the KV tiny). The server's
`OLLAMA_CONTEXT_LENGTH` is a hard ceiling on top. Measured GPU/CPU splits are in
[TESTING.md](TESTING.md).

**Ollama server** (`sudo systemctl edit ollama.service`):

```ini
[Service]
Environment="OLLAMA_KV_CACHE_TYPE=q5_0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_KEEP_ALIVE=30m"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

KV-cache quantization is server-only (no Modelfile equivalent) and needs flash
attention on to take effect.

## Thinking mode (Qwen-only)

Thinking is a runtime flag, not a Modelfile parameter — and only `qwen-custom`
has it (`granite`/`gemma` error on `--think`).

```bash
ollama run qwen-custom
```

Thinking is calibrated for problem-solving, not chitchat — asking it to handle a
greeting with thinking on produces long looping traces; that's by design.

## Docs

`AGENTS.md` (agent workflow contract — `CLAUDE.md` just points here) ·
[`TESTING.md`](TESTING.md) (testing source of truth: suite, results, plans).
