#!/usr/bin/env python3
"""
Promote the newest run results in eval/runs/<UTC>/ into the README leaderboard.

The problem this exists to prevent: `eval/runs/` is gitignored and the README
tables were hand-maintained, so the tables kept describing bases that were no
longer installed. That is exactly how the 2026-06-14 scores survived two base
swaps while still reading as current. A table nobody can regenerate is a table
nobody can trust.

This regenerates the block between the BENCH markers in README.md from whatever
`eval/runs/` actually holds, stamping the run directory and model set each number
came from. Anything outside the markers is untouched.

Usage:
  ./eval/promote.py                 # newest run per suite -> README.md
  ./eval/promote.py --dry-run       # print the block, write nothing
  ./eval/promote.py --check         # exit 1 if README is stale (for CI/hooks)
  ./eval/promote.py --run 20260728T182025Z   # pin one run stamp where present

Only suites that have a run are emitted; a suite with no results says so rather
than silently keeping an old row. Sources are parsed from each summary.md — the
`## 🏆` headline and the first `| Rank | Model |` table, which every runner
writes.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS = REPO_ROOT / "eval" / "runs"
README = REPO_ROOT / "README.md"

START = "<!-- BENCH:START -->"
END = "<!-- BENCH:END -->"

WINNER_RE = re.compile(r"^##\s*🏆\s*[^:]+:\s*`([^`]+)`\s*—\s*(.+?)\s*$", re.M)
CLOSE_RE = re.compile(r"^⚠\s*\*\*Close result:\*\*", re.M)

# suite -> (display label, preferred metric columns in priority order).
# The first column present in a summary's table wins, so a runner can rename a
# secondary column without breaking promotion.
SUITES: dict[str, tuple[str, tuple[str, ...]]] = {
    "speed":   ("Speed",               ("Gen tok/s",)),
    "code":    ("Coding",              ("Passed", "Pass rate")),
    "content": ("Content",             ("Clean", "Clean rate")),
    "learn":   ("Learning",            ("Teach /10",)),
    "tutor":   ("Tutor (leak-gated)",  ("Teach /10",)),
    "json":    ("JSON / long-context", ("Score",)),
    "persona": ("Prompt stack",        ("Clean rate",)),
    "tools":   ("Tool calling",        ("Passed", "Pass rate")),
}

# Extra context column worth carrying into the README next to the headline
# metric — the number that changes the decision when the headline is a tie.
SECONDARY: dict[str, str] = {
    "speed": "GPU/CPU split",
    "tools": "Pass rate",
    "learn": "Code pass",
    "tutor": "Leaks",
    "json": "Avg s",
}


def parse_table(text: str) -> list[dict[str, str]]:
    """Parse the first `| Rank | Model | ... |` table into row dicts."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("| Rank | Model |"):
            header = [c.strip() for c in line.strip("|").split("|")]
            rows = []
            for row in lines[i + 2:]:  # +2 skips the |---| separator
                if not row.startswith("|"):
                    break
                cells = [c.strip() for c in row.strip("|").split("|")]
                if len(cells) == len(header):
                    rows.append(dict(zip(header, cells)))
            return rows
    return []


def newest_runs(pin: str | None) -> dict[str, tuple[str, Path]]:
    """suite -> (run stamp, summary path), newest run per suite.

    Run directories are UTC stamps, so lexical sort is chronological. A suite is
    only reported if that run actually produced a summary for it.
    """
    found: dict[str, tuple[str, Path]] = {}
    if not RUNS.is_dir():
        return found
    for run in sorted((p for p in RUNS.iterdir() if p.is_dir()), key=lambda p: p.name):
        if pin and run.name != pin:
            continue
        for suite in SUITES:
            s = run / suite / "summary.md"
            if s.is_file():
                found[suite] = (run.name, s)
    return found


def metric(row: dict[str, str], prefs: tuple[str, ...]) -> str:
    for col in prefs:
        if row.get(col):
            return row[col]
    # Fall back to the first column after Rank/Model rather than dropping the row.
    rest = [k for k in row if k not in ("Rank", "Model")]
    return row[rest[0]] if rest else "—"


