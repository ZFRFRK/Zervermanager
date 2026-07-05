<div align="center">

# Zervermanager

**A robust, interactive command-line suite for automating the complete setup, configuration, and maintenance of a Debian 12 web hosting, mail, and DNS server environment.**

[![Version](https://img.shields.io/badge/Version-1.3.0-informational.svg)](#)
[![License: AGPL v3 + Additional Terms](https://img.shields.io/badge/License-AGPLv3%20%2B%20Additional%20Terms-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Platform: Debian 12](https://img.shields.io/badge/Platform-Debian%2012-A81D33?logo=debian&logoColor=white)](https://www.debian.org/)
[![Status: Stable](https://img.shields.io/badge/Status-Stable-brightgreen.svg)](#)

Runs on any fresh minimal Debian installation using Python 3 standard libraries only — no `pip` installs or virtual environments required.

</div>

---

## Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [OS Compatibility](#os-compatibility)
- [Installation](#installation)
- [Development & Testing](#development--testing)
- [Roadmap](#roadmap)
- [License](#license)

---

## Features

### Web Hosting — Apache & Nginx

| Feature | Description |
|---|---|
| Full LAMP Stack | Virtual host, MariaDB database, dedicated user, PHP settings, optional DNS |
| Full LEMP Stack | Nginx + MySQL + PHP-FPM via Unix domain sockets |
| Reverse Proxy | Route traffic to Node.js, Docker, Python, or Go backends |
| Static Sites | Serve plain HTML directories with minimal configuration |
| WordPress Installer | Automated database, config, directory, and server block setup |
| Let's Encrypt SSL | One-step HTTPS via `certbot` for Apache and Nginx |
| Self-Signed SSL | Instant local/dev certificates via `openssl` |

### Mail Server

| Feature | Description |
|---|---|
| SMTP & IMAP | Postfix and Dovecot using the Maildir format |
| Roundcube Webmail | Fully integrated browser-based mail client |
| PAM Security | Mail users isolated under the `mailuser` group with `nologin` shells |

### Infrastructure & Server Management

| Feature | Description |
|---|---|
| DNS — BIND9 | Zone administration, automated serial numbers, syntax checking |
| FTP — vsftpd | Secure transfers with per-user directory jails |
| phpMyAdmin | Web administration portal for MariaDB |
| Samba | Windows-compatible network file sharing |
| MariaDB Manager | Database and user management with an interactive CLI inspector |
| Configuration Backups | Timestamped `.bak` backups created automatically before any file is modified |
| System Controls | Start, stop, restart, and monitor systemd units from the menu |
| Firewall Controls | Manage UFW rules and open or close ports interactively |

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

---

## Roadmap

- [ ] Firewall rules table summary inside UFW controls
- [ ] `FAST_MODE` environment variable to skip sleeps during dry-runs and tests
- [ ] Extended Ubuntu LTS support with platform-specific package matrices
- [ ] `--version` flag to print current release info

---

## License

This project is licensed under the **GNU Affero General Public License v3.0**, with **additional custom terms** imposed by the original author — including, but not limited to, restrictions on commercial use, mandatory attribution, and an extended disclaimer of warranty and liability. See the full text in [`LICENSE`](LICENSE) for details.

**Use of this software constitutes acceptance of these terms in full.**

> **⚠️ License Notice:** This project is licensed under the **GNU AGPLv3 with additional restrictive terms** (including a no-commercial-use clause without written permission — see [`LICENSE`](LICENSE)). **By downloading, installing, or running this software, you confirm that you have read, understood, and agree to be bound by all terms of the license.** If you do not agree, do not use this software.
