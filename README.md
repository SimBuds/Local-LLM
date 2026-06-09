# AI Context Stack

Layered Markdown prompts compiled into local Ollama models, plus an eval suite to
pick the best model for each job. There is no fine-tuning here: behavior comes
from `prompts/`, durable memory, reusable knowledge files, and each model
builder's sampler/context params.

**What this is for:** running a small, opinionated set of local models on one
workstation, wiring them into editor assistants (Continue / Cline), and keeping
an evidence-based record of which model wins which task.

**What the testing is for:** every model in the lineup is benchmarked on speed,
coding, content/SEO, learning, and leak-gated tutoring so the "which model"
decision is measured, not guessed.

This README is the **guide**: what the project is, how to build and run models,
how to run the evals, and how to plug the models into VSCode. The benchmark
**record** — full runner docs, safety notes, history, and detailed results —
lives in [`TESTING.md`](TESTING.md).

## Models

Current lineup:

| Model | Base | ctx | Role |
|---|---|---:|---|
| `qwen` | `qwen3.6:35b-a3b-mtp-q4_K_M` | 32K Context | Patient reasoning/coding model; best coding and learning scores. |
| `gemma` | `gemma4:12b-it-q4_K_M` | 32K Context | Content model; best content generation scores. |

`qwen` is a
35B MoE/MTP model: it spills heavily to CPU, but still clears the local usability
floor and wins the reasoning-heavy benchmarks.

## Quickstart


```bash
./build-qwen
ollama run qwen
```

Each `build-*` script assembles the prompt stack, writes
`models/<name>/system.txt` and `models/<name>/Modelfile`, then runs
`ollama create <name> -f models/<name>/Modelfile`.

## Structure

```text
.
├── prompts/              # behavior controls; runs every turn
├── memory/user.md        # durable user profile
├── knowledge/**/*.md     # reusable reference context
├── eval/                 # benchmark runners and tasks
├── models/<name>/        # generated system.txt + Modelfile
└── build-qwen
```

Prompt assembly order is `knowledge/`, then `memory/`, then `prompts/`; files
within each directory are sorted. That keeps reference context first and behavior
rules last. Each Markdown file is wrapped in `--- START/END FILE ---`. Files over
100k are skipped. Builders abort if the assembled prompt contains `"""`, because
that would break the Ollama `SYSTEM """..."""` block.

## Build And Tune

The only model-specific part of a builder is the top config block:

```bash
MODEL_NAME="qwen"
BASE_MODEL="qwen3.6:35b-a3b-mtp-q4_K_M"
EXTRAS=()
PARAMS=( # Context: 262144 - 131072 - 65536 - 32768 - 16384 - 8192 - 4096
  'num_ctx 32768'         # 32k: The sweet spot for multi-file local tasks
  'temperature 0.2'       # Low temperature forces strict compliance with code syntax and tool tags
  'top_p 0.95'
  'top_k 40'
  'min_p 0.05'            # Safeguards structural format without restricting code vocabulary
  'presence_penalty 0.0'  # MUST BE ZERO. Coding requires reusing exact variable names.
  'repeat_penalty 1.05'   # Prevents infinite code loops without breaking boilerplate code
)

```

For a new model, copy an existing `build-*` script and edit only that config
block. The shared assembly section below the divider is mirrored across builders
and should stay byte-identical.

Where changes belong:

| Change | File |
|---|---|
| Behavior rule for all models | `prompts/` |
| Stable user preference/fact | `memory/user.md` |
| Reusable technical reference | `knowledge/` |
| New coding eval task | `eval/coding_tasks.py` |
| New learning/tutor eval task | `eval/learning_tasks.py` / `eval/tutor_tasks.py` |

Keep prompt text terse. Every prompt token is spent every turn; prefer removing
bad rules or tuning `PARAMS` before adding more instructions.

## Ollama Server

Local service override:

```ini
# sudo systemctl edit ollama
[Service]
Environment="OLLAMA_KV_CACHE_TYPE=q4_0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
Environment="OLLAMA_KEEP_ALIVE=10m"
```

Common commands:

```bash
sudo systemctl status ollama
sudo systemctl edit ollama
sudo systemctl daemon-reload
sudo systemctl restart ollama

ollama list
ollama ps
ollama show gemma
ollama run gemma
ollama run qwen
```

## Use In VSCode (Continue / Cline)

Both extensions talk to Ollama's local API at `http://localhost:11434`. Build the
models first (`./build-qwen`, `./build-gemma`) so the custom names resolve, then
confirm they are loaded with `ollama list`.

### Continue

Add the built models to `~/.continue/config.yaml` (Continue's provider name for
Ollama is `ollama`; `model` is the Ollama model name):

```yaml
models:
  - name: qwen (coding/learning)
    provider: ollama
    model: qwen
    roles: [chat, edit, apply]
  - name: gemma (content/fast)
    provider: ollama
    model: gemma
    roles: [chat, edit, apply]
```

