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


def _doc_multi(needles: list[str], target_words: int = 2800) -> str:
    """Bury several `needles` at evenly spread positions in filler, in order.

    Used by tasks that need more than one planted fact — a later correction that
    overrides an earlier value, or several records the model must collect. Spread
    is deterministic so extraction accuracy stays comparable run to run.
    """
    n = len(needles)
    positions = [(i + 1) / (n + 1) for i in range(n)]  # spread, none at the edges
    out: list[str] = []
    count = ni = i = 0
    while count < target_words:
        while ni < n and count >= target_words * positions[ni]:
            out.append(needles[ni])
            count += len(needles[ni].split())
            ni += 1
        para = _FILLER[i % len(_FILLER)]
        out.append(para)
        count += len(para.split())
        i += 1
    while ni < n:  # flush any needle whose position rounded past the word budget
        out.append(needles[ni])
        ni += 1
    return "\n\n".join(out)


@dataclass
class JsonTask:
    key: str
    instruction: str           # task + (where relevant) candidate profile
    needles: list[str]         # planted fact(s); >1 spreads them across the doc
    schema: dict[str, Any]     # Ollama `format` schema
    checks: list[tuple[str, Callable[[Any], bool]]]  # content-correctness probes
    base_words: int = 2600     # normal-pressure document length (run-json scales this)
    default_position: float = 0.55  # single-needle placement (ignored if multi-needle)


def build_context(task: "JsonTask", target_words: int,
                  position: float | None = None) -> str:
    """Build a task's long document at a chosen length and needle position.

    `target_words` lets run-json.py dial context *pressure* (longer doc = harder
    long-context recall); `position` overrides single-needle placement (None →
    the task default) so the needle can be put near the start, middle, or end.
    Multi-needle tasks ignore `position` and keep their deterministic spread.
    """
    if len(task.needles) == 1:
        pos = task.default_position if position is None else position
        return _doc(task.needles[0], target_words=target_words, position=pos)
    return _doc_multi(task.needles, target_words=target_words)


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
    needles=[_JD_NEEDLE],
    base_words=2600, default_position=0.55,
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
    needles=[_POLICY_NEEDLE],
    base_words=3200, default_position=0.6,
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
    needles=[_EM_NEEDLE],
    base_words=2400, default_position=0.5,
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


# --- task 4: conflicting facts (a later correction overrides an earlier value) -
_SALARY_ORIGINAL = (
    "COMPENSATION: The posted salary band for this role is 95,000 to 115,000 CAD "
    "per year, depending on experience, plus an annual performance bonus."
)
_SALARY_CORRECTION = (
    "CORRECTION (posting updated): An earlier version of this listing showed an "
    "incorrect salary band. The correct annual salary band for this role is "
    "120,000 to 140,000 CAD. Please disregard any earlier figure in this document."
)

CONFLICTING_CORRECTION = JsonTask(
    key="conflicting_correction",
    instruction=(
        "Extract the salary band for this role from the posting below. The "
        "document may contain an outdated figure that is later corrected; if so, "
        "use the corrected values, not the earlier ones."
    ),
    needles=[_SALARY_ORIGINAL, _SALARY_CORRECTION],
    base_words=2800,
    schema={
        "type": "object",
        "properties": {
            "salary_min_cad": {"type": "integer"},
            "salary_max_cad": {"type": "integer"},
        },
        "required": ["salary_min_cad", "salary_max_cad"],
    },
    checks=[
        ("uses corrected min=120000",
         lambda p: isinstance(p, dict) and p.get("salary_min_cad") == 120000),
        ("uses corrected max=140000",
         lambda p: isinstance(p, dict) and p.get("salary_max_cad") == 140000),
    ],
)

# --- task 5: strict enum classification (map prose to exact enum values) -------
_ENUM_NEEDLE = (
    "EMPLOYMENT DETAILS: This is a permanent, salaried position with standard "
    "40-hour weeks and a full benefits package; it is not a fixed-term or agency "
    "engagement. Employees on this team come into our Toronto office three days "
    "per week, with the remaining days worked from home."
)

