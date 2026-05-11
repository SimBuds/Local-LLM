# AI Context Stack

Local Ollama agent customized for Casey.

## Layout
```
ai/
├── prompts/      # Behavior controls
│   ├── system.md         # core directives
│   ├── personality.md    # voice
│   ├── formatting.md     # output shape
│   └── safety.md         # operational safety
├── memory/
│   └── user.md           # User profile
├── knowledge/    # Reference docs
│   ├── professional.md
│   └── linux/
│       └── arch.md
├── models/<name>/        # Build output (generated)
├── sessions/             # Transcripts (generated)
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
- `TEMPERATURE` default `0.6` (matches Qwen's official thinking-mode sampling recommendation)

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
1. `./build.sh`
2. `ollama run qwen-custom`

## Next step (deferred)
Tool calling (shell, files, web, git, docker, notes) lives in a future `tools/` folder
with a runner that wraps the Ollama native API. Out of scope for the baseline.
