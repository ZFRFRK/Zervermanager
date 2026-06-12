#!/usr/bin/env python3

import os
import re
import subprocess
import json
from pathlib import Path
from datetime import datetime
import shutil
import socket
import sys
import time
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

def prompt_for_ip(msg="Server IP"):
    auto_ip = detect_server_ip()
    prompt_str = f"{msg} [{auto_ip}]: " if auto_ip else f"{msg}: "
    val = input(prompt_str).strip()
    return val if val else auto_ip


def ask_domain(prompt="  Domain: "):
    while True:
        val = input(prompt).strip()
        if not val:
            return ""
        if re.match(r"^[a-zA-Z0-9.-]+$", val):
            return val
        print("  Invalid domain. Only letters, numbers, dots, and hyphens are allowed.")

def ask_db_name(prompt="Database name", default=""):
    prompt_str = f"  {prompt} [{default}]: " if default else f"  {prompt}: "
    while True:
        val = input(prompt_str).strip()
        val = val or default
        if not val:
            return ""
        if re.match(r"^[a-zA-Z0-9_]+$", val):
            return val
        print("  Invalid database name. Only letters, numbers, and underscores are allowed.")

def ask_db_user(prompt="Database user", default=""):
    prompt_str = f"  {prompt} [{default}]: " if default else f"  {prompt}: "
    while True:
        val = input(prompt_str).strip()
        val = val or default
        if not val:
            return ""
        if re.match(r"^[a-zA-Z0-9_]+$", val):
            return val
        print("  Invalid database user. Only letters, numbers, and underscores are allowed.")


def validate_docroot(path):
    if not path:
        return False, "Document root cannot be empty."
    import os
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
        with open(filepath, "w") as f:
            f.write(content)
        return True
    except Exception as e:
        print(f"  Error writing to {filepath}: {e}")
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


# ─────────────────────────────────────────
#  Reload tracker
# ─────────────────────────────────────────

_pending = set()

def prompt_confirm(msg, default="no"):
    """Ask a yes/no question via input() and return True/False."""
    prompt = f"  {msg} (Y/n): " if default == "yes" else f"  {msg} (y/N): "
    val = input(prompt).strip().lower()
    if not val:
        return default == "yes"
    return val in ("y", "yes")

def menu_header(title):
    """Print a standardized bold menu header with a separator."""
    global current_menu_width
    import re
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
        result = run(["systemctl", "reload", "apache2"])

        if result.returncode == 0:
            print(f"{C.GREEN}✓{C.RESET}")
        else:
            print(f"{C.RED}✗{C.RESET}")
            err(result.stderr.strip())

    if "bind9" in _pending:
        print(f"  {C.CYAN}Reloading BIND9...{C.RESET}", end=" ", flush=True)
        result = run(["systemctl", "reload", "bind9"])

        if result.returncode == 0:
            print(f"{C.GREEN}✓{C.RESET}")
        else:
            print(f"{C.RED}✗{C.RESET}")
            err(result.stderr.strip())

    if "nginx" in _pending:
        print(f"  {C.CYAN}Reloading Nginx...{C.RESET}", end=" ", flush=True)
        result = run(["systemctl", "reload", "nginx"])

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

def run(cmd):
    """Silent subprocess wrapper.  In --dry-run mode prints the command instead."""
    if DRY_RUN:
        print(f"  {C.DIM}[dry-run] Would run: {' '.join(str(c) for c in cmd)}{C.RESET}")
        return _MockProcessResult(0, "", "")
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )
    except FileNotFoundError:
        return _MockProcessResult(127, "", f"Command not found: {cmd[0]}")


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


def get_serial():
    return datetime.now().strftime("%Y%m%d01")


def service_status(name):
    result = run(["systemctl", "is-active", name])
    active = result.stdout.strip() == "active"
    label = f"{C.GREEN}running{C.RESET}" if active else f"{C.RED}stopped{C.RESET}"
    return label


# ─────────────────────────────────────────
#  Site metadata (type tracking)
# ─────────────────────────────────────────