ENUM_CLASSIFY = JsonTask(
    key="enum_classify",
    instruction=(
        "Classify the employment terms described in the posting below. Choose the "
        "single best value for each field strictly from its allowed enum. Map the "
        "prose to the closest enum value; do not output any value outside the enum."
    ),
    needles=[_ENUM_NEEDLE],
    base_words=2600, default_position=0.5,
    schema={
        "type": "object",
        "properties": {
            "employment_type": {
                "type": "string",
                "enum": ["full_time", "part_time", "contract", "internship", "temporary"],
            },
            "work_arrangement": {
                "type": "string",
                "enum": ["onsite", "hybrid", "remote"],
            },
        },
        "required": ["employment_type", "work_arrangement"],
    },
    checks=[
        ("employment_type=full_time",
         lambda p: isinstance(p, dict) and p.get("employment_type") == "full_time"),
        ("work_arrangement=hybrid",
         lambda p: isinstance(p, dict) and p.get("work_arrangement") == "hybrid"),
    ],
)

# --- task 6: multi-record extraction (collect every record, not just one) ------
_TEAM_NEEDLE = (
    "THE TEAM: You will join a pod of three engineers. Priya Nair is the Staff "
    "Engineer and overall tech lead for the pod. Marco Rossi is a Senior Backend "
    "Engineer focused on the payments service. Lena Okafor is a Frontend Engineer "
    "who owns the customer dashboard. The pod reports to the Director of "
    "Engineering."
)


def _members_named(parsed: Any, names: list[str]) -> bool:
    if not isinstance(parsed, dict) or not isinstance(parsed.get("members"), list):
        return False
    joined = " ".join(
        str(m.get("name", "")) for m in parsed["members"] if isinstance(m, dict)
    ).lower()
    return all(name.lower() in joined for name in names)


MULTI_EXTRACT = JsonTask(
    key="multi_extract",
    instruction=(
        "Extract every named team member described in the posting below into the "
        "members array, each with their name and role exactly as stated. Include "
        "all of them; do not summarize or drop any."
    ),
    needles=[_TEAM_NEEDLE],
    base_words=2800, default_position=0.55,
    schema={
        "type": "object",
        "properties": {
            "members": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "role": {"type": "string"},
                    },
                    "required": ["name", "role"],
                },
            },
        },
        "required": ["members"],
    },
    checks=[
        ("exactly 3 members",
         lambda p: isinstance(p, dict) and isinstance(p.get("members"), list)
                   and len(p["members"]) == 3),
        ("all three names present",
         lambda p: _members_named(p, ["Priya Nair", "Marco Rossi", "Lena Okafor"])),
    ],
)

# --- task 7: do-not-infer (return null for an unstated field, ignore distractor) -
_NOINFER_NEEDLE = (
    "ELIGIBILITY: All applicants must be legally eligible to work in Canada. This "
    "role supports government and defense-sector clients, so professionalism and "
    "discretion are expected at all times. We do not sponsor work visas for this "
    "position."
)

NO_INFER = JsonTask(
    key="no_infer",
    instruction=(
        "From the posting below, extract two fields. For security_clearance, "
        "report the clearance level the posting REQUIRES — but only if the "
        "posting explicitly states a clearance requirement. If it does not, "
        "return null. Do NOT infer a clearance requirement from the industry, "
        "the clients, or the seniority. For work_authorization, report the work "
        "eligibility requirement the posting states."
    ),
    needles=[_NOINFER_NEEDLE],
    base_words=2600, default_position=0.55,
    schema={
        "type": "object",
        "properties": {
            "security_clearance": {"type": ["string", "null"]},
            "work_authorization": {"type": ["string", "null"]},
        },
        "required": ["security_clearance", "work_authorization"],
    },
    checks=[
        ("clearance not inferred (null)",
         lambda p: isinstance(p, dict) and p.get("security_clearance") is None),
        ("work_authorization captured (non-empty)",
         lambda p: isinstance(p, dict) and isinstance(p.get("work_authorization"), str)
                   and p["work_authorization"].strip() != ""),
    ],
)


TASKS: dict[str, JsonTask] = {
    t.key: t for t in (
        JD_EXTRACT, NEEDLE_RECALL, DECLINE_GUARD,
        CONFLICTING_CORRECTION, ENUM_CLASSIFY, MULTI_EXTRACT, NO_INFER,
    )
}
