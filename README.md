# AI Context Stack

Layered-Markdown → customized Ollama models, plus an eval suite that picks the
right model per job. No fine-tuning: customization is prompt + reference context
only. Edit Markdown, run a builder, `ollama create` produces a local model.

## Models

One **versatile generalist** built from a shared prompt stack (`prompts/` +
`memory/user.md` + `knowledge/`) — no role overlays. A single all-rounder
expected to handle content, coding, and learning from the same system prompt.

| Model   | Base                  | ctx    | Notes                                                        |
|---------|-----------------------|--------|-------------------------------------------------------------|
| `gemma` | `gemma4:e4b-it-q8_0`  | 131072 | 🏆 all-rounder — content 5/5 clean, coding 26/30, ~51 tok/s. |

Fits 100% on-GPU and clears the 15 tok/s floor. e4b sliding-window attention
keeps the KV cache tiny, so the full native window fits at ~4.6 GB. `num_ctx`
is tuned for a 10 GB GPU.

Bases/ctx tuned on ollama 0.30 (leaner VRAM than 0.23.2). Full run history is in
the **Models tested** table below.

## Quickstart

```bash
./build-gemma                             # build the model
ollama run gemma                          # run it
```

Each builder assembles the prompt stack, writes
`models/<name>/{system.txt,Modelfile}`, and runs `ollama create <name>`.

## Build

```bash
./build-gemma
```

The config block at the top (`MODEL_NAME`, `BASE_MODEL`, `EXTRAS`, `PARAMS`) is
separated from the shared assembly logic below the divider. **New model:** copy
the script and edit only the config block (set `EXTRAS` if the base needs
`TEMPLATE`/`RENDERER`/`PARSER`).

## Structure

```
ai/
├── prompts/              # behavior controls (terse, durable — runs every turn)
│   ├── system.md         # core directives
│   ├── personality.md    # voice
│   ├── formatting.md     # output shape
│   └── safety.md         # operational safety
├── memory/user.md        # durable user profile
├── knowledge/**/*.md     # reusable reference context
├── eval/                 # evaluation suite (see below)
├── models/<name>/        # generated: system.txt + Modelfile
└── build-*               # one builder per model
```

## Prompt assembly order

`prompts/` + `memory/` + `knowledge/` are injected in sorted order, each file
wrapped in `--- START/END FILE ---` (files >100k skipped). The `Modelfile`
embeds `system.txt` literally inside `SYSTEM """ ... """` (no shell expansion);
builders abort if a source contains `"""`.

## Where changes belong

| Change | Goes in |
|---|---|
| Behavior rule, all models | `prompts/` |
| Stable fact about Casey | `memory/user.md` |
| Reusable technical reference | `knowledge/` |
| New eval task | `eval/coding_tasks.py` / `eval/learning_tasks.py` |
| One-off context | not in the build |

Keep prompts terse — every token is spent every turn. When a model misbehaves,
**remove** rules before adding; sampler tuning (`PARAMS`) is fair game.

## Evaluation

Five runners under `eval/`: `run.py` (content/SEO), `run-code.py` (coding pass@1
by real execution), `run-learn.py` (tutoring + code/explanation), `run-tutor.py`
(tutor leak-gated guidance), `run-speed.py` (tok/s + GPU/CPU split). Output lands
in `eval/runs/<UTC>/`.

```bash
./eval/run-speed.py --models gemma     # speed floor first
./eval/run-code.py  --models gemma     # coding pass@1
./eval/run.py       --models gemma     # content/SEO
```

> Safety: `run-code.py`/`run-learn.py` execute model-generated code in a
> subprocess with a timeout, but it is **not** containerized. Trusted models only.

## Tuning

**Sampler params** live in each builder's `PARAMS` block (`temperature 0.8`,
`top_p 0.92`, `top_k 40`, `repeat_penalty 1.15`, `repeat_last_n 256`,
`num_predict 2048`).

**Context** (`num_ctx`) is set targeting a 10 GB GPU: gemma `131072` (full
native window — sliding-window attention keeps its KV tiny, so it fits 100%
on-GPU at ~4.6 GB). The server's `OLLAMA_CONTEXT_LENGTH` is a hard ceiling on
top. **Note:** the AUR
package layers in `/etc/ollama.conf` (defaulting to `16384`), but we override it
in the service definition to `131072` to allow gemma its full window.

**Ollama server** (resolved via `EnvironmentFile` and `systemctl edit`):

### /etc/systemd/system/ollama.service.d/override.conf
```ini
[Service]
Environment="OLLAMA_KV_CACHE_TYPE=q4_0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_KEEP_ALIVE=-1"
```

KV-cache quantization is server-only (no Modelfile equivalent) and needs flash
attention on to take effect.

## Honest assessment (9 GB usable VRAM)

The 9 GB ceiling caps this box at ~4–9B models. Within that, here's the realistic
read — separating what the benchmarks measured from how the models behave in real work.

**Best pick per task**