def save_site_meta(domain, site_type, docroot, db_name=None, db_user=None, web_server="apache", dns=False):
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

    safe_write(f"{META_DIR}/{domain}.json", json.dumps(meta, indent=2))


def get_site_meta(domain):
    path = f"{META_DIR}/{domain}.json"

    if not os.path.exists(path):
        return None

    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def delete_site_meta(domain):
    path = f"{META_DIR}/{domain}.json"

    if os.path.exists(path):
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

    return f"{C.DIM}[{t}]{C.RESET}"


# ─────────────────────────────────────────
#  Apache helpers
# ─────────────────────────────────────────

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
<VirtualHost *:80>
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
<VirtualHost *:80>
    ServerName {domain}
    ServerAlias www.{domain}

    RewriteEngine On
    RewriteRule ^ https://%{{HTTP_HOST}}%{{REQUEST_URI}} [R=301,L]
</VirtualHost>

<VirtualHost *:443>
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

def find_php_fpm_socket():
    """Locate the PHP-FPM unix socket, falling back to TCP."""
    import glob
    sockets = sorted(glob.glob("/var/run/php/php*-fpm.sock"), reverse=True)
    if sockets:
        return sockets[0]
    return "127.0.0.1:9000"


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

        if os.path.exists(conf):
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
    if not os.path.exists(NAMED_CONF_LOCAL):
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
    serial = get_serial()

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

    with open(zonefile, "w") as f:
        f.write(content)


def write_reverse_zone(domain, ip, zonefile):
    serial = get_serial()
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
    if not os.path.exists(NAMED_CONF_LOCAL):
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
    if not os.path.exists(NAMED_CONF_LOCAL):
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

        if not zonefile or not os.path.exists(zonefile):
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

        if not zonefile or not os.path.exists(zonefile):
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

        if not zonefile or not os.path.exists(zonefile):
            err(f"  Zone file not found.")
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

        if clear_dest and os.path.exists(destination):
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
    if not prompt_confirm(f"{C.BOLD}Use existing site files?{C.RESET}"):
        return False

    src = input("  Path to existing site files: ").strip()

    if not src:
        warn("No path provided, using default page.")
        return False

    if not os.path.exists(src):
        err(f"Path does not exist: {src}")
        return False

    step(f"Copying files from {src} to {docroot}...")
    if copy_existing_site_files(src, docroot):
        ok("Site files copied.")
        return True
    else:
        warn("Failed to copy custom files. Default page will be used.")
        return False


# ─────────────────────────────────────────
#  Full site creation (Apache + optional DNS)
# ─────────────────────────────────────────

