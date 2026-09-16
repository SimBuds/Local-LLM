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
| `gemma` | `gemma4-26b-a4b-it-qat.gguf` | 32K | 21 MoE layers on CPU | 26B A4B MoE, QAT Q4_0. |
| `qwen` | `qwen3.6-35b-a3b-mtp-q4_K_M.gguf` | 32K | 34 MoE layers on CPU, MTP | 35B A3B MoE. Largest model that stays usable here. |
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

Each line must print `OK`. `./add-model Org/Repo-GGUF` prints a correctly formed
download command for any HuggingFace repo (see *Adding A Model*). `lite` can be
downloaded again with
`curl -L --fail -C - -o ~/models/gguf/qwen3.5-9b-mtp-q4_K_M.gguf https://huggingface.co/unsloth/Qwen3.5-9B-MTP-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf`.
The `gemma` and `qwen` copies have no public byte-identical source, because
Ollama and its store were removed from this box on 2026-09-15. The closest downloads are `google/gemma-4-26B-A4B-it-qat-q4_0-gguf`
(`gemma-4-26B_q4_0-it.gguf`) and `unsloth/Qwen3.6-35B-A3B-MTP-GGUF`
(`Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`). Neither has been tested on this box, both are
different files, and switching to one starts a new baseline for that model.

`~/models/gguf` also holds four models copied out of Ollama before it was removed,
checked against their blob digests: `granite4.2-8b-q4_K_M.gguf`, `ornith.gguf`,
`qwen3.8-27b-mtp-q4_K_M.gguf` and `gemma4-12b-it-qat.gguf`, the last two with a
`.mmproj.gguf` vision projector. Ollama's params, licenses, and ornith's built-in
system prompt are in `ollama-extras/`. None is in the lineup or has been loaded in
llama.cpp.

## Structure

```text
.
├── prompts/              # behavior controls, run every turn
├── memory/user.md        # durable user profile (gitignored, see *.example.md)
├── knowledge/**/*.md     # reusable reference context
├── eval/                 # benchmark runners, tasks, and offline unit tests
├── scripts/llm           # terminal client for the router, installed to ~/.local/bin
├── systemd/              # the llama-server user service unit
├── models/<name>/        # generated system.txt, prompt.txt, preset.ini (gitignored)
├── models/models.ini     # generated router preset: server.ini + every preset.ini
├── server.ini            # llama-server settings shared by every model
├── build-common.sh       # shared assembly, and the shared PARAMS baseline
├── build-{gemma,qwen,lite}   # one per model, discovered by make
├── add-model             # scaffolds a new build-* from a staged or remote GGUF
├── apply-universal.py    # ports the portable half of AGENTS.md between repos
├── Makefile              # see the target table below
├── AGENTS.md             # working rules for AI agents in this repo
└── TESTING.md            # testing source of truth and benchmark history
```

Prompt assembly order is `knowledge/`, then `memory/`, then `prompts/`; files
within each directory are sorted. That keeps reference context first and behavior
rules last. Each Markdown file is wrapped in `--- START/END FILE ---`. Files over
100k are skipped, as are `*.example.md` templates — injecting a template beside
the real file would hand the model two conflicting profiles. The markers stay in
`system.txt` for debugging and are stripped from `prompt.txt`, because models
recite them.

## Adding A Model

`./add-model` writes the builder for a GGUF you have already staged in
`$GGUF_DIR`. It does not download anything and it does not decide the GPU/CPU
split.

```bash
# Runs in: local terminal, repo root. Writes one build-* file, nothing else.
./add-model                            # pick from staged GGUFs with no builder
./add-model qwen3.8-27b-mtp-q4_K_M.gguf   # or name the file and skip the menu
./add-model Org/Repo-GGUF              # or look a HuggingFace repo up first
```

