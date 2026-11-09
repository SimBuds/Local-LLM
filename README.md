# AI Context Stack

Layered Markdown prompts served with local GGUF models through a llama.cpp
`llama-server` router, plus an eval suite that picks the best model for each job.
There is no fine-tuning. Behavior comes from `prompts/`, durable memory, and each
model builder's context and offload settings.

- **Purpose:** run a small set of local models on one workstation, wire them into
  editor assistants (Continue and Cline), and keep a measured record of which
  model wins which task.
- **Testing:** every model is benchmarked on speed, coding, content and SEO, tool
  calling, learning, and leak-gated tutoring.

This README covers what the project is and how to run it. Runner docs, safety
notes, history and detailed results are in [`TESTING.md`](TESTING.md).

## Models

The lineup, on llama.cpp since 2026-09-14:

| Model | GGUF (`~/models/gguf/`) | ctx | Offload | Role |
|---|---|--:|---|---|
| `gemma` | `gemma4-26b-a4b-it-qat.gguf` | 128K | 24 MoE layers on CPU | 26B A4B MoE, QAT Q4_0. |
| `qwen` | `qwen3.6-35b-a3b-mtp-q4_K_M.gguf` | 128K | 37 MoE layers on CPU, MTP | 35B A3B MoE. The largest model that stays usable here. |
| `lite` | `qwen3.5-9b-mtp-q4_K_M.gguf` | 128K | all GPU, MTP | Dense 9B. The only one that fits in 10 GB, so it is the speed anchor. |
| `freedom` | `Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf` | 128K | 34 MoE layers on CPU | Uncensored tune of `qwen`'s base, for Friends-Hermes. No MTP heads. |

- **MoE spillover stays usable.** `gemma`, `qwen` and `freedom` activate few
  parameters per token, so the expert layers kept in system RAM cost little.
  `lite` needs no spillover, which makes it the baseline the others are measured
  against.
- **MTP** (multi-token prediction) is speculative decoding built into the Qwen
  GGUFs.
- **Weights history.** `gemma` and `qwen` are the files the Ollama-era benchmarks
  used, copied byte for byte. Ollama's `qwen3.5:9b` does not load in llama.cpp,
  so `lite` is unsloth's MTP build at the same Q4_K_M quant, and its scores start
  a new history.
- **`freedom`** is held to `qwen`'s standards (spike-tested split, benchmark
  suites, `make check`) and is the model Friends-Hermes calls. Why an uncensored
  base sits beside a shared `safety.md` is in PLAN.md, *The lineup*.
- **`lite` is also a judge.** The learn and tutor suites grade with a
  leave-one-out judge panel. With two models each response had one judge and no
  disagreement could be measured. Three or more models give at least two judges
  per response.

## Quickstart

```bash
cp memory/user.example.md memory/user.md                          # then edit
cp memory/learning-profile.example.md memory/learning-profile.md  # then edit
# stage the lineup's GGUFs in ~/models/gguf first (see Model Files below)
make build        # writes models/models.ini and each model's prompt.txt
make serve        # llama-server router on http://localhost:8080, leave it running
```

Then send one request from a second terminal, with the prompt stack as the system
message:

```bash
jq -n --rawfile sys models/qwen/prompt.txt \
  '{model: "qwen", max_tokens: 80, chat_template_kwargs: {enable_thinking: false},
    messages: [{role: "system", content: $sys}, {role: "user", content: "Who are you, in one sentence?"}]}' \
  | curl -s http://localhost:8080/v1/chat/completions -H 'Content-Type: application/json' -d @- \
  | jq -r '.choices[0].message.content'
```

The router loads a model on its first request (a few seconds) and keeps one
resident at a time. If the `llama-server` service is installed, it holds port
8080 and `make serve` exits with a bind error. Use `make serve PORT=8081` and
`http://localhost:8081` instead.

`memory/*.md` holds a real user profile, so it is gitignored and only the
`*.example.md` templates are published. The builders abort with the copy commands
above if the profile is missing.

Each `build-*` script writes three files under `models/<name>/`:

- `system.txt`: a debug copy with per-file markers.
- `prompt.txt`: what clients send as the system message.
- `preset.ini`: the model's llama-server section.

`make build` joins `server.ini` and every `preset.ini` into `models/models.ini`,
the file `make serve` reads. Nothing is baked into the model: a client that does
not send `prompt.txt` gets the bare base model.

## Model Files

The GGUFs live in `~/models/gguf` (override with `GGUF_DIR`). A builder aborts
and names the path if its file is missing.

| Model | File | Source |
|---|---|---|
| `gemma` | `gemma4-26b-a4b-it-qat.gguf` | Ollama's `gemma4:26b-a4b-it-qat` blob. |
| `qwen` | `qwen3.6-35b-a3b-mtp-q4_K_M.gguf` | Ollama's `qwen3.6:35b-a3b-mtp-q4_K_M` blob. |
| `lite` | `qwen3.5-9b-mtp-q4_K_M.gguf` | `unsloth/Qwen3.5-9B-MTP-GGUF`, file `Qwen3.5-9B-Q4_K_M.gguf`. |
| `freedom` | `Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf` | Staged 2026-11-08. Matches HuggingFace `HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive`. |

Check the files against the checksums the benchmarks used. Each line must print
`OK`:

