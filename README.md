# AI Context Stack

Layered Markdown prompts served with local GGUF models through a llama.cpp
`llama-server` router, plus an eval suite to pick the best model for each job.
There is no fine-tuning here. Behavior comes from `prompts/`, durable memory,
reusable knowledge files, and each model builder's sampler, context and offload
settings.

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

Current lineup (moved to llama.cpp 2026-09-14):

| Model | GGUF (`~/models/gguf/`) | ctx | Offload | Role |
|---|---|---:|---|---|
| `gemma` | `gemma4-26b-a4b-it-qat.gguf` | 32K | 18 MoE layers on CPU | 26B A4B MoE, QAT Q4_0. |
| `qwen` | `qwen3.6-35b-a3b-mtp-q4_K_M.gguf` | 32K | 32 MoE layers on CPU, MTP | 35B A3B MoE. Largest model that stays usable here. |
| `lite` | `qwen3.5-9b-mtp-q4_K_M.gguf` | 32K | all GPU, MTP | Dense 9B. The only one that fits entirely in 10 GB, so it is the speed anchor and 3rd judge. |

`gemma` and `qwen` are the same weights the Ollama-era benchmarks used, copied
byte for byte out of Ollama's blob store. `lite` is not: Ollama's `qwen3.5:9b`
file does not load in llama.cpp, so it was replaced with unsloth's MTP build at
the same Q4_K_M quant, and its scores are not comparable with its Ollama history.
MTP (multi-token prediction) is speculative decoding built into the Qwen GGUFs.

`gemma` and `qwen` are MoE: few active parameters per token, so CPU spillover
stays usable on a 10 GB card even though neither fits fully in VRAM. `lite` is
dense but small enough to avoid spillover altogether, which is what makes it the
baseline the other two are measured against.

`lite` also exists for a measurement reason, not just a speed one: the learn and
tutor suites grade with a leave-one-out judge panel, so a 2-model lineup left
exactly one judge per response and inter-judge disagreement could never be
computed. Three models means two judges per response and a real disagreement
number.

## Quickstart


```bash
cp memory/user.example.md memory/user.md                          # then edit
cp memory/learning-profile.example.md memory/learning-profile.md  # then edit
# stage the three GGUFs in ~/models/gguf first (see Model Files below)
make build        # writes models/models.ini and each model's prompt.txt
make serve        # llama-server router on http://localhost:8080, leave it running
```

Then, from a second terminal in the repo, send one request with the prompt stack
as the system message:

```bash
jq -n --rawfile sys models/qwen/prompt.txt \
  '{model: "qwen", max_tokens: 80, chat_template_kwargs: {enable_thinking: false},
    messages: [{role: "system", content: $sys}, {role: "user", content: "Who are you, in one sentence?"}]}' \
  | curl -s http://localhost:8080/v1/chat/completions -H 'Content-Type: application/json' -d @- \
  | jq -r '.choices[0].message.content'
```

The router loads a model on its first request (a few seconds) and keeps one
model resident at a time.

The assembled system prompt carries a real user profile (skills, clients,
hardware), so `memory/*.md` is gitignored and only the `*.example.md` templates
are published. Seed them before the first build. The builders abort with the
copy commands above rather than quietly assembling a prompt with no profile.

