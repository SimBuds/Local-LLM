"""
Shared judge-panel helpers for run-learn.py and run-tutor.py.

Both runners grade free-text explanations with a leave-one-out panel of local
models. This module holds the parts that are identical between them: building the
judge prompt (with an optional stricter rubric), parsing the judge's JSON scores,
and the reliability stats — parse rate, inter-judge disagreement, under-judged
warnings — that say how much to trust the resulting /10 numbers. The rubric
dimensions and base template stay in each runner; only the mechanics live here.
"""

from __future__ import annotations

import json
import re
import statistics

from _ollama import generate, resolve_model

JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

# Appended to the judge prompt under --judge-rubric strict. The default rubric is
# lenient (a 2 for "clear"/"excellent"); strict mode pushes judges to reserve top
# marks, so a model that coasts on vague-but-plausible prose loses points.
STRICT_SUFFIX = (
    "\n\nGrade STRICTLY: award a 2 only when the dimension is fully and correctly "
    "addressed, a 1 for partial coverage, and a 0 when it is missing or wrong. "
    "When in doubt, score lower. Do not give credit for vague or generic prose."
)


def judge_scores(judge_model: str, topic: str, response: str, timeout: int,
                 template: str, rubric: list[str], strict: bool = False) -> dict:
    """Ask `judge_model` for rubric scores. Returns {dim: 0..2} plus `_parsed`
    (False when the call failed or no JSON came back — those score 0 and are
    excluded from the panel average, but still counted against parse rate)."""
    prompt = template.format(topic=topic, response=response)
    if strict:
        prompt += STRICT_SUFFIX
    jname, jthink = resolve_model(judge_model)
    try:
        text, _ = generate(jname, prompt, timeout, think=jthink)
    except Exception:  # noqa: BLE001
        return {d: 0 for d in rubric} | {"_parsed": False}
    m = JSON_RE.search(text)
    if not m:
        return {d: 0 for d in rubric} | {"_parsed": False}
    try:
        raw = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {d: 0 for d in rubric} | {"_parsed": False}
    out = {}
    for d in rubric:
        v = raw.get(d, 0)
        out[d] = max(0, min(2, int(v))) if isinstance(v, (int, float)) else 0
    out["_parsed"] = True
    return out


def reliability_lines(judge_stats: dict[str, list[bool]], records: list[dict]) -> list[str]:
    """Markdown bullet lines describing how trustworthy the judging was.

    `judge_stats` maps judge spec -> list of per-grading parsed/failed booleans.
    `records` must carry `judge_expl` (judge->/10) and `n_judges`. Reports parse
    rate per judge, mean inter-judge disagreement (only meaningful when a response
    had ≥2 judges), and a warning for responses scored by fewer than two judges.
    """
    L: list[str] = []
    for j, flags in judge_stats.items():
        n = len(flags)
        ok = sum(flags)
        pct = (ok / n * 100) if n else 0.0
        flag = "  ⚠ judge often unparseable" if n and ok / n < 0.8 else ""
        L.append(f"- `{j}` parse rate: {ok}/{n} ({pct:.0f}%){flag}")

    multi = [list(r["judge_expl"].values()) for r in records
             if r.get("n_judges", 0) >= 2]
    if multi:
        dis = statistics.mean(statistics.pstdev(v) for v in multi)
        L.append(f"- Inter-judge disagreement: mean σ {dis:.2f}/10 across "
                 f"{len(multi)} responses graded by ≥2 judges")
    else:
        L.append("- Inter-judge disagreement: n/a — each response had <2 judges. "
                 "With a 2-model lineup, leave-one-out leaves a single judge per "
                 "response; add a 3rd model to cross-check scores.")

    under = sum(1 for r in records if r.get("n_judges", 0) < 2)
    if under:
        L.append(f"- ⚠ {under}/{len(records)} responses scored by fewer than 2 "
                 f"judges — those /10 numbers rest on a single (or no) judge and "
                 f"should be read as soft signal.")
    return L
