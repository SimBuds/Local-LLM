#!/usr/bin/env python3
"""Copy the universal rules from one AGENTS.md into other repos' AGENTS.md files,
keeping each repo's own project rules.

Nothing is discovered automatically: the template and every file to update are
named on the command line.

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

Safe to re-run: a file that already matches comes out identical. It exits
without writing when a file has no `## Project-specific rules` header or no
intro comment. It only rewrites the files named. It never commits, and never
touches anything else in those repos.

Usage (from ~/Apps/Local-LLM):
  ./apply-universal.py --check AGENTS.md ~/Apps/<repo>/AGENTS.md ...   (prints the project lines and line counts)
  ./apply-universal.py AGENTS.md ~/Apps/<repo>/AGENTS.md ...           (overwrites the specific file)

  ./apply-universal.py --check AGENTS.md ~/Apps/{AD-Theme-Shopify,everything4cats,Jobhunt,SEO-LLM,Tutor-LLM}/AGENTS.md
  ./apply-universal.py AGENTS.md ~/Apps/{AD-Theme-Shopify,everything4cats,Jobhunt,SEO-LLM,Tutor-LLM}/AGENTS.md


Run --check first and confirm each "kept N project lines" matches the size of
that repo's project section before running the real command.
"""
import sys
from pathlib import Path

HEADER = "\n## Project-specific rules\n"
COMMENT_END = "body above. -->"


def split(text: str, name: str) -> tuple[str, str]:
    """(universal body + header, rest of the section after its preamble comment)."""
    i = text.find(HEADER)
    if i < 0 or text.count(HEADER) - text.count("```markdown" + HEADER) < 1:
        sys.exit(f"{name}: no '## Project-specific rules' header")
    section = text[i + len(HEADER):]
    head = section[:4000]  # the preamble, its optional fenced template and comment
    j = head.rfind(COMMENT_END)
    if j < 0:
        sys.exit(f"{name}: preamble comment not found")
    rest = section[j + len(COMMENT_END):]
    return text[:i + len(HEADER)], rest[1:] if rest.startswith("\n") else rest


check = sys.argv[1] == "--check"
args = sys.argv[2:] if check else sys.argv[1:]
template = Path(args[0]).read_text()
new_top, template_rules = split(template, args[0])
# Universal body + header + standard preamble only. The template's own project
# rules stay out, so any repo's AGENTS.md can serve as the template.
new_top = template[:len(template) - len(template_rules)]
for name in args[1:]:
    path = Path(name)
    old = path.read_text()
    _, project = split(old, name)
    out = new_top + project
    assert out.endswith(project) and out.count(HEADER) >= 1
    print(f"{name}: kept {len(project.splitlines())} project lines, "
          f"{len(old.splitlines())} -> {len(out.splitlines())} lines")
    if not check:
        path.write_text(out)