```bash
# Runs in: local terminal. Read-only, safe to repeat.
cd ~/models/gguf && sha256sum -c <<'EOF'
4c856523d61d77922dbc0b26753a6bf6208e5d69d80db0c04dcd776832d054c5  gemma4-26b-a4b-it-qat.gguf
d372de8e934898a59e6ccfabc3368474711384d8f1fd4d22d87a3f0a45400cdc  qwen3.6-35b-a3b-mtp-q4_K_M.gguf
e8dd94817e95d6c0939102049d068418269978377b13616c4726235e232841fe  qwen3.5-9b-mtp-q4_K_M.gguf
bbef58c37ce88820be9d98b6437f1cf4bac890c947bd55fc7b68e22098574231  Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf
EOF
```

- `./add-model Org/Repo-GGUF` prints a download command for any HuggingFace repo
  (see *Adding A Model*).
- `lite` downloads again with
  `curl -L --fail -C - -o ~/models/gguf/qwen3.5-9b-mtp-q4_K_M.gguf https://huggingface.co/unsloth/Qwen3.5-9B-MTP-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf`.
- `gemma` and `qwen` have no public byte-identical source, because Ollama and its
  store were removed on 2026-09-15. The closest downloads are
  `google/gemma-4-26B-A4B-it-qat-q4_0-gguf` (`gemma-4-26B_q4_0-it.gguf`) and
  `unsloth/Qwen3.6-35B-A3B-MTP-GGUF` (`Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`). Both are
  different, untested files, and switching starts a new baseline for that model.
- `~/models/gguf` holds only the lineup. Four other GGUFs copied from Ollama were
  deleted on 2026-11-09. Their 2026-10-09 load checks are in
  [`TESTING.md`](TESTING.md), *Other GGUFs on disk*.

## Structure

```text
.
├── prompts/              # behavior controls, run every turn
├── memory/user.md        # durable user profile (gitignored, see *.example.md)
├── eval/                 # benchmark runners, tasks, and offline unit tests
├── scripts/llm           # terminal client for the router, installed to ~/.local/bin
├── systemd/              # the llama-server user service unit
├── models/<name>/        # generated system.txt, prompt.txt, preset.ini (gitignored)
├── models/models.ini     # generated router preset: server.ini + every preset.ini
├── server.ini            # llama-server settings shared by every model
├── build-common.sh       # shared assembly of the prompt stack and presets
├── build-*               # one per model (gemma, qwen, lite, freedom), discovered by make
├── add-model             # scaffolds a new build-* from a staged or remote GGUF
├── apply-universal.py    # ports the portable half of AGENTS.md between repos
├── Makefile              # see the target table below
├── AGENTS.md             # working rules for AI agents in this repo
├── PLAN.md               # the blueprint and the reasoning behind decisions
└── TESTING.md            # testing source of truth and benchmark history
```

The prompt is assembled from `memory/`, then `prompts/`, each sorted by filename,
so reference context comes first and behavior rules last. Each Markdown file is
wrapped in `--- START/END FILE ---`. Files over 100k are skipped, and so are
`*.example.md` templates, because a template beside the real file would give the
model two conflicting profiles. The markers stay in `system.txt` and are stripped
from `prompt.txt`, because models recite them.

## Adding A Model

`./add-model` writes the builder for a GGUF already staged in `$GGUF_DIR`. It
never downloads.

```bash
# Runs in: local terminal, repo root. Writes one build-* file, nothing else.
./add-model                            # pick from staged GGUFs with no builder
./add-model some-model-q4_K_M.gguf     # or name the file and skip the menu
./add-model Org/Repo-GGUF              # or look a HuggingFace repo up first
```

**Given a repo** (`Org/Repo` or any URL for it), it lists the GGUFs with sizes,
groups sharded sets, flags `mmproj` vision projectors, and prints the `curl` for
the quant you pick plus a `sha256sum -c` line per file. The checksums are
HuggingFace's LFS oids, so a truncated download fails the check. A file with no
published checksum is named as unverifiable. The block sets `GGUF_DIR` itself.
The transfer is yours to run, because `$GGUF_DIR` is outside the repo. Re-run
`add-model` once the file is staged. A gated repo, a missing one, or one with no
GGUF stops with the reason.

**Then it scaffolds:**

- It refuses a GGUF that a builder already names in `BASE_MODEL`, and names that
  builder.
- It asks for the router name and refuses one that is malformed, taken, or part
  of the `gemma` / `qwen` / `lite` / `freedom` contract.
- It reads the GGUF header for MTP heads and emits `spec-type = draft-mtp` only
  when they exist.
- It writes no `PARAMS`, because the sampler baseline lives in `server.ini` (see
  *Build And Tune*).

**Then it derives the GPU/CPU split.** It loads the model once with
`--fit-target 2048` on a spare port, passing `server.ini`'s `ctx-size` as `-c`
and `ubatch-size` as `-ub`, and records the fitter's choice in `LOAD` as
`n-cpu-moe` with the fit line and date. **Close anything holding VRAM first**,
because the split is sized from what is free at that moment.

- It refuses to probe while the router at `LLM_URL` (default
  `http://localhost:8080`) lists a model as loaded or loading. Free it with
  `llm unload`. A sleeping model has already released its VRAM.
- It stops if `ctx-size` is missing.
- A model that fits entirely gets no `n-cpu-moe`, and it says so.
- `--no-probe` skips the probe and leaves the split for you.

`make build` picks the new model up, because the lineup is discovered from the
`build-*` files.

## Build And Tune

A builder's only model-specific part is its config block:

