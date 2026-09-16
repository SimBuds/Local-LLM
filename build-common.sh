# ============================================================================
# Shared assembly for build-* scripts.
#
# Source this (do not execute) after defining:
#   MODEL_NAME   router model name (the preset section, and what clients request)
#   BASE_MODEL   GGUF filename under $GGUF_DIR
#   LOAD         array of "<llama-server key> = <value>" per-model load params
#   PARAMS       optional. The shared sampler baseline below is used when a
#                builder does not define one, which is the normal case.
#
# Writes models/<name>/{system.txt,prompt.txt,preset.ini}. The Makefile joins
# every preset.ini with server.ini into models/models.ini for `make serve`.
# ============================================================================

AI_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GGUF_DIR="${GGUF_DIR:-$HOME/models/gguf}"
MODEL_FILE="$GGUF_DIR/$BASE_MODEL"

# Preflight: fail loudly if the GGUF isn't there. Without this the script writes a
# preset pointing at nothing, and the failure only surfaces when the router tries
# to load the model — leaving artifacts that look like a successful build.
if [ ! -f "$MODEL_FILE" ]; then
  echo "ERROR: GGUF for '$MODEL_NAME' not found: $MODEL_FILE" >&2
  echo "  Stage it there, set GGUF_DIR, or edit BASE_MODEL in $(basename "$0")" >&2
  echo "  Present in $GGUF_DIR:" >&2
  find "$GGUF_DIR" -maxdepth 1 -name '*.gguf' -printf '    %f\n' >&2
  exit 1
fi

# Preflight: the personal context files are gitignored, so a fresh clone has the
# `*.example.md` templates and nothing else. Assembling anyway would build a model
# whose User Profile section is simply absent — the persona suite would then score
# rules against context the model never received, which reads as model failure.
if ! compgen -G "$AI_ROOT/memory/*.md" >/dev/null \
   || [ -z "$(find "$AI_ROOT/memory" -name '*.md' ! -name '*.example.md' -print -quit)" ]; then
  echo "ERROR: no personal context in memory/ — only templates." >&2
  echo "  Seed it:  cp memory/user.example.md memory/user.md" >&2
  echo "            cp memory/learning-profile.example.md memory/learning-profile.md" >&2
  echo "  Then edit both with your own details (they stay gitignored)." >&2
  exit 1
fi

# The shared sampler baseline, deliberately the SAME for every model. Only
# run-json.py sends sampler options; every other suite inherits whatever the
# preset sets, so a value that differs between builders makes the leaderboard
# measure model x sampler instead of model. That mistake invalidated the
# 2026-06-14 coding, learning, and tutor tables, which compared `gemma` at
# temperature 0.75 / presence_penalty 0.2 against `qwen` at 0.2 / 0.0.
#
# It lives here rather than once per builder so the values cannot drift: before
# 2026-09-15 this array was copy-pasted identically into all three build-*
# scripts and staying in sync was manual discipline.
#
# A model that genuinely needs its own decoding for daily use defines PARAMS in
# its own builder, which wins over this default. That is a separate preset, not
# a tweak to the shared baseline: keep it out of head-to-head benchmark runs.
#
# Context: 262144 - 131072 - 65536 - 32768 - 16384 - 8192 - 4096
if [ -z "${PARAMS+x}" ]; then
  PARAMS=(
    'ctx-size = 65536'         # 32k: sweet spot for multi-file local tasks; matches run-json.py's pin
    'temp = 0.2'               # Low temperature forces strict compliance with code syntax and tool tags
    'top-p = 0.95'
    'top-k = 40'
    'min-p = 0.05'             # Safeguards structural format without restricting code vocabulary
    'presence-penalty = 0.0'   # MUST BE ZERO. Coding requires reusing exact variable names.
    'repeat-penalty = 1.05'    # Prevents infinite code loops without breaking boilerplate code
  )
fi

OUT_DIR="$AI_ROOT/models/$MODEL_NAME"
SYSTEM_FILE="$OUT_DIR/system.txt"
PROMPT_FILE="$OUT_DIR/prompt.txt"
PRESET_FILE="$OUT_DIR/preset.ini"
mkdir -p "$OUT_DIR"

{
  echo "=== SYSTEM METADATA ==="
  echo "Model: $MODEL_NAME"
  echo "Base:  $BASE_MODEL"
  echo "Built: $(date -u +'%Y-%m-%dT%H:%M:%SZ')"
  echo

  # Inject reference first, user context second, behavior rules last.
  # `*.example.md` are tracked templates for people cloning the repo; the real
  # files sit beside them untracked. Injecting both would hand the model two
  # conflicting User Profile sections, so the templates are skipped here.
  for dir in "$AI_ROOT/memory" "$AI_ROOT/prompts"; do
    find "$dir" -type f -name '*.md' ! -name '*.example.md' -size -100k -print0 2>/dev/null \
      | sort -z \
      | while IFS= read -r -d '' f; do
          rel="${f#"$AI_ROOT/"}"
          echo "--- START FILE: $rel ---"
          awk 1 "$f"   # like cat, but guarantees a trailing newline so the
                       # END marker never glues onto a file's last line
          echo "--- END FILE: $rel ---"
          echo
        done
  done
} > "$SYSTEM_FILE"

# llama-server has no baked-in system prompt, so clients send prompt.txt as the
# system message on every request. Strip per-file provenance markers from what the
# model receives — it recites them ("Constraints from prompts/..."); they exist
# only for human debugging in system.txt. Markdown headers in each file preserve
# section structure.
grep -vE '^--- (START|END) FILE: .* ---$' "$SYSTEM_FILE" > "$PROMPT_FILE"

{
  echo "[$MODEL_NAME]"
  echo "model = $MODEL_FILE"
  for p in "${PARAMS[@]}"; do echo "$p"; done
  for l in "${LOAD[@]}"; do echo "$l"; done
  echo
} > "$PRESET_FILE"

echo
echo "✓ Built $MODEL_NAME from $BASE_MODEL"
echo "  System prompt: $(wc -l < "$PROMPT_FILE") lines, $(wc -w < "$PROMPT_FILE") words"
echo "  Preset:        $PRESET_FILE"
