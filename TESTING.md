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
| Content generation | **`gemma-content`** (gemma4:e4b) | Won content 5/5 clean (100%) @ 180 tok/s — 2× the fastest in the field. |
| Coding assistant | **`granite-coder`** (granite4.1:8b) | Highest pass@1 (90%, 27/30) @ 1.7 s/call. Correctness-first. |
| Coding tutor | **`granite-tutor`** + `tutor` overlay _(deferred)_ | Frontrunner on the teaching test (9.9/10, panel-judged). Final pick waits for the tutor feature spec. |

**Keepers:** `gemma-content` (on the `prose` overlay), `granite-coder` (backs both
coding roles via overlays), and `qwen-custom` — retained as the **thinking-on
experimental model** (the lineup tests it as `qwen-custom:think`).
**Removed (2026-05-31):** `llama-custom`, `ministral-custom`.

## Benchmark leaderboards

### Content / SEO (`run.py`) — clean-rate
| Rank | Model | Clean | Tok/s |
|---|---|---|---|
| 1 | **gemma-content** | 5/5 (100%) | 180 |
| 2 | ministral-custom | 5/5 (100%) | 102 |
| 3 | qwen-custom | 4/5 (80%) | 85 |
| 4 | llama-custom | 1/5 (20%) | 112 |
| 5 | granite-coder | 0/5 (0%) | 96 |

### Coding correctness (`run-code.py`) — real pass@1
| Rank | Model | Pass | Speed |
|---|---|---|---|
| 1 | **granite-coder** | 27/30 (90%) | 1.7 s |
| 2 | ministral-custom | 25/30 (83%) | 2.1 s |
| 3 | qwen-custom | 25/30 (83%) | 2.3 s |
| 4 | gemma-content | 22/30 (73%) | 2.3 s |
| 5 | llama-custom | 22/30 (73%) | 2.9 s |

`calc` (operator-precedence eval) is the field ceiling — best models hit only 1–2/5.

### Teaching (`run-learn.py`) — leave-one-out judge panel
| Rank | Model | Teach /10 | Code pass |
|---|---|---|---|
| 1 | **granite-coder** | 9.9 | 12/12 |
| 2 | ministral-custom | 9.0 | 11/12 |
| 3 | qwen-custom | 7.4 | 9/12 |
| 4 | gemma-content | 7.4 | 9/12 |
| 5 | llama-custom | 6.8 | 11/12 |

Debias confirmed granite (9.9 panel vs 10.0 self-judged). qwen/gemma explain as
well as granite (9.8–9.9 when correct) but lose on the code-pass gate.

### Speed / spillover (`run-speed.py`) — 2026-06-02
_Clean VRAM, 2 prompts × 1, output capped at num_predict=200, thinking off._

| Rank | Model | Gen tok/s | Slowdown | GPU/CPU split |
|---|---|---|---|---|
| 1 | **qwen-custom** (9b) | 87.8 | 1.0× | 100% GPU |
| 2 | gemma-content (e4b) | 42.1 | 2× | 64%/36% CPU/GPU |
| 3 | granite-coder (8b) | 31.4 | 2.8× | 12%/88% CPU/GPU |
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
- Spillover is wider than the old docs claimed: `granite-coder` (12% CPU) and
  `gemma-content` (64% CPU) also partially spill at their current `num_ctx`. The
  "100% on a 10 GB GPU" line holds only for `qwen-custom`. (Measured before the
  Q6 swap; `gemma-content` now runs `batiai/gemma4-e4b:q6` at 16k and fits 100%
  on-GPU — see the quant-variants subsection below.)
- `% on CPU` doesn't linearly predict speed: gemma at 64% CPU still does 42 tok/s
  because `gemma4:e4b` activates ~4B effective params; the dense 27B does not.

### Quant variants — uploader defaults vs hardware-tuned (2026-06-02)

Swapped both spilling models to BatiAI imatrix quants and A/B'd the uploader's
baked defaults (num_ctx 131072, temp 0.7) against our builders (num_ctx 16384 +
our sampling).

| Model | Uploader default (ctx 131072) | Tuned (ctx 16384) | Old Q4_K_M |
|---|---|---|---|
| `qwen-big` (iq4, 15 GB) | 2.8 t/s · 26% GPU · prompt ~40 t/s | 3.3 t/s · 39% GPU · prompt ~1000 t/s | 2.9 t/s · 33% GPU (17 GB) |
| `gemma-content` (q6, 6.2 GB) | 111 t/s · 100% GPU | 101.6 t/s · 100% GPU | 42 t/s · 64% CPU (9.6 GB) |

