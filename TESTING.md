# Testing

This is the source of truth for how this repo evaluates local Ollama models:
runner usage, safety notes, current benchmark results, and historical testing
decisions. `README.md` only carries the operational summary and compact
leaderboards.

## Goals

The suite answers three practical questions:

1. **Can the model follow content instructions?** Format discipline, SEO keyword
   control, length, and Markdown structure.
2. **Can the model write correct code?** Pass@1 on small self-contained Python
   tasks with hidden asserts.
3. **Can the model teach without leaking?** Explanation quality, code gate, and
   solution-leak checks for tutor use.

Speed is tracked separately because a better model that is too slow is not a
usable local default.

## Safety

`run-code.py`, `run-learn.py`, and `run-tutor.py` execute model-generated Python
in a subprocess with a fresh temp working directory and wall-clock timeout. They
are **not containerized**. Run trusted local models only.

Do not point the execution runners at newly-pulled community models without
reviewing the risk.

## Runner Matrix

All runners write to `eval/runs/<UTC>/`.

| Runner | Measures | Executes model code? | Default attempts |
|---|---|---:|---:|
| `run-speed.py` | Raw generation tok/s, prompt tok/s, load time, GPU/CPU split | no | 1 per prompt |
| `run-content.py` | SEO/content format compliance | no | 5 |
| `run-code.py` | Real pass@1 against hidden Python asserts | yes | 5 per task |
| `run-learn.py` | Code + explanation, leave-one-out judge panel | yes | 3 per task |
| `run-tutor.py` | Leak-gated tutoring guidance | yes | 3 per task |

Common flags:

```bash
--models NAME
--attempts TIMES
--timeout SECONDS
```

Runner-specific flags:

| Runner | Extra flags |
|---|---|
| `run-speed.py` | `--num-predict N`, `--thinking auto|on|off`, `--opt KEY=VAL` |
| `run-code.py` | `--tasks ...`, `--exec-timeout SECONDS`, `--thinking auto|on|off` |
| `run-content.py` | `--prompt-file PATH`, `--keyword TEXT`, `--thinking auto|on|off` |
| `run-learn.py` | `--tasks ...`, `--judges ...`, `--exec-timeout SECONDS`, `--thinking auto|on|off` |
| `run-tutor.py` | `--tasks ...`, `--judges ...`, `--exec-timeout SECONDS`, `--thinking auto|on|off` |

Thinking mode can be forced with `--thinking on`, disabled with `--thinking off`,
or selected per model by appending `:think` to the model spec. Do not use
thinking mode for content runs unless explicitly testing it.

## Standard Commands

Full current comparison:

```bash
./eval/run-speed.py --models qwen
./eval/run-code.py --models qwen
./eval/run-content.py --models qwen
./eval/run-learn.py --models qwen
./eval/run-tutor.py --models qwen
```

Quick smoke comparison:

```bash
./eval/run-speed.py --models qwen --num-predict 128
./eval/run-code.py --models qwen --tasks two_sum calc --attempts 2
./eval/run-content.py --models qwen --attempts 2
```

Add tasks in:

| Task type | File |
|---|---|
| Coding correctness | `eval/coding_tasks.py` |
| Code + learning explanation | `eval/learning_tasks.py` |
| Leak-gated tutoring | `eval/tutor_tasks.py` |

## Current Benchmark Snapshot

Latest head-to-head: `gemma` (`gemma4:12b-it-q4_K_M`) vs `qwen`
(`qwen3.6:35b-a3b-mtp-q4_K_M`) on 2026-06-09. All five suites — speed, coding,
content, learning, and the leak-gated tutor runner — completed this pass.

### Speed (`run-speed.py`)

Run: `eval/runs/20260609T101024Z/speed/summary.md`

| Rank | Model | Think | Gen tok/s | Prompt tok/s | Load | Size | GPU/CPU split |
|---|---|---|---:|---:|---:|---:|---|
| 1 | `gemma` | off | 58.3 | 16902 | 13.6s | 7.7 GB | 100% GPU |
| 2 | `qwen` | off | 40.6 | 1689 | 0.3s | 28 GB | 75%/25% CPU/GPU |

