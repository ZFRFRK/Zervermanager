# ⚡ Zervermanager — `dev` Branch

[![Branch: dev](https://img.shields.io/badge/Branch-dev-orange.svg)]()
[![Status: Unstable](https://img.shields.io/badge/Status-Unstable-red.svg)]()
[![Python 3](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Platform: Debian 12](https://img.shields.io/badge/Platform-Debian%2012-red?logo=debian&logoColor=white)](https://www.debian.org/)

---

> ## ⚠️ WARNING — DO NOT USE IN PRODUCTION
>
> **This is the active development branch of Zervermanager. It is unstable, untested, and potentially dangerous.**
>
> Code on this branch is a work-in-progress. It may be broken, incomplete, or behave in unexpected ways. Running it on a live or production server **can cause data loss, misconfiguration, or irreversible system damage.**
>
> **Use this branch only if you know exactly what you are doing.**
> For the latest stable release, switch to the [`main` branch](https://github.com/ZFRFRK/Zervermanager).

---

## 📋 Before You Continue

By installing from this branch you acknowledge that:

- Features may be **partially implemented or entirely broken**
- Commands may **modify or destroy system files** without warning
- Automatic backups and safety guards **may not be present** yet
- No support is provided for issues arising from this branch

**Test only on a disposable virtual machine or a fresh throwaway server. Never on anything you care about.**

---

## 🚀 Installation

Clone the `dev` branch directly:

```bash
# Clone the dev branch directly
git clone -b dev https://github.com/ZFRFRK/Zervermanager.git
cd Zervermanager

# Grant execution rights
chmod +x zervermanager.py

# Launch the interactive manager
sudo python3 zervermanager.py
```

### 🔍 Dry-Run Mode (Strongly Recommended)

Always use `--dry-run` when exploring the dev branch. This simulates all actions without touching any system files:

```bash
sudo python3 zervermanager.py --dry-run
```

### Keeping Up to Date

The `dev` branch moves fast. Pull frequently to stay on the latest commits:

```bash
git pull origin dev
```

---

## 🧪 Running Tests

Before executing anything, run the syntax check and unit test suite:

```bash
# Syntax check
python3 -m py_compile zervermanager.py && echo "Syntax OK"

# Unit tests
python3 tests/run_tests.py
```

---

## 🔗 Links

- **Stable release** → [`main` branch](https://github.com/ZFRFRK/Zervermanager)
- **Full feature documentation** → [main branch README](https://github.com/ZFRFRK/Zervermanager/blob/main/README.md)
- **Issue tracker** → [GitHub Issues](https://github.com/ZFRFRK/Zervermanager/issues)

---

## 📄 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
