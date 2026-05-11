# AI Context Stack

Local Ollama agent customized for Casey. Source of truth for the `qwen-custom` system prompt.

## Layout
```
ai/
├── prompts/      # behavior — order-sensitive at build time
│   ├── system.md         # core directives
│   ├── personality.md    # voice
│   ├── formatting.md     # output shape
│   └── safety.md         # operational safety
├── memory/
│   └── user.md           # durable user profile (edit when something becomes permanent)
├── knowledge/    # reference docs — all *.md under this tree get concatenated
│   ├── professional.md
│   └── linux/
│       └── arch.md
├── models/<name>/        # build output (generated)
├── sessions/             # transcripts (generated)
└── build.sh
```

## Build
```bash
./build.sh
```

Env overrides:
- `AI_ROOT`     default `~/ai`
- `MODEL_NAME`  default `qwen-custom`
- `BASE_MODEL`  default `qwen3.5:9b`
- `TEMPERATURE` default `0.6`
- Ollama custom setttings:
- sudo systemctl edit ollama.service
- [Service]
- Environment="OLLAMA_KV_CACHE_TYPE=q5_0"
- Environment="OLLAMA_FLASH_ATTENTION=1"
- Environment="OLLAMA_NUM_PARALLEL=1"
- Environment="OLLAMA_CONTEXT_LENGTH=16384"
- Environment="OLLAMA_KEEP_ALIVE=-1"
- Environment="OLLAMA_MAX_LOADED_MODELS=1"
- sudo systemctl daemon-reload && sudo systemctl restart ollama

## Concatenation order
1. `prompts/system.md`
2. `prompts/personality.md`
3. `prompts/formatting.md`
4. `prompts/safety.md`
5. `memory/user.md`
6. `knowledge/**/*.md` (sorted; each preceded by a `--- path ---` marker)

Section markers (`=== HEADING ===`) wrap each block so the model can navigate context.

## Maintenance
- **Memory**: update when a preference becomes permanent. One-off facts don't belong here.
- **Knowledge**: add a file when you've explained the same technical context twice.
- **Prompts**: keep terse. Every token of system prompt is a token spent every turn.

## First-time setup
1. Open `memory/user.md` and replace every `<FILL: ...>` placeholder.
2. `./build.sh`
3. `ollama run qwen-custom`

## Next step (deferred)
Tool calling (shell, files, web, git, docker, notes) lives in a future `tools/` folder
with a runner that wraps the Ollama native API. Out of scope for the baseline.
