# PLAN.md

The blueprint: what this repo is, how it is put together, and why each load
bearing decision was made. `README.md` says how to run it, `TESTING.md` is the
testing source of truth, and `AGENTS.md` holds the working rules. This file is
the *why*, and it is the place a decision gets reconciled when the other three
each mention a piece of it.

Nothing here is new reasoning. Every claim is consolidated from `README.md`,
`TESTING.md`, `AGENTS.md`, or a comment in a `build-*` script, and every measured
number keeps the value and the date its source recorded.

---

## What this is

A local AI context stack for one workstation. It assembles a system prompt from
plain Markdown, pins per-model runtime settings, and serves the result through
one llama.cpp router that other applications on the same box call over HTTP.

The repo builds two things and owns nothing else:

1. **A prompt stack.** `memory/`, then `prompts/`, assembled
   in that order into `models/<name>/prompt.txt`.
2. **A router preset.** `server.ini` plus one section per model, joined into
   `models/models.ini`, which llama-server reads.

The weights are not part of the repo. Neither is the llama.cpp build, the
deployed preset, or the service unit. Those live outside it and belong to the
operator.

## What this is not

- **Not a serving framework.** llama.cpp's `llama-server` in router mode is the
  runtime. This repo configures it.
- **Not a fine-tuning project.** Nothing is baked into a model. A client that
  does not send `prompt.txt` gets the bare base model, which is deliberate: it
  keeps the stack inspectable and swappable without touching weights.
- **Not multi-user or networked.** One box, one card, `localhost`. `parallel = 1`
  and `--models-max 1` mean one model resident at a time with the whole card, so
  a model's throughput never depends on what else is loaded.
- **Not Ollama.** It was the runtime until 2026-09-14 and was removed from the
  box on 2026-09-15. Nothing may assume port 11434 or its blob store.

---

## Architecture

### The prompt stack is assembled at build time, not at request time

Each `build-*` script concatenates the Markdown files into `system.txt` (with
per-file provenance markers, for humans) and `prompt.txt` (markers stripped, for
the model). The markers are stripped because the model recites them: it would
answer with "Constraints from prompts/...". Clients send `prompt.txt` as the
system message on every request.

Assembly order is user context first and behavior rules last, with files sorted
within each directory. A `knowledge/` directory of reference context used to be
assembled ahead of both, and was removed on 2026-09-16.

`memory/*.md` is gitignored and only `*.example.md` templates are published,
because the assembled prompt carries a real user profile. The builders abort
rather than assemble a stack with no profile: without it the persona suite would
score rules against context the model never received, which reads as model
failure rather than as a missing file.

### One shared sampler baseline

`PARAMS` lives once in `build-common.sh` and applies to every model. A builder
may declare its own, which wins, and that is how a model needing different
decoding gets a separate preset.

The reason is a measurement failure, not tidiness. The 2026-06-14 coding,
learning, and tutor tables compared `gemma` at temperature 0.75 and presence
penalty 0.2 against `qwen` at 0.2 and 0.0, so they measured model times sampler
instead of model, and had to be thrown out. Only `run-json.py` sends sampler
options, and every other suite inherits whatever the preset sets. Until 2026-09-15
the array was copy-pasted into all three builders and staying in sync was manual
discipline.

### The lineup is discovered, not listed

`make` derives its model list from the `build-*` files that exist. Adding a
builder is enough. Before 2026-09-15 this was a hand-maintained variable and a
new builder was silently ignored until someone edited it.

---

## The lineup

| Model | Base | Why it is here |
|---|---|---|
| `gemma` | `gemma4-26b-a4b-it-qat` | 26B A4B MoE at QAT Q4_0. Same weights as the Ollama-era benchmarks, so post-switch deltas measure the runtime rather than the model. |
| `qwen` | `qwen3.6-35b-a3b-mtp` | 35B A3B MoE, the largest model that stays usable on this card. |
| `lite` | `qwen3.5-9b-mtp` | Dense 9B, the only one that fits entirely in 10 GB. Speed anchor and third judge. |

`gemma` and `qwen` are mixture-of-experts: few parameters are active per token,
so spilling experts to system RAM stays usable even though neither fits fully in
VRAM. `lite` is dense but small enough to avoid spillover, which is what makes it
the baseline the other two are measured against.

`lite` also exists for a measurement reason. The learn and tutor suites grade
with a leave-one-out judge panel. With two models that leaves exactly one judge
per response and inter-judge disagreement can never be computed. Three models
means two judges per response and a real disagreement number.

`lite`'s weights are not the Ollama-era ones: that build does not load in
llama.cpp, so it was replaced with unsloth's MTP build at the same quant, and its
scores are not comparable with its Ollama history.

---

## The router is a contract

Other repos on this box call the router directly. The endpoint, the model names,
the context size, and the structured-output shape are therefore interfaces, not
implementation details. Changing any of them is a coordinated change across
repos, not a local edit.

- `POST http://localhost:8080/v1/chat/completions`, OpenAI format.
- `model` is one of `gemma`, `qwen`, `lite`.
- The caller sends its own system message.
- Context is fixed at 65536 tokens by the preset. There is no per-request
  context size, and an oversized prompt returns HTTP 400 rather than being
  silently truncated.

