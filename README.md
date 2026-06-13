# ⚡ Zervermanager v1.3.0 ⚡

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Platform: Debian 12](https://img.shields.io/badge/Platform-Debian%2012-red?logo=debian&logoColor=white)](https://www.debian.org/)
[![Status: Release](https://img.shields.io/badge/Status-Stable%20v1.3.0-green.svg)]()

> 🚀 **Welcome to the largest, most feature-packed release of Zervermanager yet!** v1.3.0 introduces next-level automation, complete config safety, interactive database inspection, and refined control interfaces for web hosting administrators.

A robust, interactive, zero-dependency Python 3 command-line suite for automating the complete setup, configuration, and maintenance of a Debian 12 (Bookworm) web hosting, mail, and DNS server environment. 

Deployable instantly on any fresh minimal Debian installation with standard libraries only—no `pip` installs or virtual environments required.

---

## 🗺️ What's New in v1.3.0 (The Mega Update)

### 🛡️ Automatic Configuration Backups (`backup_config`)
Never lose a working config again. Zervermanager now automatically creates timestamped backups (e.g. `.bak.YYYYMMDD_HHMMSS`) before editing or overwriting any critical configuration file:
*   Apache Virtual Hosts & Nginx Site Profiles
*   BIND9 DNS Zone configurations
*   Postfix, Dovecot, Roundcube webmail files
*   Samba configuration files (`smb.conf`)

### 🗃️ Ultimate MariaDB Manager & Inspector
The database control room has been completely overhauled and split into logical modules:
*   **Logical Sub-menus**: Restructured into separate menus for **Database Management** and **User Management**.
*   **Interactive Database Inspector**: View your tables dynamically from the CLI, inspect table schema (`DESCRIBE` output), and run limited (`LIMIT 50`) data previews safely without terminal flooding.
*   **Instant SQL Imports**: When creating a LAMP or LEMP site, the tool now prompts you to import an existing database dump (`.sql`) immediately.

### 💖 Refined Console Visuals & Secret Tributary
*   **Dynamic Startup Splash**: Vibrant startup screens with auto-tiering OS compatibility checks.
*   **Mai Ouzuka Tribute**: An easter egg screen featuring the birthday tribute, accessible by entering `0831` at the main menu.

---

## 📋 Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [OS Compatibility Matrix](#os-compatibility-matrix)
- [Installation and Usage](#installation-and-usage)
- [Development and Testing](#development-and-testing)
- [Roadmap & Future Goals](#roadmap--future-goals)
- [License](#license)

---

## 🌟 Core Features

### 🌐 Web Hosting (Apache & Nginx)
*   **Full LAMP Site**: Standard vhost configuration, dedicated MariaDB database, user creation, PHP settings, and optional DNS.
*   **Full LEMP Site**: Nginx + MySQL + PHP-FPM configuration using Unix domain sockets.
*   **Reverse Proxy**: Easily route incoming traffic to backend nodes (Node.js, Docker containers, Python, Go apps).
*   **Static Sites**: Quick configuration to serve plain HTML directories.
*   **WordPress Auto-Installer**: Complete WordPress environment setup (database, configuration, directory setup, server block).
*   **Let's Encrypt Integration**: Instant SSL configuration via `certbot` for both Apache and Nginx.
*   **Self-Signed SSL**: Local/development certificates generated via `openssl` instantly.

### ✉️ Enterprise Mail Server
*   **SMTP & IMAP**: Fully configured Postfix (SMTP) and Dovecot (IMAP) using the modern, high-performance **Maildir** format.
*   **Roundcube Webmail**: Fully integrated out-of-the-box browser interface.
*   **Local PAM Security**: System-isolated mail users locked under the `mailuser` group with `nologin` shells.

### 📡 Infrastructure & Server Management
*   **DNS BIND9 Engine**: Full zone administration (forward/reverse zones, automated serial numbers, dynamic A records) with syntax checking.
*   **FTP Server (vsftpd)**: Secure file transfers with per-user directory jails.
*   **phpMyAdmin**: Web administration portal for MariaDB databases.
*   **Samba File Sharing**: Windows-friendly network sharing.
*   **System Controls**: Start/stop/restart/enable/disable systemd units and monitor service status from the menu.
*   **Firewall Controls**: Simple interfaces to manage UFW rules and open/close ports.

---

## 🛠️ Prerequisites

*   **Operating System**: Debian 12 (Bookworm) is recommended and fully supported.
*   **Access**: Root privileges (`sudo`) required.
*   **Runtime**: Python 3 standard distribution (no third-party pip dependencies required!).

---

## 🖥️ OS Compatibility Matrix

| Tier | OS | Version | Support Status |
|---|---|---|---|
| **Supported** | Debian GNU/Linux | 12 (Bookworm) | **Fully Tested & Certified** |
| **Uncertain** | Debian GNU/Linux | 11 (Bullseye) | Partially works; paths/packages may differ |
| **Uncertain** | Debian GNU/Linux | 13 (Trixie) | Newer packages might mismatch |
| **Uncertain** | Ubuntu | 20.04 / 22.04 / 24.04 | Package name and service name differences |
| **Uncertain** | Raspberry Pi OS | Bullseye / Bookworm | Limited ARM architecture validation |
| **Unsupported** | Arch Linux / RHEL / Alpine | Any | Incompatible package managers (`pacman`, `dnf`, `apk`) |

---

## 🚀 Installation and Usage

Clone the repository and run the master script directly on your server:

```bash
# Clone the repository
git clone https://github.com/ZFRFRK/Zervermanager.git
cd Zervermanager

# Grant execution rights
chmod +x zervermanager.py

# Launch the interactive manager
sudo python3 zervermanager.py
```

### 🔍 Dry-Run Mode (Safe Mode)
Want to inspect the actions first? Use the `--dry-run` flag to simulate all configurations and commands without modifying a single system file:

```bash
sudo python3 zervermanager.py --dry-run
```

---

## 🧪 Development and Testing

The project maintains a unit test suite under `/tests` ensuring the safety and stability of all functions.

### Run Syntax Checks
```bash
python3 -m py_compile zervermanager.py && echo "Syntax OK"
```

### Run Unit Tests
```bash
python3 tests/run_tests.py
```

---

## 🔮 Roadmap

- [ ] **Firewall rules visualization**: Display a clean rules table summary inside UFW controls.
- [ ] **Skip Sleep flag (`FAST_MODE`)**: Environment variable to skip script sleeps during dry-runs/tests.
- [ ] **Extended Ubuntu support**: Support configuration paths and package matrices for Ubuntu LTS versions.
- [ ] **CLI Version command**: `--version` flag to print current release info directly.

---

## 📄 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
