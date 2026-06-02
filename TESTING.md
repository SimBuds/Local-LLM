# Testing — source of truth

The single source of truth for how the `~/ai` models are tested: why we test,
how to run the suite, the current results + model-selection decision, and what's
planned. (Supersedes the old `eval/RESULTS.md`.)

## Why we test (two goals)

1. **Content / SEO** — format discipline + instruction-following for human-like
   content generation.
2. **Coding + learning** — real code correctness (pass@1) and teaching quality.

One purpose-built model per job, picked by benchmark.

## The suite

Four runners under `eval/`. Each ranks the models and writes to `eval/runs/<UTC>/`.

| Runner | Measures | Runs model code? |
|---|---|---|
| `run.py` | Content / SEO: format + instruction discipline | no |
| `run-code.py` | Coding: real pass@1 (runs code vs hidden asserts) | **yes** |
| `run-learn.py` | Tutoring: code + explanation, leave-one-out judge panel | **yes** |
| `run-speed.py` | Raw tok/s + GPU/CPU split (spillover tradeoff) | no |

```bash
./eval/run.py
./eval/run-code.py
./eval/run-learn.py
./eval/run-speed.py
```

Common flags: `--models`, `--attempts`, `--timeout`. Code/learn add `--tasks`
and `--exec-timeout`; learn adds `--judges` (leave-one-out panel — default is all
`--models`, so no model grades its own output). `run-speed.py` adds
`--num-predict` (caps output so CPU-spillover models finish fast) and
`--opt KEY=VAL` (ad-hoc Ollama options for one run, e.g. `--opt num_ctx=4096`);
it ranks by generation tok/s, not a quality winner.

**Thinking mode:** append `:think` to a model spec (`qwen-custom:think`) to test
Qwen thinking on vs off — useful on `run-code.py`/`run-learn.py`, skip for content.

**New eval task:** add to `eval/coding_tasks.py` or `eval/learning_tasks.py`.

> **Safety:** `run-code.py` / `run-learn.py` execute model-generated code in a
> subprocess with a wall-clock timeout, but it is **not** containerized. Run
> trusted models only — be deliberate before pointing them at a freshly-pulled
> community model.

## Hardware (sets the fit/speed envelope)

RTX 3080 (10 GB), Ryzen 5900x, 32 GB DDR4-3600. Models that fit 100% on the GPU
run fast; anything that spills is bottlenecked by ~57 GB/s DDR4 bandwidth, not
compute — generation tok/s falls off a cliff while prompt-eval stays fast.

---

## Model-selection decision (current lineup)

_Decision recorded 2026-05-31, from the 2026-05-29 run (5 attempts/model,
debiased, thinking-off)._ Goal: consolidate a 5-model field into **3
purpose-built models**, one per task, keeping whichever base benchmarks best per
role (a base may back two roles via different prompt overlays).

| Role | Model | Basis |
|---|---|---|
| Content generation | **`gemma-custom`** (gemma4:e4b) | Won content 5/5 clean (100%) @ 180 tok/s — 2× the fastest in the field. |
| Coding assistant | **`granite-custom`** (granite4.1:8b) | Highest pass@1 (90%, 27/30) @ 1.7 s/call. Correctness-first. |
| Coding tutor | **`granite-custom`** + `tutor` overlay _(deferred)_ | Frontrunner on the teaching test (9.9/10, panel-judged). Final pick waits for the tutor feature spec. |

**Keepers:** `gemma-custom` (on the `prose` overlay), `granite-custom` (backs both
coding roles via overlays), and `qwen-custom` — retained as the **thinking-on
experimental model** (the lineup tests it as `qwen-custom:think`).
**Removed (2026-05-31):** `llama-custom`, `ministral-custom`.

## Benchmark leaderboards

### Content / SEO (`run.py`) — clean-rate
| Rank | Model | Clean | Tok/s |
|---|---|---|---|
| 1 | **gemma-custom** | 5/5 (100%) | 180 |
| 2 | ministral-custom | 5/5 (100%) | 102 |
| 3 | qwen-custom | 4/5 (80%) | 85 |
| 4 | llama-custom | 1/5 (20%) | 112 |
| 5 | granite-custom | 0/5 (0%) | 96 |

### Coding correctness (`run-code.py`) — real pass@1
| Rank | Model | Pass | Speed |
|---|---|---|---|
| 1 | **granite-custom** | 27/30 (90%) | 1.7 s |
| 2 | ministral-custom | 25/30 (83%) | 2.1 s |
| 3 | qwen-custom | 25/30 (83%) | 2.3 s |
| 4 | gemma-custom | 22/30 (73%) | 2.3 s |
| 5 | llama-custom | 22/30 (73%) | 2.9 s |

`calc` (operator-precedence eval) is the field ceiling — best models hit only 1–2/5.

