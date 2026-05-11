# Arch Linux Technical Reference

## Core philosophy
- **Rolling release**: no partial upgrades; always suggest a full sync (`-Syu`).
- **KISS**: prefer vanilla upstream packages over heavily patched ones.
- Prefer actionable answers over theoretical explanations.

## Package management
- Primary: `pacman`
- AUR: `yay`
- Update: `sudo pacman -Syu`
- Clean: `sudo pacman -Sc` && `yay -Yc` (clean AUR orphans)
- Find orphans: `pacman -Qdt`
- Pin a package: edit `IgnorePkg` in `/etc/pacman.conf`

## Docker environment
- Package: `docker`
- Service: `docker.service`
- Socket: `/var/run/docker.sock`
- User group: `docker` (for non-root access; security tradeoff — group members effectively have root)
- Compose: `docker-compose` (standalone v2)

## System diagnostics
- Errors current boot: `journalctl -p 3 -xb`
- Failed services: `systemctl --failed`
- Hardware modules: `lsmod`, `lspci -k`
- Boot timing: `systemd-analyze blame`

## Btrfs essentials
- List subvolumes: `sudo btrfs subvolume list /`
- Snapshot: `sudo btrfs subvolume snapshot <src> <dst>`
- Common snapshot manager: `snapper` or `timeshift` (BTRFS mode).
- Before destructive ops, check for an existing snapshot as a recovery point.
