# AI Context Stack

Layered-Markdown → customized Ollama models, plus an eval suite that picks the
right model per job. No fine-tuning: customization is prompt + reference context
only. Edit Markdown, run a builder, `ollama create` produces a local model.

## Models

Purpose-built models from one shared prompt stack (`prompts/` + `memory/user.md`)
and a per-model role overlay, picked by benchmark ([TESTING.md](TESTING.md)).
**Locked lineup (2026-06-03): gemma for all three roles** — `gemma-content`,
`gemma-coder`, `gemma-tutor` (the eval default). gemma won content + coding
outright; gemma-tutor takes the tutor role on leak-cleanliness + speed + one-family
simplicity. Two **granite** builds (`granite-coder`, `granite-tutor`) stay built as
the coding/tutor fallback (granite was the prior coding/tutor king; it cratered at
content, so no granite-content). qwen was dropped from the lineup (slow thinking,
separate weights — see history below and [TESTING.md](TESTING.md)). `num_ctx` is
tuned per-family for a 10 GB GPU (splits in [TESTING.md](TESTING.md)).

| Model             | Base                   | ctx   | Role overlay | Notes                                              |
|-------------------|------------------------|-------|--------------|----------------------------------------------------|
| `gemma-content`   | `batiai/gemma4-e4b:q6` | 65536 | `prose`      | 🏆 content (80% clean, ~99 tok/s).                 |
| `gemma-coder`     | `batiai/gemma4-e4b:q6` | 65536 | `coding`     | 🏆 coding (93% pass@1 @ 3 s); `repeat_penalty 1.15`. |
| `gemma-tutor`     | `batiai/gemma4-e4b:q6` | 65536 | `tutor`      | 🏆 tutor — teach 8.4, **0 leaks**.                 |
| `granite-coder`   | `granite4.1:8b`        | 12288 | `coding`     | Coding fallback: solid pass@1 (77%), fast (2.1 s).  |
| `granite-tutor`   | `granite4.1:8b`        | 12288 | `tutor`      | Tutor fallback: teach 8.3; `repeat_penalty 1.2`.    |

Eval winners and full leaderboards: **[TESTING.md](TESTING.md)**.

## Quickstart

```bash
./build-gemma-coder                       # build one model
ollama run gemma-coder                    # run it
```

Each builder assembles the prompt stack, writes
`models/<name>/{system.txt,Modelfile}`, and runs `ollama create <name>`.

## Build

```bash
# content                coder                  tutor
./build-gemma-content    ./build-gemma-coder    ./build-gemma-tutor
                         ./build-granite-coder  ./build-granite-tutor
```

The scripts are mirrored: only the top config block differs (`MODEL_NAME`,
`BASE_MODEL`, `ROLE`, `EXTRAS`, `PARAMS`); the assembly logic below the divider
is byte-identical and must stay in lock-step (incl. the role-gated `LEARNING
PROFILE` injection — emitted only when `ROLE=tutor`). **New model:** copy a script
and edit only the config block (set `EXTRAS` if the base needs
`TEMPLATE`/`RENDERER`/`PARSER`).

## Structure

```
ai/
├── prompts/              # behavior controls (terse, durable — runs every turn)
│   ├── system.md         # core directives
│   ├── personality.md    # voice
│   ├── formatting.md     # output shape
│   ├── safety.md         # operational safety
│   └── roles/            # per-model overlays (selected by $ROLE)
│       ├── coding.md     # *-coder (gemma/granite)
│       ├── prose.md      # *-content (gemma)
│       └── tutor.md      # *-tutor (gemma/granite) — teaches; never full solutions
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

**Sampler params** live in each builder's `PARAMS` block, tuned per role. All
gemma/granite builds run hotter prose/coding sampling (`temperature 0.8`,
`top_p 0.92`, `top_k 40`, `repeat_penalty 1.2` prose / `1.15` coder).

**Context** (`num_ctx`) is set per-family targeting a 10 GB GPU: gemma `65536`,
granite `12288`. gemma's sliding-window attention keeps its KV tiny, so it fits the
larger window 100% on-GPU. The server's `OLLAMA_CONTEXT_LENGTH` is a hard ceiling
on top. Measured GPU/CPU splits are in [TESTING.md](TESTING.md).

**Ollama server** (`sudo systemctl edit ollama.service`):

```ini
[Service]
Environment="OLLAMA_KV_CACHE_TYPE=q4_0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_KEEP_ALIVE=30m"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