### Teaching (`run-learn.py`) — leave-one-out judge panel
| Rank | Model | Teach /10 | Code pass |
|---|---|---|---|
| 1 | **granite-custom** | 9.9 | 12/12 |
| 2 | ministral-custom | 9.0 | 11/12 |
| 3 | qwen-custom | 7.4 | 9/12 |
| 4 | gemma-custom | 7.4 | 9/12 |
| 5 | llama-custom | 6.8 | 11/12 |

Debias confirmed granite (9.9 panel vs 10.0 self-judged). qwen/gemma explain as
well as granite (9.8–9.9 when correct) but lose on the code-pass gate.

### Speed / spillover (`run-speed.py`) — 2026-06-02
_Clean VRAM, 2 prompts × 1, output capped at num_predict=200, thinking off._

| Rank | Model | Gen tok/s | Slowdown | GPU/CPU split |
|---|---|---|---|---|
| 1 | **qwen-custom** (9b) | 87.8 | 1.0× | 100% GPU |
| 2 | gemma-custom (e4b) | 42.1 | 2× | 64%/36% CPU/GPU |
| 3 | granite-custom (8b) | 31.4 | 2.8× | 12%/88% CPU/GPU |
| 4 | qwen-big (27b) | 2.9 | 30× | 67%/33% CPU/GPU |

`qwen-big` lever sweep (all configs land at 2.8–2.9 tok/s — **no runtime lever helps**):

| Config | Gen tok/s | GPU share |
|---|---|---|
| baseline (num_ctx 16384) | 2.9 | 33% |
| `--opt num_ctx=4096` | 2.9 | 35% |
| `--opt num_thread=12` | 2.8 | 31% |
| `num_ctx=4096 + num_thread=12` | 2.8 | 32% |

**Findings:**
- `qwen-big` (qwen3.6:27b, Q4_K_M ~17 GB) is ~30× slower than `qwen-custom`.
  Lowering `num_ctx` barely shifts the split and gives no speed; `num_thread 12`
  is slightly worse. **The only real lever is a smaller weight quant.**
- Spillover is wider than the old docs claimed: `granite-custom` (12% CPU) and
  `gemma-custom` (64% CPU) also partially spill at their current `num_ctx`. The
  "100% on a 10 GB GPU" line holds only for `qwen-custom`. (`gemma-custom` is
  still on 32k in the live model — `build-gemma` is now 16k but hasn't been
  rebuilt; rebuilding should reduce its spill.)
- `% on CPU` doesn't linearly predict speed: gemma at 64% CPU still does 42 tok/s
  because `gemma4:e4b` activates ~4B effective params; the dense 27B does not.

## Removal reasoning

- **`llama-custom` — REMOVED (2026-05-31).** Last or near-last on every axis
  (content 20%, coding 73%, teaching 6.8). Won no role.
- **`ministral-custom` — REMOVED (2026-05-31).** Genuine #2 across the board, but
  redundant once gemma + granite cover all three roles.
- **`qwen-custom` — KEEP as the thinking-on experimental model.** Casey's general
  daily-driver; `~/Apps/jobhunt` runs bare `qwen3.5:9b`. Lineup lists it as
  `qwen-custom:think`.

---

## Next phase — quant variants

Both pulled 2026-06-02. Goal: trade quality vs fit/speed against the current lineup.

- [ ] **27B quant comparison.** `batiai/qwen3.6-27b:iq4` (15 GB, loads — `qwen35`
  arch) and `:iq3` (11 GB) vs the Q4_K_M `qwen-big`. Expect 2–3× if more fits on
  GPU. Wrap in a builder (`BASE_MODEL="batiai/qwen3.6-27b:iq4"`) for the prompt stack.
- [ ] **Gemma quant upgrade.** Use the ollama.com build `batiai/gemma4-e4b:q6`,
  **not** the raw `hf.co/unsloth/...` GGUF — Ollama 0.23.2's llama.cpp engine
  errors with `unknown model architecture: 'gemma4'` on third-party GGUFs (the
  native engine that runs the official `gemma4:e4b` is fine). A ~7 GB Q6 should
  beat the current Q4_K_M on quality *and* sit 100% on GPU. Rebuild `gemma-custom`
  at 16k first.
- [ ] **Tutor role.** Define the tutor feature spec, add a `tutor` overlay, extend
  `run-learn.py` to score it. Granite is the base to beat.

## Done

- [x] **Content overlay check (2026-05-31)** — A/B'd `gemma-custom` coding vs
  `prose` overlay on content: both 5/5 clean, same speed; switched to `prose`
  (reads marginally more natural, purpose-built for human-like writing).
- [x] **Removals executed (2026-05-31)** — `llama-custom` + `ministral-custom`
  `ollama rm`'d; build scripts and `models/` dirs deleted; `DEFAULT_MODELS` and
  docs trimmed to the 3-model lineup.