```bash
MODEL_NAME="qwen"
BASE_MODEL="qwen3.6-35b-a3b-mtp-q4_K_M.gguf"   # filename under $GGUF_DIR
LOAD=(
  'n-cpu-moe = 37'           # expert layers kept in system RAM, pinned (see below)
  'spec-type = draft-mtp'    # MTP speculative decoding, only for GGUFs that carry MTP heads
)
```

Keys are llama-server flag names without the dashes. Builders source
`build-common.sh`, which aborts up front if the GGUF is not in `GGUF_DIR`, so a
stale base fails loudly instead of leaving a half-written preset. Use
`./add-model` for a new model rather than copying a builder.

**The lineup is whatever `build-*` scripts exist.** `make` discovers them, and
deleting one drops its section from `models/models.ini` on the next
`make build`. Sections follow the sorted filenames, but the order carries no
meaning, because the router sorts `/models` itself.

**The sampler baseline is shared, in `server.ini`'s `[*]` section.** Only
`run-json.py` sends sampler options, so every other suite inherits the preset. A
value that differs between builders makes the leaderboard measure model and
sampler together. That invalidated the 2026-06-14 coding, learning and tutor
tables, which compared `gemma` at `temperature 0.75` / `presence_penalty 0.2`
against `qwen` at `0.2` / `0.0`.

```ini
; server.ini, [*] section (each value carries its reason in the file)
ctx-size = 131072
temp = 0.2
top-p = 0.95
top-k = 40
min-p = 0.05
presence-penalty = 0.0
repeat-penalty = 1.05
```

A builder that defines its own `PARAMS` overrides these in its own section. That
is how a model that needs different decoding gets a separate preset. Keep such a
preset out of head-to-head runs.

**`LOAD` is per model and pinned.** `server.ini` sets `fit = off`, so the split
is what the builder declares, not what llama.cpp's fitter picks from the VRAM
free at load time. Otherwise an open browser would change a benchmark's offload
between runs.

**The splits leave about 2 GB of VRAM free.** Splits fitted at the default 1 GB
margin crashed with CUDA out of memory under normal desktop use. At 131072
context the values are gemma 24, qwen 37 and freedom 34, each spike-tested with
about 3.0 GB of the card held by other processes. `lite` stays all-GPU by Casey's
choice, so a desktop VRAM spike can crash it. The measurements are in
[`TESTING.md`](TESTING.md), *Pinned split history*.

**`server.ini` reads weights into RAM and uses a 1024-token physical batch.**
`load-mode = none` keeps the CPU expert weights in page cache, and
`ubatch-size = 1024` more than doubled prompt ingest for the models that spill.
`lite` keeps `ubatch-size = 512` in its `LOAD` to stay all-GPU. Numbers:
[`TESTING.md`](TESTING.md), *Pinned split history* and *Preset tuning on build
11022*.

### Re-deriving a split by hand

`./add-model` on a fresh builder does this for you. The manual recipe loads the
GGUF with a 2 GB fit margin and the builder's `spec-type`, then reads the fit
line. Leaving MTP off gives a wrong answer, because the MTP layer needs VRAM of
its own. The log goes to a file because llama-server buffers it, and a piped
probe stopped early lost the fit line twice.

The loop only ends for a model that spills. A model that fits never prints an
overflow line (`lite` logs `offloaded 34/34 layers to GPU`, measured 2026-09-15),
so the loop waits forever. `add-model` also stops on `model loaded`.

```bash
# Runs in: local terminal, with nothing else holding the GPU. Safe to re-run.
LOG="$(mktemp)"
llama-server -m ~/models/gguf/qwen3.6-35b-a3b-mtp-q4_K_M.gguf -c 131072 -np 1 -fa on \
  -ctk q4_0 -ctv q4_0 --no-mmproj --spec-type draft-mtp --fit-target 2048 \
  -ub 1024 --port 8081 -lv 4 > "$LOG" 2>&1 &
PID=$!
echo "probe pid: $PID, log: $LOG"
until grep -q 'layers (.* overflowing)' "$LOG" || ! kill -0 "$PID"; do sleep 1; done
grep 'layers (.* overflowing)' "$LOG" || echo "no fit line: read $LOG"
kill "$PID"
```

- The `echo` must print a numeric pid.
- A line ending `42 layers (34 overflowing), 6071 MiB used, 2157 MiB free` means
  `n-cpu-moe = 34`.
- Drop `--spec-type draft-mtp` for `gemma` and `freedom`, which have no MTP
  layer.
- `-ub 1024` matches `server.ini`, because the compute buffer grows with the
  batch. Use `-ub 512` for `lite` or any model whose `LOAD` pins 512.
- The count moves with free VRAM, which is why it is pinned rather than re-fitted
  per run.

### Where changes belong

| Change | File |
|---|---|
| Behavior rule for all models | `prompts/` |
| Stable user preference or fact | `memory/user.md` |
| New coding eval task | `eval/coding_tasks.py` |
| New content eval task | `eval/content_tasks.py` |
| New JSON or long-context eval task | `eval/json_tasks.py` |
| New learning or tutor eval task | `eval/learning_tasks.py` / `eval/tutor_tasks.py` |
| New prompt-stack rule check | `eval/persona_tasks.py` |

Keep prompt text terse, because every prompt token is spent every turn. Prefer
removing bad rules or tuning `PARAMS` over adding instructions. To find which
rules earn their place, run
`./eval/run-persona.py --models gemma qwen lite freedom --system-mode baseline`
and compare it with a stacked run. A rule the base model obeys unprompted costs
tokens for nothing. *Prompt Stack Value* below has the current answer.

