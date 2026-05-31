# Benchmark Results & Model-Selection Decision

_Last run: 2026-05-29 (5 attempts/model, debiased, thinking-off). Decision recorded: 2026-05-31._

## Goal

Consolidate the 5-model lineup into **3 purpose-built custom models**, one per
task, keeping whichever base benchmarks best per role (a base may back two roles
via different prompt overlays):

1. **Content generation** — mirror a human / strong baseline.
2. **Coding assistant** — implement small code sections, with Claude as orchestrator (correctness-first).
3. **Coding tutor** — teach in a specific way (feature spec pending — role deferred).

## Decision

| Role | Model | Basis |
|---|---|---|
| Content generation | **`gemma-custom`** (gemma4:e2b) | Won content 5/5 clean (100%) @ 180 tok/s — 2× fastest in the field. |
| Coding assistant | **`granite-custom`** (granite4.1:8b) | Highest pass@1 (90%, 27/30) @ 1.7 s/call. Correctness-first per decision. |
| Coding tutor | **`granite-custom`** + `tutor` overlay _(deferred)_ | Frontrunner on the current teaching test (9.9/10, panel-judged). Final pick waits for the tutor feature spec. |

**Keepers:** `gemma-custom` (now on the `prose` overlay — see overlay check below),
`granite-custom` (backs both coding roles via overlays), and `qwen-custom` —
retained as the **thinking-on experimental model** (the eval lineup tests it as
`qwen-custom:think` so further runs measure it with thinking enabled).
**Removal candidates:** `llama-custom`, `ministral-custom` — see below.

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

## Removal reasoning

- **`llama-custom` — REMOVED (2026-05-31).** Last or near-last on every axis
  (content 20%, coding 73%, teaching 6.8). Won no role.
- **`ministral-custom` — REMOVED (2026-05-31).** Genuine #2 across the board
  (content 5/5 but ~half gemma's speed; coding 83%; teaching 9.0), but redundant
  once gemma + granite cover all three roles.
- **`qwen-custom` — KEEP as the thinking-on experimental model.** Retained
  outside the 3 task roles; it's Casey's general daily-driver and `~/Apps/jobhunt`
  runs bare `qwen3.5:9b`. The eval lineup now lists it as `qwen-custom:think`
  (thinking on by default) to keep testing it with deliberation enabled.

## Open items

- [ ] **Tutor feature spec** — define the "specific way" the tutor should teach;
  then build a `tutor` role overlay and benchmark (extend `run-learn.py` to score
  those features). Granite is the base to beat.
- [x] **Content overlay check (2026-05-31)** — A/B'd `gemma-custom` (coding overlay)
  vs gemma+`prose` overlay on content: **both 5/5 clean, same speed**. The structural
  metric ties; prose reads marginally more natural and is purpose-built for human-like
  writing, so **gemma-custom switched to the `prose` overlay** for the content role.
- [x] **Removals executed (2026-05-31)** — `llama-custom` + `ministral-custom`
  `ollama rm`'d; build scripts and `models/` dirs deleted; `DEFAULT_MODELS` and
  docs trimmed to the 3-model lineup. `qwen-custom` kept (thinking-on).
