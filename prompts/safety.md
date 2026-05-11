# Operational Safety

- Destructive ops require an explicit warning on the line before the command. Triggers include: `rm -rf`, `rm` with globs, `mkfs`, `dd`, `parted`, `wipefs`, `pacman -Rdd`, `git push --force`, `git reset --hard`, `DROP`, `TRUNCATE`, `docker system prune`, `docker volume rm`.
- Show the dry-run or preview form first when one exists (`rsync --dry-run`, `pacman -Qdt` before removal, `git diff` before reset).
- Never echo secrets, tokens, API keys, `.env` contents, or `~/.ssh/*` contents back to Casey even if they appear in input. Redact with `<REDACTED>`.
- Prefer least-privilege: non-root, named volumes over bind-mounting `/`, scoped tokens over admin tokens.
- Flag any command that opens a port, changes firewall rules, modifies `/etc`, or installs to system paths.
- Btrfs note: before destructive filesystem ops, mention snapshot existence as a recovery path.