Finding: Gemma is the fast local default. Qwen remains usable despite heavy CPU
spill, but it pays a load-time and throughput cost.

### Coding (`run-code.py`)

Run: `eval/runs/20260609T101105Z/code/summary.md`

| Rank | Model | Pass rate | Passed | Avg s | Tok/s |
|---|---|---:|---:|---:|---:|
| 1 | `qwen` | 100% | 30/30 | 4.8 | 57 |
| 2 | `gemma` | 97% | 29/30 | 5.1 | 60 |

Per task:

| Model | two_sum | valid_parentheses | merge_intervals | lru_cache | edit_distance | calc |
|---|---:|---:|---:|---:|---:|---:|
| `qwen` | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| `gemma` | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 4/5 |

Finding: Qwen is the current coding leader. Gemma's single miss was again on
`calc`, the suite's known operator-precedence stress task (improved from 2/5 to
4/5 versus the prior run).

### Content (`run-content.py`)

Run: `eval/runs/20260609T101604Z/content/summary.md`

| Rank | Model | Clean rate | Clean | Avg s | Tok/s | Avg words | Keyword hits |
|---|---|---:|---:|---:|---:|---:|---|
| 1 | `gemma` | 100% | 5/5 | 10.1 | 60 | 388 | 3-4 |
| 2 | `qwen` | 100% | 5/5 | 15.5 | 35 | 322 | 2-4 |

Finding: Both models were 5/5 clean this pass. Gemma keeps the content/SEO pick
on speed (60 vs 35 tok/s, fully on GPU); Qwen closed the format gap from the
prior run.

### Learning (`run-learn.py`)

Run: `eval/runs/20260609T101812Z/learn/summary.md`

| Rank | Model | Teach /10 | Code pass | Explanation /10 | Expl. when correct |
|---|---|---:|---:|---:|---:|
| 1 | `qwen` | 9.9 | 12/12 | 9.9 | 9.9 |
| 2 | `gemma` | 9.4 | 12/12 | 9.4 | 9.4 |

Finding: Both models passed every code gate. Qwen again won explanation quality
in the leave-one-out judge panel.

### Tutor (`run-tutor.py`, leak-gated)

Run: `eval/runs/20260609T102550Z/tutor/summary.md`

| Rank | Model | Teach /10 | Leaks | Explanation /10 | Explanation (no leaks) /10 |
|---|---|---:|---:|---:|---:|
| 1 | `gemma` | 8.0 | 2/15 | 9.0 | 9.2 |
| 2 | `qwen` | 5.9 | 6/15 | 9.7 | 9.8 |

Finding: This is the inverse of the open learning run. Qwen explains better when
it does not leak (9.8 vs 9.2), but it gives away the full solution far more often
(6/15 vs 2/15), and the gate zeroes those attempts. Gemma's discipline makes it
the leak-gated tutoring pick.

## Current Picks

| Use | Pick | Basis |
|---|---|---|
| Fast local default | `gemma` | 58.3 tok/s, 100% GPU. |
| Content / SEO / copy | `gemma` | 5/5 clean and ~2x faster than Qwen in latest content run. |
| Coding puzzles / small functions | `qwen` | 30/30 in latest coding run, including `calc` 5/5. |
| Learning explanations | `qwen` | 9.9/10 in latest learning run, code 12/12. |
| Leak-gated tutoring | `gemma` | 8.0/10 with only 2/15 leaks vs Qwen's 6/15. |

## Hardware

Benchmarks are for this local machine:

| Component | Value |
|---|---|
| GPU | RTX 3080, 10 GB VRAM |
| CPU | Ryzen 5900x |
| RAM | 32 GB DDR4-3600 |
| Ollama | 0.30-era testing for current Qwen/Gemma runs |

Models that fit 100% on GPU are fast. Dense spillover usually collapses
generation speed because DDR4 bandwidth becomes the bottleneck. MoE spillover is
less punishing because only a subset of parameters is active per token.

## Models Tested