**After editing `prompts/` or `memory/`, rebuild and run the persona suite.** It
is the only suite that tests the stack rather than the base model:

```bash
make check   # rebuild only the models whose stack changed, then run run-persona.py
make hook    # install a pre-commit hook that does the same on staged prompt edits
```

### Make targets

| Target | What it does | When |
|---|---|---|
| `make build` | Rebuild the models whose prompt stack changed, then join `models/models.ini` | After editing a builder or the prompt stack |
| `make check` | `build`, then the persona suite over every model | Before committing a prompt change |
| `make persona` | The persona suite without rebuilding | Checking the stack without touching files |
| `make deploy` | Copy `models/models.ini` to `~/.config/llama.cpp/` | Shipping a preset change to the service |
| `make serve` | Run a router in the foreground over the repo's own preset | Trying an undeployed change, with `PORT=8081` |
| `make hook` | Install the pre-commit hook that runs `make check` | Once per clone |
| `make clean` | Drop the build stamps | Forcing a full rebuild |

- `make deploy` writes outside the repo and needs a service restart, so both are
  yours to run (see *Serving other apps*).
- `make check` and `make persona` write to `eval/runs/`, which feeds the
  leaderboard. Send a code-check run to scratch with `OUT_ROOT`, which becomes
  the runner's `--out-root`: `make check OUT_ROOT=/tmp/scratch`.
- Editing one `build-*` script rebuilds only that model. Editing `prompts/` or
  `memory/` rebuilds every model. `MODELS` is never edited by hand.

## llama-server

llama.cpp's `llama-server` runs in router mode: one process on port 8080 that
starts a child server per model on demand and routes each request by its `model`
field. Day to day it runs as the `llama-server` user service over
`~/.config/llama.cpp/models.ini` (see *Serving other apps*). `make serve` runs a
router in the foreground over the repo's own `models/models.ini`.

The installed build is 12155 (commit `e30ad80fd`, since 2026-11-05). The rollback
target and the leaderboard's build is 12009 (`e216f4ec8`). llama.cpp is built
from source because the AUR `llama.cpp-cuda` package was reported stale. Earlier
builds and the 2026-09 compiler fixes are in [`TESTING.md`](TESTING.md),
*llama.cpp build history*.

First install (updates go through `scripts/llama-update`, below):

```bash
# Runs in: local terminal, as your user (no sudo). Safe to re-run.
SRC="$HOME/src/llama.cpp"
[ -d "$SRC/.git" ] || git clone https://github.com/ggml-org/llama.cpp "$SRC"
cmake --fresh -S "$SRC" -B "$SRC/build" -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=86 \
  -DBUILD_SHARED_LIBS=OFF -DCMAKE_BUILD_TYPE=Release \
  && cmake --build "$SRC/build" -j 20 --target llama-server \
  && ln -sfn "$SRC/build/bin/llama-server" "$HOME/.local/bin/llama-server" \
  && llama-server --version
```

`-DCMAKE_CUDA_ARCHITECTURES=86` is the RTX 3080. Change it for another card.

### Updating llama.cpp

`scripts/llama-update` builds the new version **beside** the live one and
changes nothing the service uses until you swap. It never restarts the service.
It prints the command for you to run when no app or benchmark is mid-request.
Building beside matters because the router starts every model process from
`~/src/llama.cpp/build/bin/llama-server`, so an in-place rebuild would run new
model processes under the old router.

```bash
# Runs in: local terminal, repo root. Writes only inside ~/src/llama.cpp.
./scripts/llama-update status     # installed build, waiting commits, CUDA and server changes
./scripts/llama-update build      # pull --ff-only, build into build-next; the live build is untouched
```

`build` refuses uncommitted changes or a diverged history, records a
`known-good-b<build>` branch at the commit you leave, and configures with
`--fresh`. Since 2026-11-05 it also builds `llama-perplexity`, so `verify-build`
can compare the two builds' output, and a build missing either binary is not
swappable. Older build folders cannot gain it,
because a build folder cannot be moved. A failed build leaves `build` untouched
and `build-next` unswappable.

Check the new build against this repo's preset before it goes live:

```bash
# Runs in: local terminal, repo root. Needs the service idle or stopped (it
# refuses while a model is loaded). Starts and stops everything it uses.
./scripts/verify-build            # checks ~/src/llama.cpp/build-next against the live build
```

- It loads each model at its pinned split with both builds and fails if the new
  one needs more than 100 MiB of extra VRAM.
- It runs the smoke profile on a scratch router on 8081 (results go to a temp
  folder, never `eval/runs/`) and fails on any new GPU fault.
- It ends with `PASS` or `FAIL`, and exits non-zero on failure.
- An `INFO` row per model reports the KL divergence of the new build's
  next-token probabilities from the live build's, on the wikitext-2 test set (20
  chunks of 512 tokens, at the pinned split and cache types). `INFO` rows never
  change the verdict. The same binary on both sides gives a mean KLD of 0 and
  100 % same top token, so any non-zero value is a real numerical change.
- The row says `skipped` when either build lacks `llama-perplexity` (built before
  2026-11-05) or the corpus is missing. `--skip-kl` turns the report off.
- The reference logits are 2.5 to 2.7 GB per model. They go to `.scratch/kld/`
  (gitignored), not `/tmp`, which is RAM on this box, and each is deleted when
  its model is done.

Fetch the corpus once:

```bash
# Runs in: local terminal. Writes only ~/models/eval-data. Safe to re-run.
mkdir -p ~/models/eval-data && cd ~/models/eval-data && sh ~/src/llama.cpp/scripts/get-wikitext-2.sh
```