def create_full_site():
    menu_header("Create Full Site (Apache + optional DNS)")

    domain = ask_domain()
    if not domain:
        return

    # MODIFICATION: ask for DNS first
    dns_choice = input("  Create DNS zones for this site? (y/N): ").strip().lower()
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

    ssl_choice = input("  \nChoice: ").strip()

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
        cert = input("  Certificate path: ").strip()
        key  = input("  Key path: ").strip()

        if not os.path.isfile(cert):
            err("Certificate not found.")
            return

        if not os.path.isfile(key):
            err("Key not found.")
            return

    # ── Document root ──
    step("Creating document root...")
    create_docroot(docroot, domain)

    # Custom site files?
    custom = ask_for_custom_site_files(docroot)
    if not custom:
        # make sure at least an index.html exists (already created by create_docroot)
        pass

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
    ok("Apache vhost created.")
    reload_apache()

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
        enabled = os.path.exists(f"/etc/apache2/sites-enabled/{file}")
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

        if os.path.exists(conf):
            os.remove(conf)

        if remove_files == "yes" and os.path.exists(docroot):
            shutil.rmtree(docroot)

        # ── Remove metadata ──
        delete_site_meta(domain)

        ok(f"{domain} deleted.")

    ok_a, msg_a = validate_apache()

    if not ok_a:
        err(msg_a)
        return

    reload_apache()
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

        if delete_files == "yes" and os.path.exists(zonefile):
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

    if not os.path.exists(NAMED_CONF_LOCAL):
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

    domain = ask_domain()
    if not domain:
        return

    # MODIFICATION: ask for DNS first
    dns_choice = input("  Create DNS zones for this site? (y/N): ").strip().lower()
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

    db_pass = input("  Database password: ").strip()

    if not db_pass:
        err("Password cannot be empty.")
        return

    # ── SSL ──
    print("\nSSL options:")
    print("  1. HTTP only")
    print("  2. HTTPS (self-signed cert)")

    ssl_choice = input("  \nChoice [1]: ").strip() or "1"

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
        print(f"  DNS:       created")
    else:
        print(f"  DNS:       not created")

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

        if not os.path.exists(conf):
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

            with open(conf, "w") as f:
                f.write(updated)

            ok_a, msg_a = validate_apache()

            if not ok_a:
                with open(conf, "w") as f:
                    f.write(current)
                err(f"Apache error (reverted):\n{msg_a}")
                continue

            # Update metadata docroot
            meta = get_site_meta(domain)
            if meta:
                meta["docroot"] = new_docroot
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

            with open(conf, "w") as f:
                f.write(new_config)

            ok_a, msg_a = validate_apache()

            if not ok_a:
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
                old_rev_zone_name = None
                old_rev_file = None

                if zone_exists(domain):
                    old_fwd_file = get_zone_file(domain)
                    # Remove old forward zone entirely and create new.
                    remove_zone_from_conf(domain)
                    if old_fwd_file and os.path.exists(old_fwd_file):
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
                if old_fwd_file and os.path.exists(old_fwd_file):
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
            if not os.path.exists(src):
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

        if not os.path.exists(conf):
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

            with open(conf, "w") as f:
                f.write(updated)

            ok_a, msg_a = validate_apache()

            if not ok_a:
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

            if not os.path.exists(index_path):
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

            with open(conf, "w") as f:
                f.write(new_config)

            ok_a, msg_a = validate_apache()

            if not ok_a:
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
            if not os.path.exists(src):
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

    dns_choice = input("  Create DNS zones for this site? (y/N): ").strip().lower()
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

    ssl_choice = input("  \nChoice: ").strip()

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
        cert = input("  Certificate path: ").strip()
        key  = input("  Key path: ").strip()
        if not os.path.isfile(cert):
            err("Certificate not found.")
            return
        if not os.path.isfile(key):
            err("Key not found.")
            return

    step("Creating document root...")
    create_docroot(docroot, domain)
    ask_for_custom_site_files(docroot)

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

        if not os.path.exists(conf):
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

            with open(conf, "w") as f:
                f.write(updated)

            ok_n, msg_n = validate_nginx()
            if not ok_n:
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

            with open(conf, "w") as f:
                f.write(new_config)

            ok_n, msg_n = validate_nginx()
            if not ok_n:
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
            if not os.path.exists(src):
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

    dns_choice = input("  Create DNS zones for this site? (y/N): ").strip().lower()
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
    db_pass = input("  Database password: ").strip()

    if not db_pass:
        err("Password cannot be empty.")
        return

    print("\nSSL options:")
    print("  1. HTTP only")
    print("  2. HTTPS (self-signed cert)")
    ssl_choice = input("  \nChoice [1]: ").strip() or "1"

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

        if not os.path.exists(conf):
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

            with open(conf, "w") as f:
                f.write(updated)

            ok_n, msg_n = validate_nginx()
            if not ok_n:
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

            if not os.path.exists(index_path):
                err(f"index.php not found at {index_path}")
                continue

            new_pass = input("  New DB password: ").strip()
            if not new_pass:
                warn("Skipped.")
                continue

            with open(index_path, "r") as f:
                php = f.read()

            php_updated = re.sub(r"\$pass\s*=\s*'[^']*'", f"$pass = '{new_pass}'", php)

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

            with open(conf, "w") as f:
                f.write(new_config)

            ok_n, msg_n = validate_nginx()
            if not ok_n:
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
            if not os.path.exists(src):
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
    path = write_vhost(domain, content)
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
    input("\n  Press Enter to continue...")


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
    input("\n  Press Enter to continue...")


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
    
    dns_choice = input("  Create DNS zones for this site? (y/N): ").strip().lower()
    create_dns = (dns_choice == "y")

    ip = None
    if create_dns:
        ip = prompt_for_ip("Server IP")
        if not ip:
            create_dns = False

    target = input("  Proxy target [localhost:3000]: ").strip()
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
    input("\n  Press Enter to continue...")


