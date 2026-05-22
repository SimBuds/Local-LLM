#!/usr/bin/env python3
"""
Minimal eval runner: drive a fixed SEO content-writing prompt against each
custom model in turn, N attempts per model. No deps beyond stdlib — talks to
the local Ollama HTTP API.

Usage:
  ./eval/run.py                                  # defaults: 3 models, 5 attempts
  ./eval/run.py --models qwen-custom             # single model
  ./eval/run.py --attempts 3                     # fewer attempts
  ./eval/run.py --prompt-file eval/prompts/x.md  # different prompt

Output:
  eval/runs/<UTC-timestamp>/
    summary.md
    <model>/attempt-<n>.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODELS = ["qwen-custom", "granite-custom", "llama-custom"]
DEFAULT_PROMPT = Path(__file__).parent / "prompts" / "seo-product.md"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_ROOT = REPO_ROOT / "eval" / "runs"


HTML_TAG_RE = re.compile(r"<(?!!)/?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?>")
SENTENCE_SPLIT_RE = re.compile(r"[.!?]+\s+")
KEYWORD_LINE_RE = re.compile(r'(?im)^.*target keyword[^"\n]*"([^"]+)"')


def extract_keyword(prompt: str) -> str | None:
    """Pull `Target keyword: "..."` from the prompt if present."""
    m = KEYWORD_LINE_RE.search(prompt)
    return m.group(1).strip() if m else None


def score(text: str, keyword: str | None) -> dict:
    """Cheap, regex-only quality signals. No judgment — just measurements."""
    stripped = text.strip()
    words = stripped.split()
    word_count = len(words)

    html_tags = HTML_TAG_RE.findall(text)
    fenced_wrap = stripped.startswith("```") and stripped.endswith("```")

    sentences = [s for s in SENTENCE_SPLIT_RE.split(stripped) if s.strip()]
    longest_sentence_words = max((len(s.split()) for s in sentences), default=0)

    keyword_hits = 0
    if keyword:
        keyword_hits = len(re.findall(re.escape(keyword), text, flags=re.IGNORECASE))

    return {
        "words": word_count,
        "html_tags": len(html_tags),
        "fenced_wrap": fenced_wrap,
        "longest_sentence_words": longest_sentence_words,
        "keyword_hits": keyword_hits,
    }


def generate(model: str, prompt: str, timeout: int) -> tuple[str, dict]:
    """Single non-streaming call. Returns (response_text, raw_meta)."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,  # ignored by non-Qwen; harmless
    }).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("response", ""), body


