# Arch Linux Hermes + OpenClaw Stack — May 2026

## Context

Port the macOS Mac mini agent stack ([Mac-Mini-Agent.md](Mac-Mini-Agent.md)) to
Casey's Arch Linux workstation as a precursor to the eventual Mac mini deploy.
The Arch box (Ryzen 9 5900X, RTX 3080 10 GB, 32 GB RAM, Btrfs, KDE/Wayland,
Zen kernel) is the staging ground for a quiet, efficient 24/7 autonomous agent.

**Decisions locked in this session:**
- Inference: **Claude API** (mirrors Mac guide; local Ollama explicitly out of
  scope for this port even though hardware supports it).
- Channels: **Telegram + Discord** via OpenClaw.
- Host bridge: **Full Linux equivalent of the Mac capability server** — a
  FastAPI loopback service exposing KDE Plasma notifications, KMail / Kalendar
  D-Bus calls, clipboard, and file ops. This is the Arch analogue of the Mac
  AppleScript/iMessage gateway.
- Layout: `~/ai-stack/` with **systemd --user** units and `loginctl
  enable-linger casey` for true 24/7 without a login session.

**32 GB RAM means** the Mac guide's golden rule ("zero local inference") still
applies *for this port* but the memory budget is comfortable — no need to cap
Docker at 4 GB or worry about swap thrash. The 10 GB RTX 3080 is unused by
this stack; reserved for the local Ollama work in [README.md](README.md).

---

## Architecture (Arch variant)

```
                Claude API (cloud inference)
                          │
                          ▼
              Hermes Agent (planning + skills)
                          │
          ┌───────────────┼───────────────┐
          ▼                               ▼
 Docker terminal backend        OpenClaw gateway
 (sandboxed tool exec)          (Telegram + Discord)
          │
          ▼
 FastAPI capability server (host, 127.0.0.1:9000)
          │
          ▼
 D-Bus / KDE Plasma (notifications, KMail, Kalendar, clipboard)
```

---

## Plan

### 1. Pacman + AUR prerequisites

Already installed on this box (verified from `pacman -Qe` / `yay -Qm`):
`docker 29.4.3`, `docker-compose 5.1.3`, `git`, `wget`, `python-pip`, `uv`,
`fnm-bin`, `yay`, `ufw`, `snapper`, `nano`, `kate`, `visual-studio-code-bin`,
`brave-bin`, `discord`, `cuda`, `nvidia-open-dkms`. No need to reinstall.

Missing — install only these:

```bash
sudo pacman -S --needed jq tmux btop libnotify
# btop replaces htop in the Mac guide (already preferred on this box).
# libnotify gives us notify-send for the capability server.
# No nodejs/npm/pnpm via pacman — fnm manages Node versions instead:
fnm install 24 && fnm default 24
npm install -g pnpm        # pnpm via the fnm-managed npm
```

Docker service + group (one-time):

```bash
sudo systemctl enable --now docker.service
sudo usermod -aG docker casey      # log out/in for group to take effect
```

Take a Snapper snapshot of `/` before the next steps so the install is
reversible: `sudo snapper -c root create -d "pre ai-stack install"`.

### 2. Enable user-level systemd persistence

```bash
sudo loginctl enable-linger casey
mkdir -p ~/.config/systemd/user
```

This is the Arch equivalent of macOS `launchd` `RunAtLoad=true` — user units
start at boot and survive logout.

### 3. Project layout

```
~/ai-stack/
├── capability-server/      # FastAPI host gateway (native, not in Docker)
├── shared/{postgres,redis,workspace}/
├── logs/
├── secrets/                # chmod 600
└── docker-compose.yml
~/.hermes/                  # Hermes config + skill DB
~/.openclaw/                # OpenClaw config + workspace
```

### 4. Secrets

Mirror Mac §6 but use `wl-copy`/manual paste instead of `pbcopy`. File is
`~/ai-stack/secrets/anthropic.env` with `ANTHROPIC_API_KEY`,
`POSTGRES_USER=hermes`, `POSTGRES_DB=hermes`, `POSTGRES_PASSWORD=<openssl
rand -base64 24>`. `chmod 600`.

### 5. Docker Compose stack

Reuse the Mac compose verbatim with two changes:

- Drop `extra_hosts: host.docker.internal:host-gateway` — on Linux Docker
  resolves `host.docker.internal` automatically when `--add-host` is set, but
  the cleaner Arch idiom is to keep the `extra_hosts` line; both work.
- Raise `mem_limit` on the `hermes` service from `1750m` to `4g`. With 32 GB
  RAM the Mac's tight limits are wasted headroom.
- No need for Docker Desktop's VirtioFS/Rosetta toggles — vanilla `docker`
  package handles everything.

Services: `postgres:18-alpine`, `redis:7-alpine`,
`nousresearch/hermes-agent:latest`. All ports bind `127.0.0.1`.

### 6. Hermes configuration

Identical to Mac §7 — `~/.hermes/config.yaml`:

```yaml
inference:
  provider: anthropic
  model:
    default: claude-opus-4-7
```

### 7. Host capability server — Arch/KDE port

This is the substantive divergence from the Mac guide. Replaces AppleScript
with **D-Bus** calls. Native FastAPI on `127.0.0.1:9000`.

Critical file: `~/ai-stack/capability-server/capability_server.py`

Endpoints (all loopback, all input-validated):

