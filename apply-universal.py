#!/usr/bin/env python3
"""Copy the universal rules from one AGENTS.md into other repos' AGENTS.md files,
keeping each repo's own project rules.

For each target file:
  1. From TEMPLATE it takes everything above `## Project-specific rules`, the
     header itself, and the section's intro paragraph up to and including the
     `<!-- ... body above. -->` comment. The template's own project rules,
     below that comment, are never copied, so any repo's AGENTS.md can serve as
     the template.
  2. From the target it keeps everything after its own intro comment (that
     repo's project rules), byte for byte. Anything above that point in the
     target, including an old or duplicated intro, is replaced.
  3. It writes the target back as template part + target rules.

With no targets named, it finds them: every `AGENTS.md` directly in ROOTS or one
folder below, except the template. Deeper copies (a vendored plugin's own
AGENTS.md) are not ours and are never found. Naming targets skips the search.

Every target is read and split before any is written, so a malformed one stops
the run with nothing written. Safe to re-run: a file that already matches is
reported in sync and not rewritten. It never commits, and never touches anything
else in those repos. Exit 0 when everything is in sync or written, 1 when
--check found drift, 2 on an unreadable or malformed file.

Usage (from ~/Apps/Local-LLM):
  ./apply-universal.py --check AGENTS.md          (prints "in sync" or a diff per file, writes nothing)
  ./apply-universal.py AGENTS.md                  (writes every drifted file)
  ./apply-universal.py AGENTS.md ~/Apps/<repo>/AGENTS.md ...   (only the files named)

Read the --check diff before writing: a universal-rule edit made in another
repo shows up there as drift and would be overwritten. Port it into this repo's
AGENTS.md first.
"""

from __future__ import annotations

import difflib
import os
import sys
import tempfile
from pathlib import Path

HEADER = "\n## Project-specific rules\n"
COMMENT_END = "body above. -->"
ROOTS = (Path.home() / "Apps", Path.home() / "Documents" / "Projects")


class SplitError(Exception):
    """An AGENTS.md without the header or the preamble comment."""


def split(text: str, name: str) -> tuple[str, str]:
    """(universal body + header, rest of the section after its preamble comment)."""
    i = text.find(HEADER)
    if i < 0 or text.count(HEADER) - text.count("```markdown" + HEADER) < 1:
        raise SplitError(f"{name}: no '## Project-specific rules' header")
    section = text[i + len(HEADER):]
    head = section[:4000]  # the preamble, its optional fenced template and comment
    j = head.rfind(COMMENT_END)
    if j < 0:
        raise SplitError(f"{name}: preamble comment not found")
    rest = section[j + len(COMMENT_END):]
    return text[:i + len(HEADER)], rest[1:] if rest.startswith("\n") else rest


def discover(roots, template: Path) -> tuple[list[Path], list[Path]]:
    """(AGENTS.md files in or one folder below each root, roots that do not exist)."""
    found, missing = [], []
    for root in roots:
        if not root.is_dir():
            missing.append(root)
            continue
        for path in [root / "AGENTS.md", *sorted(root.glob("*/AGENTS.md"))]:
            if path.is_file() and path.resolve() != template.resolve():
                found.append(path)
    return found, missing


def write(path: Path, text: str) -> None:
    """Replace path through a temp file in the same folder, keeping its mode."""
    mode = path.stat().st_mode & 0o7777
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".AGENTS.md.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main(argv: list[str]) -> int:
    check = bool(argv) and argv[0] == "--check"
    args = argv[1:] if check else argv
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    template_path = Path(args[0])
    try:
        template = template_path.read_text()
        _, template_rules = split(template, args[0])
    except (OSError, SplitError) as e:
        print(e, file=sys.stderr)
        return 2
    # Universal body + header + standard preamble only. The template's own
    # project rules stay out, so any repo's AGENTS.md can serve as the template.
    new_top = template[:len(template) - len(template_rules)]

    if args[1:]:
        targets = [Path(a) for a in args[1:]]
    else:
        targets, missing = discover(ROOTS, template_path)
        for root in missing:
            print(f"{root}: no such folder, skipped", file=sys.stderr)

    plans, errors = [], []
    for path in targets:
        try:
            old = path.read_text()
            _, project = split(old, str(path))
        except (OSError, SplitError) as e:
            errors.append(str(e))
            continue
        out = new_top + project
        assert out.endswith(project) and out.count(HEADER) >= 1
        plans.append((path, old, out, len(project.splitlines())))
    if errors and not check:
        for e in errors:
            print(e, file=sys.stderr)
        print("nothing written: fix the files above first", file=sys.stderr)
        return 2

    drifted = 0
    for path, old, out, kept in plans:
        if old == out:
            print(f"{path}: in sync ({kept} project lines)")
            continue
        drifted += 1
        if check:
            print(f"{path}: differs from {template_path} ({kept} project lines)")
            sys.stdout.writelines(difflib.unified_diff(
                old.splitlines(keepends=True), out.splitlines(keepends=True),
                fromfile=str(path), tofile=f"{path} (after write)"))
        else:
            write(path, out)
            print(f"{path}: written, kept {kept} project lines, "
                  f"{len(old.splitlines())} -> {len(out.splitlines())} lines")
    for e in errors:
        print(e, file=sys.stderr)
    if errors:
        return 2
    return 1 if check and drifted else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