| Task | Pick | Why |
|---|---|---|
| Content / SEO / copy | **gemma** | 100% clean format, holds length |
| Coding (small/boilerplate) | **gemma** | solid pass@1 (26/30) and fine on speed once warm |
| Learning / explaining | **gemma** | same shared prompt covers it; no separate tutor build |

Operational reason to run one model: with `OLLAMA_MAX_LOADED_MODELS=1` a single
warm model covers every job, so staying on gemma avoids reload churn.

**Where local (gemma) genuinely helps**

- **Content / SEO / marketing copy** — objective format rules, gemma passes
  reliably. The one *production-grade* local use.
- **Small/self-contained coding** — boilerplate, single functions, regex, scripts,
  "explain this error." Fast rubber-duck.
- **Learning / upskilling** — quick explanations without leaving the machine.
- **Privacy / offline** — nothing leaves the machine.

**Where to reach for a frontier model instead (be realistic)**

- **Real project coding** — multi-file changes, debugging, architecture, unfamiliar
  frameworks. A 4B-effective model hallucinates APIs and loses the thread past a couple
  files. The pass@1 benchmark is six self-contained algorithm puzzles — it does **not**
  predict real-repo performance.
- **Long-context / whole-repo reasoning** — even at high ctx, reasoning *over* that
  context is weak at this size.
- **High-stakes answers** where a subtle mistake is expensive.

**Caveats on the numbers:** coding pass@1 is the most misleading (toy tasks, not real
work). Content is the most trustworthy result (objective pass/fail).

**Bottom line:** use gemma as a fast, private first-pass for content + small coding +
learning; offload heavy project work to a frontier model. Raising the local ceiling is
a VRAM decision, not a tuning one — dense models ≥15 GB drop to ~3 tok/s on this card.

## Models tested — history

Every base that's been through the suite, with where it landed. Current lineup =
gemma, a versatile generalist (above). Retired models are gone from
`ollama`/build scripts but kept here for the record.

| Model (base) | Status | Pros | Cons | Good for |
|---|---|---|---|---|
| **gemma** (`gemma4:e4b-it-q8_0`) | **current** | Won content + coding; 100% on-GPU at full ctx; e4b sliding-window keeps KV tiny; one versatile build covers every job | 4B-effective → weak on complex/multi-file reasoning | Content/SEO (production), small coding, learning — the all-rounder |
| **granite** (`granite4.1:8b-Q5_K_M`, ~6.3 GB) | **dropped 2026-06-03** | Tied coding pass@1 (26/30); ~71 tok/s, 100% on-GPU @ 16k | Content 60% clean and ran shorter; led no axis; separate weights → reload per switch | — |
| **qwen** (`batiai/qwen3.5-9b:q6`, ~7.4 GB) | **dropped 2026-06-03** | Only thinking model; strong reasoning + tutor explanations | Thinking too slow (~31 s/answer); separate weights → reload per switch | Tutoring when depth > speed (if re-added) |
| `qwen-custom` (`qwen3.5:9b` Q4, ~6.6 GB) | **removed 2026-06-02** | Fast (~88 tok/s), 100% on-GPU; thinking-capable | Superseded by Q6 qwen, then qwen dropped entirely | — |
| `ministral-custom` | **removed 2026-05-31** | Genuine #2 across all roles (content 100%, coding 83%, teach 9.0) | Redundant once gemma + granite covered every job | — |
| `llama-custom` | **removed 2026-05-31** | — | Last/near-last on every axis (content 20%, coding 73%, teach 6.8) | — |
| `qwen-big` (qwen3.6 27B dense → 35B MoE) | **retired 2026-06-02** | MoE reasoning beat qwen-custom (escapes dense-spill curse) | 13 t/s (MoE) / 3 t/s (dense) — too slow + too big to co-run | Would be viable on bigger/unified-memory VRAM |
| `qwen-moe` (`qwen3.6:35b-a3b-mtp-q4_K_M`, 22 GB) | **shelved 2026-06-03** | MTP + MoE clears the 15 tok/s floor (~32–42 tok/s) despite 73% CPU spill | ~83 s/answer; cold-start HTTP 500s; no q3 quant to shrink it | Revisit only with more VRAM |
| `gemma-big` (`batiai/gemma4-26b:iq4`, 13 GB) | **retired 2026-06-02** | More capacity than e4b | Lost every category; ~4.5× slower (23 t/s); 43% CPU spill | — |

**Hardware limit driving all of this:** RTX 3080 (10 GB, ~9 usable), Ryzen 5900x,
32 GB DDR4-3600. Models that fit 100% on-GPU run fast; anything that spills is
bottlenecked by ~57 GB/s DDR4 (not compute) and gen tok/s falls off a cliff. Dense
models ≥15 GB are effectively dead here (~3 tok/s); MoE survives spill better but
still isn't interactive.

## Docs

`AGENTS.md` (agent workflow contract). Eval suite under `eval/`; results and run history in this README.
