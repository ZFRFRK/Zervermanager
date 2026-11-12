# Zervermanager — `dev` Branch

[![Branch: dev](https://img.shields.io/badge/Branch-dev-orange.svg)]()
[![Status: Unstable](https://img.shields.io/badge/Status-Unstable-red.svg)]()
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Platform: Debian 12](https://img.shields.io/badge/Platform-Debian%2012-A81D33?logo=debian&logoColor=white)](https://www.debian.org/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)

A lightweight root-level control panel for Debian 12 servers: sites, DNS
zones, MariaDB, mail, Docker and network — from a web UI or a CLI.

> [!CAUTION]
> ## Everything in this branch can be dangerous.
>
> This is not a "mostly stable with a few bugs" branch. **Consuming any
> commit from `dev` — even a commit whose feature appears to work — can
> damage the machine you run it on.**
>
> - Working features here execute **real, root-level system operations**:
>   `apt` installs/removals, `systemctl` restarts, MariaDB database and
>   user creation, BIND9 zone file writes, apache/nginx vhost writes,
>   Docker containers. There is no undo.
> - Development code runs **without the safety guards, backups, and
>   rollback paths** that make the `main` branch production-safe.
> - Half-finished features are active and can respond to real requests:
>   what worked yesterday may crash, misconfigure, or overwrite state
>   today, and a fresh pull can change behavior under you at any time.
> - The web UI on this branch runs **without authentication** — anyone
>   who can reach the port owns the panel. Restrict it (see below) or
>   firewall it.
>
> **Treat every commit on `dev` as hostile to the host it runs on.**
> For the latest stable release, see the
> [main branch](https://github.com/ZFRFRK/Zervermanager).

---

## Before You Continue

Installing from this branch means accepting that:

- Features may be partially implemented or non-functional
- Scripts may modify or overwrite system files without warning
- Safety guards and automatic backups may not yet be in place
- No support is provided for issues arising from this branch

**Only run this on a disposable virtual machine or isolated test server —
one you are fully prepared to wipe and rebuild.**

---

## Installation

```bash
# Clone the dev branch directly
git clone -b dev https://github.com/ZFRFRK/Zervermanager.git
cd Zervermanager

# Grant execution rights
chmod +x zervermanager.py

# Launch the interactive manager
sudo python3 zervermanager.py
```

The first prompt asks how you want to use it:

```
1. Web Configuration UI (Recommended)
2. Command Line Interface (CLI)
```

The CLI covers the same feature set (Make Site, MariaDB, services,
server control, Docker) as a numbered menu.

### Web UI

By default the UI listens on **port 8090**, on all interfaces, with no
login. Useful overrides:

| | |
|---|---|
| Change the port | `ZM_PORT=8091 sudo python3 zervermanager.py` |
| Localhost only | `sudo python3 zervermanager.py --listen 127.0.0.1` |

(8090, 8080, 8443 and 22 are protected — the panel will refuse or
re-assign a port that would collide with your sites or SSH.)

#### What's in the UI

- **Dashboard** — live status of Apache, Nginx, BIND, MariaDB, Postfix,
  Dovecot and UFW, plus CPU / RAM / temperature / uptime meters
- **Create site** — LAMP, LEMP, WordPress, static and reverse-proxy
  flows: vhost + database + DNS zone + SSL in one step, streamed to the
  output terminal as it runs
- **Sites** — enable, disable, modify (doc root, DB password, vhost) or
  delete existing sites
- **DNS zones** — forward/reverse zones, MX records, lookups
- **MariaDB** — list, create, drop, back up and restore databases;
  create users and grant permissions
- **Install services** — mail stack (Postfix/Dovecot/Roundcube) with
  account management, plus FTP, phpMyAdmin and Samba
- **Server control** — start/stop/restart services, UFW rules, reload
  all, OS maintenance
- **Network** — inspect and apply static IP configuration
- **Docker** — container status and actions, one-click installs

#### Keyboard shortcuts

| Key | Action |
|---|---|
| `Ctrl K` or `/` | Command palette — jump to any page or tab |
| `g` then `d` `m` `s` `z` `b` `i` `v` `n` `k` `h` | Go to Dashboard / Sites / Make site / DNS / MariaDB / Install / Server / Network / Docker / Help |
| `r` | Reload all services |
| `?` | Full shortcut list |
| `Ctrl`/`⌘ + Enter` | Submit the form you're typing in |
| `Esc` | Close dialogs, modals and overlays |

The top bar also has a **Simple view** toggle (hides advanced fields)
and a **motion toggle** (turns animations off, respected alongside your
system's `prefers-reduced-motion` setting).

### Dry-Run Mode (Python only) — Strongly Recommended

Simulate all actions without touching any system file:

```bash
sudo python3 zervermanager.py --dry-run
```

### Staying Up to Date

The `dev` branch is updated frequently — and **every pull can bring new,
un-reviewed root-level behavior with it**. Read what changed *before*
you pull, not after. Pull often only if you are keeping your test
install in sync deliberately:

```bash
git pull origin dev
```

---

## Testing

Always run checks before executing anything on a real system:

```bash
# Syntax check
python3 -m py_compile zervermanager.py && echo "Syntax OK"

# Unit tests (command runners, dry-run safety, input validators)
python3 tests/run_tests.py
```

---

## Project Layout

| Path | What it is |
|---|---|
| `zervermanager.py` | The whole manager: CLI menu, web server, plan/apply engine |
| `web_config/` | The web UI (served by the manager on port 8090) |
| `tests/` | Unit tests, run with `python3 tests/run_tests.py` |

---

## Links

| | |
|---|---|
| Stable release | [main branch](https://github.com/ZFRFRK/Zervermanager) |
| Full documentation | [main branch README](https://github.com/ZFRFRK/Zervermanager/blob/main/README.md) |
| Issue tracker | [GitHub Issues](https://github.com/ZFRFRK/Zervermanager/issues) |

---

## License

This project is licensed under the [AGPL V3 with additional terms](LICENSE).