Nothing that uses the server may depend on this repo at runtime. That is why
`scripts/llm` is standalone Python with no repo imports: it is installed into
`~/.local/bin` and has to keep working without the checkout.

---

## Measurement design

The suites answer five questions: content-instruction following, code
correctness, teaching without leaking, driving an agent loop (`run-tools.py`,
added 2026-09-17), and whether the prompt stack actually holds. The last is the
odd one out and the reason `run-persona.py` exists: the others measure the base
model *through* the stack, so a prompt edit that silently breaks a rule passes
all of them. The stack is what this repo builds, so it gets its own regression
suite.

The tool suite exists because the editor-agent pick rested on prompt-ingest
speed, which says nothing about whether a model calls the right function or uses
what the function returned. Its scoring is deterministic, and its tool results
are fixtures rather than live calls, so it stays inside the no-network rule the
rest of the automated suite follows.

Speed is tracked separately, because a better model that is too slow is not a
usable local default.

Two design consequences worth stating plainly:

- **Benchmarks must be repeatable, so nothing may resize itself per run.** This
  is what forces the pinned offload below.
- **Rules have to earn their tokens.** Every prompt rule is spent on every turn,
  so `run-persona.py` can run against `--system-mode baseline` to measure what a
  rule buys over the bare model. Measured 2026-07-28, `bash_block` scored 9/9
  stacked and 9/9 baseline and was deleted. It is the only rule cut, and only
  after being confirmed at zero contribution twice on two different lineups.

Model-generated Python is executed by three runners, confined by bubblewrap since
2026-07-27 when `bwrap` is available. The fallback is not isolation, it is a bare
subprocess, and the runner prints which mode is active at startup. That banner is
the source of truth, not this paragraph.

---

## Locked decisions

Each of these is recorded in the `## Project-specific rules` section of
`AGENTS.md` with the same date. Changing one needs Casey's approval.

| Decision | Date | Why |
|---|---|---|
| `fit = off`, splits pinned per model | 2026-09-14 | Automatic fitting sizes the GPU/CPU split from VRAM free at load time, so a desktop app would change a benchmark's offload between runs. |
| 2 GB fit margin (`gemma` 22, `qwen` 35 at 65536 context) | 2026-09-14, values updated 2026-09-16 | The earlier values fit at llama.cpp's default 1 GB margin crashed with CUDA out of memory under normal desktop use plus a spike. Costs about 13% and 9% generation speed respectively, which Casey accepted. The 2026-09-14 values were 21 and 34 at 32768 context. |
| `CACHE_PROMPT = False` | 2026-09-14 | Cached prompt prefixes make logits not bit-identical, and seed reproducibility is already unresolved. |
| JSON schema sent as `response_format.json_schema.schema` | 2026-09-14 | The top-level `schema` shape shown in the llama-server README was accepted and silently ignored on build 10968, returning `{}`. |
| `PARAMS` identical across builders | 2026-06-14 | The tables that mistake invalidated. |
| The router endpoint, model names, and context size | 2026-09-15 | Jobhunt and SEO-LLM depend on them. |
| `cache-type-k/v = q4_0` | 2026-09-17 | Measured against `q8_0` with the tool suite: no accuracy difference in totals, and `q8_0` costs 0.3 to 0.5 GiB of VRAM per model. |
| `load-mode = none`, `ubatch-size = 1024` (`lite` 512) | 2026-09-17 | Prompt ingest 2.4× (`gemma`) and 2.7× (`qwen`) on build 11022, with model VRAM within 0.2 GiB of the spike-tested values. 2048 was faster but gave up about 400 MiB of that headroom. `lite` stays all-GPU at the 2 GB margin only at 512. |

The pinned splits are not guesses. `./add-model` re-derives one by loading the
model once with `--fit-target 2048` and reading llama.cpp's own fit line, and on
2026-09-15 that reproduced `gemma`'s pinned 21 exactly, from
`31 layers (21 overflowing), 6184 MiB used`.

---

## Hardware envelope

RTX 3080 10 GB, Ryzen 5900x, 32 GB DDR4-3600. Desktop apps hold about 1 GB of the
card at idle, so the usable budget for a model plus its KV cache is closer to
8.6 GB. Models that fit entirely on the GPU run fast. Dense spillover is usually
too slow, while MoE spillover stays usable.

Every number in this repo is for this box. A different card changes the pinned
splits, and the leaderboard becomes a new baseline.

---

## Open questions

Carried here so they are not rediscovered. The working list with full detail is
in `IMPLEMENT.md`, which is untracked and does not survive a clone.

- Seed reproducibility is unresolved, which is why prompt caching stays off for
  evaluation.
- Two persona rules, `unverified` and `fields_echo`, are obeyed by `qwen` and by
  neither of the other two, stacked or not. They are candidates for a rewrite
  rather than a cut, because deleting them would cost `qwen` a rule it does obey.
- The learn rubric is saturated, so the `qwen` and `gemma` ordering on that suite
  should not be trusted until it is rerun with a stricter rubric.
- Runners do not abort mid-run when the server dies, and score a model load
  failure as a model failure.
