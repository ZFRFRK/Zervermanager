# Zervermanager v1.2.0 — Unstable

> ⚠️ **This is an unstable pre-release.** It may contain bugs or incomplete behaviour. For production servers, use [v1.0.0](https://github.com/ZFRFRK/Zervermanager/releases/tag/v1.0.0) instead.

---

## What's new in v1.2.0

### MariaDB Manager (new)
A dedicated MariaDB management menu has been added as **option 2** in the main menu.

- List all non-system databases
- Create database (utf8mb4, with name validation)
- Drop database (requires typed confirmation)
- List all users
- Create user with password
- Drop user (requires typed confirmation)
- Grant privileges — ALL, DML, or SELECT level
- Revoke privileges
- Change user password
- Backup database via `mysqldump` to a `.sql` file
- Restore database from a `.sql` file with confirmation prompt

All operations are compatible with `--dry-run` mode.

---

## Known issues

- MariaDB Manager has not been fully tested across all edge cases
- Restore operation requires manual creation of the target database if it does not exist
- No test coverage yet for grant/revoke flows

---

## Installation

**Option 1 — Download from Assets below** and run directly:
```bash
chmod +x zervermanager_v1.2.0.py
sudo python3 zervermanager_v1.2.0.py
```

**Option 2 — Clone the dev branch:**
```bash
git clone -b dev https://github.com/ZFRFRK/Zervermanager.git
cd Zervermanager
chmod +x zervermanager_v1.2.0.py
sudo python3 zervermanager_v1.2.0.py
```

**Dry-run mode** — preview all actions without touching the server:
```bash
sudo python3 zervermanager_v1.2.0.py --dry-run
```

---

## Requirements

- Debian 12 (Bookworm) — fully tested and supported
- Root or `sudo` access
- Python 3 (standard installation, no extras needed)
- `mariadb-server` installed (auto-prompted if missing)

---

## Checksum

Verify the downloaded file matches the release:
```
sha256: <hash shown in Assets section above>
```
