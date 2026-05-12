#!/usr/bin/env bash
set -euo pipefail

# ---- Configurable ----------------------------------------------------------
AI_ROOT="${AI_ROOT:-$HOME/ai}"
MODEL_NAME="${MODEL_NAME:-qwen-custom}"
BASE_MODEL="${BASE_MODEL:-qwen3.5:9b}"
TEMPERATURE="${TEMPERATURE:-0.6}"
# ---------------------------------------------------------------------------

OUT_DIR="$AI_ROOT/models/$MODEL_NAME"
SYSTEM_FILE="$OUT_DIR/system.txt"
MODELFILE="$OUT_DIR/Modelfile"

mkdir -p "$OUT_DIR"

# Build the system prompt with section markers so the model can navigate it.
{
  echo "=== CORE DIRECTIVES ==="
  cat "$AI_ROOT/prompts/system.md"
  echo
  echo "=== VOICE ==="
  cat "$AI_ROOT/prompts/personality.md"
  echo
  echo "=== OUTPUT SHAPE ==="
  cat "$AI_ROOT/prompts/formatting.md"
  echo
  echo "=== OPERATIONAL SAFETY ==="
  cat "$AI_ROOT/prompts/safety.md"
  echo
  echo "=== USER PROFILE ==="
  cat "$AI_ROOT/memory/user.md"
  echo

  if [ -d "$AI_ROOT/knowledge" ]; then
    echo "=== REFERENCE KNOWLEDGE ==="
    # Recursive, sorted, nul-safe. Each file is preceded by a path marker.
    find "$AI_ROOT/knowledge" -type f -name '*.md' -print0 \
      | sort -z \
      | while IFS= read -r -d '' f; do
          rel="${f#"$AI_ROOT/"}"
          echo "--- $rel ---"
          cat "$f"
          echo
        done
  fi
} > "$SYSTEM_FILE"

# Guard against triple-quote collisions in the Modelfile SYSTEM block.
if grep -q '"""' "$SYSTEM_FILE"; then
  echo "ERROR: prompt content contains triple-quotes; would break Modelfile parsing." >&2
  exit 1
fi

# Write the Modelfile. No bash expansion of the prompt body — we just cat it.
{
  echo "FROM $BASE_MODEL"
  echo
  echo 'SYSTEM """'
  cat "$SYSTEM_FILE"
  echo '"""'
  echo
  echo "PARAMETER temperature $TEMPERATURE"
  echo 'PARAMETER top_p 0.95'
  echo 'PARAMETER top_k 20'
  echo 'PARAMETER min_p 0'
  echo 'PARAMETER repeat_penalty 1.05'
  echo 'PARAMETER num_ctx 16384'
} > "$MODELFILE"

ollama create "$MODEL_NAME" -f "$MODELFILE"

echo
echo "✓ Built $MODEL_NAME from $BASE_MODEL"
echo "  System prompt: $(wc -l < "$SYSTEM_FILE") lines, $(wc -w < "$SYSTEM_FILE") words"
echo "  Modelfile:     $MODELFILE"
echo "  Run:           ollama run --think false $MODEL_NAME"