| Endpoint | Mac equivalent | Arch implementation |
| --- | --- | --- |
| `POST /notify` | iMessage send | `notify-send` or `dbus-send` to `org.freedesktop.Notifications` |
| `POST /mail/send` | Mail.app AppleScript | KMail via `org.kde.kmail2` D-Bus, or fall back to `msmtp` |
| `POST /calendar/add` | Calendar.app AppleScript | Kalendar / Akonadi D-Bus, or write `.ics` to a watched folder |
| `POST /clipboard/set` | `pbcopy` equivalent | `wl-copy` subprocess (Wayland) |
| `POST /file/open` | `open` command | `xdg-open` subprocess |

Use `subprocess.run` with arg-list form (never `shell=True`) and pydantic
validation on every payload. The Mac's `applescript_escape` helper is replaced
by D-Bus's native typed args — no string escaping needed.

Dependencies: `uv pip install fastapi uvicorn pydantic dbus-next`.

### 8. OpenClaw on the host

```bash
pnpm add -g openclaw@latest
openclaw onboard --install-daemon
```

The Mac guide installs a `launchd` daemon. On Arch, `openclaw onboard` should
write a systemd --user unit; if it does not, create
`~/.config/systemd/user/openclaw.service` manually pointing at the gateway
entry point.

Edit `~/.openclaw/openclaw.json` exactly as in Mac §9 — pin
`maxConcurrentBrowsers: 1`, enable only `["telegram", "discord"]`, set model
to `anthropic/claude-opus-4-7`.

**Telegram**: BotFather token → paste during onboarding.
**Discord**: bot token from `discord.com/developers/applications`, invite with
message-content intent.

### 9. systemd --user units

Two units, both at `~/.config/systemd/user/`:

**`capability-server.service`** — Arch replacement for the Mac launchd plist
in Appendix A. ExecStart points at the uv venv's python. `WantedBy=default.target`.

**`ai-stack.service`** — wraps `docker compose up -d` / `down`.
`Type=oneshot`, `RemainAfterExit=yes`. `WantedBy=default.target`.

Enable:

```bash
systemctl --user daemon-reload
systemctl --user enable --now capability-server.service ai-stack.service
```

OpenClaw's own unit (from step 8) makes the third.

### 10. Daily maintenance (Arch idioms)

- Cron → **systemd timer**: `ai-stack-restart.timer` firing `OnCalendar=*-*-* 03:00:00`,
  running `docker compose restart`. Mirrors Mac §11's 3 AM cron.
- Monthly cleanup: same `docker builder prune -af && docker image prune -af`.
- Disk watch: Btrfs subvolume usage via `btrfs filesystem usage /`. Keep
  ≥ 50 GB free on the subvolume holding `/var/lib/docker`. Snapper is
  already installed — pre-install snapshot taken in step 1; consider
  excluding `/var/lib/docker` from `root` snapshots if it isn't already
  (otherwise image layers bloat snapshot size).
- Firewall: `ufw` is installed. All stack ports already bind `127.0.0.1`,
  so no rule changes needed; just confirm `sudo ufw status` shows defaults
  intact.

### 11. Files to create (critical paths)

- [docker-compose.yml](docker-compose.yml) (new, at `~/ai-stack/`)
- [capability_server.py](capability-server/capability_server.py) (new)
- `~/.hermes/config.yaml` (new)
- `~/.openclaw/openclaw.json` (edited by onboard, then hand-tuned)
- `~/.config/systemd/user/capability-server.service` (new)
- `~/.config/systemd/user/ai-stack.service` (new)
- `~/.config/systemd/user/ai-stack-restart.timer` + `.service` (new)
- `~/ai-stack/secrets/anthropic.env` (new, chmod 600)

Nothing in the existing `~/ai/` tree changes — Ollama/qwen-custom and the
Hermes stack are independent.

---

## Verification

End-to-end smoke test after install:

1. **Services up**:
   `systemctl --user status capability-server ai-stack openclaw` — all active.
   `docker compose -f ~/ai-stack/docker-compose.yml ps` — postgres, redis,
   hermes all `running`.
2. **Capability server**:
   `curl -X POST 127.0.0.1:9000/notify -d '{"title":"test","body":"hi"}' -H 'content-type: application/json'`
   → KDE notification appears on the desktop.
3. **Hermes ↔ Claude**:
   `docker compose exec hermes hermes chat "say hi"` returns a Claude response.
4. **Hermes → capability server**:
   ask Hermes via CLI to send a desktop notification; confirm it appears
   (proves `host.docker.internal:9000` reachable from the container).
5. **Telegram**: DM the bot from your phone → Hermes replies.
6. **Discord**: same, from a server where the bot is invited.
7. **Reboot test**: `reboot`, log in to a TTY (don't start Plasma), check
   `systemctl --user status` — services should be active because of
   `enable-linger`.
8. **Memory budget**: `htop` after 1 hour idle — total committed should sit
   well under 10 GB, leaving the GPU and most RAM free.

## Out of scope for this plan

- Migrating to local Ollama inference (separate effort; the qwen-custom stack
  in [README.md](README.md) is unaffected).
- The eventual Mac mini install — that's [Mac-Mini-Agent.md](Mac-Mini-Agent.md)
  unchanged.
- Hardening (firewall rules beyond loopback binds, AppArmor, container user
  remapping). Add if/when the box becomes externally reachable.
