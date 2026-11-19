<div align="center">

# Zervermanager

**A complete Debian 12 server manager in one Python file — an interactive CLI suite plus a full browser Web UI, from LAMP/LEMP hosting and mail to DNS, Docker, snapshots, and a live root terminal.**

[![Version](https://img.shields.io/badge/Version-1.5.0-informational.svg)](#)
[![License: AGPL v3 + Additional Terms](https://img.shields.io/badge/License-AGPLv3%20%2B%20Additional%20Terms-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Platform: Debian 12](https://img.shields.io/badge/Platform-Debian%2012-A81D33?logo=debian&logoColor=white)](https://www.debian.org/)
[![Status: Active](https://img.shields.io/badge/Status-Active-brightgreen.svg)](#)

Runs on any fresh minimal Debian installation using **Python 3 standard libraries only** — no `pip` installs, no virtual environments, no Node build step. Clone it, run it, and you get both a terminal menu system and a full web dashboard.

</div>

---

## Table of Contents

- [Highlights](#highlights)
- [Web UI](#web-ui)
- [Web Hosting](#web-hosting)
- [Mail Server](#mail-server)
- [Infrastructure & Server Management](#infrastructure--server-management)
- [Damage Control](#damage-control)
- [Prerequisites](#prerequisites)
- [OS Compatibility](#os-compatibility)
- [Installation](#installation)
- [Development & Testing](#development--testing)
- [Roadmap](#roadmap)
- [License](#license)

---

## Highlights

| | |
|---|---|
| **Single file, zero dependencies** | One `zervermanager.py` (~13k lines) using only the Python standard library. Copy it to a server and run. |
| **Two front ends, one engine** | An interactive CLI menu *and* a browser Web UI on port **8090**, driven by the same command layer. |
| **Snapshots before everything** | Every file written, symlink changed, or service touched is snapshotted first — diff and restore from the UI. |
| **Safe mode** | If startup checks fail, the manager boots into a restricted recovery mode where only safe routes are available. |
| **Web terminal** | A live shell on the server, straight from the browser — type or paste commands, session survives page reloads. |
| **LAN-first, with optional remote access** | Binds to all interfaces, auto-opens the UFW port, and handles port conflicts gracefully. At launch it *asks* whether to install/connect Tailscale — declining leaves you LAN-only. |

---

## Web UI

Point a browser at `http://<server-ip>:8090` and you get a Hyprland-inspired dashboard with a light/dark theme system:

| Page | What it does |
|---|---|
| **Dashboard** | Live system telemetry, service states, and status tiles |
| **Manage sites** | List, modify, or remove hosted sites (configs, databases, SSL) |
| **Create site** | Guided wizard: LAMP / LEMP / proxy / static / WordPress, with a live output pane |
| **DNS** | BIND9 zone and record management with syntax checking |
| **MariaDB** | Databases and users, interactively inspected |
| **Services** | Manage packages and mailboxes (Postfix, Dovecot) |
| **Server control** | systemd units, firewall, package repair |
| **Terminal** | Full root shell in the browser (see below) |
| **Network** | View and apply static IP settings |
| **Docker** | Containers, images, and stacks |

### Terminal page

- A real **PTY shell** (the user running Zervermanager — root on a typical install) behind the same Web UI
- 256-color output, scrollback, and **reconnect with full history** — the shell keeps running server-side when you close the tab
- **Paste support on plain HTTP**: `Ctrl+V`, right-click paste, or the toolbar button
- Toolbar: Paste · `^C` · Clear · Restart; window size is pushed to the PTY on resize
- Blocked by safe mode, like every other write route

> **Security note:** the Web UI and the terminal are open to anyone who can reach the port — there is no login screen. This is designed for a trusted home/LAN setup. Do not port-forward it without adding your own access control first.
>
> **Tailscale note:** Tailscale is entirely optional and off by default. If you accept the setup prompts, the UI — including the root shell — becomes reachable from **every device on your tailnet**, still without a login. Only use a tailnet you control, and consider [Tailscale ACLs](https://tailscale.com/kb/1018/acls) to limit which devices can reach port 8090.

---

## Web Hosting

| Feature | Description |
|---|---|
| Full LAMP Stack | Virtual host, MariaDB database, dedicated user, PHP settings, optional DNS |
| Full LEMP Stack | Nginx + MySQL + PHP-FPM via Unix domain sockets |
| Reverse Proxy | Route traffic to Node.js, Docker, Python, or Go backends |
| Static Sites | Serve plain HTML directories with minimal configuration |
| WordPress Installer | Automated database, config, directory, and server block setup |
| Let's Encrypt SSL | One-step HTTPS via `certbot` for Apache and Nginx |
| Self-Signed SSL | Instant local/dev certificates via `openssl` |

## Mail Server

| Feature | Description |
|---|---|
| SMTP & IMAP | Postfix and Dovecot using the Maildir format |
| Roundcube Webmail | Fully integrated browser-based mail client |
| PAM Security | Mail users isolated under the `mailuser` group with `nologin` shells |

## Infrastructure & Server Management

| Feature | Description |
|---|---|
| DNS — BIND9 | Zone administration, automated serial numbers, syntax checking |
| FTP — vsftpd | Secure transfers with per-user directory jails |
| phpMyAdmin | Web administration portal for MariaDB |
| Samba | Windows-compatible network file sharing |
| MariaDB Manager | Database and user management with an interactive CLI inspector |
| Docker | Container and image management from the CLI or the Web UI |
| Configuration Backups | Timestamped `.bak` backups created automatically before any file is modified |
| System Controls | Start, stop, restart, and monitor systemd units from the menu or the UI |
| Firewall Controls | Manage UFW rules and open or close ports interactively (UI port auto-opened) |

## Damage Control

Zervermanager treats *every* write as a risk and wraps it accordingly:

| Mechanism | What it gives you |
|---|---|
| Snapshots | File contents, symlinks, and service state captured before each change — diffable, restorable |
| Confirm & trash | Destructive actions go through a confirmation flow with a trash/restore path |
| Incidents | Failed operations are recorded with ack/snooze handling |
| Doctor | Automated `check` / `repair` passes for common breakage |
| Plan → Apply | Changes are validated as a plan before anything is executed |
| Safe mode | A restricted boot mode (auto or manual) that exposes only recovery routes |
| Change journal | Every mutation is logged and auditable from the UI |

---

## Prerequisites

- **Operating System:** Debian 12 (Bookworm)
- **Access:** Root privileges (`sudo`) required
- **Runtime:** Python 3.10 or later — standard library only, no third-party dependencies

---

## OS Compatibility

| Status | OS | Version |
|---|---|---|
| **Supported** | Debian GNU/Linux | 12 (Bookworm) — fully tested and certified |
| Uncertain | Debian GNU/Linux | 11 (Bullseye) — paths and packages may differ |
| Uncertain | Debian GNU/Linux | 13 (Trixie) — newer packages may mismatch |
| Uncertain | Ubuntu | 20.04 / 22.04 / 24.04 — service and package name differences |
| Uncertain | Raspberry Pi OS | Bullseye / Bookworm — limited ARM validation |
| **Unsupported** | Arch / RHEL / Alpine | Any — incompatible package managers |

---

## Installation

```bash
# Clone the repository
git clone https://github.com/ZFRFRK/Zervermanager.git
cd Zervermanager

# Grant execution rights
chmod +x zervermanager.py

# Launch the interactive manager
sudo python3 zervermanager.py
```

- The CLI menu starts immediately; the **Web UI comes up on port 8090** in the same process (it opens the port in UFW for you and handles conflicts).
- If 8090 is busy you'll be asked what to do — kill the occupant, or fall back to the next free port.
- The Web UI is served from the bundled `web_config/` folder next to the script — keep them together.

### Tailscale is optional

On an interactive launch you may be asked two yes/no questions:

1. *"Install Tailscale so you can reach this UI from anywhere?"* — runs the official `tailscale.com/install.sh` script, only after you say yes (installs `curl` first if missing).
2. *"Connect this server to your tailnet now?"* — runs `tailscale up` and shows a login URL/QR to approve.

Both are **optional and safe to decline** — declining keeps the UI on your LAN, and Zervermanager continues normally either way. The offer is skipped entirely in `--dry-run`, in non-interactive sessions, and if Tailscale is already connected. To skip the offer and keep the UI reachable only from the machine itself:

```bash
sudo python3 zervermanager.py --listen 127.0.0.1
```

(This also skips the automatic UFW rule.)

### Dry-Run Mode

Use `--dry-run` to simulate all actions without modifying any system file:

```bash
sudo python3 zervermanager.py --dry-run
```

---

## Development & Testing

```bash
# Syntax check
python3 -m py_compile zervermanager.py && echo "Syntax OK"

# Unit tests
python3 tests/run_tests.py
```

- Active development happens on the [`dev`](https://github.com/ZFRFRK/Zervermanager/tree/dev) branch; `main` is kept current by merge.
- The Web UI frontend lives in `web_config/` (vanilla HTML/CSS/JS, cache-busted via `?v=N` pins in `index.html`).

---

## Roadmap

- [ ] Firewall rules table summary inside UFW controls
- [ ] `FAST_MODE` environment variable to skip sleeps during dry-runs and tests
- [ ] Extended Ubuntu LTS support with platform-specific package matrices
- [ ] `--version` flag to print current release info
- [ ] Optional authentication gate for the Web UI (required before any WAN exposure)

---

## License

This project is licensed under the **GNU Affero General Public License v3.0**, with **additional custom terms** imposed by the original author — including, but not limited to, restrictions on commercial use, mandatory attribution, and an extended disclaimer of warranty and liability. See the full text in [`LICENSE`](LICENSE) for details.

**Use of this software constitutes acceptance of these terms in full.**

> **⚠️ License Notice:** This project is licensed under the **GNU AGPLv3 with additional restrictive terms** (including a no-commercial-use clause without written permission — see [`LICENSE`](LICENSE)). **By downloading, installing, or running this software, you confirm that you have read, understood, and agree to be bound by all terms of the license.** If you do not agree, do not use this software.