On a `PASS`, swap and restart straight away, because between the two a model
load runs the new binary under the old router:

```bash
# Runs in: local terminal, repo root. The printed restart is yours to run.
./scripts/llama-update swap       # build -> build-prev, build-next -> build
systemctl --user restart llama-server && ./scripts/llm status --wait 60   # must list every model
```

To undo a bad update:

```bash
# Runs in: local terminal, repo root.
./scripts/llama-update rollback   # build <-> build-prev, then restart as above
```

`LLAMA_SRC` points it at another checkout and `CUDA_ARCH` at another card. A new
build is a new benchmark baseline, so the leaderboard stays on its build until a
new `standard` pass is promoted.

### Shared server settings

`server.ini` holds what every model shares, replacing the old Ollama systemd
override:

| Was (Ollama) | Now (llama-server) | Where |
|---|---|---|
| `OLLAMA_NUM_PARALLEL=1` | `parallel = 1` (the server default is 4 slots) | `server.ini` |
| `OLLAMA_FLASH_ATTENTION=1` | `flash-attn = on` | `server.ini` |
| `OLLAMA_KV_CACHE_TYPE=q4_0` | `cache-type-k = q4_0`, `cache-type-v = q4_0` | `server.ini` |
| `OLLAMA_MAX_LOADED_MODELS=1` | `--models-max 1` | service unit, `make serve` |
| `OLLAMA_KEEP_ALIVE=10m` | `sleep-idle-seconds = 600`: an idle model sleeps and frees its VRAM, and the next request wakes it | `server.ini` |
| automatic GPU/CPU split | `fit = off` plus each builder's `LOAD` | `server.ini`, `build-*` |

- **`--models-max 1` keeps benchmarks clean.** Two resident models would share
  the 10 GB, so throughput would depend on which other model was loaded. With
  one, each model gets the whole card. `run-learn.py` and `run-tutor.py`
  generate every response first and then loop by judge, so each model loads once
  per phase.
- **The eval runners go through `eval/_gateway.py`,** which reads `LLM_URL`
  (default `http://localhost:8080`), sends `prompt.txt` as the system message,
  and turns prompt caching off. The llama-server docs warn that cached prefixes
  break bit-identical results, and reproducibility is already the weak spot (see
  [`TESTING.md`](TESTING.md)).
- **Restarting the router invalidates a run in flight.** `run-learn.py`,
  `run-persona.py` and `run-tutor.py` abort on a streak of connection failures
  rather than scoring them as model failures, but the run is still lost. Let a
  `standard` pass finish first.
- **A running router does not reload its preset.** Restart the service after
  `make deploy`, or restart `make serve` after `make build`.

Common commands:

```bash
make build && make deploy && systemctl --user restart llama-server && ./scripts/llm status --wait 60   # ship a preset change
make serve PORT=8081                                   # try an undeployed change beside the service
curl -s localhost:8080/models | jq '.data[] | {id, status: .status.value}'
curl -s -X POST localhost:8080/models/load   -H 'Content-Type: application/json' -d '{"model":"gemma"}'
curl -s -X POST localhost:8080/models/unload -H 'Content-Type: application/json' -d '{"model":"gemma"}'
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
```

## Serving other apps

The router is a machine-level service. Jobhunt, SEO-LLM, Friends-Hermes, this
repo's evals and editors are all clients of `http://localhost:8080`:

- Jobhunt and SEO-LLM use `gemma`, `qwen` and `lite`.
- Friends-Hermes uses `freedom` from a QEMU guest, which reaches the host's
  localhost at `10.0.2.2:8080`, so the router stays bound to `127.0.0.1`.

| Piece | Location |
|---|---|
| Binary | `~/.local/bin/llama-server` (built from `~/src/llama.cpp`) |
| Models | `~/models/gguf/` |
| Server config | `~/.config/llama.cpp/models.ini`, written by `make deploy` |
| Service | `~/.config/systemd/user/llama-server.service`, copied from `systemd/llama-server.service` |

This repo is the tracked source of the config. A half-finished edit never
reaches the apps until `make deploy`. The service runs as you, needs no sudo,
and starts at login.

Install once:

```bash
# Runs in: local terminal, as your user. Safe to re-run.
cd ~/Apps/Local-LLM && make deploy
mkdir -p ~/.config/systemd/user
cp ~/Apps/Local-LLM/systemd/llama-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now llama-server
./scripts/llm status --wait 60
```

The last line must list every model. It polls for up to 60 seconds because
`systemctl` returns, and `is-active` reports `active`, before the router
listens. A request sent straight after a restart got `Connection refused` on
2026-10-09 and an empty model list on 2026-11-09. If the router never answers,
it exits 1 and names the `journalctl` command to read. Every restart in this
README ends with the same check.

Both files are copies, so after changing the tracked unit, copy it again and run
`systemctl --user daemon-reload`.

| Task | Command |
|---|---|
| Status and logs | `systemctl --user status llama-server`, `journalctl --user -u llama-server -f` |
| Ship a preset change | `make build && make deploy && systemctl --user restart llama-server && ./scripts/llm status --wait 60` |
| Try a change beside the service | `make serve PORT=8081`, and `LLM_URL=http://localhost:8081` for the eval runners |
| Free the GPU now | `systemctl --user stop llama-server` (idle models also sleep after 10 minutes) |
| Plain `make serve` on 8080 | Stop the service first. The second router exits with `couldn't bind HTTP server socket`. |

### Terminal helper: `llm`

