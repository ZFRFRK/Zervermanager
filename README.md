# Zervermanager — `dev` Branch

[![Branch: dev](https://img.shields.io/badge/Branch-dev-orange.svg)]()
[![Status: Unstable](https://img.shields.io/badge/Status-Unstable-red.svg)]()
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Platform: Debian 12](https://img.shields.io/badge/Platform-Debian%2012-A81D33?logo=debian&logoColor=white)](https://www.debian.org/)

---

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
> - Running this on a live server **risks data loss, service outages,
>   and irreversible misconfiguration.**
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

# Unit tests
python3 tests/run_tests.py
```

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
