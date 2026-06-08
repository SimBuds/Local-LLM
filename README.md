# AI Context Stack

Layered Markdown prompts compiled into local Ollama models. There is no
fine-tuning here: behavior comes from `prompts/`, durable memory, reusable
knowledge files, and each model builder's sampler/context params.

The repo also includes an eval suite. `README.md` keeps the operational view and
current leaderboards; [`TESTING.md`](TESTING.md) is the source of truth for
runner usage, safety notes, run history, and detailed benchmark notes.

## Models

Current lineup:

| Model | Base | ctx | Role |
|---|---|---:|---|
| `qwen` | `qwen3.6:35b-a3b-mtp-q4_K_M` | 32K Context | Patient reasoning/coding model; best coding and learning scores. |

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
# /etc/systemd/system/ollama.service.d/override.conf
[Service]
Environment="OLLAMA_KV_CACHE_TYPE=q4_0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_KEEP_ALIVE=-1"
```

Common commands:

```bash
systemctl status ollama
systemctl edit ollama
sudo systemctl daemon-reload
sudo systemctl restart ollama

ollama list
ollama ps
ollama show gemma
ollama run gemma
ollama run qwen
```

`OLLAMA_CONTEXT_LENGTH` is a server-side ceiling above the model `num_ctx`.
KV-cache quantization is also server-side and requires flash attention.

## Evaluation

Runners live under `eval/` and write results to `eval/runs/<UTC>/`.

```bash
./eval/run-speed.py --models qwen
./eval/run-code.py --models qwen
./eval/run-content.py --models qwen
./eval/run-learn.py --models qwen
./eval/run-tutor.py --models qwen
```

`run-code.py`, `run-learn.py`, and `run-tutor.py` execute model-generated Python
with timeouts but are not containerized. Run trusted models only. Full runner
flags and safety details are in [`TESTING.md`](TESTING.md).

## Benchmark Leaderboard

Latest Gemma/Qwen head-to-head: 2026-06-07. The leak-gated tutor runner was not
completed in that pass; previous tutor history remains in [`TESTING.md`](TESTING.md).

| Suite | Winner | `gemma` | `qwen` |
|---|---|---:|---:|
| Speed | `gemma` | 56.9 tok/s, 100% GPU | 34.9 tok/s, 77%/23% CPU/GPU |
| Coding | `qwen` | 27/30 | 30/30 |
| Content | `gemma` | 5/5 clean | 4/5 clean |
| Learning | `qwen` | 9.2/10, code 12/12 | 10.0/10, code 12/12 |

Current picks:

| Use | Pick | Reason |
|---|---|---|
| Content / SEO / copy | `gemma` | 100% clean in content run; faster and fully on GPU. |
| Coding puzzles / small functions | `qwen` | Latest run swept 30/30, including `calc` 5/5. |
| Learning explanations | `qwen` | Latest `run-learn.py` score: 10.0/10 with code 12/12. |
| Fast local general use | `gemma` | Best fit/speed and no CPU spill. |

## Hardware Envelope

Benchmarks are for this box: RTX 3080 10 GB, Ryzen 5900x, 32 GB DDR4-3600.
Models that fit 100% on GPU run fast. Dense spillover is usually too slow; MoE
spillover can remain usable because fewer parameters are active per token.

## Docs

- [`AGENTS.md`](AGENTS.md): workflow contract for coding agents.
- [`TESTING.md`](TESTING.md): testing source of truth, runner docs, safety notes,
  benchmark history, and detailed results.
