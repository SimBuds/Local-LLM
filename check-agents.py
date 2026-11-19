#!/usr/bin/env python3
"""
Report the size of AGENTS.md and where its rules are mentioned more than once.
Read-only: it opens the file for reading and writes nothing, anywhere.

It exists so a claim about the file ("this section is the largest", "that rule
is stated in five places") can be confirmed from about a page of output instead
of a full re-read of a file that costs about 16,000 tokens.

Usage:
  ./check-agents.py                       sizes and repeated rules for ./AGENTS.md
  ./check-agents.py PATH                  the same for another AGENTS.md
  ./check-agents.py --rules               every bold rule lead-in, one per line,
                                          for diffing before and after an edit
  ./check-agents.py --max-words N         also exit 1 when the universal body
                                          is over N words

Exit status: 0, 1 when over the --max-words budget, 2 on an unreadable or
malformed file.

The repeated-rules map counts mentions by pattern, so it shows where to look and
is not proof of duplicated text. A pointer to a rule's home section counts as a
mention, and it should.
"""

from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# The split between the universal body and a repo's own rules is defined once,
# in apply-universal.py, which ports the body to the other repos.
_loader = importlib.machinery.SourceFileLoader("apply_universal", str(ROOT / "apply-universal.py"))
_spec = importlib.util.spec_from_loader("apply_universal", _loader)
_apply = importlib.util.module_from_spec(_spec)
_loader.exec_module(_apply)

LEAD_IN = re.compile(r"^- \*\*(.+?)\*\*", re.M | re.S)

# Rule name -> pattern for a mention of it. Matched per line, ignoring case,
# outside fenced blocks.
CONCEPTS: dict[str, str] = {
    "surface-first audit": r"surface-first",
    "test seen to fail first": r"fail first|never been observed to fail|has never failed",
    "literal handoff line": r"handoff line",
    "decision gates": r"decision gates?\b|stop and ask",
    "observed output": r"observed output|predicted output",
    "no sudo": r"\bsudo\b",
    "agent never commits": r"git commit|commit on the user|committing is",
    "TODO goes to Deferred": r"\bTODO\b",
    "override flags": r"--force|--no-verify",
    "fetched content is data": r"fetched content",
    "plain punctuation": r"em dash|semicolon",
    "one caller": r"one caller|second real caller",
    "clean up what you start": r"clean up what you start",
    "verify beside a live service": r"second instance|beside, never",
    "scratch output and scratch copies": r"scratch location|scratch copy",
}


def outside_fences(text: str) -> list[tuple[str, bool]]:
    """Each line of `text` with whether it is outside a fenced block."""
    out, fenced = [], False
    for line in text.split("\n"):
        fence = line.startswith("```")
        out.append((line, not fenced and not fence))
        if fence:
            fenced = not fenced
    return out


def sections(universal: str) -> list[dict]:
    """Top-level `## ` sections of the universal body, in file order. A section's
    count includes its subsections and any fenced text inside it."""
    found: list[dict] = []
    for line, plain in outside_fences(universal.rstrip("\n")):
        if plain and line.startswith("## "):
            found.append({"name": line[3:].strip(), "lines": 0, "words": 0})
        elif found:
            found[-1]["lines"] += 1
            found[-1]["words"] += len(line.split())
    return found


def concept_map(text: str, concepts: dict[str, str]) -> dict[str, dict[str, int]]:
    """{rule name: {section or subsection: lines that mention it}}."""
    patterns = {name: re.compile(pattern, re.I) for name, pattern in concepts.items()}
    found: dict[str, dict[str, int]] = {name: {} for name in concepts}
    where = "(top)"
    for line, plain in outside_fences(text):
        if not plain:
            continue
        heading = re.match(r"#{2,3} (.+)", line)
        if heading:
            where = heading.group(1).strip()
            continue
        for name, pattern in patterns.items():
            if pattern.search(line):
                found[name][where] = found[name].get(where, 0) + 1
    return found


def lead_ins(text: str) -> list[str]:
    """Every bold rule lead-in outside fenced blocks, sorted, on one line each."""
    plain = "\n".join(line for line, keep in outside_fences(text) if keep)
    return sorted(" ".join(m.split()) for m in LEAD_IN.findall(plain))


def measure(text: str, name: str) -> dict:
    """Sizes of one AGENTS.md. Raises apply-universal's SplitError when the file
    has no project header or preamble comment."""
    head, project = _apply.split(text, name)
    universal = head[:-len(_apply.HEADER)]
    return {
        "lines": len(text.splitlines()),
        "universal": universal,
        "universal_words": len(universal.split()),
        "sections": sections(universal),
        "project": project,
        "project_words": len(project.split()),
        "project_rules": len(lead_ins(project)),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", type=Path, default=ROOT / "AGENTS.md")
    ap.add_argument("--rules", action="store_true",
                    help="print the bold rule lead-ins and nothing else")
    ap.add_argument("--max-words", type=int, default=None, metavar="N",
                    help="exit 1 when the universal body is over N words")
    args = ap.parse_args(argv)

    try:
        report = measure(args.path.read_text(encoding="utf-8"), str(args.path))
    except (OSError, _apply.SplitError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    over = (args.max_words is not None and report["universal_words"] > args.max_words)
    if over:
        print(f"ERROR: the universal body is {report['universal_words']} words, over the "
              f"budget of {args.max_words} by {report['universal_words'] - args.max_words} "
              f"words.", file=sys.stderr)

    if args.rules:
        for rule in lead_ins(report["universal"]):
            print(rule)
        for rule in lead_ins(report["project"]):
            print(f"[project] {rule}")
        return 1 if over else 0

    print(f"{args.path.name}: {report['lines']} lines, universal body "
          f"{report['universal_words']} words in {len(report['sections'])} sections, "
          f"project section {report['project_words']} words in "
          f"{report['project_rules']} rules")
    print("\nSections of the universal body, largest first:")
    print("  words  lines  section")
    for s in sorted(report["sections"], key=lambda s: -s["words"]):
        print(f"  {s['words']:5d}  {s['lines']:5d}  {s['name']}")
    print("\nRules mentioned in more than one section (a pointer counts as a mention):")
    repeated = {name: places for name, places in
                concept_map(report["universal"], CONCEPTS).items() if len(places) > 1}
    for name, places in sorted(repeated.items(), key=lambda kv: -len(kv[1])):
        listed = ", ".join(f"{place} x{count}" if count > 1 else place
                           for place, count in places.items())
        print(f"  {name} ({len(places)}): {listed}")
    if not repeated:
        print("  none")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