Given a HuggingFace repo, as `Org/Repo` or any URL for it, it lists that repo's
GGUFs with their sizes, groups a sharded set into one entry, flags any `mmproj`
vision projector instead of offering it as a model, and prints the `curl` for the
quant you pick. It prints rather than downloads: `$GGUF_DIR` is outside the repo,
so the transfer is yours to run and to interrupt. Re-run `add-model` once the
file is staged and it carries on into the scaffold. A gated repo, a missing one,
and one with no GGUF each stop with the reason rather than a menu.

It asks for the router name, refusing one that is malformed, already taken, or
part of the `gemma` / `qwen` / `lite` contract other repos depend on. It reads
the GGUF header to decide whether the model carries MTP heads, and emits
`spec-type = draft-mtp` only when it does. It writes no `PARAMS`, because the
sampler baseline is shared (see *Build And Tune*).

Then it derives the GPU/CPU split, by loading the model once with
`--fit-target 2048` on a spare port and reading what llama.cpp's fitter chose.
That number goes into `LOAD` as `n-cpu-moe`, with the fit line and the date
recorded beside it. **Close anything holding VRAM first.** The split is sized
from what is free at that moment, which is exactly why it is then pinned. A model
that fits entirely gets no `n-cpu-moe` and says so. `--no-probe` skips the probe
and leaves the split for you to fill in.

The lineup is discovered from the `build-*` files, so nothing else needs editing:
`make build` picks the new model up.

## Build And Tune

The only model-specific part of a builder is the top config block:

```bash
MODEL_NAME="qwen"
BASE_MODEL="qwen3.6-35b-a3b-mtp-q4_K_M.gguf"   # filename under $GGUF_DIR
LOAD=(
  'n-cpu-moe = 34'           # expert layers kept in system RAM, pinned (see below)
  'spec-type = draft-mtp'    # MTP speculative decoding, only for GGUFs that carry MTP heads
)
```

Keys are llama-server flag names without the leading dashes. For a new model,
copy an existing `build-*` script and edit only that config block. Builders
source the shared `build-common.sh`, which aborts up front if the GGUF is not in
`GGUF_DIR`, so a stale or retargeted base fails loudly instead of leaving a
half-written preset behind.

**The lineup is whatever `build-*` scripts exist.** `make` discovers them, so a
new builder needs no edit anywhere else, and deleting one drops its section from
`models/models.ini` on the next `make build`. Section order follows the sorted
filenames. The router sorts `/models` itself, so file order carries no meaning.

**The sampler baseline is shared, and lives in `build-common.sh`.** Only
`run-json.py` sends sampler options. Every other suite inherits whatever the
preset sets, so a value that differs between builders makes the leaderboard
measure model × sampler instead of model. That mistake invalidated the
2026-06-14 coding, learning, and tutor tables, which compared `gemma` at
`temperature 0.75` / `presence_penalty 0.2` against `qwen` at `0.2` / `0.0`.

```bash
# build-common.sh, used by every builder that does not define its own PARAMS
PARAMS=( # Context: 262144 - 131072 - 65536 - 32768 - 16384 - 8192 - 4096
  'ctx-size = 32768'         # 32k: sweet spot for multi-file local tasks
  'temp = 0.2'               # Low temperature forces strict compliance with code syntax and tool tags
  'top-p = 0.95'
  'top-k = 40'
  'min-p = 0.05'             # Safeguards structural format without restricting code vocabulary
  'presence-penalty = 0.0'   # MUST BE ZERO. Coding requires reusing exact variable names.
  'repeat-penalty = 1.05'    # Prevents infinite code loops without breaking boilerplate code
)
```

Until 2026-09-15 this array was copy-pasted into all three builders and keeping
them in sync was manual discipline. A builder that defines its own `PARAMS`
still overrides the default, which is how a model that needs different decoding
for daily use gets a separate preset rather than skewing the shared baseline.
Keep such a preset out of head-to-head benchmark runs.

