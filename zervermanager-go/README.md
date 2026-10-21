# zervermanager-go

Go rewrite of `zervermanager.py` — see `goplan.md` for the migration plan.

## Milestone status

**Deployed status (live on 192.168.1.8, 2026-10-21):**

- **Python 1.3.27 (port 8090)** — Phase A–C + status-batching + the
  `GET /api/session` fix deployed, verified live (login, batched
  systemctl, sites list). Started detached:
  `printf '\n\n' | setsid nohup python3 /home/a2/zervermanager1/zervermanager.py`
  (survives SSH; no systemd unit yet — M7 work).
- **Go shadow (port 8091)** — systemd unit
  `zervermanager-go-shadow.service`, read-only (`-shadow` flag: mounts
  503 on mutations). Reads the same `web_config/` and the
  Python-written `auth.json`. Live parity vs Python verified:
  `/api/sites` byte-identical, all 7 service states / os / versions
  match; known deviation until M2/M6: incident counters (Python raises
  watchdog incidents in its own boot, Go M1 has no watchdog yet).
- On this box the privilege path is `su` from a2 (a2 has no sudoers
  entry, root SSH login refused) — install/packaging scripts use it.

**M1 — walking skeleton (current).** This binary serves the existing,
unchanged `web_config/` assets and implements the Phase 0 security
stack plus the core read APIs:

| Area | Python source | Go file(s) | Status |
|---|---|---|---|
| scrypt hash/verify (`n=16384 r=8 p=1`, `hex(salt):hex(dk)`) | `_hash_password` / `_verify_password` | `internal/auth` | done, tested |
| Sessions (TTL 7 d, sliding, in-memory) | `auth_session` / `auth_logout` | `internal/auth` | done, tested |
| Login rate limit (10 / 300 s / IP, pre-check accounting) | `auth_login` | `internal/auth` | done, tested |
| `/api/login` / `/api/logout` / `/api/session` | `web_handle_auth` | `internal/web/server.go` | done |
| Guard chain session→CSRF→safe-mode→Origin ‖ 404 unknown POST | `do_POST` | `internal/web/server.go` | done |
| `/api/status` (22 keys, batched cached systemctl) | `do_GET handle_api_get` | `internal/web/handlers.go`, `internal/system` | done |
| `/api/health` | `web_handle_new_api` | `internal/web/handlers.go` | done |
| `/api/sites` (meta defaults, enabled derivation) | `do_GET` | `internal/sites` | done |
| Static serving (index.html for `/`, traversal refuse) | `SimpleHTTPRequestHandler` | `internal/web` | done |
| Unknown `/api` GET → 200 `{}`; 401 body `{"error":"login required"}` | `do_GET` | done |

**Next (M2):** snapshots/trash/incidents/audit read APIs, the
`===RESULT===` streaming scaffolding, shadow-mode parity running.

## Build

```
make dev        # host build (dev box)
make build      # regenerates bin/zervermanager-linux-{amd64,arm64} — commit after changes
make test       # gofmt + go vet + go test ./...
```

The UI assets (`web_config/`) stay at the repo root as the single source
of truth for both the Python and Go versions — the installer ships them
to `/opt/zervermanager/`.

## Install on a fresh Debian 11/12/13 (the one-liner flow)

Same UX as the Python version — clone, cd, run — minus the python3
requirement (prebuilt binaries are committed in `bin/`). The Go port
lives in this subfolder of the Zervermanager repo and reuses the
repo-root `web_config/` as its UI:

```
git clone -b dev https://github.com/ZFRFRK/Zervermanager.git
cd Zervermanager/zervermanager-go
sudo ./install.sh          # prompts for the Web UI admin password
```

What it gives you:

- `/opt/zervermanager/{zervermanager, web_config/}` — self-contained app
- `/etc/zervermanager/auth.json` (0600) — admin account, written by
  `-init-password`; Python-compatible (a Python `hashlib.scrypt`
  verifier accepts it, and rollback to the Python manager is a
  `systemctl` away — see below)
- `/etc/systemd/system/zervermanager.service` — enabled, autostart,
  `Restart=on-failure`
- UI on `http://<host>:8090`, logs via `journalctl -u zervermanager -f`
- re-set password: `sudo ./install.sh --reset 'newpw'`

Shadow mode (read-only prober beside a live Python manager in §6):

```
sudo ./install.sh --port 8091 --shadow 'pw'   # unit: zervermanager-go-shadow.service
```

No prebuilt binary for your arch? With the Go toolchain installed the
installer falls back to building from source; otherwise `make build`
on any machine and copy the result into `bin/`.

**Rollback status (M1):** the Go binary does NOT yet write site/
state artifacts, so rolling back is just:

```
systemctl stop zervermanager
sudo python3 /home/a2/zervermanager1/zervermanager.py
```

**Throughput on M1:** deploy target — login, dashboard, `/api/status`,
`/api/sites`, `/api/health` and static UI only. Site-creation and
service-install POSTs route to 404 until M2/M3.

### Useful flags

- `-addr :8090` — listen address
- `-init-password 'pw'` — one-shot: write/reset the admin account in
  `auth.json` and exit
- `-shadow` — read-only prober (mutation endpoints answer 503); meant
  to run on `:8091` next to the live Python for parity diffing (§6)
- `-web-dir`, `-auth-file`, `-meta-dir`, … — see `--help`; every path
  default matches the production directory layout

## Parity notes (M1 truth table)

- **Byte-matched:** 401 body `{"error":"login required"}`; CSRF 403 body
  `{"error":"invalid CSRF token"}`; safe-mode 503 body; Set-Cookie
  attribute strings (`HttpOnly; SameSite=Strict; Path=/`, logout
  `Max-Age=0; Path=/`); login failure messages
  (`Too many attempts — try again later.` / `Invalid credentials.`).
- **Key order:** response JSON field order mirrors the Python dicts
  (structs, not maps). Values are marshalled compact, while python
  `json.dumps` default inserts `, `/`:` spaces — covered by the §7
  normalizer; browsers don't care.
- **Float rendering:** Go prints `4`, python prints `4.0` for the same
  number (`ram_*` etc.) — normalizer-exempted; §7 lists it.
- **Behavioral fix deliberately NOT ported:** the per-unit
  `service_status()` loop in the python `/api/status` re-checks the
  shared TTL cache, so 6 of 7 services report the cache-miss default
  (inactive/stopped) on a warm cache. The matching python fix is part
  of the pending Phase A–C deploy; both sides now do ONE batched query.
- **Known M2 TODOs:** login body malformed JSON → 400 (python raises);
  origin 403 bodies are text (python HTML `send_error`) — error pages
  are not parity-relevant; audit context (`_zm_actor/_zm_src`) lands
  with the audit log package in M2.

## Layout

```
cmd/zervermanager/    entry point + flags
internal/auth/        scrypt, sessions, rate limit, CSRF (tested)
internal/run/         subprocess wrapper, mandatory timeouts
internal/system/      uptime/ram/cpu/os-released + batched systemctl (linux + stub)
internal/state/       incidents.jsonl replay, site metadata readers
internal/sites/       /api/sites listing
internal/web/         routing, guard chain, static serving (tested)
```