**Findings:**
- **Gemma quant swap is the big win.** `batiai/gemma4-e4b:q6` (6.2 GB) sits 100%
  on GPU at *any* ctx (sliding-window attention keeps KV tiny) at ~100–111 tok/s
  — **~2.4× the old Q4_K_M** gemma-content (42 tok/s, 64% CPU) *and* higher quality
  (Q6 vs Q4). Clear keeper.
- **num_ctx is the decisive hardware param for a spilling model.** On `qwen-big`
  the uploader's baked `131072` default kneecaps it: KV cache steals VRAM (only
  26% GPU) and prompt-eval collapses to ~40 tok/s. Our `16384` lifts gen 2.8→3.3
  tok/s (+18%), GPU share 26→39%, prompt-eval ~25×. The base bakes 131072, so the
  builder **must** set num_ctx (it does).
- **iq4 vs Q4_K_M on the 27B is marginal.** 15 GB vs 17 GB → 3.3 vs 2.9 tok/s,
  39% vs 33% GPU. Still ~26× slower than `qwen-custom`; the dense 27B stays
  CPU-bound. A smaller quant (`:iq3`, 11 GB) is the only path to more on-GPU.
- Tuned gemma is ~9% slower than the raw base (101.6 vs 111) — the system-prompt
  stack + sampler params; both 100% GPU. Expected, negligible.

### gemma-content context ceiling (2026-06-02)

How high can `num_ctx` go before `gemma-content` spills? It doesn't — the Q6 base
holds its **full native 131k window 100% on-GPU**:

| num_ctx | gen tok/s | GPU split |
|---|---|---|
| 16384 | 103.9 | 100% GPU |
| 32768 | 104.7 | 100% GPU |
| 65536 | 105.4 | 100% GPU |
| 131072 (native max) | 105.6 | 100% GPU |

Context is effectively free (sliding-window attention → tiny KV; no speed
penalty). **Both gemma builds now ship `num_ctx 131072`.**

### Head-to-head vs base, per area (2026-06-02, 3 attempts — preliminary)

Granite rebuilt at `num_ctx 12288` first (was drifted to 16384; the drop pulled
it fully on-GPU → 31→98 tok/s).

**Content (`run.py`):**

| Model | Clean | Tok/s |
|---|---|---|
| **gemma-content** | 3/3 (100%) | 106 |
| granite-coder | 1/3 (33%) | 99 |
| qwen-custom | 1/3 (33%) | 87 |

**Coding (`run-code.py`):**

| Model | Pass | Tok/s |
|---|---|---|
| **granite-coder** | 18/18 (100%) | 98 |
| gemma-coder | 17/18 (94%) | 106 |
| gemma-content | 16/18 (89%) | 102 |
| qwen-custom | 16/18 (89%) | 86 |

**Findings (n=18 — directional, confirm at 5 attempts):**
- **Content: `gemma-content` clear winner** — 100% clean + fastest. Q6 held quality.
- **Coding: `granite-coder` still on top (100%)**, but `gemma-coder` is right behind
  at **94% and faster** — a big jump from old gemma's 73% (Q6 + coding overlay). The
  whole gap is one `calc` attempt (field-ceiling task: granite 3/3, gemma-coder 2/3).
- **Coding overlay helps gemma**: gemma-coder 17/18 vs gemma-content 16/18 (+1 on calc).
- **Granite ctx fix is a real win**: 12288 → ~98 tok/s (3× the spilling 16384).
- Margins are within noise at 3 attempts — confirm with 5 before any coding-consolidation call.

## Removal reasoning

- **`llama-custom` — REMOVED (2026-05-31).** Last or near-last on every axis
  (content 20%, coding 73%, teaching 6.8). Won no role.
- **`ministral-custom` — REMOVED (2026-05-31).** Genuine #2 across the board, but
  redundant once gemma + granite cover all three roles.
- **`qwen-custom` — KEEP as the thinking-on experimental model.** Casey's general
  daily-driver; `~/Apps/jobhunt` runs bare `qwen3.5:9b`. Lineup lists it as
  `qwen-custom:think`.

---

## Decision (2026-06-02)

**`gemma-content` (`batiai/gemma4-e4b:q6`) is the clear winner of the spillover
round** — 100% on-GPU, ~100 tok/s, higher quality than the old Q4_K_M, and small
enough (6.2 GB) to leave GPU headroom. **The dense-27B path is abandoned:** even
at iq4/iq3 it stays CPU-bound (~3 tok/s, ~26× slower than `qwen-custom`).
`qwen-big` (build script + model) is being retired.

## Plans

- [x] **Gemma quant upgrade (2026-06-02).** Rebuilt `gemma-content` from
  `batiai/gemma4-e4b:q6` — 100% GPU, ~102 tok/s, ~2.4× the old Q4_K_M. Use the
  ollama.com build, **not** the raw `hf.co/unsloth/...` GGUF (Ollama 0.23.2's
  llama.cpp engine errors `unknown model architecture: 'gemma4'`; the native
  engine that runs ollama-registry builds is fine).