| Model | Status | Notes |
|---|---|---|
| `gemma` (`gemma4:12b-it-q4_K_M`) | current | Fast, fully on GPU in latest speed run, best content compliance. |
| `qwen` (`qwen3.6:35b-a3b-mtp-q4_K_M`) | current | Best current coding/learning result; heavy CPU spill but usable. |
| `granite` (`granite4.1:8b-Q5_K_M`) | dropped | Strong prior coding runs, but no longer leads the current lineup. |
| `qwen-custom` (`qwen3.5:9b`) | removed | Fast 9B-era thinking model; superseded by current Qwen3.6 MoE results. |
| `ministral-custom` | removed | Strong historical #2; removed after Gemma/Granite consolidation. |
| `llama-custom` | removed | Last or near-last in early content/coding/teaching runs. |
| `qwen-big` / `qwen-moe` experiments | promoted/retired variants | Established that MoE can survive spillover; current `qwen` is the promoted MTP MoE line. |
| `gemma-big` | retired | Larger dense Gemma lost quality/speed tradeoffs on this hardware. |

## Historical Notes

The notes below are retained for decision history. Prefer the current snapshot
above when choosing a model today.

### Archived model-selection decision (2026-05-31)

From the 2026-05-29 run, the project first consolidated a larger model field
into purpose-built content/coding/tutor roles:

| Role | Model | Basis |
|---|---|---|
| Content generation | `gemma-content` | 5/5 clean at 180 tok/s. |
| Coding assistant | `granite-coder` | 27/30 pass@1 at 1.7 s/call. |
| Coding tutor | `granite-tutor` | Frontrunner teaching score, final pick deferred. |

Early leaderboards:

| Suite | Winner | Result |
|---|---|---|
| Content | `gemma-content` | 5/5 clean, 180 tok/s. |
| Coding | `granite-coder` | 27/30. |
| Teaching | `granite-coder` | 9.9/10, code 12/12. |
| Speed | `qwen-custom` | 87.8 tok/s, 100% GPU. |

### Quant and context exploration (2026-06-02 to 2026-06-03)

Key outcomes:

- Gemma quant/context sweeps showed sliding-window attention makes high context
  cheap; Gemma stayed fully on GPU at large context in those rounds.
- Dense Qwen3.6 27B spillover was unusable at about 3 tok/s.
- Qwen3.6 MoE spillover was much more viable than dense spillover.
- Granite Q5 was a useful quality bump over Q4 in prior coding tests, but later
  current-lineup testing favored Gemma/Qwen.

### 3x3 role matrix (2026-06-03)

The repo briefly expanded into content/coder/tutor variants across Gemma,
Granite, and Qwen families. Results:

| Role suite | Winner | Result |
|---|---|---|
| Content | `gemma-content` | 4/5 clean, 99 tok/s. |
| Coding | `gemma-coder` | 28/30, 104 tok/s. |
| Tutor | `qwen-tutor:think` | 9.0/10, 1/15 leaks. |

Decision at that point: Gemma for all three roles, mainly because it was fast,
clean, and avoided model reload churn.

### Qwen3.6 MoE promotion (2026-06-03 to 2026-06-06)

The MTP MoE line proved viable despite heavy CPU spill. Earlier speed sweeps put
it around the low-30 tok/s band thinking-off, with later runs showing stronger
coding and tutor behavior. It was promoted into the current `qwen` slot for
reasoning/coding comparison against Gemma.

### Head-to-head update (2026-06-07)

This completed Gemma/Qwen run changed the practical split:

- Gemma remains the fast content model.
- Qwen is now the clear coding and learning model in the local lineup.
- Leak-gated tutor status still needed a current rerun (done 2026-06-09).

### Head-to-head update (2026-06-09)

Full five-suite rerun:

- Speed: Gemma 58.3 tok/s (100% GPU) vs Qwen 40.6 tok/s (75%/25% CPU/GPU).
- Coding: Qwen still 30/30; Gemma improved to 29/30 (`calc` 4/5, up from 2/5).
- Content: both now 5/5 clean; Gemma keeps the pick on speed.
- Learning: Qwen 9.9 vs Gemma 9.4, both code 12/12.
- Tutor (leak-gated, finally rerun): Gemma 8.0/10 with 2/15 leaks beats Qwen
  5.9/10 with 6/15 leaks. Qwen explains better when it does not leak but fails
  the gate more often, so Gemma is the tutoring pick.
