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

### Go Port (experimental, `zervermanager-go/`)

A Go rewrite of the web manager is being built alongside the Python
version in [`zervermanager-go/`](zervermanager-go/) — **this is
development code and carries all of the dangers described above.**

Current state (may be outdated within days — check the milestone notes):

- **Working in the Go port:** login/session/CSRF, dashboard status, and
  the **Make Site engine** — LAMP, LEMP, WordPress, static, reverse
  proxy, and Docker container flows, streamed live to the browser. The
  web UI now performs **real installations and writes real vhosts, BIND9
  zones, and MariaDB databases as root** with no dry-run path.
- **Not implemented yet:** Manage Sites actions (delete/disable/etc.
  still return 403), watchdog/incidents, rollbacks.

Same UI, same API security contract, no Python runtime needed on the
server — prebuilt Linux binaries are committed, so it installs the same
way. Note that installing **stops and replaces** the service and copies
the binary over `/opt/zervermanager`:

```bash
git clone -b dev https://github.com/ZFRFRK/Zervermanager.git
cd Zervermanager/zervermanager-go
sudo ./install.sh 'your-admin-password'   # --reset / --port / --shadow also supported
```

Read-only preview without the destructive handlers:

```bash
sudo ./install.sh --port 8091 --shadow 'your-admin-password'
```

See [zervermanager-go/README.md](zervermanager-go/README.md) for the
current milestone status and scope.

### Dry-Run Mode (Python only) — Strongly Recommended

Simulate all actions without touching any system file
(**the Go port has no dry-run equivalent**):

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

For the Go port:

```bash
cd zervermanager-go
go vet ./... && go test ./...
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