**`LOAD` is per model and pinned.** It holds how the model is split between GPU
and CPU, which has to differ by model size. `server.ini` sets `fit = off`, so the
split is exactly what the builder declares rather than whatever llama.cpp's
automatic fitting picks from the VRAM free at load time (an open browser would
otherwise change a benchmark's offload between runs).

**The values leave about 2 GB of VRAM free, because 1 GB was not enough.** The
first pinned values (gemma 18, qwen 32) came from llama.cpp's default 1 GB fit
margin and crashed with CUDA out of memory once other GPU use passed somewhere
between 2.1 and 2.5 GB for `gemma` and between 2.5 and 3.0 GB for `qwen`. Desktop apps alone use 1.2 to 1.5 GB
on this box. The current values (gemma 21, qwen 34) were fit with a 2 GB margin
and then checked with 5 load-and-request cycles each while 3.0 GB of the card was
held by other processes: all passed. The cost against the old values was about
13% generation speed for `gemma` and 9% for `qwen` (details in
[`TESTING.md`](TESTING.md) under *Offload headroom*).

To re-derive a value after a model or hardware change, use `./add-model` on a
fresh builder, which runs this for you. The manual recipe below is the fallback,
and it is what `add-model` automates: load the GGUF with a 2 GB fit margin and
the same `spec-type` its builder uses, and read the fit line. Leaving MTP off
gives a wrong answer, because the MTP layer needs VRAM of its own. The log goes
to a file because llama-server buffers it: stopping a piped probe as soon as it
answered lost the fit line twice in testing.

Note the loop below only terminates for a model that spills. A model that fits
entirely never prints an overflow line (`lite` loads with
`offloaded 34/34 layers to GPU` and no fit line, measured 2026-09-15), so the
loop waits forever. `add-model` waits for the fit line or for the server to
report `model loaded`, whichever comes first.

```bash
# Runs in: local terminal, with nothing else holding the GPU. Safe to re-run.
LOG="$(mktemp)"
llama-server -m ~/models/gguf/qwen3.6-35b-a3b-mtp-q4_K_M.gguf -c 32768 -np 1 -fa on \
  -ctk q4_0 -ctv q4_0 --no-mmproj --spec-type draft-mtp --fit-target 2048 \
  --port 8081 -lv 4 > "$LOG" 2>&1 &
PID=$!
echo "probe pid: $PID, log: $LOG"
until grep -q 'layers (.* overflowing)' "$LOG" || ! kill -0 "$PID"; do sleep 1; done
grep 'layers (.* overflowing)' "$LOG" || echo "no fit line: read $LOG"
kill "$PID"
```

The `echo` must print a numeric pid. A line ending
`42 layers (34 overflowing), 6071 MiB used, 2157 MiB free` means
`n-cpu-moe = 34`. Drop `--spec-type draft-mtp` for `gemma`, which has no MTP
layer. A model that fits entirely prints no overflow and needs no `n-cpu-moe`.
The count moves with the VRAM free at that moment, which is why it is pinned
rather than re-fitted per run, so close apps that hold VRAM before deriving one.

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

Every target:

| Target | What it does | When |
|---|---|---|
| `make build` | Rebuild the models whose prompt stack changed, then join `models/models.ini` | After editing a builder or the prompt stack |
| `make check` | `build`, then the persona suite over every model | Before committing a prompt change |
| `make persona` | The persona suite without rebuilding | Checking the stack without touching files |
| `make deploy` | Copy `models/models.ini` to `~/.config/llama.cpp/` | Shipping a preset change to the service |
| `make serve` | Run a router in the foreground over the repo's own preset | Trying an undeployed change, with `PORT=8081` |
| `make hook` | Install the pre-commit hook that runs `make check` | Once per clone |
| `make clean` | Drop the build stamps | Forcing a full rebuild |

`make deploy` writes outside the repo and the service needs restarting
afterwards, so both are yours to run (see *Serving other apps*).

Editing a single `build-*` script rebuilds only that model. Editing anything
under `prompts/`, `memory/`, or `knowledge/` rebuilds every model, because they
all assemble the same stack. The lineup itself is discovered from the `build-*`
files, so `MODELS` is never edited by hand.

## llama-server

The runtime is llama.cpp's `llama-server` in router mode: one process on port
8080 that starts a child server per model on demand and routes each request by
its `model` field. Day to day it runs as the `llama-server` user service, reading
the preset this repo deploys to `~/.config/llama.cpp/models.ini` (see *Serving
other apps*). `make serve` runs a router in the foreground over the repo's own
`models/models.ini`, for trying changes before deploying them.

Benchmarks were run on llama.cpp build 10968 (commit `41abbfd59`), built from
source because the AUR `llama.cpp-cuda` package was reported stale. The recipe
used to pin `g++-15` as the CUDA host compiler. That is corrected as of
2026-09-15: this box has no `/usr/bin/g++-15`, so the block as written failed at
the configure step, and `llama-server --version` reports the installed binary was
in fact built with GNU 16.2.1. Nothing needs pinning. Verified by configuring in
a scratch directory: cmake reports `CUDA host compiler is GNU 16.2.1` against
CUDA 13.4.59.

```bash
# Runs in: local terminal, as your user (no sudo). Safe to re-run.
SRC="$HOME/src/llama.cpp"
[ -d "$SRC/.git" ] || git clone https://github.com/ggml-org/llama.cpp "$SRC"
cmake -S "$SRC" -B "$SRC/build" -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86 \
  -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_TYPE=Release \
  && cmake --build "$SRC/build" -j 20 --target llama-server \
  && ln -sfn "$SRC/build/bin/llama-server" "$HOME/.local/bin/llama-server" \
  && llama-server --version
```

`-DCMAKE_CUDA_ARCHITECTURES=86` is the RTX 3080. Change it for another card.

`server.ini` holds the settings every model shares, and replaces the old Ollama
systemd override:

| Was (Ollama) | Now (llama-server) | Where |
|---|---|---|
| `OLLAMA_NUM_PARALLEL=1` | `parallel = 1` (the server default is 4 slots) | `server.ini` |
| `OLLAMA_FLASH_ATTENTION=1` | `flash-attn = on` | `server.ini` |
| `OLLAMA_KV_CACHE_TYPE=q4_0` | `cache-type-k = q4_0`, `cache-type-v = q4_0` | `server.ini` |
| `OLLAMA_MAX_LOADED_MODELS=1` | `--models-max 1` | service unit, `make serve` |
| `OLLAMA_KEEP_ALIVE=10m` | `sleep-idle-seconds = 600`: an idle model sleeps and frees its VRAM, and the next request wakes it | `server.ini` |
| automatic GPU/CPU split | `fit = off` plus each builder's `LOAD` | `server.ini`, `build-*` |

`--models-max 1` matters for the benchmarks, not just for daily use. With two
resident models they compete for the same 10 GB, so a model's measured
throughput depends on which other model happens to be loaded beside it, and the
leaderboard would be measuring co-residency rather than the model. With one, each
model gets the whole card in turn. `run-learn.py` and `run-tutor.py` are built for
this: they generate every response first, then loop by judge, so each model loads
once per phase instead of thrashing on every call.

The eval runners reach the router through `eval/_gateway.py`, which reads
`LLM_URL` (default `http://localhost:8080`). It sends `prompt.txt` as the system
message and turns prompt caching off on every call, because the llama-server docs
warn that cached prefixes make results not bit-identical and reproducibility is
already the weak spot here (see [`TESTING.md`](TESTING.md)).

**Stopping or restarting the router (the service or `make serve`) invalidates a
run in flight.** `run-learn.py`,
`run-persona.py`, and `run-tutor.py` preflight the router and abort on a streak
of connection failures instead of recording every attempt as a model failure. An
abort still costs the run, so let a `standard` pass finish before restarting.

Common commands:

```bash
make build && make deploy && systemctl --user restart llama-server   # ship a preset change
make serve PORT=8081                                   # try an undeployed change beside the service
curl -s localhost:8080/models | jq '.data[] | {id, status: .status.value}'
curl -s -X POST localhost:8080/models/load   -H 'Content-Type: application/json' -d '{"model":"gemma"}'
curl -s -X POST localhost:8080/models/unload -H 'Content-Type: application/json' -d '{"model":"gemma"}'
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
```

A running router does not pick up a changed preset on its own: restart the service
after `make deploy`, or restart `make serve` after `make build`.

## Serving other apps

The router is a machine-level service, not part of this repo at runtime. Jobhunt,
SEO-LLM, this repo's evals, and editors are all just clients of
`http://localhost:8080`. The pieces live where a standard llama.cpp setup puts
them:

| Piece | Location |
|---|---|
| Binary | `~/.local/bin/llama-server` (built from `~/src/llama.cpp`) |
| Models | `~/models/gguf/` |
| Server config | `~/.config/llama.cpp/models.ini`, written by `make deploy` |
| Service | `~/.config/systemd/user/llama-server.service`, copied from `systemd/llama-server.service` |

This repo stays the tracked source of the config: builders generate
`models/models.ini`, and `make deploy` copies it into place, so a half-finished
edit here never reaches the apps until it is deployed. The service runs as you, so
none of this needs sudo, and it starts at login.

Install once:

```bash
# Runs in: local terminal, as your user. Safe to re-run.
cd ~/Apps/Local-LLM && make deploy
mkdir -p ~/.config/systemd/user
cp ~/Apps/Local-LLM/systemd/llama-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now llama-server
systemctl --user is-active llama-server
curl -s localhost:8080/health
```

`is-active` must print `active` and the last command `{"status":"ok"}`. If not,
read `journalctl --user -u llama-server -n 50`. Both files are copies, so after
changing the tracked unit, copy it again and run `systemctl --user daemon-reload`.

Day to day:

| Task | Command |
|---|---|
| Status / logs | `systemctl --user status llama-server`, `journalctl --user -u llama-server -f` |
| Ship a preset change | `make build && make deploy && systemctl --user restart llama-server` |
| Try a change without touching the service | `make serve PORT=8081`, and `LLM_URL=http://localhost:8081` for the eval runners |
| Free the GPU now | `systemctl --user stop llama-server` (idle models also sleep on their own after 10 minutes) |
| Plain `make serve` on 8080 | Stop the service first. Two routers cannot share a port: the second exits with `couldn't bind HTTP server socket`. |

### Terminal helper: `llm`

`scripts/llm` gives Ollama-style commands for the router. It is standalone
Python with no repo imports, so the installed copy keeps working without this
repo. Install or update it with:

```bash
# Runs in: local terminal, as your user. Safe to re-run.
install -m 755 ~/Apps/Local-LLM/scripts/llm ~/.local/bin/llm
llm status
```

| Ollama | `llm` | What it does |
|---|---|---|
| `ollama ps`, `ollama list` | `llm status` | Router up or down, each model's state (`loaded`, `sleeping`, `unloaded`), VRAM held by llama-server |
| (preload) | `llm load MODEL` | Loads and waits until ready. Reports `already loaded` or `sleeping` instead of reloading. |
| `ollama stop MODEL` | `llm unload [MODEL]` | Unloads one model, or every resident one, and waits until the router confirms it stopped |
| `ollama run MODEL "..."` | `llm chat MODEL "..."` | One request, answer streamed as it is written. Thinking off unless `--think`, which shows the reasoning on stderr, so `> file` keeps only the answer. `--verbose` adds prompt and answer token counts and speed on stderr. `--system-file PATH` sends a system message (for example `models/qwen/prompt.txt`). Reads the prompt from stdin when none is given. An unknown model lists the available names. |
| `journalctl -u ollama -f` | `llm logs` | Router log, last 50 lines then follow. Hides the one-argument-per-line dump each model load writes. `llm logs --all` shows every line. Extra arguments go straight to journalctl, e.g. `llm logs -n 20 --no-pager`. |

`LLM_URL` points it at another router, such as a test one on port 8081. Unlike
`ollama run`, `llm chat` sends no system prompt unless you pass one, so the reply
comes from the bare model.

**Contract for apps.** These are identifiers other repos depend on, so changing
any of them is a coordinated change across repos, not a local edit:

- Endpoint `POST http://localhost:8080/v1/chat/completions` (OpenAI format). Any
  non-empty API key is accepted.
- `model` is one of `gemma`, `qwen`, `lite`.
- The app sends its own system message. Nothing is baked into the model, so a
  request without one gets the bare base model.
- Context is fixed at 32768 tokens by the preset. There is no per-request
  context size, and a longer prompt returns HTTP 400 `exceed_context_size_error`
  instead of being truncated.
- Structured output: `response_format: {"type": "json_schema", "json_schema":
  {"name": "...", "schema": {...}}}`. The top-level `schema` shape shown in the
  llama-server README was silently ignored on build 10968.
- Thinking off: `chat_template_kwargs: {"enable_thinking": false}`. Thoughts, when
  on, come back in `reasoning_content`, separate from the answer.
- One model is loaded at a time. An app asking for a different model unloads the
  current one, so two apps using different models take turns paying the load.
- After 10 idle minutes a loaded model goes to sleep and frees its VRAM. The next
  request wakes it without any change on the client side: `lite` answered in
  about 1 to 2 seconds in testing (gemma and qwen wake times were not measured).
  While it sleeps, `GET /models` reports `sleeping`.

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

**Measured on llama.cpp, 2026-09-15** (`standard` pass, pinned offload gemma 21
and qwen 34, prompt caching off). Read close rows as ties: the persona suite's
run-to-run spread at low attempt counts is measured in
[`TESTING.md`](TESTING.md) under *Interpreting Results*, and it is wider than
the gap between neighbouring models here. The router log showed no out-of-memory errors
or crashed model processes during the pass. Timing columns (Avg s, prompt tok/s)
include re-processing the full prompt on every call, so they run slower than the
Ollama-era figures even where generation is faster. The Ollama-era numbers and
the comparison against them are in [`TESTING.md`](TESTING.md) under Historical
Notes. `lite`'s weights changed in the switch, so its rows start a new history.

This block is generated. `./eval/promote.py`
rewrites everything between the markers from `eval/runs/`, so it cannot drift
away from the runs the way the hand-maintained 2026-06-14 tables did (those
survived two base swaps still reading as current; see [`TESTING.md`](TESTING.md)
for that history). Treat small gaps as directional: failures are strong signal,
close wins are weak signal, and speed breaks quality ties.

<!-- BENCH:START -->

_Generated by `./eval/promote.py` from 7 runs, 20260915T012103Z–20260915T021916Z (`eval/runs/`). Do not hand-edit: re-run the script._

| Suite | Winner | `lite` | `qwen` | `gemma` |
|---|---|---|---|---|
| Speed | `lite` | 122.4, all GPU, MTP | 56.4, GPU + 34 MoE layers on CPU, MTP | 41.6, GPU + 21 MoE layers on CPU |
| Coding | `gemma` | 22/27 | 24/27 | 27/27 |
| Content | `gemma` | 8/9 | 5/9 | 9/9 |
| Learning | `qwen` | 8.2, 10/12 | 9.9, 12/12 | 9.1, 11/12 |
| Tutor (leak-gated) | `gemma` | 5.0, 7/15 | 6.1, 5/15 | 9.4, 0/15 |
| JSON / long-context | tie | 100%, 2.5 | 100%, 15.4 | 100%, 9.8 |
| Prompt stack | `qwen` | 57% | 95% | 57% |

Winner is `tie` where the runner flagged the margin as within its close-result threshold — those rows should break on speed, not the headline metric. Per-suite run directories:

- Speed: `eval/runs/20260915T012103Z/speed/summary.md` — 122 tok/s (all GPU, MTP, thinking OFF)
- Coding: `eval/runs/20260915T012204Z/code/summary.md` — 27/27 (100%) @ 46 tok/s
- Content: `eval/runs/20260915T013141Z/content/summary.md` — 9/9 clean (100%) @ 47 tok/s
- Learning: `eval/runs/20260915T015142Z/learn/summary.md` — teach 9.9/10 (code 12/12, explanation 9.9/10)
- Tutor (leak-gated): `eval/runs/20260915T021916Z/tutor/summary.md` — teach 9.4/10 (leaks 0/15, explanation 9.4/10, non-leak explanation 9.4/10)
- JSON / long-context: `eval/runs/20260915T014158Z/json/summary.md` — score 100% (schema 100%, facts 100%)
- Prompt stack: `eval/runs/20260915T013603Z/persona/summary.md` — 20/21 clean (95%)

<!-- BENCH:END -->

### Current picks

From the llama.cpp `standard` pass of 2026-09-15.

| Use | Pick | Basis |
|---|---|---|
| Fast local general use | `lite` | 122 tok/s, the only model fully on GPU. 2.2× `qwen` (56), 2.9× `gemma` (42). |
| Agentic tools (Cline) | `lite` | Prompt ingest matters most when a tool ships large contexts: 2796 tok/s vs `gemma` 701 and `qwen` 430, each re-reading a ~3k-token system prompt. |
| Coding | `gemma` | 27/27 vs `qwen` 24/27 and `lite` 22/27. Both others failed every `decode_string` attempt. |
| Content / SEO / copy | `gemma` | 9/9 clean vs `lite` 8/9 and `qwen` 5/9. All four `qwen` misses ran over a word limit by 6 to 11%. |
| Socratic tutoring (no spoilers) | `gemma` | **0/15 leaks** vs `qwen` 5/15 and `lite` 7/15, the one decisive gap again. Teach 9.4/10 vs 6.1 and 5.0. |
| Learning explanations | `qwen` | 9.9/10 with 12/12 on the code gate. `gemma` scored 9.1 because one of its 12 solutions failed. Its explanations score 9.9 when the code runs. |
| Structured JSON / app smoke tests | `lite` | All three scored 100%. `lite` averages 2.5s against `gemma` 9.8s and `qwen` 15.4s. |
| Prompt-stack fidelity | `qwen` | 95% clean vs 57% for both others. If the stack's rules have to be obeyed, this is the model that obeys them. |

No single model wins, and the split is the same as under Ollama. `gemma` takes
the quality suites, `lite` takes everything speed-shaped, and `qwen` is the only
one that reliably follows the prompt stack.

Two caveats before leaning hard on the small gaps. Samples are small (n = 9 to 27
per model), so a few points either way is noise, while failures and the leak-rate
gap are strong signal. Seeded runs now mostly reproduce across router restarts
(`lite` 10/10, `gemma` 15/15, `qwen` 14/15 in a temperature 1.5 check, see
[`TESTING.md`](TESTING.md)), which is a large improvement over Ollama but not a
guarantee, so compare prompt or scorer changes by re-scoring saved responses.

## Prompt Stack Value

What each rule actually buys, measured 2026-07-28: `run-persona.py` stacked vs
`--system-mode baseline`, 3 attempts × 3 models, so each rule scores out of 9.
Nine attempts is a small sample, and the same suite's measured run-to-run spread
(see *Interpreting Results* in [`TESTING.md`](TESTING.md)) covers several of the
smaller gaps below. Treat a large delta as signal and a one or two point delta
as unresolved.

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

- [`PLAN.md`](PLAN.md): the blueprint. What this repo is and is not, the
  architecture decisions and the reasoning behind them, the locked decisions with
  their dates, and the open questions. Read it before changing anything
  structural.
- [`TESTING.md`](TESTING.md): testing source of truth, runner docs, safety notes,
  benchmark history, and detailed results.
- [`AGENTS.md`](AGENTS.md): the working rules an AI coding agent follows in this
  repo. Everything above its `## Project-specific rules` section is portable
  across repos, and the section at the end is this project's own.
  `apply-universal.py` copies that portable half into another repo's `AGENTS.md`
  while keeping that repo's project rules byte for byte:

  ```bash
  # Runs in: local terminal. Names every file it touches, writes nothing else.
  ./apply-universal.py AGENTS.md ../other-repo/AGENTS.md
  ```

  It is safe to re-run, and it never commits.
