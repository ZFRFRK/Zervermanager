<div align="center">

# Zervermanager

**A Debian 12 server manager with a real browser UI — sites, DNS, mail, databases, Docker, and a live terminal.**

[![Version](https://img.shields.io/badge/Version-1.5.0-informational.svg)](#)
[![License: AGPL v3 + Additional Terms](https://img.shields.io/badge/License-AGPLv3%20%2B%20Additional%20Terms-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Platform: Debian 12](https://img.shields.io/badge/Platform-Debian%2012-A81D33?logo=debian&logoColor=white)](https://www.debian.org/)

One Python file. No `pip` installs. Open a browser and manage the box.

</div>

---

## What you get

- A full **web dashboard** on port **8090**.
- A classic CLI menu, if you prefer typing.
- Site, DNS, database, mail, Docker, and service management.
- A live shell in the browser. Paste works, even over plain HTTP.

## Quick start

```bash
git clone https://github.com/ZFRFRK/Zervermanager.git
cd Zervermanager
sudo python3 zervermanager.py
```

- Open `http://<server-ip>:8090`.
- Keep `web_config/` next to the script. That folder is the web UI.
- The UI has **no login screen**. Use it on a trusted LAN only.

## The Web UI

| Page | What it does |
|---|---|
| Dashboard | Uptime, RAM, CPU, service states |
| Manage sites | Edit or remove existing sites |
| Create site | Guided wizard with live output |
| DNS | Zones, records, MX entries |
| MariaDB | Databases, users, backups |
| Services | Packages and mail accounts |
| Server control | systemd, UFW, package repair |
| Network | View or change IP settings |
| Docker | Containers and one-click apps |
| Terminal | Real shell on the server |

The interface has two themes, keyboard shortcuts (`g` + a letter), and works on a phone.

## Terminal page

- Logs you into the server as the user running Zervermanager — usually root.
- Colors, scrollback, and full history after a page reload.
- Paste with `Ctrl+V`, right-click, or the **Paste** button.
- Buttons for `^C`, Clear, and Restart. Resizing the window resizes the shell.

## Tailscale is optional

Startup asks if you want Tailscale installed and connected. Both questions can be declined.

- Declining keeps everything on your LAN.
- Accepting makes the UI reachable from every device on your tailnet, so use one you trust.
- To skip the question entirely: `sudo python3 zervermanager.py --listen 127.0.0.1`

## What you can build

| Area | Highlights |
|---|---|
| Sites | LAMP, LEMP, static, reverse proxy, WordPress, Docker-backed |
| SSL | Self-signed certs or Let's Encrypt with certbot |
| DNS | BIND9 forward and reverse zones, MX records |
| Mail | Postfix, Dovecot, Roundcube, mail users |
| Databases | MariaDB create/drop, users, grants, backup/restore |
| Extras | phpMyAdmin, FTP, Samba, Docker apps |

## OS support

| Status | OS |
|---|---|
| Supported | Debian 12 (Bookworm) |
| Probably OK | Debian 11/13, Ubuntu, Raspberry Pi OS |
| Not supported | Arch, RHEL, Alpine |

## CLI quick reference

- `sudo python3 zervermanager.py` — start the web UI or CLI menu.
- `--dry-run` — preview commands without running them.
- `--listen 127.0.0.1` — local access only.

## Development

```bash
python3 -m py_compile zervermanager.py
python3 tests/run_tests.py
```

- Work happens on the [`dev`](https://github.com/ZFRFRK/Zervermanager/tree/dev) branch.
- Frontend files live in `web_config/` — plain HTML, CSS, and JS.

## Roadmap

- [ ] Login gate for the web UI
- [ ] Firewall rules table
- [ ] `--version` flag
- [ ] Broader Ubuntu support

---

## License

This project is licensed under the **GNU Affero General Public License v3.0**, with **additional custom terms** imposed by the original author — including, but not limited to, restrictions on commercial use, mandatory attribution, and an extended disclaimer of warranty and liability. See the full text in [`LICENSE`](LICENSE) for details.

**Use of this software constitutes acceptance of these terms in full.**

> **⚠️ License Notice:** This project is licensed under the **GNU AGPLv3 with additional restrictive terms** (including a no-commercial-use clause without written permission — see [`LICENSE`](LICENSE)). **By downloading, installing, or running this software, you confirm that you have read, understood, and agree to be bound by all terms of the license.** If you do not agree, do not use this software.
