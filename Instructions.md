# Instructions.md

The complete flow of this repo, from a fresh clone to a model answering another
app, to a benchmark result landing in the README. This is a recap. The detail
lives in `README.md` (how to run), `PLAN.md` (why), `TESTING.md` (evals), and
`AGENTS.md` (agent rules).

---

## 1. The big picture

```text
memory/*.md + prompts/*.md          build-<name> (sources build-common.sh)
        │                                   │
        └──────────────► models/<name>/ ◄───┘
                         ├── system.txt   (debug copy, with file markers)
                         ├── prompt.txt   (what clients send as system message)
                         └── preset.ini   (this model's llama-server section)

server.ini + every preset.ini ──make build──► models/models.ini
models/models.ini ──make deploy──► ~/.config/llama.cpp/models.ini
                                          │
                  llama-server.service (port 8080, --models-max 1)
                                          │
        Jobhunt, SEO-LLM, Continue, Cline, scripts/llm, eval/ runners
```

The repo builds two things: a **prompt stack** and a **router preset**. The
weights, the llama.cpp binary, the deployed preset, and the service unit all
live outside the repo.

---

## 2. One-time setup

1. **Build llama.cpp** from `~/src/llama.cpp` with CUDA and link
   `llama-server` into `~/.local/bin` (recipe in README, *llama-server*).
2. **Stage the GGUFs** in `~/models/gguf` (override with `GGUF_DIR`) and check
   them with the `sha256sum -c` block in README, *Model Files*.
3. **Seed personal context.** The real profile is gitignored, so copy the
   templates and edit them:
   ```bash
   cp memory/user.example.md memory/user.md
   cp memory/learning-profile.example.md memory/learning-profile.md
   ```
   The builders abort if only templates exist.
4. **Install the service** (as your user, no sudo):
   ```bash
   make deploy
   cp systemd/llama-server.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now llama-server
   curl -s localhost:8080/health    # expect {"status":"ok"}
   ```
5. **Install the terminal helper:** `install -m 755 scripts/llm ~/.local/bin/llm`
6. **Optional:** `make hook` installs a pre-commit hook that runs `make check`
   whenever `prompts/`, `memory/`, or a `build-*` file is staged.

---

## 3. Build: how a model is assembled

`make build` discovers the lineup from the `build-*` files (currently `gemma`,
`lite`, `qwen`). For each model whose stamp `models/<name>/.built` is older than
its builder, `build-common.sh`, or any file in the stack, it runs `./build-<name>`.

Each builder declares only three things and then sources `build-common.sh`:

| Variable | Meaning |
|---|---|
| `MODEL_NAME` | Router name, the `model` field clients send |
| `BASE_MODEL` | GGUF filename under `$GGUF_DIR` |
| `LOAD` | Pinned per-model load keys (`n-cpu-moe`, `spec-type = draft-mtp`) |

`build-common.sh` then:

1. **Preflights.** Aborts if the GGUF is missing or `memory/` has only templates.
2. **Applies the shared `PARAMS`** (context 65536, temp 0.2, top-p 0.95,
   top-k 40, min-p 0.05, presence 0.0, repeat 1.05) unless the builder defines
   its own. Identical samplers are what keep benchmarks comparable.
3. **Assembles `system.txt`** from `memory/` then `prompts/`, files sorted,
   skipping `*.example.md` and files over 100k, each wrapped in
   `--- START/END FILE ---` markers.
4. **Writes `prompt.txt`** with the markers stripped (models recite them).
5. **Writes `preset.ini`**: `[name]`, `model = <path>`, then `PARAMS`, then `LOAD`.

Finally `make build` concatenates `server.ini` and every `preset.ini` into
`models/models.ini`. `server.ini` holds the shared router settings: one slot
(`parallel = 1`), flash attention, q4_0 KV cache, `fit = off`, all layers on GPU
unless overridden, no vision projector, and sleep after 600 idle seconds.

Editing one builder rebuilds only that model. Editing anything in `prompts/` or
`memory/` rebuilds every model. `make clean` drops the stamps to force a full
rebuild.

---

## 4. Serve: how requests are answered

- **Live server:** the `llama-server` user service on port 8080 reads the
  **deployed** copy in `~/.config/llama.cpp/models.ini`, never the repo's copy.
  A half-finished edit therefore never reaches other apps.
- **Router mode:** one process starts a child server per model on demand and
  routes by the `model` field. `--models-max 1` keeps one model resident with the
  whole card. Asking for another model unloads the current one.
- **Idle sleep:** after 10 minutes a model sleeps and frees its VRAM. The next
  request wakes it.
- **No baked-in prompt:** the client must send `models/<name>/prompt.txt` as the
  system message, or it talks to the bare base model.

Shipping a preset change:

```bash
make build && make deploy && systemctl --user restart llama-server
```

Trying a change without touching the service:

```bash
make serve PORT=8081
LLM_URL=http://localhost:8081 ./eval/run-persona.py --models qwen
```

A running router never reloads its preset on its own. Restart it after a deploy.
Restarting mid-run invalidates any eval in flight.

---

## 5. Clients: who calls the router

**The contract** (locked, other repos depend on it):

- `POST http://localhost:8080/v1/chat/completions`, OpenAI format, any
  non-empty API key.