def build_block(pin: str | None) -> tuple[str, list[str]]:
    """Return (markdown block, warnings)."""
    runs = newest_runs(pin)
    warnings: list[str] = []
    if not runs:
        where = f"run {pin}" if pin else str(RUNS)
        return (f"{START}\n\n_No benchmark results found in {where}. "
                f"Run `./eval/run-profile.py standard --models gemma qwen lite`, "
                f"then `./eval/promote.py`._\n\n{END}"), ["no runs found"]

    # Collect the model set across suites so the table columns are stable.
    models: list[str] = []
    parsed: dict[str, dict] = {}
    for suite, (stamp, path) in runs.items():
        text = path.read_text(encoding="utf-8")
        rows = parse_table(text)
        if not rows:
            warnings.append(f"{suite}: no rankable table in {path.name}")
            continue
        m = WINNER_RE.search(text)
        entry = {
            "stamp": stamp,
            "winner": m.group(1) if m else "—",
            "headline": m.group(2) if m else "",
            "close": bool(CLOSE_RE.search(text)),
            "rows": {r["Model"].strip("`"): r for r in rows},
        }
        parsed[suite] = entry
        for name in entry["rows"]:
            if name not in models:
                models.append(name)

    # Staleness guard. "Newest per suite" silently mixes a fresh speed run with a
    # learn run from two base swaps ago — which is the exact drift this script was
    # written to kill, reintroduced one level down. A suite whose run predates the
    # newest run's DAY is called out inline rather than blended into the table.
    stamps = sorted({e["stamp"] for e in parsed.values()})
    newest_day = stamps[-1][:8]
    for suite, e in parsed.items():
        e["stale"] = e["stamp"][:8] < newest_day
    stale = sorted(s for s, e in parsed.items() if e["stale"])
    if stale:
        warnings.append(
            f"stale suites (older than {newest_day}): {', '.join(stale)} — "
            f"rerun ./eval/run-profile.py standard so every row shares a lineup")

    L = [START, ""]
    L += [f"_Generated by `./eval/promote.py` from "
          f"{'run ' + stamps[0] if len(stamps) == 1 else f'{len(stamps)} runs, {stamps[0]}–{stamps[-1]}'} "
          f"(`eval/runs/`). Do not hand-edit: re-run the script._", ""]
    if stale:
        L += [f"> ⚠ **Mixed run dates.** {len(stale)} suite(s) below "
              f"(_{', '.join(SUITES[s][0] for s in stale)}_) come from a run older "
              f"than {newest_day} and may describe a different base or lineup. "
              f"Rows marked ⚠ are not comparable with the rest; rerun "
              f"`./eval/run-profile.py standard` to refresh them.", ""]
    L += ["| Suite | Winner | " + " | ".join(f"`{m}`" for m in models) + " |",
          "|---|---|" + "---|" * len(models)]

    for suite, (label, prefs) in SUITES.items():
        e = parsed.get(suite)
        if not e:
            L.append(f"| {label} | _not run_ | " + " | ".join("—" for _ in models) + " |")
            continue
        cells = []
        for name in models:
            row = e["rows"].get(name)
            if not row:
                cells.append("—")
                continue
            val = metric(row, prefs)
            sec = SECONDARY.get(suite)
            if sec and row.get(sec):
                val = f"{val}, {row[sec]}"
            cells.append(val)
        winner = "tie" if e["close"] else f"`{e['winner']}`"
        if e["stale"]:
            winner = f"⚠ {winner}"
        L.append(f"| {label} | {winner} | " + " | ".join(cells) + " |")

    L += ["", "Winner is `tie` where the runner flagged the margin as within its "
          "close-result threshold — those rows should break on speed, not the "
          "headline metric. Per-suite run directories:", ""]
    for suite in SUITES:
        e = parsed.get(suite)
        if e:
            L.append(f"- {SUITES[suite][0]}: `eval/runs/{e['stamp']}/{suite}/summary.md`"
                     + (" ⚠ older run" if e["stale"] else "")
                     + (f" — {e['headline']}" if e["headline"] else ""))
    L += ["", END]
    return "\n".join(L), warnings


def splice(block: str) -> str:
    text = README.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit(
            f"README.md has no {START} / {END} markers — add them around the "
            f"leaderboard block so this script knows what to replace.")
    head, _, rest = text.partition(START)
    _, _, tail = rest.partition(END)
    return head + block + tail


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the block, write nothing")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if README.md does not match the current results")
    ap.add_argument("--run", default=None, metavar="STAMP",
                    help="pin to one eval/runs/<STAMP> instead of the newest per suite")
    args = ap.parse_args()

    block, warnings = build_block(args.run)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)

    if args.dry_run:
        print(block)
        return 0

    updated = splice(block)
    current = README.read_text(encoding="utf-8")
    if args.check:
        if updated != current:
            print("README.md leaderboard is stale — run ./eval/promote.py", file=sys.stderr)
            return 1
        print("README.md leaderboard is up to date")
        return 0
    if updated == current:
        print("README.md leaderboard already up to date")
        return 0
    README.write_text(updated, encoding="utf-8")
    print(f"README.md leaderboard updated from {RUNS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