def create_nginx_reverse_proxy():
    menu_header("Create Reverse Proxy Site (Nginx)")
    domain = ask_domain()
    if not domain: return

    dns_choice = input("  Create DNS zones for this site? (y/N): ").strip().lower()
    create_dns = (dns_choice == "y")

    ip = None
    if create_dns:
        ip = prompt_for_ip("Server IP")
        if not ip:
            create_dns = False
    target = input("  Proxy target [localhost:3000]: ").strip()
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
        print(f"  DNS:       created")
    input("\n  Press Enter to continue...")


def reverse_proxy_apache_menu():
    while True:
        menu_header("Reverse Proxy (Apache)")
        print("1. Create reverse proxy")
        print("2. Delete site")
        print("3. List sites")
        print("0. Back")
        choice = input("  \nChoice: ").strip()
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
        if choice == "1":   create_nginx_reverse_proxy()
        elif choice == "2": delete_nginx_site()
        elif choice == "3": list_nginx_sites()
        elif choice == "0": break
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
        except: pass
        try: shutil.rmtree("/tmp/wordpress", ignore_errors=True)
        except: pass

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
    domain = ask_domain()
    if not domain: return
    docroot = f"/var/www/{domain}"

    dns_choice = input("  Create DNS zones for this site? (y/N): ").strip().lower()
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
    db_pass = input("  Database password: ").strip()
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

    # Download WordPress
    if not _install_wordpress(domain, docroot, db_name, db_user, db_pass, "apache"):
        return

    # VHost
    vhost = f"""<VirtualHost *:80>
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

    # Restart related services
    step("Restarting related services...")
    run(["systemctl", "restart", "mariadb"])
    ok("MariaDB restarted.")
    run(["systemctl", "restart", "apache2"])
    ok("Apache2 restarted.")
    if create_dns:
        run(["systemctl", "restart", "bind9"])
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
    input("\n  Press Enter to continue...")


def create_nginx_wordpress_site():
    menu_header("Create WordPress Site (Nginx)")
    if not ensure_nginx():
        return
    if not ensure_mariadb():
        return
    domain = ask_domain()
    if not domain: return
    docroot = f"/var/www/{domain}"

    dns_choice = input("  Create DNS zones for this site? (y/N): ").strip().lower()
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
    db_pass = input("  Database password: ").strip()
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
            # Debian12 uses php8.2-fpm socket location
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
        run(["systemctl", "restart", "bind9"])
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
    input("\n  Press Enter to continue...")


def wordpress_apache_menu():
    while True:
        menu_header("WordPress Site (Apache)")
        print("1. Create WordPress site")
        print("2. Delete site")
        print("3. List sites")
        print("0. Back")
        choice = input("  \nChoice: ").strip()
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
    input("\n  Press Enter to continue...")


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
    input("\n  Press Enter to continue...")


def letsencrypt_apache_menu():
    while True:
        menu_header("Let's Encrypt SSL Site (Apache)")
        print("1. Create SSL site")
        print("2. Renew all certificates")
        print("3. Delete site")
        print("4. List sites")
        print("0. Back")
        choice = input("  \nChoice: ").strip()
        if choice == "1":   create_apache_letsencrypt_site()
        elif choice == "2":
            step("Renewing all certificates...")
            r = run(["certbot", "renew"])
            ok("Renewal complete.") if r.returncode == 0 else err("Renewal failed.")
            input("\n  Press Enter to continue...")
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
        if choice == "1":   create_nginx_letsencrypt_site()
        elif choice == "2":
            step("Renewing all certificates...")
            r = run(["certbot", "renew"])
            ok("Renewal complete.") if r.returncode == 0 else err("Renewal failed.")
            input("\n  Press Enter to continue...")
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
    if not os.path.exists(NETWORK_INTERFACES):
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
        f"\n  Apply changes now? Networking will restart (yes/no): "
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

    with open(NETWORK_INTERFACES, "w") as f:
        f.write(new_content)

    ok(f"{iface_name} set to DHCP.")
    _apply_networking(iface_name, backup)


def restore_interfaces_backup():
    """Restore /etc/network/interfaces from the .bak file."""
    backup = f"{NETWORK_INTERFACES}.bak"

    if not os.path.exists(backup):
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


def manage_server_ip():
    while True:
        menu_header("Manage Server IP")
        print("1. Show current IP / interface config")
        print("2. Set / change static IP")
        print("3. Set interface to DHCP")
        print("4. Restore backup (/etc/network/interfaces.bak)")
        print("0. Back")

        choice = input("  \nChoice: ").strip()

        if choice == "1":
            show_current_ip_config()
        elif choice == "2":
            set_interface_static()
        elif choice == "3":
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
    """Run dpkg --configure -a to repair any interrupted package installs."""
    info("Repairing package state (dpkg --configure -a)...")
    rc, stderr = run_live(["dpkg", "--configure", "-a"])
    if rc != 0 and stderr.strip():
        warn(f"dpkg repair note: {stderr.strip()[:200]}")


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
        if not os.path.exists(NAMED_CONF_LOCAL):
            if DRY_RUN:
                step(f"[dry-run] Would create {NAMED_CONF_LOCAL}")
            else:
                with open(NAMED_CONF_LOCAL, "w") as f:
                    f.write("// Local zones\n")
                ok("Created empty named.conf.local.")

    print()
    return []


def check_os_compatibility():
    """Return a 3-tier compatibility status for the running OS.

    Returns:
        (status, os_name)  where status is one of:
          'supported'   – Debian 12 / Bookworm  (green)
          'uncertain'   – Debian (other version), Ubuntu, or Raspbian  (yellow)
          'unsupported' – anything else  (red)
    """
    os_name = get_os_version()
    lower = os_name.lower()
    if "debian" in lower and ("12" in lower or "bookworm" in lower):
        return "supported", os_name
    elif any(kw in lower for kw in ("debian", "ubuntu", "raspbian", "raspberry")):
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
    elif status == "uncertain":
        print(f"  {C.YELLOW}[~] Supported (uncertain): {os_name}{C.RESET}")
        print(f"\n  {C.YELLOW}[!] This OS may work but has not been fully tested.{C.RESET}")
        print(f"      Some features may behave differently from Debian 12 (Bookworm).\n")
        if not prompt_confirm("Do you want to proceed anyway?", default="yes"):
            _shutdown()
    else:
        print(f"  {C.RED}[✘] Unsupported OS: {os_name}{C.RESET}")
        print(f"\n  {C.RED}[!] WARNING: This server manager is built for Debian 12 (Bookworm).{C.RESET}")
        print(f"      Running it on this OS is likely to cause configuration failures.\n")
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

def setup_mail_server():
    bold("\nSetup Mail Server (Postfix, Dovecot, Roundcube)")
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
    run(["systemctl", "reload", "apache2"]) 
    
    # Install PHP extensions needed by Roundcube
    php_packages = [
        "php", "php-imap", "php-mbstring", "php-xml",
        "php-gd", "php-intl"
    ]
    rc, err_msg = run_live(["apt-get", "install", "-y", "--no-install-recommends"] + php_packages)
    if rc != 0:
        err(f"Failed to install PHP extensions for Roundcube:\n{err_msg}")
    else:
        ok("PHP extensions for Roundcube installed.")
    
    # Debian's roundcube package automatically sets correct ownership for temp/logs directories.
    # Do NOT run chown -R on public_html, as it will break symlinks to /usr/share and cause 403 Forbidden errors!
    
    # Create Apache VirtualHost for Roundcube
    docroot = "/var/lib/roundcube/public_html"
    vhost_content = f"""<VirtualHost *:80>
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
    with open(vhost_path, "w") as f:
        f.write(vhost_content)
        
    run(["/usr/sbin/a2ensite", f"{webmail_domain}.conf"])
    run(["systemctl", "reload", "apache2"])
    ok(f"Apache VirtualHost created for {webmail_domain}")

    # Configure Postfix and Dovecot for Maildir format
    step("Configuring Postfix and Dovecot for Maildir...")
    run(["/usr/sbin/postconf", "-e", "home_mailbox = Maildir/"])
    with open("/etc/dovecot/conf.d/99-local-mail.conf", "w") as f:
        f.write("mail_location = maildir:~/Maildir\n")
        f.write("auth_username_format = %n\n")

    run(["systemctl", "restart", "postfix"])
    run(["systemctl", "restart", "dovecot"])
    ok("Postfix and Dovecot services restarted.")

    # Configure Roundcube SMTP and Domain settings
    step("Configuring Roundcube specific settings...")
    rc_conf = "/etc/roundcube/config.inc.php"
    if os.path.exists(rc_conf):
        with open(rc_conf, "a") as f:
            f.write(f"\n// Custom settings added by servermanager\n")
            f.write(f"$config['smtp_host'] = 'localhost:25';\n")
            f.write(f"$config['smtp_user'] = '';\n")
            f.write(f"$config['smtp_pass'] = '';\n")
            f.write(f"$config['username_domain'] = '{domain}';\n")
        ok("Roundcube configured for local SMTP and domain.")
    print()
    
    # ── BIND9 zones ──
    fwd_file = None
    rev_file = None
    if create_dns:
        rev_zone = reverse_zone_name(ip)
        fwd_file = f"{BIND_DIR}/db.{domain}"
        rev_file = reverse_zone_file(ip)

        # Forward zone
        if zone_exists(domain):
            warn(f"Forward zone {domain} already exists. Appending mail records...")
            with open(fwd_file, "r") as f:
                zcontent = f.read()
            zcontent = update_serial(zcontent)
            zcontent += f"\n; Mail server records\n"
            zcontent += f"mail    IN  A       {ip}\n"
            zcontent += f"@       IN  MX  10  {webmail_domain}.\n"
            with open(fwd_file, "w") as f:
                f.write(zcontent)
            ok("Mail records appended to forward zone.")
        else:
            step("Writing forward zone...")
            write_forward_zone(domain, ip, fwd_file)
            with open(fwd_file, "a") as f:
                f.write(f"\n; Mail server records\n")
                f.write(f"mail    IN  A       {ip}\n")
                f.write(f"@       IN  MX  10  {webmail_domain}.\n")
                
            ok_z, msg_z = validate_zone(domain, fwd_file)
            if not ok_z:
                os.remove(fwd_file)
                err(f"Zone error:\n{msg_z}")
            else:
                add_zone_to_conf(domain, fwd_file)
                ok("Forward zone created.")

        # Reverse zone
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
            else:
                add_zone_to_conf(rev_zone, rev_file)
                ok("Reverse zone created.")

        ok_b, msg_b = validate_bind9()
        if ok_b:
            reload_bind9()
        else:
            err(f"BIND9 error:\n{msg_b}")
            
    # Summary block similar to Apache site creation
    menu_separator()
    print(f"  {C.BOLD}Mail Domain:{C.RESET}  {domain}")
    print(f"  {C.BOLD}Webmail URL:{C.RESET}  http://{webmail_domain}")
    print(f"  {C.BOLD}Docroot:{C.RESET}      {docroot}")
    print(f"  {C.BOLD}Apache conf:{C.RESET}  {vhost_path}")
    if create_dns:
        print(f"  {C.BOLD}Forward zone:{C.RESET} {fwd_file}")
        print(f"  {C.BOLD}Reverse zone:{C.RESET} {rev_file}")
    else:
        print(f"  {C.BOLD}DNS:{C.RESET}          {C.DIM}not created{C.RESET}")
        print(f"  {C.YELLOW}Note: Don't forget to create DNS A/CNAME records for {webmail_domain} and MX records for {domain}!{C.RESET}")
    menu_separator()
    print()
    
    apply_reloads()

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

