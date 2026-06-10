"""
Content battery for run-content.py: format + instruction-following over short
writing prompts, scored with cheap deterministic regex signals (no LLM judge).

Each task carries its own prompt and its own `evaluate(text) -> dict` scorer,
because content success criteria are task-specific: SEO copy wants a keyword and
a fixed heading skeleton, a technical explanation wants plain prose in a word
band, a Markdown brief wants an exact section structure. A task attempt is
"clean" iff it satisfies every rule its scorer checks. The runner discovers
everything in TASKS and reports a per-task matrix, mirroring run-code.py.

`evaluate` returns at least: clean (bool), words (int), flags (str — a compact
reason string for the console/summary, "clean" when nothing tripped). Add tasks
here; keep the scorers deterministic so results are comparable run to run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Shared signals.
HTML_TAG_RE = re.compile(r"<(?!!)/?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?>")
SENTENCE_SPLIT_RE = re.compile(r"[.!?]+\s+")
KEYWORD_LINE_RE = re.compile(r'(?im)^.*target keyword[^"\n]*"([^"]+)"')
H1_RE = re.compile(r"(?m)^#[ \t]+(.+?)\s*$")
H2_RE = re.compile(r"(?m)^##[ \t]+(.+?)\s*$")
BULLET_RE = re.compile(r"(?m)^[ \t]*[-*][ \t]+\S")
# Hedging / filler the prompts forbid — instruction-following stressors.
HEDGE_RE = re.compile(r"\b(might|perhaps|maybe|arguably|in today's world)\b", re.I)


@dataclass
class ContentTask:
    key: str
    prompt: str
    evaluate: Callable[[str], dict]   # text -> {clean, words, flags, ...}


def _words(text: str) -> int:
    return len(text.strip().split())


# --- task 1: SEO product copy (the original content test) ---------------------
def _extract_keyword(prompt: str) -> str | None:
    m = KEYWORD_LINE_RE.search(prompt)
    return m.group(1).strip() if m else None


def _make_seo_eval(keyword: str | None) -> Callable[[str], dict]:
    def evaluate(text: str) -> dict:
        stripped = text.strip()
        sentences = [s for s in SENTENCE_SPLIT_RE.split(stripped) if s.strip()]
        longest = max((len(s.split()) for s in sentences), default=0)
        html = len(HTML_TAG_RE.findall(text))
        fenced = stripped.startswith("```") and stripped.endswith("```")
        h1 = H1_RE.findall(text)
        h2 = H2_RE.findall(text)
        kw_hits = len(re.findall(re.escape(keyword), text, re.I)) if keyword else 0
        kw_in_h1 = bool(keyword and h1 and re.search(re.escape(keyword), h1[0], re.I))
        hedges = len(HEDGE_RE.findall(text))
        h1_ok = len(h1) == 1 and len(h1[0]) <= 60
        h2_ok = 3 <= len(h2) <= 4  # 3 sections + optional FAQ heading

        flags = []
        if html: flags.append(f"H{html}")
        if fenced: flags.append("F")
        if longest > 40: flags.append(f"L{longest}")
        if not h1_ok: flags.append(f"h1={len(h1)}")
        if not h2_ok: flags.append(f"h2={len(h2)}")
        if hedges: flags.append(f"hedge{hedges}")
        if keyword and not (2 <= kw_hits <= 5): flags.append(f"K{kw_hits}")
        if keyword and not kw_in_h1: flags.append("kw!h1")

        clean = not flags
        return {"clean": clean, "words": _words(text),
                "flags": ",".join(flags) if flags else "clean",
                "keyword_hits": kw_hits}
    return evaluate


def seo_task_from_prompt(prompt: str, keyword: str | None = None,
                         key: str = "seo_product") -> ContentTask:
    """Build an SEO content task from an arbitrary prompt (used by the runner's
    --prompt-file ad-hoc mode). Keyword is taken from `keyword` or parsed from
    the prompt's 'Target keyword: "..."' line."""
    kw = keyword or _extract_keyword(prompt)
    return ContentTask(key=key, prompt=prompt, evaluate=_make_seo_eval(kw))


_SEO_PROMPT = (PROMPTS_DIR / "seo-product.md").read_text(encoding="utf-8").strip()
SEO_PRODUCT = seo_task_from_prompt(_SEO_PROMPT)


