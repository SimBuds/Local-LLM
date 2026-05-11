# Arch Linux Technical Reference

## Core philosophy
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
- User group: `docker`
- Compose: `docker-compose`

## System diagnostics
- Errors current boot: `journalctl -p 3 -xb`
- Failed services: `systemctl --failed`
- Hardware modules: `lsmod`, `lspci -k`
- Boot timing: `systemd-analyze blame`

## Btrfs essentials
- List subvolumes: `sudo btrfs subvolume list /`