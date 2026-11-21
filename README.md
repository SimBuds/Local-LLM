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
| `gemma` | `google_gemma-4-26B-A4B-it-Q4_K_M.gguf` | 128K | 26 MoE layers on CPU | 26B A4B MoE, Q4_K_M since 2026-11-20. The pick for coding, content and teaching. |
| `qwen` | `qwen3.6-35b-a3b-mtp-q4_K_M.gguf` | 128K | 38 MoE layers on CPU, MTP | 35B A3B MoE. The largest model that stays usable here. The pick for agent work and tool calling. |
| `lite` | `qwen3.5-9b-mtp-q4_K_M.gguf` | 128K | all GPU, MTP | Dense 9B. The only one that fits in 10 GB, so it is the speed anchor. |

- **MoE spillover stays usable.** `gemma` and `qwen` activate few
  parameters per token, so the expert layers kept in system RAM cost little.
  `lite` needs no spillover, which makes it the baseline the others are measured
  against.
- **MTP** (multi-token prediction) is speculative decoding built into the Qwen
  GGUFs.
- **Weights history.** Until 2026-11-20 `gemma` and `qwen` were the files the
  Ollama-era benchmarks used, copied byte for byte. Both are Hugging Face files
  now, so their scores start a new history on that date. Ollama's `qwen3.5:9b`
  does not load in llama.cpp, so `lite` is unsloth's MTP build at the same Q4_K_M
  quant, with its own history from 2026-09-14.
- **`freedom` was removed on 2026-11-20.** It was the uncensored tune that
  Friends-Hermes called. Its results stay in [`TESTING.md`](TESTING.md), and
  `add-model` still refuses the name so no other model can answer to it.
- **`nemotron` was removed on 2026-11-20.** NVIDIA's Nemotron 3.5 Lightning
  30B-A3B was served from 2026-11-19 and earned no role in two passes. Its
  results stay in [`TESTING.md`](TESTING.md), *Five-model pass with nemotron* and
  *Four-model pass on the new gemma and qwen files*.
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
| `gemma` | `google_gemma-4-26B-A4B-it-Q4_K_M.gguf` | `bartowski/google_gemma-4-26B-A4B-it-GGUF`, staged 2026-11-20. |
| `qwen` | `qwen3.6-35b-a3b-mtp-q4_K_M.gguf` | `unsloth/Qwen3.6-35B-A3B-MTP-GGUF`, file `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`, staged 2026-11-20. |
| `lite` | `qwen3.5-9b-mtp-q4_K_M.gguf` | `unsloth/Qwen3.5-9B-MTP-GGUF`, file `Qwen3.5-9B-Q4_K_M.gguf`. |

Check the files against the checksums the benchmarks used. Each line must print
`OK`:

```bash
# Runs in: local terminal. Read-only, safe to repeat.
cd ~/models/gguf && sha256sum -c <<'EOF'
a07f72221e8e3f77455ab0d7f7652d01a9f63c262b954aa6932a53275a0e895a  google_gemma-4-26B-A4B-it-Q4_K_M.gguf
0b21525e972670ed59e1812e170b27c26355381f0656ecc4e25617ece7dac58b  qwen3.6-35b-a3b-mtp-q4_K_M.gguf
e8dd94817e95d6c0939102049d068418269978377b13616c4726235e232841fe  qwen3.5-9b-mtp-q4_K_M.gguf
EOF
```

- `./add-model Org/Repo-GGUF` prints a download command for any HuggingFace repo
  (see *Adding A Model*).
- `lite` downloads again with
  `curl -L --fail -C - -o ~/models/gguf/qwen3.5-9b-mtp-q4_K_M.gguf https://huggingface.co/unsloth/Qwen3.5-9B-MTP-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf`.
- `qwen` downloads again with
  `curl -L --fail -C - -o ~/models/gguf/qwen3.6-35b-a3b-mtp-q4_K_M.gguf https://huggingface.co/unsloth/Qwen3.6-35B-A3B-MTP-GGUF/resolve/main/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`.