`scripts/llm` gives Ollama-style commands for the router. It is standalone
Python, so the installed copy works without this repo:

```bash
# Runs in: local terminal, as your user. Safe to re-run.
install -m 755 ~/Apps/Local-LLM/scripts/llm ~/.local/bin/llm
llm status
```

| Ollama | `llm` | What it does |
|---|---|---|
| `ollama ps`, `ollama list` | `llm status` | Router up or down, each model's state (`loaded`, `sleeping`, `unloaded`), VRAM held by llama-server. |
| (preload) | `llm load MODEL` | Loads and waits until ready. Reports `already loaded` or `sleeping` instead of reloading. |
| `ollama stop MODEL` | `llm unload [MODEL]` | Unloads one model, or every resident one, and waits for the router to confirm. |
| `ollama run MODEL "..."`, `ollama run MODEL` | `llm chat MODEL "..."`, `llm chat MODEL` | One request, streamed. See below. |
| `journalctl -u ollama -f` | `llm logs` | Router log, last 50 lines then follow, without the per-load argument dump. `llm logs --all` shows every line. Extra arguments go to journalctl, for example `llm logs -n 20 --no-pager`. |

`llm chat` details:

- Thinking is off unless `--think`, which prints the reasoning on stderr, so
  `> file` keeps only the answer.
- `--verbose` adds token counts and speed on stderr.
- `--system-file PATH` sends a system message (for example
  `models/qwen/prompt.txt`). Unlike `ollama run`, `llm chat` sends none
  otherwise, so the reply comes from the bare model.
- A piped stdin becomes the prompt. With no prompt on a terminal it starts a
  session: Ctrl-C stops the current answer and keeps what arrived, and Ctrl-C or
  Ctrl-D at the `>>>` prompt ends it.
- An unknown model lists the available names.

`LLM_URL` points `llm` at another router, such as a test one on port 8081.

### Contract for apps

Other repos depend on these, so changing one is a coordinated change across
repos:

- Endpoint `POST http://localhost:8080/v1/chat/completions` (OpenAI format). Any
  non-empty API key is accepted.
- `model` is one of `gemma`, `qwen`, `lite`, `freedom`.
- The app sends its own system message. A request without one gets the bare base
  model.
- Context is fixed at 131072 tokens by the preset (65536 until 2026-10-30), with
  no per-request size. A longer prompt returns HTTP 400
  `exceed_context_size_error` instead of being truncated.
- Structured output:
  `response_format: {"type": "json_schema", "json_schema": {"name": "...", "schema": {...}}}`.
  The top-level `schema` shape in the llama-server README was silently ignored
  on build 10968.
- Thinking off: `chat_template_kwargs: {"enable_thinking": false}`. When on,
  thoughts come back in `reasoning_content`, apart from the answer.
- One model is loaded at a time, so two apps using different models take turns
  paying the load.
- After 10 idle minutes a model sleeps and frees its VRAM, and `GET /models`
  reports `sleeping`. The next request wakes it with no client change. `lite`
  answered in about 1 to 2 seconds. `gemma` took 11.0 s and `qwen` 17.6 s for a
  request that takes 2.4 s and 3.1 s awake, about the cost of a cold load
  (2026-10-31, see [`TESTING.md`](TESTING.md)).

## Use In VSCode (Continue / Cline)

Both extensions use the router's OpenAI-compatible API at
`http://localhost:8080/v1` with the `llama-server` service running. The model
names are `gemma`, `qwen`, `lite` and `freedom`, and any non-empty key is
accepted.

A client that does not send `models/<name>/prompt.txt` as its system message
gets the bare base model, without the identity, honesty and formatting rules
the persona suite measures.

### Continue

Continue takes a per-model system message in `chatOptions.baseSystemMessage`.
This prints `~/.continue/config.yaml` entries with each `prompt.txt` embedded,
ready to paste under `models:`:

```bash
# Runs in: repo root, after make build. Prints YAML only, writes nothing.
python3 - <<'EOF'
import json
for name, label in [("qwen", "stack fidelity"), ("gemma", "coding/content/tutor"), ("lite", "fast"), ("freedom", "uncensored")]:
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

The embedded prompt includes the `memory/` profile, so treat that config as
personal. Re-run the script after `make build` when the stack changes. Pick the
default from the leaderboard, not from load size.

### Cline

Set **API Provider** to `OpenAI Compatible`, **Base URL** to
`http://localhost:8080/v1`, **API Key** to any value, **Model ID** to `qwen`,
`gemma`, `lite` or `freedom`, and the context window to `131072`. Cline sends its
own system prompt and its docs offer no way to replace it, so it runs without
this repo's stack. Cline ingests large prompts, so choose by the **Prompt
tok/s** column of `run-speed.py`, not generation speed.

### Notes for both

- Switching models costs a reload (about 2 to 7 seconds), because the router
  keeps one loaded.
- `gemma`, `qwen` and `freedom` keep some expert layers in system RAM, and MoE
  keeps that usable. `lite` fits, and shows what the split costs.
- The eval runners turn thinking off per request. Requests without the flag get
  the chat template's default, and `gemma` was seen thinking by default.

## Evaluation

Runners live under `eval/` and write to `eval/runs/<UTC>/`. Routine testing uses
profiles: `smoke` after a rebuild, `standard` for the routine comparison (about 1
hour), and `deep` for a several-hour confidence run. [`TESTING.md`](TESTING.md)
says when to use each.