def delete_mail_user():
    menu_header("Delete Mail User")

    username = input("  Enter mail username to delete: ").strip()
    if not username:
        return

    # Basic safety check
    if username in ["root", "admin", "a2"]:
        if not prompt_confirm(f"Are you sure you want to delete {username}?"):
            return

    step(f"Deleting user {username} and their mailbox...")
    res = run(["/usr/sbin/userdel", "-r", username])
    if res.returncode == 0:
        ok(f"User {username} deleted.")
    else:
        err(f"Failed to delete user:\n{res.stderr.strip()}")

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
    
    if os.path.exists("/etc/apache2/conf-available/roundcube.conf"):
        run(["/usr/sbin/a2disconf", "roundcube"])
        run(["systemctl", "reload", "apache2"])
    
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
    print(f"  Use any system user to connect via FTP.")
    print(f"  Config: /etc/vsftpd.conf")
    input("\n  Press Enter to continue...")


def delete_ftp_server():
    menu_header("Remove FTP Server (vsftpd)")
    if not prompt_confirm("Remove vsftpd and its configuration?", "no"):
        return
    run_live(["systemctl", "stop", "vsftpd"])
    rc, _ = run_live(["apt-get", "purge", "-y", "vsftpd"])
    ok("vsftpd removed.") if rc == 0 else err("Failed to remove vsftpd.")
    run(["rm", "-f", "/etc/vsftpd.conf"])
    input("\n  Press Enter to continue...")


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
    run(["systemctl", "reload", "apache2"])

    ok("phpMyAdmin installed successfully.")
    print()
    print(f"  {C.BOLD}Access via:{C.RESET}  http://<your-server-ip>/phpmyadmin")
    print(f"  Login with your MariaDB root or site database credentials.")
    input("\n  Press Enter to continue...")