# --- task 2: concise technical explanation (plain prose, word-banded) ---------
_TECH_PROMPT = (
    "Explain what a database index is and the central tradeoff it makes, written "
    "for a working software developer who has never used one deliberately.\n\n"
    "Constraints:\n"
    "- 120 to 200 words.\n"
    "- Plain prose only: no headings, no bullet lists, no code blocks, no tables.\n"
    "- Define the core idea, then state at least one concrete downside or cost of "
    "adding an index (e.g. its effect on writes or storage).\n"
    "- US English. No hedging ('might', 'perhaps', 'maybe'). No filler intro "
    "('In today's world…').\n"
    "- Return only the explanation. No preface, no closing remarks."
)
# Signals that the required downside was actually stated, not just the benefit.
_COST_RE = re.compile(
    r"\b(slow\w*|wri\w*|insert\w*|updat\w*|storage|disk|space|overhead|"
    r"cost\w*|expensive|maintain\w*|trade-?off)\b", re.I)


def _eval_tech_explain(text: str) -> dict:
    stripped = text.strip()
    words = _words(text)
    has_fence = "```" in text
    has_heading = bool(H1_RE.search(text) or H2_RE.search(text))
    has_bullets = bool(BULLET_RE.search(text))
    hedges = len(HEDGE_RE.findall(text))
    mentions_index = bool(re.search(r"\bindex(es|ing)?\b", text, re.I))
    states_cost = bool(_COST_RE.search(text))

    flags = []
    if not (120 <= words <= 200): flags.append(f"W{words}")
    if has_fence: flags.append("fence")
    if has_heading: flags.append("heading")
    if has_bullets: flags.append("bullets")
    if hedges: flags.append(f"hedge{hedges}")
    if not mentions_index: flags.append("no-index")
    if not states_cost: flags.append("no-cost")

    return {"clean": not flags, "words": words,
            "flags": ",".join(flags) if flags else "clean"}


TECH_EXPLAIN = ContentTask(
    key="tech_explain", prompt=_TECH_PROMPT, evaluate=_eval_tech_explain)


# --- task 3: structured Markdown brief (exact section skeleton) ---------------
_BRIEF_PROMPT = (
    "Write a short structured Markdown brief for a feature kickoff: adding "
    "two-factor authentication (2FA) to an existing web app.\n\n"
    "Use exactly this structure and nothing else:\n"
    "- A single H1 line: the title.\n"
    "- Then three H2 sections, titled exactly `Goal`, `Scope`, and `Risks`, in "
    "that order.\n"
    "- Under `Goal`: a single sentence (no list).\n"
    "- Under `Scope`: a bulleted list of 2 to 4 items.\n"
    "- Under `Risks`: a bulleted list of 2 to 4 items.\n\n"
    "Constraints: under 200 words total. US English. No hedging. Markdown only. "
    "No preface or closing remarks, no code blocks."
)


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """Split into (h2_title, body_lines) sections by H2 heading, in order."""
    out: list[tuple[str, list[str]]] = []
    cur_title: str | None = None
    cur_body: list[str] = []
    for line in text.splitlines():
        m = H2_RE.match(line)
        if m:
            if cur_title is not None:
                out.append((cur_title, cur_body))
            cur_title, cur_body = m.group(1).strip(), []
        elif cur_title is not None:
            cur_body.append(line)
    if cur_title is not None:
        out.append((cur_title, cur_body))
    return out


def _bullet_count(lines: list[str]) -> int:
    return sum(1 for ln in lines if BULLET_RE.match(ln))


def _eval_md_brief(text: str) -> dict:
    words = _words(text)
    h1 = H1_RE.findall(text)
    has_fence = "```" in text
    hedges = len(HEDGE_RE.findall(text))
    secs = _sections(text)
    titles = [t for t, _ in secs]

    flags = []
    if len(h1) != 1: flags.append(f"h1={len(h1)}")
    if titles != ["Goal", "Scope", "Risks"]:
        flags.append("sections=" + "/".join(titles) if titles else "sections=none")
    else:
        scope_b = _bullet_count(secs[1][1])
        risks_b = _bullet_count(secs[2][1])
        if _bullet_count(secs[0][1]) != 0: flags.append("goal-bullets")
        if not (2 <= scope_b <= 4): flags.append(f"scope_b={scope_b}")
        if not (2 <= risks_b <= 4): flags.append(f"risks_b={risks_b}")
    if words >= 200: flags.append(f"W{words}")
    if has_fence: flags.append("fence")
    if hedges: flags.append(f"hedge{hedges}")

    return {"clean": not flags, "words": words,
            "flags": ",".join(flags) if flags else "clean"}


MD_BRIEF = ContentTask(
    key="md_brief", prompt=_BRIEF_PROMPT, evaluate=_eval_md_brief)


TASKS: dict[str, ContentTask] = {
    t.key: t for t in (SEO_PRODUCT, TECH_EXPLAIN, MD_BRIEF)
}