KV-cache quantization is server-only (no Modelfile equivalent) and needs flash
attention on to take effect.

## Honest assessment (9 GB usable VRAM)

The 9 GB ceiling caps this box at ~4–9B models. Within that, here's the realistic
read — separating what the benchmarks measured from how the models behave in real work.

**Best pick per task**

| Task | Pick | Why |
|---|---|---|
| Content / SEO / copy | **gemma-content** | 80% clean @ ~99 tok/s; production-useful |
| Coding (small/boilerplate) | **gemma-coder** | 93% pass@1 *and* fastest (3 s); granite ties on speed |
| Tutor / learning | **gemma-tutor** daily; **granite-tutor** fallback | gemma clean (0 leaks) + fast; granite teach 8.3 on a separate base |

Operational reason gemma takes the whole lineup: all three gemma builds share one
6.2 GB base, so with `OLLAMA_MAX_LOADED_MODELS=1` a single warm model covers all
three roles via overlay swaps. granite is separate weights → a reload per switch.

**Where local (gemma) genuinely helps**

- **Content / SEO / marketing copy** — objective format rules, gemma-content passes
  reliably. The one *production-grade* local use.
- **Small/self-contained coding** — boilerplate, single functions, regex, scripts,
  "explain this error." Fast rubber-duck.
- **Learning / upskilling** — the tutor overlay's refuse-to-solve behavior is good pedagogy.
- **Privacy / offline** — nothing leaves the machine.

**Where to reach for a frontier model instead (be realistic)**

- **Real project coding** — multi-file changes, debugging, architecture, unfamiliar
  frameworks. A 4B-effective model hallucinates APIs and loses the thread past a couple
  files. The 93% benchmark is six self-contained algorithm puzzles — it does **not**
  predict real-repo performance.
- **Long-context / whole-repo reasoning** — even at 65k ctx, reasoning *over* that
  context is weak at this size.
- **High-stakes answers** where a subtle mistake is expensive.

**Caveats on the numbers:** coding pass@1 is the most misleading (toy tasks, not real
work). Tutor scores are soft (judged by other small models — trust the leak gate, not
the decimals). Content is the most trustworthy result (objective pass/fail).

**Bottom line:** use gemma as a fast, private first-pass for content + small coding +
learning; offload heavy project work to a frontier model. Raising the local ceiling is
a VRAM decision, not a tuning one — dense models ≥15 GB drop to ~3 tok/s on this card.

## Models tested — history

Every base that's been through the suite, with where it landed. Current lineup =
gemma (3 roles) + granite (coder/tutor) (above). Retired models are gone from
`ollama`/build scripts but kept here for the record.

| Model (base) | Status | Pros | Cons | Good for |
|---|---|---|---|---|
| **gemma** (`batiai/gemma4-e4b:q6`, ~6.2 GB) | **current — locked lineup** | Won content + coding; 100% on-GPU at full ctx (~100 tok/s); e4b sliding-window keeps KV tiny; one base covers 3 roles | 4B-effective → weak on complex/multi-file reasoning | Content/SEO (production), small coding, tutoring — the all-rounder |
| **granite** (`granite4.1:8b`, ~5.3 GB) | current — coder/tutor fallback | Solid pass@1 (77%), fast (2.1 s), small; was the prior coding/tutor king | Cratered at content (0% clean) → no granite-content; no longer leads any role | Coding/tutor fallback on a separate base |
| **qwen** (`batiai/qwen3.5-9b:q6`, ~7.4 GB) | **dropped 2026-06-03** | Only thinking model; best tutor explanations (9.0); strongest raw reasoning | Thinking too slow (~31 s/answer); separate weights → reload per switch; 1 tutor leak | Tutoring when depth > speed (if re-added) |
| `qwen-custom` (`qwen3.5:9b` Q4, ~6.6 GB) | **removed 2026-06-02** | Fast (~88 tok/s), 100% on-GPU; thinking-capable | Superseded by the Q6 qwen builds, then qwen dropped entirely | — (was a separate project) |
| `ministral-custom` | **removed 2026-05-31** | Genuine #2 across all three roles (content 100%, coding 83%, teach 9.0) | Redundant once gemma + granite covered every role | — (was a strong generalist) |
| `llama-custom` | **removed 2026-05-31** | — | Last/near-last on every axis (content 20%, coding 73%, teach 6.8); won no role | — |
| `qwen-big` (qwen3.6 27B dense → 35B MoE) | **retired 2026-06-02** | MoE reasoning beat qwen-custom (escapes dense-spill curse) | 13 t/s (MoE) / 3 t/s (dense) — too slow + too big to co-run; failed content/tutor | Would be viable on bigger/unified-memory VRAM |
| `gemma-big` (`batiai/gemma4-26b:iq4`, 13 GB) | **retired 2026-06-02** | More capacity than e4b | Lost every category; ~4.5× slower (23 t/s); 43% CPU spill | — |