def delete_phpmyadmin():
    menu_header("Remove phpMyAdmin")
    if not prompt_confirm("Remove phpMyAdmin completely?", "no"):
        return
    rc, _ = run_live(["apt-get", "purge", "-y", "phpmyadmin"])
    ok("phpMyAdmin removed.") if rc == 0 else err("Failed to remove phpMyAdmin.")
    run(["systemctl", "reload", "apache2"])
    input("\n  Press Enter to continue...")


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
        print(f"  Add Samba users with:  smbpasswd -a <username>")
    input("\n  Press Enter to continue...")


def delete_samba():
    menu_header("Remove Samba")
    if not prompt_confirm("Remove Samba and its configuration?", "no"):
        return
    run_live(["systemctl", "stop", "smbd"])
    rc, _ = run_live(["apt-get", "purge", "-y", "samba", "samba-common"])
    ok("Samba removed.") if rc == 0 else err("Failed to remove Samba.")
    input("\n  Press Enter to continue...")


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
    print("2. Make services")
    print("3. Manage Server")
    print("4. Reload All Related Services")
    print("5. Help")
    print("0. Exit")
    menu_separator()
    print(f"  {C.CYAN}OS Detected: {get_os_version()}{C.RESET}")
    return input("  \nChoice: ").strip()


def _shutdown():
    """Centralised graceful shutdown — add cleanup tasks here as needed."""
    print(f"\n  {C.CYAN}Goodbye!{C.RESET}")
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

    show_loading_screen()

    while True:
        choice = display_main_menu()

        if not choice.isdigit():
            warn("Please enter a number.")
            continue

        if choice == "1":
            make_site_menu()
        elif choice == "2":
            make_services_menu()
        elif choice == "3":
            manage_server_menu()
        elif choice == "4":
            reload_all_services()
        elif choice == "5":
            show_help()
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
        import traceback
        print(f"\n  {C.RED}[!] An unexpected error occurred: {e}{C.RESET}")
        traceback.print_exc()
        sys.exit(1)
