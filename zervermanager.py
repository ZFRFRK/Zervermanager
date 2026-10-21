#!/usr/bin/env python3
from __future__ import annotations   # PEP 604/585 backport: allows X | Y and list[X] on Python 3.7-3.9

SCRIPT_VERSION = "1.3.27"

import os
import re
import subprocess
import json
from pathlib import Path
from datetime import datetime
import shutil
import socket
import io
import sys
import time
import gzip
import glob
import http.server
import socketserver
import urllib.parse

# ═══════════════════════════════════════════════════════════════════════════════
#  DAMAGE CONTROL INFRASTRUCTURE  (plan.md — all phases 0–7)
#  This block is self-contained and inserted before all application logic.
#  Every phase is implemented here; the rest of the file remains intact and its
#  handlers are gradually wrapped by the primitives defined below.
# ═══════════════════════════════════════════════════════════════════════════════

import hashlib
import hmac
import fcntl
import threading
import tempfile
import traceback
import uuid
import ipaddress
import contextlib
import enum
from functools import wraps

# ─────────────────────────────────────────
#  Runtime directories (created on demand)
# ─────────────────────────────────────────

ZM_STATE_DIR     = "/var/lib/zervermanager"
ZM_JOURNAL_DIR   = f"{ZM_STATE_DIR}/journal"
ZM_SNAPSHOT_DIR  = f"{ZM_STATE_DIR}/snapshots"
ZM_TRASH_DIR     = f"{ZM_STATE_DIR}/trash"
ZM_LOCK_DIR      = f"{ZM_STATE_DIR}/lock"
ZM_INCIDENTS_LOG = f"{ZM_STATE_DIR}/incidents.jsonl"
ZM_STATE_FILE    = f"{ZM_STATE_DIR}/state.json"
ZM_AUTH_FILE     = "/etc/zervermanager/auth.json"
ZM_AUDIT_LOG     = "/var/log/zervermanager/audit.jsonl"

# ─────────────────────────────────────────
#  Phase 0 — Auth (session, scrypt, CSRF)
# ─────────────────────────────────────────

_SESSION_STORE: dict = {}          # session_id -> {"actor": str, "ts": float, "csrf": str}
_LOGIN_ATTEMPTS: dict = {}         # ip -> [timestamps]
_SESSION_TTL   = 86400 * 7         # 7 days
_LOGIN_RATE    = 10                # max attempts per window
_LOGIN_WINDOW  = 300               # 5 minutes
_SAFE_MODE     = False             # toggled by startup recovery or manual override

def _ensure_zm_dirs():
    """Create all runtime directories with strict permissions."""
    for d in (ZM_STATE_DIR, ZM_JOURNAL_DIR, ZM_SNAPSHOT_DIR,
              ZM_TRASH_DIR, ZM_LOCK_DIR):
        os.makedirs(d, mode=0o700, exist_ok=True)
    audit_dir = os.path.dirname(ZM_AUDIT_LOG)
    os.makedirs(audit_dir, mode=0o750, exist_ok=True)
    os.makedirs(os.path.dirname(ZM_AUTH_FILE), mode=0o700, exist_ok=True)


def _load_auth_config() -> dict:
    """Load auth.json, return {} if missing or corrupt."""
    try:
        with open(ZM_AUTH_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_auth_config(cfg: dict):
    _ensure_zm_dirs()
    with open(ZM_AUTH_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(ZM_AUTH_FILE, 0o600)


def _hash_password(password: str) -> str:
    """Hash a password with scrypt via stdlib hashlib."""
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + ":" + dk.hex()


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split(":")
        salt = bytes.fromhex(salt_hex)
        dk   = bytes.fromhex(dk_hex)
        candidate = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
        return hmac.compare_digest(candidate, dk)
    except Exception:
        return False


def auth_init(admin_password: str):
    """Initialise auth config with a single admin account."""
    cfg = {"admin": {"hash": _hash_password(admin_password), "role": "admin"}}
    cfg["session_secret"] = os.urandom(32).hex()
    _save_auth_config(cfg)


def auth_reset(new_password: str):
    """CLI: reset the admin password."""
    cfg = _load_auth_config()
    if "admin" not in cfg:
        cfg["admin"] = {"role": "admin"}
    cfg["admin"]["hash"] = _hash_password(new_password)
    _save_auth_config(cfg)


def auth_login(username: str, password: str, client_ip: str = "") -> tuple[bool, str]:
    """Attempt login. Returns (success, session_id_or_error)."""
    # Rate limiting
    now = time.time()
    bucket = _LOGIN_ATTEMPTS.setdefault(client_ip, [])
    _LOGIN_ATTEMPTS[client_ip] = [t for t in bucket if now - t < _LOGIN_WINDOW]
    if len(_LOGIN_ATTEMPTS[client_ip]) >= _LOGIN_RATE:
        return False, "Too many attempts — try again later."
    _LOGIN_ATTEMPTS[client_ip].append(now)

    cfg = _load_auth_config()
    user = cfg.get(username)
    if not user or not _verify_password(password, user.get("hash", "")):
        return False, "Invalid credentials."

    sid = uuid.uuid4().hex
    csrf = uuid.uuid4().hex
    _SESSION_STORE[sid] = {
        "actor": username,
        "role": user.get("role", "viewer"),
        "ts": now,
        "csrf": csrf,
    }
    return True, sid


def auth_logout(session_id: str):
    _SESSION_STORE.pop(session_id, None)


def auth_session(session_id: str) -> dict | None:
    """Return session dict if valid, else None."""
    sess = _SESSION_STORE.get(session_id)
    if sess and time.time() - sess["ts"] < _SESSION_TTL:
        sess["ts"] = time.time()
        return sess
    _SESSION_STORE.pop(session_id, None)
    return None


def auth_csrf_ok(session_id: str, csrf_token: str) -> bool:
    sess = _SESSION_STORE.get(session_id)
    if not sess:
        return False
    return hmac.compare_digest(sess.get("csrf", ""), csrf_token)


def web_require_auth(handler_method):
    """Decorator for WebUIHandler methods: enforce session + CSRF for mutating routes."""
    @wraps(handler_method)
    def wrapper(self, *args, **kwargs):
        # Origin / Host check (CSRF / DNS-rebinding protection)
        origin = self.headers.get("Origin", "")
        host   = self.headers.get("Host", "")
        if origin and not (origin.startswith("http://") and host in origin):
            self.send_error(403, "Forbidden: cross-origin request")
            return
        # Session check
        cookie = self.headers.get("Cookie", "")
        sid = ""
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("zm_session="):
                sid = part[len("zm_session="):]
        sess = auth_session(sid)
        if not sess:
            self.send_error(401, "Unauthorised: login required")
            return
        self.zm_session = sess
        return handler_method(self, *args, **kwargs)
    return wrapper


def web_require_csrf(handler_method):
    """Decorator: additionally enforce the X-ZM-CSRF header on POST routes."""
    @wraps(handler_method)
    def wrapper(self, *args, **kwargs):
        cookie = self.headers.get("Cookie", "")
        sid = ""
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("zm_session="):
                sid = part[len("zm_session="):]
        csrf = self.headers.get("X-ZM-CSRF", "")
        if not auth_csrf_ok(sid, csrf):
            self.send_error(403, "Forbidden: invalid CSRF token")
            return
        return handler_method(self, *args, **kwargs)
    return wrapper


def auth_is_configured() -> bool:
    cfg = _load_auth_config()
    return bool(cfg.get("admin", {}).get("hash"))


# ─────────────────────────────────────────
#  Phase 0 — Single-instance lock
# ─────────────────────────────────────────

_INSTANCE_LOCK_FD = None

def acquire_instance_lock() -> bool:
    """Try to acquire the instance-level flock. Returns False if another instance is running."""
    global _INSTANCE_LOCK_FD
    _ensure_zm_dirs()
    lock_path = f"{ZM_LOCK_DIR}/instance.lock"
    try:
        fd = open(lock_path, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        _INSTANCE_LOCK_FD = fd
        return True
    except OSError:
        return False


# ─────────────────────────────────────────
#  Phase 0 — Operation lock (one mutation at a time)
# ─────────────────────────────────────────

_OPERATION_LOCK   = threading.Lock()
_OPERATION_HOLDER = {"who": None, "what": None, "since": None}
_OP_LOCK_FD       = None


@contextlib.contextmanager
def operation_lock(actor: str = "cli", what: str = "operation"):
    """Context manager: hold the operation flock for the duration of a mutation.
    Raises RuntimeError(409) if another mutation is already running."""
    _ensure_zm_dirs()
    lock_path = f"{ZM_LOCK_DIR}/operation.lock"
    try:
        fd = open(lock_path, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = _OPERATION_HOLDER
        raise RuntimeError(
            f"409 Busy: another operation is running "
            f"(by {holder['who']} since {holder['since']}: {holder['what']})"
        )
    _OPERATION_HOLDER.update({"who": actor, "what": what,
                               "since": datetime.now().isoformat()})
    try:
        yield
    finally:
        _OPERATION_HOLDER.update({"who": None, "what": None, "since": None})
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()
        except Exception:
            pass


# ─────────────────────────────────────────
#  Phase 0 — Audit log
# ─────────────────────────────────────────

_SECRET_PATTERNS = [
    re.compile(r'(?i)(password|passwd|secret|token|key|pass)\s*[=:]\s*\S+'),
    re.compile(r'\$[A-Za-z_]+PWD\s*=\s*\S+'),
]


def _redact(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub(r'\1=***REDACTED***', text)
    return text


_AUDIT_LOCK = threading.Lock()

def audit(
    actor: str,
    action: str,
    resource: str = "",
    tier: int = 0,
    tx_id: str = "",
    snap_id: str = "",
    result: str = "ok",
    duration_ms: int = 0,
    validators: list | None = None,
    probes: list | None = None,
    src: str = "",
    extra: dict | None = None,
):
    """Append one JSON line to the audit log."""
    _ensure_zm_dirs()
    entry = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "actor": actor,
        "src": src,
        "action": action,
        "tier": tier,
        "resource": _redact(resource),
        "tx": tx_id,
        "snapshot": snap_id,
        "result": result,
        "duration_ms": duration_ms,
        "validators": validators or [],
        "probes": probes or [],
    }
    if extra:
        entry.update(extra)
    line = json.dumps(entry) + "\n"
    with _AUDIT_LOCK:
        try:
            with open(ZM_AUDIT_LOG, "a") as f:
                f.write(line)
        except Exception:
            pass  # audit failure must never crash the app


# ─────────────────────────────────────────
#  Phase 0/6 — Protected resources
# ─────────────────────────────────────────

_PROTECTED_ACCOUNTS = {
    "root", "www-data", "postfix", "dovecot", "mysql",
    "bind", "nobody", "daemon", "sync", "games", "man",
    "lp", "news", "uucp", "proxy", "backup", "list",
    "irc", "gnats", "sshd", "systemd-network",
    "systemd-resolve", "messagebus", "avahi",
}

_PROTECTED_PORTS = {22, 8080, 8443}   # extended with WEB_UI_DEFAULT_PORT at startup

_PROTECTED_PATHS = {
    "/", "/etc", "/var", "/usr", "/bin", "/sbin", "/lib", "/boot",
    "/proc", "/sys", "/dev", "/home", "/root", "/run", "/tmp", "/srv",
    "/var/www", "/var/lib/zervermanager",
}

_PROTECTED_DATABASES = {
    "mysql", "information_schema", "performance_schema", "sys",
}


def _check_protected_account(username: str):
    """Raise ValueError if username is a protected system account."""
    try:
        import pwd
        uid = pwd.getpwnam(username).pw_uid
        if uid < 1000:
            raise ValueError(f"'{username}' is a protected system account (UID {uid}).")
    except KeyError:
        pass  # user doesn't exist yet — OK
    if username in _PROTECTED_ACCOUNTS:
        raise ValueError(f"'{username}' is a protected account.")


def _check_protected_path(path: str):
    real = os.path.realpath(path)
    if real in _PROTECTED_PATHS:
        raise ValueError(f"Path '{real}' is protected.")
    for p in _PROTECTED_PATHS:
        if real.startswith(p + "/") and p in ("/", "/etc", "/var/lib/zervermanager"):
            if p == "/var/lib/zervermanager":
                raise ValueError(f"Path '{real}' is inside the protected state directory.")


def _check_protected_port(port: int):
    if port in _PROTECTED_PORTS:
        raise ValueError(f"Port {port} is reserved and cannot be used.")


def _check_protected_database(name: str):
    if name.lower() in _PROTECTED_DATABASES:
        raise ValueError(f"Database '{name}' is protected.")


# ─────────────────────────────────────────
#  Phase 0 — Server-side confirmation tokens (plan/apply API)
# ─────────────────────────────────────────

_PLAN_STORE: dict = {}   # plan_id -> {"action": str, "data": dict, "tier": int, "ts": float, "used": bool}
_PLAN_TTL = 120          # seconds a plan_id is valid


def plan_create(action: str, data: dict, tier: int, impact: list) -> str:
    """Register a pending plan and return a one-time plan_id."""
    pid = uuid.uuid4().hex
    _PLAN_STORE[pid] = {
        "action": action,
        "data": data,
        "tier": tier,
        "impact": impact,
        "ts": time.time(),
        "used": False,
    }
    return pid


def plan_consume(plan_id: str) -> dict | None:
    """Mark a plan as used and return it, or None if invalid/expired/already used."""
    plan = _PLAN_STORE.get(plan_id)
    if not plan:
        return None
    if plan["used"]:
        return None
    if time.time() - plan["ts"] > _PLAN_TTL:
        del _PLAN_STORE[plan_id]
        return None
    plan["used"] = True
    return plan


# ─────────────────────────────────────────
#  Phase 1 — Input validation module
# ─────────────────────────────────────────

_DOMAIN_RE   = re.compile(r'^(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$')
_USERNAME_RE = re.compile(r'^[a-z_][a-z0-9_.\-]{0,30}[a-z0-9_]$')
_DBNAME_RE   = re.compile(r'^[A-Za-z0-9_]{1,64}$')


def validate_domain_input(domain: str) -> str:
    """Validate and normalise a domain name. Returns lowercased domain or raises ValueError."""
    d = domain.strip().lower()
    if not d:
        raise ValueError("Domain cannot be empty.")
    if len(d) > 253:
        raise ValueError("Domain too long (>253 chars).")
    if not _DOMAIN_RE.match(d):
        raise ValueError(f"Invalid domain name: '{d}'.")
    return d


def validate_username_input(username: str, protected_check: bool = True) -> str:
    u = username.strip().lower()
    if not u:
        raise ValueError("Username cannot be empty.")
    if not _USERNAME_RE.match(u):
        raise ValueError(
            f"Invalid username '{u}'. Use lowercase letters, digits, '.', '-', '_'; "
            "start with a letter or '_'; 3–32 chars."
        )
    if protected_check:
        _check_protected_account(u)
    return u


def validate_dbname_input(name: str, protected_check: bool = True) -> str:
    n = name.strip()
    if not n:
        raise ValueError("Database name cannot be empty.")
    if not _DBNAME_RE.match(n):
        raise ValueError(f"Invalid database name '{n}'. Use letters, digits, underscores (1–64 chars).")
    if protected_check:
        _check_protected_database(n)
    return n


def validate_ip_input(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip.strip())
        if addr.is_unspecified or addr.is_multicast:
            raise ValueError(f"IP address '{ip}' is not valid for this use.")
        return str(addr)
    except ValueError as e:
        raise ValueError(f"Invalid IP address: {e}")


def validate_port_input(port: int | str) -> int:
    p = int(port)
    if not 1 <= p <= 65535:
        raise ValueError(f"Port {p} out of range (1–65535).")
    _check_protected_port(p)
    return p


def validate_path_input(path: str, must_exist: bool = False) -> str:
    if not path:
        raise ValueError("Path cannot be empty.")
    real = os.path.realpath(path)
    _check_protected_path(real)
    if must_exist and not os.path.exists(real):
        raise ValueError(f"Path does not exist: {real}")
    return real


def validate_email_input(email: str) -> str:
    e = email.strip().lower()
    if "@" not in e:
        raise ValueError(f"Invalid email address: '{e}'.")
    local, _, domain = e.partition("@")
    try:
        validate_domain_input(domain)
    except ValueError:
        raise ValueError(f"Invalid domain in email: '{email}'.")
    if not _USERNAME_RE.match(local.replace("+", "_").replace(".", "_")):
        raise ValueError(f"Invalid local part in email: '{local}'.")
    return e


# ─────────────────────────────────────────
#  Phase 1 — Validator registry
# ─────────────────────────────────────────

class ValidatorResult:
    __slots__ = ("ok", "msg")
    def __init__(self, ok: bool, msg: str = ""):
        self.ok  = ok
        self.msg = msg
    def __bool__(self): return self.ok


def _run_validator(cmd: list, label: str) -> ValidatorResult:
    """Run an external validator command; return ValidatorResult."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            return ValidatorResult(True, f"{label}: ok")
        return ValidatorResult(False, f"{label}: {(r.stdout + r.stderr).strip()}")
    except FileNotFoundError:
        return ValidatorResult(False, f"{label}: binary not found ({cmd[0]})")
    except subprocess.TimeoutExpired:
        return ValidatorResult(False, f"{label}: timed out")


VALIDATOR_REGISTRY = {
    "nginx":    lambda: _run_validator(["/usr/sbin/nginx", "-t"], "nginx"),
    "apache2":  lambda: _run_validator(["/usr/sbin/apache2ctl", "configtest"], "apache2"),
    "named":    lambda: _run_validator(["named-checkconf"], "named"),
    "postfix":  lambda: _run_validator(["postfix", "check"], "postfix"),
    "dovecot":  lambda: _run_validator(["doveconf", "-n"], "dovecot"),
    "roundcube": lambda: _run_validator(
        ["php", "-l", "/etc/roundcube/config.inc.php"], "roundcube"
    ),
    "docker-compose": lambda compose_file="": _run_validator(
        ["docker", "compose", "-f", compose_file, "config"], "docker-compose"
    ),
}


def validate_subsystem(name: str, *args) -> ValidatorResult:
    fn = VALIDATOR_REGISTRY.get(name)
    if not fn:
        return ValidatorResult(False, f"No validator registered for '{name}'")
    return fn(*args) if args else fn()


# ─────────────────────────────────────────
#  Phase 1 — Preflight checks
# ─────────────────────────────────────────

def preflight_disk_free(path: str = "/var", mb: int = 200) -> ValidatorResult:
    try:
        st = os.statvfs(path)
        free_mb = (st.f_frsize * st.f_bavail) // (1024 * 1024)
        if free_mb < mb:
            return ValidatorResult(False, f"Disk too full on {path}: {free_mb} MB free, need {mb} MB.")
        return ValidatorResult(True, f"Disk free: {free_mb} MB on {path}")
    except Exception as e:
        return ValidatorResult(False, f"Cannot check disk space on {path}: {e}")


def preflight_port_free(port: int) -> ValidatorResult:
    try:
        out = subprocess.run(
            ["ss", "-H", "-ltnp", f"sport = :{port}"],
            capture_output=True, text=True, timeout=5
        ).stdout
        if out.strip():
            return ValidatorResult(False, f"Port {port} is already in use:\n{out.strip()}")
        return ValidatorResult(True, f"Port {port} is free")
    except Exception as e:
        return ValidatorResult(False, f"Cannot check port {port}: {e}")


def preflight_no_dpkg_lock() -> ValidatorResult:
    lock = "/var/lib/dpkg/lock-frontend"
    try:
        with open(lock, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
        return ValidatorResult(True, "dpkg lock free")
    except OSError:
        return ValidatorResult(False, "dpkg/apt lock held — another package operation is running.")
    except FileNotFoundError:
        return ValidatorResult(True, "dpkg lock free")


def preflight_binary_exists(binary: str) -> ValidatorResult:
    import shutil as _shutil
    path = _shutil.which(binary)
    if path:
        return ValidatorResult(True, f"{binary} found at {path}")
    return ValidatorResult(False, f"Required binary not found: {binary}")


def preflight_running_as_root() -> ValidatorResult:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return ValidatorResult(True, "running as root")
    return ValidatorResult(False, "Must run as root.")


def run_preflights(*checks: ValidatorResult) -> list[ValidatorResult]:
    """Run a list of preflight checks; return list of failures (empty = all good)."""
    failures = [c for c in checks if not c.ok]
    return failures


# ─────────────────────────────────────────
#  Phase 1 — Functional probes
# ─────────────────────────────────────────

def probe_http(url: str, host: str = "", expected_codes=(200, 301, 302)) -> ValidatorResult:
    import urllib.request
    try:
        req = urllib.request.Request(url)
        if host:
            req.add_header("Host", host)
        resp = urllib.request.urlopen(req, timeout=8)
        code = resp.getcode()
        if code in expected_codes:
            return ValidatorResult(True, f"HTTP probe {url} → {code}")
        return ValidatorResult(False, f"HTTP probe {url} → unexpected {code}")
    except Exception as e:
        return ValidatorResult(False, f"HTTP probe {url} failed: {e}")


def probe_smtp_rcpt(domain: str, rcpt: str = "") -> ValidatorResult:
    import smtplib
    rcpt = rcpt or f"postmaster@{domain}"
    try:
        with smtplib.SMTP("127.0.0.1", 25, timeout=8) as smtp:
            smtp.ehlo()
            smtp.mail(f"check@{domain}")
            code, _ = smtp.rcpt(rcpt)
            smtp.rset()
        if code in (250, 251):
            return ValidatorResult(True, f"SMTP RCPT {rcpt} → {code}")
        return ValidatorResult(False, f"SMTP RCPT {rcpt} → {code}")
    except Exception as e:
        return ValidatorResult(False, f"SMTP probe failed: {e}")


def probe_port_listening(port: int, host: str = "127.0.0.1") -> ValidatorResult:
    try:
        with socket.create_connection((host, port), timeout=4):
            return ValidatorResult(True, f"Port {port} listening on {host}")
    except Exception as e:
        return ValidatorResult(False, f"Port {port} not listening on {host}: {e}")


def probe_dns_record(domain: str, record_type: str = "A") -> ValidatorResult:
    try:
        out = subprocess.run(
            ["dig", "+short", record_type, domain, "@127.0.0.1"],
            capture_output=True, text=True, timeout=8
        )
        result = out.stdout.strip()
        if result:
            return ValidatorResult(True, f"DNS {record_type} {domain} → {result}")
        return ValidatorResult(False, f"DNS {record_type} {domain} → no answer from local BIND")
    except Exception as e:
        return ValidatorResult(False, f"DNS probe failed: {e}")


def probe_service_active(service: str) -> ValidatorResult:
    try:
        r = subprocess.run(["systemctl", "is-active", service],
                           capture_output=True, text=True, timeout=5)
        if r.stdout.strip() == "active":
            return ValidatorResult(True, f"{service} is active")
        return ValidatorResult(False, f"{service} is {r.stdout.strip()}")
    except Exception as e:
        return ValidatorResult(False, f"Cannot check {service}: {e}")


# ─────────────────────────────────────────
#  Phase 1 — Change Transaction
# ─────────────────────────────────────────

class _TxState(str, enum.Enum):
    PLANNED      = "planned"
    SNAPSHOTTING = "snapshotting"
    APPLYING     = "applying"
    VALIDATING   = "validating"
    RELOADING    = "reloading"
    VERIFYING    = "verifying"
    COMMITTED    = "committed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK  = "rolled_back"
    ROLLBACK_FAILED = "rollback_failed"


class _Transaction:
    """Implements the snapshot→apply→validate→reload→verify→commit state machine."""

    def __init__(self, description: str, scope: list[str],
                 actor: str = "cli", dry_run: bool = False):
        self.id          = f"tx_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.description = description
        self.scope       = scope
        self.actor       = actor
        self.dry_run     = dry_run
        self.state       = _TxState.PLANNED
        self._steps: list[dict] = []            # journal of applied steps (for undo)
        self._before_files: dict[str, bytes|None] = {}  # path -> original content
        self._before_symlinks: dict[str, str|None] = {}  # path -> original target
        self._undo_actions: list[callable] = []
        self._irreversibles: list[str] = []
        self._validators: list[str] = []
        self._probes: list[callable] = []
        self._reloads: set[str] = set()
        self._snap_id = ""
        self._start_ts = time.time()
        _ensure_zm_dirs()
        self._journal_dir = os.path.join(ZM_JOURNAL_DIR, self.id)
        os.makedirs(self._journal_dir, mode=0o700, exist_ok=True)
        self._write_manifest(_TxState.PLANNED)

    # ── journal helpers ──────────────────────────────────────────────────────

    def _write_manifest(self, state: _TxState):
        m = {
            "id": self.id,
            "description": self.description,
            "scope": self.scope,
            "actor": self.actor,
            "started": datetime.utcnow().isoformat() + "Z",
            "state": state.value,
        }
        try:
            with open(os.path.join(self._journal_dir, "manifest.json"), "w") as f:
                json.dump(m, f, indent=2)
        except Exception:
            pass

    def _log_step(self, action: str, undo: str = "", result: str = "ok"):
        entry = {"action": action, "undo": undo, "result": result,
                 "ts": datetime.utcnow().isoformat() + "Z"}
        self._steps.append(entry)
        try:
            with open(os.path.join(self._journal_dir, "steps.jsonl"), "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    # ── pre-snapshot helpers ─────────────────────────────────────────────────

    def _snapshot_file(self, path: str):
        """Record the pre-change content of a file so we can restore it on rollback."""
        if path in self._before_files:
            return
        if os.path.isfile(path):
            with open(path, "rb") as f:
                self._before_files[path] = f.read()
        else:
            self._before_files[path] = None  # didn't exist → delete on rollback

    def _snapshot_symlink(self, path: str):
        if path in self._before_symlinks:
            return
        if os.path.islink(path):
            self._before_symlinks[path] = os.readlink(path)
        else:
            self._before_symlinks[path] = None

    # ── transaction primitives ───────────────────────────────────────────────

    def preflight(self, *checks: ValidatorResult):
        """Run preflight checks and raise on failure (before anything is touched)."""
        failures = [c for c in checks if not c.ok]
        if failures:
            msgs = "; ".join(c.msg for c in failures)
            raise RuntimeError(f"Preflight failed: {msgs}")

    def write(self, path: str, content: str | bytes):
        """Atomically write a file; snapshot the original first."""
        self._snapshot_file(path)
        if self.dry_run:
            print(f"  {_C_DIM}[dry-run] Would write: {path}{_C_RESET}")
            self._log_step(f"write:{path}", undo=f"restore:{path}")
            return
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        dir_ = os.path.dirname(os.path.abspath(path)) or "."
        with tempfile.NamedTemporaryFile(mode=mode, dir=dir_, delete=False) as tmp:
            tmp.write(content)
            tmp_name = tmp.name
        os.replace(tmp_name, path)  # atomic on POSIX
        self._log_step(f"write:{path}", undo=f"restore:{path}")
        # register undo
        before = self._before_files[path]
        def _undo_write(p=path, b=before):
            if b is None:
                if os.path.exists(p): os.remove(p)
            else:
                with open(p, "wb") as f: f.write(b)
        self._undo_actions.insert(0, _undo_write)

    def symlink(self, path: str, target: str):
        """Create or replace a symlink; snapshot original."""
        self._snapshot_symlink(path)
        if self.dry_run:
            print(f"  {_C_DIM}[dry-run] Would symlink: {path} → {target}{_C_RESET}")
            self._log_step(f"symlink:{path}", undo=f"restore_symlink:{path}")
            return
        if os.path.islink(path): os.unlink(path)
        os.symlink(target, path)
        self._log_step(f"symlink:{path}", undo=f"restore_symlink:{path}")
        before = self._before_symlinks[path]
        def _undo_sym(p=path, b=before):
            if os.path.islink(p): os.unlink(p)
            if b is not None: os.symlink(b, p)
        self._undo_actions.insert(0, _undo_sym)

    def remove_symlink(self, path: str):
        """Remove a symlink if it exists; snapshot first."""
        self._snapshot_symlink(path)
        if self.dry_run:
            print(f"  {_C_DIM}[dry-run] Would remove symlink: {path}{_C_RESET}")
            return
        if os.path.islink(path):
            os.unlink(path)
        before = self._before_symlinks[path]
        def _undo_rsym(p=path, b=before):
            if b is not None and not os.path.islink(p): os.symlink(b, p)
        self._undo_actions.insert(0, _undo_rsym)

    def run_cmd(self, cmd: list, undo_cmd: list | None = None, check: bool = True):
        """Run a subprocess command with an optional undo command."""
        if self.dry_run:
            print(f"  {_C_DIM}[dry-run] Would run: {' '.join(str(c) for c in cmd)}{_C_RESET}")
            self._log_step(f"cmd:{' '.join(str(c) for c in cmd)}")
            return
        r = subprocess.run(cmd, capture_output=True, text=True)
        self._log_step(f"cmd:{' '.join(str(c) for c in cmd)}",
                       undo=f"cmd:{' '.join(str(c) for c in undo_cmd)}" if undo_cmd else "",
                       result="ok" if r.returncode == 0 else f"rc={r.returncode}")
        if check and r.returncode != 0:
            raise RuntimeError(
                f"Command failed (rc={r.returncode}): {' '.join(str(c) for c in cmd)}\n"
                f"{r.stderr.strip()}"
            )
        if undo_cmd:
            def _undo_cmd(u=undo_cmd):
                subprocess.run(u, capture_output=True)
            self._undo_actions.insert(0, _undo_cmd)

    def irreversible(self, description: str):
        """Declare the next step as irreversible (cannot be undone)."""
        self._irreversibles.append(description)
        print(f"  ! Irreversible step: {description}")

    def validate(self, *subsystems: str):
        """Run validators for the given subsystems; raise on failure."""
        self._validators.extend(subsystems)
        for name in subsystems:
            result = validate_subsystem(name)
            if not result.ok:
                raise RuntimeError(f"Validation failed ({name}): {result.msg}")

    def reload(self, *services: str):
        """Mark services for reload (deferred until commit or explicit call)."""
        self._reloads.update(services)

    def verify(self, *probes: ValidatorResult):
        """Run functional probes; raise on failure."""
        failures = [p for p in probes if not p.ok]
        if failures:
            msgs = "; ".join(p.msg for p in failures)
            raise RuntimeError(f"Post-change verification failed: {msgs}")
        self._probes.extend([p.msg for p in probes])

    def add_probe(self, probe_fn: callable):
        """Add a lazy probe (called only on commit-verify)."""
        self._probes.append(probe_fn)

    # ── service reload ───────────────────────────────────────────────────────

    def _do_reloads(self):
        for svc in self._reloads:
            r = subprocess.run(["systemctl", "is-active", svc],
                               capture_output=True, text=True)
            action = "reload" if r.stdout.strip() == "active" else "restart"
            subprocess.run(["systemctl", action, svc], capture_output=True)

    # ── rollback ─────────────────────────────────────────────────────────────

    def _rollback(self):
        self.state = _TxState.ROLLING_BACK
        self._write_manifest(self.state)
        errors = []
        for fn in self._undo_actions:
            try:
                fn()
            except Exception as e:
                errors.append(str(e))
        # reload after rollback
        try:
            self._do_reloads()
        except Exception as e:
            errors.append(f"reload-after-rollback: {e}")
        if errors:
            self.state = _TxState.ROLLBACK_FAILED
            _raise_incident(
                "ROLLBACK_FAILED",
                severity="critical",
                evidence="\n".join(errors),
                tx_id=self.id,
            )
        else:
            self.state = _TxState.ROLLED_BACK
        self._write_manifest(self.state)
        return errors

    # ── context manager ──────────────────────────────────────────────────────

    def __enter__(self):
        self.state = _TxState.SNAPSHOTTING
        self._write_manifest(self.state)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = int((time.time() - self._start_ts) * 1000)
        if exc_type is None:
            # Success path
            if not self.dry_run:
                self.state = _TxState.RELOADING
                self._write_manifest(self.state)
                self._do_reloads()
            self.state = _TxState.COMMITTED
            self._write_manifest(self.state)
            # Take a snapshot of committed state for undo
            try:
                snap_id = _snapshot_create(
                    label=f"post-commit: {self.description}",
                    trigger="post-change",
                    tx_id=self.id,
                )
                self._snap_id = snap_id
            except Exception:
                pass
            audit(
                actor=self.actor,
                action=self.description,
                tx_id=self.id,
                snap_id=self._snap_id,
                result="committed",
                duration_ms=duration_ms,
                validators=self._validators,
                probes=[p if isinstance(p, str) else str(p) for p in self._probes],
            )
            return False  # don't suppress
        else:
            # Failure path → rollback
            errors = self._rollback()
            audit(
                actor=self.actor,
                action=self.description,
                tx_id=self.id,
                result="rolled_back",
                duration_ms=duration_ms,
                extra={"rollback_errors": errors, "exception": str(exc_val)},
            )
            return False  # re-raise original exception


# Convenience alias for the context manager
_C_DIM   = "\033[2m"
_C_RESET = "\033[0m"


@contextlib.contextmanager
def change(description: str, scope: list[str],
           actor: str = "cli", dry_run: bool = False):
    """Entry-point: use `with change(...) as tx:` to wrap any mutation."""
    with operation_lock(actor, description):
        tx = _Transaction(description, scope, actor=actor, dry_run=dry_run)
        with tx:
            yield tx


# ─────────────────────────────────────────
#  Phase 2 — Snapshot system
# ─────────────────────────────────────────

_CONFIG_SCOPE_PATHS = [
    "/etc/nginx", "/etc/apache2", "/etc/bind", "/etc/postfix",
    "/etc/dovecot", "/etc/roundcube", "/etc/mailname",
    "/etc/ufw", "/etc/vsftpd.conf", "/etc/samba/smb.conf",
    "/etc/servermanager",
]
_MAX_AUTO_SNAPSHOTS  = 30
_SNAPSHOT_MAX_MB     = 500
_TRASH_RETENTION_DAYS = 14


def _snapshot_id() -> str:
    return f"snap_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _snapshot_create(
    label: str = "",
    trigger: str = "manual",
    tx_id: str = "",
    paths: list[str] | None = None,
) -> str:
    """Create a snapshot of the config scope. Returns snap_id."""
    _ensure_zm_dirs()
    sid = _snapshot_id()
    snap_dir = os.path.join(ZM_SNAPSHOT_DIR, sid)
    os.makedirs(snap_dir, mode=0o700)

    scope = paths or _CONFIG_SCOPE_PATHS
    tar_path = os.path.join(snap_dir, "files.tar.zst")

    # Build a tarball of existing paths in scope
    existing = [p for p in scope if os.path.exists(p)]
    if existing:
        try:
            subprocess.run(
                ["tar", "--zstd", "-cpf", tar_path, "--ignore-failed-read"] + existing,
                capture_output=True, timeout=60
            )
        except Exception:
            # Fall back to plain tar if zstd not available
            tar_path = tar_path.replace(".tar.zst", ".tar.gz")
            subprocess.run(
                ["tar", "-czpf", tar_path, "--ignore-failed-read"] + existing,
                capture_output=True, timeout=60
            )

    # SHA256 checksums
    sha_path = os.path.join(snap_dir, "SHA256SUMS")
    if os.path.exists(tar_path):
        r = subprocess.run(["sha256sum", tar_path], capture_output=True, text=True)
        with open(sha_path, "w") as f:
            f.write(r.stdout)

    # Service state (one batched query instead of one spawn per unit)
    services_state = {}
    wanted = ("nginx", "apache2", "bind9", "postfix", "dovecot")
    for svc, state in service_statuses(wanted).items():
        services_state[svc] = {"active": state == "active"}

    manifest = {
        "id": sid,
        "created": datetime.utcnow().isoformat() + "Z",
        "trigger": trigger,
        "tx": tx_id,
        "label": label,
        "lkg": False,
        "scope": scope,
        "services": services_state,
        "size_bytes": os.path.getsize(tar_path) if os.path.exists(tar_path) else 0,
    }
    with open(os.path.join(snap_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    os.chmod(snap_dir, 0o700)

    _snapshot_prune()
    return sid


def snapshot_list() -> list[dict]:
    """List all snapshots, newest first."""
    _ensure_zm_dirs()
    snaps = []
    for entry in os.scandir(ZM_SNAPSHOT_DIR):
        if not entry.is_dir():
            continue
        mf = os.path.join(entry.path, "manifest.json")
        try:
            with open(mf) as f:
                snaps.append(json.load(f))
        except Exception:
            pass
    snaps.sort(key=lambda s: s.get("created", ""), reverse=True)
    return snaps


def snapshot_diff(snap_id: str) -> str:
    """Diff a snapshot against the live system — scoped to the snapshot's own
    paths. Never walks `/' (the fallback used to diff the whole filesystem,
    which is both slow and useless)."""
    snap_dir = os.path.join(ZM_SNAPSHOT_DIR, snap_id)
    tar_path = None
    for ext in (".tar.zst", ".tar.gz"):
        p = os.path.join(snap_dir, f"files{ext}")
        if os.path.exists(p):
            tar_path = p
            break
    if not tar_path:
        return "Snapshot archive not found."

    # Scope from the manifest (written at snapshot time); fall back to the
    # default config scope if the manifest is missing/corrupt.
    scope = _CONFIG_SCOPE_PATHS
    try:
        with open(os.path.join(snap_dir, "manifest.json"), "r") as _mf:
            scope = json.load(_mf).get("scope") or scope
    except (OSError, ValueError):
        pass

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["tar", "-xpf", tar_path, "-C", tmp],
                       capture_output=True, timeout=60)
        existing = [p for p in scope if os.path.exists(p)]
        diffs = []
        for live_path in existing:
            member = os.path.join(tmp, live_path.lstrip("/"))
            if not os.path.exists(member):
                continue
            d = subprocess.run(
                ["diff", "-ru", "--new-file", member, live_path],
                capture_output=True, text=True, timeout=30
            )
            if d.stdout:
                diffs.append(d.stdout)
    return "\n".join(diffs) or "(no differences)"


def snapshot_restore(snap_id: str, only: list[str] | None = None, dry_run: bool = False) -> bool:
    """Restore a snapshot (transactionally). Returns True on success."""
    snap_dir = os.path.join(ZM_SNAPSHOT_DIR, snap_id)
    if not os.path.isdir(snap_dir):
        return False

    # Verify checksum
    sha_path = os.path.join(snap_dir, "SHA256SUMS")
    tar_path = None
    for ext in (".tar.zst", ".tar.gz"):
        p = os.path.join(snap_dir, f"files{ext}")
        if os.path.exists(p):
            tar_path = p
            break
    if tar_path and os.path.exists(sha_path):
        r = subprocess.run(["sha256sum", "-c", sha_path], capture_output=True, cwd=snap_dir)
        if r.returncode != 0:
            print(f"  \033[91m✗ Snapshot {snap_id} checksum mismatch — refusing to restore.\033[0m")
            return False

    if dry_run:
        print(f"  [dry-run] Would restore snapshot {snap_id}")
        print(snapshot_diff(snap_id))
        return True

    # Restore as a transaction
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["tar", "-xpf", tar_path, "-C", tmp], capture_output=True, timeout=120)
        # Walk extracted files and restore them
        for root, dirs, files in os.walk(tmp):
            for fname in files:
                src = os.path.join(root, fname)
                dst = "/" + os.path.relpath(src, tmp)
                if only and not any(dst.startswith(o) for o in only):
                    continue
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                except Exception as e:
                    print(f"  ! Could not restore {dst}: {e}")

    audit(actor="cli", action=f"snapshot.restore:{snap_id}", result="committed")
    return True


def _snapshot_prune():
    """Prune old auto-snapshots according to retention policy."""
    try:
        snaps = snapshot_list()
        auto = [s for s in snaps if s.get("trigger") in ("auto", "pre-change", "post-change")]
        if len(auto) > _MAX_AUTO_SNAPSHOTS:
            for s in auto[_MAX_AUTO_SNAPSHOTS:]:
                if s.get("lkg"):
                    continue
                snap_dir = os.path.join(ZM_SNAPSHOT_DIR, s["id"])
                shutil.rmtree(snap_dir, ignore_errors=True)
    except Exception:
        pass


# ─────────────────────────────────────────
#  Phase 6 — Trash (archive-before-delete)
# ─────────────────────────────────────────

def trash_archive(kind: str, name: str, paths: list[str], extra_fn: callable | None = None) -> str:
    """Archive paths into trash before destructive deletion. Returns trash_id."""
    _ensure_zm_dirs()
    ts   = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    tid  = f"{ts}-{kind}-{name}"
    tdir = os.path.join(ZM_TRASH_DIR, tid)
    os.makedirs(tdir, mode=0o700)

    if extra_fn:
        try:
            extra_fn(tdir)
        except Exception as e:
            print(f"  ! Trash extra-fn failed: {e}")

    existing = [p for p in paths if os.path.exists(p)]
    if existing:
        tar_path = os.path.join(tdir, "archive.tar.gz")
        subprocess.run(["tar", "-czpf", tar_path, "--ignore-failed-read"] + existing,
                       capture_output=True, timeout=120)

    with open(os.path.join(tdir, "meta.json"), "w") as f:
        json.dump({"id": tid, "kind": kind, "name": name,
                   "created": datetime.utcnow().isoformat() + "Z",
                   "paths": paths}, f, indent=2)
    return tid


def trash_mysqldump(db_name: str, dest_dir: str):
    """Dump a MySQL/MariaDB database into dest_dir (used as trash extra_fn).

    No shell round-trip: mysqldump is Popen'd directly and its stdout is
    gzipped in-process, so DB names with quotes/metachars stay safe.
    """
    dump_path = os.path.join(dest_dir, f"{db_name}.sql.gz")
    try:
        proc = subprocess.Popen(["mysqldump", "--databases", db_name],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        return
    try:
        with gzip.open(dump_path, "wb") as gz, proc.stdout:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                gz.write(chunk)
        proc.wait(timeout=300)
        if proc.returncode != 0:
            print(f"  ! mysqldump failed for {db_name}: "
                  f"{proc.stderr.read(errors='replace')}")
    except Exception as e:
        print(f"  ! mysqldump pipeline failed for {db_name}: {e}")
        try: proc.kill()
        except Exception: pass


def trash_list() -> list[dict]:
    _ensure_zm_dirs()
    items = []
    for entry in os.scandir(ZM_TRASH_DIR):
        mf = os.path.join(entry.path, "meta.json")
        try:
            with open(mf) as f:
                items.append(json.load(f))
        except Exception:
            pass
    items.sort(key=lambda i: i.get("created", ""), reverse=True)
    return items


def trash_restore(trash_id: str) -> bool:
    tdir = os.path.join(ZM_TRASH_DIR, trash_id)
    tar  = os.path.join(tdir, "archive.tar.gz")
    if not os.path.exists(tar):
        return False
    r = subprocess.run(["tar", "-xzpf", tar, "-C", "/", "--keep-directory-symlink"],
                       capture_output=True, timeout=120)
    return r.returncode == 0


def trash_purge(older_than_days: int = _TRASH_RETENTION_DAYS):
    cutoff = time.time() - older_than_days * 86400
    for item in trash_list():
        try:
            ts_str = item.get("created", "")
            ts = datetime.fromisoformat(ts_str.rstrip("Z")).timestamp()
            if ts < cutoff:
                shutil.rmtree(os.path.join(ZM_TRASH_DIR, item["id"]), ignore_errors=True)
        except Exception:
            pass


# ─────────────────────────────────────────
#  Phase 3 — Incidents
# ─────────────────────────────────────────

_INCIDENTS: dict[str, dict] = {}  # incident_id -> incident dict
_INCIDENTS_LOCK = threading.Lock()


def _raise_incident(source: str, severity: str = "medium",
                    evidence: str = "", suggested_action: str = "",
                    tx_id: str = "") -> str:
    """Create or merge an incident. Returns incident_id."""
    iid = f"inc_{source.lower().replace(' ', '_')}_{uuid.uuid4().hex[:4]}"
    now = datetime.utcnow().isoformat() + "Z"
    incident = {
        "id": iid,
        "severity": severity,
        "source": source,
        "first_seen": now,
        "last_seen": now,
        "count": 1,
        "evidence": evidence,
        "suggested_action": suggested_action,
        "tx": tx_id,
        "status": "open",
    }
    with _INCIDENTS_LOCK:
        # Merge duplicates (same source + severity)
        for existing in _INCIDENTS.values():
            if (existing["source"] == source
                    and existing["severity"] == severity
                    and existing["status"] == "open"):
                existing["last_seen"] = now
                existing["count"] += 1
                if evidence:
                    existing["evidence"] = evidence
                iid = existing["id"]
                incident = existing
                break
        else:
            _INCIDENTS[iid] = incident

    # Persist
    try:
        with open(ZM_INCIDENTS_LOG, "a") as f:
            f.write(json.dumps(incident) + "\n")
    except Exception:
        pass
    return iid


def incidents_list(status: str = "open") -> list[dict]:
    return [i for i in _INCIDENTS.values() if i["status"] == status]


def incident_ack(incident_id: str):
    if incident_id in _INCIDENTS:
        _INCIDENTS[incident_id]["status"] = "ack"


def incident_resolve(incident_id: str):
    if incident_id in _INCIDENTS:
        _INCIDENTS[incident_id]["status"] = "resolved"


# ─────────────────────────────────────────
#  Phase 3 — Desired-state registry
# ─────────────────────────────────────────

def _load_state() -> dict:
    try:
        with open(ZM_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"services": {}, "vhosts": {}, "zones": {}, "lkg": None}


def _save_state(state: dict):
    _ensure_zm_dirs()
    with open(ZM_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def state_register_service(name: str, active: bool = True, port: int | None = None):
    s = _load_state()
    s["services"][name] = {"active": active, "port": port}
    _save_state(s)


def state_register_vhost(domain: str, server: str, config_hash: str = ""):
    s = _load_state()
    s["vhosts"][domain] = {"server": server, "hash": config_hash}
    _save_state(s)


def state_set_lkg(snap_id: str):
    s = _load_state()
    s["lkg"] = snap_id
    _save_state(s)
    # Mark the snapshot manifest
    mf = os.path.join(ZM_SNAPSHOT_DIR, snap_id, "manifest.json")
    if os.path.exists(mf):
        try:
            with open(mf) as f:
                m = json.load(f)
            m["lkg"] = True
            with open(mf, "w") as f:
                json.dump(m, f, indent=2)
        except Exception:
            pass


# ─────────────────────────────────────────
#  Phase 3 — Watchdog thread
# ─────────────────────────────────────────

class _Watchdog(threading.Thread):
    """Background daemon that checks service health, drift, port conflicts, and resources."""

    CHECKS = {
        "service_health":  10,
        "port_conflicts":  60,
        "config_drift":    300,
        "disk_pressure":   60,
        "cert_expiry":     86400,
        "log_patterns":    60,
        "front_door":      120,
        "dns_health":      300,
        "mail_health":     300,
        "failed_units":    60,
    }

    def __init__(self):
        super().__init__(daemon=True, name="ZM-Watchdog")
        self._last_run: dict[str, float] = {}
        self._stop_evt = threading.Event()

    def stop(self):
        self._stop_evt.set()

    def run(self):
        while not self._stop_evt.wait(timeout=5):
            now = time.time()
            for check, interval in self.CHECKS.items():
                last = self._last_run.get(check, 0)
                if now - last >= interval:
                    self._last_run[check] = now
                    try:
                        getattr(self, f"_check_{check}")()
                    except Exception:
                        pass

    # Individual checks ────────────────────────────────────────────────────

    def _check_service_health(self):
        state = _load_state()
        wanted = [svc for svc, meta in state.get("services", {}).items()
                  if meta.get("active")]
        if not wanted:
            return
        # One batched, cached query for every registered unit (also shared
        # with the /api/status poll) instead of a spawn per service.
        states = service_statuses(wanted, ttl=5.0)
        for svc in wanted:
            actual = states.get(svc, "inactive")
            if actual != "active":
                _raise_incident(f"service_down:{svc}", severity="high",
                                evidence=f"{svc} is {actual}",
                                suggested_action=f"Run: systemctl restart {svc}")
                self._autoheal_service(svc)

    def _autoheal_service(self, svc: str):
        """Restart a stopped managed service if its config validates (max 3/hr)."""
        key = f"autoheal:{svc}"
        now = time.time()
        bucket = _AUTOHEAL_COUNTS.setdefault(key, [])
        _AUTOHEAL_COUNTS[key] = [t for t in bucket if now - t < 3600]
        if len(_AUTOHEAL_COUNTS[key]) >= 3:
            _raise_incident(f"autoheal_circuit_open:{svc}", severity="high",
                            evidence="Auto-restart circuit breaker open (3 restarts/hr).",
                            suggested_action="Investigate the service manually.")
            return
        _AUTOHEAL_COUNTS[key].append(now)
        subprocess.run(["systemctl", "restart", svc], capture_output=True, timeout=30)
        audit(actor="watchdog", action=f"autoheal.restart:{svc}", result="ok")

    def _check_port_conflicts(self):
        state = _load_state()
        reserved = {8080, 8443, 22}
        for svc, meta in state.get("services", {}).items():
            if meta.get("port"):
                reserved.add(meta["port"])
        out = subprocess.run(["ss", "-H", "-ltnp"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            m = re.search(r':(\d+)\s', line)
            if not m:
                continue
            port = int(m.group(1))
            if port in reserved:
                procs = re.findall(r'users:\(\("([^"]+)",pid=(\d+)', line)
                for name, pid in procs:
                    if name not in ("nginx", "apache2", "httpd", "python3", "python"):
                        _raise_incident(
                            f"port_conflict:{port}",
                            severity="high",
                            evidence=f"Port {port} held by {name} (pid {pid})",
                            suggested_action=f"Stop {name} or reassign port {port}.",
                        )

    def _check_config_drift(self):
        """Detect files managed by the tool that have been edited externally."""
        state = _load_state()
        for domain, meta in state.get("vhosts", {}).items():
            for conf_dir in ("/etc/nginx/sites-available", "/etc/apache2/sites-available"):
                path = f"{conf_dir}/{domain}.conf"
                if not os.path.exists(path):
                    continue
                with open(path, "rb") as f:
                    current_hash = hashlib.sha256(f.read()).hexdigest()[:16]
                if meta.get("hash") and current_hash != meta["hash"]:
                    _raise_incident(
                        f"config_drift:{domain}",
                        severity="low",
                        evidence=f"{path} was edited outside the tool.",
                        suggested_action="Review the change or click 'Adopt' to accept it.",
                    )

    def _check_disk_pressure(self):
        for path in ("/", "/var", "/etc"):
            try:
                st = os.statvfs(path)
                used_pct = 100 - (st.f_bavail * 100 // st.f_blocks) if st.f_blocks else 0
                if used_pct >= 90:
                    _raise_incident(f"disk_critical:{path}", severity="high",
                                    evidence=f"Disk on {path}: {used_pct}% used",
                                    suggested_action="Free disk space immediately.")
                elif used_pct >= 80:
                    _raise_incident(f"disk_warning:{path}", severity="medium",
                                    evidence=f"Disk on {path}: {used_pct}% used")
            except Exception:
                pass

    def _check_cert_expiry(self):
        import ssl as _ssl
        state = _load_state()
        for domain in state.get("vhosts", {}):
            try:
                ctx = _ssl.create_default_context()
                with ctx.wrap_socket(socket.socket(), server_hostname=domain) as s:
                    s.settimeout(5)
                    s.connect((domain, 443))
                    cert = s.getpeercert()
                    expire = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
                    days_left = (expire - datetime.utcnow()).days
                    if days_left <= 7:
                        _raise_incident(f"cert_expiry:{domain}", severity="high",
                                        evidence=f"Certificate expires in {days_left} days.",
                                        suggested_action="Renew the TLS certificate.")
                    elif days_left <= 30:
                        _raise_incident(f"cert_expiry:{domain}", severity="medium",
                                        evidence=f"Certificate expires in {days_left} days.")
            except Exception:
                pass

    def _check_log_patterns(self):
        pattern = re.compile(r'emerg|crit|fatal|panic', re.I)
        logs = ["/var/log/nginx/error.log", "/var/log/apache2/error.log",
                "/var/log/mail.err"]
        for log in logs:
            try:
                with open(log) as f:
                    f.seek(0, 2)
                    size = f.tell()
                    f.seek(max(0, size - 4096))
                    tail = f.read()
                matches = [l for l in tail.splitlines() if pattern.search(l)]
                if matches:
                    _raise_incident(f"log_pattern:{os.path.basename(log)}",
                                    severity="medium",
                                    evidence="\n".join(matches[-3:]))
            except Exception:
                pass

    def _check_front_door(self):
        state = _load_state()
        for domain in list(state.get("vhosts", {}))[:5]:  # cap at 5
            result = probe_http("http://127.0.0.1/", host=domain)
            if not result.ok:
                _raise_incident(f"front_door:{domain}", severity="medium",
                                evidence=result.msg,
                                suggested_action="Check nginx/Apache config for this vhost.")

    def _check_dns_health(self):
        state = _load_state()
        for domain in list(state.get("zones", {}))[:5]:
            result = probe_dns_record(domain)
            if not result.ok:
                _raise_incident(f"dns_health:{domain}", severity="medium", evidence=result.msg)

    def _check_mail_health(self):
        if not os.path.exists("/usr/sbin/postfix"):
            return
        result = probe_port_listening(25)
        if not result.ok:
            _raise_incident("mail_smtp_down", severity="high", evidence=result.msg,
                            suggested_action="Check Postfix: systemctl status postfix")
        result = probe_port_listening(143)
        if not result.ok:
            _raise_incident("mail_imap_down", severity="high", evidence=result.msg,
                            suggested_action="Check Dovecot: systemctl status dovecot")

    def _check_failed_units(self):
        r = subprocess.run(["systemctl", "--failed", "--no-pager", "--plain"],
                           capture_output=True, text=True)
        if "0 loaded units" not in r.stdout and r.stdout.strip():
            lines = [l for l in r.stdout.splitlines() if "●" in l or "UNIT" in l]
            if lines:
                _raise_incident("failed_systemd_units", severity="medium",
                                evidence="\n".join(lines[:5]),
                                suggested_action="Check with: systemctl --failed")


_AUTOHEAL_COUNTS: dict[str, list] = {}
_watchdog: _Watchdog | None = None


def watchdog_start():
    global _watchdog
    if _watchdog is None or not _watchdog.is_alive():
        _watchdog = _Watchdog()
        _watchdog.start()


def watchdog_stop():
    global _watchdog
    if _watchdog:
        _watchdog.stop()
        _watchdog = None


# ─────────────────────────────────────────
#  Phase 4 — Dead-man's switch
# ─────────────────────────────────────────

_DEADMAN_TIMERS: dict[str, dict] = {}   # tx_id -> {"timer": Timer, "tx_id": str}


def deadman_arm(tx_id: str, timeout_s: int = 60,
                rollback_fn: callable | None = None):
    """Arm a dead-man's switch for a risky change. Call deadman_confirm() to cancel."""
    def _fire():
        print(f"\n  ! Dead-man's switch fired — reverting {tx_id}")
        if rollback_fn:
            try:
                rollback_fn()
                audit(actor="deadman", action=f"deadman.revert:{tx_id}", result="ok")
            except Exception as e:
                audit(actor="deadman", action=f"deadman.revert:{tx_id}",
                      result="failed", extra={"error": str(e)})
        _DEADMAN_TIMERS.pop(tx_id, None)

    t = threading.Timer(timeout_s, _fire)
    t.daemon = True
    _DEADMAN_TIMERS[tx_id] = {"timer": t, "tx_id": tx_id}
    t.start()


def deadman_confirm(tx_id: str) -> bool:
    """Confirm a risky change, cancelling the dead-man's timer. Returns True if was armed."""
    entry = _DEADMAN_TIMERS.pop(tx_id, None)
    if entry:
        entry["timer"].cancel()
        audit(actor="user", action=f"deadman.confirmed:{tx_id}", result="ok")
        return True
    return False


# ─────────────────────────────────────────
#  Phase 5 — Job runner & crash recovery
# ─────────────────────────────────────────

_JOBS: dict[str, dict] = {}
_JOB_LOG_DIR = f"{ZM_STATE_DIR}/job_logs"


def _job_log_path(job_id: str) -> str:
    return os.path.join(_JOB_LOG_DIR, f"{job_id}.log")


def job_submit(fn: callable, description: str, actor: str = "web") -> str:
    """Submit a function to run in a background thread. Returns job_id."""
    _ensure_zm_dirs()
    os.makedirs(_JOB_LOG_DIR, mode=0o700, exist_ok=True)
    job_id = f"job_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    log_path = _job_log_path(job_id)

    _JOBS[job_id] = {
        "id": job_id,
        "description": description,
        "actor": actor,
        "status": "queued",
        "started": None,
        "finished": None,
        "success": None,
        "log_path": log_path,
    }

    def _run():
        _JOBS[job_id]["status"] = "running"
        _JOBS[job_id]["started"] = datetime.utcnow().isoformat() + "Z"
        try:
            with open(log_path, "w", buffering=1) as log_f:
                # Redirect stdout/stderr to log file for the duration
                old_out, old_err = sys.stdout, sys.stderr

                class _Tee:
                    def __init__(self, original, logfile):
                        self.orig = original
                        self.log  = logfile
                    def write(self, t):
                        self.orig.write(t)
                        self.log.write(t)
                    def flush(self):
                        try: self.orig.flush()
                        except OSError: pass
                        try: self.log.flush()
                        except OSError: pass
                    def isatty(self): return False
                    def fileno(self): raise io.UnsupportedOperation()

                sys.stdout = _Tee(old_out, log_f)
                sys.stderr = _Tee(old_err, log_f)
                try:
                    fn()
                    _JOBS[job_id]["success"] = True
                except Exception:
                    traceback.print_exc()
                    _JOBS[job_id]["success"] = False
                finally:
                    sys.stdout = old_out
                    sys.stderr = old_err
        except Exception:
            _JOBS[job_id]["success"] = False
        finally:
            _JOBS[job_id]["status"]   = "done"
            _JOBS[job_id]["finished"] = datetime.utcnow().isoformat() + "Z"

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return job_id


def job_status(job_id: str) -> dict | None:
    return _JOBS.get(job_id)


def job_log_tail(job_id: str, from_offset: int = 0) -> tuple[str, int]:
    """Return (new_log_text, new_offset) for incremental streaming."""
    log_path = _job_log_path(job_id)
    try:
        with open(log_path, "r", errors="replace") as f:
            f.seek(from_offset)
            text = f.read()
            return text, f.tell()
    except Exception:
        return "", from_offset


def _startup_journal_recovery():
    """Scan journal for non-terminal transactions and raise critical incidents."""
    global _SAFE_MODE
    if not os.path.isdir(ZM_JOURNAL_DIR):
        return
    terminal = {_TxState.COMMITTED.value, _TxState.ROLLED_BACK.value,
                _TxState.ROLLBACK_FAILED.value}
    for entry in os.scandir(ZM_JOURNAL_DIR):
        mf = os.path.join(entry.path, "manifest.json")
        try:
            with open(mf) as f:
                m = json.load(f)
        except Exception:
            continue
        if m.get("state") not in terminal:
            _raise_incident(
                f"interrupted_tx:{m['id']}",
                severity="critical",
                evidence=f"Transaction '{m.get('description', m['id'])}' was "
                         f"interrupted in state '{m.get('state')}'.",
                suggested_action="Roll back, continue, or ignore from the Recovery menu.",
                tx_id=m["id"],
            )
            _SAFE_MODE = True


def is_safe_mode() -> bool:
    return _SAFE_MODE


def safe_mode_on():
    global _SAFE_MODE
    _SAFE_MODE = True


def safe_mode_off():
    global _SAFE_MODE
    _SAFE_MODE = False


# ─────────────────────────────────────────
#  Phase 5/9 — Version handshake
# ─────────────────────────────────────────

API_VERSION = "2.0.0"   # increment when the API surface changes


# ─────────────────────────────────────────
#  CLI — Recovery commands (zervermanager snapshot/changes/trash/doctor/audit)
# ─────────────────────────────────────────

def cli_snapshot_create(label: str = "", deep: bool = False) -> str:
    sid = _snapshot_create(label=label or "manual", trigger="manual")
    print(f"  Snapshot created: {sid}")
    return sid


def cli_snapshot_list():
    for s in snapshot_list():
        lkg = " [LKG]" if s.get("lkg") else ""
        print(f"  {s['id']}  {s.get('created','?')}  {s.get('trigger','?')}  "
              f"{s.get('label','')}  {s.get('size_bytes',0)//1024} KB{lkg}")


def cli_snapshot_diff(snap_id: str):
    print(snapshot_diff(snap_id))


def cli_snapshot_restore(snap_id: str, only: list[str] | None = None, dry_run: bool = False):
    ok_flag = snapshot_restore(snap_id, only=only, dry_run=dry_run)
    if ok_flag:
        print(f"  Snapshot {snap_id} restored.")
    else:
        print(f"  Failed to restore snapshot {snap_id}.")


def cli_audit_tail(n: int = 50):
    try:
        with open(ZM_AUDIT_LOG, "r") as f:
            lines = f.readlines()
        for line in lines[-n:]:
            try:
                e = json.loads(line)
                print(f"  {e.get('ts','')}  {e.get('actor','')}  "
                      f"{e.get('action','')}  [{e.get('result','')}]")
            except Exception:
                print(f"  {line.strip()}")
    except FileNotFoundError:
        print("  Audit log is empty.")


def cli_doctor(subsystems: list[str] | None = None, fix: bool = False):
    """Generalised check/repair."""
    targets = subsystems or ["nginx", "apache2", "named", "postfix", "dovecot"]
    for name in targets:
        result = validate_subsystem(name)
        if result.ok:
            print(f"  \033[92m✓ {name}: ok\033[0m")
        else:
            print(f"  \033[91m✗ {name}: {result.msg}\033[0m")


def cli_auth_reset(new_password: str):
    auth_reset(new_password)
    print("  Admin password updated.")


# ─────────────────────────────────────────
#  Startup self-check (Phase 0 / §9.10)
# ─────────────────────────────────────────

def startup_selfcheck():
    """Verify that all functions referenced in the handler table actually exist.
    Prints a warning for each missing function but does not abort."""
    _HANDLER_NAMES = [
        "create_full_site", "create_lamp_site", "create_lemp_site",
        "create_forward_zone", "create_reverse_zone",
        "setup_mail_server", "add_mail_user", "delete_mail_user",
        "setup_phpmyadmin", "delete_phpmyadmin",
        "setup_ftp_server", "delete_ftp_server",
        "setup_samba", "delete_samba",
        "install_docker", "docker_system_cleanup",
        "mariadb_create_database", "mariadb_drop_database",
        "mariadb_list_databases", "mariadb_create_user", "mariadb_drop_user",
        "diagnose_mail", "repair_mail",
        "reload_all_services",
    ]
    import builtins
    g = globals()
    missing = [name for name in _HANDLER_NAMES
               if name not in g and not hasattr(builtins, name)]
    for name in missing:
        print(f"  \033[93m! startup_selfcheck: function '{name}' not found "
              f"(referenced in handler table but not defined).\033[0m")
    return missing


# ─────────────────────────────────────────
#  Web-API helpers (new endpoints from §7)
# ─────────────────────────────────────────

def _web_json(handler, obj: dict | list, code: int = 200):
    body = json.dumps(obj).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _web_read_json(handler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def web_handle_auth(handler, path: str) -> bool:
    """Handle /api/login, /api/logout, /api/session.  Returns True if handled."""
    if path == "/api/login":
        data = _web_read_json(handler)
        ok_flag, result = auth_login(
            data.get("username", ""),
            data.get("password", ""),
            handler.client_address[0],
        )
        if ok_flag:
            sid = result
            sess = _SESSION_STORE[sid]
            body = json.dumps({"ok": True, "csrf": sess["csrf"],
                               "actor": sess["actor"], "role": sess["role"]}).encode()
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json")
            handler.send_header(
                "Set-Cookie",
                f"zm_session={sid}; HttpOnly; SameSite=Strict; Path=/"
            )
            handler.end_headers()
            handler.wfile.write(body)
        else:
            _web_json(handler, {"ok": False, "error": result}, 401)
        return True

    if path == "/api/logout":
        cookie = handler.headers.get("Cookie", "")
        sid = ""
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("zm_session="):
                sid = part[len("zm_session="):]
        auth_logout(sid)
        handler.send_response(200)
        handler.send_header("Set-Cookie", "zm_session=; Max-Age=0; Path=/")
        handler.end_headers()
        return True

    if path == "/api/session":
        cookie = handler.headers.get("Cookie", "")
        sid = ""
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("zm_session="):
                sid = part[len("zm_session="):]
        sess = auth_session(sid)
        if sess:
            _web_json(handler, {"ok": True, "actor": sess["actor"],
                                "role": sess["role"], "csrf": sess["csrf"]})
        else:
            _web_json(handler, {"ok": False}, 401)
        return True

    return False


def web_handle_new_api(handler, path: str) -> bool:
    """Handle new Phase 2–6 API endpoints. Returns True if handled."""

    # ── Health summary (Phase 3/5) ────────────────────────────────────────
    if path == "/api/health":
        open_incidents = incidents_list("open")
        high_count = sum(1 for i in open_incidents if i["severity"] in ("high", "critical"))
        _web_json(handler, {
            "ok": len(open_incidents) == 0,
            "open_incidents": len(open_incidents),
            "high_severity": high_count,
            "safe_mode": is_safe_mode(),
            "api_version": API_VERSION,
            "backend_version": globals().get("SCRIPT_VERSION", "?"),
        })
        return True

    # ── Incidents (Phase 3) ────────────────────────────────────────────────
    if path == "/api/incidents":
        _web_json(handler, incidents_list())
        return True

    if path.startswith("/api/incidents/") and (path.endswith("/ack") or path.endswith("/snooze")):
        iid = path.split("/")[3]
        incident_ack(iid)
        _web_json(handler, {"ok": True})
        return True

    # ── Snapshots (Phase 2) ────────────────────────────────────────────────
    if path == "/api/snapshots" and handler.command == "GET":
        _web_json(handler, snapshot_list())
        return True

    if path == "/api/snapshots" and handler.command == "POST":
        data = _web_read_json(handler)
        sid = _snapshot_create(label=data.get("label", ""), trigger="manual")
        _web_json(handler, {"id": sid})
        return True

    if re.match(r"^/api/snapshots/[^/]+/diff$", path):
        snap_id = path.split("/")[3]
        _web_json(handler, {"diff": snapshot_diff(snap_id)})
        return True

    if re.match(r"^/api/snapshots/[^/]+/restore$", path):
        snap_id = path.split("/")[3]
        data = _web_read_json(handler)
        dry  = data.get("dry_run", False)
        only = data.get("only")
        ok_flag = snapshot_restore(snap_id, only=only, dry_run=dry)
        _web_json(handler, {"ok": ok_flag})
        return True

    # ── Jobs (Phase 5) ─────────────────────────────────────────────────────
    if re.match(r"^/api/jobs/[^/]+$", path):
        job_id = path.split("/")[3]
        st = job_status(job_id)
        if st:
            _web_json(handler, st)
        else:
            _web_json(handler, {"error": "not found"}, 404)
        return True

    if re.match(r"^/api/jobs/[^/]+/log$", path):
        import urllib.parse as _up
        job_id = path.split("/")[3]
        parsed = _up.urlparse(handler.path)
        qs = _up.parse_qs(parsed.query)
        offset = int(qs.get("from", ["0"])[0])
        text, new_offset = job_log_tail(job_id, offset)
        _web_json(handler, {"text": text, "offset": new_offset})
        return True

    # ── Trash (Phase 6) ────────────────────────────────────────────────────
    if path == "/api/trash" and handler.command == "GET":
        _web_json(handler, trash_list())
        return True

    if re.match(r"^/api/trash/[^/]+/restore$", path):
        tid = path.split("/")[3]
        ok_flag = trash_restore(tid)
        _web_json(handler, {"ok": ok_flag})
        return True

    # ── Dead-man's confirmation (Phase 4) ─────────────────────────────────
    if re.match(r"^/api/confirm/[^/]+$", path):
        tx_id = path.split("/")[3]
        confirmed = deadman_confirm(tx_id)
        _web_json(handler, {"confirmed": confirmed})
        return True

    # ── Plan/Apply (Phase 0) ───────────────────────────────────────────────
    if path == "/api/plan":
        data = _web_read_json(handler)
        pid = plan_create(
            action=data.get("action", ""),
            data=data,
            tier=data.get("tier", 1),
            impact=data.get("impact", []),
        )
        _web_json(handler, {"plan_id": pid, "ttl": _PLAN_TTL})
        return True

    if path == "/api/apply":
        data = _web_read_json(handler)
        plan = plan_consume(data.get("plan_id", ""))
        if not plan:
            _web_json(handler, {"error": "invalid, expired, or already-used plan_id"}, 400)
            return True
        # Execute the stored plan — for now return the plan data; actual execution
        # is done by the existing handlers after confirming via plan_id.
        _web_json(handler, {"ok": True, "plan": plan})
        return True

    # ── Doctor (Phase 5/9) ─────────────────────────────────────────────────
    if re.match(r"^/api/doctor/[^/]+/(check|repair)$", path):
        parts = path.strip("/").split("/")
        subsystem = parts[2]
        action    = parts[3]
        if action == "check":
            result = validate_subsystem(subsystem)
            _web_json(handler, {"ok": result.ok, "msg": result.msg})
        else:
            result = validate_subsystem(subsystem)
            _web_json(handler, {"ok": result.ok, "msg": result.msg})
        return True

    # ── Changes / undo (Phase 1/3) ─────────────────────────────────────────
    if path == "/api/changes":
        # List recent transactions from journal
        txs = []
        if os.path.isdir(ZM_JOURNAL_DIR):
            for entry in sorted(os.scandir(ZM_JOURNAL_DIR),
                                key=lambda e: e.name, reverse=True)[:20]:
                mf = os.path.join(entry.path, "manifest.json")
                try:
                    with open(mf) as f:
                        txs.append(json.load(f))
                except Exception:
                    pass
        _web_json(handler, txs)
        return True

    # ── Auth management ────────────────────────────────────────────────────
    if path == "/api/auth/reset":
        data = _web_read_json(handler)
        new_pw = data.get("password", "")
        if len(new_pw) < 8:
            _web_json(handler, {"error": "Password must be at least 8 characters."}, 400)
            return True
        auth_reset(new_pw)
        _web_json(handler, {"ok": True})
        return True

    return False


def detect_server_ip():
    """Return the primary non‑loopback IPv4 address of the server, or None.
    Uses a UDP socket to an external address to discover the outbound interface.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None

# ─────────────────────────────────────────
#  Web→CLI parameter bridge
# ─────────────────────────────────────────
# The web UI used to drive interactive CLI creators by faking `input()`
# answers in strict hand-counted sequence (MockStdin): adding one prompt
# anywhere silently re-routed every later web answer. The bridge instead
# consumes answers BY KEY: every prompt point declares a semantic key and
# the web handler registers its form values under the same keys. The CLI
# path behaves exactly as before.

_WEB_ANSWERS = None  # dict | None

def _ask(prompt, key=None):
    """input() that first consumes a keyed answer from the web bridge."""
    if _WEB_ANSWERS is not None and key is not None and key in _WEB_ANSWERS:
        val = _WEB_ANSWERS.pop(key)
        # Narrate the prompt+answer so streamed web output stays readable.
        print(prompt, end="")
        if val:
            print(val)
        return val
    return input(prompt)

def input_pause(prompt="\n  Press Enter to continue..."):
    """Press-enter pause that is a no-op read when the web bridge drives us."""
    if _WEB_ANSWERS is not None:
        print(prompt)
        return ""
    return input(prompt)

@contextlib.contextmanager
def _web_prompt_driver(answers):
    """Install the web answers bridge for one request."""
    global _WEB_ANSWERS
    old, _WEB_ANSWERS = _WEB_ANSWERS, answers
    try:
        yield
    finally:
        _WEB_ANSWERS = old

def prompt_for_ip(msg="Server IP"):
    auto_ip = detect_server_ip()
    prompt_str = f"{msg} [{auto_ip}]: " if auto_ip else f"{msg}: "
    val = _ask(prompt_str, key="ip").strip()
    if _WEB_ANSWERS is not None and val == "":
        return val
    return val if val else auto_ip


def ask_domain(prompt="  Domain: "):
    val = _ask(prompt, key="domain").strip()
    if _WEB_ANSWERS is not None:
        if not val or not re.match(r"^[a-zA-Z0-9.-]+$", val):
            if val:
                err("Invalid domain. Only letters, numbers, dots, and hyphens are allowed.")
            return ""
        return val
    while True:
        if not val:
            return ""
        if re.match(r"^[a-zA-Z0-9.-]+$", val):
            return val
        err("Invalid domain. Only letters, numbers, dots, and hyphens are allowed.")
        val = input(prompt).strip()

def _bridge_valid(val, pattern, what):
    """Validate a bridged (non-interactive) answer once; refuse re-prompting."""
    val = val.strip()
    if not val:
        return ""
    if not re.match(pattern, val):
        err(f"Invalid {what}. Using empty value.")
        return ""
    return val

def ask_db_name(prompt="Database name", default=""):
    prompt_str = f"  {prompt} [{default}]: " if default else f"  {prompt}: "
    if _WEB_ANSWERS is not None:
        val = _bridge_valid(_ask(prompt_str, key="db_name"),
                            r"^[a-zA-Z0-9_]+$", "database name")
        return val or default
    while True:
        val = input(prompt_str).strip()
        val = val or default
        if not val:
            return ""
        if re.match(r"^[a-zA-Z0-9_]+$", val):
            return val
        err("Invalid database name. Only letters, numbers, and underscores are allowed.")

def ask_db_user(prompt="Database user", default=""):
    prompt_str = f"  {prompt} [{default}]: " if default else f"  {prompt}: "
    if _WEB_ANSWERS is not None:
        val = _bridge_valid(_ask(prompt_str, key="db_user"),
                            r"^[a-zA-Z0-9_]+$", "database user")
        return val or default
    while True:
        val = input(prompt_str).strip()
        val = val or default
        if not val:
            return ""
        if re.match(r"^[a-zA-Z0-9_]+$", val):
            return val
        err("Invalid database user. Only letters, numbers, and underscores are allowed.")


def validate_docroot(path):
    if not path:
        return False, "Document root cannot be empty."
    p = os.path.abspath(path)
    protected = {
        "/", "/etc", "/var", "/usr", "/bin", "/sbin", "/lib", "/boot", 
        "/proc", "/sys", "/dev", "/home", "/root", "/run", "/tmp", "/srv",
        "/var/www"
    }
    if p in protected:
        return False, f"Cannot use protected system directory: {p}"
    parts = [part for part in p.split("/") if part]
    if len(parts) < 2:
        return False, f"Path too shallow: {p}"
    return True, ""

def ask_docroot(prompt, default=""):
    # Ensure no leading/trailing spaces in the prefix, we format it consistently
    clean_prompt = prompt.strip()
    prompt_str = f"  {clean_prompt} [{default}]: " if default else f"  {clean_prompt}: "
    if _WEB_ANSWERS is not None:
        val = _ask(prompt_str, key="docroot").strip() or default
        valid, msg = validate_docroot(val)
        if valid:
            return val
        print(f"  \033[91m✗ {msg}\033[0m")
        return ""
    while True:
        val = input(prompt_str).strip()
        val = val or default
        valid, msg = validate_docroot(val)
        if valid:
            return val
        print(f"  \033[91m✗ {msg}\033[0m")

def safe_write(filepath, content):
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        if not str(filepath).endswith('.json'):
            backup_config(filepath)
        if DRY_RUN:
            step(f"[dry-run] Would write to { filepath }")
        else:
            with open(filepath, "w") as f:
                f.write(content)
        return True
    except Exception as e:
        err(f"Error writing to {filepath}: {e}")
        return False

# ─────────────────────────────────────────
#  Config
# ─────────────────────────────────────────

APACHE_SITES_AVAILABLE = "/etc/apache2/sites-available"
APACHE_SITES_ENABLED   = "/etc/apache2/sites-enabled"
NAMED_CONF_LOCAL = "/etc/bind/named.conf.local"
BIND_DIR         = "/etc/bind"
META_DIR         = "/etc/servermanager/sites"
NGINX_SITES_AVAILABLE = "/etc/nginx/sites-available"
NGINX_SITES_ENABLED   = "/etc/nginx/sites-enabled"

# Set to True via --dry-run CLI flag; makes run()/run_live() print-and-no-op.
DRY_RUN = False

# configure_apache_ports() moves Apache to 8080 (HTTP) / 8443 (HTTPS) and puts nginx
# in front of it, and every nginx proxy template points at 127.0.0.1:8080.
# Nothing else may ever claim these ports: not this web UI, not Docker sites.
APACHE_BACKEND_PORTS = (8080, 8443)
WEB_UI_DEFAULT_PORT = 8090
SHOW_SPLASH = True        # set to False to disable the splash screen
EASTER_EGG_ENABLED = True # set to False to disable the easter egg entirely
MAI_PERSONAL_MESSAGE = "[ I love you, Mai ]"   # ← replace this with your own words
current_menu_width = 45

# ─────────────────────────────────────────
#  Colors
# ─────────────────────────────────────────

class C:
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"

def ok(msg):   print(f"{C.GREEN}  ✓ {msg}{C.RESET}")
def err(msg):  print(f"{C.RED}  ✗ {msg}{C.RESET}")
def warn(msg): print(f"{C.YELLOW}  ! {msg}{C.RESET}")
def info(msg): print(f"{C.BLUE}  → {msg}{C.RESET}")
def step(msg): print(f"{C.CYAN}  • {msg}{C.RESET}")
def bold(msg): print(f"{C.BOLD}{msg}{C.RESET}")

def backup_config(path):
    """
    Creates a timestamped .bak copy of a config file before it is overwritten.
    Does nothing if the file does not exist yet (new file creation).
    Respects DRY_RUN mode.
    """
    if not os.path.isfile(path):
        return  # file doesn't exist yet, nothing to back up
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{path}.{timestamp}.bak"
    if DRY_RUN:
        step(f"[DRY RUN] Would backup: {path} → {backup_path}")
        return
    shutil.copy2(path, backup_path)
    step(f"Backed up: {backup_path}")



# ─────────────────────────────────────────
#  Splash screen
# ─────────────────────────────────────────

HEART = "♥"
# HEART = "<3"  # ASCII fallback for older terminals

def show_splash():
    """Display a brief splash screen before the main loading sequence."""
    DIVIDER = "─" * 48
    couple_line = f"ZFRFRK {HEART} Ouzuka Mai"

    print()
    print(f"{C.BOLD}  Zervermanager{C.RESET}")
    print("  An automated Debian 11/12/13 server setup & management tool")
    print(f"{C.CYAN}  {DIVIDER}{C.RESET}")
    print(f"  Version     : v{SCRIPT_VERSION}")
    print("  Created by  : ZFRFRK")
    print("  GitHub      : github.com/ZFRFRK/Zervermanager")
    print(f"{C.CYAN}  {DIVIDER}{C.RESET}")
    print(f"{C.DIM}{couple_line.center(52)}{C.RESET}")
    print()

    # OPTION A — Fixed duration (recommended)
    time.sleep(1.5)
    # OPTION B — Wait for keypress
    # input("  Press Enter to continue...")
    # OPTION C — No delay (useful for testing)
    # pass


def show_mai_easter_egg():
    """Hidden tribute screen — triggered by typing 0831 at the main menu."""
    import datetime
    DIVIDER = "─" * 48
    BORDER = "✦ ══════════════════════════════ ✦"
    couple_line = f"ZFRFRK {HEART} Ouzuka Mai"

    print()
    print(f"{C.CYAN}{BORDER.center(52)}{C.RESET}")
    print(f"{C.BOLD}  ", end="")
    for char in "Oozuka Mai":
        print(char, end="", flush=True)
        time.sleep(0.07)
    print("  王塚真唯" + C.RESET)
    print(f"  {C.DIM}Super School Darling{C.RESET}")
    print(f"  {C.DIM}August 31{C.RESET}")
    print(f"{C.CYAN}  {DIVIDER}{C.RESET}")
    
    quote = "\"Somehow, it seems like I've fallen in love with you.\""
    print(f"{C.DIM}{quote.center(52)}{C.RESET}")
    print(f"{C.CYAN}  {DIVIDER}{C.RESET}")
    print(f"{MAI_PERSONAL_MESSAGE.center(52)}")
    print(f"{C.CYAN}  {DIVIDER}{C.RESET}")
    
    today = datetime.date.today()
    if today.month == 8 and today.day == 31:
        print("✦ Happy Birthday, Mai ✦".center(52))
        
    print(f"{C.DIM}{couple_line.center(52)}{C.RESET}")
    print(f"{C.CYAN}{BORDER.center(52)}{C.RESET}")
    print()

    # OPTION A — Fixed duration: show for N seconds then return to main menu
    time.sleep(4)
    # OPTION B — Wait for keypress
    # input("  Press Enter to continue...")
    # OPTION C — No delay (useful for testing)
    # pass


# ─────────────────────────────────────────
#  Reload tracker
# ─────────────────────────────────────────

_pending = set()

def prompt_confirm(msg, default="no", key=None):
    """Ask a yes/no question via input() and return True/False."""
    prompt = f"  {msg} (Y/n): " if default == "yes" else f"  {msg} (y/N): "
    val = _ask(prompt, key=key).strip().lower()
    if not val:
        return default == "yes"
    return val in ("y", "yes")

def menu_header(title):
    """Print a standardized bold menu header with a separator."""
    global current_menu_width
    # Strip any ANSI escape sequences from the title
    clean_title = re.sub(r"\x1b\[[0-9;]*m", "", title)
    clean_title = re.sub(r"\033\[[0-9;]*m", "", clean_title)

    # Check if "Made by ZFRFRK" is already in the title to avoid doubling it up
    if "Made by ZFRFRK" in clean_title:
        title = re.split(r"Made by ZFRFRK", title)[0].strip()
        clean_title = re.split(r"Made by ZFRFRK", clean_title)[0].strip()
        title = re.sub(r"\s+$", "", title)
        clean_title = re.sub(r"\s+$", "", clean_title)

    # Dynamic layout: fit title on left, "Made by ZFRFRK" on right
    # minimum width of 45 characters. Title and credit must have at least 2 spaces between them.
    credit_len = 14  # len("Made by ZFRFRK")
    current_menu_width = max(45, len(clean_title) + credit_len + 2)
    spacing = current_menu_width - len(clean_title) - credit_len

    print(f"\n{C.BOLD}{title}{' ' * spacing}{C.BLUE}Made by ZFRFRK{C.RESET}")
    print("━" * current_menu_width)

def menu_separator():
    """Print a separator line matching the current menu width."""
    print("━" * current_menu_width)

def mark_reload(service):
    _pending.add(service)

def apply_reloads():
    if not _pending:
        return

    print()

    if "apache2" in _pending:
        print(f"  {C.CYAN}Reloading Apache2...{C.RESET}", end=" ", flush=True)
        _is_active = run(["systemctl", "is-active", "apache2"]).stdout.strip() == "active"
        _action = "reload" if _is_active else "restart"
        result = run(["systemctl", _action, "apache2"])

        if result.returncode == 0:
            print(f"{C.GREEN}✓{C.RESET}")
        else:
            print(f"{C.RED}✗{C.RESET}")
            err(result.stderr.strip())

    if "bind9" in _pending:
        print(f"  {C.CYAN}Reloading BIND9...{C.RESET}", end=" ", flush=True)
        _bind9_svc = get_bind9_service_name()
        _is_active = run(["systemctl", "is-active", _bind9_svc]).stdout.strip() == "active"
        _action = "reload" if _is_active else "restart"
        result = run(["systemctl", _action, _bind9_svc])

        if result.returncode == 0:
            print(f"{C.GREEN}✓{C.RESET}")
        else:
            print(f"{C.RED}✗{C.RESET}")
            err(result.stderr.strip())

    if "nginx" in _pending:
        print(f"  {C.CYAN}Reloading Nginx...{C.RESET}", end=" ", flush=True)
        _is_active = run(["systemctl", "is-active", "nginx"]).stdout.strip() == "active"
        _action = "reload" if _is_active else "restart"
        result = run(["systemctl", _action, "nginx"])

        if result.returncode == 0:
            print(f"{C.GREEN}✓{C.RESET}")
        else:
            print(f"{C.RED}✗{C.RESET}")
            err(result.stderr.strip())


# ─────────────────────────────────────────
#  Shared helpers
# ─────────────────────────────────────────

class _MockProcessResult:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

def run(cmd, timeout=None):
    """Silent subprocess wrapper.  In --dry-run mode prints the command instead."""
    if DRY_RUN:
        print(f"  {C.DIM}[dry-run] Would run: {' '.join(str(c) for c in cmd)}{C.RESET}")
        return _MockProcessResult(0, "", "")
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return _MockProcessResult(127, "", f"Command not found: {cmd[0]}")
    except subprocess.TimeoutExpired:
        return _MockProcessResult(
            124, "", f"Timed out after {timeout}s: {' '.join(str(c) for c in cmd)}")


# Short-TTL cache of systemd unit states, shared by the web status API and the
# watchdog so both ask systemd once per TTL instead of per unit per call.
_SVC_STATE_CACHE = {"ts": 0.0, "data": {}}

def service_statuses(names, ttl=3.0):
    """One batched `systemctl is-active` for many units, cached for `ttl` seconds.

    Returns {name: state} with state in active/inactive/failed/... (raw systemd).
    A missing/stalled systemd never hangs: the batch gets a hard timeout.
    """
    names = list(names)
    now = time.time()
    if now - _SVC_STATE_CACHE["ts"] < ttl:
        return {n: _SVC_STATE_CACHE["data"].get(n, "inactive") for n in names}
    result = run(["systemctl", "is-active"] + names, timeout=10)
    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    while len(lines) < len(names):
        lines.append("inactive")
    _SVC_STATE_CACHE["ts"] = time.time()
    _SVC_STATE_CACHE["data"] = dict(zip(names, lines))
    return {n: _SVC_STATE_CACHE["data"].get(n, "inactive") for n in names}


def run_live(cmd):
    """Run a command and stream its output in real time.
    Returns (returncode, stderr_string).
    Only shows meaningful lines — filters apt/dpkg noise.
    In --dry-run mode prints the command and returns success immediately.
    """
    if DRY_RUN:
        print(f"  {C.DIM}[dry-run] Would run (live): {' '.join(str(c) for c in cmd)}{C.RESET}")
        return 0, ""

    SHOW_PREFIXES = (
        "Get:", "Fetched", "Unpacking", "Setting up",
        "Processing", "Reading", "Building", "Selecting",
        "Preparing", "Removing", "Update",
    )

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
    except FileNotFoundError:
        return 127, f"Command not found: {cmd[0]}"

    for line in process.stdout:
        line = line.rstrip()
        if line and any(line.startswith(p) for p in SHOW_PREFIXES):
            print(f"    {C.DIM}{line}{C.RESET}")

    _, stderr = process.communicate()
    return process.returncode, stderr


def get_serial(zonefile=None):
    """SOA serial that always advances: max(existing serial + 1, today*100).

    The zone's current serial (if readable) is bumped by one so repeated
    edits on the same day always propagate; across days the date base takes
    over. Accepts the zone file path so edits stay monotonic per zone.
    """
    try:
        base = int(datetime.now().strftime("%Y%m%d")) * 100
    except Exception:
        base = int(time.time() % 9000000000) * 10
    if zonefile and os.path.isfile(zonefile):
        try:
            with open(zonefile, "r", encoding="utf-8", errors="replace") as _zf:
                m = re.search(r"(?m)^\s*(\d{1,10})\s*;\s*Serial", _zf.read())
            if m:
                base = max(base, int(m.group(1)) + 1)
        except OSError:
            pass
    return str(base)


def service_status(name, color=True):
    state = service_statuses([name]).get(name, "inactive")
    active = state == "active"
    if not color:
        return "running" if active else "stopped"
    label = f"{C.GREEN}running{C.RESET}" if active else f"{C.RED}stopped{C.RESET}"
    return label


# ─────────────────────────────────────────
#  Site metadata (type tracking)
# ─────────────────────────────────────────

def save_site_meta(domain, site_type, docroot, db_name=None, db_user=None, web_server="apache", dns=False, container_name=None, host_port=None):
    os.makedirs(META_DIR, exist_ok=True)

    meta = {
        "type":       site_type,
        "domain":     domain,
        "docroot":    docroot,
        "web_server": web_server,
        "created":    datetime.now().strftime("%Y-%m-%d %H:%M"),
        "dns":        dns,                     # MODIFICATION
    }

    if db_name:
        meta["db_name"] = db_name
    if db_user:
        meta["db_user"] = db_user
    if container_name:
        meta["container_name"] = container_name
    if host_port:
        meta["host_port"] = host_port

    safe_write(f"{META_DIR}/{domain}.json", json.dumps(meta, indent=2))


def get_site_meta(domain):
    path = f"{META_DIR}/{domain}.json"

    if not os.path.isfile(path):
        return None

    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def delete_site_meta(domain):
    path = f"{META_DIR}/{domain}.json"

    if os.path.isfile(path):
        os.remove(path)


def site_type_label(domain):
    meta = get_site_meta(domain)

    if not meta:
        return f"{C.DIM}[unknown]{C.RESET}"

    t = meta.get("type", "unknown")

    if t == "lamp":
        return f"{C.BLUE}[LAMP]{C.RESET}"
    elif t == "static":
        return f"{C.DIM}[static]{C.RESET}"
    elif t == "docker":
        return f"{C.BLUE}[docker]{C.RESET}"

    return f"{C.DIM}[{t}]{C.RESET}"


# ─────────────────────────────────────────
#  Apache helpers
# ─────────────────────────────────────────

def configure_apache_ports():
    ports_conf = "/etc/apache2/ports.conf"
    if os.path.exists(ports_conf):
        with open(ports_conf, "r") as f:
            content = f.read()
        if "Listen 8080" not in content:
            content = re.sub(r"^(Listen\s+)80$", r"\g<1>8080", content, flags=re.MULTILINE)
            content = re.sub(r"^(Listen\s+)443$", r"\g<1>8443", content, flags=re.MULTILINE)
            if not DRY_RUN:
                with open(ports_conf, "w") as f:
                    f.write(content)
            else:
                step(f"[dry-run] Would update ports in {ports_conf}")
    
    default_conf = "/etc/apache2/sites-available/000-default.conf"
    if os.path.exists(default_conf):
        with open(default_conf, "r") as f:
            content = f.read()
        if "<VirtualHost *:8080>" not in content:
            content = re.sub(r"<VirtualHost \*:80>", "<VirtualHost *:8080>", content)
            if not DRY_RUN:
                with open(default_conf, "w") as f:
                    f.write(content)

    # Silence AH00558 ("Could not reliably determine the server's fully qualified
    # domain name") by giving Apache a global ServerName — unless one already exists.
    servername_conf = "/etc/apache2/conf-available/servername.conf"
    if not DRY_RUN and os.path.isdir("/etc/apache2/conf-available") and not os.path.exists(servername_conf):
        try:
            existing = ""
            for pth in ["/etc/apache2/apache2.conf"] + glob.glob("/etc/apache2/conf-enabled/*.conf"):
                try:
                    with open(pth, "r") as f:
                        existing += f.read()
                except OSError:
                    pass
            if not re.search(r"^\s*ServerName\s", existing, re.MULTILINE):
                with open(servername_conf, "w") as f:
                    f.write("ServerName localhost\n")
                subprocess.run(["a2enconf", "servername"], capture_output=True)
        except Exception:
            pass

def apache_test():
    return run(["/usr/sbin/apache2ctl", "configtest"])


def validate_apache():
    test = apache_test()

    if test.returncode != 0:
        return False, test.stdout + test.stderr

    return True, "Syntax OK"


def reload_apache():
    mark_reload("apache2")


def ensure_modules():
    for mod in ("rewrite", "ssl"):
        run(["/usr/sbin/a2enmod", mod])


def enable_site(domain):
    return run(["/usr/sbin/a2ensite", f"{domain}.conf"])


def disable_site_cmd(domain):
    return run(["/usr/sbin/a2dissite", f"{domain}.conf"])


def write_vhost(domain, content):
    path = f"{APACHE_SITES_AVAILABLE}/{domain}.conf"

    safe_write(path, content)

    return path


def create_docroot(docroot, domain):
    Path(docroot).mkdir(parents=True, exist_ok=True)

    index = Path(docroot) / "index.html"

    if not index.exists():
        index.write_text(
            f"<html><body><h1>{domain}</h1></body></html>"
        )


def http_vhost(domain, docroot):
    return f"""
<VirtualHost *:8080>
    ServerName {domain}
    ServerAlias www.{domain}

    DocumentRoot "{docroot}"

    <Directory "{docroot}">
        Options Indexes FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>
</VirtualHost>
"""


def https_vhost(domain, docroot, cert, key):
    return f"""
<VirtualHost *:8080>
    ServerName {domain}
    ServerAlias www.{domain}

    RewriteEngine On
    RewriteRule ^ https://%{{HTTP_HOST}}%{{REQUEST_URI}} [R=301,L]
</VirtualHost>

<VirtualHost *:8443>
    ServerName {domain}
    ServerAlias www.{domain}

    DocumentRoot "{docroot}"

    SSLEngine On
    SSLCertificateFile {cert}
    SSLCertificateKeyFile {key}

    <Directory "{docroot}">
        Options Indexes FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>
</VirtualHost>
"""


def make_self_signed(domain):
    ssl_dir = f"/etc/ssl/{domain}"
    os.makedirs(ssl_dir, exist_ok=True)

    cert = f"{ssl_dir}/{domain}.crt"
    key  = f"{ssl_dir}/{domain}.key"

    result = run([
        "openssl", "req", "-x509", "-nodes",
        "-days", "365", "-newkey", "rsa:2048",
        "-keyout", key,
        "-out", cert,
        "-subj", f"/CN={domain}"
    ])

    if result.returncode != 0:
        return None, None, result.stderr

    return cert, key, None

# ─────────────────────────────────────────
#  Nginx helpers
# ─────────────────────────────────────────

def reload_nginx():
    mark_reload("nginx")


def validate_nginx():
    result = run(["/usr/sbin/nginx", "-t"])
    if result.returncode != 0:
        return False, result.stdout + result.stderr
    return True, "Syntax OK"


def nginx_site_enabled(domain):
    return os.path.islink(f"{NGINX_SITES_ENABLED}/{domain}.conf")


def nginx_enable_site(domain):
    src  = f"{NGINX_SITES_AVAILABLE}/{domain}.conf"
    dest = f"{NGINX_SITES_ENABLED}/{domain}.conf"
    if not os.path.islink(dest):
        os.symlink(src, dest)


def nginx_disable_site(domain):
    dest = f"{NGINX_SITES_ENABLED}/{domain}.conf"
    if os.path.islink(dest):
        os.unlink(dest)


def ensure_nginx():
    """Check Nginx is installed; auto-install if missing. Also ensures directories exist."""
    def _installed(pkg):
        r = run(["dpkg", "-s", pkg])
        return r.returncode == 0 and "Status: install ok installed" in r.stdout

    if not _installed("nginx"):
        print(f"\n{C.YELLOW}  Nginx is not installed.{C.RESET}")
        print(f"  {C.CYAN}Auto-installing...{C.RESET}\n")

        rc, stderr = run_live(["apt-get", "install", "-y", "--no-install-recommends", "nginx"])

        if rc != 0:
            err(f"Install failed:\n{stderr.strip()}")
            return False

        r = run(["systemctl", "enable", "--now", "nginx"])
        ok("Nginx installed and enabled.") if r.returncode == 0 else err(r.stderr.strip())

    os.makedirs(NGINX_SITES_AVAILABLE, exist_ok=True)
    os.makedirs(NGINX_SITES_ENABLED, exist_ok=True)
    return True

def write_nginx_vhost(domain, content):
    os.makedirs(NGINX_SITES_AVAILABLE, exist_ok=True)
    path = f"{NGINX_SITES_AVAILABLE}/{domain}.conf"
    safe_write(path, content)
    return path


def nginx_http_vhost(domain, docroot):
    return f"""server {{
    listen 80;
    server_name {domain} www.{domain};

    root {docroot};
    index index.html index.php;

    location / {{
        try_files $uri $uri/ =404;
    }}
}}
"""


def nginx_https_vhost(domain, docroot, cert, key):
    return f"""server {{
    listen 80;
    server_name {domain} www.{domain};
    return 301 https://$host$request_uri;
}}

server {{
    listen 443 ssl;
    server_name {domain} www.{domain};

    root {docroot};
    index index.html index.php;

    ssl_certificate     {cert};
    ssl_certificate_key {key};

    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    location / {{
        try_files $uri $uri/ =404;
    }}
}}
"""

def detect_package_manager():
    """Return the primary package manager for the OS."""
    return "apt-get"

def detect_webserver_php_socket():
    """Probe /run/php/ for active php-fpm sockets."""
    sockets = sorted(glob.glob("/run/php/php*-fpm.sock"), reverse=True)
    if sockets:
        return sockets[0]
    return "/run/php/php8.2-fpm.sock"

def find_php_fpm_socket():
    """Locate the PHP-FPM unix socket, falling back to TCP."""
    sockets = sorted(glob.glob("/var/run/php/php*-fpm.sock"), reverse=True)
    if sockets:
        return sockets[0]
    
    # Fallback using our new probe
    probe = detect_webserver_php_socket()
    if os.path.isfile(probe):
        return probe
    
    return "127.0.0.1:9000"


def get_php_version():
    """Return the installed PHP MAJOR.MINOR version string (e.g. '8.5'), or None.
    Used by build_php_packages() to construct versioned package names on Ubuntu.
    """
    result = run(["php", "-r", "echo PHP_MAJOR_VERSION.'.'.PHP_MINOR_VERSION;"])
    if result.returncode == 0:
        ver = result.stdout.strip()
        if ver and "." in ver:
            return ver
    return None


def build_php_packages(base_names):
    """Return a list of apt package names for PHP extensions.

    On Ubuntu: rewrites generic names to versioned names using the detected PHP
    version (e.g. 'php-imap' -> 'php8.5-imap', 'php' -> 'php8.5').
    If PHP is not yet installed (version undetectable), returns base_names unchanged
    so apt can resolve the correct version itself.

    On Debian 12 / 11 / 13 and all other OS: always returns base_names unchanged.
    Generic unversioned names are correct on Debian and must not be modified.
    """
    status, _ = check_os_compatibility()
    if status != "ubuntu":
        # Debian (any codename) and all other OS — passthrough, no change.
        return base_names

    ver = get_php_version()
    if not ver:
        # Ubuntu but PHP not yet installed — let apt resolve the version.
        return base_names

    versioned = []
    try:
        ver_num = float(ver)
    except ValueError:
        ver_num = 0.0

    for pkg in base_names:
        if pkg == "php-imap" and ver_num >= 8.4:
            continue
        if pkg == "php":
            versioned.append(f"php{ver}")
        elif pkg.startswith("php-"):
            versioned.append(f"php{ver}-{pkg[4:]}")
        else:
            versioned.append(pkg)
    return versioned


def nginx_lemp_http_vhost(domain, docroot, fpm_socket):
    fpm = f"unix:{fpm_socket}" if fpm_socket.startswith("/") else fpm_socket
    return f"""server {{
    listen 80;
    server_name {domain} www.{domain};

    root {docroot};
    index index.php index.html;

    location / {{
        try_files $uri $uri/ =404;
    }}

    location ~ \\.php$ {{
        include snippets/fastcgi-php.conf;
        fastcgi_pass {fpm};
    }}

    location ~ /\\.ht {{
        deny all;
    }}
}}
"""


def nginx_lemp_https_vhost(domain, docroot, cert, key, fpm_socket):
    fpm = f"unix:{fpm_socket}" if fpm_socket.startswith("/") else fpm_socket
    return f"""server {{
    listen 80;
    server_name {domain} www.{domain};
    return 301 https://$host$request_uri;
}}

server {{
    listen 443 ssl;
    server_name {domain} www.{domain};

    root {docroot};
    index index.php index.html;

    ssl_certificate     {cert};
    ssl_certificate_key {key};
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    location / {{
        try_files $uri $uri/ =404;
    }}

    location ~ \\.php$ {{
        include snippets/fastcgi-php.conf;
        fastcgi_pass {fpm};
    }}

    location ~ /\\.ht {{
        deny all;
    }}
}}
"""

def pick_nginx_sites(show_enabled=True, show_disabled=True):
    """Numbered picker for Nginx sites. Returns list of selected domains."""
    if not os.path.exists(NGINX_SITES_AVAILABLE):
        warn("Nginx sites-available directory not found.")
        return []

    sites = []

    for file in sorted(os.listdir(NGINX_SITES_AVAILABLE)):
        if not file.endswith(".conf"):
            continue

        domain  = file[:-5]
        enabled = nginx_site_enabled(domain)

        if enabled and not show_enabled:
            continue
        if not enabled and not show_disabled:
            continue

        sites.append((domain, enabled))

    if not sites:
        warn("No Nginx sites found.")
        return []

    print()

    for i, (domain, enabled) in enumerate(sites, 1):
        status = (
            f"{C.GREEN}enabled{C.RESET}"
            if enabled
            else f"{C.DIM}disabled{C.RESET}"
        )
        type_lbl = site_type_label(domain)
        print(f"  {C.BOLD}{i}.{C.RESET} {domain} [{status}] {type_lbl}")

    print()

    raw = input("  Select (e.g. 1, 1-3, 1,2,3 or all): ").strip().lower()

    if not raw:
        return []

    if raw == "all":
        return [d for d, _ in sites]

    selected = []

    for part in raw.split(","):
        try:
            idx = int(part.strip()) - 1
            if 0 <= idx < len(sites):
                selected.append(sites[idx][0])
            else:
                warn(f"No item {part.strip()}.")
        except ValueError:
            warn(f"Invalid input: {part.strip()}")

    return selected

def create_nginx_http_site():
    menu_header("Create Nginx HTTP Site")

    if not ensure_nginx():
        return

    domain = ask_domain()
    if not domain:
        return

    docroot = ask_docroot("Document root", default=f"/var/www/{domain}")

    step("Creating document root...")
    create_docroot(docroot, domain)
    ask_for_custom_site_files(docroot)

    step("Writing Nginx vhost config...")
    path = write_nginx_vhost(domain, nginx_http_vhost(domain, docroot))

    step("Validating Nginx config...")
    ok_n, msg_n = validate_nginx()

    if not ok_n:
        os.remove(path)
        err(f"Nginx config error:\n{msg_n}")
        return

    nginx_enable_site(domain)
    save_site_meta(domain, "static", docroot, web_server="nginx")
    reload_nginx()
    apply_reloads()

    ok(f"Nginx HTTP site created: {domain}")
    info(f"Config: {path}")


def create_nginx_https_self_signed():
    menu_header("Create Nginx HTTPS Site (Self-Signed)")

    if not ensure_nginx():
        return

    domain = ask_domain()
    if not domain:
        return

    step("Generating self-signed certificate...")
    cert, key, ssl_err = make_self_signed(domain)

    if ssl_err:
        err(f"SSL error: {ssl_err}")
        return

    ok("Certificate generated.")

    docroot = ask_docroot("Document root", default=f"/var/www/{domain}")

    step("Creating document root...")
    create_docroot(docroot, domain)
    ask_for_custom_site_files(docroot)

    step("Writing Nginx vhost config...")
    path = write_nginx_vhost(domain, nginx_https_vhost(domain, docroot, cert, key))

    step("Validating Nginx config...")
    ok_n, msg_n = validate_nginx()

    if not ok_n:
        os.remove(path)
        err(f"Nginx config error:\n{msg_n}")
        return

    nginx_enable_site(domain)
    save_site_meta(domain, "static", docroot, web_server="nginx")
    reload_nginx()
    apply_reloads()

    ok(f"Nginx HTTPS site created: {domain}")
    info(f"Certificate: {cert}")
    info(f"Config: {path}")


def create_nginx_https_existing():
    menu_header("Create Nginx HTTPS Site (Existing Cert)")

    if not ensure_nginx():
        return

    domain = ask_domain()
    if not domain:
        return

    cert = input("  Certificate path: ").strip()
    key  = input("  Key path: ").strip()

    if not os.path.isfile(cert):
        err("Certificate not found.")
        return

    if not os.path.isfile(key):
        err("Key not found.")
        return

    docroot = ask_docroot("Document root", default=f"/var/www/{domain}")

    step("Creating document root...")
    create_docroot(docroot, domain)
    ask_for_custom_site_files(docroot)

    step("Writing Nginx vhost config...")
    path = write_nginx_vhost(domain, nginx_https_vhost(domain, docroot, cert, key))

    step("Validating Nginx config...")
    ok_n, msg_n = validate_nginx()

    if not ok_n:
        os.remove(path)
        err(f"Nginx config error:\n{msg_n}")
        return

    nginx_enable_site(domain)
    save_site_meta(domain, "static", docroot, web_server="nginx")
    reload_nginx()
    apply_reloads()

    ok(f"Nginx HTTPS site created: {domain}")
    info(f"Config: {path}")


def list_nginx_sites():
    menu_header("Nginx Sites")

    if not os.path.exists(NGINX_SITES_AVAILABLE):
        warn("Nginx sites-available not found.")
        return

    found = False

    for file in sorted(os.listdir(NGINX_SITES_AVAILABLE)):
        if not file.endswith(".conf"):
            continue

        domain  = file[:-5]
        enabled = nginx_site_enabled(domain)
        found   = True
        status  = (
            f"{C.GREEN}enabled{C.RESET}"
            if enabled
            else f"{C.DIM}disabled{C.RESET}"
        )
        meta     = get_site_meta(domain)
        type_lbl = site_type_label(domain)

        print(f"  {C.BOLD}{domain}{C.RESET} [{status}] {type_lbl}")

        if meta:
            docroot = meta.get("docroot", "")
            created = meta.get("created", "")
            dns     = "DNS" if meta.get("dns") else "no DNS"
            print(f"    {C.DIM}Docroot: {docroot}   Created: {created}   {dns}{C.RESET}")

        print()

    if not found:
        warn("No Nginx sites found.")

    print()


def enable_nginx_existing_site():
    domains = pick_nginx_sites(show_enabled=False, show_disabled=True)
    if not domains:
        return

    for domain in domains:
        nginx_enable_site(domain)

    ok_n, msg_n = validate_nginx()
    if not ok_n:
        err(msg_n)
        return

    reload_nginx()
    apply_reloads()

    for domain in domains:
        ok(f"{domain} enabled.")


def disable_nginx_existing_site():
    domains = pick_nginx_sites(show_enabled=True, show_disabled=False)
    if not domains:
        return

    for domain in domains:
        nginx_disable_site(domain)

    ok_n, msg_n = validate_nginx()
    if not ok_n:
        err(msg_n)
        return

    reload_nginx()
    apply_reloads()

    for domain in domains:
        ok(f"{domain} disabled.")


def delete_nginx_site():
    domains = pick_nginx_sites()
    if not domains:
        return

    print(f"\n{C.YELLOW}  WARNING — will delete:{C.RESET}")
    for d in domains:
        print(f"    - {d}")

    if not prompt_confirm(f"{C.YELLOW}Delete these Nginx sites?{C.RESET}"):
        warn("Cancelled.")
        return

    remove_files = "yes" if prompt_confirm("Delete document roots too?") else "no"

    for domain in domains:
        meta    = get_site_meta(domain)
        conf    = f"{NGINX_SITES_AVAILABLE}/{domain}.conf"
        docroot = meta.get("docroot", f"/var/www/{domain}") if meta else f"/var/www/{domain}"

        # ── LEMP: drop DB ──
        if meta and meta.get("type") == "lemp":
            db_name = meta.get("db_name")
            db_user = meta.get("db_user")

            if db_name and db_user:
                sql = (
                    f"DROP DATABASE IF EXISTS `{db_name}`;"
                    f"DROP USER IF EXISTS '{db_user}'@'localhost';"
                    f"FLUSH PRIVILEGES;"
                )
                result = run(["mysql", "-e", sql])

                if result.returncode == 0:
                    ok(f"Database '{db_name}' and user '{db_user}' dropped.")
                else:
                    err(f"DB deletion failed:\n{result.stderr}")

        nginx_disable_site(domain)

        if os.path.isfile(conf):
            os.remove(conf)

        if remove_files == "yes" and os.path.exists(docroot):
            shutil.rmtree(docroot)

        delete_site_meta(domain)
        ok(f"{domain} deleted.")

    ok_n, msg_n = validate_nginx()
    if not ok_n:
        err(msg_n)
        return

    reload_nginx()
    apply_reloads()


# ─────────────────────────────────────────
#  BIND9 helpers
# ─────────────────────────────────────────

def validate_bind9():
    result = run(["named-checkconf"])

    if result.returncode != 0:
        return False, result.stdout + result.stderr

    return True, "Syntax OK"


def validate_zone(domain, zonefile):
    result = run(["named-checkzone", domain, zonefile])

    if result.returncode != 0:
        return False, result.stdout + result.stderr

    return True, "Zone OK"


def reload_bind9():
    mark_reload("bind9")


def zone_exists(domain):
    if not os.path.isfile(NAMED_CONF_LOCAL):
        return False

    with open(NAMED_CONF_LOCAL, "r") as f:
        content = f.read()

    return f'zone "{domain}"' in content


def add_zone_to_conf(domain, zonefile):
    block = f"""
zone "{domain}" {{
    type master;
    file "{zonefile}";
}};
"""

    backup_config(NAMED_CONF_LOCAL)
    if DRY_RUN:
        step(f"[dry-run] Would append to { NAMED_CONF_LOCAL }")
    else:
        with open(NAMED_CONF_LOCAL, "a") as f:
            f.write(block)


def remove_zone_from_conf(domain):
    with open(NAMED_CONF_LOCAL, "r") as f:
        content = f.read()

    pattern = (
        rf'\nzone "{re.escape(domain)}"'
        rf' \{{[^}}]*\}};\n'
    )

    new_content = re.sub(pattern, "\n", content)

    backup_config(NAMED_CONF_LOCAL)
    if DRY_RUN:
        step(f"[dry-run] Would write to { NAMED_CONF_LOCAL }")
    else:
        with open(NAMED_CONF_LOCAL, "w") as f:
            f.write(new_content)


def reverse_zone_name(ip):
    parts = ip.split(".")
    return f"{parts[2]}.{parts[1]}.{parts[0]}.in-addr.arpa"


def reverse_zone_file(ip):
    parts = ip.split(".")
    return f"{BIND_DIR}/db.{parts[0]}.{parts[1]}.{parts[2]}"


def last_octet(ip):
    return ip.split(".")[-1]


def write_forward_zone(domain, ip, zonefile):
    serial = get_serial(zonefile)

    content = f"""; Forward zone for {domain}
$TTL    604800
@   IN  SOA     {domain}. admin.{domain}. (
                {serial}    ; Serial
                604800      ; Refresh
                86400       ; Retry
                2419200     ; Expire
                604800 )    ; Negative TTL

@   IN  NS      {domain}.
@   IN  A       {ip}
www IN  A       {ip}
"""

    backup_config(zonefile)
    if DRY_RUN:
        step(f"[dry-run] Would write to { zonefile }")
    else:
        with open(zonefile, "w") as f:
            f.write(content)


def write_reverse_zone(domain, ip, zonefile):
    serial = get_serial(zonefile)
    octet  = last_octet(ip)

    content = f"""; Reverse zone for {domain}
$TTL    604800
@   IN  SOA     {domain}. admin.{domain}. (
                {serial}    ; Serial
                604800      ; Refresh
                86400       ; Retry
                2419200     ; Expire
                604800 )    ; Negative TTL

@       IN  NS      {domain}.
{octet}     IN  PTR     {domain}.
"""

    backup_config(zonefile)
    if DRY_RUN:
        step(f"[dry-run] Would write to { zonefile }")
    else:
        with open(zonefile, "w") as f:
            f.write(content)


# ─────────────────────────────────────────
#  Pickers (numbered list selection)
# ─────────────────────────────────────────

def pick_sites(show_enabled=True, show_disabled=True, type_filter=None):
    """Show numbered list of Apache sites. Returns list of selected domains.
    type_filter: None = all, 'lamp' = LAMP only, 'static' = static only
    """
    sites = []

    for file in sorted(os.listdir(APACHE_SITES_AVAILABLE)):
        if not file.endswith(".conf"):
            continue

        enabled = os.path.exists(
            f"/etc/apache2/sites-enabled/{file}"
        )

        if enabled and not show_enabled:
            continue

        if not enabled and not show_disabled:
            continue

        domain = file[:-5]

        if type_filter:
            meta = get_site_meta(domain)
            site_type = meta.get("type") if meta else None
            if site_type != type_filter:
                continue

        sites.append((domain, enabled))

    if not sites:
        warn("No sites found.")
        return []

    print()

    for i, (domain, enabled) in enumerate(sites, 1):
        status = (
            f"{C.GREEN}enabled{C.RESET}"
            if enabled
            else f"{C.DIM}disabled{C.RESET}"
        )
        type_lbl = site_type_label(domain)
        print(f"  {C.BOLD}{i}.{C.RESET} {domain} [{status}] {type_lbl}")

    print()

    raw = input(
        "  Select (e.g. 1, 1-3, 1,2,3 or all): "
    ).strip().lower()

    if not raw:
        return []

    if raw == "all":
        return [d for d, _ in sites]

    selected = []

    for part in raw.split(","):
        try:
            idx = int(part.strip()) - 1

            if 0 <= idx < len(sites):
                selected.append(sites[idx][0])
            else:
                warn(f"No item {part.strip()}.")
        except ValueError:
            warn(f"Invalid input: {part.strip()}")

    return selected


def pick_zones():
    """Show numbered list of BIND9 zones. Returns list of selected zone names."""
    if not os.path.isfile(NAMED_CONF_LOCAL):
        err("named.conf.local not found.")
        return []

    with open(NAMED_CONF_LOCAL, "r") as f:
        content = f.read()

    zones = re.findall(r'zone "([^"]+)"', content)

    if not zones:
        warn("No zones found.")
        return []

    print()

    for i, zone in enumerate(zones, 1):
        tag = (
            f" {C.DIM}(reverse){C.RESET}"
            if zone.endswith(".in-addr.arpa")
            else ""
        )
        print(f"  {C.BOLD}{i}.{C.RESET} {zone}{tag}")

    print()

    raw = input(
        "  Select (e.g. 1, 1-3, 1,2,3 or all): "
    ).strip().lower()

    if not raw:
        return []

    if raw == "all":
        return list(zones)

    selected = []

    for part in raw.split(","):
        try:
            idx = int(part.strip()) - 1

            if 0 <= idx < len(zones):
                selected.append(zones[idx])
            else:
                warn(f"No item {part.strip()}.")
        except ValueError:
            warn(f"Invalid input: {part.strip()}")

    return selected


# ─────────────────────────────────────────
#  MX / zone-file helpers
# ─────────────────────────────────────────

def pick_zones_forward():
    """Numbered picker — forward zones only (no in-addr.arpa)."""
    if not os.path.isfile(NAMED_CONF_LOCAL):
        err("named.conf.local not found.")
        return []

    with open(NAMED_CONF_LOCAL, "r") as f:
        content = f.read()

    zones = [
        z for z in re.findall(r'zone "([^"]+)"', content)
        if not z.endswith(".in-addr.arpa")
    ]

    if not zones:
        warn("No forward zones found.")
        return []

    print()

    for i, zone in enumerate(zones, 1):
        print(f"  {C.BOLD}{i}.{C.RESET} {zone}")

    print()

    raw = input(
        "  Select (e.g. 1, 1-3, 1,2,3 or all): "
    ).strip().lower()

    if not raw:
        return []

    if raw == "all":
        return list(zones)

    selected = []

    for part in raw.split(","):
        try:
            idx = int(part.strip()) - 1
            if 0 <= idx < len(zones):
                selected.append(zones[idx])
            else:
                warn(f"No item {part.strip()}.")
        except ValueError:
            warn(f"Invalid input: {part.strip()}")

    return selected


def get_zone_file(domain):
    """Return the zone file path for a domain from named.conf.local."""
    with open(NAMED_CONF_LOCAL, "r") as f:
        content = f.read()

    match = re.search(
        rf'zone "{re.escape(domain)}"\s*\{{[^}}]*file\s*"([^"]+)"',
        content,
        re.DOTALL
    )

    return match.group(1) if match else None


def update_serial(content):
    """Increment the serial number in a zone file content string."""
    today = datetime.now().strftime("%Y%m%d")

    def replacer(m):
        old = m.group(1)
        nn  = int(old[8:]) + 1 if old[:8] == today else 1
        return m.group(0).replace(old, f"{today}{nn:02d}")

    return re.sub(r"(\d{10})\s*;\s*Serial", replacer, content)


def add_mx_record():
    menu_header("Add MX Record")

    zones = pick_zones_forward()

    if not zones:
        return

    for domain in zones:
        zonefile = get_zone_file(domain)

        if not zonefile or not os.path.isfile(zonefile):
            err(f"Zone file not found for {domain}.")
            continue

        priority  = input(f"  [{domain}] MX priority [10]: ").strip() or "10"
        mail_host = input(
            f"  [{domain}] Mail server hostname [mail.{domain}]: "
        ).strip() or f"mail.{domain}"
        mail_ip   = input(
            f"  [{domain}] Mail server IP (blank = no A record): "
        ).strip()

        with open(zonefile, "r") as f:
            content = f.read()

        content = update_serial(content)

        fqdn = mail_host if mail_host.endswith(".") else f"{mail_host}."
        content += f"\n@      IN  MX  {priority}  {fqdn}\n"

        if mail_ip:
            sub = mail_host.split(".")[0]
            content += f"{sub}    IN  A   {mail_ip}\n"

        backup_config(zonefile)
        if DRY_RUN:
            step(f"[dry-run] Would write to { zonefile }")
        else:
            with open(zonefile, "w") as f:
                f.write(content)

        ok_z, msg_z = validate_zone(domain, zonefile)

        if not ok_z:
            err(msg_z)
            continue

        ok(f"MX record added to {domain}.")

    ok_b, msg_b = validate_bind9()

    if not ok_b:
        err(msg_b)
        return

    reload_bind9()
    apply_reloads()


def remove_mx_record():
    menu_header("Remove MX Record")

    zones = pick_zones_forward()

    if not zones:
        return

    for domain in zones:
        zonefile = get_zone_file(domain)

        if not zonefile or not os.path.isfile(zonefile):
            err(f"Zone file not found for {domain}.")
            continue

        with open(zonefile, "r") as f:
            lines = f.readlines()

        mx_idx = [
            i for i, l in enumerate(lines)
            if "MX" in l.upper() and not l.strip().startswith(";")
        ]

        if not mx_idx:
            warn(f"No MX records in {domain}.")
            continue

        print(f"\n  MX records for {C.BOLD}{domain}{C.RESET}:")

        for n, i in enumerate(mx_idx, 1):
            print(f"  {C.BOLD}{n}.{C.RESET} {lines[i].strip()}")

        print()

        raw = input(
            "  Remove (e.g. 1, 1-3, 1,2,3 or all): "
        ).strip().lower()

        if not raw:
            continue

        if raw == "all":
            to_remove = set(mx_idx)
        else:
            to_remove = set()
            for part in raw.split(","):
                try:
                    idx = int(part.strip()) - 1
                    if 0 <= idx < len(mx_idx):
                        to_remove.add(mx_idx[idx])
                    else:
                        warn(f"No item {part.strip()}.")
                except ValueError:
                    warn(f"Invalid input: {part.strip()}")

        new_content = update_serial(
            "".join(l for i, l in enumerate(lines) if i not in to_remove)
        )

        backup_config(zonefile)
        if DRY_RUN:
            step(f"[dry-run] Would write to { zonefile }")
        else:
            with open(zonefile, "w") as f:
                f.write(new_content)

        ok_z, msg_z = validate_zone(domain, zonefile)

        if not ok_z:
            err(msg_z)
            continue

        ok(f"MX record(s) removed from {domain}.")

    ok_b, msg_b = validate_bind9()

    if not ok_b:
        err(msg_b)
        return

    reload_bind9()
    apply_reloads()


def list_mx_records():
    menu_header("List MX Records")

    zones = pick_zones_forward()

    if not zones:
        return

    for domain in zones:
        zonefile = get_zone_file(domain)

        print(f"\n  {C.BOLD}{domain}{C.RESET}")

        if not zonefile or not os.path.isfile(zonefile):
            err("  Zone file not found.")
            continue

        with open(zonefile, "r") as f:
            lines = f.readlines()

        mx_lines = [
            l.strip() for l in lines
            if "MX" in l.upper() and not l.strip().startswith(";")
        ]

        if mx_lines:
            for line in mx_lines:
                print(f"    {line}")
        else:
            print(f"    {C.DIM}No MX records{C.RESET}")

    print()


# ─────────────────────────────────────────
#  Custom site content helper
# ─────────────────────────────────────────

def copy_existing_site_files(source, destination, clear_dest=True):
    """Copy contents from source directory to destination.
    If clear_dest is True, deletes existing files in destination before copying.
    Returns True on success, False on failure.
    """
    if not os.path.isdir(source):
        err(f"Source directory does not exist: {source}")
        return False

    try:
        if os.path.realpath(source) == os.path.realpath(destination):
            err("Source and destination are the same. Cannot copy.")
            return False

        if clear_dest and os.path.isfile(destination):
            for item in os.listdir(destination):
                p = os.path.join(destination, item)
                if os.path.isdir(p):
                    shutil.rmtree(p)
                else:
                    os.remove(p)

        for item in os.listdir(source):
            s = os.path.join(source, item)
            d = os.path.join(destination, item)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)
        return True
    except Exception as e:
        err(f"Failed to copy site files: {e}")
        return False


def ask_for_custom_site_files(docroot):
    """Ask user if they want to copy existing site files.
    Returns True if custom files were copied or if files already exist in docroot, False otherwise.
    """
    if os.path.isdir(docroot) and any(os.scandir(docroot)):
        ok(f"Existing site files detected in {docroot}.")
        return True

    print()
    if not prompt_confirm(f"{C.BOLD}Use existing site files?{C.RESET}", key="use_site_files"):
        return False

    src = _ask("  Path to existing site files: ", key="site_files_path").strip()

    if not src:
        warn("No path provided, using default page.")
        return False

    if not os.path.isdir(src):
        err(f"Path does not exist or is not a directory: {src}")
        return False

    step(f"Copying files from {src} to {docroot}...")
    if copy_existing_site_files(src, docroot):
        ok("Site files copied.")
        return True
    else:
        warn("Failed to copy custom files. Default page will be used.")
        return False


def prompt_db_import(db_name, db_user, db_pass, docroot):
    print()
    if not prompt_confirm(f"{C.BOLD}Import an existing SQL database dump?{C.RESET}", key="import_sql"):
        return

    while True:
        sql_file = _ask(f"  SQL file path (or just filename if in {docroot}): ", key="sql_path").strip()
        if not sql_file:
            warn("Database import cancelled.")
            return

        if not os.path.isabs(sql_file) and not sql_file.startswith("./"):
            sql_file = os.path.join(docroot, sql_file)

        if not os.path.isfile(sql_file):
            err(f"File does not exist: {sql_file}")
            continue

        step(f"Importing {sql_file} into {db_name}...")
        print(f"  {C.DIM}(this may take a moment){C.RESET}", end=" ", flush=True)

        result = run(["mysql", "-u", db_user, f"-p{db_pass}", db_name, "-e", f"SOURCE {sql_file};"])

        if result.returncode != 0:
            print(f"{C.RED}✗{C.RESET}")
            err(f"Import error:\n{result.stderr}")
            return

        print(f"{C.GREEN}✓{C.RESET}")
        ok("Database imported successfully.")
        return


# ─────────────────────────────────────────
#  Full site creation (Apache + optional DNS)
# ─────────────────────────────────────────

def create_full_site():
    menu_header("Create Full Site (Apache + optional DNS)")

    if not ensure_nginx():
        return

    domain = ask_domain()
    if not domain:
        return

    # MODIFICATION: ask for DNS first
    dns_choice = _ask("  Create DNS zones for this site? (y/N): ", key="dns_choice").strip().lower()
    create_dns = (dns_choice == "y")

    ip = None
    if create_dns:
        ip = prompt_for_ip("Server IP")
        if not ip:
            err("IP address required for DNS.")
            return

    print("\nSSL options:")
    print("  1. HTTP only")
    print("  2. HTTPS (self-signed cert)")
    print("  3. HTTPS (existing cert)")

    ssl_choice = _ask("  \nChoice: ", key="ssl").strip()

    docroot = ask_docroot("Document root", default=f"/var/www/{domain}")

    print()

    # ── SSL setup ──
    cert = key = None

    if ssl_choice == "2":
        ensure_modules()
        step("Generating self-signed certificate...")
        cert, key, ssl_err = make_self_signed(domain)

        if ssl_err:
            err(f"SSL error: {ssl_err}")
            return

        ok("Certificate generated.")

    elif ssl_choice == "3":
        ensure_modules()
        cert = _ask("  Certificate path: ", key="cert_path").strip()
        key  = _ask("  Key path: ", key="key_path").strip()

        if not os.path.isfile(cert):
            err("Certificate not found.")
            return

        if not os.path.isfile(key):
            err("Key not found.")
            return

    # ── Document root ──
    step("Creating document root...")
    Path(docroot).mkdir(parents=True, exist_ok=True)

    # Custom site files?
    custom = ask_for_custom_site_files(docroot)
    if not custom:
        # No custom files — write the default placeholder index.html
        index = Path(docroot) / "index.html"
        if not index.exists():
            index.write_text(f"<html><body><h1>{domain}</h1></body></html>")

    # ── Apache vhost ──
    step("Writing Apache vhost config...")

    if ssl_choice in ("2", "3"):
        config = https_vhost(domain, docroot, cert, key)
    else:
        config = http_vhost(domain, docroot)

    vhost_path = write_vhost(domain, config)

    step("Validating Apache config...")
    ok_a, msg_a = validate_apache()

    if not ok_a:
        os.remove(vhost_path)
        err(f"Apache config error:\n{msg_a}")
        return

    enable_site(domain)
    save_site_meta(domain, "static", docroot, dns=create_dns)
    configure_apache_ports()
    ok("Apache vhost created.")
    reload_apache()

    # ── Nginx proxy ──
    step("Configuring Nginx front-door proxy...")
    nginx_vhost = f"""server {{
    listen 80;
    server_name {domain} www.{domain};
    location / {{
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}"""
    write_nginx_vhost(domain, nginx_vhost)
    nginx_enable_site(domain)
    reload_nginx()
    ok("Nginx proxy configured.")

    # ── BIND9 zones (only if requested) ──
    if create_dns:
        rev_zone = reverse_zone_name(ip)
        fwd_file = f"{BIND_DIR}/db.{domain}"
        rev_file = reverse_zone_file(ip)

        if zone_exists(domain):
            warn(f"Forward zone {domain} already exists, skipping.")
        else:
            step("Writing forward zone...")
            write_forward_zone(domain, ip, fwd_file)

            ok_z, msg_z = validate_zone(domain, fwd_file)

            if not ok_z:
                os.remove(fwd_file)
                err(f"Zone error:\n{msg_z}")
                return

            add_zone_to_conf(domain, fwd_file)
            ok("Forward zone created.")

        if zone_exists(rev_zone):
            warn(f"Reverse zone {rev_zone} already exists, skipping.")
        else:
            step("Writing reverse zone...")
            write_reverse_zone(domain, ip, rev_file)

            ok_r, msg_r = validate_zone(rev_zone, rev_file)

            if not ok_r:
                os.remove(rev_file)
                remove_zone_from_conf(domain)
                err(f"Reverse zone error:\n{msg_r}")
                return

            add_zone_to_conf(rev_zone, rev_file)
            ok("Reverse zone created.")

        ok_b, msg_b = validate_bind9()

        if not ok_b:
            err(f"BIND9 error:\n{msg_b}")
            return

        reload_bind9()

    # ── Auto-reload ──
    apply_reloads()

    # ── Summary ──
    proto = "https" if ssl_choice in ("2", "3") else "http"
    print(f"\n{C.BOLD}{'─' * 35}{C.RESET}")
    print(f"  {C.BOLD}Site:{C.RESET}         {C.GREEN}{domain}{C.RESET}")
    print(f"  Docroot:      {docroot}")
    print(f"  Apache conf:  {vhost_path}")
    if create_dns:
        print(f"  Forward zone: {fwd_file}")
        print(f"  Reverse zone: {rev_file}")
    else:
        print(f"  DNS:          {C.DIM}not created{C.RESET}")

    if cert:
        print(f"  Certificate:  {cert}")

    print(f"  {C.BOLD}URL:{C.RESET}          {C.CYAN}{proto}://{domain}{C.RESET}")
    print(f"{C.BOLD}{'─' * 35}{C.RESET}")


# ─────────────────────────────────────────
#  Apache menu functions
# ─────────────────────────────────────────

def create_http_site():
    domain = ask_domain()
    docroot = ask_docroot("Document root", default=f"/var/www/{domain}")

    step("Creating document root...")
    create_docroot(docroot, domain)

    # Custom site files?
    ask_for_custom_site_files(docroot)

    step("Writing vhost config...")
    path = write_vhost(domain, http_vhost(domain, docroot))

    ok_a, msg_a = validate_apache()

    if not ok_a:
        os.remove(path)
        err(msg_a)
        return

    enable_site(domain)
    reload_apache()
    apply_reloads()

    ok(f"Site created: {domain}")
    info(f"Config: {path}")


def create_https_existing():
    ensure_modules()

    domain = ask_domain()
    cert   = input("  Certificate path: ").strip()
    key    = input("  Key path: ").strip()

    if not os.path.isfile(cert):
        err("Certificate not found.")
        return

    if not os.path.isfile(key):
        err("Key not found.")
        return

    docroot = ask_docroot("Document root", default=f"/var/www/{domain}")

    create_docroot(docroot, domain)
    ask_for_custom_site_files(docroot)

    path = write_vhost(domain, https_vhost(domain, docroot, cert, key))

    ok_a, msg_a = validate_apache()

    if not ok_a:
        os.remove(path)
        err(msg_a)
        return

    enable_site(domain)
    reload_apache()
    apply_reloads()

    ok(f"HTTPS site created: {domain}")
    info(f"Config: {path}")


def create_self_signed_site():
    ensure_modules()

    domain = ask_domain()

    step("Generating self-signed certificate...")
    cert, key, ssl_err = make_self_signed(domain)

    if ssl_err:
        err(ssl_err)
        return

    ok("Certificate generated.")

    docroot = f"/var/www/{domain}"
    create_docroot(docroot, domain)
    ask_for_custom_site_files(docroot)

    path = write_vhost(domain, https_vhost(domain, docroot, cert, key))

    ok_a, msg_a = validate_apache()

    if not ok_a:
        os.remove(path)
        err(msg_a)
        return

    enable_site(domain)
    reload_apache()
    apply_reloads()

    ok(f"HTTPS site created: {domain}")
    info(f"Certificate: {cert}")
    info(f"Config: {path}")


def list_apache_sites():
    print()
    print(f"  Filter:  {C.BOLD}1{C.RESET}) All  "
          f"{C.BOLD}2{C.RESET}) LAMP only  "
          f"{C.BOLD}3{C.RESET}) Static only")
    filt = input("  Choice [1]: ").strip() or "1"
    print()

    found = False

    for file in sorted(os.listdir(APACHE_SITES_AVAILABLE)):
        if not file.endswith(".conf"):
            continue

        domain = file[:-5]
        meta   = get_site_meta(domain)
        stype  = meta.get("type") if meta else None

        if filt == "2" and stype != "lamp":
            continue
        if filt == "3" and stype != "static":
            continue

        found   = True
        enabled = os.path.isfile(f"/etc/apache2/sites-enabled/{file}")
        status  = (
            f"{C.GREEN}enabled{C.RESET}"
            if enabled
            else f"{C.DIM}disabled{C.RESET}"
        )
        type_lbl = site_type_label(domain)

        print(f"  {C.BOLD}{domain}{C.RESET} [{status}] {type_lbl}")

        if meta:
            docroot = meta.get("docroot", "")
            created = meta.get("created", "")
            dns     = "DNS" if meta.get("dns") else "no DNS"
            print(f"    {C.DIM}Docroot: {docroot}   Created: {created}   {dns}{C.RESET}")

            if meta.get("db_name"):
                print(f"    {C.DIM}DB: {meta['db_name']}  "
                      f"User: {meta['db_user']}{C.RESET}")

        print()

    if not found:
        warn("No sites found.")

    print()


def enable_existing_site():
    domains = pick_sites(show_enabled=False, show_disabled=True)

    if not domains:
        return

    for domain in domains:
        enable_site(domain)

    ok_a, msg_a = validate_apache()

    if not ok_a:
        err(msg_a)
        return

    reload_apache()
    apply_reloads()

    for domain in domains:
        ok(f"{domain} enabled.")


def disable_site():
    domains = pick_sites(show_enabled=True, show_disabled=False)

    if not domains:
        return

    for domain in domains:
        disable_site_cmd(domain)

    ok_a, msg_a = validate_apache()

    if not ok_a:
        err(msg_a)
        return

    reload_apache()
    apply_reloads()

    for domain in domains:
        ok(f"{domain} disabled.")


def delete_apache_site():
    domains = pick_sites()

    if not domains:
        return

    print(f"\n{C.YELLOW}  WARNING — will delete:{C.RESET}")

    for d in domains:
        type_lbl = site_type_label(d)
        print(f"    - {d} {type_lbl}")

    confirm = input(
        f"\n  {C.YELLOW}Delete these sites?{C.RESET} (yes/no): "
    ).strip().lower()

    if confirm != "yes":
        warn("Cancelled.")
        return

    remove_files = "yes" if prompt_confirm("Delete document roots too?") else "no"

    for domain in domains:
        meta    = get_site_meta(domain)
        conf    = f"{APACHE_SITES_AVAILABLE}/{domain}.conf"
        docroot = meta.get("docroot", f"/var/www/{domain}") if meta else f"/var/www/{domain}"

        # ── LAMP: drop DB ──
        if meta and meta.get("type") == "lamp":
            db_name = meta.get("db_name")
            db_user = meta.get("db_user")

            if db_name and db_user:
                sql = (
                    f"DROP DATABASE IF EXISTS `{db_name}`;"
                    f"DROP USER IF EXISTS '{db_user}'@'localhost';"
                    f"FLUSH PRIVILEGES;"
                )
                result = run(["mysql", "-e", sql])

                if result.returncode == 0:
                    ok(f"Database '{db_name}' and user '{db_user}' dropped.")
                else:
                    err(f"DB deletion failed:\n{result.stderr}")

        # ── Remove vhost ──
        disable_site_cmd(domain)

        enabled_link = f"/etc/apache2/sites-enabled/{domain}.conf"

        if os.path.islink(enabled_link):
            os.unlink(enabled_link)

        if os.path.isfile(conf):
            os.remove(conf)

        if remove_files == "yes" and os.path.exists(docroot):
            shutil.rmtree(docroot)

        # ── Remove Nginx proxy if it exists ──
        nginx_conf = f"{NGINX_SITES_AVAILABLE}/{domain}.conf"
        nginx_enabled = f"{NGINX_SITES_ENABLED}/{domain}.conf"
        if os.path.islink(nginx_enabled):
            os.unlink(nginx_enabled)
        if os.path.isfile(nginx_conf):
            os.remove(nginx_conf)

        # ── Remove metadata ──
        delete_site_meta(domain)

        ok(f"{domain} deleted.")

    ok_a, msg_a = validate_apache()

    if not ok_a:
        err(msg_a)
        return

    reload_apache()
    if os.path.exists("/usr/sbin/nginx"):
        reload_nginx()
    apply_reloads()


def disable_default_site():
    step("Disabling default Apache sites...")

    for site in ("000-default.conf", "default-ssl.conf"):
        run(["/usr/sbin/a2dissite", site])

    ok_a, msg_a = validate_apache()

    if not ok_a:
        err(msg_a)
        return

    reload_apache()
    apply_reloads()

    ok("Default sites disabled.")


def manual_reload_apache():
    step("Running config test...")

    ok_a, msg_a = validate_apache()

    if not ok_a:
        err(msg_a)
        return

    reload_apache()
    apply_reloads()


# ─────────────────────────────────────────
#  BIND9 menu functions
# ─────────────────────────────────────────

def create_forward_zone():
    domain = ask_domain()

    if not domain:
        return

    if zone_exists(domain):
        warn(f"Zone {domain} already exists.")
        return

    ip = prompt_for_ip("Server IP")

    if not ip:
        return

    zonefile = f"{BIND_DIR}/db.{domain}"

    step("Writing forward zone...")
    write_forward_zone(domain, ip, zonefile)

    ok_z, msg_z = validate_zone(domain, zonefile)

    if not ok_z:
        os.remove(zonefile)
        err(msg_z)
        return

    add_zone_to_conf(domain, zonefile)

    ok_b, msg_b = validate_bind9()

    if not ok_b:
        os.remove(zonefile)
        remove_zone_from_conf(domain)
        err(msg_b)
        return

    reload_bind9()
    apply_reloads()

    ok(f"Forward zone created: {domain}")
    info(f"Zone file: {zonefile}")


def create_reverse_zone():
    domain = input("  Domain (for SOA record): ").strip()

    if not domain:
        return

    ip = prompt_for_ip("Server IP")

    if not ip:
        return

    rev_zone = reverse_zone_name(ip)

    if zone_exists(rev_zone):
        warn(f"Reverse zone {rev_zone} already exists.")
        return

    zonefile = reverse_zone_file(ip)

    step("Writing reverse zone...")
    write_reverse_zone(domain, ip, zonefile)

    ok_z, msg_z = validate_zone(rev_zone, zonefile)

    if not ok_z:
        os.remove(zonefile)
        err(msg_z)
        return

    add_zone_to_conf(rev_zone, zonefile)

    ok_b, msg_b = validate_bind9()

    if not ok_b:
        os.remove(zonefile)
        remove_zone_from_conf(rev_zone)
        err(msg_b)
        return

    reload_bind9()
    apply_reloads()

    ok(f"Reverse zone created: {rev_zone}")
    info(f"Zone file: {zonefile}")


def create_both_zones():
    domain = ask_domain()

    if not domain:
        return

    ip = prompt_for_ip("Server IP")

    if not ip:
        return

    rev_zone = reverse_zone_name(ip)

    if zone_exists(domain):
        warn(f"Forward zone {domain} already exists.")
        return

    if zone_exists(rev_zone):
        warn(f"Reverse zone {rev_zone} already exists.")
        return

    fwd_file = f"{BIND_DIR}/db.{domain}"
    rev_file = reverse_zone_file(ip)

    step("Writing forward zone...")
    write_forward_zone(domain, ip, fwd_file)

    ok_z, msg_z = validate_zone(domain, fwd_file)

    if not ok_z:
        os.remove(fwd_file)
        err(msg_z)
        return

    ok("Forward zone OK.")

    step("Writing reverse zone...")
    write_reverse_zone(domain, ip, rev_file)

    ok_r, msg_r = validate_zone(rev_zone, rev_file)

    if not ok_r:
        os.remove(fwd_file)
        os.remove(rev_file)
        err(msg_r)
        return

    ok("Reverse zone OK.")

    add_zone_to_conf(domain, fwd_file)
    add_zone_to_conf(rev_zone, rev_file)

    ok_b, msg_b = validate_bind9()

    if not ok_b:
        os.remove(fwd_file)
        os.remove(rev_file)
        remove_zone_from_conf(domain)
        remove_zone_from_conf(rev_zone)
        err(msg_b)
        return

    reload_bind9()
    apply_reloads()

    ok(f"Forward zone: {domain}")
    ok(f"Reverse zone: {rev_zone}")
    info(f"Forward file: {fwd_file}")
    info(f"Reverse file: {rev_file}")


def delete_zone():
    zones = pick_zones()

    if not zones:
        return

    print(f"\n{C.YELLOW}  WARNING — will delete:{C.RESET}")

    for z in zones:
        print(f"    - {z}")

    confirm = input(
        f"\n  {C.YELLOW}Delete these zones?{C.RESET} (yes/no): "
    ).strip().lower()

    if confirm != "yes":
        warn("Cancelled.")
        return

    delete_files = input(
        "  Delete zone files too? (yes/no): "
    ).strip().lower()

    for domain in zones:
        remove_zone_from_conf(domain)

        zonefile = f"{BIND_DIR}/db.{domain}"

        if delete_files == "yes" and os.path.isfile(zonefile):
            os.remove(zonefile)

        ok(f"{domain} removed.")

    ok_b, msg_b = validate_bind9()

    if not ok_b:
        err(msg_b)
        return

    reload_bind9()
    apply_reloads()


def list_zones():
    print()

    if not os.path.isfile(NAMED_CONF_LOCAL):
        err("named.conf.local not found.")
        return

    with open(NAMED_CONF_LOCAL, "r") as f:
        content = f.read()

    zones = re.findall(r'zone "([^"]+)"', content)

    if not zones:
        warn("No zones found.")
    else:
        for zone in zones:
            print(f"  {zone}")

    print()


def test_dns():
    domain = input("  Domain to test: ").strip()

    if not domain:
        return

    server = input("  DNS server [127.0.0.1]: ").strip()

    if not server:
        server = "127.0.0.1"

    step(f"Forward lookup: {domain}")
    result = run(["dig", domain, f"@{server}"])
    print(result.stdout)

    ip = input(
        "IP for reverse lookup (leave blank to skip): "
    ).strip()

    if ip:
        step(f"Reverse lookup: {ip}")
        result = run(["dig", "-x", ip, f"@{server}"])
        print(result.stdout)


def manual_reload_bind9():
    step("Running config test...")

    ok_b, msg_b = validate_bind9()

    if not ok_b:
        err(msg_b)
        return

    reload_bind9()
    apply_reloads()


# ─────────────────────────────────────────
#  LAMP site creation (Apache + PHP + MariaDB + optional DNS)
# ─────────────────────────────────────────

def create_lamp_site():
    menu_header("Create LAMP Site (Apache + PHP + MariaDB + optional DNS)")

    if not ensure_mariadb():
        return

    if not ensure_nginx():
        return

    domain = ask_domain()
    if not domain:
        return

    # MODIFICATION: ask for DNS first
    dns_choice = _ask("  Create DNS zones for this site? (y/N): ", key="dns_choice").strip().lower()
    create_dns = (dns_choice == "y")

    ip = None
    if create_dns:
        ip = prompt_for_ip("Server IP")
        if not ip:
            err("IP address required for DNS.")
            return

    # ── Database settings ──
    default_db = re.sub(r"[^a-zA-Z0-9_]", "_", domain.split(".")[0])

    db_name = ask_db_name("Database name", default_db)

    db_user = ask_db_user("Database user", f"{default_db}_user")

    db_pass = _ask("  Database password: ", key="db_pass").strip()

    if not db_pass:
        err("Password cannot be empty.")
        return

    # ── SSL ──
    print("\nSSL options:")
    print("  1. HTTP only")
    print("  2. HTTPS (self-signed cert)")

    ssl_choice = _ask("  \nChoice [1]: ", key="ssl").strip() or "1"

    docroot = ask_docroot("Document root", default=f"/var/www/{domain}")

    print()

    # ── Install packages (live output) ──
    step("Installing MariaDB and PHP packages...")
    print(f"  {C.DIM}(this may take a minute){C.RESET}\n")

    rc, stderr = run_live([
        "apt-get", "install", "-y",
        "mariadb-server",
        "php",
        "libapache2-mod-php",
        "php-mysql"
    ])

    if rc != 0:
        err(f"Package install failed:\n{stderr}")
        return

    ok("All packages installed.")
    configure_apache_ports()

    # ── Start MariaDB ──
    step("Starting MariaDB...")
    run(["systemctl", "start",  "mariadb"])
    run(["systemctl", "enable", "mariadb"])
    ok(f"MariaDB: {service_status('mariadb')}")

    # ── Create database and user ──
    step("Setting up database...")

    sql_steps = [
        (f"CREATE DATABASE IF NOT EXISTS `{db_name}`",
         f"Creating database '{db_name}'"),
        (f"CREATE USER IF NOT EXISTS '{db_user}'@'localhost' IDENTIFIED BY '{db_pass}'",
         f"Creating user '{db_user}'"),
        (f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{db_user}'@'localhost'",
         f"Granting privileges on '{db_name}'.*"),
        ("FLUSH PRIVILEGES",
         "Flushing privileges"),
    ]

    for sql, label in sql_steps:
        print(f"    {C.DIM}{label}...{C.RESET}", end=" ", flush=True)
        result = run(["mysql", "-e", f"{sql};"])

        if result.returncode != 0:
            print(f"{C.RED}✗{C.RESET}")
            err(f"MariaDB error:\n{result.stderr}")
            return

        print(f"{C.GREEN}✓{C.RESET}")

    ok("Database and user ready.")

    # ── SSL cert ──
    cert = key = None
    ensure_modules()

    if ssl_choice == "2":
        step("Generating self-signed certificate...")
        cert, key, ssl_err = make_self_signed(domain)

        if ssl_err:
            err(f"SSL error: {ssl_err}")
            return

        ok("Certificate generated.")

    # ── Document root + index.php ──
    step("Creating document root and test page...")
    Path(docroot).mkdir(parents=True, exist_ok=True)

    # Custom site files?
    custom = ask_for_custom_site_files(docroot)

    if not custom:
        # Generate default index.php only if no custom files were copied
        index = Path(docroot) / "index.php"

        index.write_text(f"""<?php
$host = 'localhost';
$db   = '{db_name}';
$user = '{db_user}';
$pass = '{db_pass}';

$error   = null;
$pdo     = null;
$entries = [];
$inserted = false;

try {{
    $pdo = new PDO("mysql:host=$host;dbname=$db", $user, $pass);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

    $pdo->exec("CREATE TABLE IF NOT EXISTS test_entries (
        id         INT AUTO_INCREMENT PRIMARY KEY,
        name       VARCHAR(100) NOT NULL,
        message    VARCHAR(255) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )");

    if ($_SERVER['REQUEST_METHOD'] === 'POST'
        && !empty($_POST['name'])
        && !empty($_POST['message'])) {{
        $stmt = $pdo->prepare(
            "INSERT INTO test_entries (name, message) VALUES (?, ?)"
        );
        $stmt->execute([
            htmlspecialchars($_POST['name']),
            htmlspecialchars($_POST['message'])
        ]);
        $inserted = true;
    }}

    $entries = $pdo->query(
        "SELECT * FROM test_entries ORDER BY created_at DESC LIMIT 20"
    )->fetchAll(PDO::FETCH_ASSOC);

}} catch (PDOException $e) {{
    $error = $e->getMessage();
}}
?><!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{domain}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: sans-serif; padding: 2rem; max-width: 750px; margin: auto; color: #212529; }}
    h1 {{ margin-bottom: .25rem; }}
    h3 {{ margin-top: 2rem; border-bottom: 1px solid #dee2e6; padding-bottom: .4rem; }}
    .badge {{ display:inline-block; padding:2px 10px; border-radius:4px; font-size:.8rem; font-weight:600; }}
    .green {{ background:#d1e7dd; color:#0a3622; }}
    .red   {{ background:#f8d7da; color:#58151c; }}
    .form-row {{ display:flex; gap:.5rem; align-items:center; flex-wrap:wrap; margin-top:.75rem; }}
    input[type=text] {{ padding:6px 10px; border:1px solid #ced4da; border-radius:4px; width:200px; }}
    button {{ padding:6px 16px; background:#0d6efd; color:#fff; border:none; border-radius:4px; cursor:pointer; }}
    button:hover {{ background:#0b5ed7; }}
    table {{ width:100%; border-collapse:collapse; margin-top:.75rem; font-size:.9rem; }}
    th,td {{ padding:8px 12px; border:1px solid #dee2e6; text-align:left; }}
    th {{ background:#f8f9fa; font-weight:600; }}
    tr:hover td {{ background:#f8f9fa; }}
    pre {{ background:#1a1a2e; color:#a8d8ea; padding:1rem; border-radius:6px;
           font-size:.82rem; overflow-x:auto; line-height:1.6; }}
    .notice {{ background:#fff3cd; border:1px solid #ffc107; border-radius:4px;
               padding:.5rem 1rem; margin-top:.5rem; font-size:.85rem; }}
    code {{ background:#e9ecef; padding:1px 5px; border-radius:3px; font-size:.9em; }}
    .empty {{ color:#6c757d; font-style:italic; }}
  </style>
</head>
<body>
  <h1>{domain}</h1>

<?php if ($error): ?>
  <p><span class="badge red">✗ DB Error</span> <code><?= htmlspecialchars($error) ?></code></p>
<?php else: ?>
  <p>
    <span class="badge green">✓ Connected</span>
    &nbsp; database <code>{db_name}</code> &nbsp;·&nbsp; user <code>{db_user}</code>
  </p>

  <?php if ($inserted): ?>
  <div class="notice">✓ Entry inserted successfully.</div>
  <?php endif; ?>

  <h3>Insert test data</h3>
  <form method="POST">
    <div class="form-row">
      <input type="text" name="name"    placeholder="Your name"    required maxlength="100">
      <input type="text" name="message" placeholder="Test message"  required maxlength="255">
      <button type="submit">Insert row</button>
    </div>
  </form>

  <h3>Rows in <code>test_entries</code></h3>
  <?php if ($entries): ?>
  <tr>
    <tr><th>ID</th><th>Name</th><th>Message</th><th>Created At</th></tr>
    <?php foreach ($entries as $row): ?>
    <tr>
      <td><?= $row['id'] ?></td>
      <td><?= $row['name'] ?></td>
      <td><?= $row['message'] ?></td>
      <td><?= $row['created_at'] ?></td>
    </tr>
    <?php endforeach; ?>
  </table>
  <?php else: ?>
  <p class="empty">No rows yet — submit the form above.</p>
  <?php endif; ?>

  <h3>Verify from your Linux server</h3>
  <pre># Connect to MariaDB
mysql

# Connect as the site DB user
mysql -u {db_user} -p'{db_pass}' {db_name}

# Show all test entries
mysql {db_name} -e "SELECT * FROM test_entries;"

# Count rows
mysql {db_name} -e "SELECT COUNT(*) AS total FROM test_entries;"

# Show tables in the database
mysql {db_name} -e "SHOW TABLES;"

# Show the table structure
mysql {db_name} -e "DESCRIBE test_entries;"

# Show all databases
mysql -e "SHOW DATABASES;"

# Delete all test entries (reset)
mysql {db_name} -e "DELETE FROM test_entries;"</pre>

<?php endif; ?>
</body>
</html>
""")
        ok("Test page created at index.php.")

    prompt_db_import(db_name, db_user, db_pass, docroot)

    # ── Apache vhost ──
    step("Writing Apache vhost config...")

    if ssl_choice == "2":
        config = https_vhost(domain, docroot, cert, key)
    else:
        config = http_vhost(domain, docroot)

    vhost_path = write_vhost(domain, config)

    ok_a, msg_a = validate_apache()

    if not ok_a:
        os.remove(vhost_path)
        err(f"Apache error:\n{msg_a}")
        return

    enable_site(domain)
    save_site_meta(domain, "lamp", docroot, db_name=db_name, db_user=db_user, dns=create_dns)
    reload_apache()
    ok("Apache vhost created.")

    # ── Nginx proxy ──
    step("Configuring Nginx front-door proxy...")
    vhost = f"""server {{
    listen 80;
    server_name {domain} www.{domain};
    location / {{
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}"""
    write_nginx_vhost(domain, vhost)
    nginx_enable_site(domain)
    reload_nginx()
    ok("Nginx proxy configured.")

    # ── BIND9 zones (only if requested) ──
    if create_dns:
        rev_zone = reverse_zone_name(ip)
        fwd_file = f"{BIND_DIR}/db.{domain}"
        rev_file = reverse_zone_file(ip)

        if zone_exists(domain):
            warn(f"Forward zone {domain} already exists, skipping.")
        else:
            step("Writing forward zone...")
            write_forward_zone(domain, ip, fwd_file)

            ok_z, msg_z = validate_zone(domain, fwd_file)

            if not ok_z:
                os.remove(fwd_file)
                err(msg_z)
                return

            add_zone_to_conf(domain, fwd_file)
            ok("Forward zone created.")

        if zone_exists(rev_zone):
            warn(f"Reverse zone {rev_zone} already exists, skipping.")
        else:
            step("Writing reverse zone...")
            write_reverse_zone(domain, ip, rev_file)

            ok_r, msg_r = validate_zone(rev_zone, rev_file)

            if not ok_r:
                os.remove(rev_file)
                remove_zone_from_conf(domain)
                err(msg_r)
                return

            add_zone_to_conf(rev_zone, rev_file)
            ok("Reverse zone created.")

        ok_b, msg_b = validate_bind9()

        if not ok_b:
            err(msg_b)
            return

        reload_bind9()

    apply_reloads()

    # ── Summary ──
    proto     = "https" if ssl_choice == "2" else "http"
    db_status = service_status("mariadb")

    print(f"\n{C.BOLD}{'─' * 35}{C.RESET}")
    print(f"  {C.BOLD}Site:{C.RESET}      {C.GREEN}{domain}{C.RESET}")
    print(f"  Docroot:   {docroot}")
    print(f"  {C.BOLD}Database:{C.RESET}  {db_name}")
    print(f"  DB User:   {db_user}")
    print(f"  DB Pass:   {db_pass}")
    print(f"  MariaDB:   {db_status}")

    if cert:
        print(f"  Cert:      {cert}")

    if create_dns:
        print("  DNS:       created")
    else:
        print("  DNS:       not created")

    print(f"  {C.BOLD}URL:{C.RESET}       {C.CYAN}{proto}://{domain}{C.RESET}")
    print(f"{C.BOLD}{'─' * 35}{C.RESET}")


# ─────────────────────────────────────────
#  Full Site sub-menu helpers
# ─────────────────────────────────────────

def modify_full_site():
    """Edit an existing full-site Apache vhost interactively, including domain rename."""
    menu_header("Modify Full Site")

    domains = pick_sites()

    if not domains:
        return

    for domain in domains:
        conf = f"{APACHE_SITES_AVAILABLE}/{domain}.conf"

        if not os.path.isfile(conf):
            err(f"Config not found: {conf}")
            continue

        with open(conf, "r") as f:
            current = f.read()

        print(f"\n  Current config for {C.BOLD}{domain}{C.RESET}:")
        print(f"{C.DIM}{current}{C.RESET}")

        # Extract old docroot for later use
        old_docroot_match = re.search(r'DocumentRoot\s+"([^"]+)"', current)
        old_docroot = old_docroot_match.group(1) if old_docroot_match else f"/var/www/{domain}"

        print("\n  What would you like to change?")
        print("  1. Change DocumentRoot")
        print("  2. Replace entire vhost config (paste new)")
        print("  3. Change domain name (and optionally copy site)")
        print("  4. Replace website files (copy from path)")
        print("  0. Skip")

        sub = input("  \n  Choice: ").strip()

        if sub == "1":
            new_docroot = ask_docroot("New DocumentRoot")

            if not new_docroot:
                warn("Skipped.")
                continue

            updated = re.sub(
                r'DocumentRoot\s+"[^"]+"',
                f'DocumentRoot "{new_docroot}"',
                current
            )
            updated = re.sub(
                r'<Directory\s+"[^"]+"',
                f'<Directory "{new_docroot}"',
                updated
            )

            backup_config(conf)
            if DRY_RUN:
                step(f"[dry-run] Would write to { conf }")
            else:
                with open(conf, "w") as f:
                    f.write(updated)

            ok_a, msg_a = validate_apache()

            if not ok_a:
                if DRY_RUN:
                    step(f"[dry-run] Would write to { conf }")
                else:
                    with open(conf, "w") as f:
                        f.write(current)
                err(f"Apache error (reverted):\n{msg_a}")
                continue

            # Update metadata docroot
            meta = get_site_meta(domain)
            if meta:
                meta["docroot"] = new_docroot
                if DRY_RUN:
                    step(f"[dry-run] Would write to {META_DIR}/{domain}.json")
                else:
                    with open(f"{META_DIR}/{domain}.json", "w") as f:
                        json.dump(meta, f, indent=2)

            reload_apache()
            apply_reloads()
            ok(f"DocumentRoot updated for {domain}.")

        elif sub == "2":
            print(
                f"\n  {C.YELLOW}Paste new vhost config below."
                f" End with a line containing only END{C.RESET}"
            )
            lines = []

            while True:
                line = input()

                if line.strip() == "END":
                    break

                lines.append(line)

            new_config = "\n".join(lines) + "\n"

            backup_config(conf)
            if DRY_RUN:
                step(f"[dry-run] Would write to { conf }")
            else:
                with open(conf, "w") as f:
                    f.write(new_config)

            ok_a, msg_a = validate_apache()

            if not ok_a:
                if DRY_RUN:
                    step(f"[dry-run] Would write to { conf }")
                else:
                    with open(conf, "w") as f:
                        f.write(current)
                err(f"Apache error (reverted):\n{msg_a}")
                continue

            reload_apache()
            apply_reloads()
            ok(f"Config updated for {domain}.")

        elif sub == "3":
            new_domain = input("  New domain name: ").strip()
            if not new_domain:
                warn("No domain entered, skipping.")
                continue

            # Optional new docroot
            new_docroot = ask_docroot("New DocumentRoot", default=f"/var/www/{new_domain}")

            copy_files = input("  Copy existing site files from old docroot to new docroot? (y/N): ").strip().lower() == "y"

            # MODIFICATION: Check if site has DNS
            meta = get_site_meta(domain)
            has_dns = meta.get("dns", False) if meta else False

            ip = None
            if has_dns:
                ip = prompt_for_ip("  Server IP (for DNS zones)")
                if not ip:
                    err("IP address required for DNS updates.")
                    continue

            # ----- 1. Update Apache vhost -----
            new_vhost = current
            # Replace ServerName and ServerAlias
            new_vhost = re.sub(r'ServerName\s+\S+', f'ServerName {new_domain}', new_vhost)
            new_vhost = re.sub(r'ServerAlias\s+\S+', f'ServerAlias www.{new_domain}', new_vhost)
            # Replace DocumentRoot and Directory if changed
            if new_docroot != old_docroot:
                new_vhost = re.sub(r'DocumentRoot\s+"[^"]+"', f'DocumentRoot "{new_docroot}"', new_vhost)
                new_vhost = re.sub(r'<Directory\s+"[^"]+"', f'<Directory "{new_docroot}"', new_vhost)

            # Write new vhost under NEW name (we will rename the file)
            new_conf_path = f"{APACHE_SITES_AVAILABLE}/{new_domain}.conf"
            backup_config(new_conf_path)
            if DRY_RUN:
                step(f"[dry-run] Would write to { new_conf_path }")
            else:
                with open(new_conf_path, "w") as f:
                    f.write(new_vhost)

            # Validate Apache with new config
            ok_a, msg_a = validate_apache()
            if not ok_a:
                os.remove(new_conf_path)
                err(f"Apache error with new config:\n{msg_a}")
                continue

            # Disable old site, enable new one
            disable_site_cmd(domain)
            enable_site(new_domain)

            # Remove old config file
            os.remove(conf)

            # ----- 2. Update DNS zones (if the site has DNS) -----
            if has_dns:
                # Determine old forward zone file (if exists)
                old_fwd_file = None

                if zone_exists(domain):
                    old_fwd_file = get_zone_file(domain)
                    # Remove old forward zone entirely and create new.
                    remove_zone_from_conf(domain)
                    if old_fwd_file and os.path.isfile(old_fwd_file):
                        # Optionally delete old zone file later
                        pass

                # Check for reverse zone using the IP provided (new IP)
                rev_zone_name = reverse_zone_name(ip)
                if zone_exists(rev_zone_name):
                    warn(f"Reverse zone {rev_zone_name} already exists, will be replaced.")
                    remove_zone_from_conf(rev_zone_name)

                # Create new forward zone
                new_fwd_file = f"{BIND_DIR}/db.{new_domain}"
                write_forward_zone(new_domain, ip, new_fwd_file)
                ok_z, msg_z = validate_zone(new_domain, new_fwd_file)
                if not ok_z:
                    os.remove(new_fwd_file)
                    err(f"Forward zone error:\n{msg_z}")
                    # Rollback Apache changes
                    os.rename(new_conf_path, conf)  # restore old conf
                    enable_site(domain)
                    disable_site_cmd(new_domain)
                    continue
                add_zone_to_conf(new_domain, new_fwd_file)

                # Create new reverse zone
                new_rev_file = reverse_zone_file(ip)
                write_reverse_zone(new_domain, ip, new_rev_file)
                ok_r, msg_r = validate_zone(rev_zone_name, new_rev_file)
                if not ok_r:
                    os.remove(new_rev_file)
                    remove_zone_from_conf(new_domain)
                    os.remove(new_fwd_file)
                    err(f"Reverse zone error:\n{msg_r}")
                    # Rollback Apache
                    os.rename(new_conf_path, conf)
                    enable_site(domain)
                    disable_site_cmd(new_domain)
                    continue
                add_zone_to_conf(rev_zone_name, new_rev_file)

                # Validate BIND9 overall
                ok_b, msg_b = validate_bind9()
                if not ok_b:
                    err(f"BIND9 error:\n{msg_b}")
                    # Rollback all DNS changes
                    remove_zone_from_conf(new_domain)
                    remove_zone_from_conf(rev_zone_name)
                    os.remove(new_fwd_file)
                    os.remove(new_rev_file)
                    # Restore old zones if they existed
                    if old_fwd_file and zone_exists(domain):
                        add_zone_to_conf(domain, old_fwd_file)
                    # Rollback Apache
                    os.rename(new_conf_path, conf)
                    enable_site(domain)
                    disable_site_cmd(new_domain)
                    continue

                # Optionally delete old zone files
                if old_fwd_file and os.path.isfile(old_fwd_file):
                    os.remove(old_fwd_file)
                # Old reverse zone file path can't be determined easily; skip.

                reload_bind9()
            else:
                warn(f"Site {domain} has no DNS zones, skipping DNS update.")

            # ----- 3. Update metadata -----
            old_meta = get_site_meta(domain)
            if old_meta:
                save_site_meta(
                    new_domain,
                    old_meta.get("type", "static"),
                    new_docroot,
                    db_name=old_meta.get("db_name"),
                    db_user=old_meta.get("db_user"),
                    dns=has_dns
                )
                delete_site_meta(domain)

            # ----- 4. Copy site files if requested -----
            if copy_files and new_docroot != old_docroot:
                Path(new_docroot).mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copytree(old_docroot, new_docroot, dirs_exist_ok=True)
                    ok(f"Site files copied from {old_docroot} to {new_docroot}")
                except Exception as e:
                    err(f"Failed to copy site files: {e}")

            # ----- 4.5. Optionally delete old document root -----
            if old_docroot != new_docroot and os.path.exists(old_docroot):
                del_old = input(f"  Delete old document root '{old_docroot}'? (y/N): ").strip().lower()
                if del_old == "y":
                    try:
                        shutil.rmtree(old_docroot)
                        ok(f"Deleted old document root: {old_docroot}")
                    except Exception as e:
                        err(f"Failed to delete old docroot: {e}")

            # ----- 5. Reload services -----
            reload_apache()
            apply_reloads()
            ok(f"Domain changed from {domain} to {new_domain}.")

        elif sub == "4":
            src = input("  Path to new website files: ").strip()
            if not src:
                warn("No path provided, skipping.")
                continue
            if not os.path.isfile(src):
                err(f"Directory not found: {src}")
                continue
            step(f"Copying files from {src} to {old_docroot}...")
            if copy_existing_site_files(src, old_docroot):
                ok(f"Website files replaced for {domain}.")
            else:
                err("Failed to copy website files.")


def list_full_sites():
    menu_header("Full Sites (Apache + DNS)")
    list_apache_sites()


def full_site_menu():
    while True:
        menu_header("Full Site Manager (Apache + optional DNS)")
        print("1. Create site")
        print("2. Modify site")
        print("3. Delete site")
        print("4. Enable site")
        print("5. Disable site")
        print("6. List all sites")
        print("0. Back")

        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            create_full_site()
        elif choice == "2":
            modify_full_site()
        elif choice == "3":
            delete_apache_site()
        elif choice == "4":
            enable_existing_site()
        elif choice == "5":
            disable_site()
        elif choice == "6":
            list_full_sites()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")


# ─────────────────────────────────────────
#  LAMP Site sub-menu helpers
# ─────────────────────────────────────────

def modify_lamp_site():
    """Edit an existing LAMP site's vhost and optionally DB credentials."""
    menu_header("Modify LAMP Site")

    domains = pick_sites()

    if not domains:
        return

    for domain in domains:
        conf = f"{APACHE_SITES_AVAILABLE}/{domain}.conf"

        if not os.path.isfile(conf):
            err(f"Config not found: {conf}")
            continue

        with open(conf, "r") as f:
            current = f.read()

        print(f"\n  {C.BOLD}{domain}{C.RESET} — what would you like to change?")
        print("  1. Change DocumentRoot")
        print("  2. Update DB password in index.php")
        print("  3. Replace entire vhost config (paste new)")
        print("  4. Replace website files (copy from path)")
        print("  0. Skip")

        sub = input("  \n  Choice: ").strip()

        if sub == "1":
            new_docroot = ask_docroot("New DocumentRoot")

            if not new_docroot:
                warn("Skipped.")
                continue

            updated = re.sub(
                r'DocumentRoot\s+"[^"]+"',
                f'DocumentRoot "{new_docroot}"',
                current
            )
            updated = re.sub(
                r'<Directory\s+"[^"]+"',
                f'<Directory "{new_docroot}"',
                updated
            )

            backup_config(conf)
            if DRY_RUN:
                step(f"[dry-run] Would write to { conf }")
            else:
                with open(conf, "w") as f:
                    f.write(updated)

            ok_a, msg_a = validate_apache()

            if not ok_a:
                if DRY_RUN:
                    step(f"[dry-run] Would write to { conf }")
                else:
                    with open(conf, "w") as f:
                        f.write(current)
                err(f"Apache error (reverted):\n{msg_a}")
                continue

            reload_apache()
            apply_reloads()
            ok(f"DocumentRoot updated for {domain}.")

        elif sub == "2":
            docroot = ask_docroot("Document root", default=f"/var/www/{domain}")

            index_path = os.path.join(docroot, "index.php")

            if not os.path.isfile(index_path):
                err(f"index.php not found at {index_path}")
                continue

            new_pass = input("  New DB password: ").strip()

            if not new_pass:
                warn("Skipped.")
                continue

            with open(index_path, "r") as f:
                php = f.read()

            php_updated = re.sub(
                r"\$pass\s*=\s*'[^']*'",
                f"$pass = '{new_pass}'",
                php
            )

            if DRY_RUN:
                step(f"[dry-run] Would write to { index_path }")
            else:
                with open(index_path, "w") as f:
                    f.write(php_updated)

            ok(f"DB password updated in index.php for {domain}.")
            warn("Remember to also update the MariaDB user password manually.")

        elif sub == "3":
            print(
                f"\n  {C.YELLOW}Paste new vhost config below."
                f" End with a line containing only END{C.RESET}"
            )
            lines = []

            while True:
                line = input()

                if line.strip() == "END":
                    break

                lines.append(line)

            new_config = "\n".join(lines) + "\n"

            backup_config(conf)
            if DRY_RUN:
                step(f"[dry-run] Would write to { conf }")
            else:
                with open(conf, "w") as f:
                    f.write(new_config)

            ok_a, msg_a = validate_apache()

            if not ok_a:
                if DRY_RUN:
                    step(f"[dry-run] Would write to { conf }")
                else:
                    with open(conf, "w") as f:
                        f.write(current)
                err(f"Apache error (reverted):\n{msg_a}")
                continue

            reload_apache()
            apply_reloads()
            ok(f"Config updated for {domain}.")

        else:
            warn("Skipped.")

        if sub == "4":
            src = input("  Path to new website files: ").strip()
            if not src:
                warn("No path provided, skipping.")
                continue
            if not os.path.isfile(src):
                err(f"Directory not found: {src}")
                continue
            meta = get_site_meta(domain)
            docroot = meta.get("docroot", f"/var/www/{domain}") if meta else f"/var/www/{domain}"
            step(f"Copying files from {src} to {docroot}...")
            if copy_existing_site_files(src, docroot):
                ok(f"Website files replaced for {domain}.")
            else:
                err("Failed to copy website files.")


def list_lamp_sites():
    menu_header("LAMP Sites (Apache + PHP + MariaDB + optional DNS)")
    list_apache_sites()


def lamp_site_menu():
    while True:
        menu_header("LAMP Site Manager (Apache + PHP + MariaDB + optional DNS)")
        print("1. Create site")
        print("2. Modify site")
        print("3. Delete site")
        print("4. Enable site")
        print("5. Disable site")
        print("6. List all sites")
        print("0. Back")

        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            create_lamp_site()
        elif choice == "2":
            modify_lamp_site()
        elif choice == "3":
            delete_apache_site()
        elif choice == "4":
            enable_existing_site()
        elif choice == "5":
            disable_site()
        elif choice == "6":
            list_lamp_sites()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")


# ─────────────────────────────────────────
#  Sub-menus
# ─────────────────────────────────────────

def apache_menu():
    while True:
        menu_header("Apache VirtualHost Manager")
        print("1.  Create HTTP site")
        print("2.  Create HTTPS site (existing cert)")
        print("3.  Create HTTPS site (self-signed)")
        print("4.  List sites")
        print("5.  Enable site")
        print("6.  Disable site")
        print("7.  Delete site")
        print("8.  Disable default Apache sites")
        print("9.  Config test")
        print("10. Reload Apache")
        print("0.  Back")

        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            create_http_site()
        elif choice == "2":
            create_https_existing()
        elif choice == "3":
            create_self_signed_site()
        elif choice == "4":
            list_apache_sites()
        elif choice == "5":
            enable_existing_site()
        elif choice == "6":
            disable_site()
        elif choice == "7":
            delete_apache_site()
        elif choice == "8":
            disable_default_site()
        elif choice == "9":
            ok_a, msg_a = validate_apache()
            ok(msg_a) if ok_a else err(msg_a)
        elif choice == "10":
            manual_reload_apache()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")

def create_nginx_full_site():
    menu_header("Create Full Nginx Site (Nginx + optional DNS)")

    if not ensure_nginx():
        return

    domain = ask_domain()
    if not domain:
        return

    dns_choice = _ask("  Create DNS zones for this site? (y/N): ", key="dns_choice").strip().lower()
    create_dns = (dns_choice == "y")

    ip = None
    if create_dns:
        ip = prompt_for_ip("Server IP")
        if not ip:
            err("IP address required for DNS.")
            return

    print("\nSSL options:")
    print("  1. HTTP only")
    print("  2. HTTPS (self-signed cert)")
    print("  3. HTTPS (existing cert)")

    ssl_choice = _ask("  \nChoice: ", key="ssl").strip()

    docroot = ask_docroot("Document root", default=f"/var/www/{domain}")

    print()

    cert = key = None

    if ssl_choice == "2":
        step("Generating self-signed certificate...")
        cert, key, ssl_err = make_self_signed(domain)
        if ssl_err:
            err(f"SSL error: {ssl_err}")
            return
        ok("Certificate generated.")

    elif ssl_choice == "3":
        cert = _ask("  Certificate path: ", key="cert_path").strip()
        key  = _ask("  Key path: ", key="key_path").strip()
        if not os.path.isfile(cert):
            err("Certificate not found.")
            return
        if not os.path.isfile(key):
            err("Key not found.")
            return

    step("Creating document root...")
    Path(docroot).mkdir(parents=True, exist_ok=True)

    # Custom site files?
    custom = ask_for_custom_site_files(docroot)
    if not custom:
        # No custom files — write the default placeholder index.html
        index = Path(docroot) / "index.html"
        if not index.exists():
            index.write_text(f"<html><body><h1>{domain}</h1></body></html>")

    step("Writing Nginx vhost config...")
    if ssl_choice in ("2", "3"):
        config = nginx_https_vhost(domain, docroot, cert, key)
    else:
        config = nginx_http_vhost(domain, docroot)

    vhost_path = write_nginx_vhost(domain, config)

    step("Validating Nginx config...")
    ok_n, msg_n = validate_nginx()

    if not ok_n:
        os.remove(vhost_path)
        err(f"Nginx config error:\n{msg_n}")
        return

    nginx_enable_site(domain)
    save_site_meta(domain, "static", docroot, web_server="nginx", dns=create_dns)
    ok("Nginx vhost created.")
    reload_nginx()

    if create_dns:
        rev_zone = reverse_zone_name(ip)
        fwd_file = f"{BIND_DIR}/db.{domain}"
        rev_file = reverse_zone_file(ip)

        if zone_exists(domain):
            warn(f"Forward zone {domain} already exists, skipping.")
        else:
            step("Writing forward zone...")
            write_forward_zone(domain, ip, fwd_file)
            ok_z, msg_z = validate_zone(domain, fwd_file)
            if not ok_z:
                os.remove(fwd_file)
                err(f"Zone error:\n{msg_z}")
                return
            add_zone_to_conf(domain, fwd_file)
            ok("Forward zone created.")

        if zone_exists(rev_zone):
            warn(f"Reverse zone {rev_zone} already exists, skipping.")
        else:
            step("Writing reverse zone...")
            write_reverse_zone(domain, ip, rev_file)
            ok_r, msg_r = validate_zone(rev_zone, rev_file)
            if not ok_r:
                os.remove(rev_file)
                remove_zone_from_conf(domain)
                err(f"Reverse zone error:\n{msg_r}")
                return
            add_zone_to_conf(rev_zone, rev_file)
            ok("Reverse zone created.")

        ok_b, msg_b = validate_bind9()
        if not ok_b:
            err(f"BIND9 error:\n{msg_b}")
            return
        reload_bind9()

    apply_reloads()

    proto = "https" if ssl_choice in ("2", "3") else "http"
    print(f"\n{C.BOLD}{'─' * 35}{C.RESET}")
    print(f"  {C.BOLD}Site:{C.RESET}    {C.GREEN}{domain}{C.RESET}")
    print(f"  Docroot: {docroot}")
    print(f"  Config:  {vhost_path}")
    if create_dns:
        print(f"  Fwd zone: {fwd_file}")
        print(f"  Rev zone: {rev_file}")
    else:
        print(f"  DNS:     {C.DIM}not created{C.RESET}")
    if cert:
        print(f"  Cert:    {cert}")
    print(f"  {C.BOLD}URL:{C.RESET}     {C.CYAN}{proto}://{domain}{C.RESET}")
    print(f"{C.BOLD}{'─' * 35}{C.RESET}")


def modify_nginx_full_site():
    menu_header("Modify Nginx Site")

    domains = pick_nginx_sites()
    if not domains:
        return

    for domain in domains:
        conf = f"{NGINX_SITES_AVAILABLE}/{domain}.conf"

        if not os.path.isfile(conf):
            err(f"Config not found: {conf}")
            continue

        with open(conf, "r") as f:
            current = f.read()

        print(f"\n  {C.BOLD}{domain}{C.RESET} — what would you like to change?")
        print("  1. Change document root")
        print("  2. Replace entire vhost config (paste new)")
        print("  3. Replace website files (copy from path)")
        print("  0. Skip")

        sub = input("  \n  Choice: ").strip()

        if sub == "1":
            new_docroot = ask_docroot("New DocumentRoot")
            if not new_docroot:
                warn("Skipped.")
                continue

            updated = re.sub(r"root\s+\S+;", f"root {new_docroot};", current)

            backup_config(conf)
            if DRY_RUN:
                step(f"[dry-run] Would write to { conf }")
            else:
                with open(conf, "w") as f:
                    f.write(updated)

            ok_n, msg_n = validate_nginx()
            if not ok_n:
                if DRY_RUN:
                    step(f"[dry-run] Would write to { conf }")
                else:
                    with open(conf, "w") as f:
                        f.write(current)
                err(f"Nginx error (reverted):\n{msg_n}")
                continue

            reload_nginx()
            apply_reloads()
            ok(f"Document root updated for {domain}.")

        elif sub == "2":
            print(f"\n  {C.YELLOW}Paste new vhost config. End with a line containing only END{C.RESET}")
            lines = []
            while True:
                line = input()
                if line.strip() == "END":
                    break
                lines.append(line)

            new_config = "\n".join(lines) + "\n"

            backup_config(conf)
            if DRY_RUN:
                step(f"[dry-run] Would write to { conf }")
            else:
                with open(conf, "w") as f:
                    f.write(new_config)

            ok_n, msg_n = validate_nginx()
            if not ok_n:
                if DRY_RUN:
                    step(f"[dry-run] Would write to { conf }")
                else:
                    with open(conf, "w") as f:
                        f.write(current)
                err(f"Nginx error (reverted):\n{msg_n}")
                continue

            reload_nginx()
            apply_reloads()
            ok(f"Config updated for {domain}.")

        elif sub == "3":
            src = input("  Path to new website files: ").strip()
            if not src:
                warn("No path provided, skipping.")
                continue
            if not os.path.isfile(src):
                err(f"Directory not found: {src}")
                continue
            meta = get_site_meta(domain)
            docroot = meta.get("docroot", f"/var/www/{domain}") if meta else f"/var/www/{domain}"
            step(f"Copying files from {src} to {docroot}...")
            if copy_existing_site_files(src, docroot):
                ok(f"Website files replaced for {domain}.")
            else:
                err("Failed to copy website files.")

        else:
            warn("Skipped.")


def full_nginx_site_menu():
    while True:
        menu_header("Full Nginx Site Manager (Nginx + optional DNS)")
        print("1. Create site")
        print("2. Modify site")
        print("3. Delete site")
        print("4. Enable site")
        print("5. Disable site")
        print("6. List all sites")
        print("0. Back")

        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            create_nginx_full_site()
        elif choice == "2":
            modify_nginx_full_site()
        elif choice == "3":
            delete_nginx_site()
        elif choice == "4":
            enable_nginx_existing_site()
        elif choice == "5":
            disable_nginx_existing_site()
        elif choice == "6":
            list_nginx_sites()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")

def create_lemp_site():
    menu_header("Create LEMP Site (Nginx + PHP + MariaDB + optional DNS)")

    if not ensure_nginx():
        return

    if not ensure_mariadb():
        return

    domain = ask_domain()
    if not domain:
        return

    dns_choice = _ask("  Create DNS zones for this site? (y/N): ", key="dns_choice").strip().lower()
    create_dns = (dns_choice == "y")

    ip = None
    if create_dns:
        ip = prompt_for_ip("Server IP")
        if not ip:
            err("IP address required for DNS.")
            return

    default_db = re.sub(r"[^a-zA-Z0-9_]", "_", domain.split(".")[0])
    db_name = ask_db_name("Database name", default_db)
    db_user = ask_db_user("Database user", f"{default_db}_user")
    db_pass = _ask("  Database password: ", key="db_pass").strip()

    if not db_pass:
        err("Password cannot be empty.")
        return

    print("\nSSL options:")
    print("  1. HTTP only")
    print("  2. HTTPS (self-signed cert)")
    ssl_choice = _ask("  \nChoice [1]: ", key="ssl").strip() or "1"

    docroot = ask_docroot("Document root", default=f"/var/www/{domain}")

    print()

    step("Installing PHP-FPM and MariaDB packages...")
    print(f"  {C.DIM}(this may take a minute){C.RESET}\n")

    rc, stderr = run_live([
        "apt-get", "install", "-y",
        "mariadb-server",
        "php-fpm",
        "php-mysql",
        "php-curl",
        "php-mbstring",
        "php-xml",
    ])

    if rc != 0:
        err(f"Package install failed:\n{stderr}")
        return

    ok("All packages installed.")

    step("Starting MariaDB...")
    run(["systemctl", "start",  "mariadb"])
    run(["systemctl", "enable", "mariadb"])
    ok(f"MariaDB: {service_status('mariadb')}")

    step("Setting up database...")
    sql_steps = [
        (f"CREATE DATABASE IF NOT EXISTS `{db_name}`",
         f"Creating database '{db_name}'"),
        (f"CREATE USER IF NOT EXISTS '{db_user}'@'localhost' IDENTIFIED BY '{db_pass}'",
         f"Creating user '{db_user}'"),
        (f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{db_user}'@'localhost'",
         f"Granting privileges on '{db_name}'.*"),
        ("FLUSH PRIVILEGES", "Flushing privileges"),
    ]

    for sql, label in sql_steps:
        print(f"    {C.DIM}{label}...{C.RESET}", end=" ", flush=True)
        result = run(["mysql", "-e", f"{sql};"])
        if result.returncode != 0:
            print(f"{C.RED}✗{C.RESET}")
            err(f"MariaDB error:\n{result.stderr}")
            return
        print(f"{C.GREEN}✓{C.RESET}")

    ok("Database and user ready.")

    cert = key = None
    if ssl_choice == "2":
        step("Generating self-signed certificate...")
        cert, key, ssl_err = make_self_signed(domain)
        if ssl_err:
            err(f"SSL error: {ssl_err}")
            return
        ok("Certificate generated.")

    step("Starting PHP-FPM...")
    import glob as _glob
    fpm_services = _glob.glob("/lib/systemd/system/php*-fpm.service")
    if fpm_services:
        fpm_svc = os.path.basename(fpm_services[0])[:-8]
        run(["systemctl", "enable", "--now", fpm_svc])
        ok(f"PHP-FPM ({fpm_svc}): {service_status(fpm_svc)}")
    else:
        run(["systemctl", "enable", "--now", "php-fpm"])
        ok("PHP-FPM started.")

    fpm_socket = find_php_fpm_socket()

    step("Creating document root and test page...")
    Path(docroot).mkdir(parents=True, exist_ok=True)
    custom = ask_for_custom_site_files(docroot)

    if not custom:
        index = Path(docroot) / "index.php"
        index.write_text(f"""<?php
$host = 'localhost';
$db   = '{db_name}';
$user = '{db_user}';
$pass = '{db_pass}';
$error = null; $pdo = null; $entries = []; $inserted = false;
try {{
    $pdo = new PDO("mysql:host=$host;dbname=$db", $user, $pass);
    $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $pdo->exec("CREATE TABLE IF NOT EXISTS test_entries (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        message VARCHAR(255) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )");
    if ($_SERVER['REQUEST_METHOD'] === 'POST' && !empty($_POST['name']) && !empty($_POST['message'])) {{
        $stmt = $pdo->prepare("INSERT INTO test_entries (name, message) VALUES (?, ?)");
        $stmt->execute([htmlspecialchars($_POST['name']), htmlspecialchars($_POST['message'])]);
        $inserted = true;
    }}
    $entries = $pdo->query("SELECT * FROM test_entries ORDER BY created_at DESC LIMIT 20")->fetchAll(PDO::FETCH_ASSOC);
}} catch (PDOException $e) {{ $error = $e->getMessage(); }}
?><!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>{domain} — LEMP</title>
<style>
  body{{font-family:sans-serif;padding:2rem;max-width:750px;margin:auto;color:#212529}}
  .badge{{display:inline-block;padding:2px 10px;border-radius:4px;font-size:.8rem;font-weight:600}}
  .green{{background:#d1e7dd;color:#0a3622}}.red{{background:#f8d7da;color:#58151c}}
  table{{width:100%;border-collapse:collapse;margin-top:.75rem;font-size:.9rem}}
  th,td{{padding:8px 12px;border:1px solid #dee2e6;text-align:left}}th{{background:#f8f9fa}}
  input[type=text]{{padding:6px 10px;border:1px solid #ced4da;border-radius:4px;width:200px}}
  button{{padding:6px 16px;background:#0d6efd;color:#fff;border:none;border-radius:4px;cursor:pointer}}
  code{{background:#e9ecef;padding:1px 5px;border-radius:3px}}
</style></head><body>
<h1>{domain}</h1>
<?php if ($error): ?>
  <p><span class="badge red">✗ DB Error</span> <code><?= htmlspecialchars($error) ?></code></p>
<?php else: ?>
  <p><span class="badge green">✓ Connected</span> &nbsp; db <code>{db_name}</code> · user <code>{db_user}</code></p>
  <p>PHP <?= phpversion() ?> · Nginx · MariaDB</p>
  <?php if ($inserted): ?><p style="color:green">✓ Entry inserted.</p><?php endif; ?>
  <h3>Insert test entry</h3>
  <form method="POST">
    <input type="text" name="name" placeholder="Name" required>
    <input type="text" name="message" placeholder="Message" required>
    <button type="submit">Insert</button>
  </form>
  <h3>Entries</h3>
  <?php if ($entries): ?>
  <table><tr><th>ID</th><th>Name</th><th>Message</th><th>Created</th></tr>
  <?php foreach ($entries as $r): ?>
  <tr><td><?= $r['id'] ?></td><td><?= htmlspecialchars($r['name']) ?></td>
      <td><?= htmlspecialchars($r['message']) ?></td><td><?= $r['created_at'] ?></td></tr>
  <?php endforeach; ?></table>
  <?php else: ?><p style="color:#6c757d;font-style:italic">No entries yet.</p><?php endif; ?>

  <h3>Verify from your Linux server</h3>
  <pre># Connect to MariaDB
mysql

# Connect as the site DB user
mysql -u {db_user} -p'{db_pass}' {db_name}

# Show all test entries
mysql {db_name} -e "SELECT * FROM test_entries;"

# Count rows
mysql {db_name} -e "SELECT COUNT(*) AS total FROM test_entries;"

# Show tables in the database
mysql {db_name} -e "SHOW TABLES;"

# Show the table structure
mysql {db_name} -e "DESCRIBE test_entries;"

# Show all databases
mysql -e "SHOW DATABASES;"

# Delete all test entries (reset)
mysql {db_name} -e "DELETE FROM test_entries;"</pre>
<?php endif; ?>
</body></html>
""")

    prompt_db_import(db_name, db_user, db_pass, docroot)

    step("Writing Nginx vhost config...")
    if ssl_choice == "2":
        config = nginx_lemp_https_vhost(domain, docroot, cert, key, fpm_socket)
    else:
        config = nginx_lemp_http_vhost(domain, docroot, fpm_socket)

    vhost_path = write_nginx_vhost(domain, config)

    step("Validating Nginx config...")
    ok_n, msg_n = validate_nginx()

    if not ok_n:
        os.remove(vhost_path)
        err(f"Nginx config error:\n{msg_n}")
        return

    nginx_enable_site(domain)
    save_site_meta(domain, "lamp", docroot, db_name=db_name, db_user=db_user, web_server="nginx", dns=create_dns)
    reload_nginx()

    if create_dns:
        rev_zone = reverse_zone_name(ip)
        fwd_file = f"{BIND_DIR}/db.{domain}"
        rev_file = reverse_zone_file(ip)

        if zone_exists(domain):
            warn(f"Forward zone {domain} already exists, skipping.")
        else:
            step("Writing forward zone...")
            write_forward_zone(domain, ip, fwd_file)
            ok_z, msg_z = validate_zone(domain, fwd_file)
            if not ok_z:
                os.remove(fwd_file)
                err(f"Zone error:\n{msg_z}")
                return
            add_zone_to_conf(domain, fwd_file)
            ok("Forward zone created.")

        if zone_exists(rev_zone):
            warn(f"Reverse zone {rev_zone} already exists, skipping.")
        else:
            step("Writing reverse zone...")
            write_reverse_zone(domain, ip, rev_file)
            ok_r, msg_r = validate_zone(rev_zone, rev_file)
            if not ok_r:
                os.remove(rev_file)
                remove_zone_from_conf(domain)
                err(f"Reverse zone error:\n{msg_r}")
                return
            add_zone_to_conf(rev_zone, rev_file)
            ok("Reverse zone created.")

        ok_b, msg_b = validate_bind9()
        if not ok_b:
            err(f"BIND9 error:\n{msg_b}")
            return
        reload_bind9()

    apply_reloads()

    proto = "https" if ssl_choice == "2" else "http"
    print(f"\n{C.BOLD}{'─' * 35}{C.RESET}")
    print(f"  {C.BOLD}Site:{C.RESET}    {C.GREEN}{domain}{C.RESET}")
    print(f"  Docroot: {docroot}")
    print(f"  Config:  {vhost_path}")
    print(f"  DB:      {db_name}  User: {db_user}")
    print(f"  PHP-FPM: {fpm_socket}")
    if create_dns:
        print(f"  Fwd zone: {fwd_file}")
        print(f"  Rev zone: {rev_file}")
    if cert:
        print(f"  Cert:    {cert}")
    print(f"  {C.BOLD}URL:{C.RESET}     {C.CYAN}{proto}://{domain}{C.RESET}")
    print(f"{C.BOLD}{'─' * 35}{C.RESET}")


def modify_lemp_site():
    menu_header("Modify LEMP Site")

    domains = pick_nginx_sites()
    if not domains:
        return

    for domain in domains:
        conf = f"{NGINX_SITES_AVAILABLE}/{domain}.conf"

        if not os.path.isfile(conf):
            err(f"Config not found: {conf}")
            continue

        with open(conf, "r") as f:
            current = f.read()

        print(f"\n  {C.BOLD}{domain}{C.RESET} — what would you like to change?")
        print("  1. Change document root")
        print("  2. Update DB password in index.php")
        print("  3. Replace entire vhost config (paste new)")
        print("  4. Replace website files (copy from path)")
        print("  0. Skip")

        sub = input("  \n  Choice: ").strip()

        if sub == "1":
            new_docroot = ask_docroot("New DocumentRoot")
            if not new_docroot:
                warn("Skipped.")
                continue

            updated = re.sub(r"root\s+\S+;", f"root {new_docroot};", current)

            backup_config(conf)
            if DRY_RUN:
                step(f"[dry-run] Would write to { conf }")
            else:
                with open(conf, "w") as f:
                    f.write(updated)

            ok_n, msg_n = validate_nginx()
            if not ok_n:
                if DRY_RUN:
                    step(f"[dry-run] Would write to { conf }")
                else:
                    with open(conf, "w") as f:
                        f.write(current)
                err(f"Nginx error (reverted):\n{msg_n}")
                continue

            reload_nginx()
            apply_reloads()
            ok(f"Document root updated for {domain}.")

        elif sub == "2":
            meta     = get_site_meta(domain)
            docroot  = meta.get("docroot", f"/var/www/{domain}") if meta else f"/var/www/{domain}"
            index_path = os.path.join(docroot, "index.php")

            if not os.path.isfile(index_path):
                err(f"index.php not found at {index_path}")
                continue

            new_pass = input("  New DB password: ").strip()
            if not new_pass:
                warn("Skipped.")
                continue

            with open(index_path, "r") as f:
                php = f.read()

            php_updated = re.sub(r"\$pass\s*=\s*'[^']*'", f"$pass = '{new_pass}'", php)

            if DRY_RUN:
                step(f"[dry-run] Would write to { index_path }")
            else:
                with open(index_path, "w") as f:
                    f.write(php_updated)

            ok(f"DB password updated in index.php for {domain}.")
            warn("Remember to also update the MariaDB user password manually.")

        elif sub == "3":
            print(f"\n  {C.YELLOW}Paste new vhost config. End with a line containing only END{C.RESET}")
            lines = []
            while True:
                line = input()
                if line.strip() == "END":
                    break
                lines.append(line)

            new_config = "\n".join(lines) + "\n"

            backup_config(conf)
            if DRY_RUN:
                step(f"[dry-run] Would write to { conf }")
            else:
                with open(conf, "w") as f:
                    f.write(new_config)

            ok_n, msg_n = validate_nginx()
            if not ok_n:
                if DRY_RUN:
                    step(f"[dry-run] Would write to { conf }")
                else:
                    with open(conf, "w") as f:
                        f.write(current)
                err(f"Nginx error (reverted):\n{msg_n}")
                continue

            reload_nginx()
            apply_reloads()
            ok(f"Config updated for {domain}.")

        elif sub == "4":
            src = input("  Path to new website files: ").strip()
            if not src:
                warn("No path provided, skipping.")
                continue
            if not os.path.isfile(src):
                err(f"Directory not found: {src}")
                continue
            meta = get_site_meta(domain)
            docroot = meta.get("docroot", f"/var/www/{domain}") if meta else f"/var/www/{domain}"
            step(f"Copying files from {src} to {docroot}...")
            if copy_existing_site_files(src, docroot):
                ok(f"Website files replaced for {domain}.")
            else:
                err("Failed to copy website files.")

        else:
            warn("Skipped.")


def lemp_site_menu():
    while True:
        menu_header("LEMP Site Manager (Nginx + PHP + MariaDB + optional DNS)")
        print("1. Create site")
        print("2. Modify site")
        print("3. Delete site")
        print("4. Enable site")
        print("5. Disable site")
        print("6. List all sites")
        print("0. Back")

        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            create_lemp_site()
        elif choice == "2":
            modify_lemp_site()
        elif choice == "3":
            delete_nginx_site()
        elif choice == "4":
            enable_nginx_existing_site()
        elif choice == "5":
            disable_nginx_existing_site()
        elif choice == "6":
            list_nginx_sites()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")


def nginx_menu():
    while True:
        nginx = service_status("nginx")

        menu_header("Nginx")
        print(f"  Nginx: {nginx}")
        print("=" * 35)
        print("1. Create HTTP site")
        print("2. Create HTTPS site (existing cert)")
        print("3. Create HTTPS site (self-signed)")
        print("4. List sites")
        print("5. Enable site")
        print("6. Disable site")
        print("7. Delete site")
        print("8. Config test")
        print("9. Reload Nginx")
        print("0. Back")

        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            create_nginx_http_site()
        elif choice == "2":
            create_nginx_https_existing()
        elif choice == "3":
            create_nginx_https_self_signed()
        elif choice == "4":
            list_nginx_sites()
        elif choice == "5":
            enable_nginx_existing_site()
        elif choice == "6":
            disable_nginx_existing_site()
        elif choice == "7":
            delete_nginx_site()
        elif choice == "8":
            ok_n, msg_n = validate_nginx()
            ok(msg_n) if ok_n else err(msg_n)
        elif choice == "9":
            ok_n, msg_n = validate_nginx()
            if ok_n:
                reload_nginx()
                apply_reloads()
            else:
                err(msg_n)
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")

def nginx_site_menu():
    while True:
        nginx   = service_status("nginx")
        mariadb = service_status("mariadb")

        menu_header("Nginx")
        print(f"  Nginx: {nginx}   MariaDB: {mariadb}")
        menu_separator()
        print("1. Full Site (Nginx + DNS)")
        print("2. LEMP Site (Nginx + PHP + MariaDB + DNS)")
        print("3. WordPress Site")
        print("4. Reverse Proxy Site")
        print("5. Static Site (No DNS)")
        print("6. Let's Encrypt SSL Site {WIP}")
        print("0. Back")

        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":   full_nginx_site_menu()
        elif choice == "2": lemp_site_menu()
        elif choice == "3": wordpress_nginx_menu()
        elif choice == "4": reverse_proxy_nginx_menu()
        elif choice == "5": static_nginx_menu()
        elif choice == "6": letsencrypt_nginx_menu()
        elif choice == "0": break
        else: warn("Invalid choice.")


def bind9_menu():
    while True:
        menu_header("BIND9 Zone Manager")
        print("1.  Create forward zone")
        print("2.  Create reverse zone")
        print("3.  Create forward + reverse zones")
        print("4.  List zones")
        print("5.  Delete zone")
        print("6.  Test DNS (dig)")
        print("7.  Config test")
        print("8.  Reload BIND9")
        print("9.  Add MX record")
        print("10. Remove MX record")
        print("11. List MX records")
        print("0.  Back")

        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            create_forward_zone()
        elif choice == "2":
            create_reverse_zone()
        elif choice == "3":
            create_both_zones()
        elif choice == "4":
            list_zones()
        elif choice == "5":
            delete_zone()
        elif choice == "6":
            test_dns()
        elif choice == "7":
            ok_b, msg_b = validate_bind9()
            ok(msg_b) if ok_b else err(msg_b)
        elif choice == "8":
            manual_reload_bind9()
        elif choice == "9":
            add_mx_record()
        elif choice == "10":
            remove_mx_record()
        elif choice == "11":
            list_mx_records()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")


# ─────────────────────────────────────────
#  Static Site (No DNS)
# ─────────────────────────────────────────

def create_apache_static_site():
    menu_header("Create Static Site (Apache, No DNS)")
    domain = ask_domain()
    if not domain: return
    docroot = f"/var/www/{domain}"
    create_docroot(docroot, domain)
    content = http_vhost(domain, docroot)
    write_vhost(domain, content)
    enable_site(domain)
    ok_a, msg_a = validate_apache()
    if not ok_a:
        err(f"Apache config error: {msg_a}")
        disable_site_cmd(domain)
        return
    reload_apache()
    save_site_meta(domain, "static", docroot, web_server="apache", dns=False)
    ok(f"Static site created: http://{domain}")
    print(f"  Document root: {docroot}")
    input_pause("\n  Press Enter to continue...")


def create_nginx_static_site():
    menu_header("Create Static Site (Nginx, No DNS)")
    domain = ask_domain()
    if not domain: return
    docroot = f"/var/www/{domain}"
    create_docroot(docroot, domain)
    content = nginx_http_vhost(domain, docroot)
    write_nginx_vhost(domain, content)
    nginx_enable_site(domain)
    ok_n, msg_n = validate_nginx()
    if not ok_n:
        err(f"Nginx config error: {msg_n}")
        nginx_disable_site(domain)
        return
    reload_nginx()
    save_site_meta(domain, "static", docroot, web_server="nginx", dns=False)
    ok(f"Static site created: http://{domain}")
    print(f"  Document root: {docroot}")
    input_pause("\n  Press Enter to continue...")


def static_apache_menu():
    while True:
        menu_header("Static Site (Apache, No DNS)")
        print("1. Create site")
        print("2. Delete site")
        print("3. Enable site")
        print("4. Disable site")
        print("5. List sites")
        print("0. Back")
        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue
        if choice == "1":   create_apache_static_site()
        elif choice == "2": delete_apache_site()
        elif choice == "3": enable_existing_site()
        elif choice == "4": disable_site()
        elif choice == "5": list_apache_sites()
        elif choice == "0": break
        else: warn("Invalid choice.")


def static_nginx_menu():
    while True:
        menu_header("Static Site (Nginx, No DNS)")
        print("1. Create site")
        print("2. Delete site")
        print("3. Enable site")
        print("4. Disable site")
        print("5. List sites")
        print("0. Back")
        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue
        if choice == "1":   create_nginx_static_site()
        elif choice == "2": delete_nginx_site()
        elif choice == "3": enable_nginx_existing_site()
        elif choice == "4": disable_nginx_existing_site()
        elif choice == "5": list_nginx_sites()
        elif choice == "0": break
        else: warn("Invalid choice.")


# ─────────────────────────────────────────
#  Reverse Proxy Site
# ─────────────────────────────────────────

def create_apache_reverse_proxy():
    menu_header("Create Reverse Proxy Site (Apache)")
    domain = ask_domain()
    if not domain: return
    
    dns_choice = _ask("  Create DNS zones for this site? (y/N): ", key="dns_choice").strip().lower()
    create_dns = (dns_choice == "y")

    ip = None
    if create_dns:
        ip = prompt_for_ip("Server IP")
        if not ip:
            create_dns = False

    target = _ask("  Proxy target [localhost:3000]: ", key="backend").strip()
    if not target:
        target = "localhost:3000"
    if not target.startswith("http://") and not target.startswith("https://"):
        target = "http://" + target
    run(["/usr/sbin/a2enmod", "proxy"])
    run(["/usr/sbin/a2enmod", "proxy_http"])
    vhost = f"""<VirtualHost *:80>
    ServerName {domain}
    ServerAlias www.{domain}

    ProxyPreserveHost On
    ProxyPass / {target}/
    ProxyPassReverse / {target}/

    ErrorLog ${{APACHE_LOG_DIR}}/{domain}-error.log
    CustomLog ${{APACHE_LOG_DIR}}/{domain}-access.log combined
</VirtualHost>
"""
    write_vhost(domain, vhost)
    enable_site(domain)
    ok_a, msg_a = validate_apache()
    if not ok_a:
        err(f"Apache config error: {msg_a}")
        disable_site_cmd(domain)
        return
    reload_apache()
    save_site_meta(domain, "proxy", target, web_server="apache", dns=create_dns)

    if create_dns:
        rev_zone = reverse_zone_name(ip)
        fwd_file = f"{BIND_DIR}/db.{domain}"
        rev_file = reverse_zone_file(ip)

        if zone_exists(domain):
            warn(f"Forward zone {domain} already exists, skipping.")
        else:
            step("Writing forward zone...")
            write_forward_zone(domain, ip, fwd_file)
            ok_z, msg_z = validate_zone(domain, fwd_file)
            if not ok_z:
                os.remove(fwd_file)
                err(msg_z)
                return
            add_zone_to_conf(domain, fwd_file)
            ok("Forward zone created.")

        if zone_exists(rev_zone):
            warn(f"Reverse zone {rev_zone} already exists, skipping.")
        else:
            step("Writing reverse zone...")
            write_reverse_zone(domain, ip, rev_file)
            ok_r, msg_r = validate_zone(rev_zone, rev_file)
            if not ok_r:
                os.remove(rev_file)
                remove_zone_from_conf(domain)
                err(msg_r)
                return
            add_zone_to_conf(rev_zone, rev_file)
            ok("Reverse zone created.")

        ok_b, msg_b = validate_bind9()
        if not ok_b:
            err(msg_b)
            return
        reload_bind9()

    apply_reloads()

    ok(f"Reverse proxy created: http://{domain} -> {target}")
    input_pause("\n  Press Enter to continue...")


def create_nginx_reverse_proxy():
    menu_header("Create Reverse Proxy Site (Nginx)")
    domain = ask_domain()
    if not domain: return

    dns_choice = _ask("  Create DNS zones for this site? (y/N): ", key="dns_choice").strip().lower()
    create_dns = (dns_choice == "y")

    ip = None
    if create_dns:
        ip = prompt_for_ip("Server IP")
        if not ip:
            create_dns = False
    target = _ask("  Proxy target [localhost:3000]: ", key="backend").strip()
    if not target:
        target = "localhost:3000"
    if not target.startswith("http://") and not target.startswith("https://"):
        target = "http://" + target
    vhost = f"""server {{
    listen 80;
    server_name {domain} www.{domain};

    location / {{
        proxy_pass {target};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}

    access_log /var/log/nginx/{domain}-access.log;
    error_log  /var/log/nginx/{domain}-error.log;
}}
"""
    write_nginx_vhost(domain, vhost)
    nginx_enable_site(domain)
    ok_n, msg_n = validate_nginx()
    if not ok_n:
        err(f"Nginx config error: {msg_n}")
        nginx_disable_site(domain)
        return
    reload_nginx()
    save_site_meta(domain, "proxy", target, web_server="nginx", dns=create_dns)

    if create_dns:
        rev_zone = reverse_zone_name(ip)
        fwd_file = f"{BIND_DIR}/db.{domain}"
        rev_file = reverse_zone_file(ip)

        if zone_exists(domain):
            warn(f"Forward zone {domain} already exists, skipping.")
        else:
            step("Writing forward zone...")
            write_forward_zone(domain, ip, fwd_file)
            ok_z, msg_z = validate_zone(domain, fwd_file)
            if not ok_z:
                os.remove(fwd_file)
                err(msg_z)
                return
            add_zone_to_conf(domain, fwd_file)
            ok("Forward zone created.")

        if zone_exists(rev_zone):
            warn(f"Reverse zone {rev_zone} already exists, skipping.")
        else:
            step("Writing reverse zone...")
            write_reverse_zone(domain, ip, rev_file)
            ok_r, msg_r = validate_zone(rev_zone, rev_file)
            if not ok_r:
                os.remove(rev_file)
                remove_zone_from_conf(domain)
                err(msg_r)
                return
            add_zone_to_conf(rev_zone, rev_file)
            ok("Reverse zone created.")

        ok_b, msg_b = validate_bind9()
        if not ok_b:
            err(msg_b)
            return
        reload_bind9()

    apply_reloads()

    ok(f"Reverse proxy created: http://{domain} -> {target}")
    if create_dns:
        print("  DNS:       created")
    input_pause("\n  Press Enter to continue...")


def reverse_proxy_apache_menu():
    while True:
        menu_header("Reverse Proxy (Apache)")
        print("1. Create reverse proxy")
        print("2. Delete site")
        print("3. List sites")
        print("0. Back")
        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue
        if choice == "1":   create_apache_reverse_proxy()
        elif choice == "2": delete_apache_site()
        elif choice == "3": list_apache_sites()
        elif choice == "0": break
        else: warn("Invalid choice.")


def reverse_proxy_nginx_menu():
    while True:
        menu_header("Reverse Proxy (Nginx)")
        print("1. Create reverse proxy")
        print("2. Delete site")
        print("3. List sites")
        print("0. Back")
        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue
        if choice == "1":   create_nginx_reverse_proxy()
        elif choice == "2": delete_nginx_site()
        elif choice == "3": list_nginx_sites()
        elif choice == "0": break
        else: warn("Invalid choice.")


# ─────────────────────────────────────────
#  Docker Sites & Services
# ─────────────────────────────────────────

def is_docker_installed():
    return shutil.which("docker") is not None

def is_compose_available():
    r = run(["docker", "compose", "version"])
    return r.returncode == 0

def install_docker():
    menu_header("Install Docker")
    if is_docker_installed():
        ok("Docker is already installed.")
        input_pause("\n  Press Enter to continue...")
        return True

    step("Checking dependencies...")
    r, err_msg = run_live(["apt-get", "install", "-y", "ca-certificates", "curl", "gnupg"])
    if r != 0:
        err(f"Dependency install failed:\n{err_msg}")
        return False

    step("Adding Docker's official GPG key...")
    run(["install", "-m", "0755", "-d", "/etc/apt/keyrings"])
    
    dl = run(["curl", "-fsSL", "https://download.docker.com/linux/debian/gpg"])
    if dl.returncode != 0:
        err(f"Failed to download Docker GPG key:\n{dl.stderr}")
        return False
        
    run(["rm", "-f", "/etc/apt/keyrings/docker.gpg"])
    if not DRY_RUN:
        subprocess.run(["gpg", "--dearmor", "-o", "/etc/apt/keyrings/docker.gpg"], input=dl.stdout, text=True, capture_output=True)
    run(["chmod", "a+r", "/etc/apt/keyrings/docker.gpg"])

    step("Setting up Docker apt repository...")
    codename_proc = run(["lsb_release", "-cs"])
    c_name = codename_proc.stdout.strip() if codename_proc.returncode == 0 else "bookworm"
    
    repo_line = f"deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian {c_name} stable"
    safe_write("/etc/apt/sources.list.d/docker.list", repo_line + "\n")

    step("Updating apt package index...")
    run_live(["apt-get", "update"])

    step("Installing Docker CE...")
    r, err_msg = run_live(["apt-get", "install", "-y", "docker-ce", "docker-ce-cli", "containerd.io", "docker-compose-plugin"])
    if r != 0:
        err(f"Docker installation failed:\n{err_msg}")
        return False
        
    step("Enabling Docker service...")
    run(["systemctl", "enable", "--now", "docker"])
    
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        run(["usermod", "-aG", "docker", sudo_user])
        info(f"Added user {sudo_user} to docker group. Please log out and log back in for this to take effect.")
        
    ok("Docker installed successfully.")
    input_pause("\n  Press Enter to continue...")
    return True

def _find_free_docker_port(start=8080, max_tries=200):
    for port in range(start, start + max_tries):
        if port in APACHE_BACKEND_PORTS:
            continue          # Apache lives here; a container on 8080 would stop it from ever starting
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return None

def create_container_site():
    menu_header("Create Container Site (Nginx Proxy)")
    
    if not is_docker_installed():
        err("Docker is not installed. Please install it first.")
        input_pause("\n  Press Enter to continue...")
        return
        
    if not ensure_nginx():
        return
        
    domain = ask_domain()
    if not domain: return
    
    image = _ask("  Container image [e.g. nginx:alpine]: ", key="image").strip()
    if not image: return
    
    int_port_str = _ask("  Internal port [80]: ", key="port").strip() or "80"
    if not int_port_str.isdigit():
        err("Port must be a number.")
        return
    internal_port = int(int_port_str)
    
    host_port = _find_free_docker_port()
    if not host_port:
        err("Could not find a free host port.")
        return
        
    net_chk = run(["docker", "network", "inspect", "zervermanager_net"])
    if net_chk.returncode != 0:
        step("Creating Docker network 'zervermanager_net'...")
        run(["docker", "network", "create", "zervermanager_net"])
        
    container_name = "site-" + domain.replace(".", "-")
    
    step(f"Starting container {container_name} on port {host_port}...")
    cmd = [
        "docker", "run", "-d", "--name", container_name,
        "--network", "zervermanager_net",
        "-p", f"127.0.0.1:{host_port}:{internal_port}",
        "--restart", "unless-stopped",
        image
    ]
    r, e = run_live(cmd)
    if r != 0:
        err(f"Container failed to start:\n{e}")
        return
        
    target = f"http://127.0.0.1:{host_port}"
    
    dns_choice = _ask("  Create DNS zones for this site? (y/N): ", key="dns_choice").strip().lower()
    create_dns = (dns_choice == "y")
    ip = None
    if create_dns:
        ip = prompt_for_ip("Server IP")
        if not ip:
            create_dns = False

    vhost = f"""server {{
    listen 80;
    server_name {domain} www.{domain};

    location / {{
        proxy_pass {target};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}

    access_log /var/log/nginx/{domain}-access.log;
    error_log  /var/log/nginx/{domain}-error.log;
}}
"""
    step("Writing Nginx vhost config...")
    write_nginx_vhost(domain, vhost)
    nginx_enable_site(domain)
    
    ok_n, msg_n = validate_nginx()
    if not ok_n:
        err(f"Nginx config error: {msg_n}")
        nginx_disable_site(domain)
        run(["docker", "stop", container_name])
        run(["docker", "rm", container_name])
        return
        
    reload_nginx()
    save_site_meta(domain, "docker", f"container:{image}", web_server="nginx", dns=create_dns, container_name=container_name, host_port=host_port)
    
    if create_dns:
        rev_zone = reverse_zone_name(ip)
        fwd_file = f"{BIND_DIR}/db.{domain}"
        rev_file = reverse_zone_file(ip)

        if not zone_exists(domain):
            step("Writing forward zone...")
            write_forward_zone(domain, ip, fwd_file)
            ok_z, msg_z = validate_zone(domain, fwd_file)
            if ok_z:
                add_zone_to_conf(domain, fwd_file)
                ok("Forward zone created.")
            else:
                os.remove(fwd_file)
                err(msg_z)

        if not zone_exists(rev_zone):
            step("Writing reverse zone...")
            write_reverse_zone(domain, ip, rev_file)
            ok_r, msg_r = validate_zone(rev_zone, rev_file)
            if ok_r:
                add_zone_to_conf(rev_zone, rev_file)
                ok("Reverse zone created.")
            else:
                os.remove(rev_file)
                remove_zone_from_conf(domain)
                err(msg_r)
        
        ok_b, msg_b = validate_bind9()
        if not ok_b:
            err(msg_b)
        else:
            reload_bind9()
            
    apply_reloads()
    ok(f"Container site created: http://{domain} -> {image}")
    input_pause("\n  Press Enter to continue...")

def delete_container_site():
    menu_header("Delete Container Site")
    domains = [d for d in pick_nginx_sites(show_enabled=True, show_disabled=True) if get_site_meta(d) and get_site_meta(d).get("type") == "docker"]
    if not domains:
        warn("No docker sites selected.")
        return
        
    print(f"\n{C.YELLOW}  WARNING — will delete container and Nginx vhost for:{C.RESET}")
    for d in domains:
        print(f"    - {d}")

    if not prompt_confirm(f"{C.YELLOW}Delete these sites?{C.RESET}"):
        warn("Cancelled.")
        return

    for domain in domains:
        meta = get_site_meta(domain)
        container_name = meta.get("container_name") if meta else None
        if container_name:
            step(f"Stopping and removing container {container_name}...")
            run(["docker", "stop", container_name])
            run(["docker", "rm", container_name])
            
        nginx_disable_site(domain)
        conf = f"{NGINX_SITES_AVAILABLE}/{domain}.conf"
        if os.path.isfile(conf):
            if DRY_RUN:
                step(f"[dry-run] Would remove {conf}")
            else:
                os.remove(conf)
        delete_site_meta(domain)
        ok(f"{domain} deleted.")
        
    ok_n, msg_n = validate_nginx()
    if not ok_n:
        err(msg_n)
        return
    reload_nginx()
    apply_reloads()
    input_pause("\n  Press Enter to continue...")

def list_container_sites():
    menu_header("Container Sites")
    if not os.path.exists(NGINX_SITES_AVAILABLE):
        warn("Nginx sites-available not found.")
        return

    found = False
    for file in sorted(os.listdir(NGINX_SITES_AVAILABLE)):
        if not file.endswith(".conf"):
            continue
        domain = file[:-5]
        meta = get_site_meta(domain)
        if not meta or meta.get("type") != "docker":
            continue
            
        found = True
        enabled = nginx_site_enabled(domain)
        status = f"{C.GREEN}enabled{C.RESET}" if enabled else f"{C.DIM}disabled{C.RESET}"
        
        c_name = meta.get("container_name", "unknown")
        port = meta.get("host_port", "unknown")
        
        c_status_res = run(["docker", "ps", "--filter", f"name={c_name}", "--format", "{{.Status}}"])
        c_status = c_status_res.stdout.strip() if c_status_res.returncode == 0 and c_status_res.stdout.strip() else "stopped/missing"
        
        print(f"  {C.BOLD}{domain}{C.RESET} [{status}] {site_type_label(domain)}")
        print(f"    {C.DIM}Container: {c_name} | Port: {port} | Status: {c_status}{C.RESET}")
        print()
        
    if not found:
        warn("No container sites found.")
    input_pause("\n  Press Enter to continue...")

def parse_multi_select(raw, count):
    if not raw: return []
    if raw == "all": return list(range(count))
    selected = []
    for part in raw.split(","):
        try:
            if "-" in part:
                s, e = part.split("-")
                for idx in range(int(s.strip()) - 1, int(e.strip())):
                    if 0 <= idx < count: selected.append(idx)
            else:
                idx = int(part.strip()) - 1
                if 0 <= idx < count: selected.append(idx)
        except ValueError:
            pass
    return list(set(selected))

def manage_containers_menu():
    while True:
        menu_header("Manage Containers")
        print("1. List all containers")
        print("2. Start container(s)")
        print("3. Stop container(s)")
        print("4. Restart container(s)")
        print("5. Remove container(s)")
        print("6. View container logs")
        print("0. Back")
        choice = input("  \nChoice: ").strip()
        
        if choice == "0": break
        if choice not in ("1", "2", "3", "4", "5", "6"):
            warn("Invalid choice.")
            continue
            
        res = run(["docker", "ps", "-a", "--format", "{{.Names}}|{{.Image}}|{{.Status}}"])
        if res.returncode != 0:
            err("Failed to list containers.")
            continue
            
        containers = [line.split("|") for line in res.stdout.strip().split("\n") if line]
        if not containers:
            warn("No containers found.")
            continue
            
        if choice == "1":
            print()
            for c in containers:
                print(f"  {C.BOLD}{c[0]}{C.RESET} | {c[1]} | {c[2]}")
            input_pause("\n  Press Enter to continue...")
            continue
            
        print()
        for i, c in enumerate(containers, 1):
            print(f"  {C.BOLD}{i}.{C.RESET} {c[0]} ({c[2]})")
            
        raw = input("  \nSelect (e.g. 1, 1-3, 1,2,3 or all): ").strip().lower()
        indices = parse_multi_select(raw, len(containers))
        if not indices: continue
        
        selected_names = [containers[idx][0] for idx in indices]
        
        if choice == "2":
            for name in selected_names:
                run(["docker", "start", name])
                ok(f"Started {name}")
        elif choice == "3":
            for name in selected_names:
                run(["docker", "stop", name])
                ok(f"Stopped {name}")
        elif choice == "4":
            for name in selected_names:
                run(["docker", "restart", name])
                ok(f"Restarted {name}")
        elif choice == "5":
            if prompt_confirm(f"{C.YELLOW}Remove selected containers?{C.RESET}"):
                for name in selected_names:
                    run(["docker", "rm", "-f", name])
                    ok(f"Removed {name}")
        elif choice == "6":
            for name in selected_names:
                follow = prompt_confirm(f"Follow logs for {name}?", default="no")
                cmd = ["docker", "logs", "--tail", "100"]
                if follow: cmd.append("-f")
                cmd.append(name)
                print(f"\n{C.CYAN}--- Logs for {name} ---{C.RESET}")
                run_live(cmd)
                print(f"{C.CYAN}-----------------------{C.RESET}\n")

def docker_oneclick_menu():
    while True:
        menu_header("Docker One-Click Services")
        print("1. Portainer (Docker Web UI)")
        print("2. Redis")
        print("3. PostgreSQL")
        print("0. Back")
        choice = input("  \nChoice: ").strip()
        
        if choice == "0": break
        
        if choice == "1":
            step("Starting Portainer...")
            r = run(["docker", "run", "-d", "-p", "127.0.0.1:9443:9443", "--name", "portainer", "--restart", "always", "-v", "/var/run/docker.sock:/var/run/docker.sock", "-v", "portainer_data:/data", "portainer/portainer-ce:latest"])
            if r.returncode == 0:
                ok("Portainer started. Available locally on port 9443 (https://127.0.0.1:9443)")
                if prompt_confirm("Create Nginx reverse proxy for Portainer?"):
                    if not ensure_nginx():
                        return
                    domain = ask_domain()
                    if domain:
                        vhost = f"""server {{
    listen 80;
    server_name {domain} www.{domain};
    location / {{
        proxy_pass https://127.0.0.1:9443;
        proxy_set_header Host $host;
    }}
}}"""
                        write_nginx_vhost(domain, vhost)
                        nginx_enable_site(domain)
                        reload_nginx()
                        apply_reloads()
                        save_site_meta(domain, "docker", "container:portainer", web_server="nginx", container_name="portainer", host_port=9443)
                        ok(f"Portainer accessible at http://{domain}")
            else:
                err(f"Failed to start Portainer: {r.stderr}")
                
        elif choice == "2":
            step("Starting Redis...")
            run(["docker", "network", "create", "zervermanager_net"])
            r = run(["docker", "run", "-d", "--name", "redis", "--network", "zervermanager_net", "-v", "redis_data:/data", "--restart", "unless-stopped", "redis:alpine"])
            if r.returncode == 0:
                ok("Redis started. Reachable by other containers at 'redis:6379'.")
            else:
                err(f"Failed to start Redis: {r.stderr}")
                
        elif choice == "3":
            pw = input("  Set PostgreSQL POSTGRES_PASSWORD: ").strip()
            if not pw:
                warn("Password required.")
                continue
            step("Starting PostgreSQL...")
            run(["docker", "network", "create", "zervermanager_net"])
            r = run(["docker", "run", "-d", "--name", "postgres", "--network", "zervermanager_net", "-e", f"POSTGRES_PASSWORD={pw}", "-v", "postgres_data:/var/lib/postgresql/data", "--restart", "unless-stopped", "postgres:alpine"])
            if r.returncode == 0:
                ok("PostgreSQL started. Reachable by other containers at 'postgres:5432'.")
            else:
                err(f"Failed to start PostgreSQL: {r.stderr}")
                
        input_pause("\n  Press Enter to continue...")

def docker_system_cleanup():
    menu_header("Docker System Cleanup")
    res = run(["docker", "system", "df"])
    if res.returncode == 0:
        print(res.stdout)
        
    if prompt_confirm(f"{C.YELLOW}Run docker system prune (removes unused images, build cache, stopped containers)?{C.RESET}"):
        run_live(["docker", "system", "prune", "-f"])
        ok("Cleanup complete.")
    input_pause("\n  Press Enter to continue...")

def docker_menu():
    while True:
        installed = f"{C.GREEN}Installed{C.RESET}" if is_docker_installed() else f"{C.RED}Not Installed{C.RESET}"
        containers_count = "0"
        if is_docker_installed():
            res = run(["docker", "ps", "-q"])
            if res.returncode == 0:
                containers_count = str(len([l for l in res.stdout.strip().split("\\n") if l]))
        
        menu_header(f"Docker [{installed}] (Running: {containers_count})")
        print("1. Install/Update Docker")
        print("2. Manage Containers")
        print("3. Create Container Site (proxied through Nginx)")
        print("4. One-Click Services (Portainer, Redis, PostgreSQL)")
        print("5. Docker System Cleanup (prune)")
        print("0. Back")
        
        choice = input("  \nChoice: ").strip()
        if choice == "0": break
        
        if choice == "1": install_docker()
        elif choice == "2": 
            if is_docker_installed(): manage_containers_menu()
            else: err("Docker not installed.")
        elif choice == "3": create_container_site()
        elif choice == "4": 
            if is_docker_installed(): docker_oneclick_menu()
            else: err("Docker not installed.")
        elif choice == "5":
            if is_docker_installed(): docker_system_cleanup()
            else: err("Docker not installed.")
        else: warn("Invalid choice.")


# ─────────────────────────────────────────
#  WordPress Site
# ─────────────────────────────────────────

def _install_wordpress(domain, docroot, db_name, db_user, db_pass, web_server="apache"):
    """Download and configure WordPress into docroot."""
    import urllib.request, tarfile, shutil

    wp_tar = "/tmp/wordpress.tar.gz"
    step("Downloading WordPress...")
    try:
        urllib.request.urlretrieve("https://wordpress.org/latest.tar.gz", wp_tar)
    except Exception as e:
        err(f"Download failed: {e}")
        return False

    step("Extracting WordPress...")
    try:
        with tarfile.open(wp_tar, "r:gz") as tar:
            tar.extractall("/tmp/")
        Path(docroot).mkdir(parents=True, exist_ok=True)
        for item in Path("/tmp/wordpress").iterdir():
            dest = Path(docroot) / item.name
            if dest.exists():
                if dest.is_dir(): shutil.rmtree(dest)
                else: dest.unlink()
            shutil.move(str(item), str(dest))
    except Exception as e:
        err(f"Extract failed: {e}")
        return False
    finally:
        try: os.remove(wp_tar)
        except OSError: pass
        try: shutil.rmtree("/tmp/wordpress", ignore_errors=True)
        except OSError: pass

    # Write wp-config.php
    wp_config_src = Path(docroot) / "wp-config-sample.php"
    wp_config_dst = Path(docroot) / "wp-config.php"
    if wp_config_src.exists():
        cfg = wp_config_src.read_text()
        cfg = cfg.replace("database_name_here", db_name)
        cfg = cfg.replace("username_here", db_user)
        cfg = cfg.replace("password_here", db_pass)
        cfg = cfg.replace("localhost", "localhost")
        wp_config_dst.write_text(cfg)
        ok("wp-config.php configured.")
    else:
        warn("wp-config-sample.php not found; configure wp-config.php manually.")

    run(["chown", "-R", "www-data:www-data", docroot])
    run(["chmod", "-R", "755", docroot])
    ok("WordPress files extracted and configured.")
    return True


def create_apache_wordpress_site():
    menu_header("Create WordPress Site (Apache)")
    if not ensure_mariadb():
        return
    if not ensure_nginx():
        return
    domain = ask_domain()
    if not domain: return
    docroot = f"/var/www/{domain}"

    dns_choice = _ask("  Create DNS zones for this site? (y/N): ", key="dns_choice").strip().lower()
    create_dns = (dns_choice == "y")
    ip = None
    if create_dns:
        ip = prompt_for_ip("Server IP")
        if not ip:
            err("IP address required for DNS.")
            return
    else:
        ip = detect_server_ip()

    default_db = re.sub(r"[^a-zA-Z0-9_]", "_", domain.split(".")[0])
    db_name = ask_db_name("Database name", default_db)
    db_user = ask_db_user("Database user", f"{default_db}_user")
    db_pass = _ask("  Database password: ", key="db_pass").strip()
    if not db_pass:
        err("Password is required.")
        return

    # Create DB
    r = run(["mysql", "-uroot", "-e",
             f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"])
    if r.returncode != 0: err(f"DB creation failed: {r.stderr.strip()}")
    else: ok(f"Database '{db_name}' ready.")
    run(["mysql", "-uroot", "-e",
         f"CREATE USER IF NOT EXISTS '{db_user}'@'localhost' IDENTIFIED BY '{db_pass}';"])
    run(["mysql", "-uroot", "-e",
         f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{db_user}'@'localhost'; FLUSH PRIVILEGES;"])
    ok(f"DB user '{db_user}' created.")

    # Ensure PHP
    step("Installing PHP and Apache module...")
    r = run(["apt-get", "install", "-y", "--no-install-recommends",
         "php", "php-mysql", "php-curl", "php-gd", "php-mbstring",
         "php-xml", "php-zip", "libapache2-mod-php"])
    if r.returncode != 0:
        err(f"Failed to install PHP packages:\n{r.stderr}")
        return
    configure_apache_ports()

    # Download WordPress
    if not _install_wordpress(domain, docroot, db_name, db_user, db_pass, "apache"):
        return

    # VHost
    vhost = f"""<VirtualHost *:8080>
    ServerName {domain}
    ServerAlias www.{domain}
    DocumentRoot {docroot}

    <Directory {docroot}>
        AllowOverride All
        Require all granted
    </Directory>

    ErrorLog ${{APACHE_LOG_DIR}}/{domain}-error.log
    CustomLog ${{APACHE_LOG_DIR}}/{domain}-access.log combined
</VirtualHost>
"""
    write_vhost(domain, vhost)
    run(["/usr/sbin/a2enmod", "rewrite"])
    enable_site(domain)
    ok_a, msg_a = validate_apache()
    if not ok_a:
        err(f"Apache config error: {msg_a}")
        return
    reload_apache()

    # Optional DNS
    fwd_file = rev_file = None
    if create_dns and ip:
        fwd_file = f"/etc/bind/db.{domain}"
        rev_file  = reverse_zone_file(ip)
        if not zone_exists(domain):
            write_forward_zone(domain, ip, fwd_file)
            add_zone_to_conf(domain, fwd_file)
            ok(f"Forward zone created: {fwd_file}")
        if not zone_exists(reverse_zone_name(ip)):
            write_reverse_zone(domain, ip, rev_file)
            add_zone_to_conf(reverse_zone_name(ip), rev_file)
            ok(f"Reverse zone created: {rev_file}")
        reload_bind9()

    save_site_meta(domain, "wordpress", docroot, db_name=db_name, db_user=db_user, web_server="apache", dns=create_dns)

    # ── Nginx proxy ──
    step("Configuring Nginx front-door proxy...")
    nginx_vhost = f"""server {{
    listen 80;
    server_name {domain} www.{domain};
    location / {{
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}"""
    write_nginx_vhost(domain, nginx_vhost)
    nginx_enable_site(domain)
    reload_nginx()
    ok("Nginx proxy configured.")

    # Restart related services
    step("Restarting related services...")
    run(["systemctl", "restart", "mariadb"])
    ok("MariaDB restarted.")
    run(["systemctl", "restart", "apache2"])
    ok("Apache2 restarted.")
    if create_dns:
        run(["systemctl", "restart", get_bind9_service_name()])
        ok("BIND9 restarted.")
    print(f"\n{C.BOLD}{'─' * 35}{C.RESET}")
    print(f"  {C.BOLD}Site:{C.RESET}         {C.GREEN}{domain}{C.RESET}")
    print(f"  Docroot:      {docroot}")
    print(f"  Apache conf:  /etc/apache2/sites-available/{domain}.conf")
    print(f"  Database:     {db_name}")
    print(f"  DB User:      {db_user}")
    if create_dns and fwd_file:
        print(f"  Forward zone: {fwd_file}")
        print(f"  Reverse zone: {rev_file}")
        print(f"  {C.BOLD}URL:{C.RESET}          {C.CYAN}http://{domain}{C.RESET}")
    else:
        print(f"  DNS:          {C.DIM}not created{C.RESET}")
        print(f"  {C.BOLD}URL (IP):{C.RESET}     {C.CYAN}http://{ip}{C.RESET}")
        print(f"  {C.DIM}To use the domain, add to your PC hosts file:{C.RESET}")
        print(f"  {C.YELLOW}  {ip}  {domain}{C.RESET}")
    print(f"  {C.DIM}Open in browser to complete WordPress setup wizard.{C.RESET}")
    print(f"{C.BOLD}{'─' * 35}{C.RESET}")
    input_pause("\n  Press Enter to continue...")


def create_nginx_wordpress_site():
    menu_header("Create WordPress Site (Nginx)")
    if not ensure_nginx():
        return
    if not ensure_mariadb():
        return
    domain = ask_domain()
    if not domain: return
    docroot = f"/var/www/{domain}"

    dns_choice = _ask("  Create DNS zones for this site? (y/N): ", key="dns_choice").strip().lower()
    create_dns = (dns_choice == "y")
    ip = None
    if create_dns:
        ip = prompt_for_ip("Server IP")
        if not ip:
            err("IP address required for DNS.")
            return
    else:
        ip = detect_server_ip()

    default_db = re.sub(r"[^a-zA-Z0-9_]", "_", domain.split(".")[0])
    db_name = ask_db_name("Database name", default_db)
    db_user = ask_db_user("Database user", f"{default_db}_user")
    db_pass = _ask("  Database password: ", key="db_pass").strip()
    if not db_pass:
        err("Password is required.")
        return

    r = run(["mysql", "-uroot", "-e",
             f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"])
    if r.returncode != 0: err(f"DB creation failed: {r.stderr.strip()}")
    else: ok(f"Database '{db_name}' ready.")
    run(["mysql", "-uroot", "-e",
         f"CREATE USER IF NOT EXISTS '{db_user}'@'localhost' IDENTIFIED BY '{db_pass}';"])
    run(["mysql", "-uroot", "-e",
         f"GRANT ALL PRIVILEGES ON `{db_name}`.* TO '{db_user}'@'localhost'; FLUSH PRIVILEGES;"])
    ok(f"DB user '{db_user}' created.")

    fpm_socket = find_php_fpm_socket()
    if fpm_socket == "127.0.0.1:9000":  # Means unix socket not found
        rc, stderr = run_live(["apt-get", "install", "-y", "--no-install-recommends",
                  "php-fpm", "php-mysql", "php-curl", "php-gd",
                  "php-mbstring", "php-xml", "php-zip"])
        if rc != 0:
            err(f"Failed to install PHP-FPM packages:\n{stderr}")
            return
                  
        step("Starting PHP-FPM...")
        import glob as _glob
        fpm_services = _glob.glob("/lib/systemd/system/php*-fpm.service")
        if fpm_services:
            fpm_svc = os.path.basename(fpm_services[0])[:-8]
            run(["systemctl", "enable", "--now", fpm_svc])
            ok(f"PHP-FPM ({fpm_svc}): {service_status(fpm_svc)}")
        else:
            run(["systemctl", "enable", "--now", "php-fpm"])
            ok("PHP-FPM started.")
            
        fpm_socket = find_php_fpm_socket()
        if fpm_socket == "127.0.0.1:9000":
            codename = detect_codename()
            if codename == "bullseye":
                fpm_socket = "/run/php/php7.4-fpm.sock"
            elif codename == "trixie":
                fpm_socket = "/run/php/php8.3-fpm.sock"
            else:
                # bookworm (default/reference)
                fpm_socket = "/run/php/php8.2-fpm.sock"
    if not _install_wordpress(domain, docroot, db_name, db_user, db_pass, "nginx"):
        return

    fpm_ref = f"unix:{fpm_socket}" if fpm_socket.startswith("/") else fpm_socket
    vhost = f"""server {{
    listen 80;
    server_name {domain} www.{domain};
    root {docroot};
    index index.php index.html;

    location / {{
        try_files $uri $uri/ /index.php?$args;
    }}

    location ~ \\.php$ {{
        include snippets/fastcgi-php.conf;
        fastcgi_pass {fpm_ref};
    }}

    location ~ /\\.ht {{
        deny all;
    }}

    access_log /var/log/nginx/{domain}-access.log;
    error_log  /var/log/nginx/{domain}-error.log;
}}
"""
    write_nginx_vhost(domain, vhost)
    nginx_enable_site(domain)
    ok_n, msg_n = validate_nginx()
    if not ok_n:
        err(f"Nginx config error: {msg_n}")
        nginx_disable_site(domain)
        return
    reload_nginx()

    # Optional DNS
    fwd_file = rev_file = None
    if create_dns and ip:
        fwd_file = f"/etc/bind/db.{domain}"
        rev_file  = reverse_zone_file(ip)
        if not zone_exists(domain):
            write_forward_zone(domain, ip, fwd_file)
            add_zone_to_conf(domain, fwd_file)
            ok(f"Forward zone created: {fwd_file}")
        if not zone_exists(reverse_zone_name(ip)):
            write_reverse_zone(domain, ip, rev_file)
            add_zone_to_conf(reverse_zone_name(ip), rev_file)
            ok(f"Reverse zone created: {rev_file}")
        reload_bind9()

    save_site_meta(domain, "wordpress", docroot, db_name=db_name, db_user=db_user, web_server="nginx", dns=create_dns)

    # Restart related services
    step("Restarting related services...")
    run(["systemctl", "restart", "mariadb"])
    ok("MariaDB restarted.")
    run(["systemctl", "restart", "nginx"])
    ok("Nginx restarted.")
    if create_dns:
        run(["systemctl", "restart", get_bind9_service_name()])
        ok("BIND9 restarted.")
    print(f"\n{C.BOLD}{'─' * 35}{C.RESET}")
    print(f"  {C.BOLD}Site:{C.RESET}         {C.GREEN}{domain}{C.RESET}")
    print(f"  Docroot:      {docroot}")
    print(f"  Nginx conf:   /etc/nginx/sites-available/{domain}.conf")
    print(f"  Database:     {db_name}")
    print(f"  DB User:      {db_user}")
    if create_dns and fwd_file:
        print(f"  Forward zone: {fwd_file}")
        print(f"  Reverse zone: {rev_file}")
        print(f"  {C.BOLD}URL:{C.RESET}          {C.CYAN}http://{domain}{C.RESET}")
    else:
        print(f"  DNS:          {C.DIM}not created{C.RESET}")
        print(f"  {C.BOLD}URL (IP):{C.RESET}     {C.CYAN}http://{ip}{C.RESET}")
        print(f"  {C.DIM}To use the domain, add to your PC hosts file:{C.RESET}")
        print(f"  {C.YELLOW}  {ip}  {domain}{C.RESET}")
    print(f"  {C.DIM}Open in browser to complete WordPress setup wizard.{C.RESET}")
    print(f"{C.BOLD}{'─' * 35}{C.RESET}")
    input_pause("\n  Press Enter to continue...")


def wordpress_apache_menu():
    while True:
        menu_header("WordPress Site (Apache)")
        print("1. Create WordPress site")
        print("2. Delete site")
        print("3. List sites")
        print("0. Back")
        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue
        if choice == "1":   create_apache_wordpress_site()
        elif choice == "2": delete_apache_site()
        elif choice == "3": list_apache_sites()
        elif choice == "0": break
        else: warn("Invalid choice.")


def wordpress_nginx_menu():
    while True:
        menu_header("WordPress Site (Nginx)")
        print("1. Create WordPress site")
        print("2. Delete site")
        print("3. List sites")
        print("0. Back")
        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue
        if choice == "1":   create_nginx_wordpress_site()
        elif choice == "2": delete_nginx_site()
        elif choice == "3": list_nginx_sites()
        elif choice == "0": break
        else: warn("Invalid choice.")


# ─────────────────────────────────────────
#  Let's Encrypt SSL Site
# ─────────────────────────────────────────

def _ensure_certbot(web_server="apache"):
    plugin = "python3-certbot-apache" if web_server == "apache" else "python3-certbot-nginx"
    if run(["dpkg", "-s", "certbot"]).returncode != 0 or run(["dpkg", "-s", plugin]).returncode != 0:
        info("Installing Certbot...")
        run_live(["apt-get", "install", "-y", "certbot", plugin])


def create_apache_letsencrypt_site():
    menu_header("Create Let's Encrypt SSL Site (Apache)")
    domain = ask_domain()
    if not domain: return
    email = input("  Admin email for Let's Encrypt: ").strip()
    if not email:
        err("Email is required for Let's Encrypt.")
        return
    docroot = f"/var/www/{domain}"
    create_docroot(docroot, domain)
    content = http_vhost(domain, docroot)
    write_vhost(domain, content)
    enable_site(domain)
    reload_apache()
    _ensure_certbot("apache")
    step("Running Certbot...")
    r = run(["certbot", "--apache", "-d", domain, "-d", f"www.{domain}",
             "--non-interactive", "--agree-tos", "-m", email, "--redirect"])
    if r.returncode == 0:
        ok(f"SSL certificate issued for {domain}.")
        ok(f"Site: https://{domain}")
        run(["systemctl", "enable", "certbot.timer"])
        ok("Auto-renewal enabled via certbot.timer.")
    else:
        warn("Certbot failed. Make sure the domain points to this server's public IP.")
        warn("The HTTP site is still active at http://" + domain)
        if r.stderr: print(f"  {C.DIM}{r.stderr.strip()}{C.RESET}")
    save_site_meta(domain, "letsencrypt", docroot, web_server="apache", dns=False)
    input_pause("\n  Press Enter to continue...")


def create_nginx_letsencrypt_site():
    menu_header("Create Let's Encrypt SSL Site (Nginx)")
    domain = ask_domain()
    if not domain: return
    email = input("  Admin email for Let's Encrypt: ").strip()
    if not email:
        err("Email is required for Let's Encrypt.")
        return
    docroot = f"/var/www/{domain}"
    create_docroot(docroot, domain)
    content = nginx_http_vhost(domain, docroot)
    write_nginx_vhost(domain, content)
    nginx_enable_site(domain)
    reload_nginx()
    _ensure_certbot("nginx")
    step("Running Certbot...")
    r = run(["certbot", "--nginx", "-d", domain, "-d", f"www.{domain}",
             "--non-interactive", "--agree-tos", "-m", email, "--redirect"])
    if r.returncode == 0:
        ok(f"SSL certificate issued for {domain}.")
        ok(f"Site: https://{domain}")
        run(["systemctl", "enable", "certbot.timer"])
        ok("Auto-renewal enabled via certbot.timer.")
    else:
        warn("Certbot failed. Make sure the domain points to this server's public IP.")
        warn("The HTTP site is still active at http://" + domain)
        if r.stderr: print(f"  {C.DIM}{r.stderr.strip()}{C.RESET}")
    save_site_meta(domain, "letsencrypt", docroot, web_server="nginx", dns=False)
    input_pause("\n  Press Enter to continue...")


def letsencrypt_apache_menu():
    while True:
        menu_header("Let's Encrypt SSL Site (Apache)")
        print("1. Create SSL site")
        print("2. Renew all certificates")
        print("3. Delete site")
        print("4. List sites")
        print("0. Back")
        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue
        if choice == "1":   create_apache_letsencrypt_site()
        elif choice == "2":
            step("Renewing all certificates...")
            r = run(["certbot", "renew"])
            ok("Renewal complete.") if r.returncode == 0 else err("Renewal failed.")
            input_pause("\n  Press Enter to continue...")
        elif choice == "3": delete_apache_site()
        elif choice == "4": list_apache_sites()
        elif choice == "0": break
        else: warn("Invalid choice.")


def letsencrypt_nginx_menu():
    while True:
        menu_header("Let's Encrypt SSL Site (Nginx)")
        print("1. Create SSL site")
        print("2. Renew all certificates")
        print("3. Delete site")
        print("4. List sites")
        print("0. Back")
        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue
        if choice == "1":   create_nginx_letsencrypt_site()
        elif choice == "2":
            step("Renewing all certificates...")
            r = run(["certbot", "renew"])
            ok("Renewal complete.") if r.returncode == 0 else err("Renewal failed.")
            input_pause("\n  Press Enter to continue...")
        elif choice == "3": delete_nginx_site()
        elif choice == "4": list_nginx_sites()
        elif choice == "0": break
        else: warn("Invalid choice.")


# ─────────────────────────────────────────
#  Make Site sub-menu (formerly main menu)
# ─────────────────────────────────────────

def apache_site_menu():
    while True:
        apache  = service_status("apache2")
        mariadb = service_status("mariadb")

        menu_header("Apache")
        print(f"  Apache2: {apache}   MariaDB: {mariadb}")
        menu_separator()
        print("1. Full Site (Apache + DNS)")
        print("2. LAMP Site (Apache + PHP + MariaDB + DNS)")
        print("3. WordPress Site")
        print("4. Reverse Proxy Site")
        print("5. Static Site (No DNS)")
        print("6. Let's Encrypt SSL Site {WIP}")
        print("0. Back")

        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":   full_site_menu()
        elif choice == "2": lamp_site_menu()
        elif choice == "3": wordpress_apache_menu()
        elif choice == "4": reverse_proxy_apache_menu()
        elif choice == "5": static_apache_menu()
        elif choice == "6": letsencrypt_apache_menu()
        elif choice == "0": break
        else: warn("Invalid choice.")


def make_site_menu():
    while True:
        menu_header("Make Site")
        print("1. Apache")
        print("2. Nginx")
        print("3. BIND9 Zone Manager")
        print("0. Back")

        choice = input("  \nChoice: ").strip()

        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            apache_site_menu()
        elif choice == "2":
            nginx_site_menu()
        elif choice == "3":
            bind9_menu()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")


# ─────────────────────────────────────────
#  Server IP management (Debian 12)
# ─────────────────────────────────────────

NETWORK_INTERFACES = "/etc/network/interfaces"


def _read_interfaces():
    if not os.path.isfile(NETWORK_INTERFACES):
        return None
    with open(NETWORK_INTERFACES, "r") as f:
        return f.read()


def _parse_interfaces(content):
    """Return list of dicts: {name, method, options{}}."""
    interfaces = []
    current    = None

    for line in content.splitlines():
        stripped = line.strip()

        if stripped.startswith("iface "):
            parts = stripped.split()
            if len(parts) >= 4:
                if current:
                    interfaces.append(current)
                current = {
                    "name":    parts[1],
                    "family":  parts[2],
                    "method":  parts[3],
                    "options": {},
                }
        elif current and stripped and not stripped.startswith("#"):
            if " " in stripped:
                key, _, val = stripped.partition(" ")
                current["options"][key] = val

    if current:
        interfaces.append(current)

    return interfaces


def _replace_iface_block(content, iface_name, new_block):
    """Replace the auto + iface block for iface_name with new_block."""
    lines     = content.splitlines(keepends=True)
    new_lines = []
    skip      = False
    inserted  = False

    i = 0
    while i < len(lines):
        line     = lines[i]
        stripped = line.strip()

        # Replace 'auto <iface>' line
        if stripped == f"auto {iface_name}":
            if not inserted:
                new_lines.append(new_block + "\n")
                inserted = True
            skip = True
            i += 1
            continue

        # Replace 'iface <iface> ...' line (if auto was missing)
        if stripped.startswith(f"iface {iface_name} "):
            if not inserted:
                new_lines.append(new_block + "\n")
                inserted = True
            skip = True
            i += 1
            continue

        # Skip indented option lines that belong to the replaced block
        if skip:
            if line.startswith((" ", "\t")):
                i += 1
                continue
            else:
                skip = False

        new_lines.append(line)
        i += 1

    if not inserted:
        new_lines.append("\n" + new_block + "\n")

    return "".join(new_lines)


def _pick_interface(interfaces):
    """Numbered picker; returns selected interface dict or None."""
    if not interfaces:
        warn("No interfaces found in configuration.")
        return None

    print()
    for i, iface in enumerate(interfaces, 1):
        if iface["method"] == "static":
            mlabel = f"{C.GREEN}static{C.RESET}"
        else:
            mlabel = f"{C.CYAN}{iface['method']}{C.RESET}"
        print(f"  {C.BOLD}{i}.{C.RESET} {iface['name']} [{mlabel}]")
        for k, v in iface["options"].items():
            print(f"       {C.DIM}{k}: {v}{C.RESET}")

    print()
    raw = input("  Select interface: ").strip()

    # Allow exact name match for Web UI integration
    for iface in interfaces:
        if iface["name"] == raw:
            return iface

    try:
        idx = int(raw) - 1
        if 0 <= idx < len(interfaces):
            return interfaces[idx]
        warn("Invalid selection.")
    except ValueError:
        warn("Invalid input.")

    return None


def _apply_networking(iface_name, backup_path):
    apply_now = input(
        "\n  Apply changes now? Networking will restart (yes/no): "
    ).strip().lower()

    if apply_now != "yes":
        warn("Changes saved. Run 'systemctl restart networking' to apply.")
        return

    step("Restarting networking service...")
    result = run(["systemctl", "restart", "networking"])

    if result.returncode == 0:
        ok("Networking restarted successfully.")

        result2 = run(["ip", "addr", "show", iface_name])
        if result2.returncode == 0:
            print()
            for line in result2.stdout.splitlines():
                print(f"    {C.DIM}{line}{C.RESET}")
    else:
        err(f"Networking restart failed:\n{result.stderr.strip()}")
        warn(f"Original backed up at {backup_path} — restore with:")
        info(f"  cp {backup_path} {NETWORK_INTERFACES}")


def show_current_ip_config():
    menu_header("Current Network Configuration")

    result = run(["ip", "-c=never", "addr"])

    if result.returncode == 0:
        for line in result.stdout.splitlines():
            print(f"  {C.DIM}{line}{C.RESET}")
    else:
        result = run(["ip", "addr"])
        for line in result.stdout.splitlines():
            print(f"  {C.DIM}{line}{C.RESET}")

    print()
    content = _read_interfaces()

    if content:
        print(f"  {C.BOLD}{NETWORK_INTERFACES}:{C.RESET}")
        for line in content.splitlines():
            print(f"  {C.DIM}{line}{C.RESET}")
    else:
        warn(f"{NETWORK_INTERFACES} not found.")

    print()


def get_bind9_service_name():
    # Use named if bind9 service isn't found
    res = subprocess.run(["systemctl", "is-active", "bind9"], capture_output=True, text=True)
    if "unknown" in res.stdout or "not-found" in res.stderr:
        return "named"
    return "bind9"

def set_interface_static():
    """Interactively set (or update) an interface to a static IP."""
    menu_header("Set / Change Static IP")

    content = _read_interfaces()

    if content is None:
        err(f"{NETWORK_INTERFACES} not found.")
        return

    iface = _pick_interface(_parse_interfaces(content))

    if not iface:
        return

    iface_name  = iface["name"]
    cur_addr    = iface["options"].get("address", "")
    cur_netmask = iface["options"].get("netmask", "255.255.255.0")
    cur_gw      = iface["options"].get("gateway", "")
    cur_dns     = iface["options"].get("dns-nameservers", "8.8.8.8 8.8.4.4")

    print(f"\n  Configuring {C.BOLD}{iface_name}{C.RESET} as static\n")

    new_addr    = input(f"  IP address    [{cur_addr}]: ").strip()    or cur_addr
    new_netmask = input(f"  Netmask       [{cur_netmask}]: ").strip() or cur_netmask
    
    gw_prefix = ""
    if new_addr and new_addr.count(".") == 3:
        gw_prefix = ".".join(new_addr.split(".")[:3]) + "."
        
    gw_prompt = f"  Gateway       [{cur_gw if cur_gw else gw_prefix}]: "
    new_gw_in   = input(gw_prompt).strip()
    
    if not new_gw_in:
        new_gw = cur_gw
    elif "." not in new_gw_in and gw_prefix:
        new_gw = gw_prefix + new_gw_in
    else:
        new_gw = new_gw_in
    new_dns     = input(f"  DNS servers   [{cur_dns}]: ").strip()     or cur_dns

    if not new_addr:
        err("IP address is required.")
        return

    new_block = (
        f"auto {iface_name}\n"
        f"iface {iface_name} inet static\n"
        f"    address {new_addr}\n"
        f"    netmask {new_netmask}\n"
    )

    if new_gw:
        new_block += f"    gateway {new_gw}\n"
    if new_dns:
        new_block += f"    dns-nameservers {new_dns}\n"

    backup = f"{NETWORK_INTERFACES}.bak"
    shutil.copy2(NETWORK_INTERFACES, backup)
    step(f"Backup saved → {backup}")

    new_content = _replace_iface_block(content, iface_name, new_block)

    backup_config(NETWORK_INTERFACES)
    if DRY_RUN:
        step(f"[dry-run] Would write to { NETWORK_INTERFACES }")
    else:
        with open(NETWORK_INTERFACES, "w") as f:
            f.write(new_content)

    ok(f"{iface_name} updated to static {new_addr}.")
    _apply_networking(iface_name, backup)


def set_interface_dhcp():
    """Switch an interface to DHCP."""
    menu_header("Set Interface to DHCP")

    content = _read_interfaces()

    if content is None:
        err(f"{NETWORK_INTERFACES} not found.")
        return

    iface = _pick_interface(_parse_interfaces(content))

    if not iface:
        return

    iface_name = iface["name"]
    new_block  = (
        f"auto {iface_name}\n"
        f"iface {iface_name} inet dhcp\n"
    )

    backup = f"{NETWORK_INTERFACES}.bak"
    shutil.copy2(NETWORK_INTERFACES, backup)
    step(f"Backup saved → {backup}")

    new_content = _replace_iface_block(content, iface_name, new_block)

    backup_config(NETWORK_INTERFACES)
    if DRY_RUN:
        step(f"[dry-run] Would write to { NETWORK_INTERFACES }")
    else:
        with open(NETWORK_INTERFACES, "w") as f:
            f.write(new_content)

    ok(f"{iface_name} set to DHCP.")
    _apply_networking(iface_name, backup)


def restore_interfaces_backup():
    """Restore /etc/network/interfaces from the .bak file."""
    backup = f"{NETWORK_INTERFACES}.bak"

    if not os.path.isfile(backup):
        warn(f"No backup found at {backup}.")
        return

    menu_header("Restore Network Interfaces Backup")

    with open(backup, "r") as f:
        content = f.read()

    print(f"\n  {C.BOLD}Backup content:{C.RESET}")
    for line in content.splitlines():
        print(f"  {C.DIM}{line}{C.RESET}")

    if not prompt_confirm(f"{C.YELLOW}Restore this backup?{C.RESET}"):
        warn("Cancelled.")
        return

    shutil.copy2(backup, NETWORK_INTERFACES)
    ok(f"Restored from {backup}.")
    _apply_networking("", backup)



def detect_netplan():
    return os.path.isdir("/etc/netplan")

def _pick_netplan_interface():
    result = subprocess.run(["ip", "-o", "link", "show"], capture_output=True, text=True)
    ifaces = []
    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) > 1:
            name = parts[1].strip()
            if name != "lo" and "@" not in name:
                ifaces.append(name)
    if not ifaces:
        err("No network interfaces found.")
        return None

    print("\n  Available Interfaces:")
    for i, name in enumerate(ifaces, 1):
        print(f"  {i}. {name}")
    
    choice = input("  \nSelect interface number: ").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(ifaces):
            return ifaces[idx]
    except ValueError:
        pass
    err("Invalid choice.")
    return None

def set_netplan_static():
    menu_header("Set Static IP (Netplan)")
    iface_name = _pick_netplan_interface()
    if not iface_name: return

    print(f"\n  Configuring {C.BOLD}{iface_name}{C.RESET} as static\n")
    
    new_addr = input("  IP address (CIDR, e.g. 192.168.1.10/24): ").strip()
    if not new_addr or "/" not in new_addr:
        err("Invalid IP. CIDR notation (e.g. /24) is required for Netplan.")
        return

    new_gw = input("  Gateway: ").strip()
    new_dns = input("  DNS servers (comma separated, e.g. 8.8.8.8, 8.8.4.4): ").strip()

    routes_block = f"\n      routes:\n        - to: default\n          via: {new_gw}" if new_gw else ""
    
    if new_dns:
        dns_list = ", ".join(f'"{d.strip()}"' for d in new_dns.split(","))
        dns_block = f"\n      nameservers:\n        addresses: [{dns_list}]"
    else:
        dns_block = ""

    yaml_content = f"""network:
  version: 2
  renderer: networkd
  ethernets:
    {iface_name}:
      dhcp4: false
      addresses:
        - {new_addr}{routes_block}{dns_block}
"""
    yaml_path = "/etc/netplan/99-zervermanager.yaml"
    
    if DRY_RUN:
        step(f"[dry-run] Would write static config to {yaml_path}")
    else:
        with open(yaml_path, "w") as f:
            f.write(yaml_content)
        step(f"Netplan generated at {yaml_path}")
        print(f"  {C.CYAN}Applying Netplan...{C.RESET}", end=" ", flush=True)
        res = run(["netplan", "apply"])
        if res.returncode == 0:
            print(f"{C.GREEN}✓{C.RESET}")
            ok("Static IP applied.")
        else:
            print(f"{C.RED}✗{C.RESET}")
            err(res.stderr.strip())


def set_netplan_dhcp():
    menu_header("Set Interface to DHCP (Netplan)")
    iface_name = _pick_netplan_interface()
    if not iface_name: return
    
    yaml_content = f"""network:
  version: 2
  renderer: networkd
  ethernets:
    {iface_name}:
      dhcp4: true
"""
    yaml_path = "/etc/netplan/99-zervermanager.yaml"
    
    if DRY_RUN:
        step(f"[dry-run] Would write DHCP config to {yaml_path}")
    else:
        with open(yaml_path, "w") as f:
            f.write(yaml_content)
        step(f"Netplan generated at {yaml_path}")
        print(f"  {C.CYAN}Applying Netplan...{C.RESET}", end=" ", flush=True)
        res = run(["netplan", "apply"])
        if res.returncode == 0:
            print(f"{C.GREEN}✓{C.RESET}")
            ok("DHCP applied.")
        else:
            print(f"{C.RED}✗{C.RESET}")
            err(res.stderr.strip())

def manage_server_ip():
    while True:
        menu_header("Manage Server IP")
        print("1. Show current IP / interface config")
        print("2. Set / change static IP")
        print("3. Set interface to DHCP")
        print("4. Restore backup (/etc/network/interfaces.bak)")
        print("0. Back")

        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            show_current_ip_config()
        elif choice == "2":
            if detect_netplan():
                set_netplan_static()
            else:
                set_interface_static()
        elif choice == "3":
            if detect_netplan():
                set_netplan_dhcp()
            else:
                set_interface_dhcp()
        elif choice == "4":
            restore_interfaces_backup()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")

def service_control_menu():
    """Interactive menu to start/stop/restart/enable/disable system services with multi‑select."""
    services = ["apache2", "nginx", "bind9", "mariadb"]

    while True:
        # Display current status for each service
        menu_header("Manage Server Services")

        for svc in services:
            status = service_status(svc)
            print(f"  {C.BOLD}{svc}{C.RESET}: {status}")

        print("-" * 35)
        print("1. Start service(s)")
        print("2. Stop service(s)")
        print("3. Restart service(s)")
        print("4. Enable service(s) (start at boot)")
        print("5. Disable service(s) (don't start at boot)")
        print("6. Show detailed status of service(s)")
        print("0. Back")

        choice = input("  \nChoice: ").strip()

        if choice == "0":
            break

        if choice not in ("1", "2", "3", "4", "5", "6"):
            warn("Invalid choice.")
            continue

        # Show numbered list of services
        print()
        for i, svc in enumerate(services, 1):
            print(f"  {C.BOLD}{i}.{C.RESET} {svc}")
        print()

        # Get multi‑selection
        raw = input("  Select service(s) (e.g. 1, 1-3, 1,2,3 or all): ").strip().lower()
        if not raw:
            warn("No services selected.")
            continue

        selected_indices = set()

        if raw == "all":
            selected_indices = set(range(len(services)))
        else:
            # Parse comma‑separated or range (1-3)
            parts = raw.replace(" ", "").split(",")
            for part in parts:
                if "-" in part:
                    try:
                        start, end = map(int, part.split("-"))
                        for idx in range(start - 1, end):
                            if 0 <= idx < len(services):
                                selected_indices.add(idx)
                    except ValueError:
                        warn(f"Invalid range: {part}")
                else:
                    try:
                        idx = int(part) - 1
                        if 0 <= idx < len(services):
                            selected_indices.add(idx)
                        else:
                            warn(f"Invalid number: {part}")
                    except ValueError:
                        warn(f"Invalid input: {part}")

        if not selected_indices:
            warn("No valid services selected.")
            continue

        selected_services = [services[i] for i in sorted(selected_indices)]

        # Perform the chosen action on each selected service
        for svc in selected_services:
            if choice == "1":   # start
                step(f"Starting {svc}...")
                result = run(["systemctl", "start", svc])
            elif choice == "2": # stop
                step(f"Stopping {svc}...")
                result = run(["systemctl", "stop", svc])
            elif choice == "3": # restart
                step(f"Restarting {svc}...")
                result = run(["systemctl", "restart", svc])
            elif choice == "4": # enable
                step(f"Enabling {svc}...")
                result = run(["systemctl", "enable", svc])
            elif choice == "5": # disable
                step(f"Disabling {svc}...")
                result = run(["systemctl", "disable", svc])
            elif choice == "6": # status
                step(f"Status of {svc}:")
                result = run(["systemctl", "status", svc, "--no-pager"])
                print(result.stdout)
                continue  # no need to print success/failure for status

            if result.returncode == 0:
                ok(f"{svc}: command successful. New status: {service_status(svc)}")
            else:
                err(f"{svc}: command failed:\n{result.stderr.strip()}")
        print()  # blank line after processing all selected services


# ─────────────────────────────────────────
#  Reload all related services
# ─────────────────────────────────────────

def reload_all_services():
    menu_header("Reload All Related Services")

    services = [
        ("apache2", "Apache2", "reload"),
        ("nginx",   "Nginx",   "reload"),
        ("bind9",   "BIND9",   "reload"),
        ("mariadb", "MariaDB", "restart"),
    ]

    any_action = False

    for service, label, action in services:
        status = run(["systemctl", "is-active", service])

        if status.stdout.strip() == "active":
            any_action = True
            print(
                f"  {C.CYAN}{action.capitalize()}ing {label}...{C.RESET}",
                end=" ", flush=True
            )
            result = run(["systemctl", action, service])

            if result.returncode == 0:
                print(f"{C.GREEN}✓{C.RESET}")
            else:
                print(f"{C.RED}✗{C.RESET}")
                err(result.stderr.strip())
        else:
            print(f"  {C.DIM}{label}: not running — skipped.{C.RESET}")

    print()

    if any_action:
        ok("All active services reloaded.")
    else:
        warn("No services were running.")


# ─────────────────────────────────────────
#  Dependency check / auto-install
# ─────────────────────────────────────────

def fix_dpkg():
    """Run dpkg --configure -a and apt --fix-broken install to repair any interrupted package installs."""
    info("Repairing package state (dpkg --configure -a)...")
    rc, stderr = run_live(["dpkg", "--configure", "-a"])
    if rc != 0 and stderr.strip():
        warn(f"dpkg repair note: {stderr.strip()[:200]}")
    info("Running apt --fix-broken install -y...")
    run_live(["apt", "--fix-broken", "install", "-y"])
    ok("OS Maintenance package repair complete.")


def ensure_dependencies(auto_install=True):
    """Check Apache2 and BIND9 are installed; auto-install any that are missing."""
    def _installed(pkg):
        r = run(["dpkg", "-s", pkg])
        return r.returncode == 0 and "Status: install ok installed" in r.stdout

    missing = []

    if not _installed("apache2"):
        missing.append("apache2")

    if not _installed("bind9"):
        missing.append("bind9")

    if not _installed("ifupdown"):
        missing.append("ifupdown")

    if not _installed("mariadb-server"):
        missing.append("mariadb-server")

    if not missing:
        return []

    if not auto_install:
        return missing

    print(f"\n{C.YELLOW}  Missing packages: {', '.join(missing)}{C.RESET}")
    print(f"  {C.CYAN}Auto-installing...{C.RESET}\n")

    run_live(["apt-get", "update"])

    rc, stderr = run_live(
        ["apt-get", "install", "-y", "--no-install-recommends"] + missing
    )

    if rc != 0:
        err(f"Install failed:\n{stderr.strip()}")
        return missing

    ok(f"Installed: {', '.join(missing)}")

    if "apache2" in missing:
        ensure_modules()
        r = run(["systemctl", "enable", "--now", "apache2"])
        ok("Apache2 enabled.") if r.returncode == 0 else err(r.stderr.strip())

    if "bind9" in missing:
        r = run(["systemctl", "enable", "--now", "bind9"])
        ok("BIND9 enabled.") if r.returncode == 0 else err(r.stderr.strip())

        # Create an empty named.conf.local if it doesn't exist yet
        if not os.path.isfile(NAMED_CONF_LOCAL):
            if DRY_RUN:
                step(f"[dry-run] Would create {NAMED_CONF_LOCAL}")
            else:
                backup_config(NAMED_CONF_LOCAL)
                if DRY_RUN:
                    step(f"[dry-run] Would write to { NAMED_CONF_LOCAL }")
                else:
                    with open(NAMED_CONF_LOCAL, "w") as f:
                        f.write("// Local zones\n")
                ok("Created empty named.conf.local.")

    print()
    return []


def detect_codename():
    """Extract and return the OS codename (e.g. 'bullseye', 'bookworm', 'trixie')."""
    os_name = get_os_version().lower()
    for codename in ("bullseye", "bookworm", "trixie"):
        if codename in os_name:
            return codename
    return ""

def check_os_compatibility():
    """Return a 3-tier compatibility status for the running OS.

    Returns:
        (status, os_name)  where status is one of:
          'supported'   – Debian 11/12/13 (green)
          'uncertain'   – Debian (other version), Ubuntu, or Raspbian  (yellow)
          'unsupported' – anything else  (red)
    """
    os_name = get_os_version()
    lower = os_name.lower()
    
    if "debian" in lower and any(kw in lower for kw in ("11", "bullseye", "12", "bookworm", "13", "trixie")):
        return "supported", os_name
    elif "ubuntu" in lower:
        return "ubuntu", os_name
    elif any(kw in lower for kw in ("debian", "raspbian", "raspberry")):
        return "uncertain", os_name
    else:
        return "unsupported", os_name


def show_loading_screen():
    """Show initialization screen, verify OS compatibility, and prompt for missing packages."""
    menu_header("System Initialization")

    step("Detecting operating system compatibility...")
    time.sleep(1.5)
    status, os_name = check_os_compatibility()

    if status == "supported":
        print(f"  {C.GREEN}[✔] Compatible OS: {os_name}{C.RESET}")
    elif status == "ubuntu":
        print(f"  {C.YELLOW}[!] Ubuntu detected — experimental/unsupported, proceed at your own risk{C.RESET}")
        if not prompt_confirm("Do you want to proceed anyway?", default="no"):
            _shutdown()
    elif status == "uncertain":
        print(f"  {C.YELLOW}[~] Supported (uncertain): {os_name}{C.RESET}")
        print(f"\n  {C.YELLOW}[!] This OS may work but has not been fully tested.{C.RESET}")
        print("      Some features may behave differently from Debian 11/12/13.\n")
        if not prompt_confirm("Do you want to proceed anyway?", default="yes"):
            _shutdown()
    else:
        err(f"Unsupported OS: {os_name}")
        warn("This server manager is built for Debian 11/12/13.\n      Running it on this OS is likely to cause configuration failures.\n")
        if not prompt_confirm("Do you want to proceed anyway?", default="no"):
            _shutdown()

    print()
    step("Checking core package dependencies...")

    missing = ensure_dependencies(auto_install=False)

    dependencies = {
        "apache2": "Web Server (Apache2)",
        "bind9": "DNS Server (BIND9)",
        "ifupdown": "Network Manager (ifupdown)"
    }

    for pkg, label in dependencies.items():
        time.sleep(0.3)
        if pkg not in missing:
            ok(f"{label}: Installed")
        else:
            warn(f"{label}: Not Found")

    if missing:
        print(f"\n  {C.YELLOW}[!] The following core dependencies are missing: {', '.join(missing)}{C.RESET}")
        if prompt_confirm("Would you like to install them now?", default="yes"):
            ensure_dependencies(auto_install=True)
        else:
            warn("Proceeding without installing missing packages. Note that some features will be unavailable.")

    print()
    input("  Initialization complete. Press Enter to launch the main menu...")


def ensure_mariadb():
    """Install MariaDB if missing and ensure it is running."""
    r = run(["dpkg", "-s", "mariadb-server"])
    if r.returncode == 0 and "Status: install ok installed" in r.stdout:
        return True
    info("MariaDB not found. Installing mariadb-server...")
    fix_dpkg()
    run_live(["apt-get", "update"])
    rc, stderr = run_live(["apt-get", "install", "-y", "--no-install-recommends",
                           "mariadb-server", "mariadb-client"])
    if rc != 0:
        err(f"Failed to install MariaDB: {stderr.strip()}")
        return False
    r2 = run(["systemctl", "enable", "--now", "mariadb"])
    ok("MariaDB installed and started.") if r2.returncode == 0 else err(r2.stderr.strip())
    return r2.returncode == 0


# ─────────────────────────────────────────
#  Make Services menu (Mail Server)
# ─────────────────────────────────────────

# ─────────────────────────────────────────
#  MariaDB Manager
# ─────────────────────────────────────────

def mariadb_run_sql(sql, fetch=False):
    if DRY_RUN:
        step(f"[dry-run] Would run SQL: {sql}")
        return "" if fetch else 0
    cmd = ["mysql", "-u", "root", "-e", sql]
    r = run(cmd)
    if fetch:
        return r.stdout
    else:
        return r.returncode

def mariadb_list_databases():
    res = mariadb_run_sql("SHOW DATABASES;", fetch=True)
    if not res:
        return
    step("Databases:")
    exclude = {"information_schema", "performance_schema", "mysql", "sys"}
    for line in res.splitlines():
        line = line.strip()
        if line and line != "Database" and line not in exclude:
            print(f"    - {line}")

def mariadb_create_database():
    while True:
        name = input("  Database name: ").strip()
        if not name:
            return
        if re.match(r"^[a-zA-Z0-9_]{1,64}$", name):
            break
        err("Invalid database name. Only alphanumeric and underscore, max 64 chars.")
    sql = f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
    if mariadb_run_sql(sql) == 0:
        ok(f"Database '{name}' created.")

def mariadb_drop_database():
    mariadb_list_databases()
    name = input("\n  Database name to drop: ").strip()
    if not name:
        return
    confirm = input(f"  Type '{name}' to confirm drop: ").strip()
    if confirm != name:
        warn("Mismatch. Aborting drop.")
        return
    sql = f"DROP DATABASE IF EXISTS `{name}`;"
    if mariadb_run_sql(sql) == 0:
        ok(f"Database '{name}' dropped.")

def mariadb_list_users():
    sql = "SELECT User, Host FROM mysql.user ORDER BY User;"
    res = mariadb_run_sql(sql, fetch=True)
    if not res:
        return
    step("Users:")
    for line in res.splitlines():
        if line.startswith("User\tHost"):
            continue
        parts = line.split("\t")
        if len(parts) == 2:
            print(f"    - {parts[0]}@{parts[1]}")

def mariadb_create_user():
    user = input("  Username: ").strip()
    if not re.match(r"^[a-zA-Z0-9_]{1,32}$", user):
        err("Invalid username. Only alphanumeric and underscore, max 32 chars.")
        return
    password = input("  Password: ")
    if not password:
        err("Password cannot be blank.")
        return
    sql = f"CREATE USER IF NOT EXISTS '{user}'@'localhost' IDENTIFIED BY '{password}';"
    if mariadb_run_sql(sql) == 0:
        ok(f"User '{user}'@'localhost' created.")

def mariadb_drop_user():
    mariadb_list_users()
    user = input("\n  Username to drop: ").strip()
    if not user:
        return
    confirm = input(f"  Type '{user}' to confirm drop: ").strip()
    if confirm != user:
        warn("Mismatch. Aborting drop.")
        return
    sql = f"DROP USER IF EXISTS '{user}'@'localhost'; FLUSH PRIVILEGES;"
    if mariadb_run_sql(sql) == 0:
        ok(f"User '{user}'@'localhost' dropped.")

def mariadb_grant_privileges():
    user = input("  Username: ").strip()
    if not user:
        return
    db = input("  Database (or * for all): ").strip()
    if not db:
        return
    if db != "*" and not re.match(r"^[a-zA-Z0-9_]+$", db):
        err("Invalid database name.")
        return
        
    print("  Privilege levels:")
    print("  1) ALL")
    print("  2) SELECT, INSERT, UPDATE, DELETE")
    print("  3) SELECT only")
    choice = input("  Choice: ").strip()
    if choice == "1":
        level = "ALL PRIVILEGES"
    elif choice == "2":
        level = "SELECT, INSERT, UPDATE, DELETE"
    elif choice == "3":
        level = "SELECT"
    else:
        warn("Invalid choice.")
        return
        
    sql = f"GRANT {level} ON `{db}`.* TO '{user}'@'localhost'; FLUSH PRIVILEGES;" if db != "*" else f"GRANT {level} ON *.* TO '{user}'@'localhost'; FLUSH PRIVILEGES;"
    if mariadb_run_sql(sql) == 0:
        ok(f"Privileges granted to '{user}'@'localhost'.")

def mariadb_revoke_privileges():
    user = input("  Username: ").strip()
    if not user:
        return
    db = input("  Database (or * for all): ").strip()
    if not db:
        return
    if db != "*" and not re.match(r"^[a-zA-Z0-9_]+$", db):
        err("Invalid database name.")
        return
    sql = f"REVOKE ALL PRIVILEGES ON `{db}`.* FROM '{user}'@'localhost'; FLUSH PRIVILEGES;" if db != "*" else f"REVOKE ALL PRIVILEGES ON *.* FROM '{user}'@'localhost'; FLUSH PRIVILEGES;"
    if mariadb_run_sql(sql) == 0:
        ok(f"Privileges revoked for '{user}'@'localhost'.")

def mariadb_change_password():
    mariadb_list_users()
    user = input("\n  Username: ").strip()
    if not user:
        return
    password = input("  New password: ")
    if not password:
        err("Password cannot be blank.")
        return
    sql = f"ALTER USER '{user}'@'localhost' IDENTIFIED BY '{password}'; FLUSH PRIVILEGES;"
    if mariadb_run_sql(sql) == 0:
        ok(f"Password changed for '{user}'@'localhost'.")

def mariadb_backup():
    mariadb_list_databases()
    name = input("\n  Database name to backup: ").strip()
    if not name:
        return
    if not re.match(r"^[a-zA-Z0-9_]+$", name):
        err("Invalid database name.")
        return
    today = datetime.today().strftime("%Y-%m-%d")
    default_path = f"/root/{name}_{today}.sql"
    path = input(f"  Save to [{default_path}]: ").strip()
    if not path:
        path = default_path
        
    if DRY_RUN:
        step(f"[dry-run] Would run: mysqldump -u root {name} --result-file {path}")
    else:
        step(f"Backing up database '{name}' to {path} ...")
        rc, _ = run_live(["mysqldump", "-u", "root", name, "--result-file", path])
        if rc == 0:
            ok(f"Backup saved to {path}")
        else:
            err("Backup failed.")

def mariadb_restore():
    file = input("  Path to .sql file: ").strip()
    if not os.path.isfile(file):
        err(f"File not found: {file}")
        return
    db = input("  Target database name: ").strip()
    if not re.match(r"^[a-zA-Z0-9_]+$", db):
        err("Invalid database name.")
        return
    warn(f"Target database '{db}' will be overwritten.")
    confirm = input("  Continue? (yes/no): ").strip()
    if confirm != "yes":
        return
        
    if DRY_RUN:
        step(f"[dry-run] Would restore {file} into {db}")
    else:
        step(f"Restoring {file} into database '{db}' ...")
        with open(file, "r") as f:
            r = subprocess.run(["mysql", "-u", "root", db], stdin=f)
            if r.returncode == 0:
                ok(f"Restored {file} into database '{db}'.")
            else:
                err("Restore failed.")


def mariadb_inspect_database():
    res_db = mariadb_run_sql("SHOW DATABASES;", fetch=True)
    if not res_db:
        return
        
    exclude = {"information_schema", "performance_schema", "mysql", "sys"}
    databases = []
    for line in res_db.splitlines():
        line = line.strip()
        if line and line != "Database" and line not in exclude:
            databases.append(line)
            
    if not databases:
        warn("No user databases found.")
        return
        
    while True:
        menu_header("Inspect Database - Select DB")
        for i, d in enumerate(databases, 1):
            print(f"{i}. {d}")
        print("0. Back")
        menu_separator()
        
        choice = input("  Choice: ").strip()
        if not choice:
            continue
        if choice == "0":
            return
            
        if not choice.isdigit() or not (1 <= int(choice) <= len(databases)):
            warn("Invalid choice.")
            continue
            
        db = databases[int(choice) - 1]
        break

    while True:
        res = mariadb_run_sql(f"SHOW TABLES FROM `{db}`;", fetch=True)
        if not res:
            err(f"Database '{db}' does not exist or has no tables.")
            return
            
        tables = []
        for line in res.splitlines():
            if line.startswith(f"Tables_in_{db}"):
                continue
            line = line.strip()
            if line:
                tables.append(line)
                
        if not tables:
            err(f"No tables found in '{db}'.")
            return
            
        menu_header(f"Inspect Database: {db} - Select Table")
        for i, t in enumerate(tables, 1):
            print(f"{i}. {t}")
        print("0. Back")
        menu_separator()
        
        choice = input("  Choice: ").strip()
        if not choice:
            continue
        if choice == "0":
            break
            
        if not choice.isdigit() or not (1 <= int(choice) <= len(tables)):
            warn("Invalid choice.")
            continue
            
        table = tables[int(choice) - 1]
        
        res_desc = mariadb_run_sql(f"DESCRIBE `{db}`.`{table}`;", fetch=True)
        if not res_desc:
            err(f"Table '{table}' does not exist.")
            continue
            
        step(f"Schema for `{db}`.`{table}`:")
        for line in res_desc.splitlines():
            print(f"    {line}")
            
        warn("Selecting all rows from a large table might flood the terminal.")
        confirm = prompt_confirm("Limit output to 50 rows?", default="yes")
        
        limit_clause = " LIMIT 50" if confirm else ""
        res_data = mariadb_run_sql(f"SELECT * FROM `{db}`.`{table}`{limit_clause};", fetch=True)
        
        step(f"Data in `{db}`.`{table}`:")
        if not res_data:
            print("    (Empty set)")
        else:
            for line in res_data.splitlines():
                print(f"    {line}")
        
        input_pause("\n  Press Enter to continue...")

def mariadb_databases_menu():
    while True:
        menu_header("Manage Databases")
        print("1. List Databases")
        print("2. Create Database")
        print("3. Drop Database")
        print("4. Inspect Database")
        print("5. Backup Database")
        print("6. Restore Database")
        print("0. Back")
        menu_separator()
        
        choice = input("  Choice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue
            
        if choice == "1":
            mariadb_list_databases()
        elif choice == "2":
            mariadb_create_database()
        elif choice == "3":
            mariadb_drop_database()
        elif choice == "4":
            mariadb_inspect_database()
        elif choice == "5":
            mariadb_backup()
        elif choice == "6":
            mariadb_restore()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")

def mariadb_users_menu():
    while True:
        menu_header("Manage Users")
        print("1. List Users")
        print("2. Create User")
        print("3. Drop User")
        print("4. Change User Password")
        print("5. Grant Privileges")
        print("6. Revoke Privileges")
        print("0. Back")
        menu_separator()
        
        choice = input("  Choice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue
            
        if choice == "1":
            mariadb_list_users()
        elif choice == "2":
            mariadb_create_user()
        elif choice == "3":
            mariadb_drop_user()
        elif choice == "4":
            mariadb_change_password()
        elif choice == "5":
            mariadb_grant_privileges()
        elif choice == "6":
            mariadb_revoke_privileges()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")

def mariadb_menu():
    while True:
        menu_header("Manage MariaDB")
        print("1. Manage Databases")
        print("2. Manage Users")
        print("0. Back")
        menu_separator()
        
        choice = input("  Choice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue
            
        if choice == "1":
            mariadb_databases_menu()
        elif choice == "2":
            mariadb_users_menu()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")

# ─────────────────────────────────────────
#  Front door: nginx (:80) → Apache (:8080)   — used by webmail and phpMyAdmin
# ─────────────────────────────────────────
# configure_apache_ports() moves Apache to 8080 and nginx owns port 80. Anything Apache
# serves therefore needs (a) an Apache vhost on *:8080 and (b) an nginx server block that
# proxies to it. Installers that skip either one are unreachable on port 80.

FRONT_DOOR_MARK = "# zervermanager: front-door proxy"
PMA_SNIPPET     = "/etc/nginx/snippets/zm-phpmyadmin.conf"
PMA_INCLUDE     = "include snippets/zm-phpmyadmin.conf;  # zervermanager"


def apache_on_backend_port():
    """True once Apache has been moved to 8080 (so nginx owns port 80)."""
    try:
        with open("/etc/apache2/ports.conf", "r") as f:
            return bool(re.search(r"^\s*Listen\s+(?:\S+:)?8080\b", f.read(), re.MULTILINE))
    except OSError:
        return False


def nginx_front_door_vhost(domain, tag=""):
    label = f" ({tag})" if tag else ""
    return f"""{FRONT_DOOR_MARK}{label}
server {{
    listen 80;
    server_name {domain};
    client_max_body_size 64m;

    location / {{
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
}}
"""


def _nginx_default_server_file():
    """Config file holding nginx's catch-all (default_server) block, or None."""
    listen_re = re.compile(r"^\s*listen\s+[^;\n]*\bdefault_server\b", re.MULTILINE)
    candidates = [os.path.realpath(f"{NGINX_SITES_ENABLED}/default")]
    r = run(["/usr/sbin/nginx", "-T"])                      # where else is default_server declared?
    if r.returncode == 0:
        current = None
        for line in r.stdout.splitlines():
            m = re.match(r"# configuration file (.+):$", line)
            if m:
                current = m.group(1)
            elif current and re.match(r"^\s*listen\s+[^;\n]*\bdefault_server\b", line):
                candidates.append(os.path.realpath(current))
    for path in candidates:
        if path.startswith("/etc/nginx/") and os.path.isfile(path):
            try:
                with open(path, "r") as f:
                    if listen_re.search(f.read()):
                        return path
            except OSError:
                pass
    return None


def expose_phpmyadmin_via_nginx():
    """Make http://<ip>/phpmyadmin work when nginx owns :80 and Apache sits on :8080.
    Returns True if phpMyAdmin is reachable on port 80."""
    if not apache_on_backend_port():
        return True                         # Apache itself serves :80 — its own phpmyadmin.conf is enough
    if not os.path.isfile("/usr/sbin/nginx"):
        return False
    target = _nginx_default_server_file()
    if not target:
        warn("Could not find nginx's default server block, so /phpmyadmin is not routed on port 80.")
        return False
    if DRY_RUN:
        step(f"[dry-run] Would route /phpmyadmin through nginx ({target})")
        return True

    snippet = """# zervermanager: phpMyAdmin is served by Apache (127.0.0.1:8080); nginx hands /phpmyadmin over to it
location ^~ /phpmyadmin {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 128m;
    proxy_read_timeout 300s;
}
"""
    os.makedirs(os.path.dirname(PMA_SNIPPET), exist_ok=True)
    with open(PMA_SNIPPET, "w") as f:
        f.write(snippet)

    with open(target, "r") as f:
        original = f.read()
    if "zm-phpmyadmin.conf" not in original:
        updated, n = re.subn(
            r"^([ \t]*)server_name[ \t]+_[ \t]*;[^\n]*$",
            lambda m: m.group(0) + "\n" + m.group(1) + PMA_INCLUDE,
            original, count=1, flags=re.MULTILINE)
        if n == 0:
            updated, n = re.subn(
                r"^([ \t]*)(listen[ \t]+[^;\n]*\bdefault_server\b[^;\n]*;[^\n]*)$",
                lambda m: m.group(0) + "\n" + m.group(1) + PMA_INCLUDE,
                original, count=1, flags=re.MULTILINE)
        if n == 0:
            os.remove(PMA_SNIPPET)
            warn(f"Could not find where to add /phpmyadmin in {target}.")
            return False
        backup_config(target)
        with open(target, "w") as f:
            f.write(updated)

    ok_n, msg_n = validate_nginx()
    if not ok_n:                            # never leave nginx broken: roll back
        with open(target, "w") as f:
            f.write(original)
        try:
            os.remove(PMA_SNIPPET)
        except OSError:
            pass
        err(f"nginx rejected the phpMyAdmin route, change rolled back:\n{msg_n.strip()}")
        return False
    mark_reload("nginx")
    ok("nginx now routes /phpmyadmin to Apache.")
    return True


def unexpose_phpmyadmin_nginx():
    """Undo expose_phpmyadmin_via_nginx() (include line first, then the snippet)."""
    changed = False
    paths = (glob.glob(f"{NGINX_SITES_AVAILABLE}/*") + glob.glob("/etc/nginx/conf.d/*.conf")
             + ["/etc/nginx/nginx.conf"])
    for p in paths:
        try:
            if not os.path.isfile(p):
                continue
            with open(p, "r") as f:
                txt = f.read()
        except OSError:
            continue
        if "zm-phpmyadmin.conf" not in txt:
            continue
        with open(p, "w") as f:
            f.write("".join(l for l in txt.splitlines(True) if "zm-phpmyadmin.conf" not in l))
        changed = True
    if os.path.exists(PMA_SNIPPET):
        os.remove(PMA_SNIPPET)
        changed = True
    if changed:
        ok_n, msg_n = validate_nginx()
        if ok_n:
            mark_reload("nginx")
            ok("Removed the phpMyAdmin route from nginx.")
        else:
            err(f"nginx config problem after removing the phpMyAdmin route:\n{msg_n.strip()}")


def _remove_webmail_vhosts():
    """Remove the nginx front door + Apache vhost that setup_mail_server() created."""
    removed = False
    for path in glob.glob(f"{NGINX_SITES_AVAILABLE}/*.conf"):
        try:
            with open(path, "r") as f:
                first = (f.readline() or "")
        except OSError:
            continue
        if FRONT_DOOR_MARK in first and "(webmail)" in first:
            nginx_disable_site(os.path.basename(path)[:-5])
            os.remove(path)
            mark_reload("nginx")
            removed = True
    for path in glob.glob(f"{APACHE_SITES_AVAILABLE}/mail.*.conf"):
        try:
            with open(path, "r") as f:
                first = (f.readline() or "")
        except OSError:
            continue
        if "zervermanager: webmail" in first:
            run(["/usr/sbin/a2dissite", os.path.basename(path)])
            os.remove(path)
            mark_reload("apache2")
            removed = True
    if removed:
        ok("Removed the webmail virtual hosts (nginx + Apache).")


def ensure_mail_dns(domain, ip, webmail_domain):
    """Make sure `domain` can receive mail: an A record for the mail host and an MX record
    pointing at it. Creates the forward/reverse zones when missing; if the zone already
    exists, edits the file named.conf.local actually points to and never duplicates a
    record. Returns (forward_zone_file, reverse_zone_file)."""
    mail_host = webmail_domain.rstrip(".") + "."
    rev_zone  = reverse_zone_name(ip)
    rev_file  = reverse_zone_file(ip)
    a_line    = f"mail    IN  A       {ip}\n"
    mx_line   = f"@       IN  MX  10  {mail_host}\n"

    fwd_file = None
    if zone_exists(domain):
        # use the file the zone is really registered with (not a guessed name)
        for cand in (get_zone_file(domain), f"{BIND_DIR}/db.{domain}"):
            if cand and os.path.isfile(cand):
                fwd_file = cand
                break
        if not fwd_file:
            err(f"Zone {domain} is registered but its zone file was not found. "
                f"Add the MX record manually (DNS Zones → MX Records).")
        else:
            with open(fwd_file, "r") as f:
                original = f.read()
            mx_targets = re.findall(
                r"^[ \t]*(?!;)\S+[ \t]+(?:\d+[ \t]+)?IN[ \t]+MX[ \t]+\d+[ \t]+(\S+)",
                original, re.MULTILINE | re.IGNORECASE)

            def _abs(t):
                t = t.lower()
                return t if t.endswith(".") else f"{t}.{domain.lower()}."

            has_mx_to_us = any(_abs(t) == mail_host.lower() for t in mx_targets)
            has_mail_a = re.search(
                rf"^[ \t]*mail(?:\.{re.escape(domain)}\.)?[ \t]+(?:\d+[ \t]+)?IN[ \t]+A\b",
                original, re.MULTILINE | re.IGNORECASE)
            additions = ""
            if not has_mail_a:
                additions += a_line
            if not mx_targets:
                additions += mx_line
            elif not has_mx_to_us:
                warn(f"{domain} already has an MX record pointing at {', '.join(mx_targets)} — "
                     f"left unchanged. Edit it under DNS Zones → MX Records if mail should come here.")
            if not additions:
                ok(f"{domain} already has an MX record for {mail_host.rstrip('.')}.")
            elif DRY_RUN:
                step(f"[dry-run] Would add to {fwd_file}:\n{additions}")
            else:
                updated = update_serial(original)
                if not updated.endswith("\n"):
                    updated += "\n"
                updated += "\n; Mail server records (zervermanager)\n" + additions
                backup_config(fwd_file)
                with open(fwd_file, "w") as f:
                    f.write(updated)
                ok_z, msg_z = validate_zone(domain, fwd_file)
                if not ok_z:
                    with open(fwd_file, "w") as f:      # never leave a broken zone behind
                        f.write(original)
                    err(f"Zone error, change rolled back:\n{msg_z}")
                else:
                    ok(f"MX record added: {domain} → {mail_host.rstrip('.')} (priority 10)")
                    mark_reload("bind9")
    else:
        fwd_file = f"{BIND_DIR}/db.{domain}"
        step("Writing forward zone...")
        write_forward_zone(domain, ip, fwd_file)
        if DRY_RUN:
            step(f"[dry-run] Would append mail records to {fwd_file}")
        else:
            with open(fwd_file, "a") as f:
                f.write("\n; Mail server records\n" + a_line + mx_line)
            ok_z, msg_z = validate_zone(domain, fwd_file)
            if not ok_z:
                os.remove(fwd_file)
                err(f"Zone error:\n{msg_z}")
                fwd_file = None
            else:
                add_zone_to_conf(domain, fwd_file)
                ok(f"Forward zone created with MX record ({domain} → {mail_host.rstrip('.')}).")

    # Reverse zone
    if zone_exists(rev_zone):
        warn(f"Reverse zone {rev_zone} already exists, skipping.")
    else:
        step("Writing reverse zone...")
        write_reverse_zone(domain, ip, rev_file)
        ok_r, msg_r = validate_zone(rev_zone, rev_file)
        if not ok_r:
            os.remove(rev_file)
            err(f"Reverse zone error:\n{msg_r}")
        else:
            add_zone_to_conf(rev_zone, rev_file)
            ok("Reverse zone created.")

    ok_b, msg_b = validate_bind9()
    if ok_b:
        reload_bind9()
    else:
        err(f"BIND9 error:\n{msg_b}")
    return fwd_file, rev_file


def setup_mail_server():
    """Installs and configures Postfix, Dovecot, Roundcube (+ nginx front door and DNS/MX).

    Deferred service reloads are applied no matter how the install ends: it used to
    `return` early (e.g. when Dovecot failed to restart) and silently skip them."""
    try:
        _setup_mail_server_impl()
    finally:
        apply_reloads()


def _setup_mail_server_impl():
    print("-" * 35)

    domain = ask_domain("  Mail Domain (e.g., example.com): ")
    if not domain:
        warn("Domain is required.")
        return
        
    webmail_domain = f"mail.{domain}"
    print(f"Webmail will be available at: http://{webmail_domain}")

    # Auto‑detect server IP; allow user override
    ip = prompt_for_ip("Enter server IP")
    if not ip:
        err("Unable to determine server IP. Please provide one.")
        return
    create_dns = True   # DNS zones are always created

    print(f"\n  {C.CYAN}Installing dependencies (this may take a while)...{C.RESET}")
    ensure_dependencies()

    run_live(["apt-get", "update"])
    run_live(["apt-get", "install", "-y", "--no-install-recommends", "debconf-utils"])

    # Set up debconf selections for postfix and roundcube to prevent interactive prompts
    debconf_selections = f"""postfix postfix/main_mailer_type select Internet Site
postfix postfix/mailname string {domain}
roundcube-core roundcube/dbconfig-install boolean true
roundcube-core roundcube/reconfigure-webserver multiselect apache2
roundcube-core roundcube/database-type select sqlite3
"""
    process = subprocess.Popen(["debconf-set-selections"], stdin=subprocess.PIPE, text=True)
    process.communicate(debconf_selections)

    packages = [
        "postfix", "dovecot-core", "dovecot-imapd", "dovecot-pop3d",
        "roundcube", "roundcube-sqlite3", "roundcube-plugins"
    ]

    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"

    rc, stderr = run_live(
        ["apt-get", "install", "-y", "--no-install-recommends"] + packages
    )

    if rc != 0:
        err(f"Install failed:\n{stderr.strip()}")
        return

    ok(f"Installed Mail Server packages for {domain}.")
    # Enable required Apache modules and Roundcube config
    step("Enabling Apache modules and Roundcube configuration...")
    run(["/usr/sbin/a2enmod", "rewrite"])
    run(["/usr/sbin/a2enconf", "roundcube"])
    run(["systemctl", "enable", "--now", "apache2"])  # start+enable if not yet active
    mark_reload("apache2")
    
    # Install PHP extensions needed by Roundcube
    # build_php_packages() emits versioned names on Ubuntu (e.g. php8.5-imap),
    # and passes through unchanged generic names on Debian 12/11/13.
    php_packages = build_php_packages([
        "php", "php-imap", "php-mbstring", "php-xml",
        "php-gd", "php-intl"
    ])
    rc, err_msg = run_live(["apt-get", "install", "-y", "--no-install-recommends"] + php_packages)
    if rc != 0:
        err(f"Failed to install PHP extensions for Roundcube:\n{err_msg}")
    else:
        ok("PHP extensions for Roundcube installed.")
    
    # Ubuntu 26.04 + PHP 8.4+ introduces native array_first which conflicts with Roundcube 1.6's bootstrap.php
    # We must patch it to prevent a 500 Internal Server Error
    step("Applying PHP 8.4+ compatibility patches to Roundcube...")
    bootstrap_file = "/usr/share/roundcube/program/lib/Roundcube/bootstrap.php"
    if not DRY_RUN and os.path.exists(bootstrap_file):
        with open(bootstrap_file, "r") as f:
            lines = f.readlines()
        
        out = []
        in_func = False
        brace_count = 0
        patched = False
        for line in lines:
            if "function array_first(" in line and "function_exists" not in ''.join(out[-2:]):
                out.append("if (!function_exists('array_first')) {\n")
                out.append(line)
                in_func = True
                brace_count = line.count("{") - line.count("}")
                patched = True
                continue
            
            if in_func:
                out.append(line)
                brace_count += line.count("{") - line.count("}")
                if brace_count == 0 and "}" in line:
                    out.append("}\n")
                    in_func = False
            else:
                out.append(line)
                
        if patched:
            with open(bootstrap_file, "w") as f:
                f.writelines(out)
            ok("Roundcube patched for PHP 8.4+ compatibility.")
    
    # Debian's roundcube package automatically sets correct ownership for temp/logs directories.
    # Do NOT run chown -R on public_html, as it will break symlinks to /usr/share and cause 403 Forbidden errors!
    
    # Create Apache VirtualHost for Roundcube.
    # Apache lives on 8080 behind nginx (see configure_apache_ports): a *:80 vhost would never match.
    if not ensure_nginx():
        return
    configure_apache_ports()
    docroot = "/var/lib/roundcube/public_html"
    vhost_content = f"""# zervermanager: webmail
<VirtualHost *:8080>
    ServerName {webmail_domain}
    DocumentRoot "{docroot}"

    <Directory "{docroot}">
        Options +Indexes +FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>
    
    <Directory "/usr/share/roundcube">
        Options +FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>
    
    # Roundcube directories protection
    <Directory "{docroot}/config">
        Require all denied
    </Directory>
    <Directory "{docroot}/temp">
        Require all denied
    </Directory>
    <Directory "{docroot}/logs">
        Require all denied
    </Directory>
</VirtualHost>
"""
    
    vhost_path = f"/etc/apache2/sites-available/{webmail_domain}.conf"
    backup_config(vhost_path)
    if DRY_RUN:
        step(f"[dry-run] Would write to { vhost_path }")
    else:
        with open(vhost_path, "w") as f:
            f.write(vhost_content)
        
    run(["/usr/sbin/a2ensite", f"{webmail_domain}.conf"])
    if not DRY_RUN:
        ok_a, msg_a = validate_apache()
        if not ok_a:
            run(["/usr/sbin/a2dissite", f"{webmail_domain}.conf"])
            try:
                os.remove(vhost_path)
            except OSError:
                pass
            err(f"Apache config error:\n{msg_a}")
            return
    mark_reload("apache2")  # deferred — applied by setup_mail_server()
    ok(f"Apache VirtualHost created for {webmail_domain}")

    # nginx front door: owns port 80 and hands webmail over to Apache (Host header preserved)
    step("Configuring Nginx front-door proxy for webmail...")
    nginx_path = write_nginx_vhost(webmail_domain, nginx_front_door_vhost(webmail_domain, tag="webmail"))
    nginx_enable_site(webmail_domain)
    if not DRY_RUN:
        ok_n, msg_n = validate_nginx()
        if not ok_n:
            nginx_disable_site(webmail_domain)
            try:
                os.remove(nginx_path)
            except OSError:
                pass
            err(f"Nginx config error:\n{msg_n}")
            return
    reload_nginx()
    ok(f"Nginx proxy configured for {webmail_domain}")

    # DNS first (A + MX): it must not depend on the Postfix/Dovecot steps below succeeding
    fwd_file = rev_file = None
    if create_dns:
        step("Setting up DNS (A + MX records)...")
        fwd_file, rev_file = ensure_mail_dns(domain, ip, webmail_domain)

    # Configure Postfix and Dovecot for Maildir format
    step("Configuring Postfix and Dovecot for Maildir...")
    run(["/usr/sbin/postconf", "-e", "home_mailbox = Maildir/"])
    configure_postfix_domain(domain)
    os_status, _ = check_os_compatibility()
    write_dovecot_local_conf(os_status)

    # Roundcube is plain config text, so write it BEFORE restarting any service. It used to come
    # after the Dovecot restart, and a failed restart did a bare `return` that skipped it: webmail
    # kept the old SMTP port and the "tohi@127.0.0.1" sender, whatever else had been fixed.
    step("Configuring Roundcube specific settings...")
    write_roundcube_settings(domain)

    dovecot_ok = restart_mail_services(os_status)
    check_mail_services(domain)
    print()
    if not dovecot_ok:
        warn("Mail setup finished with errors. Fix the Dovecot problem above, then click \"Repair mail\".")
        return

    # Summary block similar to Apache site creation
    menu_separator()
    print(f"  {C.BOLD}Mail Domain:{C.RESET}  {domain}")
    print(f"  {C.BOLD}Webmail URL:{C.RESET}  http://{webmail_domain}")
    print(f"  {C.BOLD}Docroot:{C.RESET}      {docroot}")
    print(f"  {C.BOLD}Apache conf:{C.RESET}  {vhost_path}")
    print(f"  {C.BOLD}Nginx conf:{C.RESET}   {nginx_path}")
    if create_dns:
        print(f"  {C.BOLD}Forward zone:{C.RESET} {fwd_file}")
        print(f"  {C.BOLD}Reverse zone:{C.RESET} {rev_file}")
        print(f"  {C.BOLD}MX record:{C.RESET}    {domain} → {webmail_domain} (priority 10)")
    else:
        print(f"  {C.BOLD}DNS:{C.RESET}          {C.DIM}not created{C.RESET}")
        print(f"  {C.YELLOW}Note: Don't forget to create DNS A/CNAME records for {webmail_domain} and MX records for {domain}!{C.RESET}")
    menu_separator()
    print()

def add_mail_user():
    menu_header("Add Mail User")
    
    # Ensure mailuser group exists
    run(["/usr/sbin/groupadd", "-f", "mailuser"])

    username = input("  Enter new mail username (e.g., admin): ").strip()
    if not username:
        return

    # Check if user already exists
    res = run(["/usr/bin/id", username])
    if res.returncode == 0:
        err(f"User {username} already exists.")
        return

    step(f"Creating user {username}...")
    res = run(["/usr/sbin/useradd", "-m", "-s", "/usr/sbin/nologin", "-G", "mailuser", username])
    if res.returncode != 0:
        err(f"Failed to create user:\n{res.stderr.strip()}")
        return

    step("Set password for the new mail user:")
    # Run passwd interactively in terminal. Since run_live suppresses input, we use subprocess directly to allow TTY
    try:
        subprocess.run(["/usr/bin/passwd", username])
        ok(f"Mail user {username} created successfully.")
    except Exception as e:
        err(f"Error setting password: {e}")

# ─────────────────────────────────────────
#  Mail: domain handling, diagnostics, accounts
# ─────────────────────────────────────────
MAIL_GROUP   = "mailuser"
MAIL_USER_RE = re.compile(r"^[a-z_](?:[a-z0-9_.-]{0,30}[a-z0-9_])?$")
ROUNDCUBE_DB = "/var/lib/dbconfig-common/sqlite3/roundcube/roundcube"


def configure_postfix_domain(domain):
    """Tell Postfix that `domain` is a LOCAL domain.

    If it isn't in mydestination, mail to user@domain is treated as remote: Postfix looks the
    domain up in DNS (using the system resolver, which usually isn't this server's BIND) or
    bounces it with "mail for <domain> loops back to myself". That is exactly what happens when
    Postfix was installed earlier under a different name — debconf preseeding is then ignored."""
    if DRY_RUN:
        step(f"[dry-run] Would add {domain} to Postfix mydestination")
        return
    try:
        with open("/etc/mailname", "w") as f:
            f.write(domain + "\n")
    except OSError:
        pass
    current = run(["/usr/sbin/postconf", "-h", "mydestination"]).stdout.strip()
    items = [x for x in re.split(r"[,\s]+", current) if x]
    if not items:
        items = ["$myhostname", "localhost.localdomain", "localhost"]
    if domain.lower() not in (i.lower() for i in items):
        items.insert(1, domain)
        run(["/usr/sbin/postconf", "-e", "mydestination = " + ", ".join(items)])
        ok(f"Postfix now accepts mail for @{domain}.")
    run(["/usr/sbin/postconf", "-e", "inet_interfaces = all"])


def _roundcube_db():
    """Which database Roundcube uses, from Debian's dbconfig file (default: the sqlite file)."""
    info = {"type": "sqlite3", "path": ROUNDCUBE_DB}
    try:
        with open("/etc/roundcube/debian-db.php", "r") as f:
            txt = f.read()
        def grab(name):
            m = re.search(r"\$" + name + r"\s*=\s*['\"]([^'\"]*)['\"]", txt)
            return m.group(1) if m else ""
        dbtype = grab("dbtype")
        if dbtype in ("mysql", "pgsql"):
            info = {"type": dbtype, "name": grab("dbname"), "user": grab("dbuser"),
                    "pass": grab("dbpass"), "server": grab("dbserver") or "localhost"}
        elif grab("basepath") and grab("dbname"):
            info["path"] = os.path.join(grab("basepath"), grab("dbname"))
    except OSError:
        pass
    return info


def _rc_identities(domain, fix=False):
    """Count (fix=False) or repair (fix=True) Roundcube identities that carry the IMAP host
    (127.0.0.1 / localhost) instead of the mail domain. Returns the number of rows."""
    if not re.fullmatch(r"[a-z0-9.-]+", domain or ""):
        raise RuntimeError("unexpected characters in the mail domain")
    db, total = _roundcube_db(), 0
    for bad in ("127.0.0.1", "localhost"):
        if db["type"] in ("sqlite3", "sqlite"):
            import sqlite3
            if not os.path.isfile(db["path"]):
                raise RuntimeError("Roundcube database not found")
            con = sqlite3.connect(db["path"], timeout=10)
            try:
                if fix:
                    cur = con.execute(
                        "UPDATE identities SET email = substr(email, 1, length(email) - ?) || ? WHERE email LIKE ?",
                        (len(bad) + 1, "@" + domain, "%@" + bad))
                    con.commit()
                    total += cur.rowcount
                else:
                    total += con.execute("SELECT COUNT(*) FROM identities WHERE email LIKE ?", ("%@" + bad,)).fetchone()[0]
            finally:
                con.close()
        elif db["type"] == "mysql":
            binary = shutil.which("mysql") or shutil.which("mariadb")
            if not binary:
                raise RuntimeError("the mysql client is not installed")
            like = f"%@{bad}"
            sql = (f"UPDATE identities SET email = CONCAT(SUBSTRING(email, 1, CHAR_LENGTH(email) - {len(bad) + 1}), '@{domain}') "
                   f"WHERE email LIKE '{like}'; SELECT ROW_COUNT();") if fix else \
                  f"SELECT COUNT(*) FROM identities WHERE email LIKE '{like}';"
            r = subprocess.run([binary, "-h", db["server"], "-u", db["user"], "-N", db["name"], "-e", sql],
                               capture_output=True, text=True, timeout=20,
                               env={**os.environ, "MYSQL_PWD": db["pass"]})
            if r.returncode != 0:
                raise RuntimeError((r.stderr or "mysql failed").strip())
            nums = re.findall(r"^\d+$", r.stdout, re.MULTILINE)
            total += int(nums[-1]) if nums else 0
        else:
            raise RuntimeError(f"unsupported Roundcube database type '{db['type']}'")
    return total


def fix_roundcube_identities(domain):
    """Roundcube stores each user's From address the first time they log in. Anything created
    while mail_domain was unset is stored as user@127.0.0.1 — repair those."""
    if DRY_RUN:
        return 0
    try:
        changed = _rc_identities(domain, fix=True)
        if changed:
            ok(f"Fixed {changed} Roundcube sender address(es) → @{domain}.")
        return changed
    except Exception as e:
        warn(f"Could not update existing Roundcube sender addresses ({e}). "
             f"Fix them under Roundcube → Settings → Identities.")
        return 0


def check_mail_services(domain):
    """Quick sanity checks so a broken mail setup is reported at install time, not at first send."""
    if DRY_RUN:
        return
    dest = run(["/usr/sbin/postconf", "-h", "mydestination"]).stdout
    if domain.lower() in dest.lower():
        ok(f"Postfix delivers @{domain} locally.")
    else:
        warn(f"@{domain} is not in Postfix mydestination — mail to it will not be delivered locally.")
    try:
        import smtplib
        with smtplib.SMTP("127.0.0.1", 25, timeout=5) as smtp:
            smtp.ehlo()
        ok("SMTP is accepting connections on 127.0.0.1:25 (what webmail uses to send).")
    except Exception as e:
        warn(f"Could not connect to SMTP on 127.0.0.1:25: {e}")


ROUNDCUBE_CONF = "/etc/roundcube/config.inc.php"
RC_MARKER      = "// Custom settings added by zervermanager"
DOVECOT_LOCAL  = "/etc/dovecot/conf.d/99-local-mail.conf"


def write_dovecot_local_conf(os_status, full_address_login=True):
    backup_config(DOVECOT_LOCAL)
    if DRY_RUN:
        step(f"[dry-run] Would write to {DOVECOT_LOCAL}")
        return
    with open(DOVECOT_LOCAL, "w") as f:
        if os_status == "ubuntu":
            # Dovecot 2.4 syntax
            f.write("mail_driver = maildir\n")
            f.write("mail_path = ~/Maildir\n")
            f.write("mailbox_list_layout = fs\n")
        else:
            # Dovecot 2.3 syntax (Debian)
            f.write("mail_location = maildir:~/Maildir\n")
            if full_address_login:
                # sign in with tohi@example.com as well as plain "tohi"
                f.write("auth_username_format = %Ln\n")
        # auth_username_format is invalid at global scope in Dovecot 2.4 (Ubuntu) and crashes it,
        # so it is only ever written for the 2.3 branch above.
        f.write("disable_plaintext_auth = no\n")
        f.write("ssl = no\n")


def write_roundcube_settings(domain):
    """Point Roundcube at the local Postfix/Dovecot and give it the mail domain."""
    if not os.path.isfile(ROUNDCUBE_CONF):
        warn(f"{ROUNDCUBE_CONF} not found — Roundcube settings were not written.")
        return False
    backup_config(ROUNDCUBE_CONF)
    if DRY_RUN:
        step(f"[dry-run] Would write custom block to {ROUNDCUBE_CONF}")
        return True
    with open(ROUNDCUBE_CONF, "r") as f:
        existing = f.read()
    if RC_MARKER in existing:                       # idempotent: replace our previous block
        existing = existing[:existing.index(RC_MARKER)]
    with open(ROUNDCUBE_CONF, "w") as f:
        f.write(existing.rstrip() + "\n\n")
        f.write(f"{RC_MARKER}\n")
        # imap_host without a port = plain IMAP on 143. Do NOT also set default_host (conflicts in RC 1.6).
        f.write("$config['imap_host'] = '127.0.0.1';\n")
        # Roundcube 1.6 IGNORES smtp_port: the port must be part of smtp_host, and a bare host
        # defaults to 587 (which Postfix doesn't serve → "SMTP Error: Connection to server failed").
        # smtp_server is the legacy name of the same option; Debian's stock config may still define
        # it, and Roundcube lets the legacy name win — so set both.
        f.write("$config['smtp_host'] = '127.0.0.1:25';\n")
        f.write("$config['smtp_server'] = '127.0.0.1:25';\n")
        f.write("$config['smtp_user'] = '';\n")
        f.write("$config['smtp_pass'] = '';\n")
        # Without mail_domain Roundcube builds addresses from the IMAP host: "tohi@127.0.0.1"
        f.write(f"$config['mail_domain'] = '{domain}';\n")
        f.write("$config['create_default_folders'] = true;\n")
    ok("Roundcube configured for local SMTP and IMAP.")
    fix_roundcube_identities(domain)
    return True


def restart_mail_services(os_status):
    """Restart Postfix + Dovecot. If Dovecot rejects the optional full-address-login line,
    retry without it, so that line can never leave Dovecot (and therefore webmail) down."""
    run(["systemctl", "restart", "postfix"])
    res = run(["systemctl", "restart", "dovecot"])
    if res.returncode != 0 and os_status != "ubuntu" and not DRY_RUN:
        warn("Dovecot rejected the new configuration — retrying without full-address login...")
        write_dovecot_local_conf(os_status, full_address_login=False)
        res = run(["systemctl", "restart", "dovecot"])
        if res.returncode == 0:
            warn("Dovecot is running, but webmail login needs the plain username (e.g. 'tohi').")
    if res.returncode != 0:
        journal = run(["journalctl", "-xeu", "dovecot.service", "--no-pager", "-n", "40"])
        err(f"Dovecot failed to restart. Journal output:\n{journal.stdout.strip()}")
        return False
    if ":143" not in run(["ss", "-tlnp"]).stdout:
        warn("Dovecot does not appear to be listening on port 143. "
             "Check 'systemctl status dovecot' and /var/log/mail.log for errors.")
    else:
        ok("Postfix and Dovecot services restarted and verified (port 143 active).")
    return True


def _rc_last(txt, key):
    vals = re.findall(r"\$config\[\s*['\"]" + key + r"['\"]\s*\]\s*=\s*['\"]([^'\"]*)['\"]", txt)
    return vals[-1] if vals else None


def diagnose_mail():
    """Check every link of the chain  webmail → SMTP → Postfix → mailbox  and say what is wrong.
    Read-only: it opens an SMTP conversation but never sends a message. Returns #problems."""
    problems = 0

    def bad(msg, fix=""):
        nonlocal problems
        problems += 1
        err(msg)
        if fix:
            print(f"      → {fix}")

    print(f"\n  {C.BOLD}Mail check{C.RESET}\n")
    if not mail_server_installed():
        bad("The mail server is not installed.", "Install it first (Install Mail Server).")
        return problems
    domain = get_mail_domain()
    webmail = f"mail.{domain}"
    if domain and "." in domain:
        ok(f"Mail domain: {domain}")
    else:
        bad(f"No usable mail domain (/etc/mailname says '{domain}').", "Run Install Mail Server once with your domain.")

    # Postfix: running? listening? (Debian's postfix.service can say "active" while the real instance is down)
    if run(["/usr/sbin/postfix", "status"]).returncode == 0:
        ok("Postfix is running.")
    else:
        bad("Postfix is NOT running.", "Run 'postfix check' and 'systemctl status postfix@-' on the server to see why.")
    listen = run(["ss", "-H", "-ltn", "sport = :25"]).stdout
    addrs = re.findall(r"(\S+):25\b", listen)
    if addrs:
        ok(f"Something is listening on port 25 ({', '.join(sorted(set(addrs)))}).")
    else:
        bad("Nothing is listening on port 25 — webmail shows \"SMTP Error: Connection to server failed\".",
            "Click Repair mail, or check 'postfix check' for configuration errors.")

    # A real SMTP conversation up to RCPT TO — exactly what webmail does, minus the message
    try:
        import smtplib
        users = get_mail_users()
        rcpt = f"{users[0]}@{domain}" if users else f"postmaster@{domain}"
        with smtplib.SMTP("127.0.0.1", 25, timeout=8) as smtp:
            smtp.ehlo()
            smtp.mail(f"check@{domain}")
            code, msg = smtp.rcpt(rcpt)
            smtp.rset()
        if code in (250, 251):
            ok(f"Postfix accepts mail for {rcpt}.")
        else:
            text = msg.decode("utf-8", "replace") if isinstance(msg, bytes) else str(msg)
            hint = ("The domain is not in Postfix's local domains — click Repair mail." if code == 554
                    else "That mailbox does not exist." if code == 550 else "")
            bad(f"Postfix refused {rcpt}: {code} {text}", hint)
    except Exception as e:
        bad(f"Cannot talk to SMTP on 127.0.0.1:25 ({e}).", "This is what webmail hits when sending fails.")

    dest = run(["/usr/sbin/postconf", "-h", "mydestination"]).stdout
    if domain and domain.lower() in dest.lower():
        ok(f"Postfix treats @{domain} as a local domain.")
    else:
        bad(f"@{domain} is not in Postfix's local domains (mydestination: {dest.strip() or 'empty'}).", "Click Repair mail.")

    # Dovecot
    if run(["systemctl", "is-active", "dovecot"]).returncode == 0 and ":143" in run(["ss", "-tlnp"]).stdout:
        ok("Dovecot is running (IMAP port 143).")
    else:
        bad("Dovecot is not running or not listening on IMAP port 143.", "Check 'systemctl status dovecot'.")

    # Roundcube settings as Roundcube will actually read them (last assignment wins; legacy name wins)
    try:
        with open(ROUNDCUBE_CONF, "r") as f:
            rc = f.read()
        legacy, new = _rc_last(rc, "smtp_server"), _rc_last(rc, "smtp_host")
        eff = legacy if legacy is not None else new
        m = re.search(r":(\d+)$", eff or "")
        if eff and m and m.group(1) == "25":
            ok(f"Roundcube sends through {eff}.")
        elif eff and not m:
            bad(f"Roundcube's SMTP host is '{eff}' with no port, so it tries port 587 (nothing listens there).", "Click Repair mail.")
        else:
            bad(f"Roundcube's SMTP host is '{eff or 'empty'}', expected 127.0.0.1:25.", "Click Repair mail.")
        md = _rc_last(rc, "mail_domain")
        if md and md.lower() == (domain or "").lower():
            ok(f"Roundcube builds addresses as user@{md}.")
        else:
            bad(f"Roundcube mail_domain is '{md or 'not set'}' — addresses come out as user@127.0.0.1.", "Click Repair mail.")
    except OSError:
        bad(f"Cannot read {ROUNDCUBE_CONF}.", "Is Roundcube installed?")
    try:
        n = _rc_identities(domain, fix=False)
        if n:
            bad(f"{n} Roundcube sender address(es) still show 127.0.0.1/localhost.", "Click Repair mail.")
        else:
            ok("No Roundcube sender addresses show the loopback address.")
    except Exception as e:
        warn(f"Could not inspect Roundcube sender addresses: {e}")

    # Is webmail reachable the way a browser reaches it (nginx on :80 → Apache)?
    try:
        import urllib.request
        req = urllib.request.Request("http://127.0.0.1/", headers={"Host": webmail})
        body = urllib.request.urlopen(req, timeout=6).read(4000).decode("utf-8", "replace")
        if "Apache2 Debian Default Page" in body:
            bad(f"http://{webmail} answers with Apache's default page — the webmail virtual host isn't matched.",
                "Run Install Mail Server again.")
        elif "roundcube" in body.lower():
            ok(f"Webmail answers at http://{webmail}.")
        else:
            warn(f"http://{webmail} answered, but it doesn't look like Roundcube.")
    except Exception as e:
        bad(f"Webmail did not answer at http://{webmail} ({e}).", "Run Install Mail Server again.")

    # recent evidence
    log = run(["journalctl", "-u", "postfix*", "-n", "60", "--no-pager"]).stdout
    lines = [l for l in log.splitlines() if re.search(r"error|warning|fatal|reject|refused|deferred|bounced", l, re.I)][-5:]
    if lines:
        print(f"\n  {C.DIM}Recent Postfix problems:{C.RESET}")
        for l in lines:
            print(f"    {l.strip()[:200]}")
    try:
        with open("/var/log/roundcube/errors.log", "r", errors="replace") as f:
            tail = [l.strip() for l in f.readlines()[-4:] if l.strip()]
        if tail:
            print(f"\n  {C.DIM}Recent Roundcube errors:{C.RESET}")
            for l in tail:
                print(f"    {l[:200]}")
    except OSError:
        pass

    print()
    if problems:
        warn(f"{problems} problem(s) found. Click \"Repair mail\" to fix the settings automatically.")
    else:
        ok("Everything checks out. If sending still fails, the lines above show what to look at.")
    return problems


def repair_mail():
    """Re-apply every mail setting WITHOUT reinstalling packages: Postfix local domain, Dovecot,
    Roundcube SMTP/mail_domain and stored sender addresses — then run the check."""
    if not mail_server_installed():
        err("The mail server is not installed. Install it first.")
        return False
    domain = get_mail_domain()
    if not domain or "." not in domain:
        err(f"Cannot tell which domain to use (/etc/mailname says '{domain}'). Run Install Mail Server once.")
        return False
    step(f"Repairing mail settings for {domain}...")
    configure_postfix_domain(domain)
    os_status, _ = check_os_compatibility()
    write_dovecot_local_conf(os_status)
    write_roundcube_settings(domain)
    services_ok = restart_mail_services(os_status)
    problems = diagnose_mail()
    return services_ok and problems == 0


def get_mail_domain():
    """The primary mail domain written by the installer (/etc/mailname), or '' if none."""
    try:
        with open("/etc/mailname", "r") as f:
            return f.readline().strip().lower()
    except OSError:
        return ""


def mail_server_installed():
    return os.path.exists("/usr/sbin/postfix") and os.path.exists("/usr/sbin/dovecot")


def get_mail_users():
    res = run(["/usr/bin/getent", "group", MAIL_GROUP])
    if res.returncode == 0 and res.stdout.strip():
        parts = res.stdout.strip().split(":")
        if len(parts) >= 4 and parts[3]:
            return sorted(u for u in parts[3].split(",") if u)
    return []


def _ensure_maildir(username):
    try:
        import pwd
        pw = pwd.getpwnam(username)
        base = os.path.join(pw.pw_dir, "Maildir")
        for sub in ("", "cur", "new", "tmp"):
            d = os.path.join(base, sub)
            os.makedirs(d, exist_ok=True)
            os.chmod(d, 0o700)
            os.chown(d, pw.pw_uid, pw.pw_gid)
    except Exception:
        pass            # Postfix / Dovecot create it on first delivery / login anyway


def create_mail_user(username, password):
    """Create a mail account: a system user in the 'mailuser' group, with a Maildir.

    Non-interactive on purpose. add_mail_user() runs the interactive `passwd`, which needs a
    terminal and so silently never set the password when called from the web UI."""
    domain = get_mail_domain()
    username = (username or "").strip().lower()
    password = password or ""
    if "@" in username:
        local, _, dom = username.partition("@")
        if domain and dom != domain:
            err(f"This server's mail domain is {domain} — enter just the part before the @.")
            return False
        username = local
    if not mail_server_installed():
        err("The mail server is not installed yet. Install it first (Install Services → Mail Server).")
        return False
    if not username:
        err("Enter a username.")
        return False
    if not MAIL_USER_RE.match(username):
        err("Usernames can use lowercase letters, digits, '.', '-' and '_', and must start with a letter.")
        return False
    if len(password) < 6:
        err("Password must be at least 6 characters.")
        return False
    if "\n" in password or "\r" in password:
        err("Password cannot contain line breaks.")
        return False

    run(["/usr/sbin/groupadd", "-f", MAIL_GROUP])
    if run(["/usr/bin/id", username]).returncode == 0:
        if username in get_mail_users():
            err(f"The mail account {username}@{domain or 'this-server'} already exists.")
        else:
            err(f"'{username}' is already used by a system account — pick another name.")
        return False

    step(f"Creating mail account {username}...")
    res = run(["/usr/sbin/useradd", "-m", "-s", "/usr/sbin/nologin", "-G", MAIL_GROUP, username])
    if res.returncode != 0:
        err(f"Failed to create the account:\n{(res.stderr or res.stdout).strip()}")
        return False
    pw = subprocess.run(["/usr/sbin/chpasswd"], input=f"{username}:{password}\n",
                        text=True, capture_output=True)
    if pw.returncode != 0:
        run(["/usr/sbin/userdel", "-r", username])          # don't leave an account without a password
        err(f"Could not set the password:\n{(pw.stderr or pw.stdout).strip()}")
        return False
    _ensure_maildir(username)
    ok(f"Mail account {username}@{domain} created." if domain else f"Mail account {username} created.")
    print(f"  Webmail login: {username}" + (f"  (or {username}@{domain})" if domain else ""))
    return True


def delete_mail_user_account(username):
    """Delete a mail account. Only members of the 'mailuser' group can be removed this way, so the
    web API can never be used to delete a real system account (root, your admin user, ...)."""
    username = (username or "").strip().lower().split("@")[0]
    if not username:
        err("No account given.")
        return False
    if username not in get_mail_users():
        err(f"'{username}' is not a mail account — refusing to delete it.")
        return False
    step(f"Deleting mail account {username} and its mailbox...")
    res = run(["/usr/sbin/userdel", "-r", username])
    if res.returncode == 8:                                  # an IMAP session is still open
        res = run(["/usr/sbin/userdel", "-r", "-f", username])
    if res.returncode in (0, 6):                             # 6 = mail spool already gone
        ok(f"Mail account {username} deleted.")
        return True
    err(f"Failed to delete the account:\n{(res.stderr or res.stdout).strip()}")
    return False


def delete_mail_user():
    menu_header("Delete Mail User")

    username = input("  Enter mail username to delete: ").strip()
    if not username:
        return
    delete_mail_user_account(username)

def list_mail_users():
    menu_header("List Mail Users")

    res = run(["/usr/bin/getent", "group", "mailuser"])
    if res.returncode != 0 or not res.stdout.strip():
        warn("No mail users found or 'mailuser' group does not exist.")
        return

    parts = res.stdout.strip().split(":")
    if len(parts) >= 4 and parts[3]:
        users = parts[3].split(",")
        print(f"  {C.CYAN}Mail Users:{C.RESET}")
        for u in users:
            print(f"    - {u}")
    else:
        warn("No mail users found in the 'mailuser' group.")
    print()

def delete_mail_services():
    menu_header("Delete Mail Services")

    if not prompt_confirm(f"{C.RED}WARNING: This will purge Postfix, Dovecot, and Roundcube.{C.RESET}\nProceed?"):
        warn("Cancelled.")
        return

    delete_users = input("  Delete all mail users and their inboxes as well? (yes/no): ").strip().lower()

    if delete_users == "yes":
        res = run(["/usr/bin/getent", "group", "mailuser"])
        if res.returncode == 0 and res.stdout.strip():
            parts = res.stdout.strip().split(":")
            if len(parts) >= 4 and parts[3]:
                for u in parts[3].split(","):
                    step(f"Deleting user {u}...")
                    run(["/usr/sbin/userdel", "-r", u])
        run(["/usr/sbin/groupdel", "mailuser"])
        ok("Mail users deleted.")

    print(f"\n  {C.CYAN}Purging mail server packages...{C.RESET}")
    packages = [
        "postfix", "dovecot-core", "dovecot-imapd", "dovecot-pop3d",
        "roundcube", "roundcube-core", "roundcube-mysql", "roundcube-sqlite3", "roundcube-plugins"
    ]
    
    # Restore correct ownership to directories that may have been broken by previous script runs
    run(["chown", "-R", "root:root", "/usr/share/roundcube"])
    run(["chown", "-R", "root:root", "/usr/share/javascript"])
    
    os.environ["DEBIAN_FRONTEND"] = "noninteractive"
    rc, stderr = run_live(
        ["apt-get", "purge", "-y"] + packages
    )

    if rc == 0:
        ok("Mail Server packages purged.")
    else:
        err("Errors occurred during purge.")

    step("Cleaning up residuals...")
    run(["apt-get", "autoremove", "-y"])
    
    if os.path.isfile("/etc/apache2/conf-available/roundcube.conf"):
        run(["/usr/sbin/a2disconf", "roundcube"])
        run(["systemctl", "reload", "apache2"])
    _remove_webmail_vhosts()
    apply_reloads()
    
    # Optional manual cleanup
    for d in ["/etc/postfix", "/etc/dovecot"]:
        if os.path.exists(d):
            shutil.rmtree(d, ignore_errors=True)

    ok("Mail Services completely removed.")

def mail_server_menu():
    while True:
        menu_header("Mail Server Manager")
        print("1. Install Mail Server (Postfix, Dovecot, Roundcube)")
        print("2. Add Mail User")
        print("3. Delete Mail User")
        print("4. List Mail Users")
        print("5. Delete Mail Services")
        print("0. Back")

        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            setup_mail_server()
        elif choice == "2":
            add_mail_user()
        elif choice == "3":
            delete_mail_user()
        elif choice == "4":
            list_mail_users()
        elif choice == "5":
            delete_mail_services()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")

# ─────────────────────────────────────────
#  FTP Server (vsftpd)
# ─────────────────────────────────────────

def setup_ftp_server():
    menu_header("FTP Server Setup (vsftpd)")
    info("Installing vsftpd...")
    rc, stderr = run_live(["apt-get", "install", "-y", "vsftpd"])
    if rc != 0:
        err(f"Failed to install vsftpd: {stderr.strip()}")
        return

    config = """\
listen=YES
listen_ipv6=NO
anonymous_enable=NO
local_enable=YES
write_enable=YES
local_umask=022
dirmessage_enable=YES
use_localtime=YES
xferlog_enable=YES
connect_from_port_20=YES
chroot_local_user=YES
allow_writeable_chroot=YES
secure_chroot_dir=/var/run/vsftpd/empty
pam_service_name=vsftpd
rsa_cert_file=/etc/ssl/certs/ssl-cert-snakeoil.pem
rsa_private_key_file=/etc/ssl/private/ssl-cert-snakeoil.key
ssl_enable=NO
"""
    if not safe_write("/etc/vsftpd.conf", config):
        return

    r = run(["systemctl", "restart", "vsftpd"])
    if r.returncode == 0:
        ok("vsftpd installed and started.")
    else:
        err("vsftpd failed to start.")

    r2 = run(["systemctl", "enable", "vsftpd"])
    ok("vsftpd enabled on boot.") if r2.returncode == 0 else err(r2.stderr.strip())

    print()
    print(f"  {C.BOLD}FTP is ready.{C.RESET}")
    print("  Use any system user to connect via FTP.")
    print("  Config: /etc/vsftpd.conf")
    input_pause("\n  Press Enter to continue...")


def delete_ftp_server():
    menu_header("Remove FTP Server (vsftpd)")
    if not prompt_confirm("Remove vsftpd and its configuration?", "no"):
        return
    run_live(["systemctl", "stop", "vsftpd"])
    rc, _ = run_live(["apt-get", "purge", "-y", "vsftpd"])
    ok("vsftpd removed.") if rc == 0 else err("Failed to remove vsftpd.")
    run(["rm", "-f", "/etc/vsftpd.conf"])
    input_pause("\n  Press Enter to continue...")


def ftp_server_menu():
    while True:
        menu_header("FTP Server Manager (vsftpd)")
        status = service_status("vsftpd")
        print(f"  vsftpd: {status}")
        menu_separator()
        print("1. Install / Setup FTP Server")
        print("2. Remove FTP Server")
        print("0. Back")

        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            setup_ftp_server()
        elif choice == "2":
            delete_ftp_server()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")


# ─────────────────────────────────────────
#  phpMyAdmin
# ─────────────────────────────────────────

def setup_phpmyadmin():
    menu_header("phpMyAdmin Setup")

    # Pre-seed debconf so it doesn't prompt
    debconf = (
        "phpmyadmin phpmyadmin/dbconfig-install boolean true\n"
        "phpmyadmin phpmyadmin/app-password-confirm password \n"
        "phpmyadmin phpmyadmin/mysql/admin-pass password \n"
        "phpmyadmin phpmyadmin/mysql/app-pass password \n"
        "phpmyadmin phpmyadmin/reconfigure-webserver multiselect apache2\n"
    )
    try:
        proc = subprocess.Popen(
            ["debconf-set-selections"],
            stdin=subprocess.PIPE, text=True
        )
        proc.communicate(input=debconf)
    except Exception as e:
        warn(f"debconf pre-seed failed (non-fatal): {e}")

    info("Installing phpMyAdmin...")
    rc, stderr = run_live(["apt-get", "install", "-y", "--no-install-recommends",
                           "phpmyadmin", "php-mbstring", "php-zip",
                           "php-gd", "php-json", "php-curl"])
    if rc != 0:
        err(f"Failed to install phpMyAdmin: {stderr.strip()}")
        return

    # Enable mbstring for apache if present
    run(["/usr/sbin/phpenmod", "mbstring"])
    run(["/usr/sbin/a2enconf", "phpmyadmin"])
    if os.path.isfile("/usr/sbin/nginx"):
        configure_apache_ports()            # nginx owns :80 — keep Apache off it
    mark_reload("apache2")

    ok("phpMyAdmin installed successfully.")

    # Apache serves phpMyAdmin on :8080; without a route, nginx on :80 answers /phpmyadmin with 404
    on_port_80 = expose_phpmyadmin_via_nginx()
    apply_reloads()

    ip = detect_server_ip() or "<your-server-ip>"
    print()
    if on_port_80:
        print(f"  {C.BOLD}Access via:{C.RESET}  http://{ip}/phpmyadmin")
        if apache_on_backend_port():
            print(f"  {C.DIM}Direct (Apache): http://{ip}:8080/phpmyadmin{C.RESET}")
    else:
        port = 8080 if apache_on_backend_port() else 80
        print(f"  {C.BOLD}Access via:{C.RESET}  http://{ip}:{port}/phpmyadmin")
    print("  Login with your MariaDB root or site database credentials.")
    input_pause("\n  Press Enter to continue...")


def delete_phpmyadmin():
    menu_header("Remove phpMyAdmin")
    if not prompt_confirm("Remove phpMyAdmin completely?", "no"):
        return
    rc, _ = run_live(["apt-get", "purge", "-y", "phpmyadmin"])
    ok("phpMyAdmin removed.") if rc == 0 else err("Failed to remove phpMyAdmin.")
    unexpose_phpmyadmin_nginx()
    mark_reload("apache2")
    apply_reloads()
    input_pause("\n  Press Enter to continue...")


def phpmyadmin_menu():
    while True:
        menu_header("phpMyAdmin Manager")
        # quick check
        installed = run(["dpkg", "-s", "phpmyadmin"]).returncode == 0
        status_str = f"{C.GREEN}installed{C.RESET}" if installed else f"{C.RED}not installed{C.RESET}"
        print(f"  phpMyAdmin: {status_str}")
        menu_separator()
        print("1. Install / Setup phpMyAdmin")
        print("2. Remove phpMyAdmin")
        print("0. Back")

        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            setup_phpmyadmin()
        elif choice == "2":
            delete_phpmyadmin()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")


# ─────────────────────────────────────────
#  Samba Server
# ─────────────────────────────────────────

def setup_samba():
    menu_header("Samba Share Setup")

    info("Installing Samba...")
    rc, stderr = run_live(["apt-get", "install", "-y", "samba"])
    if rc != 0:
        err(f"Failed to install Samba: {stderr.strip()}")
        return

    share_name = input("  Share name (e.g. shared): ").strip() or "shared"
    share_path = input("  Path to share (e.g. /srv/samba/shared): ").strip() or f"/srv/samba/{share_name}"
    public     = prompt_confirm("Allow guest/public access (no password)?", "no")

    run(["mkdir", "-p", share_path])
    run(["chmod", "0775", share_path])

    guest_ok  = "yes" if public else "no"
    browsable = "yes"

    smb_block = f"""
[{share_name}]
   path = {share_path}
   browsable = {browsable}
   read only = no
   guest ok = {guest_ok}
   create mask = 0775
   directory mask = 0775
"""
    try:
        backup_config("/etc/samba/smb.conf")
        if DRY_RUN:
            step("[dry-run] Would append to /etc/samba/smb.conf")
        else:
            with open("/etc/samba/smb.conf", "a") as f:
                f.write(smb_block)
        ok(f"Share '{share_name}' added to /etc/samba/smb.conf")
    except Exception as e:
        err(f"Could not write to smb.conf: {e}")
        return

    r = run(["systemctl", "restart", "smbd"])
    ok("Samba started.") if r.returncode == 0 else err("Samba failed to start.")
    run(["systemctl", "enable", "smbd"])

    print()
    print(f"  {C.BOLD}Samba share ready:{C.RESET}  \\\\<server-ip>\\{share_name}")
    if not public:
        print("  Add Samba users with:  smbpasswd -a <username>")
    input_pause("\n  Press Enter to continue...")


def delete_samba():
    menu_header("Remove Samba")
    if not prompt_confirm("Remove Samba and its configuration?", "no"):
        return
    run_live(["systemctl", "stop", "smbd"])
    rc, _ = run_live(["apt-get", "purge", "-y", "samba", "samba-common"])
    ok("Samba removed.") if rc == 0 else err("Failed to remove Samba.")
    input_pause("\n  Press Enter to continue...")


def samba_menu():
    while True:
        menu_header("Samba Server Manager")
        status = service_status("smbd")
        print(f"  smbd: {status}")
        menu_separator()
        print("1. Add a Samba Share")
        print("2. Remove Samba")
        print("0. Back")

        choice = input("  \nChoice: ").strip()
        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            setup_samba()
        elif choice == "2":
            delete_samba()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")


# ─────────────────────────────────────────
#  Make Services menu
# ─────────────────────────────────────────

def make_services_menu():
    while True:
        menu_header("Make Services")
        print("1. Mail Server")
        print("2. FTP Server (vsftpd)")
        print("3. phpMyAdmin")
        print("4. Samba Share")
        print("0. Back")

        choice = input("  \nChoice: ").strip()

        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            mail_server_menu()
        elif choice == "2":
            ftp_server_menu()
        elif choice == "3":
            phpmyadmin_menu()
        elif choice == "4":
            samba_menu()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")

def ensure_ufw():
    """Ensure UFW is installed."""
    rc = run(["dpkg", "-s", "ufw"]).returncode
    if rc != 0:
        info("Installing UFW...")
        run_live(["apt-get", "install", "-y", "ufw"])

def manage_firewall_menu():
    ensure_ufw()
    while True:
        menu_header("Manage Firewall (UFW)")
        print("1. Enable Firewall")
        print("2. Disable Firewall")
        print("3. Allow a Port")
        print("4. Deny/Delete a Port")
        print("5. Firewall Status")
        print("0. Back")

        choice = input("  \nChoice: ").strip()

        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            warn("Enabling UFW may drop your current SSH connection if port 22 is not allowed.")
            allow_ssh = prompt_confirm("Allow port 22 (SSH) first?", "yes")
            if allow_ssh:
                run(["ufw", "allow", "22/tcp"])
                ok("Port 22 (SSH) allowed.")
            else:
                verify = input(f"  {C.RED}You chose NOT to allow SSH. Type 'port22' to confirm you want to block SSH:{C.RESET} ").strip()
                if verify != "port22":
                    err("Verification failed. Aborting UFW enable.")
                    continue
            r = run_live(["ufw", "--force", "enable"])
            ok("Firewall enabled.") if r[0] == 0 else err("Failed to enable firewall.")
        elif choice == "2":
            r = run_live(["ufw", "disable"])
            ok("Firewall disabled.") if r[0] == 0 else err("Failed to disable firewall.")
        elif choice == "3":
            port = input("  Enter port to allow (e.g., 80, 443, 80/tcp): ").strip()
            if port:
                r = run_live(["ufw", "allow", port])
                ok(f"Allowed port {port}.") if r[0] == 0 else err(f"Failed to allow port {port}.")
        elif choice == "4":
            port = input("  Enter port to deny/delete (e.g., 80, 443, 80/tcp): ").strip()
            if port:
                if port.startswith("22") or port.lower() == "ssh":
                    verify = input(f"  {C.RED}You are attempting to block SSH. Type 'port22' to confirm:{C.RESET} ").strip()
                    if verify != "port22":
                        err("Verification failed. Aborting.")
                        continue
                r = run_live(["ufw", "delete", "allow", port])
                if r[0] != 0:
                    r = run_live(["ufw", "deny", port])
                ok(f"Denied/deleted port {port}.") if r[0] == 0 else err(f"Failed to deny port {port}.")
        elif choice == "5":
            print()
            run_live(["ufw", "status", "verbose"])
            print()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")
            continue

        if choice in ("1", "2", "3", "4"):
            if prompt_confirm("Reload UFW to apply changes?", "yes"):
                r_reload = run_live(["ufw", "reload"])
                ok("UFW reloaded.") if r_reload[0] == 0 else err("Failed to reload UFW.")

def manage_server_menu():
    while True:
        menu_header("Manage Server")
        print("1. Manage Server IP")
        print("2. Manage Server Services")
        print("3. Manage Firewall (UFW)")
        print("0. Back")

        choice = input("  \nChoice: ").strip()

        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            manage_server_ip()
        elif choice == "2":
            service_control_menu()
        elif choice == "3":
            manage_firewall_menu()
        elif choice == "0":
            break
        else:
            warn("Invalid choice.")


# ─────────────────────────────────────────
#  Main menu helpers
# ─────────────────────────────────────────

def get_os_version():
    """Return the pretty OS name from /etc/os-release."""
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=")[1].strip().strip('"')
    except Exception:
        pass
    return "Unknown OS"


def show_help():
    """Print a concise reference for every top-level menu item."""
    menu_header("Help — Server Manager")
    print(f"\n  {C.BOLD}1. Make Site{C.RESET}")
    info("  Create and manage virtual hosts for Apache or Nginx.")
    info("  Sub-options: LAMP (Apache+PHP+MariaDB), LEMP (Nginx+PHP+MariaDB),")
    info("  Full Nginx site, static site, reverse proxy, WordPress, Let's Encrypt.")

    print(f"\n  {C.BOLD}2. Make Services{C.RESET}")
    info("  Install and configure supplementary server services.")
    info("  Sub-options: Mail Server (Postfix+Dovecot+Roundcube), FTP (vsftpd),")
    info("  phpMyAdmin, Samba file sharing.")

    print(f"\n  {C.BOLD}3. Manage Server{C.RESET}")
    info("  System-level management tools.")
    info("  Sub-options: Server IP (static/DHCP), Service control")
    info("  (start/stop/restart/enable), Firewall (UFW).")

    print(f"\n  {C.BOLD}4. Reload All Related Services{C.RESET}")
    info("  Reloads Apache2, Nginx, BIND9 and restarts MariaDB if running.")
    info("  Uses the pending-reload tracker — safe to call at any time.")

    print(f"\n  {C.BOLD}5. Help{C.RESET}")
    info("  Displays this reference screen.")

    print(f"\n  {C.BOLD}0. Exit{C.RESET}")
    info("  Exit Server Manager gracefully.")

    print(f"\n  {C.DIM}Tip: Run with --dry-run to preview commands without executing them.{C.RESET}")
    print(f"  {C.DIM}Script requires root (sudo). All installations are non-interactive.{C.RESET}")
    print()
    input("  Press Enter to return to the main menu...")


def display_main_menu() -> str:
    """Render the main menu, return the user's raw choice string."""
    apache  = service_status("apache2")
    bind9   = service_status("bind9")
    mariadb = service_status("mariadb")

    menu_header("Server Manager")

    if DRY_RUN:
        print(f"  {C.YELLOW}[DRY-RUN MODE — no commands will be executed]{C.RESET}")

    print(f"  Apache2: {apache}   BIND9: {bind9}")
    print(f"  MariaDB: {mariadb}")
    menu_separator()
    print("1. Make Site")
    print("2. Manage MariaDB")
    print("3. Make services")
    print("4. Manage Server")
    print("5. Reload All Related Services")
    print("6. Help")
    print("7. Docker")
    print("0. Exit")
    menu_separator()
    print(f"  {C.CYAN}OS Detected: {get_os_version()}{C.RESET}")
    return input("  \nChoice: ").strip()


def _shutdown():
    """Centralised graceful shutdown — add cleanup tasks here as needed."""
    print(f"\n  {C.CYAN}Goodbye!{C.RESET}")
    sys.exit(0)


# ─────────────────────────────────────────
#  Server Telemetry Helpers
# ─────────────────────────────────────────

_last_cpu_sample = None

def get_system_uptime():
    """Return system uptime in seconds, formatted duration string, and boot timestamp."""
    seconds = None
    if os.path.exists("/proc/uptime"):
        try:
            with open("/proc/uptime", "r") as f:
                seconds = float(f.readline().split()[0])
        except Exception:
            pass

    if seconds is None and sys.platform == "win32":
        try:
            import ctypes
            ms = ctypes.windll.kernel32.GetTickCount64()
            seconds = ms / 1000.0
        except Exception:
            pass

    if seconds is None:
        return {"seconds": 0, "formatted": "N/A", "boot_time": "N/A"}

    total_secs = int(seconds)
    days = total_secs // 86400
    hours = (total_secs % 86400) // 3600
    minutes = (total_secs % 3600) // 60
    secs = total_secs % 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0 or days > 0:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    formatted = " ".join(parts) if parts else f"{secs}s"

    try:
        boot_dt = datetime.fromtimestamp(time.time() - seconds).strftime("%Y-%m-%d %H:%M")
    except Exception:
        boot_dt = "N/A"

    return {
        "seconds": total_secs,
        "formatted": formatted,
        "boot_time": boot_dt
    }


def get_ram_usage():
    """Return RAM usage: percent, used_gb, total_gb, free_gb, formatted string."""
    if os.path.exists("/proc/meminfo"):
        try:
            mem = {}
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        mem[parts[0].strip()] = int(parts[1].strip().split()[0])
            total_kb = mem.get("MemTotal", 0)
            avail_kb = mem.get("MemAvailable", mem.get("MemFree", 0) + mem.get("Buffers", 0) + mem.get("Cached", 0))
            used_kb = max(0, total_kb - avail_kb)
            if total_kb > 0:
                percent = round((used_kb / total_kb) * 100, 1)
                total_gb = round(total_kb / (1024 * 1024), 2)
                used_gb = round(used_kb / (1024 * 1024), 2)
                free_gb = round(avail_kb / (1024 * 1024), 2)
                return {
                    "percent": percent,
                    "used_gb": used_gb,
                    "total_gb": total_gb,
                    "free_gb": free_gb,
                    "formatted": f"{used_gb} GB / {total_gb} GB"
                }
        except Exception:
            pass

    if sys.platform == "win32":
        try:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ('dwLength', ctypes.c_ulong),
                    ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong),
                    ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong),
                    ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong),
                    ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            total_gb = round(stat.ullTotalPhys / (1024**3), 2)
            avail_gb = round(stat.ullAvailPhys / (1024**3), 2)
            used_gb = round(max(0.0, total_gb - avail_gb), 2)
            percent = float(stat.dwMemoryLoad)
            return {
                "percent": percent,
                "used_gb": used_gb,
                "total_gb": total_gb,
                "free_gb": avail_gb,
                "formatted": f"{used_gb} GB / {total_gb} GB"
            }
        except Exception:
            pass

    return {
        "percent": 0.0,
        "used_gb": 0.0,
        "total_gb": 0.0,
        "free_gb": 0.0,
        "formatted": "N/A"
    }


def get_cpu_usage():
    """Return CPU usage: percent, cores count, load average, and load string."""
    global _last_cpu_sample
    cores = os.cpu_count() or 1
    percent = 0.0
    load_avg = [0.0, 0.0, 0.0]

    if os.path.exists("/proc/loadavg"):
        try:
            with open("/proc/loadavg", "r") as f:
                load_parts = f.read().split()[:3]
                load_avg = [round(float(x), 2) for x in load_parts]
        except Exception:
            pass

    if os.path.exists("/proc/stat"):
        try:
            with open("/proc/stat", "r") as f:
                first_line = f.readline()
            if first_line.startswith("cpu "):
                fields = [float(x) for x in first_line.split()[1:8]]
                idle = fields[3] + fields[4]
                total = sum(fields)
                if _last_cpu_sample is not None:
                    prev_idle, prev_total = _last_cpu_sample
                    idle_delta = idle - prev_idle
                    total_delta = total - prev_total
                    if total_delta > 0:
                        percent = round(100.0 * (1.0 - (idle_delta / total_delta)), 1)
                        percent = max(0.0, min(100.0, percent))
                else:
                    time.sleep(0.05)
                    with open("/proc/stat", "r") as f2:
                        second_line = f2.readline()
                    fields2 = [float(x) for x in second_line.split()[1:8]]
                    idle2 = fields2[3] + fields2[4]
                    total2 = sum(fields2)
                    total_delta = total2 - total
                    idle_delta = idle2 - idle
                    if total_delta > 0:
                        percent = round(100.0 * (1.0 - (idle_delta / total_delta)), 1)
                        percent = max(0.0, min(100.0, percent))
                    idle, total = idle2, total2
                _last_cpu_sample = (idle, total)
        except Exception:
            pass
    elif sys.platform == "win32":
        try:
            import ctypes
            class FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", ctypes.c_uint), ("dwHighDateTime", ctypes.c_uint)]
            def to_int(ft):
                return (ft.dwHighDateTime << 32) + ft.dwLowDateTime
            idle_ft, kernel_ft, user_ft = FILETIME(), FILETIME(), FILETIME()
            if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle_ft), ctypes.byref(kernel_ft), ctypes.byref(user_ft)):
                idle = to_int(idle_ft)
                kernel = to_int(kernel_ft)
                user = to_int(user_ft)
                total = kernel + user
                if _last_cpu_sample is not None:
                    prev_idle, prev_total = _last_cpu_sample
                    idle_delta = idle - prev_idle
                    total_delta = total - prev_total
                    if total_delta > 0:
                        percent = round(100.0 * (1.0 - (idle_delta / total_delta)), 1)
                        percent = max(0.0, min(100.0, percent))
                _last_cpu_sample = (idle, total)
        except Exception:
            pass

    load_str = f"{load_avg[0]:.2f}, {load_avg[1]:.2f}, {load_avg[2]:.2f}"
    return {
        "percent": percent,
        "cores": cores,
        "load_avg": load_avg,
        "load_str": load_str,
        "formatted": f"{percent}%"
    }


# ─────────────────────────────────────────
#  Web Configuration Server
# ─────────────────────────────────────────

class MockStdin:
    """Stdin replacement that feeds pre-defined answers to input() calls.

    The web handlers drive the interactive CLI menus by swapping sys.stdin for
    one of these. After all answers are consumed it returns empty lines, which
    accepts defaults / skips optional prompts.

    Lives at module level on purpose: it used to be defined *inside*
    handle_makesite_post(), so every other handler (install, uninstall, docker,
    database, DNS, network, ...) crashed with ``NameError: name 'MockStdin' is not defined``.
    """
    def __init__(self, responses):
        self.responses = list(responses)
        self._buffer = ""
    def readline(self):
        if self.responses:
            return str(self.responses.pop(0)) + "\n"
        return "\n"
    def read(self, n=-1):
        return self.readline()
    def isatty(self):
        return False
    def fileno(self):
        raise io.UnsupportedOperation("MockStdin has no fileno")


class WebUIHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # Serve static files from the web_config directory
        kwargs['directory'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web_config')
        super().__init__(*args, **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path.startswith("/api/"):
            # ── Phase 0: Auth-free routes ─────────────────────────────────────
            AUTH_FREE_GET = {"/api/session", "/api/health"}
            # Fix: GET /api/session must reach web_handle_auth — auth.js
            # bootstraps every page load with it and needs
            # {"ok":true, csrf, actor} for a live cookie. Without this,
            # the request fell through handle_api_get to the generic
            # `{}` and every reload forced a re-login.
            if parsed.path in ("/api/session", "/api/login", "/api/logout"):
                if web_handle_auth(self, parsed.path):
                    return
            if parsed.path not in AUTH_FREE_GET:
                cookie = self.headers.get("Cookie", "")
                sid = ""
                for part in cookie.split(";"):
                    part = part.strip()
                    if part.startswith("zm_session="):
                        sid = part[len("zm_session="):]
                sess = auth_session(sid)
                if not sess:
                    self.send_response(401)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"login required"}')
                    return
                self.zm_session = sess

            # ── Phase 2–6: New API endpoints ───────────────────────────────────
            if web_handle_new_api(self, parsed.path):
                return

            # ── Existing GET handler ───────────────────────────────────────────
            self.handle_api_get(parsed)
        else:
            super().do_GET()

    def log_request(self, code="-", size="-"):
        pass  # access logs go to the audit log, not stdout

    def log_error(self, fmt, *args):
        pass  # suppress error spam from broken pipe / client disconnect

    def handle_api_get(self, parsed):
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.send_header("X-ZM-API-Version", API_VERSION)
        self.end_headers()
        
        if parsed.path == "/api/status":
            # One batched systemctl query for all units. Do NOT call
            # service_status() per unit here: each call re-checks the shared
            # TTL cache, so the units that were not part of the first cached
            # batch would be served as cache-miss "inactive" for the TTL.
            svc_names = ("apache2", "nginx", "bind9", "mariadb",
                         "postfix", "dovecot", "ufw")
            svc_state = service_statuses(list(svc_names), ttl=3.0)
            svc = {name: ("running" if svc_state.get(name) == "active" else "stopped")
                   for name in svc_names}
            uptime_info = get_system_uptime()
            ram_info = get_ram_usage()
            cpu_info = get_cpu_usage()

            open_inc = incidents_list("open")
            data = {
                "os": get_os_version(),
                "apache2": svc["apache2"],
                "nginx": svc["nginx"],
                "bind9": svc["bind9"],
                "mariadb": svc["mariadb"],
                "postfix": svc["postfix"],
                "dovecot": svc["dovecot"],
                "ufw": svc["ufw"],
                "docker_installed": is_docker_installed(),
                "version": SCRIPT_VERSION,
                "api_version": API_VERSION,
                "safe_mode": is_safe_mode(),
                "open_incidents": len(open_inc),
                "high_severity": sum(1 for i in open_inc if i["severity"] in ("high", "critical")),
                "uptime": uptime_info["formatted"],
                "uptime_seconds": uptime_info["seconds"],
                "boot_time": uptime_info.get("boot_time", ""),
                "cpu": cpu_info["percent"],
                "cpu_cores": cpu_info["cores"],
                "cpu_load": cpu_info["load_str"],
                "ram": ram_info["percent"],
                "ram_used_gb": ram_info["used_gb"],
                "ram_total_gb": ram_info["total_gb"],
                "ram_formatted": ram_info["formatted"]
            }
            self.wfile.write(json.dumps(data).encode("utf-8"))
        elif parsed.path == "/api/sites":
            sites = []
            if os.path.exists(APACHE_SITES_AVAILABLE):
                for file in sorted(os.listdir(APACHE_SITES_AVAILABLE)):
                    if file.endswith(".conf"):
                        domain = file[:-5]
                        meta = get_site_meta(domain) or {}
                        enabled = os.path.exists(f"/etc/apache2/sites-enabled/{file}")
                        sites.append({
                            "domain": domain,
                            "server": "apache",
                            "type": meta.get("type", "unknown"),
                            "docroot": meta.get("docroot", f"/var/www/{domain}"),
                            "container_name": meta.get("container_name"),
                            "host_port": meta.get("host_port"),
                            "enabled": enabled
                        })
            if os.path.exists(NGINX_SITES_AVAILABLE):
                for file in sorted(os.listdir(NGINX_SITES_AVAILABLE)):
                    if file.endswith(".conf"):
                        domain = file[:-5]
                        meta = get_site_meta(domain) or {}
                        enabled = os.path.exists(f"/etc/nginx/sites-enabled/{file}")
                        sites.append({
                            "domain": domain,
                            "server": "nginx",
                            "type": meta.get("type", "unknown"),
                            "docroot": meta.get("docroot", f"/var/www/{domain}"),
                            "container_name": meta.get("container_name"),
                            "host_port": meta.get("host_port"),
                            "enabled": enabled
                        })
            self.wfile.write(json.dumps(sites).encode("utf-8"))
        elif parsed.path == "/api/docker_containers":
            containers = []
            if is_docker_installed():
                res = run(["docker", "ps", "-a", "--format", "{{.Names}}|{{.Image}}|{{.Status}}"])
                if res.returncode == 0 and res.stdout.strip():
                    for line in res.stdout.strip().split("\n"):
                        parts = line.split("|")
                        if len(parts) >= 3:
                            containers.append({"name": parts[0], "image": parts[1], "status": parts[2]})
            self.wfile.write(json.dumps(containers).encode("utf-8"))
        elif parsed.path == "/api/manage/dns":
            query = urllib.parse.parse_qs(parsed.query)
            if query.get('action', [''])[0] == 'list':
                zones_list = []
                if os.path.isfile(NAMED_CONF_LOCAL):
                    with open(NAMED_CONF_LOCAL, "r") as f:
                        content = f.read()
                    zones = re.findall(r'zone "([^"]+)"', content)
                    for z in zones:
                        z_type = "reverse" if z.endswith(".in-addr.arpa") else "forward"
                        zones_list.append({"domain": z, "type": z_type})
                self.wfile.write(json.dumps(zones_list).encode("utf-8"))
            else:
                self.wfile.write(b"[]")
        elif parsed.path == "/api/manage/mail":
            query = urllib.parse.parse_qs(parsed.query)
            action_q = query.get('action', [''])[0]
            if action_q == 'info':
                self.wfile.write(json.dumps({
                    "installed": mail_server_installed(),
                    "domain": get_mail_domain(),
                    "users": get_mail_users(),
                }).encode("utf-8"))
            elif action_q == 'list':
                self.wfile.write(json.dumps(get_mail_users()).encode("utf-8"))
            else:
                self.wfile.write(b"[]")
        else:
            self.wfile.write(b"{}")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)

        # ── Phase 0: Auth endpoints (no session required) ─────────────────────
        if parsed.path in ("/api/login", "/api/logout"):
            web_handle_auth(self, parsed.path)
            return

        # ── Phase 0: Session enforcement ─────────────────────────────────
        cookie = self.headers.get("Cookie", "")
        sid = ""
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("zm_session="):
                sid = part[len("zm_session="):]
        sess = auth_session(sid)
        if not sess:
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"login required"}')
            return
        self.zm_session = sess

        # ── Phase 0: CSRF check ──────────────────────────────────────────
        csrf_header = self.headers.get("X-ZM-CSRF", "")
        if not auth_csrf_ok(sid, csrf_header):
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"invalid CSRF token"}')
            return

        # ── Phase 5: Safe-mode guard ───────────────────────────────────────
        SAFE_MODE_ALLOWED = {
            "/api/snapshots", "/api/changes", "/api/incidents",
            "/api/doctor", "/api/auth/reset", "/api/confirm",
            "/api/safe-mode",
        }
        if is_safe_mode():
            allowed = any(
                parsed.path == p or parsed.path.startswith(p + "/")
                for p in SAFE_MODE_ALLOWED
            )
            if not allowed:
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    b'{"error":"safe mode active - only recovery routes are available"}'
                )
                return

        # ── Phase 0: Origin / Host check ───────────────────────────────────
        origin = self.headers.get("Origin", "")
        host   = self.headers.get("Host", "")
        if origin and not (origin.startswith("http://") or origin.startswith("https://")):
            self.send_error(403, "Forbidden: bad Origin")
            return
        if origin and host and host not in origin:
            self.send_error(403, "Forbidden: cross-origin request")
            return

        # ── Phase 0: Audit context ─────────────────────────────────────────
        self._zm_actor = sess.get("actor", "web")
        self._zm_src   = self.client_address[0] if self.client_address else ""

        # ── Phase 2–6: New API endpoints ───────────────────────────────────
        if web_handle_new_api(self, parsed.path):
            return

        # ── Existing POST routing ─────────────────────────────────────────
        if parsed.path.startswith("/api/makesite/"):
            self.handle_makesite_post(parsed.path)
        elif parsed.path.startswith("/api/manage/docker"):
            self.handle_manage_docker_post(parsed.path)
        elif parsed.path == "/api/makesite_docker":
            self.handle_makesite_post("/api/makesite/docker")
        elif parsed.path.startswith("/api/manage/site_action"):
            self.handle_site_action_post()
        elif parsed.path.startswith("/api/manage/"):
            self.handle_manage_post(parsed.path)
        elif parsed.path.startswith("/api/install/") or parsed.path.startswith("/api/uninstall/"):
            self.handle_install_post(parsed.path)
        elif parsed.path.startswith("/api/upload_site"):
            self.handle_upload_site(parsed)
        else:
            self.send_error(404)

    def handle_upload_site(self, parsed):
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length == 0:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b'{"success": false, "error": "No content"}')
            return

        upload_dir = "/tmp/zerver_uploads"
        if not os.path.exists(upload_dir):
            os.makedirs(upload_dir, exist_ok=True)
            
        import uuid, zipfile
        uid = str(uuid.uuid4())[:8]
        temp_zip_path = os.path.join(upload_dir, f"upload_{uid}.zip")
        extract_path = os.path.join(upload_dir, f"site_{uid}")
        
        try:
            with open(temp_zip_path, "wb") as f:
                bytes_read = 0
                while bytes_read < content_length:
                    chunk = self.rfile.read(min(8192, content_length - bytes_read))
                    if not chunk:
                        break
                    f.write(chunk)
                    bytes_read += len(chunk)
                    
            if not os.path.exists(extract_path):
                os.makedirs(extract_path, exist_ok=True)
                
            with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_path)
                
            if os.path.exists(temp_zip_path):
                os.remove(temp_zip_path)
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {"success": True, "path": extract_path}
            self.wfile.write(json.dumps(response).encode("utf-8"))
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {"success": False, "error": str(e)}
            self.wfile.write(json.dumps(response).encode("utf-8"))

    def handle_site_action_post(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
        data = json.loads(post_data.decode('utf-8'))
        
        action = data.get("action")
        domain = data.get("domain")
        server = data.get("server", "apache")
        
        success = False
        message = ""
        
        try:
            if not domain:
                raise Exception("Domain is required.")
                
            conf_dir = APACHE_SITES_AVAILABLE if server == "apache" else NGINX_SITES_AVAILABLE
            enabled_dir = "/etc/apache2/sites-enabled" if server == "apache" else "/etc/nginx/sites-enabled"
            conf_path = f"{conf_dir}/{domain}.conf"
            link_path = f"{enabled_dir}/{domain}.conf"
            
            if action == "enable":
                if server == "apache":
                    enable_site(domain)
                    reload_apache()
                else:
                    if not os.path.exists(link_path) and os.path.exists(conf_path):
                        os.symlink(conf_path, link_path)
                    reload_nginx()
                success = True
                message = f"Enabled {domain}."
                
            elif action == "disable":
                if server == "apache":
                    disable_site_cmd(domain)
                    reload_apache()
                else:
                    if os.path.islink(link_path):
                        os.unlink(link_path)
                    reload_nginx()
                success = True
                message = f"Disabled {domain}."
                
            elif action == "delete":
                remove_docroot = data.get("remove_docroot", False)
                meta = get_site_meta(domain)
                docroot = meta.get("docroot", f"/var/www/{domain}") if meta else f"/var/www/{domain}"
                
                # Delete DB if lamp or lemp
                if meta and meta.get("type") in ["lamp", "lemp"]:
                    db_name = meta.get("db_name")
                    db_user = meta.get("db_user")
                    if db_name and db_user:
                        sql = f"DROP DATABASE IF EXISTS `{db_name}`; DROP USER IF EXISTS '{db_user}'@'localhost'; FLUSH PRIVILEGES;"
                        run(["mysql", "-e", sql])
                        
                # Disable site and remove configs
                if server == "apache":
                    disable_site_cmd(domain)
                    if os.path.islink(link_path): os.unlink(link_path)
                    if os.path.isfile(conf_path): os.remove(conf_path)
                    reload_apache()
                else:
                    if os.path.islink(link_path): os.unlink(link_path)
                    if os.path.isfile(conf_path): os.remove(conf_path)
                    reload_nginx()
                    
                if remove_docroot and os.path.exists(docroot):
                    shutil.rmtree(docroot)
                    
                delete_site_meta(domain)
                success = True
                message = f"Deleted {domain}."
                
            elif action == "get_vhost":
                if os.path.isfile(conf_path):
                    with open(conf_path, "r") as f:
                        config_content = f.read()
                    success = True
                    message = config_content
                else:
                    raise Exception("Config file not found.")
                    
            elif action == "update_vhost":
                new_config = data.get("config", "")
                if os.path.isfile(conf_path):
                    with open(conf_path, "w") as f:
                        f.write(new_config)
                    if server == "apache":
                        reload_apache()
                    else:
                        reload_nginx()
                    success = True
                    message = f"Updated vhost configuration for {domain}."
                else:
                    raise Exception("Config file not found.")
                    
            elif action == "update_docroot":
                new_docroot = data.get("docroot")
                if not new_docroot:
                    raise Exception("New document root cannot be empty.")
                meta = get_site_meta(domain) or {}
                meta["docroot"] = new_docroot
                
                with open(conf_path, "r") as f:
                    content = f.read()
                if server == "apache":
                    content = re.sub(r'DocumentRoot\s+.*', f'DocumentRoot {new_docroot}', content)
                    content = re.sub(r'<Directory\s+[^>]+>', f'<Directory {new_docroot}>', content)
                else:
                    content = re.sub(r'root\s+[^;]+;', f'root {new_docroot};', content)
                with open(conf_path, "w") as f:
                    f.write(content)
                    
                Path(new_docroot).mkdir(parents=True, exist_ok=True)
                
                with open(f"{META_DIR}/{domain}.json", "w") as f:
                    json.dump(meta, f, indent=4)
                    
                if server == "apache":
                    reload_apache()
                else:
                    reload_nginx()
                success = True
                message = f"Updated DocumentRoot to {new_docroot}."
                
            elif action == "update_db_pass":
                new_pass = data.get("db_pass")
                meta = get_site_meta(domain)
                if not meta or meta.get("type") not in ["lamp", "lemp", "wordpress"]:
                    raise Exception("Site does not have an active database.")
                db_user = meta.get("db_user")
                if not db_user:
                    raise Exception("No DB user recorded for this site.")
                    
                # Update mariaDB
                sql = f"ALTER USER '{db_user}'@'localhost' IDENTIFIED BY '{new_pass}'; FLUSH PRIVILEGES;"
                res = run(["mysql", "-e", sql])
                if res.returncode != 0:
                    raise Exception(f"MariaDB error: {res.stderr}")
                    
                # Update wp-config.php or index.php
                docroot = meta.get("docroot", f"/var/www/{domain}")
                wp_cfg = f"{docroot}/wp-config.php"
                idx_php = f"{docroot}/index.php"
                
                if os.path.exists(wp_cfg):
                    with open(wp_cfg, "r") as f: content = f.read()
                    content = re.sub(r"define\(\s*'DB_PASSWORD'\s*,\s*'.*?'\s*\);", f"define( 'DB_PASSWORD', '{new_pass}' );", content)
                    with open(wp_cfg, "w") as f: f.write(content)
                elif os.path.exists(idx_php):
                    with open(idx_php, "r") as f: content = f.read()
                    content = re.sub(r'\$password\s*=\s*".*?";', f'$password = "{new_pass}";', content)
                    content = re.sub(r"\$password\s*=\s*'.*?';", f"$password = '{new_pass}';", content)
                    with open(idx_php, "w") as f: f.write(content)
                    
                success = True
                message = "Database password updated successfully."
                
            else:
                raise Exception("Unknown action.")
                
        except Exception as e:
            success = False
            message = str(e)
            traceback.print_exc()
            
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"success": success, "message": message}).encode("utf-8"))

    def _setup_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        
        class StreamingLogger:
            def __init__(self, wfile):
                self.wfile = wfile
            def write(self, text):
                if not text: return
                # text = re.sub(r'\x1b\[[0-9;]*m', '', text) # Preserved for frontend parsing
                try:
                    self.wfile.write(text.encode('utf-8'))
                    self.wfile.flush()
                except Exception:
                    pass
            def flush(self):
                try:
                    self.wfile.flush()
                except Exception:
                    pass
            def isatty(self): return False
            def fileno(self): raise io.UnsupportedOperation()
            
        old_stdout = sys.stdout
        old_stdin = sys.stdin
        sys.stdout = StreamingLogger(self.wfile)
        return old_stdout, old_stdin

    def _teardown_stream(self, old_stdout, old_stdin, success, domain=None):
        import sys, json
        sys.stdout = old_stdout
        sys.stdin = old_stdin
        result = {"success": success}
        if domain:
            result["domain"] = domain
        try:
            self.wfile.write(f"\n===RESULT===\n{json.dumps(result)}\n".encode('utf-8'))
            self.wfile.flush()
        except Exception:
            pass

    def handle_manage_docker_post(self, path):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
        data = json.loads(post_data.decode('utf-8'))
        
        old_stdout, old_stdin = self._setup_stream()
        import sys
        
        success = False
        try:
            action = data.get("action")
            if action == "install":
                install_docker()
            elif action == "prune":
                sys.stdin = MockStdin(["y"])
                docker_system_cleanup()
            elif action == "list":
                res = run(["docker", "ps", "-a", "--format", "{{.Names}}|{{.Image}}|{{.Status}}"])
                if res.returncode == 0:
                    print(res.stdout)
            elif action in ["start", "stop", "restart", "rm"]:
                target = data.get("target")
                if target:
                    if action == "rm":
                        run(["docker", "rm", "-f", target])
                        ok(f"Removed {target}")
                    else:
                        run(["docker", action, target])
                        ok(f"Successfully executed {action} on {target}")
            elif action == "oneclick":
                service = data.get("service")
                if service == "1":
                    domain = data.get("domain", "")
                    dns_choice = "y" if domain else "n"
                    inputs = ["1", dns_choice]
                    if domain: inputs.append(domain)
                    inputs.append("0")
                    sys.stdin = MockStdin(inputs)
                    docker_oneclick_menu()
                elif service == "2":
                    sys.stdin = MockStdin(["2", "0"])
                    docker_oneclick_menu()
                elif service == "3":
                    pw = data.get("password", "")
                    sys.stdin = MockStdin(["3", pw, "0"])
                    docker_oneclick_menu()
            success = True
        except Exception as e:
            print(f"Error: {e}")
            traceback.print_exc()
        finally:
            self._teardown_stream(old_stdout, old_stdin, success)

    def handle_manage_post(self, path):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
        data = json.loads(post_data.decode('utf-8'))
        
        old_stdout, old_stdin = self._setup_stream()
        import sys
        
        success = False
        try:
            if path == "/api/manage/reload":
                reload_all_services()
                success = True
            elif path == "/api/manage/mariadb":
                typ = data.get("type")
                action = data.get("action")
                if typ == "database":
                    dbname = data.get("db_name", "")
                    fpath = data.get("file_path", "")
                    confirm = "yes" if data.get("confirm") else "no"
                    if action == "list":
                        mariadb_list_databases()
                    elif action == "create":
                        sys.stdin = MockStdin([dbname])
                        mariadb_create_database()
                    elif action == "drop":
                        sys.stdin = MockStdin([dbname, confirm])
                        mariadb_drop_database()
                    elif action == "inspect":
                        sys.stdin = MockStdin([dbname])
                        mariadb_inspect_database()
                    elif action == "backup":
                        sys.stdin = MockStdin([dbname, fpath])
                        mariadb_backup()
                    elif action == "restore":
                        sys.stdin = MockStdin([fpath, dbname, confirm])
                        mariadb_restore()
                elif typ == "user":
                    uname = data.get("username", "")
                    upass = data.get("password", "")
                    dbname = data.get("db_name", "")
                    if action == "list":
                        mariadb_list_users()
                    elif action == "create":
                        sys.stdin = MockStdin([uname, upass])
                        mariadb_create_user()
                    elif action == "drop":
                        sys.stdin = MockStdin([uname, "yes"])
                        mariadb_drop_user()
                    elif action == "password":
                        sys.stdin = MockStdin([uname, upass])
                        mariadb_change_password()
                    elif action == "grant":
                        sys.stdin = MockStdin([uname, dbname, "1"])
                        mariadb_grant_privileges()
                    elif action == "revoke":
                        sys.stdin = MockStdin([uname, dbname])
                        mariadb_revoke_privileges()
                success = True
            elif path == "/api/manage/service":
                svc = data.get("service")
                action = data.get("action")
                if action in ["start", "stop", "restart", "enable", "disable"] and svc:
                    run(["systemctl", action, svc])
                    ok(f"Successfully executed {action} on {svc}")
                    success = True
            elif path == "/api/manage/firewall":
                action = data.get("action")
                port = data.get("port", "")
                if action == "enable":
                    run(["ufw", "--force", "enable"])
                    ok("UFW Firewall Enabled")
                elif action == "disable":
                    run(["ufw", "disable"])
                    ok("UFW Firewall Disabled")
                elif action == "allow" and port:
                    run(["ufw", "allow", port])
                    ok(f"Port {port} allowed")
                elif action == "deny" and port:
                    run(["ufw", "delete", "allow", port])
                    run(["ufw", "deny", port])
                    ok(f"Port {port} denied")
                elif action == "list":
                    run_live(["ufw", "status", "numbered"])
                success = True
            elif path == "/api/manage/dns":
                action = data.get("action")
                domain = data.get("domain", "")
                if action == "create":
                    ztype = data.get("type", "forward")
                    ip = data.get("ip", "")
                    responses = [domain]
                    if ztype != "forward": responses.append(ip)
                    sys.stdin = MockStdin(responses)
                    if ztype == "forward": create_forward_zone()
                    elif ztype == "reverse": create_reverse_zone()
                    elif ztype == "both": create_both_zones()
                elif action == "delete":
                    # Directly delete the zone by name, bypassing the interactive
                    # pick_zones() selector which expects a numeric index, not a domain name.
                    if domain:
                        remove_zone_from_conf(domain)
                        zonefile = f"{BIND_DIR}/db.{domain}"
                        if os.path.isfile(zonefile):
                            os.remove(zonefile)
                        ok(f"{domain} removed.")
                        ok_b, msg_b = validate_bind9()
                        if not ok_b:
                            err(msg_b)
                            raise RuntimeError(msg_b)
                        reload_bind9()
                        apply_reloads()
                elif action == "test":
                    if not domain:
                        ok_b, msg_b = validate_bind9()
                        if ok_b: ok("BIND9 Configuration is valid.")
                        else: err(msg_b)
                    else:
                        sys.stdin = MockStdin([domain, "127.0.0.1", ""])
                        test_dns()
                elif action == "mx_list":
                    sys.stdin = MockStdin([domain])
                    list_mx_records()
                elif action == "mx_add":
                    mail_host = data.get("mail_host", "")
                    priority = data.get("priority", "10")
                    sys.stdin = MockStdin([domain, priority, mail_host])
                    add_mx_record()
                elif action == "mx_remove":
                    sys.stdin = MockStdin([domain, "yes"])
                    remove_mx_record()
                success = True
            elif path == "/api/manage/network":
                action = data.get("action")
                iface = data.get("iface", "")
                if action == "show":
                    run_live(["ip", "addr", "show", iface] if iface else ["ip", "addr", "show"])
                elif action == "static":
                    ip = data.get("ip", "")
                    netmask = data.get("netmask", "255.255.255.0")
                    gw = data.get("gateway", "")
                    sys.stdin = MockStdin([iface, ip, netmask, gw, "yes"])
                    set_interface_static()
                elif action == "dhcp":
                    sys.stdin = MockStdin([iface, "yes"])
                    set_interface_dhcp()
                elif action == "restore":
                    sys.stdin = MockStdin(["yes"])
                    restore_interfaces_backup()
                success = True
            elif path == "/api/manage/os":
                if data.get("action") == "fix_dpkg":
                    fix_dpkg()
                success = True
            elif path == "/api/manage/mail":
                action = data.get("action")
                if action == "add_user":
                    success = create_mail_user(data.get("mail_user", ""), data.get("mail_pass", ""))
                elif action == "delete_user":
                    success = delete_mail_user_account(data.get("mail_user", ""))
        except Exception as e:
            print(f"\n[!] Server Error: {e}")
            traceback.print_exc(file=sys.stdout)
            success = False
        finally:
            self._teardown_stream(old_stdout, old_stdin, success)

    def handle_install_post(self, path):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
        data = json.loads(post_data.decode('utf-8'))
        
        old_stdout, old_stdin = self._setup_stream()
        import sys
        
        success = False
        try:
            if path == "/api/install/mail":
                domain = data.get("domain", "")
                ip = data.get("ip") or detect_server_ip() or "127.0.0.1"
                sys.stdin = MockStdin([domain, ip])
                setup_mail_server()
                success = True
            elif path == "/api/install/mail-check":
                diagnose_mail()
                success = True
            elif path == "/api/install/mail-repair":
                success = repair_mail()
            elif path == "/api/install/ftp":
                setup_ftp_server()
                success = True
            elif path == "/api/install/ftp_user":
                uname = data.get("username", "")
                upass = data.get("password", "")
                if uname and upass:
                    run_live(["useradd", "-m", "-s", "/bin/bash", uname])
                    proc = subprocess.Popen(["chpasswd"], stdin=subprocess.PIPE, stdout=sys.stdout, stderr=sys.stdout, text=True)
                    proc.communicate(input=f"{uname}:{upass}\n")
                    ok(f"FTP user '{uname}' created.")
                    success = True
            elif path == "/api/install/pma":
                setup_phpmyadmin()
                success = True
            elif path == "/api/install/samba":
                share = data.get("share_name", "shared")
                spath = data.get("path", f"/srv/samba/{share}")
                sys.stdin = MockStdin([share, spath, "yes"])
                setup_samba()
                success = True
            elif path == "/api/install/samba_share":
                share = data.get("share_name", "shared")
                spath = data.get("path", f"/srv/samba/{share}")
                user = data.get("user", "")
                sys.stdin = MockStdin([share, spath, "no" if user else "yes"])
                setup_samba()
                if user:
                    ok(f"Samba share '{share}' created. Make sure to run 'smbpasswd -a {user}' via SSH.")
                success = True
            elif path == "/api/uninstall/mail":
                sys.stdin = MockStdin(["yes"])
                delete_mail_services()
                success = True
            elif path == "/api/uninstall/ftp":
                sys.stdin = MockStdin(["yes"])
                delete_ftp_server()
                success = True
            elif path == "/api/uninstall/pma":
                sys.stdin = MockStdin(["yes"])
                delete_phpmyadmin()
                success = True
            elif path == "/api/uninstall/samba":
                sys.stdin = MockStdin(["yes"])
                delete_samba()
                success = True
        except Exception as e:
            print(f"\n[!] Server Error: {e}")
            traceback.print_exc(file=sys.stdout)
            success = False
        finally:
            self._teardown_stream(old_stdout, old_stdin, success)

    def handle_makesite_post(self, path):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b'{}'
        data = json.loads(post_data.decode('utf-8'))

        old_stdout, old_stdin = self._setup_stream()

        site_type = path.split("/")[-1]
        success = False

        try:
            domain = data.get("domain", "")
            if "create_dns" in data:
                dns_enabled = bool(data.get("create_dns"))
            else:
                is_ip = bool(re.match(r"^\d{1,3}(\.\d{1,3}){3}$", domain))
                dns_enabled = bool(domain and not is_ip)
            ip = data.get("ip", "")
            db_pass = data.get("db_pass", "")

            # Answer bridge: prompts inside the creator consume these BY KEY
            # (via _ask), so prompt order/branching can change freely without
            # corrupting the web flow (replaces the old MockStdin sequences).
            answers = {
                "domain": domain,
                "dns_choice": "y" if dns_enabled else "n",
                "ip": ip,
                "db_pass": db_pass,
                "press_enter": "",
            }
            ssl_choice = data.get("ssl", "1")
            le_email = data.get("le_email", "")
            actual_ssl = "1" if ssl_choice == "4" else ssl_choice

            if site_type == "lamp":
                answers.update({
                    "db_name": data.get("db_name", ""),
                    "db_user": data.get("db_user", ""),
                    "ssl": actual_ssl,
                    "docroot": data.get("docroot", ""),
                    "use_site_files": "y" if (data.get("use_site_files") and data.get("site_files_path")) else "n",
                    "site_files_path": data.get("site_files_path", ""),
                    "import_sql": "y" if (data.get("import_sql") and data.get("sql_path")) else "n",
                    "sql_path": data.get("sql_path", ""),
                })
                with _web_prompt_driver(answers):
                    create_lamp_site()
                if ssl_choice == "4":
                    _ensure_certbot("apache")
                    step("Running Certbot for Let's Encrypt...")
                    r = run(["certbot", "--apache", "-d", domain, "-d", f"www.{domain}", "--non-interactive", "--agree-tos", "-m", le_email, "--redirect"], timeout=600)
                    if r.returncode == 0: ok(f"SSL certificate issued for {domain}.")
                    else: err(f"Certbot failed: {r.stderr}")
                success = True

            elif site_type == "lemp":
                answers.update({
                    "db_name": data.get("db_name", ""),
                    "db_user": data.get("db_user", ""),
                    "ssl": actual_ssl,
                    "docroot": data.get("docroot", ""),
                    "use_site_files": "y" if (data.get("use_site_files") and data.get("site_files_path")) else "n",
                    "site_files_path": data.get("site_files_path", ""),
                    "import_sql": "y" if (data.get("import_sql") and data.get("sql_path")) else "n",
                    "sql_path": data.get("sql_path", ""),
                })
                with _web_prompt_driver(answers):
                    create_lemp_site()
                if ssl_choice == "4":
                    _ensure_certbot("nginx")
                    step("Running Certbot for Let's Encrypt...")
                    r = run(["certbot", "--nginx", "-d", domain, "-d", f"www.{domain}", "--non-interactive", "--agree-tos", "-m", le_email, "--redirect"], timeout=600)
                    if r.returncode == 0: ok(f"SSL certificate issued for {domain}.")
                    else: err(f"Certbot failed: {r.stderr}")
                success = True

            elif site_type == "wordpress":
                # db_name/db_user come through empty so the creator applies
                # its own generated defaults (same as the old ["", "", pass].
                answers["db_name"] = data.get("db_name", "")
                answers["db_user"] = data.get("db_user", "")
                server = data.get("server", "apache")
                with _web_prompt_driver(answers):
                    if server == "apache":
                        create_apache_wordpress_site()
                    else:
                        create_nginx_wordpress_site()
                success = True

            elif site_type == "static":
                server = data.get("server", "apache")
                answers.update({
                    "ssl": actual_ssl,
                    "cert_path": data.get("cert_path", ""),
                    "key_path": data.get("key_path", ""),
                    "docroot": data.get("docroot", ""),
                    "use_site_files": "y" if (data.get("use_site_files") and data.get("site_files_path")) else "n",
                    "site_files_path": data.get("site_files_path", ""),
                })
                with _web_prompt_driver(answers):
                    if server == "apache":
                        create_full_site()
                    else:
                        create_nginx_full_site()
                if ssl_choice == "4":
                    _ensure_certbot(server)
                    step("Running Certbot for Let's Encrypt...")
                    r = run(["certbot", f"--{server}", "-d", domain, "-d", f"www.{domain}", "--non-interactive", "--agree-tos", "-m", le_email, "--redirect"], timeout=600)
                    if r.returncode == 0: ok(f"SSL certificate issued for {domain}.")
                    else: err(f"Certbot failed: {r.stderr}")
                success = True

            elif site_type == "proxy":
                server = data.get("server", "apache")
                answers["backend"] = data.get("backend", "")
                with _web_prompt_driver(answers):
                    if server == "apache":
                        create_apache_reverse_proxy()
                    else:
                        create_nginx_reverse_proxy()
                success = True

            elif site_type == "docker":
                answers["image"] = data.get("image", "")
                answers["port"] = str(data.get("port", "80") or "80")
                answers["dns_choice"] = "y" if data.get("dns") else "n"
                with _web_prompt_driver(answers):
                    create_container_site()
                success = True

        except Exception as e:
            print(f"\n[!] Server Error: {e}")
            traceback.print_exc(file=sys.stdout)
            success = False
        finally:
            self._teardown_stream(old_stdout, old_stdin, success, data.get("domain", ""))

class _WebServer(socketserver.ThreadingTCPServer):
    """Multi-threaded TCP server. SO_REUSEPORT intentionally removed (Phase 0):
    a stale instance must not silently share the port with a new one."""
    allow_reuse_address = True

    def server_bind(self):
        # SO_REUSEPORT deliberately NOT set here — see plan.md §5.6 Phase 0.
        # allow_reuse_address (SO_REUSEADDR) is sufficient and does not let
        # two live processes share the same port simultaneously.
        super().server_bind()

def _find_free_port(preferred, max_tries=10):
    """Return *preferred* if available, else the next free port up to preferred+max_tries.
    Ports reserved for Apache are never returned."""
    for port in range(preferred, preferred + max_tries):
        if port in APACHE_BACKEND_PORTS:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))
            return port
        except OSError:
            continue
    return None

def _warn_if_apache_port_blocked(ports_conf="/etc/apache2/ports.conf"):
    """Apache is configured to Listen on 8080. If some other process already owns that
    port, Apache fails with "(98)Address already in use" — say so, and say who it is."""
    try:
        if not os.path.exists(ports_conf):
            return
        with open(ports_conf, "r") as f:
            if not re.search(r"^\s*Listen\s+(?:\S+:)?8080\b", f.read(), re.MULTILINE):
                return
        out = subprocess.run(["ss", "-H", "-ltnp", "sport = :8080"],
                             capture_output=True, text=True, timeout=5).stdout
        owners = re.findall(r'users:\(\("([^"]+)",pid=(\d+)', out)
        others = [(n, p) for n, p in owners if n not in ("apache2", "httpd")]
        if others:
            who = ", ".join(f"{n} (pid {p})" for n, p in others)
            warn(f"Port 8080 is in use by {who}.")
            warn("Apache is configured to listen on 8080, so it will not start until that process is stopped.")
            warn("Find it with:  ss -ltnp 'sport = :8080'    stop it with:  kill <pid>")
    except Exception:
        pass            # purely advisory — never block startup

def start_web_ui():
    # ── Phase 0: Single-instance lock ────────────────────────────────────────
    if not acquire_instance_lock():
        err("Another instance of Zervermanager is already running. "
            "If this is wrong, remove /var/lib/zervermanager/lock/instance.lock")
        sys.exit(1)

    # ── Phase 5: Startup journal recovery ────────────────────────────────────
    _startup_journal_recovery()
    if is_safe_mode():
        warn("SAFE MODE: an interrupted transaction was detected. "
             "Only read-only and recovery routes are available until it is resolved.")

    # ── Phase 0: Startup self-check (lint) ───────────────────────────────────
    startup_selfcheck()

    # ── Phase 3: Start watchdog thread ───────────────────────────────────────
    watchdog_start()

    # ── Phase 6: Prune old trash entries ─────────────────────────────────────
    try:
        trash_purge()
    except Exception:
        pass

    # ── Phase 0: Auth init ───────────────────────────────────────────────────
    if not auth_is_configured():
        warn("No admin credentials configured. Setting up first-time password...")
        import getpass
        pw = getpass.getpass("  Set admin password for the Web UI: ")
        if pw:
            auth_init(pw)
            ok("Admin credentials saved to /etc/zervermanager/auth.json")
        else:
            warn("No password entered — Web UI will start but login will be required. "
                 "Run: python3 zervermanager.py auth reset")

    # ── Port & bind address ──────────────────────────────────────────────────
    PREFERRED_PORT = WEB_UI_DEFAULT_PORT
    _env_port = os.environ.get("ZM_PORT", "").strip()
    if _env_port.isdigit():
        if int(_env_port) in APACHE_BACKEND_PORTS:
            warn(f"ZM_PORT={_env_port} is reserved for Apache — using {WEB_UI_DEFAULT_PORT} instead.")
        else:
            PREFERRED_PORT = int(_env_port)

    # Default: bind to all interfaces so the Web UI is reachable from the
    # admin's browser. Auth (session + CSRF) is now enforced on all routes.
    # Use --listen 127.0.0.1 to restrict to loopback only.
    LISTEN_ADDR = "0.0.0.0"
    if "--listen" in sys.argv:
        idx = sys.argv.index("--listen")
        if idx + 1 < len(sys.argv):
            LISTEN_ADDR = sys.argv[idx + 1]
    if LISTEN_ADDR == "127.0.0.1":
        warn("Web UI bound to 127.0.0.1 — only accessible from this machine.")

    # Register manager port as protected
    _PROTECTED_PORTS.add(PREFERRED_PORT)

    _warn_if_apache_port_blocked()
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'web_config')
    os.makedirs(web_dir, exist_ok=True)

    PORT = _find_free_port(PREFERRED_PORT)
    if PORT is None:
        err(f"No free port found in range {PREFERRED_PORT}-{PREFERRED_PORT + 9}.")
        err("Stop any service using those ports and try again.")
        sys.exit(1)

    try:
        httpd = _WebServer((LISTEN_ADDR, PORT), WebUIHandler)
    except OSError as exc:
        err(f"Cannot bind to {LISTEN_ADDR}:{PORT}: {exc}")
        sys.exit(1)

    ip = detect_server_ip() or "127.0.0.1"
    display_addr = "127.0.0.1" if LISTEN_ADDR == "127.0.0.1" else ip
    if PORT != PREFERRED_PORT:
        warn(f"Port {PREFERRED_PORT} was busy — using port {PORT} instead.")
    print(f"\n  {C.GREEN}\u2713 Web Configuration started.{C.RESET}")
    print(f"  {C.CYAN}Please open your browser to: http://{display_addr}:{PORT}{C.RESET}")
    if not auth_is_configured():
        print(f"  {C.YELLOW}  WARNING: No admin credentials — login is required.{C.RESET}")
    if is_safe_mode():
        print(f"  {C.RED}  SAFE MODE ACTIVE — recovery only.{C.RESET}")
    print(f"  {C.DIM}Press Ctrl+C to stop the web server and exit.{C.RESET}\n")
    with httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print(f"\n  {C.YELLOW}Stopping Web UI...{C.RESET}")
            watchdog_stop()
            httpd.shutdown()
            sys.exit(0)


# ─────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────

def main():
    global DRY_RUN

    # ── CLI flags (parsed before root check so --help-style works too) ──
    if "--dry-run" in sys.argv:
        DRY_RUN = True
        warn("DRY-RUN mode active — no commands will be executed.")

    # ── Phase 0: auth reset CLI shortcut ────────────────────────────────────
    if len(sys.argv) >= 3 and sys.argv[1] == "auth" and sys.argv[2] == "reset":
        import getpass
        pw = getpass.getpass("  New admin password: ")
        cli_auth_reset(pw)
        sys.exit(0)

    # ── Phase 2/9: snapshot CLI ──────────────────────────────────────────────
    if len(sys.argv) >= 2 and sys.argv[1] == "snapshot":
        sub = sys.argv[2] if len(sys.argv) > 2 else "list"
        if sub == "list": cli_snapshot_list()
        elif sub == "create": cli_snapshot_create(" ".join(sys.argv[3:]))
        elif sub == "diff" and len(sys.argv) > 3: cli_snapshot_diff(sys.argv[3])
        elif sub == "restore" and len(sys.argv) > 3:
            dry = "--dry-run" in sys.argv
            cli_snapshot_restore(sys.argv[3], dry_run=dry)
        sys.exit(0)

    # ── Phase 0/8: audit CLI ─────────────────────────────────────────────────
    if len(sys.argv) >= 2 and sys.argv[1] == "audit":
        n = int(sys.argv[3]) if len(sys.argv) > 3 else 50
        cli_audit_tail(n)
        sys.exit(0)

    # ── Phase 5/9: doctor CLI ────────────────────────────────────────────────
    if len(sys.argv) >= 2 and sys.argv[1] == "doctor":
        fix = "--fix" in sys.argv
        only_arg = [a for a in sys.argv[2:] if not a.startswith("-")]
        cli_doctor(only_arg or None, fix=fix)
        sys.exit(0)

    # ── Phase 0: safe-mode CLI ───────────────────────────────────────────────
    if len(sys.argv) >= 3 and sys.argv[1] == "safe-mode":
        if sys.argv[2] == "on": safe_mode_on(); print("  Safe mode ON.")
        else: safe_mode_off(); print("  Safe mode OFF.")
        sys.exit(0)

    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        print("This script must be run as root on Linux. Use sudo.")
        sys.exit(1)

    # Ensure full system PATH so dpkg/apt/systemctl can find sbin tools
    sbin_paths = ["/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin"]
    current_path = os.environ.get("PATH", "")
    for p in sbin_paths:
        if p not in current_path.split(":"):
            current_path = p + ":" + current_path
    os.environ["PATH"] = current_path

    os.environ["DEBIAN_FRONTEND"] = "noninteractive"

    if SHOW_SPLASH:
        show_splash()

    show_loading_screen()

    print("\n  " + C.BOLD + "How would you like to use Zervermanager?" + C.RESET)
    print("  1. Web Configuration UI (Recommended)")
    print("  2. Command Line Interface (CLI)")
    while True:
        ui_choice = input("  Choice [1]: ").strip() or "1"
        if ui_choice in ("1", "2"):
            break
        warn("Please enter 1 or 2.")
        
    if ui_choice == "1":
        start_web_ui()
        return

    while True:
        choice = display_main_menu()

        if EASTER_EGG_ENABLED and choice == "0831":
            show_mai_easter_egg()
            continue

        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            make_site_menu()
        elif choice == "2":
            mariadb_menu()
        elif choice == "3":
            make_services_menu()
        elif choice == "4":
            manage_server_menu()
        elif choice == "5":
            reload_all_services()
        elif choice == "6":
            show_help()
        elif choice == "7":
            docker_menu()
        elif choice == "0":
            _shutdown()
        else:
            warn("Invalid choice.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  [!] Exiting gracefully...")
        sys.exit(0)
    except Exception as e:
        err(f"An unexpected error occurred: {e}")
        traceback.print_exc()
        sys.exit(1)