```bash
./eval/run-profile.py smoke --models gemma qwen lite freedom
./eval/run-profile.py standard --models gemma qwen lite freedom
```

Single runners, for targeted sweeps:

```bash
./eval/run-speed.py --models gemma qwen lite freedom
./eval/run-code.py --models gemma qwen lite freedom
./eval/run-content.py --models gemma qwen lite freedom
./eval/run-learn.py --models gemma qwen lite freedom
./eval/run-tutor.py --models gemma qwen lite freedom
./eval/run-json.py --models gemma qwen lite freedom
./eval/run-tools.py --models gemma qwen lite freedom
./eval/run-persona.py --models gemma qwen lite freedom
```

- **`run-persona.py`** is the prompt-stack regression suite. It checks that the
  rules in `prompts/` and `memory/` are obeyed: the identity rule, the
  `memory/user.md` honesty rules about Casey's skill buckets, `Unverified:`
  marking, and output shape. It is the only runner that notices when a prompt
  edit breaks a rule. Scoring is deterministic regex, with no judge.
- **`run-tools.py`** is the agent test. Each task offers a small tool list with
  distractors and scores the right tool, the right arguments, nothing extra, and
  no call where none fits. Five tasks feed the model's call a canned result and
  score what it does next: inventing an address instead of using the one it
  looked up, answering from memory after a search, or claiming success on an
  error. Every tool result is a fixture, with no network.
- **`run-json.py`** is the structured-output test Jobhunt and SEO-LLM depend on.
  It constrains decode with a JSON schema, buries facts in a long document, and
  scores schema conformance and fact recall. It first checks that every model
  serves at least `--num-ctx` (default 65536, room for its `high` preset's
  largest prompt of 51,307 tokens) and aborts otherwise. SEO-LLM requires 32768,
  and Jobhunt sizes its prompts to fit 32768.
- **`run-code.py`, `run-learn.py` and `run-tutor.py`** execute model-written
  Python inside **bubblewrap**: read-only `/usr`, no network, no host PID or IPC
  namespace, no `$HOME`, and writes only to a throwaway CWD. The banner shows the
  mode. Without a working `bwrap` it falls back loudly to a timeout-bounded
  subprocess. Flags and sandbox details are in [`TESTING.md`](TESTING.md).

Offline unit tests need no server, GPU or model:

```bash
python3 -m unittest discover -s eval -p 'test_*.py'
```

After a run, promote the numbers instead of copying them:

```bash
./eval/promote.py             # newest run per suite -> the table below
./eval/promote.py --check     # exit 1 if the table is stale
```

## Benchmark Leaderboard

**Measured on llama.cpp build 12155, 2026-11-09** (`standard` pass, pinned
offload gemma 24, qwen 37 and freedom 34, 131072 context, `load-mode = none`,
`ubatch-size = 1024`, prompt caching off). The coding suite was rerun alone after
a GPU fault. [`TESTING.md`](TESTING.md), *Current Benchmark Snapshot*, compares
it with the two build 12009 passes. Learn and tutor now use three judges per
response, so they are not comparable with three-model results.

- **Read close rows as ties.** Between two identical 12009 passes `lite` moved 3
  points on coding and on tool calling, and the persona suite's spread at low attempt
  counts (*Interpreting Results*) is wider than the gaps here. Failures are
  strong signal, close wins weak, and speed breaks quality ties.
- **Timing includes re-processing the full prompt** on every call, so it runs
  slower than the Ollama-era figures, which are in [`TESTING.md`](TESTING.md)
  under Historical Notes.
- **The block is generated.** `./eval/promote.py` rewrites it from `eval/runs/`,
  so it cannot drift the way the hand-kept 2026-06-14 tables did.

<!-- BENCH:START -->

_Generated by `./eval/promote.py` from 8 runs, 20261109T152408Z–20261109T184614Z (`eval/runs/`). Do not hand-edit: re-run the script._

| Suite | Winner | `lite` | `qwen` | `freedom` | `gemma` |
|---|---|---|---|---|---|
| Speed | `lite` | 139.7, all GPU, MTP | 58.3, GPU + 37 MoE layers on CPU, MTP | 57.1, GPU + 34 MoE layers on CPU | 47.1, GPU + 24 MoE layers on CPU |
| Coding | tie | 20/27 | 26/27 | 24/27 | 25/27 |
| Content | `gemma` | 7/9 | 7/9 | 8/9 | 9/9 |
| Learning | tie | 8.2, 10/12 | 9.9, 12/12 | 9.9, 12/12 | 9.8, 12/12 |
| Tutor (leak-gated) | `gemma` | 5.8, 6/15 | 5.6, 6/15 | 3.9, 8/15 | 8.4, 2/15 |
| JSON / long-context | tie | 100%, 2.3 | 100%, 7.1 | 100%, 6.7 | 100%, 5.5 |
| Prompt stack | `qwen` | 67% | 86% | 76% | 67% |
| Tool calling | `freedom` | 33/39, 85% | 36/39, 92% | 39/39, 100% | 30/39, 77% |

Winner is `tie` where the runner flagged the margin as within its close-result threshold — those rows should break on speed, not the headline metric. Per-suite run directories:

- Speed: `eval/runs/20261109T152408Z/speed/summary.md` — 140 tok/s (all GPU, MTP, thinking OFF)
- Coding: `eval/runs/20261109T184614Z/code/summary.md` — 26/27 (96%) @ 74 tok/s
- Content: `eval/runs/20261109T152833Z/content/summary.md` — 9/9 clean (100%) @ 47 tok/s
- Learning: `eval/runs/20261109T155652Z/learn/summary.md` — teach 9.9/10 (code 12/12, explanation 9.9/10)
- Tutor (leak-gated): `eval/runs/20261109T162935Z/tutor/summary.md` — teach 8.4/10 (leaks 2/15, explanation 9.5/10, non-leak explanation 9.6/10)
- JSON / long-context: `eval/runs/20261109T153907Z/json/summary.md` — score 100% (schema 100%, facts 100%)
- Prompt stack: `eval/runs/20261109T153312Z/persona/summary.md` — 18/21 clean (86%)
- Tool calling: `eval/runs/20261109T154642Z/tools/summary.md` — 39/39 (100%)

<!-- BENCH:END -->

### Current picks

From the 2026-10-30 and 2026-10-31 `standard` passes on build 12009 at 131072
context, not yet revised for the 2026-11-09 pass on 12155 in the leaderboard above.

| Use | Pick | Basis |
|---|---|---|
| Fast local general use | `lite` | 132.5 tok/s, the only model fully on GPU. 2.2× `qwen` (61.5), 3.1× `gemma` (43.0). |
| Agentic tools (Cline) | `lite` | Prompt ingest 3354 tok/s vs `gemma` 1462 and `qwen` 1015. Use `qwen` when tool accuracy matters more (39/39 in both passes vs `lite` 36 then 33). |
| Tool calling / agent loops | `qwen` | 39/39 in both passes vs `lite` 33/39 and `gemma` 30/39. `lite` flipped three tasks between identical passes. `gemma` skipped the tool on 6 attempts and got an argument wrong on 3, both times. |
| Coding | `gemma` | 26/27 vs 25/27 for `qwen` and `lite`, and 25 to 27 in every pass since 2026-09-15. The runner calls this pass a tie, so the pick rests on that record. |
| Content / SEO / copy | `gemma` | 9/9 clean in every pass since 2026-07-28. `lite` 8/9, `qwen` 7/9. |
| Socratic tutoring (no spoilers) | `gemma` | 0/15 leaks vs 5/15 (`qwen`) and 7/15 (`lite`), and 1 in 30 across both passes. Teach 9.1/10 vs 6.0 and 4.9. |
| Learning explanations | `qwen` or `gemma` (tie) | 9.9 and 9.8/10 with 12/12 on the code gate, inside the tie threshold. |
| Structured JSON / app smoke tests | `lite` | All three scored 100%. `lite` averages 2.3s against `gemma` 5.5s and `qwen` 7.3s. |
| Prompt-stack fidelity | `qwen` | 90% clean in both passes vs 57% for `gemma` and `lite`. |

No single model wins. `lite` takes everything speed-shaped, `gemma` takes
content, tutoring and coding, and `qwen` takes tool calling and the prompt stack
and ties `gemma` on learning. Tool calling is where `gemma` is clearly weakest.

Two caveats:

- Samples are small (n = 9 to 27 per model), so a few points either way is noise.
  Failures and the leak-rate gap are strong signal.
- Seeded runs mostly reproduce across router restarts (`lite` 10/10, `gemma`
  15/15, `qwen` 14/15 in a temperature 1.5 check, see [`TESTING.md`](TESTING.md)),
  but not always. Compare prompt or scorer changes by re-scoring saved responses.

## Prompt Stack Value

Measured 2026-07-28 with `run-persona.py`, stacked against
`--system-mode baseline`: `identity` and `model_origin` are load-bearing,
`familiar_skill` helps but leaks, `unverified` and `fields_echo` work on `qwen`
only, and `bash_block` bought nothing, so it was deleted. The per-rule table is
in [`TESTING.md`](TESTING.md), *Measuring what the prompt stack contributes*.

## Models Tested

Every model tried before the current lineup, and why it left, is in
[`TESTING.md`](TESTING.md), *Models Tested*.

## Hardware Envelope

Benchmarks are for this box: RTX 3080 10 GB, Ryzen 5900x, 32 GB DDR4-3600.
Models that fit fully on the GPU run fast. Dense spillover is usually too slow,
and MoE spillover can stay usable. Desktop apps hold about 1 GB of the card at
idle, so a model plus its KV cache has about 8.6 GB. Runtime: llama.cpp build
12155 (`e30ad80fd`) with CUDA 13.4.

## Docs

- [`PLAN.md`](PLAN.md): the blueprint. Scope, architecture decisions and their
  reasons, locked decisions with dates, and open questions. Read it before any
  structural change.
- [`TESTING.md`](TESTING.md): runner docs, safety notes, benchmark history and
  detailed results.
- [`AGENTS.md`](AGENTS.md): the rules an AI coding agent follows here.
  Everything above `## Project-specific rules` is portable, and this repo's copy
  is the master. `apply-universal.py` copies the portable half into every other
  `AGENTS.md` in or one folder below `~/Apps` and `~/Documents/Projects`, and
  keeps each repo's project rules byte for byte:

  ```bash
  # Runs in: local terminal, from this repo.
  ./apply-universal.py --check AGENTS.md   # "in sync" or a diff per file, writes nothing, exit 1 on drift
  ./apply-universal.py AGENTS.md           # writes every file that differs
  ./apply-universal.py AGENTS.md ../other-repo/AGENTS.md   # only the files named
  ```

  Read the `--check` diff first: a universal-rule edit made in another repo shows
  up as drift and would be overwritten, so copy it here first. A file without
  the `## Project-specific rules` header or its intro comment stops the run with
  nothing written. It is safe to re-run and never commits.
