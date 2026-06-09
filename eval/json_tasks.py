"""
Tasks for run-json.py: schema-constrained extraction over long contexts.

This is the eval the *consumer* apps (jobhunt, seo-cli) actually live or die on
and which the other runners don't touch: can the model (a) emit JSON that parses,
(b) conform to a required schema, and (c) pull the *correct* facts out of a long,
noisy document? Each task buries real facts ("needles") inside benign HR/policy
boilerplate so the prompt runs several thousand tokens, stressing long-context
fidelity the same way jobhunt's score/tailor prompts do (~6k+ tokens, num_ctx
pinned).

Each task carries:
  - schema:  an Ollama `format` JSON schema, passed to constrain decode.
  - checks:  (label, fn(parsed) -> bool) content-correctness probes. These are
             the long-context recall signal — schema-valid but wrong-fact output
             still fails them.

Add tasks here; run-json.py discovers everything in TASKS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# --- filler -------------------------------------------------------------------
# Benign, realistic boilerplate used to pad documents to a long token count so
# the needles are genuinely buried. None of this contains a fact a check probes.
_FILLER = [
    "Our company was founded on the belief that great products come from "
    "empowered teams. We invest heavily in professional development, offering "
    "an annual learning stipend, conference budgets, and dedicated growth time "
    "every sprint. We believe careers are marathons, not sprints, and we plan "
    "accordingly.",
    "We are proud of our inclusive culture. We welcome applicants of all "
    "backgrounds and are committed to building a team that reflects the "
    "communities we serve. Accommodations are available throughout the hiring "
    "process on request, and our facilities are fully accessible.",
    "Benefits include comprehensive health, dental, and vision coverage from "
    "day one, a generous paid-time-off policy, parental leave, and a wellness "
    "program. We also host quarterly offsites, weekly catered lunches when "
    "in-office, and an annual charitable matching program.",
    "Our engineering organization values pragmatism over dogma. We ship in "
    "small increments, review each other's work generously, and treat incidents "
    "as learning opportunities rather than occasions for blame. Documentation is "
    "a first-class deliverable, not an afterthought.",
    "Day to day, you can expect a mix of focused individual work and "
    "collaborative sessions. We protect maker time, keep meetings purposeful, "
    "and default to asynchronous written communication so that distributed "
    "teammates across time zones stay aligned without constant live calls.",
    "We measure success by customer outcomes, not vanity metrics. Teams set "
    "their own quarterly goals in collaboration with product and design, and we "
    "review progress openly. Compensation is reviewed annually and benchmarked "
    "against market data for comparable roles in the region.",
]


def _doc(needle: str, target_words: int = 2600, position: float = 0.55) -> str:
    """Bury `needle` inside ~`target_words` of filler at `position` (0..1).

    Returns one long string. Filler cycles deterministically so the doc is
    stable across runs (extraction accuracy must be comparable run to run).
    """
    out: list[str] = []
    count = 0
    inserted = False
    i = 0
    while count < target_words:
        if not inserted and count >= target_words * position:
            out.append(needle)
            count += len(needle.split())
            inserted = True
        para = _FILLER[i % len(_FILLER)]
        out.append(para)
        count += len(para.split())
        i += 1
    if not inserted:
        out.append(needle)
    return "\n\n".join(out)


@dataclass
class JsonTask:
    key: str
    instruction: str           # task + (where relevant) candidate profile
    context: str               # long document the facts are buried in
    schema: dict[str, Any]     # Ollama `format` schema
    checks: list[tuple[str, Callable[[Any], bool]]]  # content-correctness probes


def _has_skill(parsed: Any, key: str, needle: str) -> bool:
    """True if `needle` appears (case-insensitive substring) in any element of
    the string list at parsed[key]."""
    if not isinstance(parsed, dict):
        return False
    arr = parsed.get(key)
    if not isinstance(arr, list):
        return False
    return any(isinstance(x, str) and needle.lower() in x.lower() for x in arr)


# --- task 1: JD field extraction ---------------------------------------------
_JD_NEEDLE = (
    "THE ROLE: We are hiring a Mid-Level Full-Stack Developer for our Toronto "
    "team. This is a fully remote position open to candidates anywhere in Canada. "
    "You will need a minimum of 3 years of professional web development "
    "experience. Required skills: strong TypeScript and React, Node.js/Express "
    "on the back end, and hands-on experience with PostgreSQL. Nice to have: "
    "Docker, Shopify, and exposure to LLM tooling. This is an individual "
    "contributor role with no direct reports."
)

JD_EXTRACT = JsonTask(
    key="jd_extract",
    instruction=(
        "You are parsing a job posting. Extract the structured fields defined by "
        "the schema from the posting below. Use only what the posting states; do "
        "not invent skills or numbers."
    ),
    context=_doc(_JD_NEEDLE),
    schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "seniority": {"type": "string",
                          "enum": ["junior", "mid", "senior", "lead"]},
            "years_required": {"type": ["integer", "null"]},
            "remote": {"type": "boolean"},
            "must_have_skills": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "seniority", "years_required", "remote",
                     "must_have_skills"],
    },
    checks=[
        ("seniority=mid", lambda p: isinstance(p, dict) and p.get("seniority") == "mid"),
        ("years=3", lambda p: isinstance(p, dict) and p.get("years_required") == 3),
        ("remote=true", lambda p: isinstance(p, dict) and p.get("remote") is True),
        ("TypeScript in must-have", lambda p: _has_skill(p, "must_have_skills", "TypeScript")),
        ("PostgreSQL in must-have", lambda p: _has_skill(p, "must_have_skills", "PostgreSQL")),
    ],
)

# --- task 2: needle recall (buried numeric fact) ------------------------------
_POLICY_NEEDLE = (
    "RELOCATION POLICY: For roles that require relocation, the company provides "
    "relocation assistance capped at 5000 CAD per hire, reimbursed against "
    "submitted receipts within the first 90 days of employment. Relocation "
    "support is not available for fully remote positions."
)

NEEDLE_RECALL = JsonTask(
    key="needle_recall",
    instruction=(
        "Answer using only the policy document below. Find the relocation policy "
        "and report whether relocation assistance is offered and its cap in CAD. "
        "If the document does not state a cap, use null."
    ),
    context=_doc(_POLICY_NEEDLE, target_words=3200, position=0.6),
    schema={
        "type": "object",
        "properties": {
            "relocation_offered": {"type": "boolean"},
            "relocation_cap_cad": {"type": ["integer", "null"]},
        },
        "required": ["relocation_offered", "relocation_cap_cad"],
    },
    checks=[
        ("offered=true", lambda p: isinstance(p, dict) and p.get("relocation_offered") is True),
        ("cap=5000", lambda p: isinstance(p, dict) and p.get("relocation_cap_cad") == 5000),
    ],
)

# --- task 3: decline guard (boolean judgment over a stretch JD) ----------------
_EM_NEEDLE = (
    "ABOUT THE ROLE: We are seeking a Senior Engineering Manager to lead our "
    "Platform group in Toronto. You will directly manage a team of 8-12 "
    "engineers, own hiring and performance reviews, set headcount, and be "
    "accountable for multi-quarter roadmaps. Requirements: 10+ years of "
    "professional software engineering experience and at least 4 years managing "
    "engineers directly. This is a people-leadership role, not an individual "
    "contributor position."
)

DECLINE_GUARD = JsonTask(
    key="decline_guard",
    instruction=(
        "Candidate profile: ~3 years of full-stack experience as an individual "
        "contributor, no people-management or direct-report experience. Decide "
        "whether this candidate should apply to the role described below. A role "
        "demanding far more experience or core responsibilities the candidate "
        "lacks (e.g. managing people) should NOT be recommended."
    ),
    context=_doc(_EM_NEEDLE, target_words=2400, position=0.5),
    schema={
        "type": "object",
        "properties": {
            "recommend_apply": {"type": "boolean"},
            "top_reason": {"type": "string"},
            "missing_must_haves": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["recommend_apply", "top_reason", "missing_must_haves"],
    },
    checks=[
        ("declines (recommend_apply=false)",
         lambda p: isinstance(p, dict) and p.get("recommend_apply") is False),
        ("flags a gap",
         lambda p: isinstance(p, dict) and isinstance(p.get("missing_must_haves"), list)
                   and len(p["missing_must_haves"]) >= 1),
    ],
)


TASKS: dict[str, JsonTask] = {
    t.key: t for t in (JD_EXTRACT, NEEDLE_RECALL, DECLINE_GUARD)
}