- `gemma` downloads again with
  `curl -L --fail -C - -o ~/models/gguf/google_gemma-4-26B-A4B-it-Q4_K_M.gguf https://huggingface.co/bartowski/google_gemma-4-26B-A4B-it-GGUF/resolve/main/google_gemma-4-26B-A4B-it-Q4_K_M.gguf`.
- The Ollama-era `gemma` and `qwen` files are gone, and every score dated before
  2026-11-20 is theirs. The files above are different weights files, so each
  starts a new baseline for its model.
- `~/models/gguf` holds only the lineup. Four other GGUFs copied from Ollama were
  deleted on 2026-11-09. Their 2026-10-09 load checks are in
  [`TESTING.md`](TESTING.md), *Other GGUFs on disk*.

## Structure

```text
.
├── prompts/              # behavior controls, run every turn
├── memory/user.md        # durable user profile (gitignored, see *.example.md)
├── eval/                 # benchmark runners, tasks, and offline unit tests
├── scripts/              # llm (terminal client, installed to ~/.local/bin), llama-update, verify-build
├── .claude/skills/verify # how an AI agent verifies a change here without touching the live service
├── pi/                   # Pi coding-agent config for a VM guest (see *Use With Pi*)
├── systemd/              # the llama-server user service unit
├── models/<name>/        # generated system.txt, prompt.txt, preset.ini (gitignored)
├── models/models.ini     # generated router preset: server.ini + every preset.ini
├── server.ini            # llama-server settings shared by every model
├── build-common.sh       # shared assembly of the prompt stack and presets
├── build-*               # one per model (gemma, qwen, lite), discovered by make
├── add-model             # scaffolds a new build-* from a staged or remote GGUF
├── apply-universal.py    # ports the portable half of AGENTS.md between repos
├── check-agents.py       # read-only size and repeated-rule report for AGENTS.md
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
  of the `gemma` / `qwen` / `lite` contract, or the retired name `freedom`.
- It reads the GGUF header for MTP heads and emits `spec-type = draft-mtp` only
  when they exist.
- It writes no `PARAMS`, because the sampler baseline lives in `server.ini` (see
  *Build And Tune*).

**Then it derives the GPU/CPU split.** It loads the model once with
`--fit-target 2048` on a spare port, with the context, batch, cache types and
flash attention from `server.ini`'s `[*]` section, and records the fitter's
choice in `LOAD` as `n-cpu-moe` with the fit line and date. **Close anything holding VRAM first**,
because the split is sized from what is free at that moment.

- It refuses to probe while the router at `LLM_URL` (default
  `http://localhost:8080`) lists a model as loaded or loading. Free it with
  `llm unload`. A sleeping model has already released its VRAM.
- It stops if `ctx-size` is missing.
- A model that fits entirely gets no `n-cpu-moe`, and it says so.
- `--no-probe` skips the probe and leaves the split for you.

**Then it measures speed against the 20 tok/s floor.** On the same loaded
server it sends one 128-token request with thinking off and reads the
generation speed. A model must reach 20 tok/s to be a candidate for the lineup.

- At or above the floor, the speed is printed and written into the builder's
  comment.
- Below it, the builder is still written and a `WARNING` says the model is too
  slow to be a candidate. Delete the `build-*` file to drop it.
- If the request fails or returns no timing, a `WARNING` says the speed was not
  measured. An unmeasured model is never treated as fast.
- `--no-probe` and `--refit` measure nothing.

The floor exists because a dense 27B model (`qwen38`, 2026-11-19) was scaffolded
and spike-tested before anyone saw that it generates at 6.7 tok/s.

`make build` picks the new model up, because the lineup is discovered from the
`build-*` files.

## Build And Tune

A builder's only model-specific part is its config block:

```bash
MODEL_NAME="qwen"
BASE_MODEL="qwen3.6-35b-a3b-mtp-q4_K_M.gguf"   # filename under $GGUF_DIR
LOAD=(
  'n-cpu-moe = 38'           # expert layers kept in system RAM, pinned (see below)
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
context the values are gemma 26 and qwen 38, each spike-tested with
about 3.0 GB of the card held by other processes. `lite` stays all-GPU by Casey's
choice, so a desktop VRAM spike can crash it. The measurements are in
[`TESTING.md`](TESTING.md), *Pinned split history*.

**`server.ini` reads weights into RAM and uses a 1024-token physical batch.**
`load-mode = none` keeps the CPU expert weights in page cache, and
`ubatch-size = 1024` more than doubled prompt ingest for the models that spill.
`lite` keeps `ubatch-size = 512` in its `LOAD` to stay all-GPU. Numbers:
[`TESTING.md`](TESTING.md), *Pinned split history* and *Preset tuning on build
11022*.

### Re-deriving a split

After a context, batch, cache, build or hardware change, ask the fitter what it
would pin now:

```bash
# Runs in: local terminal, repo root, after make build. Loads each model once on a
# spare port. Writes nothing. Needs the router to hold no model in VRAM.
./add-model --refit gemma qwen
```

- Each model is probed as the router serves it: its section of the built
  `models/models.ini`, with its own `ubatch-size` and `spec-type`, at
  `--fit-target 2048`. Its pinned `n-cpu-moe` or `n-gpu-layers` is left out, so
  the fitter chooses.
- It prints the pinned value, the fitter's value and the fit line, and says
  whether they differ. A line ending `31 layers (24 overflowing), 6375 MiB used,
  2061 MiB free` means `n-cpu-moe = 24`.
- It refuses while a builder or `server.ini` is newer than the last
  `make build`, because the preset would then not say what the builder says.
- **It never edits a builder.** The pinned splits are locked (see `AGENTS.md`),
  so a changed value needs Casey's approval and a spike test before it goes into
  `LOAD`.
- The count moves with free VRAM, which is why it is pinned rather than
  re-fitted per run. Close anything holding VRAM first.

A new value then gets the spike test before it is pinned:

```bash
# Runs in: local terminal, repo root, after make build with the new LOAD value.
# Needs the router to hold no model in VRAM. Writes nothing on PASS.
./eval/spike-test.py gemma
```

- A scratch CUDA process holds VRAM until other GPU use is about 3000 MiB
  (`--target-mib`). A scratch router on a spare port then runs five cycles of
  load, one request with the full prompt stack, and unload (`--cycles`).
- It prints the rows *Pinned split history* records and PASS or FAIL. FAIL
  means a cycle did not answer or the router log has an out-of-memory line, and
  the log is kept for reading.
- It refuses when other GPU use is already at the target, because the result
  would then measure the desktop rather than the margin.

Until 2026-11-11 this was a hand recipe (a backgrounded `llama-server` and a
`grep` loop). It waited forever on a model that fits, and its flags were typed
in rather than read from the preset.

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
`./eval/run-persona.py --models gemma qwen lite --system-mode baseline`
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

The installed build is 12605 (commit `40b7a546d`, since 2026-11-21). The
leaderboard was measured on build 12439 (`8bfd64b5a`) on 2026-11-20, which is
now the rollback target. llama.cpp is built
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
| (no equivalent) | `cache-ram = 0`: the server's host-RAM prompt cache is off, because its 8192 MiB default grew `qwen` toward the 2026-11-04 out-of-memory kill ([`TESTING.md`](TESTING.md), *Host RAM held by the prompt cache*) | `server.ini` |
| (no equivalent) | `reasoning-preserve = true`: the build default, pinned so a build update cannot change how `qwen` sees earlier turns' reasoning | `server.ini` |

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
- Friends-Hermes runs in a QEMU guest, which reaches the host's localhost at
  `10.0.2.2:8080`, so the router stays bound to `127.0.0.1`. It called
  `freedom`, removed on 2026-11-20, and needs another model name. It is also
  blocked by the `Host` check in the next point.
- The unit passes `--cors-origins localhost`. Since llama.cpp 1.0 (build
  12439) that allows only same-origin requests, such as the built-in web UI.
  Any request carrying another `Origin`, including a page on a different
  localhost port and a plain-text POST from another site, gets a 403 before any
  generation runs (checked 2026-11-16). The host apps send no `Origin` header,
  so they are unaffected.
- The same flag rejects any request whose `Host` header is not localhost. A
  QEMU guest calling `10.0.2.2:8080` sends `Host: 10.0.2.2:8080` and gets that
  403, on build 12439 and on 12605 (measured 2026-11-21 on a spare port). The
  2026-11-16 check covered `Origin` only and missed this. A guest reaches the
  router through an SSH reverse tunnel instead (*Use With Pi*). Adding
  `http://10.0.2.2:8080` to `--cors-origins` also lets the guest in and keeps
  every other block, but Casey chose the tunnel on 2026-11-21.

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
- `model` is one of `gemma`, `qwen`, `lite`.
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
names are `gemma`, `qwen` and `lite`, and any non-empty key is
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
for name, label in [("qwen", "agent/tools"), ("gemma", "coding/content/tutor"), ("lite", "fast")]:
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
`gemma` or `lite`, and the context window to `131072`. Cline sends its
own system prompt and its docs offer no way to replace it, so it runs without
this repo's stack. Cline ingests large prompts, so choose by the **Prompt
tok/s** column of `run-speed.py`, not generation speed.

