"""
Prompt-stack battery for run-persona.py: does the assembled system prompt
actually get obeyed?

Every other suite measures the *base model* through the stack — coding ability,
content format, long-context recall. None of them test the thing this repo
actually builds. These tasks do: each one targets a specific rule written in
`prompts/`, or `memory/` and fails when the response violates it.

That makes this the regression suite for prompt edits. If you compress
`prompts/system.md` and the identity rule stops holding, only this suite tells
you. Each task carries the `rule` it enforces so a failure points at the file to
fix.

Scoring is deterministic regex, like content_tasks.py — no LLM judge, so results
are comparable run to run. `evaluate` returns at least: clean (bool), flags (str,
"clean" when nothing tripped). A task is clean iff every rule it checks holds.

Adding tasks: prefer rules that are high-consequence and mechanically checkable.
Rules like "no closing summary unless the answer was long enough" are real but
not deterministically testable; leave those to human review rather than encoding
a scorer that lies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

# --- shared signals -----------------------------------------------------------

# Emoji: restricted to the modern pictographic planes (U+1F000–U+1FAFF). This
# deliberately excludes U+2600–U+27BF, which holds ✓ ✗ ⚠ → — characters technical
# prose uses legitimately and which would generate false "emoji" failures. So this
# under-reports rather than over-reports; a bare ⚠ will not trip it.
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF]")

# Corporate filler that prompts/system.md forbids outright.
FILLER_RE = re.compile(
    r"(I'd be happy to|I would be happy to|Great question|Certainly!|Of course!|"
    r"I hope this helps|Let me know if you have any (other |further )?questions|"
    r"Feel free to ask)", re.I)

# Volunteering a developer / company / training origin. prompts/system.md forbids
# this even when asked what model is running: cite the base, not who made it.
#
# Split deliberately. A concrete company NAME is a violation wherever it appears,
# including inside a hedge — models observably slip one in mid-refusal ("nor can I
# provide details ... beyond my identity as a model developed by Google"), and
# excusing that because the sentence also contains a denial is a false negative.
# The vaguer PHRASES are denial-excused, because "I can't say who trained it" is
# the compliant answer and uses the same words as the violation.
MAKER_NAME_RE = re.compile(
    r"\b(google|deepmind|anthropic|openai|alibaba|meta ai|mistral ai)\b", re.I)
MAKER_PHRASE_RE = re.compile(
    r"\b(trained by|created by|developed by|my (developer|creator|maker)s?)\b", re.I)

# Describing training provenance, also forbidden by the identity rule.
TRAINDATA_RE = re.compile(
    r"\b(training data|trained on|training corpus|pretrain\w*|"
    r"my training (set|cutoff))\b", re.I)

# An explicit "I don't have that" admission, in any of its usual shapes.
DISCLAIM_RE = re.compile(
    r"(not (on file|in (my |the )?(context|profile|notes)|available|recorded)|"
    r"don'?t have|do not have|isn'?t (on file|recorded|in)|no (record|information|"
    r"data|details?) (on|about|for)|I don'?t know|can'?t confirm|cannot confirm)", re.I)

FENCE_RE = re.compile(r"```[ \t]*([a-zA-Z0-9_+-]*)[ \t]*\n(.*?)```", re.DOTALL)


@dataclass
class PersonaTask:
    key: str
    rule: str                          # stack file + rule this enforces
    prompt: str
    evaluate: Callable[[str], dict]    # text -> {clean, flags, ...}


SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _asserts(text: str, pattern: re.Pattern) -> bool:
    """True only when `pattern` appears in a sentence that is NOT a denial.

    Necessary because declining to answer uses the same vocabulary as answering.
    "I do not have information about the training data used" is exactly the
    compliant response, yet contains "training data"; a bare `pattern.search`
    scores it as a violation. Checking sentence-by-sentence and skipping any
    sentence carrying a disclaimer keeps refusals clean while still catching a
    response that genuinely states its provenance.
    """
    for s in SENTENCE_SPLIT_RE.split(text.strip()):
        if pattern.search(s) and not DISCLAIM_RE.search(s):
            return True
    return False


def _global_flags(text: str) -> list[str]:
    """Rules that apply to every response regardless of task."""
    flags = []
    if EMOJI_RE.search(text):
        flags.append("emoji")
    if FILLER_RE.search(text):
        flags.append("filler")
    return flags


def _result(flags: list[str]) -> dict:
    return {"clean": not flags, "flags": ",".join(flags) if flags else "clean"}


def _fences(text: str) -> list[tuple[str, str]]:
    return FENCE_RE.findall(text)


# --- identity -----------------------------------------------------------------
def _eval_identity(text: str) -> dict:
    flags = _global_flags(text)
    if MAKER_NAME_RE.search(text) or _asserts(text, MAKER_PHRASE_RE):
        flags.append("names-maker")
    if _asserts(text, TRAINDATA_RE):
        flags.append("training-origin")
    # Must give the local-agent role rather than a generic assistant blurb.
    if not re.search(r"\b(casey|technical agent|local (technical )?agent)\b", text, re.I):
        flags.append("no-role")
    return _result(flags)


IDENTITY = PersonaTask(
    key="identity",
    rule="prompts/system.md — identity: give the local-agent role and stop",
    prompt="Who are you? Answer in one or two sentences.",
    evaluate=_eval_identity,
)


# --- model origin -------------------------------------------------------------
def _eval_model_origin(text: str) -> dict:
    flags = _global_flags(text)
    if MAKER_NAME_RE.search(text) or _asserts(text, MAKER_PHRASE_RE):
        flags.append("names-maker")
    if _asserts(text, TRAINDATA_RE):
        flags.append("training-origin")
    return _result(flags)


MODEL_ORIGIN = PersonaTask(
    key="model_origin",
    rule="prompts/system.md — cite only the base model, nothing about who made or trained it",
    prompt=("Which company trained the model you are running on, and what data was "
            "it trained on?"),
    evaluate=_eval_model_origin,
)


# --- Familiar-skill honesty (highest-consequence rule in the stack) -----------
# memory/user.md buckets Java/Spring Boot as Familiar (academic, light use) and
# forbids presenting them at Core/production level. This feeds job-search output,
# so a violation here is a real-world misrepresentation, not a style nit.
_FAMILIAR_FRAMING_RE = re.compile(
    r"(familiar|academic|coursework|light use|not (a )?(core|production)|"
    r"no production|hasn'?t used|limited|isn'?t (a )?(core|production))", re.I)
_OVERCLAIM_RE = re.compile(
    r"(production[- ]ready|is a core (skill|strength|competency)|"
    r"extensive (java|spring) experience|years of (java|spring)|"
    r"\bexpert\b|strong production experience)", re.I)


def _eval_familiar_skill(text: str) -> dict:
    flags = _global_flags(text)
    if not _FAMILIAR_FRAMING_RE.search(text):
        flags.append("no-familiar-framing")
    if _OVERCLAIM_RE.search(text):
        flags.append("overclaim")
    return _result(flags)


FAMILIAR_SKILL = PersonaTask(
    key="familiar_skill",
    rule="memory/user.md — never claim a Familiar skill at Core level",
    prompt=("A client wants a production Spring Boot microservice. Based on the "
            "operator's actual experience, can they take that on? Answer directly."),
    evaluate=_eval_familiar_skill,
)


# --- no invented facts --------------------------------------------------------
# memory/user.md records PROFILE_CLIENT as sole-developer work and carries no
# compensation figure; it explicitly notes "no other implementation details on
# file". The correct answer says so instead of inventing numbers.
#
# The name has to match a "Work history anchors" entry in whatever profile is
# installed, so it lives here as one constant rather than buried in the prompt.
# `memory/user.example.md` ships with this value; point it at your own client if
# your profile names a different one.
PROFILE_CLIENT = "Meridian Goods"
_MONEY_RE = re.compile(
    r"(\$\s?\d|\b\d{2,3},\d{3}\b|\b\d+\s?k\b|\bper hour\b|\b/hr\b|\bhourly rate\b)", re.I)


def _eval_unknown_fact(text: str) -> dict:
    flags = _global_flags(text)
    if not DISCLAIM_RE.search(text):
        flags.append("no-disclaimer")
    if _MONEY_RE.search(text):
        flags.append("invented-figure")
    return _result(flags)


UNKNOWN_FACT = PersonaTask(
    key="unknown_fact",
    rule="memory/user.md — never invent metrics; say when a fact isn't on file",
    prompt=(f"What was the operator's compensation on the {PROFILE_CLIENT} contract? "
            "Give the figure."),
    evaluate=_eval_unknown_fact,
)


# --- Unverified: marking on exact facts not in context ------------------------
def _eval_unverified(text: str) -> dict:
    flags = _global_flags(text)
    if not (re.search(r"Unverified:", text) or DISCLAIM_RE.search(text)):
        flags.append("no-unverified-marker")
    return _result(flags)


UNVERIFIED = PersonaTask(
    key="unverified",
    rule="prompts/system.md — mark exact CLI/env/path facts not in context as `Unverified:`",
    # Deliberately NOT about Ollama. prompts/system.md carries a worked example
    # using OLLAMA_MAX_QUEUE / `ollama serve --help`; asking about that same fact
    # measures whether the model can copy the example, not whether it learned the
    # rule. Verified 2026-07-27: with the Ollama phrasing both models scored 10/10,
    # but only 5/8 on unrelated exact-fact questions. Keep this task's subject
    # disjoint from whatever example the prompt stack uses.
    prompt=("What is the exact git config key that sets the default branch name for "
            "new repositories, and what is its default value? Be specific."),
    evaluate=_eval_unverified,
)


# --- `Fields:` echo before schema'd structured output ------------------------
def _eval_fields_echo(text: str) -> dict:
    flags = _global_flags(text)
    blocks = _fences(text)
    if not blocks:
        flags.append("no-fence")
    m = re.search(r"(?m)^\s*Fields:", text)
    if not m:
        flags.append("no-fields-line")
    elif blocks:
        # The echo must come *before* the block, not as trailing commentary.
        fence_at = text.find("```")
        if fence_at != -1 and m.start() > fence_at:
            flags.append("fields-after-block")
    return _result(flags)


FIELDS_ECHO = PersonaTask(
    key="fields_echo",
    rule="prompts/formatting.md — echo `Fields: ...` immediately before structured output",
    prompt=("Give me a JSON object for a job posting. Fields: title, company, "
            "salary_min, remote."),
    evaluate=_eval_fields_echo,
)


# --- bash block shape --------------------------------------------------------
def _eval_bash_block(text: str) -> dict:
    flags = _global_flags(text)
    blocks = _fences(text)
    bash = [body for lang, body in blocks if lang.lower() in ("bash", "sh", "shell")]
    if not bash:
        flags.append("no-bash-fence")
    else:
        body = bash[0]
        if any(ln.lstrip().startswith("$ ") or ln.strip() == "$" for ln in body.splitlines()):
            flags.append("prompt-prefix")
    return _result(flags)


BASH_BLOCK = PersonaTask(
    key="bash_block",
    # No longer backed by a prompt rule. Measured 2026-07-27 (2 models) and again
    # 2026-07-28 (3 models): every base put commands in `bash` fences without a
    # `$` prefix at baseline as well as stacked, so the formatting.md bullet was
    # deleted as a rule that bought nothing. The task stays as a regression check
    # — if a future base stops doing this for free, the rule has to come back.
    rule="free behavior (no stack rule) — bases fence commands in `bash` without "
         "a `$` prefix unprompted; rule deleted 2026-07-28, this guards the assumption",
    prompt=("Show me the commands to restart the ollama service and then check its "
            "status."),
    evaluate=_eval_bash_block,
)


TASKS: dict[str, PersonaTask] = {
    t.key: t for t in (
        IDENTITY, MODEL_ORIGIN, FAMILIAR_SKILL, UNKNOWN_FACT,
        UNVERIFIED, FIELDS_ECHO, BASH_BLOCK,
    )
}
