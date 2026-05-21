# Jobhunt project reference

`~/Apps/jobhunt` — local-first Python/uv CLI for personal job-search
automation. Authoritative docs live in the repo (`README.md`, `AGENTS.md`,
`PLAN.md`); defer to them for anything not on this page.

## What it does

Pulls jobs from public ATS APIs (Greenhouse, Lever, Ashby, SmartRecruiters,
Adzuna CA, Job Bank RSS), scores them against Casey's verified profile,
tailors resume + cover via local Ollama, and assists Playwright autofill.
**Casey submits — never the bot.**

## Stack non-negotiables

- Python 3.12+, `uv` only (never `pip` / `poetry`).
- `typer` CLI, `httpx`, `pydantic` v2, stdlib `sqlite3` + plain SQL
  migrations (no ORM), `playwright`, `pytest` + `pytest-asyncio`,
  `ruff` + `mypy --strict`.
- LLM runtime is **local Ollama only** (`qwen-custom:latest` /
  `qwen3.5:9b`). No cloud LLMs at runtime, ever.
- All LLM calls go through `jobhunt.gateway` with JSON-schema enforcement.

## Hard rules — refuse if asked to violate

- No LinkedIn, Indeed, or Glassdoor scraping. Public ATS APIs only.
- Never auto-submit applications.
- Never auto-create employer accounts.
- Respect `robots.txt` for non-API HTTP fetches.
- No ORM. No web framework. No cloud LLM in runtime.
- No bypassing the gateway for LLM calls.
- Do not commit anything in `data/`, `~/.config/jobhunt/`, or `*.secret.*`.

## User-facing commands (9)

`convert-resume`, `scan`, `apply`, `add`, `answer`, `interview-prep`,
`list`, `analyze`, `discover slugs`. Hidden: `db`, `config` (except
`config seed`).

## Profile guard

`scan`, `list`, and `apply` call `ensure_profile(cfg)` — exit if
`kb/profile/verified.json` is missing.

## Honesty enforcement

`pipeline.tailor._enforce_no_fabrication` rejects any role/employer/date
divergence from `verified.json`, any unverified skill, and any Familiar
skill placed in a non-Familiar category. Audit verdicts: `ship` / `revise`
/ `block`. Adding tailoring capabilities must keep these green.

## When asked jobhunt questions

If the question touches phase numbers, validator internals, scoring
rubric, or audit logic — defer to `AGENTS.md` in the repo. This page
covers durable rules, not implementation details.
