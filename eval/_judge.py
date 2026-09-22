"""
Shared judge-panel helpers for run-learn.py and run-tutor.py.

Both runners grade free-text explanations with a leave-one-out panel of local
models. This module holds the parts that are identical between them: building the
judge prompt (with an optional stricter rubric), parsing the judge's JSON scores,
and the reliability stats — parse rate, inter-judge disagreement, under-judged
warnings — that say how much to trust the resulting /10 numbers. The rubric
dimensions and base template stay in each runner; only the mechanics live here.

Two independent noise sources sit under every /10 in these suites, and they need
different fixes:

  * BETWEEN judges — one model's taste is not a measurement. Fixed structurally by
    the panel: leave-one-out over a 3-model lineup gives 2 judges per response.
    With only 2 models it gave 1, and inter-judge disagreement was uncomputable.
  * WITHIN a judge — the same judge re-reading the same response does not always
    return the same JSON. Fixed by `repeats`: score N times and take the MEDIAN,
    so one anomalous sample cannot move the number. Median, not mean, because a
    judge that misfires tends to misfire hard (a 2 read as a 0), and a mean lets
    that single outlier drag the score.

`judge_total()` handles the within-judge half and reports the spread it saw, so
the summaries can show whether the repeats were needed or were noise-free.
"""

from __future__ import annotations

import json
import re
import statistics
import urllib.error

from _gateway import DEAD_SERVER_STREAK, after_failure, generate, resolve_model

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
                 template: str, rubric: list[str], strict: bool = False,
                 options: dict | None = None) -> dict:
    """Ask `judge_model` for rubric scores. Returns {dim: 0..2} plus `_parsed`
    (False when the call failed or no JSON came back — those score 0 and are
    excluded from the panel average, but still counted against parse rate).

    `options` is passed through to the judge's generate call. A run is only
    reproducible if the grading is seeded too, not just the generation."""
    prompt = template.format(topic=topic, response=response)
    if strict:
        prompt += STRICT_SUFFIX
    jname, jthink = resolve_model(judge_model)
    try:
        text, _ = generate(jname, prompt, timeout, think=jthink, options=options)
    except (urllib.error.URLError, TimeoutError) as e:
        # A crashed judge stops the run: on 2026-09-17 qwen died mid-grading and
        # 53 calls were recorded as unparseable. The streak is passed at the
        # threshold because a judge call has no loop of its own to count in, so
        # a router that is down is confirmed and reported on the first failure.
        after_failure(jname, e, DEAD_SERVER_STREAK)
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


def judge_total(judge_model: str, topic: str, response: str, timeout: int,
                template: str, rubric: list[str], strict: bool = False,
                options: dict | None = None,
                repeats: int = 1) -> tuple[float | None, list[bool], float]:
    """Score one response with `judge_model`, `repeats` times, and take the median.

    Returns `(median_total, parse_flags, spread)`:
      * `median_total` — median of the parsed /10 totals, or None when every
        repeat failed to parse (the caller must then treat the response as
        ungraded by this judge, NOT as a zero — a judge that returns garbage is
        missing data, and scoring it 0 would punish the model being graded for
        the judge's failure).
      * `parse_flags` — one bool per repeat, so parse rate still counts every
        call rather than collapsing a 1-of-3 success into a clean "parsed".
      * `spread` — max minus min across parsed totals, the within-judge noise
        this repeat loop absorbed. 0.0 when fewer than 2 repeats parsed.

    With a fixed seed every repeat would otherwise be byte-identical and the
    median would be a single sample wearing a disguise, so each repeat offsets
    the seed. Unseeded runs already vary per call and are left alone.
    """
    totals: list[int] = []
    flags: list[bool] = []
    for i in range(max(1, repeats)):
        opts = dict(options) if options else None
        if opts and "seed" in opts:
            opts["seed"] = opts["seed"] + i
        sc = judge_scores(judge_model, topic, response, timeout, template,
                          rubric, strict, options=opts)
        flags.append(bool(sc.get("_parsed")))
        if sc.get("_parsed"):
            totals.append(sum(sc[d] for d in rubric))
    if not totals:
        return None, flags, 0.0
    return statistics.median(totals), flags, float(max(totals) - min(totals))


def reliability_lines(judge_stats: dict[str, list[bool]], records: list[dict],
                      repeats: int = 1) -> list[str]:
    """Markdown bullet lines describing how trustworthy the judging was.

    `judge_stats` maps judge spec -> list of per-call parsed/failed booleans (one
    entry per repeat, not per response). `records` must carry `judge_expl`
    (judge -> median /10) and `n_judges`; `judge_spread` (judge -> within-judge
    max-min) is used when present. Reports parse rate per judge, within-judge
    noise absorbed by the repeat median, mean inter-judge disagreement (only
    meaningful when a response had ≥2 judges), and a warning for responses scored
    by fewer than two judges.
    """
    L: list[str] = []
    for j, flags in judge_stats.items():
        n = len(flags)
        ok = sum(flags)
        pct = (ok / n * 100) if n else 0.0
        flag = "  ⚠ judge often unparseable" if n and ok / n < 0.8 else ""
        calls = f"{n} calls" if repeats == 1 else f"{n} calls, {repeats}/response"
        L.append(f"- `{j}` parse rate: {ok}/{n} ({pct:.0f}%) over {calls}{flag}")

    if repeats > 1:
        spreads = [s for r in records for s in r.get("judge_spread", {}).values()]
        if spreads:
            mean_spread = statistics.mean(spreads)
            noisy = sum(1 for s in spreads if s >= 2)
            L.append(f"- Within-judge noise: mean spread {mean_spread:.2f}/10 "
                     f"across {len(spreads)} judge×response medians "
                     f"({repeats} calls each); {noisy} varied by ≥2 points "
                     f"between repeats. The reported score is the median, so "
                     f"that variation is absorbed rather than propagated.")
    else:
        L.append("- Within-judge noise: not measured — 1 call per judge per "
                 "response. Re-run with `--judge-repeats 3` to score each "
                 "response several times and take the median.")

    multi = [list(r["judge_expl"].values()) for r in records
             if r.get("n_judges", 0) >= 2]
    if multi:
        dis = statistics.mean(statistics.pstdev(v) for v in multi)
        L.append(f"- Inter-judge disagreement: mean σ {dis:.2f}/10 across "
                 f"{len(multi)} responses graded by ≥2 judges")
    else:
        L.append("- Inter-judge disagreement: n/a — each response had <2 judges. "
                 "Leave-one-out over an N-model lineup leaves N-1 judges per "
                 "response, so a 2-model run leaves one; add a 3rd model "
                 "(`--models gemma qwen lite`) to cross-check scores.")

    under = sum(1 for r in records if r.get("n_judges", 0) < 2)
    if under:
        L.append(f"- ⚠ {under}/{len(records)} responses scored by fewer than 2 "
                 f"judges — those /10 numbers rest on a single (or no) judge and "
                 f"should be read as soft signal.")
    return L