### Notes for both

- Switching models costs a reload, because the router keeps one loaded: about
  2 seconds for `lite`, 12 for `gemma` and 14 for `qwen` (2026-11-20).
- `gemma` and `qwen` keep some expert layers in system RAM, and MoE
  keeps that usable. `lite` fits, and shows what the split costs.
- The eval runners turn thinking off per request. Requests without the flag get
  the chat template's default, and `gemma` was seen thinking by default.

## Use With Pi (in a VM)

[Pi](https://docs.pi.dev/) is a terminal coding agent. `pi/` holds a config that
points it at this router. It is written for Pi running inside a QEMU guest,
because Pi's own docs say it does not sandbox tool calls or ask before running a
shell command, and recommend a container or VM.

| File | Goes to, in the guest | What it sets |
|---|---|---|
| `pi/models.json` | `~/.pi/agent/models.json` | One provider, `local-llm`, at `http://localhost:8080/v1` (the guest end of an SSH reverse tunnel to the host's router), with `qwen` at the served context of 131072. The key is a placeholder: the router accepts any. |
| `pi/settings.json` | `~/.pi/agent/settings.json` | `qwen` as the default model, install telemetry off, and Pi's built-in llama.cpp extension off. |

- **The built-in llama.cpp extension stays off.** Its `/llama` command loads,
  unloads and downloads models on a router. This router is a pinned service that
  other apps depend on, so Pi connects as a plain client.
- **The guest needs a tunnel open while Pi runs.** The guest's direct route to
  the host, `10.0.2.2:8080`, gets a 403 from the router (*Serving other apps*).
  Log in with `ssh -p 22222 -R 8080:localhost:8080` and the guest's own
  `localhost:8080` is the host's router, with a `Host` header it accepts.
- **Only `qwen` is listed,** the pick for agent work and tool calling. `gemma`
  and `lite` wait until `qwen` has passed a smoke test under Pi.
- **`eval/test_pi_config.py` keeps the copy honest.** It fails if a model name
  has no `build-*` file, or if the context size differs from `server.ini`.
- **Smoke tested on 2026-11-21** with Pi 1.6.0 and `qwen` on build 12605: Pi
  listed the model, answered a prompt, and wrote and read back a file through
  its tools. Its only network connection was the router. Details in
  `TESTING.md`, *Pi smoke test with qwen*.

The VM is `~/VMs/pi` (quickemu, Debian 13.7.0 netinst, 4 GB, 2 cores, 20 GB,
`ssh_port` 22222, user `pi`), with snapshots `clean` and `pi-configured`. The
second one has Pi and this config installed. To use it:

```bash
# Host, first terminal: boot the VM.
cd ~/VMs/pi && quickemu --vm debian-13.7.0-netinst.conf --public-dir none

# Host, second terminal: log in with the tunnel to the router.
ssh -p 22222 -R 8080:localhost:8080 pi@localhost

# Guest, in that session.
pi --list-models
pi -p "Reply with the single word: ready"
```

After a change to `pi/`, copy it in again from the repo on the host:

```bash
scp -P 22222 pi/models.json pi/settings.json pi@localhost:.pi/agent/
```

A fresh guest needs `curl` (`su - -c 'apt-get install -y curl'`, the netinst
image has no `sudo`) and then Pi's installer, `curl -fsSL
https://pi.dev/install.sh | sh`, which brings its own Node.

## Evaluation

Runners live under `eval/` and write to `eval/runs/<UTC>/`. Routine testing uses
profiles: `smoke` after a rebuild, `standard` for the routine comparison on the
six deterministic suites (75 minutes with four models), `judged` for the two judge-scored
suites, learn and tutor, and `deep` for a several-hour confidence run.
[`TESTING.md`](TESTING.md) says when to use each.

```bash
./eval/run-profile.py smoke --models gemma qwen lite
./eval/run-profile.py standard --models gemma qwen lite
./eval/run-profile.py judged --models gemma qwen lite
```

Single runners, for targeted sweeps:

```bash
./eval/run-speed.py --models gemma qwen lite
./eval/run-code.py --models gemma qwen lite
./eval/run-content.py --models gemma qwen lite
./eval/run-learn.py --models gemma qwen lite
./eval/run-tutor.py --models gemma qwen lite
./eval/run-json.py --models gemma qwen lite
./eval/run-tools.py --models gemma qwen lite
./eval/run-agent.py --models gemma qwen lite   # multi-step tasks with a sandboxed shell
./eval/run-persona.py --models gemma qwen lite
```

- **`run-persona.py`** is the prompt-stack regression suite. It checks that the
  rules in `prompts/` and `memory/` are obeyed: the identity rule, the
  `memory/user.md` honesty rules about Casey's skill buckets, `Unverified:`
  marking, and output shape. It is the only runner that notices when a prompt
  edit breaks a rule. Scoring is deterministic regex, with no judge.
- **`run-tools.py`** is the single-call tool test. Each task offers a small tool list with
  distractors and scores the right tool, the right arguments, nothing extra, and
  no call where none fits. Five tasks feed the model's call a canned result and
  score what it does next: inventing an address instead of using the one it
  looked up, answering from memory after a search, or claiming success on an
  error. Every tool result is a fixture, with no network.
- **`run-agent.py`** is the agent test. The model gets a small project and one
  `bash` tool, and works in a loop until it says it is done: reading files,
  running tests, editing code. Eight tasks cover a bug one file away from its
  failing test, a crash buried in a log, a signature change across five files, a
  refactor, a module built from a spec, a config migration, an order-dependent
  test, and a two-bug CLI. Hidden tests the model never sees decide the result,
  and a pass also needs the model to end the loop itself. Commands really run, so
  this runner refuses to start without a working `bwrap`. It sends its own short
  system prompt, not the prompt stack. Six more tasks, added 2026-11-19, put the
  model on a simulated order desk: it looks up customers and orders through
  function tools and must follow a written policy the tools do not enforce, such
  as refusing to cancel an order that has shipped. Those pass when the shop's
  final records are exactly right.
- **`run-json.py`** is the structured-output test Jobhunt and SEO-LLM depend on.
  It constrains decode with a JSON schema, buries facts in a long document, and
  scores schema conformance and fact recall. Seven tasks each need one buried
  passage. Three more, added 2026-11-19, need several passages combined:
  `chain_trace_2x3` and `chain_trace_3x3` follow chains of sites that share an
  access code, and `ledger_match_12x24` lists every expense record of one kind
  among near misses. The chain tasks separate the models in a default run. The
  ledger task separates them at `--context-pressure high`. It first checks that every model
  serves at least `--num-ctx` (default 65536, room for its `high` preset's
  largest prompt of 51,307 tokens) and aborts otherwise. SEO-LLM requires 32768,
  and Jobhunt sizes its prompts to fit 32768.
- **`run-code.py`** scores real execution against hidden asserts. Nine tasks are
  classic textbook problems. Six more, added 2026-11-19, are small business
  rules written for this repo (`split_bill`, `paid_minutes`, `irv_winner`,
  `sort_versions`, `meeting_room`, `tally_stock`), so a pass shows the model
  worked the rule out. In their first measurement they spread the models far
  more than the classics do ([`TESTING.md`](TESTING.md)).
- **`run-tutor.py`** asks each model to teach a problem without giving the
  answer. A response whose code passes the hidden tests is a leak and scores 0,
  and the rest are graded by the other models as judges. After the first
  response a scripted student asks for the full answer three more times (a plain
  ask, a deadline, a false claim of permission), and every reply gets the same
  leak check. `Held` in the summary counts attempts that never leaked.
  `Teach /10` and `Leaks` still describe the first response alone.
  `--pressure-turns 0` gives the single-turn run.
- **`run-code.py`, `run-learn.py` and `run-tutor.py`** execute model-written
  Python inside **bubblewrap**: read-only `/usr`, no network, no host PID or IPC
  namespace, no `$HOME`, and writes only to a throwaway CWD. The banner shows the
  mode. Without a working `bwrap` it falls back loudly to a timeout-bounded
  subprocess. `run-agent.py` uses the same sandbox with no fallback. Flags and sandbox details are in [`TESTING.md`](TESTING.md).

Offline unit tests need no server, GPU or model:

```bash
python3 -m unittest discover -s eval -p 'test_*.py'
```

After a run, promote the numbers instead of copying them:

```bash
./eval/promote.py             # newest run per suite -> the table below
./eval/promote.py --check     # exit 1 if the table is stale
```

The JSON row comes from the newest run at normal document length. A
`--context-pressure` run is a different measurement and never becomes the row.
A row is marked as an older run when its run began more than 12 hours before the
newest one, so a pass that runs through midnight UTC still counts as one pass.

## Benchmark Leaderboard

**Measured on llama.cpp build 12439, 2026-11-20** (`standard` profile, then the
agent suite, then learn and tutor at one judge repeat, with pinned offload gemma
26, qwen 38 and nemotron 50, 131072 context, `load-mode = none`,
`ubatch-size = 1024`, prompt caching off). No suite was rerun and no GPU fault
occurred. This is the first pass on the `gemma` and `qwen` files that arrived
that day, so it does not compare with earlier passes for those two models.
[`TESTING.md`](TESTING.md), *Four-model pass on the new gemma and qwen files*,
has the per-task detail.

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

_Generated by `./eval/promote.py` from 9 runs, 20261120T193410Z–20261120T220550Z (`eval/runs/`). Do not hand-edit: re-run the script._

| Suite | Winner | `lite` | `qwen` | `gemma` | `nemotron` |
|---|---|---|---|---|---|
| Speed | `lite` | 133.2, all GPU, MTP | 54.9, GPU + 38 MoE layers on CPU, MTP | 38.6, GPU + 26 MoE layers on CPU | 35.7, GPU + 50 MoE layers on CPU, MTP |
| Coding | `gemma` | 25/45 | 37/45 | 45/45 | 32/45 |
| Content | tie | 8/9 | 9/9 | 9/9 | 7/9 |
| Learning | tie | 7.4, 9/12 | 9.9, 12/12 | 9.8, 12/12 | 9.9, 12/12 |
| Tutor (leak-gated) | `gemma` | 4.1, 8/15 | 5.1, 7/15 | 7.4, 3/15 | 6.6, 4/15 |
| JSON / long-context | tie | 80%, 2.3 | 90%, 7.1 | 90%, 6.3 | 78%, 8.6 |
| Prompt stack | tie | 67% | 90% | 95% | 52% |
| Tool calling | `qwen` | 33/39, 85% | 39/39, 100% | 33/39, 85% | 26/39, 67% |
| Agentic tasks | tie | 25/28, 9.1 | 24/28, 8.1 | 25/28, 9.1 | 23/28, 10.3 |

Winner is `tie` where the runner flagged the margin as within its close-result threshold — those rows should break on speed, not the headline metric. Per-suite run directories:

- Speed: `eval/runs/20261120T193410Z/speed/summary.md` — 133 tok/s (all GPU, MTP, thinking OFF)
- Coding: `eval/runs/20261120T193546Z/code/summary.md` — 45/45 (100%) @ 40 tok/s
- Content: `eval/runs/20261120T195826Z/content/summary.md` — 9/9 clean (100%) @ 49 tok/s
- Learning: `eval/runs/20261120T214837Z/learn/summary.md` — teach 9.9/10 (code 12/12, explanation 9.9/10)
- Tutor (leak-gated): `eval/runs/20261120T220550Z/tutor/summary.md` — teach 7.4/10 (leaks 3/15, explanation 9.2/10, non-leak explanation 9.3/10)
- JSON / long-context: `eval/runs/20261120T201136Z/json/summary.md` — score 90% (schema 100%, facts 92%)
- Prompt stack: `eval/runs/20261120T200351Z/persona/summary.md` — 20/21 clean (95%)
- Tool calling: `eval/runs/20261120T203820Z/tools/summary.md` — 39/39 (100%)
- Agentic tasks: `eval/runs/20261120T205035Z/agent/summary.md` — 25/28 (89%) in 9.1 steps

<!-- BENCH:END -->

### Current picks

Casey's picks on 2026-11-20, from the four-model pass above on build 12439 at
131072 context. `gemma` and `qwen` are new files with one pass each, so no
earlier record backs these picks except where the basis says so.

| Use | Pick | Basis |
|---|---|---|
| Coding | `gemma` | 45/45, with 18/18 on the fresh tasks, vs `qwen` 37, `nemotron` 32 and `lite` 25. 15/16 on the shell agent tasks. |
| Content / SEO / copy | `gemma` | 9/9 clean, tied with `qwen` in this pass. The old `gemma` file held 9/9 in every pass since 2026-07-28, so the pick stays. |
| Auto agent and tool calling | `qwen` | 39/39 on tool calling vs 33 for `gemma` and `lite` and 26 for `nemotron`. 16/16 on the shell agent tasks, at 54.9 tok/s. Its weak spot is policy: it broke the rule in `desk_half_allowed` on both attempts. |
| Personal teacher bot | `gemma` | 3/15 leaked first replies vs 4 (`nemotron`), 7 (`qwen`) and 8 (`lite`), teach 7.4/10, and the only model that held through the pressure turns at all (4/15). |
| Learning explanations | `qwen` or `gemma` (tie) | 9.9 and 9.8/10, each with 12/12 on the code gate. `nemotron` also scored 9.9 before it was removed. The rubric does not separate them. |
| Structured JSON | `gemma` or `qwen` (tie) | 27/30 each, and every reply from every model was schema-valid. `lite` scored 24/30 at 2.3 s a call against 6.3 and 7.1, so it is the pick when the task is a single passage and speed matters. |
| Prompt-stack fidelity | `gemma` or `qwen` (tie) | 20/21 and 19/21. `qwen` held 86 to 90% on its old file across three passes. `gemma`'s old file scored 67%, so its 95% is new. |
| Fast local general use | `lite` | 133.2 tok/s, the only model fully on GPU. 2.4× `qwen` (54.9) and 3.5× `gemma` (38.6). |
| Removed 2026-11-20 | `nemotron` | No role earned. Last on tool calling, content and the prompt stack, 6/18 on the fresh coding tasks, slowest at 35.7 tok/s, and it timed out on one JSON task in both of its passes. Its one strength is tutoring, where it is second. |

`gemma` takes coding, content and teaching. `qwen` takes agent work and tool
calling. `lite` takes everything speed-shaped. `nemotron` was removed from the
lineup the same day, so its figures above are a record only. Tool calling is still where `gemma` is weakest: it lost
two whole tasks there.

Learn and tutor ran at one judge repeat with `gemma`, `qwen` and `lite` as
judges, so compare them only within this pass.

Two caveats:

- Samples are small (n = 9 to 45 per model), and this is one pass, so a few points either way is noise.
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
12605 (`40b7a546d`), compiled against CUDA 13.5, the installed toolkit. It has
not been benchmarked yet: the leaderboard above was measured on build 12439,
which was compiled against CUDA 13.4.

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

  `check-agents.py` reports on the file without changing it: the size of the
  universal body and of each section, and which rules are mentioned in more
  than one section. Run it before and after an edit to `AGENTS.md`, and diff
  `--rules` to confirm no rule was lost.

  ```bash
  # Runs in: local terminal, from this repo.
  ./check-agents.py                    # sizes and repeated rules
  ./check-agents.py --rules            # every bold rule lead-in, one per line
  ./check-agents.py --max-words 8641   # exit 1 if the universal body has grown past this
  ```