**Hardware limit driving all of this:** RTX 3080 (10 GB, ~9 usable), Ryzen 5900x,
32 GB DDR4-3600. Models that fit 100% on-GPU run fast; anything that spills is
bottlenecked by ~57 GB/s DDR4 (not compute) and gen tok/s falls off a cliff. Dense
models ≥15 GB are effectively dead here (~3 tok/s); MoE survives spill better but
still isn't interactive. Full splits + run history in [TESTING.md](TESTING.md).

## Docs

`AGENTS.md` (agent workflow contract) · [`TESTING.md`](TESTING.md) (testing source of truth: suite, results, plans).

GPU's fully restored. Here's where you actually stand, with current numbers on the new engine:

Model (100% GPU)	tok/s now (0.30 + CUDA13)
gemma-coder	94.8
granite-coder (Q5)	54.7
(granite dropped from ~90 → 55 on the 0.30 engine — worth a look later, but gemma is your driver.)

The honest answer about "Claude Code speeds"
"Claude Code speed" is really two different things, and they have opposite answers on a 9 GB card:

1. Streaming responsiveness (raw tok/s) — you already match or beat it. ✅
Claude/ChatGPT/Gemini stream output at roughly 50–90 tok/s. Your gemma-coder does ~95 tok/s, 100% on-GPU. So the feel of text flying onto the screen faster than you can read — you have that right now, locally. That box is checked.

2. Time-to-a-correct-answer (what actually makes Claude Code feel fast) — not replicable here. ❌
What makes Claude Code productive isn't the token rate — it's that a frontier model gets real, multi-file tasks right in one shot, holds 200k of context, reasons across your repo, and drives tools. A 4–8B local model at 95 tok/s still:

hallucinates APIs and loses the thread past a couple of files,
needs several re-prompts to converge (or never does),
so it's slower to a working result despite higher tok/s. Your own benchmark says this: the 93% pass@1 is six toy algorithm puzzles — it explicitly does not predict real-repo performance.

What that means practically
Tasks local already nails at Claude-snappy speed: small/self-contained coding, regex, boilerplate, "explain this error," content/SEO, tutoring. For these, gemma at 95 tok/s genuinely feels like Claude Code. Use it as your fast, private first-pass.
Real project coding (multi-file, debugging, architecture, unfamiliar frameworks): no 9 GB local setup replaces Claude Code. The gap is model intelligence, not speed — and you've already maxed the tuning levers (quant, ctx, KV cache, flash attn all dialed in).
The only real lever left is VRAM, not config:
24 GB GPU (3090/4090) → 14B–32B dense models on-GPU at usable speed → meaningfully closer to real-coding-usable.
Apple unified memory (M-series, 36 GB+) → runs the big MoEs (like the qwen3.6 you're pulling) at 25–60 tok/s instead of ~13.
Bottom line: you've already got Claude-Code-responsiveness for the tasks this hardware can do well — that's the win from today's fixes. Matching Claude Code on real coding is a VRAM purchase, not a speed-tuning problem. The qwen3.6 MoE you're pulling is the right instinct (MoE survives spill), but at 23 GB on 10 GB it'll land ~13 tok/s — a patient thinker, not a snappy chat.

I also saved the ollama setup as a memory so we never re-run this saga: