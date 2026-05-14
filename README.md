# AI Context Stack

Local Ollama agent structure for customizing `qwen3.5:9b` with layered prompts,
personal memory, and project knowledge.

The goal is to keep the model customization transparent: edit plain Markdown
source files, run one build script, and let Ollama create a local model from the
generated `Modelfile`.

## Structure
```
ai/
├── prompts/              # Source: durable behavior controls
│   ├── system.md         # Core directives and agent rules
│   ├── personality.md    # Voice and interaction style
│   ├── formatting.md     # Output conventions
│   └── safety.md         # Operational safety rules
├── memory/               # Source: durable user profile and preferences
│   └── user.md
├── knowledge/            # Source: reusable reference context
│   ├── professional.md
│   ├── projects/
│   │   └── jobhunt.md
│   └── linux/
│       └── arch.md
├── models/<name>/        # Generated: system.txt and Modelfile
├── sessions/             # Future/generated: transcripts or run logs
└── build.sh
```

## Layer Responsibilities

- `prompts/`: controls how the agent behaves. Keep these short and durable.
- `memory/`: controls what the agent knows about Casey. Store stable preferences
  and profile facts here, not one-off conversation details.
- `knowledge/`: controls reusable domain context. Add files here when the same
  technical reference would otherwise be repeated across conversations.
- `models/`: generated build output. Do not hand-edit unless debugging a build.
- `sessions/`: reserved for generated transcripts or runner logs.

## Build
```bash
./build.sh
```

Env overrides:
- `AI_ROOT`     default `~/ai`
- `MODEL_NAME`  default `qwen-custom`
- `BASE_MODEL`  default `qwen3.5:9b`

`build.sh` creates:

- `models/<MODEL_NAME>/system.txt`
- `models/<MODEL_NAME>/Modelfile`
- an Ollama model named by `MODEL_NAME`

## Prompt Assembly Order
1. `prompts/system.md`
2. `prompts/personality.md`
3. `prompts/formatting.md`
4. `prompts/safety.md`
5. `memory/user.md`
6. `knowledge/**/*.md` (sorted; each preceded by a `--- path ---` marker)

Section markers (`=== HEADING ===`) wrap each block so the model can navigate context.

## First-Time Setup
```bash
./build.sh
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
Environment="OLLAMA_CONTEXT_LENGTH=16384"
Environment="OLLAMA_KEEP_ALIVE=10m"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

## Editing Workflow

1. Edit the relevant source file in `prompts/`, `memory/`, or `knowledge/`.
2. Run `./build.sh`.
3. Start a fresh model session with `ollama run --think false qwen-custom`.
4. If behavior is wrong, adjust the smallest responsible layer and rebuild.

Use this order when deciding where a change belongs:

- Behavior rule? Put it in `prompts/`.
- Stable fact about Casey? Put it in `memory/user.md`.
- Reusable technical reference? Put it in `knowledge/`.
- Temporary context? Keep it out of the build.

## Maintenance
- **Memory**: update when a preference becomes permanent. One-off facts don't belong here.
- **Knowledge**: add a file when you've explained the same technical context twice.
- **Prompts**: keep terse. Every token of system prompt is a token spent every turn.

## Model Notes

The baseline is `qwen3.5:9b`, configured through Ollama parameters in the
generated `Modelfile`. This repo does not fine-tune weights; it customizes the
model through the system prompt and reference context.

The current defaults are tuned for a concise technical assistant:

- `temperature 0.6`
- `top_p 0.95`
- `top_k 20`
- `repeat_penalty 1.15`
- `repeat_last_n 256`
- `num_predict 2048`
- `num_ctx 16384`

## Next Step: Tools
Tool calling (shell, files, web, git, docker, notes) lives in a future `tools/` folder
with a runner that wraps the Ollama native API. Out of scope for the baseline.

## Thinking Mode

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
