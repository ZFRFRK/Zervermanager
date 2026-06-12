# Zervermanager

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Python 3](https://img.shields.io/badge/Python-3-blue?logo=python&logoColor=white)
![Platform: Debian 12](https://img.shields.io/badge/Platform-Debian%2012-red?logo=debian&logoColor=white)
![Status: Unstable](https://img.shields.io/badge/Status-Unstable-orange)

> ⚠️ **You are on the `dev` branch (v1.2.0 — unstable).** For the latest stable release, switch to the [`main`](https://github.com/ZFRFRK/Zervermanager/tree/main) branch.

A robust, interactive Python 3 command-line tool for automating the complete setup and management of a Debian 12 (Bookworm) web hosting, mail, and DNS server environment.

Self-contained using only the Python standard library — deployable instantly on any fresh minimal Debian installation with zero external dependencies.

---

## Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [OS Compatibility](#os-compatibility)
- [Installation and Usage](#installation-and-usage)
- [Development and Testing](#development-and-testing)
- [Roadmap](#roadmap)
- [License](#license)

---

## Features

### Web Hosting

| Feature | Stack | Description |
|---|---|---|
| Full LAMP site | Apache + MySQL + PHP | Creates virtual host, database, PHP configuration, and optional DNS zone |
| Full LEMP site | Nginx + MySQL + PHP-FPM | Same as above for Nginx |
| Static site | Apache or Nginx | Serves plain HTML from `/var/www/<domain>` |
| Reverse proxy | Apache or Nginx | Proxies to a backend application (Node.js, Python, Docker, etc.) |
| WordPress (Apache) | Apache + MySQL + PHP | Full WordPress installation with database, wp-config, virtual host, and optional DNS |
| WordPress (Nginx) | Nginx + MySQL + PHP-FPM | Full WordPress installation on Nginx with FPM socket |
| Let's Encrypt SSL (Apache) | Certbot + Apache | Obtains and installs a TLS certificate with automatic renewal |
| Let's Encrypt SSL (Nginx) | Certbot + Nginx | Same for Nginx |
| Self-signed SSL | Apache / Nginx | Generates a self-signed certificate for internal or development use |

### MariaDB Manager *(new in v1.2.0)*

| Feature | Description |
|---|---|
| List Databases | Show all non-system databases |
| Create Database | utf8mb4, with name validation |
| Drop Database | Requires typed confirmation |
| List Users | All `@localhost` users |
| Create User | With password |
| Drop User | Requires typed confirmation |
| Grant Privileges | ALL, DML, or SELECT level |
| Revoke Privileges | Revokes all grants on a database |
| Change User Password | Immediate effect with FLUSH |
| Backup Database | `mysqldump` to a `.sql` file |
| Restore Database | stdin restore with confirmation prompt |

### Mail Server

- **SMTP** via Postfix and **IMAP** via Dovecot, configured with the modern Maildir format
- **Roundcube Webmail** for browser-based mail management
- System-isolated mail users under the `mailuser` group with PAM-based local authentication

### Infrastructure Services

| Service | Description |
|---|---|
| Mail Server | Full stack: Postfix (SMTP) + Dovecot (IMAP) + Roundcube (Webmail) |
| FTP Server | vsftpd with per-user directory isolation |
| phpMyAdmin | Web-based MySQL administration interface |
| Samba | Windows-compatible network file sharing |

### Server Management

| Feature | Description |
|---|---|
| Network IP Management | Configure network interfaces via `/etc/network/interfaces` or Netplan |
| Service Control | Start, stop, restart, enable, and disable systemd services from the CLI |
| Firewall Management | Enable/disable UFW, open/close ports, list active rules |

### DNS (BIND9)

Complete zone management — create forward and reverse zones, add and remove A records, and validate configuration with `named-checkconf` and `named-checkzone`.

### System

| Feature | Description |
|---|---|
| Startup loading screen | OS tier detection (supported / uncertain / unsupported) and dependency pre-check |
| Dry-run mode (`--dry-run`) | Simulates all system commands without modifying the server |
| Auto-install dependencies | Prompts to install apache2, bind9, and ifupdown if missing |

---

## Prerequisites

- **Operating System:** Debian 12 (Bookworm) — recommended and fully supported
- **Access:** Root or `sudo` privileges (enforced at startup)
- **Runtime:** Python 3 standard installation — no pip, no virtual environment required

---

## OS Compatibility

### Supported (fully tested)

| OS | Version |
|---|---|
| Debian GNU/Linux | 12 (Bookworm) |

### Uncertain (recognised, may partially work)

The script detects these systems and prompts for confirmation before proceeding.

| OS | Known Limitations |
|---|---|
| Debian GNU/Linux 11 (Bullseye) | Package versions and paths may differ |
| Debian GNU/Linux 13 (Trixie / testing) | Newer package names may break apt commands |
| Ubuntu 20.04, 22.04, 24.04 LTS | Service names, paths, and PHP versions differ |
| Raspberry Pi OS (Bullseye / Bookworm) | Limited testing; ARM architecture may cause issues |

### Unsupported

| OS | Reason |
|---|---|
| Arch Linux / Manjaro | Uses `pacman`, not `apt-get` |
| CentOS / RHEL / Fedora | Uses `dnf` / `yum`; different service names and file paths |
| Alpine Linux | Uses `apk`; no `apt-get` |
| FreeBSD / OpenBSD | Not Linux; `systemctl` unavailable |
| Windows / non-Debian WSL | Not a target environment |

---

## Installation and Usage

### Stable (v1.0.0)

```bash
git clone https://github.com/ZFRFRK/Zervermanager.git
cd Zervermanager
chmod +x zervermanager.py
sudo python3 zervermanager.py
```

### Unstable (v1.2.0 — dev branch)

```bash
git clone -b dev https://github.com/ZFRFRK/Zervermanager.git
cd Zervermanager
chmod +x zervermanager.py
sudo python3 zervermanager.py
```

**Dry-run mode** — preview all actions without making any system changes:
```bash
sudo python3 zervermanager.py --dry-run
```

---

## Development and Testing

The `tests/` directory contains a comprehensive unit test suite covering subprocess wrappers, dry-run safety, input validators, output helpers, and menu logic.

### Syntax Check

```bash
python3 -m py_compile zervermanager.py && echo "OK"
```

### Run Unit Tests

```bash
python3 tests/run_tests.py
```

All 40 tests must pass with the `All tests passed` result.

### Dry-run Smoke Test

```bash
sudo python3 zervermanager.py --dry-run
```

---

## Roadmap

| Priority | Feature |
|---|---|
| Medium | `time.sleep` skip flag for faster test runs (`FAST_MODE` environment variable) |
| Medium | Per-menu input validation for all sub-menus |
| Medium | Firewall rule summary screen after applying UFW rules |
| Low | Timestamped configuration backup before overwriting Apache / BIND9 config files |
| Low | `--version` CLI flag |
| Planned | Full Ubuntu 20.04 / 22.04 / 24.04 LTS support |

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
