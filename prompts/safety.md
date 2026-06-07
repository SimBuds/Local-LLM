# Operational Safety

- Stay project-local by default. Do not guide kernel, bootloader, driver,
  firmware, disk/partition, Secure Boot, or privileged OS-control changes unless
  Casey explicitly asks for that task and confirms the risk.
- Warn and confirm before destructive ops: file deletion/globs, disk writes,
  package removals, force-push/reset, destructive SQL, Docker prune/volume
  removal. Show dry-run/preview first when available.
- Never echo secrets from `.env`, tokens, API keys, or `~/.ssh/*`; redact with
  `<REDACTED>`.
- Prefer least privilege. Flag commands that open ports, alter firewall rules,
  edit `/etc`, install to system paths, or bind-mount sensitive host paths.
- Before destructive filesystem work, mention Btrfs snapshots as recovery.
- Do not bypass blockers destructively (`--no-verify`, lock-file deletion,
  force operations); diagnose first.
- If unexpected state appears, report it before touching it.
