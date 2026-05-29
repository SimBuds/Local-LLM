"""
Shared plumbing for the eval runners (run.py = content, run-code.py = coding).
Stdlib-only; talks to the local Ollama HTTP API. No scoring lives here — each
runner owns its own scorer and summary. This file is just the transport plus a
few helpers both runners need.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/generate"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODELS = [
    "qwen-custom", "granite-custom", "llama-custom",
    "ministral-custom", "gemma-custom",
]

# A ```lang fenced block (group 1 = body). Greedy-safe, handles missing lang.
FENCE_RE = re.compile(r"```[ \t]*([a-zA-Z0-9_+-]*)[ \t]*\n(.*?)```", re.DOTALL)


def resolve_model(spec: str) -> tuple[str, bool]:
    """Map a leaderboard spec to (ollama_name, think).

    A trailing `:think` selects Qwen-style thinking mode while keeping the real
    Ollama model name intact, so `qwen-custom` and `qwen-custom:think` can be
    ranked as separate entries. Thinking is a runtime flag, not a Modelfile
    setting — only qwen-custom honors it; the others error on `--think`, so
    don't tag them.
    """
    if spec.endswith(":think"):
        return spec[: -len(":think")], True
    return spec, False


def generate(model: str, prompt: str, timeout: int, think: bool = False) -> tuple[str, dict]:
    """Single non-streaming call. Returns (response_text, raw_meta).

    `model` is the real Ollama name (resolve a `:think` spec first). `think`
    toggles Qwen thinking mode; it is ignored by non-Qwen models.
    """
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": think,
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("response", ""), body


def tok_per_s(meta: dict) -> float:
    n = meta.get("eval_count", 0)
    return n / (meta.get("eval_duration", 1) / 1e9) if n else 0.0


def new_run_dir(out_root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_root / stamp
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def extract_code(text: str, prefer_lang: str = "python") -> str:
    """
    Pull source out of a model response. Prefers a fenced block tagged with
    `prefer_lang`, then any fenced block, then (last resort) the raw text —
    a model that ignored the 'code only' instruction still gets executed.
    """
    blocks = FENCE_RE.findall(text)
    if blocks:
        for lang, body in blocks:
            if lang.lower() == prefer_lang:
                return body.strip("\n")
        return blocks[0][1].strip("\n")  # first fenced block, whatever the tag
    return text.strip()


def run_program(source: str, exec_timeout: int) -> tuple[bool, str]:
    """Execute `source` in a throwaway temp dir. Returns (passed, short_reason).

    SAFETY: runs model-generated code in a subprocess with a wall-clock timeout
    and a fresh CWD, but it is NOT containerized. Only run trusted models/tasks.
    """
    with tempfile.TemporaryDirectory() as td:
        prog = Path(td) / "sol.py"
        prog.write_text(source, encoding="utf-8")
        try:
            proc = subprocess.run(
                [sys.executable, str(prog)],
                cwd=td, capture_output=True, text=True, timeout=exec_timeout,
            )
        except subprocess.TimeoutExpired:
            return False, "timeout"
    if proc.returncode == 0:
        return True, "pass"
    err = (proc.stderr or proc.stdout).strip().splitlines()
    last = err[-1] if err else f"exit {proc.returncode}"
    if "AssertionError" in last:
        return False, "wrong-answer"
    if "SyntaxError" in last:
        return False, "syntax"
    return False, last[:60]
