# Zervermanager — `dev` Branch

[![Branch: dev](https://img.shields.io/badge/Branch-dev-orange.svg)]()
[![Status: Unstable](https://img.shields.io/badge/Status-Unstable-red.svg)]()
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Platform: Debian 12](https://img.shields.io/badge/Platform-Debian%2012-A81D33?logo=debian&logoColor=white)](https://www.debian.org/)

---

> [!CAUTION]
> **This branch is unstable and is not intended for production use.**
>
> Code here is actively being developed. It may be incomplete, broken, or behave in entirely unexpected ways. Executing it on any live or production system **risks data loss, service outages, and irreversible misconfiguration.**
>
> For the latest stable release, see the [main branch](https://github.com/ZFRFRK/Zervermanager).

---

## Before You Continue

Installing from this branch means accepting that:

- Features may be partially implemented or non-functional
- Scripts may modify or overwrite system files without warning
- Safety guards and automatic backups may not yet be in place
- No support is provided for issues arising from this branch

**Only run this on a disposable virtual machine or isolated test server.**

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

### Dry-Run Mode — Strongly Recommended

Simulate all actions without touching any system file:

```bash
sudo python3 zervermanager.py --dry-run
```

### Staying Up to Date

The `dev` branch is updated frequently. Pull often to keep in sync:

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