Each `build-*` script assembles the prompt stack and writes three files under
`models/<name>/`: `system.txt` (human debug copy with per-file markers),
`prompt.txt` (what clients send as the system message), and `preset.ini` (the
model's llama-server section). `make build` then joins `server.ini` and every
`preset.ini` into `models/models.ini`, the one file `make serve` reads.
Nothing is baked into the model: a client that does not send `prompt.txt` gets
the bare base model.

## Model Files

The GGUFs live outside the repo in `~/models/gguf` (override with `GGUF_DIR`).
Builders abort naming the missing path if a file is not there.

| Model | File | Where it came from |
|---|---|---|
| `gemma` | `gemma4-26b-a4b-it-qat.gguf` | Copied from Ollama's `gemma4:26b-a4b-it-qat` blob. |
| `qwen` | `qwen3.6-35b-a3b-mtp-q4_K_M.gguf` | Copied from Ollama's `qwen3.6:35b-a3b-mtp-q4_K_M` blob. |
| `lite` | `qwen3.5-9b-mtp-q4_K_M.gguf` | `unsloth/Qwen3.5-9B-MTP-GGUF`, file `Qwen3.5-9B-Q4_K_M.gguf`. |

Check the staged files against the checksums the benchmarks were run with:

```bash
# Runs in: local terminal. Read-only, safe to repeat.
cd ~/models/gguf && sha256sum -c <<'EOF'
4c856523d61d77922dbc0b26753a6bf6208e5d69d80db0c04dcd776832d054c5  gemma4-26b-a4b-it-qat.gguf
d372de8e934898a59e6ccfabc3368474711384d8f1fd4d22d87a3f0a45400cdc  qwen3.6-35b-a3b-mtp-q4_K_M.gguf
e8dd94817e95d6c0939102049d068418269978377b13616c4726235e232841fe  qwen3.5-9b-mtp-q4_K_M.gguf
EOF
```

Each line must print `OK`. `lite` can be downloaded again with
`curl -L --fail -C - -o ~/models/gguf/qwen3.5-9b-mtp-q4_K_M.gguf https://huggingface.co/unsloth/Qwen3.5-9B-MTP-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf`.
The `gemma` and `qwen` copies have no public byte-identical source once Ollama's
store is gone. The closest downloads are `google/gemma-4-26B-A4B-it-qat-q4_0-gguf`
(`gemma-4-26B_q4_0-it.gguf`) and `unsloth/Qwen3.6-35B-A3B-MTP-GGUF`
(`Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`). Neither has been tested on this box, both are
different files, and switching to one starts a new baseline for that model.

## Structure

```text
.
├── prompts/              # behavior controls; runs every turn
├── memory/user.md        # durable user profile (gitignored; see *.example.md)
├── knowledge/**/*.md     # reusable reference context
├── eval/                 # benchmark runners, tasks, and offline unit tests
├── models/<name>/        # generated system.txt, prompt.txt, preset.ini
├── models/models.ini     # generated router preset: server.ini + every preset.ini
├── server.ini            # llama-server settings shared by every model
├── Makefile              # make build / serve / check
└── build-{gemma,qwen,lite}
```

Prompt assembly order is `knowledge/`, then `memory/`, then `prompts/`; files
within each directory are sorted. That keeps reference context first and behavior
rules last. Each Markdown file is wrapped in `--- START/END FILE ---`. Files over
100k are skipped, as are `*.example.md` templates — injecting a template beside
the real file would hand the model two conflicting profiles. The markers stay in
`system.txt` for debugging and are stripped from `prompt.txt`, because models
recite them.

## Build And Tune

The only model-specific part of a builder is the top config block:

```bash
MODEL_NAME="qwen"
BASE_MODEL="qwen3.6-35b-a3b-mtp-q4_K_M.gguf"   # filename under $GGUF_DIR
PARAMS=( # Context: 262144 - 131072 - 65536 - 32768 - 16384 - 8192 - 4096
  'ctx-size = 32768'         # 32k: sweet spot for multi-file local tasks
  'temp = 0.2'               # Low temperature forces strict compliance with code syntax and tool tags
  'top-p = 0.95'
  'top-k = 40'
  'min-p = 0.05'             # Safeguards structural format without restricting code vocabulary
  'presence-penalty = 0.0'   # MUST BE ZERO. Coding requires reusing exact variable names.
  'repeat-penalty = 1.05'    # Prevents infinite code loops without breaking boilerplate code
)
LOAD=(
  'n-cpu-moe = 32'           # expert layers kept in system RAM, pinned (see below)
  'spec-type = draft-mtp'    # MTP speculative decoding, only for GGUFs that carry MTP heads
)
```

Keys are llama-server flag names without the leading dashes. For a new model,
copy an existing `build-*` script and edit only that config block. Builders
source the shared `build-common.sh`, which aborts up front if the GGUF is not in
`GGUF_DIR`, so a stale or retargeted base fails loudly instead of leaving a
half-written preset behind.

**Keep `PARAMS` identical across builders.** Only `run-json.py` sends sampler
options. Every other suite inherits whatever the preset sets, so differing values
across builders make the leaderboard measure model × sampler instead of model.
That mistake invalidated the 2026-06-14 coding, learning, and tutor tables, which
compared `gemma` at `temperature 0.75` / `presence_penalty 0.2` against `qwen` at
`0.2` / `0.0`. If a model needs its own decoding for daily use, make that a
separate preset rather than skewing the shared baseline.

**`LOAD` is per model and pinned.** It holds how the model is split between GPU
and CPU, which has to differ by model size. `server.ini` sets `fit = off`, so the
split is exactly what the builder declares rather than whatever llama.cpp's
automatic fitting picks from the VRAM free at load time (an open browser would
otherwise change a benchmark's offload between runs). The current values are what
`--fit` chose on 2026-09-14 with about 1 GB of desktop VRAM in use. To re-derive
one after a model or hardware change, load the GGUF with fitting on, with the
same `spec-type` its builder uses, and read the fit line. Leaving MTP off gives a
wrong answer, because the MTP layer needs VRAM of its own: without it the recipe
reported `41 layers (30 overflowing)` for `qwen` in testing.

```bash
# Runs in: local terminal, with nothing else holding the GPU. Stop it with Ctrl-C once the line prints.
llama-server -m ~/models/gguf/qwen3.6-35b-a3b-mtp-q4_K_M.gguf -c 32768 -np 1 -fa on \
  -ctk q4_0 -ctv q4_0 --no-mmproj --spec-type draft-mtp --port 8081 -lv 4 2>&1 \
  | grep 'layers (.* overflowing)'
```

A line ending `42 layers (32 overflowing), 7226 MiB used` means `n-cpu-moe = 32`.
Drop `--spec-type draft-mtp` for `gemma`, which has no MTP layer. A model that
fits entirely prints no overflow and needs no `n-cpu-moe`. The answer moves with
free VRAM, which is why it is pinned rather than re-fitted per run: the same
command read `33 overflowing` with 1.5 GB of desktop use instead of 1 GB. Close
apps that hold VRAM before deriving a value.

Where changes belong:

| Change | File |
|---|---|
| Behavior rule for all models | `prompts/` |
| Stable user preference/fact | `memory/user.md` |
| Reusable technical reference | `knowledge/` |
| New coding eval task | `eval/coding_tasks.py` |
| New content eval task | `eval/content_tasks.py` |
| New JSON/long-context eval task | `eval/json_tasks.py` |
| New learning/tutor eval task | `eval/learning_tasks.py` / `eval/tutor_tasks.py` |
| New prompt-stack rule check | `eval/persona_tasks.py` |

Keep prompt text terse. Every prompt token is spent every turn; prefer removing
bad rules or tuning `PARAMS` before adding more instructions. To find out *which*
rules are worth keeping, run
`./eval/run-persona.py --models gemma qwen lite --system-mode baseline` and
compare against a stacked run: a rule the base model already obeys unprompted is
costing tokens for nothing. See *Prompt Stack Value* below for the current
measured answer.

**After editing anything in `prompts/`, `memory/`, or `knowledge/`, rebuild and
run the persona suite** — it is the only suite that tests the stack itself rather
than the base model behind it. That used to be manual discipline; it is now a
target:

```bash
make check   # rebuild only the models whose stack changed, then run run-persona.py
make hook    # install a pre-commit hook that does the same on staged prompt edits
```

`make build` rebuilds without verifying, `make persona` verifies without
rebuilding, and `make clean` drops the stamps to force a full rebuild. Editing a
single `build-*` script rebuilds only that model; editing anything under
`prompts/`, `memory/`, or `knowledge/` rebuilds all three, because every builder
assembles the same stack.

## llama-server

The runtime is llama.cpp's `llama-server` in router mode: one process on port
8080 that starts a child server per model on demand and routes each request by
its `model` field. `make serve` runs it in the foreground over
`models/models.ini`. The repo ships no service unit.

Benchmarks were run on llama.cpp build 10968 (commit `41abbfd59`), built from
source because the AUR `llama.cpp-cuda` package was reported stale. The recipe
pins `g++-15` as the CUDA host compiler for CUDA 13.4. Building with the system
GCC 16 was not tried.

```bash
# Runs in: local terminal, as your user (no sudo). Safe to re-run.
SRC="$HOME/src/llama.cpp"
[ -d "$SRC/.git" ] || git clone https://github.com/ggml-org/llama.cpp "$SRC"
cmake -S "$SRC" -B "$SRC/build" -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86 \
  -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-15 -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_TYPE=Release \
  && cmake --build "$SRC/build" -j 20 --target llama-server \
  && ln -sfn "$SRC/build/bin/llama-server" "$HOME/.local/bin/llama-server" \
  && llama-server --version
```

`server.ini` holds the settings every model shares, and replaces the old Ollama
systemd override:

| Was (Ollama) | Now (llama-server) | Where |
|---|---|---|
| `OLLAMA_NUM_PARALLEL=1` | `parallel = 1` (the server default is 4 slots) | `server.ini` |
| `OLLAMA_FLASH_ATTENTION=1` | `flash-attn = on` | `server.ini` |
| `OLLAMA_KV_CACHE_TYPE=q4_0` | `cache-type-k = q4_0`, `cache-type-v = q4_0` | `server.ini` |
| `OLLAMA_MAX_LOADED_MODELS=1` | `--models-max 1` | `make serve` |
| `OLLAMA_KEEP_ALIVE=10m` | not set (in testing a model stayed loaded until another was requested) | none |
| automatic GPU/CPU split | `fit = off` plus each builder's `LOAD` | `server.ini`, `build-*` |

`--models-max 1` matters for the benchmarks, not just for daily use. With two
resident models they compete for the same 10 GB, so a model's measured
throughput depends on which other model happens to be loaded beside it, and the
leaderboard would be measuring co-residency rather than the model. With one, each
model gets the whole card in turn. `run-learn.py` and `run-tutor.py` are built for
this: they generate every response first, then loop by judge, so each model loads
once per phase instead of thrashing on every call.

The eval runners reach the router through `eval/_ollama.py`, which reads
`LLM_URL` (default `http://localhost:8080`). It sends `prompt.txt` as the system
message and turns prompt caching off on every call, because the llama-server docs
warn that cached prefixes make results not bit-identical and reproducibility is
already the weak spot here (see [`TESTING.md`](TESTING.md)).

**Stopping `make serve` invalidates a run in flight.** `run-learn.py`,
`run-persona.py`, and `run-tutor.py` preflight the router and abort on a streak
of connection failures instead of recording every attempt as a model failure. An
abort still costs the run, so let a `standard` pass finish before restarting.

Common commands:

```bash
make build && make serve                               # rebuild presets, start the router
curl -s localhost:8080/models | jq '.data[] | {id, status: .status.value}'
curl -s -X POST localhost:8080/models/load   -H 'Content-Type: application/json' -d '{"model":"gemma"}'
curl -s -X POST localhost:8080/models/unload -H 'Content-Type: application/json' -d '{"model":"gemma"}'
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
```

A running router does not pick up a rebuilt `models/models.ini` on its own, so
restart `make serve` after `make build`.

## Use In VSCode (Continue / Cline)

Both extensions talk to the router's OpenAI-compatible API at
`http://localhost:8080/v1`, with `make serve` running. The model names are the
preset names `gemma`, `qwen`, and `lite`. The server has no API key set, so any
non-empty key is accepted.

The prompt stack is no longer part of the model. A client that does not send
`models/<name>/prompt.txt` as its system message talks to the bare base model,
without the identity, honesty, and formatting rules the persona suite measures.

### Continue

Continue takes a per-model system message in `chatOptions.baseSystemMessage`.
This prints `~/.continue/config.yaml` model entries with each built `prompt.txt`
embedded, ready to paste under `models:`:

```bash
# Runs in: repo root, after make build. Prints YAML only, writes nothing.
python3 - <<'EOF'
import json
for name, label in [("qwen", "stack fidelity"), ("gemma", "coding/content/tutor"), ("lite", "fast")]:
    prompt = open(f"models/{name}/prompt.txt", encoding="utf-8").read()
    print(f"  - name: {name} ({label})\n"
          f"    provider: openai\n"
          f"    apiBase: http://localhost:8080/v1\n"
          f"    apiKey: local\n"
          f"    model: {name}\n"
          f"    roles: [chat, edit, apply]\n"
          f"    chatOptions:\n"
          f"      baseSystemMessage: {json.dumps(prompt, ensure_ascii=False)}")
EOF
```

The embedded prompt includes the `memory/` profile, so treat that config file as
personal. Re-run the script after `make build` when the stack changes. Pick the
default from the leaderboard below rather than from load size, because that guess
is what the benchmark exists to replace.

### Cline

In Cline's settings, set **API Provider** to `OpenAI Compatible`, **Base URL** to
`http://localhost:8080/v1`, **API Key** to any value, **Model ID** to `qwen`,
`gemma`, or `lite`, and the context window to `32768`. Cline sends its own system
prompt, and its docs describe no way to replace it, so Cline runs without this
repo's prompt stack. Cline ingests large prompts, so prompt-eval throughput
matters more here than generation speed. `run-speed.py` reports both, and the
**Prompt tok/s** column is the one to drive this choice.

Notes for both: the router keeps one model loaded, so switching models in the
editor costs a reload (about 2 to 7 seconds on this box). `gemma` and `qwen` do
not fit entirely in 10 GB of VRAM and keep some expert layers in system RAM. Both
are MoE, which is what keeps that usable. `lite` fits, and is the control for how
much the split actually costs. The eval runners turn thinking off per request.
Requests without that flag get the model's chat-template default, and `gemma`
was observed thinking by default.

## Evaluation

Runners live under `eval/` and write results to `eval/runs/<UTC>/`. Routine
testing goes through profiles (`smoke` after a rebuild, `standard` for the
under-1-hour comparison, `deep` for a several-hour confidence run — see
[`TESTING.md`](TESTING.md) for when to use each):

```bash
./eval/run-profile.py smoke --models gemma qwen lite
./eval/run-profile.py standard --models gemma qwen lite
```

Individual runners remain available for targeted sweeps:

```bash
./eval/run-speed.py --models gemma qwen lite
./eval/run-code.py --models gemma qwen lite
./eval/run-content.py --models gemma qwen lite
./eval/run-learn.py --models gemma qwen lite
./eval/run-tutor.py --models gemma qwen lite
./eval/run-json.py --models gemma qwen lite
./eval/run-persona.py --models gemma qwen lite
```

`run-persona.py` is the prompt-stack regression suite: it checks that the rules in
`prompts/`, `memory/`, and `knowledge/` are actually obeyed — the identity rule,
the `memory/user.md` honesty rules about Casey's skill buckets, `Unverified:`
marking, and output shape. Every other runner measures the base model *through*
the stack, so this is the only one that notices when a prompt edit breaks a rule.
Scoring is deterministic regex, no judge.

`run-json.py` is the structured-output test the consumer apps (Jobhunt,
SEO-LLM) depend on: it constrains decode with a JSON schema, buries facts in a
multi-thousand-token document, and scores schema conformance plus long-context
fact recall. Before the run it checks that every model serves at least
`--num-ctx` context (default 32768, what those apps were built around) and
aborts if one does not. llama.cpp fixes context at load time, and a prompt that
still overflows gets an HTTP 400 from the server rather than being truncated.

`run-code.py`, `run-learn.py`, and `run-tutor.py` execute model-generated Python.
That execution is confined by **bubblewrap**: read-only `/usr`, no network, no
host PID/IPC namespace, no access to `$HOME`, and write access only to a
throwaway CWD. Each run prints the active mode in its banner, and degrades
loudly — not silently — to a bare timeout-bounded subprocess on a box without a
working `bwrap`. Full runner flags and the sandbox details are in
[`TESTING.md`](TESTING.md).

The gateway and runner helpers have offline unit tests that need no server, GPU,
or model:

```bash
python3 -m unittest discover -s eval -p 'test_*.py'
```

After a run, promote the numbers into the leaderboard below instead of copying
them by hand:

```bash
./eval/promote.py             # newest run per suite -> the table below
./eval/promote.py --check     # exit 1 if the table is stale
```

## Benchmark Leaderboard

**These numbers were measured under Ollama (2026-07-28), not llama.cpp.** They
have not been re-run since the 2026-09-14 runtime switch, and `lite`'s base
weights changed in that switch, so treat them as the Ollama-era baseline until
the next `standard` pass replaces them. Single-request checks on llama.cpp are in
[`TESTING.md`](TESTING.md) under *Runtime switch*.

This block is generated. `./eval/promote.py`
rewrites everything between the markers from `eval/runs/`, so it cannot drift
away from the runs the way the hand-maintained 2026-06-14 tables did (those
survived two base swaps still reading as current; see [`TESTING.md`](TESTING.md)
for that history). Treat small gaps as directional: failures are strong signal,
close wins are weak signal, and speed breaks quality ties.

<!-- BENCH:START -->

_Generated by `./eval/promote.py` from 7 runs, 20260728T215614Z–20260728T233628Z (`eval/runs/`). Do not hand-edit: re-run the script._

| Suite | Winner | `lite` | `qwen` | `gemma` |
|---|---|---|---|---|
| Speed | `lite` | 89.4, 100% GPU | 40.9, 75%/25% CPU/GPU | 28.3, 66%/34% CPU/GPU |
| Coding | `gemma` | 23/27 | 23/27 | 26/27 |
| Content | `gemma` | 7/9 | 8/9 | 9/9 |
| Learning | tie | 7.0, 9/12 | 9.9, 12/12 | 9.8, 12/12 |
| Tutor (leak-gated) | `gemma` | 5.3, 6/15 | 5.6, 6/15 | 9.5, 0/15 |
| JSON / long-context | tie | 100%, 1.9 | 100%, 7.2 | 100%, 6.0 |
| Prompt stack | `qwen` | 57% | 90% | 43% |

Winner is `tie` where the runner flagged the margin as within its close-result threshold — those rows should break on speed, not the headline metric. Per-suite run directories:

- Speed: `eval/runs/20260728T215614Z/speed/summary.md` — 89 tok/s (100% GPU, thinking OFF)
- Coding: `eval/runs/20260728T215725Z/code/summary.md` — 26/27 (96%) @ 31 tok/s
- Content: `eval/runs/20260728T220323Z/content/summary.md` — 9/9 clean (100%) @ 30 tok/s
- Learning: `eval/runs/20260728T221541Z/learn/summary.md` — teach 9.9/10 (code 12/12, explanation 9.9/10)
- Tutor (leak-gated): `eval/runs/20260728T223432Z/tutor/summary.md` — teach 9.5/10 (leaks 0/15, explanation 9.5/10, non-leak explanation 9.5/10)
- JSON / long-context: `eval/runs/20260728T221011Z/json/summary.md` — score 100% (schema 100%, facts 100%)
- Prompt stack: `eval/runs/20260728T233628Z/persona/summary.md` — 19/21 clean (90%)

<!-- BENCH:END -->

### Current picks

From the Ollama-era 2026-07-28 run. Revisit them after the llama.cpp re-baseline.

| Use | Pick | Basis |
|---|---|---|
| Fast local general use | `lite` | 89 tok/s at **100% GPU** — 2× `qwen`, 3× `gemma`, and the only model with no CPU spill. |
| Agentic tools (Cline) | `lite` | Prompt ingest is what matters when the tool ships large contexts: 9562 tok/s vs `gemma` 6465 and `qwen` 1563. |
| Coding | `gemma` | 26/27 vs 23/27 for both others. |
| Content / SEO / copy | `gemma` | 9/9 clean vs `qwen` 8/9, `lite` 7/9. |
| Socratic tutoring (no spoilers) | `gemma` | **0/15 leaks** vs 6/15 for both others — the one decisive gap in the whole run. Teach 9.5/10 vs 5.6 and 5.3. |
| Learning explanations | `qwen` / `gemma` (tie) | 9.9 vs 9.8, inside the tie threshold; both 12/12 on the code gate. `lite` trails on the gate (9/12), not on explanation quality. |
| Structured JSON / app smoke tests | `lite` | All three scored 100%; `lite` is ~3× faster end-to-end (1.9s vs 6.0/7.2s). |
| Prompt-stack fidelity | `qwen` | 90–95% clean vs `gemma` 43–57%. If you want the stack's rules actually obeyed, this is the model that obeys them. |

No single model wins. `gemma` takes the quality suites, `lite` takes everything
speed-shaped and is genuinely competitive on quality, `qwen` is the only one that
reliably follows the prompt stack. `lite` earning three rows is the result worth
noting — a 9B dense model that fits in VRAM was not expected to be the pick for
anything.

Two caveats before leaning hard on the small gaps. Samples are small (n = 9–27
per model), and `--seed` does not currently survive a process restart on this box
(see [`TESTING.md`](TESTING.md)), so run-to-run drift is real: `lite`'s
prompt-stack score read 62% and 57% on two runs of the same suite against builds
that differ only by a rule measured to change nothing. Failures and the leak-rate
gap are strong signal; a few points either way is not.

## Prompt Stack Value

What each rule actually buys, measured 2026-07-28: `run-persona.py` stacked vs
`--system-mode baseline`, 3 attempts × 3 models, so each rule scores out of 9.

| Rule | Stacked | Baseline | Buys | Read |
|---|---:|---:|---:|---|
| `identity` | 9/9 | 0/9 | **+9** | Load-bearing. Without it every base volunteers a developer and training origin. |
| `model_origin` | 7/9 | 0/9 | **+7** | Load-bearing, same reason. |
| `familiar_skill` | 5/9 | 1/9 | **+4** | Load-bearing but leaky — no model holds it 3/3. The honesty rule most worth rewriting. |
| `unknown_fact` | 6/9 | 3/9 | **+3** | Load-bearing on `qwen`/`lite`. `gemma`'s clean baseline is an artifact: with no profile it has never heard of Casey, so it declines for the wrong reason. |
| `unverified` | 3/9 | 0/9 | **+3** | Works on `qwen` only; 0/3 on `gemma` and `lite` **stacked as well as unstacked**. See the negative result in [`TESTING.md`](TESTING.md) — rewriting it did not fix this. |
| `fields_echo` | 3/9 | 0/9 | **+3** | Same shape: `qwen` 3/3, the other two 0/3 stacked. |
| `bash_block` | 9/9 | 9/9 | **0** | Free. **Rule deleted 2026-07-28** — all three bases already fence commands in `bash` with no `$` prefix unprompted. |

Only `bash_block` was cut: it is the one rule confirmed at zero contribution
twice, on two different lineups. The deletion was verified with `make check` —
`bash_block` still scores 3/3 on all three models with the rule gone.

The honest caveat on the rest: `unverified` and `fields_echo` are not earning
their tokens on two of three models, but they are not *free* either — deleting
them would cost `qwen` a rule it does obey. They are candidates for a rewrite,
not a cut, and a rewrite has to be measured against a task that does not share
its subject with any example in the stack (that mistake is documented in
[`TESTING.md`](TESTING.md)).

## Models Tested

Full roster (current and retired). See [`TESTING.md`](TESTING.md) for the reasoning:

| Model | Base | Status |
|---|---|---|
| `gemma` | `gemma4-26b-a4b-it-qat.gguf` (Ollama `gemma4:26b-a4b-it-qat`, same bytes) | current. Rebuilt 2026-07-28, moved to llama.cpp 2026-09-14 |
| `qwen` | `qwen3.6-35b-a3b-mtp-q4_K_M.gguf` (Ollama `qwen3.6:35b-a3b-mtp-q4_K_M`, same bytes) | current. Rebuilt 2026-07-28, moved to llama.cpp 2026-09-14 with MTP on |
| `lite` | `qwen3.5-9b-mtp-q4_K_M.gguf` (unsloth `Qwen3.5-9B-MTP-GGUF` Q4_K_M) | current. Added 2026-07-28 as the in-VRAM speed anchor and 3rd judge, base replaced 2026-09-14 |
| `lite` (Ollama) | `qwen3.5:9b` | replaced 2026-09-14. The Ollama file does not load in llama.cpp (`rope.dimension_sections` has 3 entries, llama.cpp expects 4). |
| `qwen` (uncensored) | `hf.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive:Q4_K_M` | reverted 2026-07-28 — never benchmarked; works against `prompts/safety.md` by construction, so the shared stack spent tokens every turn fighting the base's own tuning. An uncensored base needs its own tag and its own stack, not a swap under the shared one. |
| `gemma` (prior) | `gemma4:12b-it-q4_K_M` | retired 2026-07-27 — base no longer installed; source of the 2026-06-14 scores |
| `gemma-custom` | `gemma4:e4b` | removed — superseded by gemma4 12B |
| `granite-custom` | `granite4.1:8b-Q5_K_M` | dropped — strong prior coding, no longer leads |
| `qwen-custom` | `qwen3.5:9b` | superseded 2026-06 by Qwen3.6 MoE; the base returned 2026-07-28 as `lite` |
| `ministral-custom` | `ministral-3:8b` | removed — historical #2 |
| `llama-custom` | `llama3.1:8b` | removed — trailed in early runs |
| `gemma-big` | `gemma3:27b` | retired — lost the quality/speed tradeoff on this box |

## Hardware Envelope

Benchmarks are for this box: RTX 3080 10 GB, Ryzen 5900x, 32 GB DDR4-3600.
Models that fit 100% on GPU run fast. Dense spillover is usually too slow; MoE
spillover can remain usable because fewer parameters are active per token.
Desktop apps hold about 1 GB of the card at idle, so the usable budget for a
model plus its KV cache is closer to 8.6 GB. Runtime: llama.cpp build 10968
(`41abbfd59`) with CUDA 13.4.

## Docs

- [`TESTING.md`](TESTING.md): testing source of truth, runner docs, safety notes,
  benchmark history, and detailed results.
