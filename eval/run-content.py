#!/usr/bin/env python3
"""
Content benchmark: drive a fixed SEO content-writing prompt against each custom
model in turn, N attempts per model, and score format + instruction-following with
cheap regex signals (no LLM judge). The summary ranks models by a composite
"clean rate" (tie-break: speed) and declares a winner.

Usage:
  ./eval/run-content.py                          # all models, 5 attempts
  ./eval/run-content.py --models gemma           # single model
  ./eval/run-content.py --attempts 3
  ./eval/run-content.py --prompt-file eval/prompts/x.md  # different prompt

Output:
  eval/runs/<UTC>/content/
    summary.md
    <model>/attempt-<n>.md

For the coding benchmark, see run-code.py.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ollama import (  # noqa: E402
    REPO_ROOT, generate, get_effective_think, new_run_dir, resolve_model, tok_per_s,
)

DEFAULT_PROMPT = Path(__file__).parent / "prompts" / "seo-product.md"
DEFAULT_OUT_ROOT = REPO_ROOT / "eval" / "runs"

HTML_TAG_RE = re.compile(r"<(?!!)/?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?>")
SENTENCE_SPLIT_RE = re.compile(r"[.!?]+\s+")
KEYWORD_LINE_RE = re.compile(r'(?im)^.*target keyword[^"\n]*"([^"]+)"')
H1_RE = re.compile(r"(?m)^#[ \t]+(.+?)\s*$")
H2_RE = re.compile(r"(?m)^##[ \t]+.+$")
# Hedging / filler the prompt forbids — instruction-following stressors.
HEDGE_RE = re.compile(r"\b(might|perhaps|maybe|arguably|in today's world)\b", re.I)


def extract_keyword(prompt: str) -> str | None:
    m = KEYWORD_LINE_RE.search(prompt)
    return m.group(1).strip() if m else None


def score(text: str, keyword: str | None) -> dict:
    """Cheap, regex-only quality + structure signals. No judgment, just measurement."""
    stripped = text.strip()
    words = stripped.split()

    html_tags = HTML_TAG_RE.findall(text)
    fenced_wrap = stripped.startswith("```") and stripped.endswith("```")
    sentences = [s for s in SENTENCE_SPLIT_RE.split(stripped) if s.strip()]
    longest = max((len(s.split()) for s in sentences), default=0)

    keyword_hits = (len(re.findall(re.escape(keyword), text, re.I))
                    if keyword else 0)

    h1 = H1_RE.findall(text)
    h2_count = len(H2_RE.findall(text))
    # Structure the prompt demands: exactly one H1 and three H2s.
    h1_ok = len(h1) == 1 and len(h1[0]) <= 60
    h2_ok = 3 <= h2_count <= 4  # 3 sections + optional FAQ heading
    kw_in_h1 = bool(keyword and h1 and re.search(re.escape(keyword), h1[0], re.I))
    hedges = len(HEDGE_RE.findall(text))

    return {
        "words": len(words),
        "html_tags": len(html_tags),
        "fenced_wrap": fenced_wrap,
        "longest_sentence_words": longest,
        "keyword_hits": keyword_hits,
        "h1_count": len(h1),
        "h2_count": h2_count,
        "h1_ok": h1_ok,
        "h2_ok": h2_ok,
        "kw_in_h1": kw_in_h1,
        "hedges": hedges,
    }


def is_clean(s: dict, keyword: str | None) -> bool:
    """A 'clean' attempt obeys every format + structure + instruction rule."""
    return (
        s["html_tags"] == 0
        and not s["fenced_wrap"]
        and s["longest_sentence_words"] <= 40
        and s["h1_ok"] and s["h2_ok"]
        and s["hedges"] == 0
        and (not keyword or (2 <= s["keyword_hits"] <= 5 and s["kw_in_h1"]))
    )


def run_attempt(model: str, prompt: str, n: int, total: int, timeout: int,
                thinking_mode: str, keyword: str | None) -> dict:
    print(f"  [{n}/{total}] ", end="", flush=True)
    name, model_think = resolve_model(model)

    think = get_effective_think(thinking_mode, model_think)

    t0 = time.monotonic()
    try:
        text, meta = generate(name, prompt, timeout, think=think)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"FAIL ({time.monotonic()-t0:.1f}s): {e}")
        return {"ok": False, "error": str(e), "elapsed_s": time.monotonic() - t0}
    elapsed = time.monotonic() - t0
    s = score(text, keyword)
    clean = is_clean(s, keyword)
    flags = []
    if s["html_tags"]: flags.append(f"H{s['html_tags']}")
    if s["fenced_wrap"]: flags.append("F")
    if s["longest_sentence_words"] > 40: flags.append(f"L{s['longest_sentence_words']}")
    if not s["h1_ok"]: flags.append(f"h1={s['h1_count']}")
    if not s["h2_ok"]: flags.append(f"h2={s['h2_count']}")
    if s["hedges"]: flags.append(f"hedge{s['hedges']}")
    if keyword and not (2 <= s["keyword_hits"] <= 5): flags.append(f"K{s['keyword_hits']}")
    flag_str = ",".join(flags) if flags else "clean"
    print(f"{'ok ' if clean else 'OK*'}  {elapsed:5.1f}s  "
          f"{tok_per_s(meta):5.1f} tok/s  {s['words']:4d} words  [{flag_str}]")
    return {"ok": True, "clean": clean, "elapsed_s": elapsed,
            "tok_per_s": tok_per_s(meta), "text": text, **s}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="+", required=True, help="Ollama model names")
    ap.add_argument("--attempts", type=int, default=5)
    ap.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--thinking", choices=["auto", "on", "off"], default="auto",
                    help="Thinking mode: 'auto' respects suffix configuration, 'on' forces thinking tokens, 'off' strips thinking passes.")
    ap.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    ap.add_argument("--keyword", type=str, default=None)
    args = ap.parse_args()

    if not args.prompt_file.is_file():
        print(f"prompt file not found: {args.prompt_file}", file=sys.stderr)
        return 1
    prompt = args.prompt_file.read_text(encoding="utf-8").strip()
    keyword = args.keyword or extract_keyword(prompt)

    run_dir = new_run_dir(args.out_root) / "content"
    run_dir.mkdir(parents=True)
    print(f"Run dir: {run_dir.relative_to(REPO_ROOT)}")
    print(f"Prompt:  {args.prompt_file.relative_to(REPO_ROOT)} ({len(prompt.split())} words)")
    print(f"Models:  {', '.join(args.models)} × {args.attempts} attempts")
    print(f"Keyword: {keyword or '(none)'}\n")

    summary: dict[str, list[dict]] = {}
    for model in args.models:
        print(f"=== {model} ===")
        mdir = run_dir / model
        mdir.mkdir()
        rs = []
        for n in range(1, args.attempts + 1):
            r = run_attempt(model, prompt, n, args.attempts, args.timeout, args.thinking, keyword)
            if r.get("ok"):
                (mdir / f"attempt-{n}.md").write_text(r["text"], encoding="utf-8")
            rs.append(r)
        summary[model] = rs
        print()

    write_summary(run_dir, summary, args, keyword, prompt)
    return 0


def write_summary(run_dir, summary, args, keyword, prompt) -> None:
    ranked = []
    for model, rs in summary.items():
        ok = [r for r in rs if r.get("ok")]
        if not ok:
            ranked.append({"model": model, "clean_rate": -1.0, "n_clean": 0,
                           "n_ok": 0, "total": len(rs), "avg_s": 0, "avg_tps": 0,
                           "avg_w": 0, "kw": "—"})
            continue
        n_clean = sum(1 for r in ok if r["clean"])
        kw_hits = [r["keyword_hits"] for r in ok]
        ranked.append({
            "model": model,
            "clean_rate": n_clean / len(ok),
            "n_clean": n_clean, "n_ok": len(ok), "total": len(rs),
            "avg_s": sum(r["elapsed_s"] for r in ok) / len(ok),
            "avg_tps": sum(r["tok_per_s"] for r in ok) / len(ok),
            "avg_w": sum(r["words"] for r in ok) / len(ok),
            "kw": f"{min(kw_hits)}–{max(kw_hits)}" if keyword else "—",
        })
    ranked.sort(key=lambda r: (-r["clean_rate"], -r["avg_tps"]))

    L = ["# Content benchmark", "",
         f"- Prompt: `{args.prompt_file.relative_to(REPO_ROOT)}` ({len(prompt.split())} words)",
         f"- Attempts per model: {args.attempts}",
         f"- Target keyword: `{keyword}`" if keyword else "- Target keyword: _(none)_",
         "- **Clean** = no HTML, not fenced-wrapped, longest sentence ≤40 words, "
         "exactly 1 H1 (≤60 chars) + 3 H2s, no hedging, keyword 2–5× incl. in H1.", ""]
    best = next((r for r in ranked if r["clean_rate"] >= 0), None)
    if best:
        L += [f"## 🏆 Winner: `{best['model']}` — "
              f"{best['n_clean']}/{best['n_ok']} clean "
              f"({best['clean_rate']*100:.0f}%) @ {best['avg_tps']:.0f} tok/s", ""]
    L += ["| Rank | Model | Clean rate | Clean | Avg s | Tok/s | Avg words | KW hits |",
          "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(ranked, 1):
        if r["n_ok"] == 0:
            L.append(f"| {i} | `{r['model']}` | (all failed) | 0/{r['total']} | — | — | — | — |")
            continue
        L.append(f"| {i} | `{r['model']}` | {r['clean_rate']*100:.0f}% | "
                 f"{r['n_clean']}/{r['n_ok']} | {r['avg_s']:.1f} | {r['avg_tps']:.0f} | "
                 f"{r['avg_w']:.0f} | {r['kw']} |")
    (run_dir / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"Summary: {(run_dir / 'summary.md').relative_to(REPO_ROOT)}")
    if best:
        print(f"Winner:  {best['model']} ({best['clean_rate']*100:.0f}% clean)")


if __name__ == "__main__":
    sys.exit(main())