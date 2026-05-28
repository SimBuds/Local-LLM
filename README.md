# AI Context Stack

Local Ollama agent structure for customizing base models with layered prompts,
personal memory, and project knowledge.

The goal is to keep the model customization transparent: edit plain Markdown
source files, run one build script, and let Ollama create a local model from the
generated `Modelfile`.

## Models

Three project-agnostic siblings, all built from the same neutral prompt stack
(`prompts/` + `memory/user.md`). Project-specific overlays (e.g. the SEO rules
in `~/Apps/SEO-LLM/prompts/seo/`) are injected at request time by the consuming
app, not baked into the model.

| Model            | Base            | Best for                                              |
|------------------|-----------------|-------------------------------------------------------|
| `qwen-custom`    | `qwen3.5:9b`    | Default daily driver: concise technical assistant, code/debug/design. Supports runtime thinking mode (`qct`). |
| `granite-custom` | `granite4.1:8b` | Instruction-following + structured output (JSON, schema.org, FAQ). |
| `llama-custom`   | `llama3.1:8b`   | General long-form prose drafting.                     |

Each model has its own builder script (`build-qwen`, `build-granite`,
`build-llama`). The scripts are intentionally mirrored: only the config block
at the top differs (`MODEL_NAME`, `BASE_MODEL`, `ROLE`, `EXTRAS`, `PARAMS`); the
assembly logic below is byte-identical. To add a new model, copy one of the
existing scripts and edit the config block. Output goes to
`models/<name>/{system.txt,Modelfile}` and `ollama create` runs at the end.
Granite/llama use higher-temperature sampler settings tuned for prose;
`qwen-custom`'s defaults are tuned for terse technical assistance (see
[Model Notes](#model-notes)).

## Structure
```
ai/
├── prompts/              # Source: durable behavior controls
│   ├── system.md         # Core directives and agent rules
│   ├── personality.md    # Voice and interaction style
│   ├── formatting.md     # Output conventions
│   ├── safety.md         # Operational safety rules
│   └── roles/            # Per-model role overlays (selected by $ROLE)
│       ├── coding.md     # qwen-custom, granite-custom
│       └── prose.md      # llama-custom
├── memory/               # Source: durable user profile and preferences
│   └── user.md
├── knowledge/            # Source: reusable reference context
│   └── linux/arch.md
├── models/<name>/        # Generated: system.txt and Modelfile
├── sessions/             # Future/generated: transcripts or run logs
├── build-qwen            # Builder: qwen-custom
├── build-granite         # Builder: granite-custom
└── build-llama           # Builder: llama-custom
```

## Layer Responsibilities

- `prompts/`: controls how the agent behaves. Keep these short and durable.
  `prompts/roles/` holds per-model overlays (`coding`, `prose`) selected by each
  builder's `ROLE`; the other files in `prompts/` apply to every model.
- `memory/`: controls what the agent knows about Casey. Store stable
  preferences and profile facts here, not one-off conversation details.
- `knowledge/`: reusable domain context loaded into every model. Add a file
  here only when the same technical reference would otherwise be repeated
  across conversations.
- `build-*`: one script per model. Per-model config (base, sampler params,
  any `TEMPLATE`/`RENDERER`/`PARSER` extras) lives in the top block of each
  script; the assembly logic below the divider is identical across scripts
  and should be kept in lock-step.
- `models/`: generated build output. Do not hand-edit unless debugging a build.
- `sessions/`: reserved for generated transcripts or runner logs.

## Build
```bash
./build-qwen
./build-granite
./build-llama
```

Each script assembles the prompt stack, writes
`models/<name>/{system.txt,Modelfile}`, and runs `ollama create <name>`.

Adding a new model: copy one of the scripts (qwen if you need
`TEMPLATE`/`RENDERER`/`PARSER`; granite or llama otherwise) and edit only the
config block at the top.

## Prompt Assembly Order
1. `prompts/system.md`
2. `prompts/personality.md`
3. `prompts/formatting.md`
4. `prompts/roles/$ROLE.md` (per-model overlay: `coding` for qwen/granite, `prose` for llama)
5. `prompts/safety.md`
6. `memory/user.md`
7. `knowledge/**/*.md` (sorted; each wrapped in `--- START/END FILE: <path> ---` markers; files >100k are skipped)

Section markers (`=== HEADING ===`) wrap each block so the model can navigate context.

## First-Time Setup
```bash
./build-qwen
ollama run --think false qwen-custom
```

Recommended shell helpers (add to `~/.bashrc`) so `--think false` is the default
and thinking mode is an explicit opt-in:

```bash
qc()  { ollama run --think false qwen-custom "$@"; }
qct() { ollama run qwen-custom "$@"; }   # thinking mode when needed
```

### Ollama systemd settings

The gateway is tuned to a specific server config. Mirror these
(`sudo systemctl edit ollama.service`):

```ini
[Service]
Environment="OLLAMA_KV_CACHE_TYPE=q5_0"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_KEEP_ALIVE=10m"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

## Editing Workflow

1. Edit the relevant source file in `prompts/`, `memory/`, `knowledge/`, or the config block of the relevant `build-*` script.
2. Run the matching builder (e.g. `./build-qwen`).
3. Start a fresh model session with `ollama run --think false <name>`.
4. If behavior is wrong, adjust the smallest responsible layer and rebuild.

Use this order when deciding where a change belongs:

- Behavior rule for all models? Put it in `prompts/`.
- Behavior rule for one role only? Put it in `prompts/roles/<role>.md`.
- Stable fact about Casey? Put it in `memory/user.md`.
- Reusable technical reference? Put it in `knowledge/`.
- Temporary context? Keep it out of the build.

## Maintenance
- **Memory**: update when a preference becomes permanent. One-off facts don't belong here.
- **Knowledge**: add a file when you've explained the same technical context twice.
- **Prompts**: keep terse. Every token of system prompt is a token spent every turn.

## Model Notes

This repo does not fine-tune weights; it customizes each base model through
the system prompt, reference context, and Ollama sampler parameters in the
generated `Modelfile`. Sampler params live in the config block at the top of
each `build-*` script — edit there.

`qwen-custom` defaults are tuned for a concise technical assistant
(`temperature 0.6`, `top_p 0.95`, `top_k 20`, `min_p 0`, `repeat_last_n 256`,
`presence_penalty 1.5`). `granite-custom` and `llama-custom` run hotter for
prose; see the `PARAMS` block in each builder for exact values.

`num_ctx` is set per-model in each builder's `PARAMS` (`16384` across all
three today). The Ollama server's `OLLAMA_CONTEXT_LENGTH` still applies as a
server-side ceiling — see the systemd snippet above for the current value.

## Next Step: Tools
Tool calling (shell, files, web, git, docker, notes) lives in a future `tools/` folder
with a runner that wraps the Ollama native API. Out of scope for the baseline.

## Thinking Mode

Thinking mode is Qwen-only. `granite-custom` and `llama-custom` have no thinking
mode — run them with plain `ollama run <name>` (passing `--think false` errors).

Ollama exposes Qwen thinking mode as a runtime option, not a Modelfile
parameter. Use this for normal interactive sessions:

```bash
ollama run --think false qwen-custom
```

If you want reasoning enabled but hidden in the terminal, use:

```bash
ollama run --hidethinking qwen-custom
```

### When to use which

Thinking mode is calibrated for problem-solving, not chitchat. Use `qc` for
greetings, acknowledgments, and short factual questions. Use `qct` when you
want the model to deliberate — code architecture, debugging, design tradeoffs.
Asking `qct` to handle a greeting will produce long, looping thinking traces;
that is the model's design, not a bug to fix in the prompt.
