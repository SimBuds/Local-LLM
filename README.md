# AI Context Stack

Layered-Markdown → customized Ollama models, plus an eval suite that picks the
right model per job. No fine-tuning: customization is prompt + reference context
only. Edit Markdown, run a builder, `ollama create` produces a local model.

## Models

Three purpose-built models from one shared prompt stack (`prompts/` +
`memory/user.md`) and a per-model role overlay, picked by benchmark
([eval/RESULTS.md](eval/RESULTS.md)). `num_ctx` is tuned so each sits 100% on a 10 GB GPU.

| Model            | Base            | ctx   | Role / best-for                                       |
|------------------|-----------------|-------|-------------------------------------------------------|
| `gemma-custom`   | `gemma4:e2b`    | 32768 | **Content generation** (`prose` overlay) — best content scorer, ~175 tok/s. |
| `granite-custom` | `granite4.1:8b` | 12288 | **Coding assistant + tutor** (`coding` overlay) — top code-correctness and tutor scorer. |
| `qwen-custom`    | `qwen3.5:9b`    | 16384 | General daily driver + **thinking-on experimental** model; only model with thinking mode. |

Eval winners (see [Evaluation](#evaluation)): **content → `gemma-custom`**,
**coding correctness + tutoring → `granite-custom`**.

## Quickstart

```bash
./build-qwen                              # build one model
ollama run --think false qwen-custom      # run it (--think is qwen-only)
```

Each builder assembles the prompt stack, writes
`models/<name>/{system.txt,Modelfile}`, and runs `ollama create <name>`.

## Build

```bash
./build-qwen  ./build-granite  ./build-gemma
```

The scripts are mirrored: only the top config block differs (`MODEL_NAME`,
`BASE_MODEL`, `ROLE`, `EXTRAS`, `PARAMS`); the assembly logic below the divider
is byte-identical and must stay in lock-step. **New model:** copy a script
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
│       ├── coding.md     # qwen, granite
│       └── prose.md      # gemma (content)
├── memory/user.md        # durable user profile
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

Three runners, each ranks all models and declares a winner. Output lands in
`eval/runs/<UTC>/`.

```bash
./eval/run.py          # content/SEO: format + instruction discipline
./eval/run-code.py     # coding: real pass@1 (runs code vs hidden asserts)
./eval/run-learn.py    # tutoring: code + explanation, graded by a local judge
```

Common flags: `--models`, `--attempts`, `--timeout`. Code/learn add `--tasks`
and `--exec-timeout`; learn adds `--judges` (a leave-one-out panel — default is
all `--models`, so no model grades its own output).

**Thinking mode:** append `:think` to a model spec (`qwen-custom:think`) to test
Qwen thinking on vs off — useful on `run-code.py`/`run-learn.py`, skip for content.

### Latest crowns (5 attempts, debiased, thinking-off)

| Skill | King | Score | Speed |
|---|---|---|---|
| Content / SEO | **gemma-custom** | 5/5 clean (100%) | 180 tok/s |
| Coding correctness | **granite-custom** | 27/30 (90%) | 1.7 s/call |
| Teaching (panel-judged) | **granite-custom** | 9.9/10 | 97 tok/s |

Notes: qwen/gemma explain as well as granite (9.8–9.9 when correct) but trail on
the code-pass gate. Thinking mode wasn't worth its ~18× latency on any skill, so
qwen is kept as a deliberate thinking-on experimental model, not a default path.
(Removed after benchmarking: `llama-custom`, `ministral-custom` — won no role.)

Full leaderboards, the 3-role consolidation decision, and keep/remove reasoning:
[`eval/RESULTS.md`](eval/RESULTS.md).

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

**Context** (`num_ctx`) is set per-model to keep each fully on a 10 GB GPU:
qwen `16384`, granite `12288`, gemma `32768`. The server's
`OLLAMA_CONTEXT_LENGTH` is a hard ceiling on top.

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
`PLAN.md` (blueprint + rationale) · `IMPLEMENT.md` (phase log).
