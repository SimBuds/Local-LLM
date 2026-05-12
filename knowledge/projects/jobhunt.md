# Jobhunt Project Reference

Casey's `~/Apps/jobhunt` repo is an active local-first CLI for job search
automation. Use this reference when helping with that project, but prefer the
repo's own `README.md`, `CLAUDE.md`, `PLAN.md`, and tests when specific details
matter.

## Purpose

- Pull jobs from public ATS APIs and selected public sources.
- Scope results to the GTA plus Remote-Canada postings.
- Score jobs against Casey's parsed baseline resume using local Ollama models.
- Draft tailored resumes and cover letters.
- Assist with browser autofill through Playwright.
- Keep Casey in the loop: the tool fills forms but never submits applications.

## Non-goals

- No LinkedIn, Indeed, or Glassdoor scraping.
- No bot-submitted applications.
- No auto-account creation on employer sites.
- No stored employer credentials.
- No cloud LLM calls in the runtime path.

## Stack

- Python 3.12+
- `uv` for dependency management
- Typer CLI
- httpx for async HTTP
- Pydantic v2
- SQLite with plain SQL migrations; no ORM
- Playwright for browser automation
- pytest, pytest-asyncio, ruff, and mypy strict
- Local Ollama at `http://localhost:11434`

## Commands

- `jobhunt convert-resume`: parse the baseline resume into `kb/profile/`.
- `jobhunt scan`: ingest and score jobs.
- `jobhunt apply <job-id>`: tailor docs and autofill; Casey submits manually.
- `jobhunt apply --top N`: pick top unapplied jobs, capped at 10.
- `jobhunt apply --best`: interactive pick from top candidates.
- `jobhunt apply --url <URL>`: one-off manual job URL flow.
- `jobhunt list`: pipeline view and weekly tracking.

`db` and `config` are setup/internal commands.

## Agent Rules For This Repo

- Use `uv`, not `pip` or Poetry.
- Inspect relevant files before changing behavior.
- Keep `cli.py` focused on Typer wiring; place logic in command or domain modules.
- Use specific project errors from `jobhunt.errors`; avoid bare `Exception`.
- Keep secrets in `~/.config/jobhunt/secrets.toml` or environment variables.
- Never commit API keys, resume-private data, or generated secrets.
- Route LLM calls through `jobhunt.gateway`; do not create direct Ollama clients elsewhere.
- Keep long prompts in `kb/prompts/`, not inline Python strings.
- Preserve no-fabrication constraints in resume and cover-letter generation.
- Keep browser automation human-in-the-loop and never click Submit.