def run_attempt(model: str, prompt: str, n: int, total: int, timeout: int,
                keyword: str | None) -> dict:
    print(f"  [{n}/{total}] ", end="", flush=True)
    t0 = time.monotonic()
    try:
        text, meta = generate(model, prompt, timeout)
    except (urllib.error.URLError, TimeoutError) as e:
        elapsed = time.monotonic() - t0
        print(f"FAIL ({elapsed:.1f}s): {e}")
        return {"ok": False, "error": str(e), "elapsed_s": elapsed}
    elapsed = time.monotonic() - t0
    eval_count = meta.get("eval_count", 0)
    tps = (eval_count / (meta.get("eval_duration", 1) / 1e9)) if eval_count else 0
    s = score(text, keyword)
    # Inline flags: H=HTML, F=fenced, L=long sentence (>40 words), K=keyword over/under (target 4)
    flags = []
    if s["html_tags"] > 0:        flags.append(f"H{s['html_tags']}")
    if s["fenced_wrap"]:          flags.append("F")
    if s["longest_sentence_words"] > 40: flags.append(f"L{s['longest_sentence_words']}")
    if keyword and not (2 <= s["keyword_hits"] <= 5): flags.append(f"K{s['keyword_hits']}")
    flag_str = ",".join(flags) if flags else "—"
    print(f"ok  {elapsed:5.1f}s  {eval_count:4d} tok  {tps:5.1f} tok/s  "
          f"{s['words']:4d} words  [{flag_str}]")
    return {
        "ok": True,
        "elapsed_s": elapsed,
        "eval_count": eval_count,
        "tok_per_s": tps,
        "text": text,
        **s,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS,
                        help=f"models to test (default: {' '.join(DEFAULT_MODELS)})")
    parser.add_argument("--attempts", type=int, default=5,
                        help="attempts per model (default: 5)")
    parser.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT,
                        help=f"prompt file (default: {DEFAULT_PROMPT.relative_to(REPO_ROOT)})")
    parser.add_argument("--timeout", type=int, default=300,
                        help="per-call timeout in seconds (default: 300)")
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT,
                        help="run output root (default: eval/runs/)")
    parser.add_argument("--keyword", type=str, default=None,
                        help="target keyword for scoring (default: parsed from prompt)")
    args = parser.parse_args()

    if not args.prompt_file.is_file():
        print(f"prompt file not found: {args.prompt_file}", file=sys.stderr)
        return 1
    prompt = args.prompt_file.read_text(encoding="utf-8").strip()
    keyword = args.keyword or extract_keyword(prompt)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = args.out_root / stamp
    run_dir.mkdir(parents=True, exist_ok=False)
    print(f"Run dir: {run_dir.relative_to(REPO_ROOT)}")
    print(f"Prompt:  {args.prompt_file.relative_to(REPO_ROOT)} "
          f"({len(prompt.split())} words)")
    print(f"Models:  {', '.join(args.models)} × {args.attempts} attempts each")
    print(f"Keyword: {keyword or '(none — keyword score disabled)'}")
    print()

    summary: dict[str, list[dict]] = {}
    for model in args.models:
        print(f"=== {model} ===")
        model_dir = run_dir / model
        model_dir.mkdir()
        results = []
        for n in range(1, args.attempts + 1):
            r = run_attempt(model, prompt, n, args.attempts, args.timeout, keyword)
            if r.get("ok"):
                (model_dir / f"attempt-{n}.md").write_text(r["text"], encoding="utf-8")
            results.append(r)
        summary[model] = results
        print()

    # Write summary.md
    lines = [
        f"# Eval run {stamp}",
        "",
        f"- Prompt: `{args.prompt_file.relative_to(REPO_ROOT)}` "
        f"({len(prompt.split())} words)",
        f"- Attempts per model: {args.attempts}",
        f"- Target keyword: `{keyword}`" if keyword else "- Target keyword: _(none)_",
        "",
        "Flags: **H**=HTML tags, **F**=fenced ```` ``` ```` wrap, "
        "**L**=longest sentence >40 words, **K**=keyword count outside 2–5.",
        "",
        "| Model | OK | Avg s | Avg tok/s | Avg words | HTML | Fenced | Max sent. | KW hits | Clean attempts |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for model, rs in summary.items():
        ok = [r for r in rs if r.get("ok")]
        if not ok:
            lines.append(f"| `{model}` | 0/{len(rs)} | — | — | — | — | — | — | — | — |")
            continue
        avg_s = sum(r["elapsed_s"] for r in ok) / len(ok)
        avg_tps = sum(r["tok_per_s"] for r in ok) / len(ok)
        avg_w = sum(r["words"] for r in ok) / len(ok)
        html_attempts = sum(1 for r in ok if r["html_tags"] > 0)
        fenced_attempts = sum(1 for r in ok if r["fenced_wrap"])
        max_sent = max((r["longest_sentence_words"] for r in ok), default=0)
        kw_hits = [r["keyword_hits"] for r in ok]
        kw_summary = f"{min(kw_hits)}–{max(kw_hits)}" if keyword else "—"
        clean = sum(
            1 for r in ok
            if r["html_tags"] == 0
            and not r["fenced_wrap"]
            and r["longest_sentence_words"] <= 40
            and (not keyword or 2 <= r["keyword_hits"] <= 5)
        )
        lines.append(
            f"| `{model}` | {len(ok)}/{len(rs)} | {avg_s:.1f} | "
            f"{avg_tps:.1f} | {avg_w:.0f} | "
            f"{html_attempts}/{len(ok)} | {fenced_attempts}/{len(ok)} | "
            f"{max_sent} | {kw_summary} | {clean}/{len(ok)} |"
        )
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Summary: {(run_dir / 'summary.md').relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