- [x] **27B quant path evaluated + rejected (2026-06-02).** `:iq4` (15 GB) =
  3.3 tok/s @ 39% GPU; still CPU-bound. `qwen-big` retired (no `:iq3` follow-up —
  anything ≥11 GB on a dense 27B stays heavily on CPU).
- [x] **Gemma split into two builds (2026-06-02).** `gemma-content` (`prose`,
  num_ctx 131072) + `gemma-coder` (`coding` overlay, `repeat_penalty 1.15`). Both
  off `batiai/gemma4-e4b:q6` at 131072 (free on-GPU — see ctx ceiling above).
- [x] **Head-to-head vs base, per area (2026-06-02, 3 attempts).** Content:
  `gemma-content` 100% clean (clear winner). Coding: `granite-coder` 100% vs
  `gemma-coder` 94% — granite holds, gemma-coder close + faster. See subsection above.
- [ ] **Confirm coding at 5 attempts.** The granite-vs-gemma-coder gap (one `calc`)
  is within noise at n=18; rerun `run-code.py --attempts 5` before any consolidation.
- **Coding side = two purpose-built models:**
  - `gemma-coder` — autocomplete / small coding tasks (produces code; 94% pass@1).
  - **tutor** — teaching assistant that *refuses to solve*: shows 2–3 approaches +
    the "why", Socratic, builds learning plans, adapts to a curated
    `memory/learning-profile.md` (role-gated, tutor-only injection).
- [x] **Tutor Phase 1 (2026-06-02).** `tutor` overlay + learning-profile +
  `gemma-tutor` & `granite-tutor` built. Smoke test: asked for a full solution,
  gemma-tutor refused and gave 3 approaches + a Socratic question. ✔
- [x] **Tutor Phase 2 (2026-06-02).** Added `run-tutor.py` and `eval/tutor_tasks.py`.
  Built a leak-gated tutor rubric where full solutions that pass hidden tests
  score 0, and responses are judged by a leave-one-out panel on teaching quality.
  Smoke test: `./eval/run-tutor.py --models gemma-tutor --judges granite-tutor --tasks two_sum --attempts 1`. ✔
- [x] **Tutor Phase 3 (2026-06-02).** Ran `./eval/run-tutor.py --models gemma-tutor granite-tutor --judges gemma-coder granite-coder --attempts 3`.
  Winner: `gemma-tutor` 9.7/10 vs `granite-tutor` 9.6/10, leak rate 0/15.
  Summary: `eval/runs/20260602T201423Z/tutor/summary.md`.

## Big-model exploration (2026-06-02)

Two bigger BatiAI quants pulled to test whether more capacity is worth the spill,
targeting **content, tutor, and a general thinking driver** (not coding —
`granite-coder`/`gemma-coder` already cover that).

**Phase A — viability triage (ctx capped 8192, num_predict 150):**

| Model | Arch | Size | Gen tok/s | GPU/CPU | Verdict |
|---|---|---|---|---|---|
| `batiai/gemma4-26b:iq4` | gemma4 dense | 13 GB | 27.8 | 43%/57% | viable |
| `batiai/qwen3.6-35b:q4` | qwen35**moe** | 18 GB | 13.2 | 58%/42% | viable |

**Finding — MoE beats the dense-spillover curse.** The 35B MoE spills *more* than
the dead dense `qwen-big` (18 vs 17 GB) yet runs **13.2 tok/s vs 2.9** (~4.5×):
MoE activates only a few experts per token, so per-token bandwidth ≪ 18 GB. So
`qwen-big`'s lesson ("dense ≥15 GB = dead on this GPU") does **not** generalize to
MoE — both new models are usable.

- [ ] **Phase B — build custom variants** for the target roles: `gemma4-26b` →
  content (`prose`) + tutor; `qwen3.6-35b` (MoE, thinking-capable) → thinking
  driver + tutor.
- [ ] **Phase C — quality eval vs incumbents:** `run.py` (content), `run-tutor.py`
  (tutor, leak-gated), thinking-driver check. The bigger model must beat the
  on-GPU incumbent by enough to justify the speed drop (28/13 vs ~100 tok/s).

## Done

- [x] **Content overlay check (2026-05-31)** — A/B'd `gemma-content` coding vs
  `prose` overlay on content: both 5/5 clean, same speed; switched to `prose`
  (reads marginally more natural, purpose-built for human-like writing).
- [x] **Removals executed (2026-05-31)** — `llama-custom` + `ministral-custom`
  `ollama rm`'d; build scripts and `models/` dirs deleted; `DEFAULT_MODELS` and
  docs trimmed to the 3-model lineup.