Pick `qwen` for coding/reasoning and `gemma` for fast content per the
leaderboard above. Continue auto-discovers Ollama, but listing the custom names
keeps the prompt-stacked builds (not the raw bases) in the model picker.

### Cline

In Cline's settings, set **API Provider** to `Ollama`, **Base URL** to
`http://localhost:11434`, and **Model** to `qwen` or `gemma`. Cline is
agentic/coding-heavy, so `qwen` is the better default there; switch to `gemma`
when you want speed and the task is lighter.

Notes for both: keep `OLLAMA_KEEP_ALIVE` long enough to avoid reload churn when
switching models, and remember `qwen` spills to CPU on this box (slower first
token, ~40 tok/s) while `gemma` stays fully on GPU.

## Evaluation

Runners live under `eval/` and write results to `eval/runs/<UTC>/`.

```bash
./eval/run-speed.py --models qwen gemma
./eval/run-code.py --models qwen gemma
./eval/run-content.py --models qwen gemma
./eval/run-learn.py --models qwen gemma
./eval/run-tutor.py --models qwen gemma
./eval/run-json.py --models qwen gemma
```

`run-json.py` is the structured-output test the consumer apps (Jobhunt,
SEO-LLM) depend on: it constrains decode with a JSON schema, buries facts in a
multi-thousand-token document, and scores schema conformance plus long-context
fact recall. It pins `num_ctx 32768` to match how those apps call Ollama.

`run-code.py`, `run-learn.py`, and `run-tutor.py` execute model-generated Python
with timeouts but are not containerized. Run trusted models only. Full runner
flags and safety details are in [`TESTING.md`](TESTING.md).

## Benchmark Leaderboard

Latest Gemma/Qwen head-to-head: 2026-06-09 (all five suites from that run).
Note: `gemma` was rebuilt with new params after this run, so these numbers
predate the current build — re-run the suites before relying on them. The
`run-json.py` (schema/long-context) suite is wired in but not yet benchmarked.

| Suite | Winner | `gemma` | `qwen` |
|---|---|---:|---:|
| Speed | `gemma` | 58.3 tok/s, 100% GPU | 40.6 tok/s, 75%/25% CPU/GPU |
| Coding | `qwen` | 29/30 | 30/30 |
| Content | `gemma` | 5/5 clean | 5/5 clean |
| Learning | `qwen` | 9.4/10, code 12/12 | 9.9/10, code 12/12 |
| Tutor (leak-gated) | `gemma` | 8.0/10, leaks 2/15 | 5.9/10, leaks 6/15 |

Current picks:

| Use | Pick | Reason |
|---|---|---|
| Content / SEO / copy | `gemma` | 100% clean in content run; faster and fully on GPU. |
| Coding puzzles / small functions | `qwen` | Latest run swept 30/30; `gemma` dropped one `calc`. |
| Learning explanations | `qwen` | Latest `run-learn.py` teach score: 9.9/10 with code 12/12. |
| Socratic tutoring (no spoilers) | `gemma` | Leak-gated `run-tutor.py`: 8.0/10 with only 2/15 leaks vs `qwen`'s 6/15. |
| Fast local general use | `gemma` | Best fit/speed and no CPU spill. |

## Models Tested

Current lineup, scored out of 10 per suite (speed normalized to the fastest
result; 2026-06-09 run):

```text
                gemma                          qwen
Speed    10.0  ████████████████████  |  7.0  ██████████████
Coding    9.7  ███████████████████   | 10.0  ████████████████████
Content  10.0  ████████████████████  | 10.0  ████████████████████
Learning  9.4  ███████████████████   |  9.9  ████████████████████
Tutor     8.0  ████████████████      |  5.9  ████████████
```

Full roster (current and retired). See [`TESTING.md`](TESTING.md) for the reasoning:

| Model | Base | Status |
|---|---|---|
| `gemma` | `gemma4:12b-it-q4_K_M` | current — content/speed/tutor pick |
| `qwen` | `qwen3.6:35b-a3b-mtp-q4_K_M` | current — coding/learning pick |
| `gemma-custom` | `gemma4:e4b` | removed — superseded by gemma4 12B |
| `granite-custom` | `granite4.1:8b-Q5_K_M` | dropped — strong prior coding, no longer leads |
| `qwen-custom` | `qwen3.5:9b` | removed — superseded by Qwen3.6 MoE |
| `ministral-custom` | `ministral-3:8b` | removed — historical #2 |
| `llama-custom` | `llama3.1:8b` | removed — trailed in early runs |
| `gemma-big` | `gemma3:27b` | retired — lost the quality/speed tradeoff on this box |

## Hardware Envelope

Benchmarks are for this box: RTX 3080 10 GB, Ryzen 5900x, 32 GB DDR4-3600.
Models that fit 100% on GPU run fast. Dense spillover is usually too slow; MoE
spillover can remain usable because fewer parameters are active per token.

## Docs

- [`AGENTS.md`](AGENTS.md): workflow contract for coding agents.
- [`TESTING.md`](TESTING.md): testing source of truth, runner docs, safety notes,
  benchmark history, and detailed results.