- `model` is `gemma`, `qwen`, or `lite`.
- Context is fixed at 65536. An oversized prompt returns HTTP 400
  `exceed_context_size_error`, never a silent truncation.
- Structured output uses `response_format.json_schema.schema`.
- Thinking off: `chat_template_kwargs: {"enable_thinking": false}`.

**The clients:**

| Client | How it connects |
|---|---|
| Jobhunt, SEO-LLM | Direct HTTP to the contract above |
| Continue | `apiBase: http://localhost:8080/v1`, with `prompt.txt` embedded as `baseSystemMessage` (generator script in README) |
| Cline | OpenAI Compatible provider at the same URL. Sends its own system prompt, so it runs without this stack. |
| `llm` | `llm status`, `llm load`, `llm unload`, `llm chat MODEL "..."`, `llm logs`. Standalone, no repo imports. |
| `eval/` runners | Always through `eval/_gateway.py` |

---

## 6. Evaluate: how models are measured

All eval traffic goes through `eval/_gateway.py`. It reads `LLM_URL`, sends
`prompt.txt` as the system message, keeps `cache_prompt` off, handles model
load and unload and `/props` reads, preflights the router, and aborts on a
streak of dead-server failures in the runners that support it.

| Runner | Measures | Scoring |
|---|---|---|
| `run-speed.py` | Generation and prompt tok/s | Timings |
| `run-code.py` | Code correctness | Executes generated Python in bubblewrap |
| `run-content.py` | Content and SEO instruction following | Deterministic checks |
| `run-json.py` | Schema conformance and long-context fact recall | Deterministic, checks served context first |
| `run-learn.py` | Teaching quality | Code gate plus leave-one-out judge panel (`_judge.py`) |
| `run-tutor.py` | Socratic teaching without leaking the answer | Leak gate plus judge panel |
| `run-persona.py` | Whether the prompt stack's rules hold | Deterministic regex, no judge |

`run-persona.py` is the only suite that tests the stack itself. Run it with
`--system-mode baseline` to measure what each rule buys over the bare model.

Routine runs use profiles:

```bash
./eval/run-profile.py smoke    --models gemma qwen lite   # after a rebuild, minutes
./eval/run-profile.py standard --models gemma qwen lite   # routine comparison, about 2 hours
./eval/run-profile.py deep     --models gemma qwen lite   # several hours
```

Each run writes `eval/runs/<UTC>/<suite>/summary.md`. A run that only checks
that code works passes `--out-root` to a scratch directory, because the newest
run in `eval/runs/` becomes the leaderboard.

Offline unit tests (no server, GPU, or model):

```bash
python3 -m unittest discover -s eval -p 'test_*.py'
```

---

## 7. Publish: how results reach the README

```bash
./eval/promote.py           # newest run per suite rewrites the BENCH block in README.md
./eval/promote.py --check   # exit 1 if that block is stale
```

The leaderboard between `<!-- BENCH:START -->` and `<!-- BENCH:END -->` is
generated. Never hand-edit it. Current picks (2026-09-15 `standard` pass):
`gemma` for coding, content, and tutoring, `lite` for speed, Cline, and JSON,
`qwen` for learning explanations and prompt-stack fidelity.

---

## 8. Everyday loops

**Change a prompt rule or profile**

1. Edit `prompts/*.md` or `memory/*.md`.
2. `make check` (rebuild everything, then run the persona suite).
3. If it holds, `make deploy && systemctl --user restart llama-server`.

**Add a model**

1. `./add-model Org/Repo-GGUF` prints the download and checksum commands. Run them.
2. Close anything holding VRAM, then `./add-model <file>.gguf`. It picks a
   router name (refusing contract names), reads MTP support from the GGUF
   header, and probes once with `--fit-target 2048` to pin `n-cpu-moe`.
3. `make build`. The new builder is discovered automatically.
4. Benchmark with `make serve PORT=8081` and `LLM_URL=http://localhost:8081`.

**Change a pinned split or context size**

Re-derive the split with the fit probe (README, *Build And Tune*) at a 2 GB
margin, record the fit line in the builder comment, and spike-test it with about
3 GB of the card held elsewhere. Context size is part of the app contract and
needs a coordinated change in Jobhunt and SEO-LLM.

**Port the agent rules to another repo**

```bash
./apply-universal.py --check AGENTS.md ../other-repo/AGENTS.md
./apply-universal.py AGENTS.md ../other-repo/AGENTS.md
```

---

## 9. Where things live

| Piece | Location | Owner |
|---|---|---|
| Prompt stack sources | `prompts/`, `memory/` | repo (memory gitignored) |
| Builders | `build-*`, `build-common.sh`, `add-model` | repo |
| Generated outputs | `models/` | repo, gitignored |
| Shared router settings | `server.ini` | repo |
| Service unit source | `systemd/llama-server.service` | repo |
| Eval runners and results | `eval/`, `eval/runs/` (gitignored) | repo |
| llama.cpp build | `~/src/llama.cpp`, `~/.local/bin/llama-server` | Casey |
| Weights | `~/models/gguf/` | Casey |
| Deployed preset | `~/.config/llama.cpp/models.ini` | Casey |
| Installed service | `~/.config/systemd/user/llama-server.service` | Casey |
