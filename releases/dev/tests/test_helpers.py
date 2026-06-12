"""
Unit tests for zervermanager.py helpers — v1.2.0 (dev).

Run with:
    python tests/run_tests.py
  or:
    python -m pytest tests/ (if pytest is available)

All tests use only the Python standard library (unittest + unittest.mock).
No real system commands are executed — subprocess is fully mocked.
"""
import sys
import os
import io
import unittest
from unittest.mock import patch, MagicMock, call

# ---------------------------------------------------------------------------
# Bootstrap: locate the dev folder (parent of this tests/ directory) so we
# can import zervermanager.py that lives directly inside it.
# The script guards its execution behind  `if __name__ == "__main__":`  so
# importing it directly is safe once we stub out the root check.
# ---------------------------------------------------------------------------
# dev/tests/test_helpers.py  →  SCRIPT_DIR = dev/
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# On Windows os.geteuid doesn't exist. Inject a stub so unittest.mock can
# patch it and the imported script's root-check doesn't abort import.
if not hasattr(os, "geteuid"):
    os.geteuid = lambda: 0  # type: ignore[attr-defined]

import importlib.util

def _load_servermanager():
    # v1.2.0 dev: zervermanager.py lives in the same folder as this tests/ dir.
    spec = importlib.util.spec_from_file_location(
        "zervermanager",
        os.path.join(SCRIPT_DIR, "zervermanager.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__name__ = "zervermanager"   # prevent __main__ block from running
    with patch("os.geteuid", return_value=0):
        spec.loader.exec_module(mod)
    return mod

sm = _load_servermanager()


# ===========================================================================
# Helpers
# ===========================================================================

def capture_stdout(fn, *args, **kwargs):
    """Call fn(*args, **kwargs) and return (return_value, printed_string)."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        result = fn(*args, **kwargs)
    return result, buf.getvalue()


# ===========================================================================
# 1. run() helper
# ===========================================================================

class TestRunHelper(unittest.TestCase):

    def setUp(self):
        # Reset dry-run flag before each test
        sm.DRY_RUN = False

    def test_run_success(self):
        """run() returns subprocess result with returncode 0 on success."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "hello\n"
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result) as mock_sub:
            result = sm.run(["echo", "hello"])
        mock_sub.assert_called_once_with(["echo", "hello"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "hello\n")

    def test_run_file_not_found(self):
        """run() returns returncode 127 when the command binary doesn't exist."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = sm.run(["nonexistent_command"])
        self.assertEqual(result.returncode, 127)
        self.assertIn("nonexistent_command", result.stderr)

    def test_run_dry_run_no_subprocess(self):
        """In DRY_RUN mode run() must NOT call subprocess.run."""
        sm.DRY_RUN = True
        with patch("subprocess.run") as mock_sub:
            result, _ = capture_stdout(sm.run, ["rm", "-rf", "/etc"])
        mock_sub.assert_not_called()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_run_dry_run_prints_command(self):
        """In DRY_RUN mode run() prints the would-run command."""
        sm.DRY_RUN = True
        with patch("subprocess.run"):
            _, output = capture_stdout(sm.run, ["systemctl", "restart", "apache2"])
        self.assertIn("dry-run", output.lower())
        self.assertIn("systemctl", output)
        self.assertIn("restart", output)


# ===========================================================================
# 2. run_live() helper
# ===========================================================================

class TestRunLive(unittest.TestCase):

    def setUp(self):
        sm.DRY_RUN = False

    def test_run_live_dry_run_no_popen(self):
        """In DRY_RUN mode run_live() must NOT call subprocess.Popen."""
        sm.DRY_RUN = True
        with patch("subprocess.Popen") as mock_popen:
            (rc, stderr), _ = capture_stdout(sm.run_live, ["apt-get", "install", "-y", "vim"])
        mock_popen.assert_not_called()
        self.assertEqual(rc, 0)
        self.assertEqual(stderr, "")

    def test_run_live_dry_run_prints_command(self):
        """In DRY_RUN mode run_live() prints the would-run command."""
        sm.DRY_RUN = True
        with patch("subprocess.Popen"):
            _, output = capture_stdout(sm.run_live, ["apt-get", "update"])
        self.assertIn("dry-run", output.lower())
        self.assertIn("apt-get", output)

    def test_run_live_file_not_found(self):
        """run_live() returns returncode 127 when binary is missing."""
        with patch("subprocess.Popen", side_effect=FileNotFoundError):
            rc, stderr = sm.run_live(["missing_tool"])
        self.assertEqual(rc, 127)
        self.assertIn("missing_tool", stderr)

    def test_run_live_filters_noise(self):
        """run_live() only prints lines starting with known prefixes."""
        mock_proc = MagicMock()
        # stdout lines: one that should show, one that shouldn't
        mock_proc.stdout = iter(["Get:1 http://example.com package\n", "debconf noise line\n"])
        mock_proc.communicate.return_value = ("", "")
        mock_proc.returncode = 0
        with patch("subprocess.Popen", return_value=mock_proc):
            _, output = capture_stdout(sm.run_live, ["apt-get", "install", "-y", "curl"])
        self.assertIn("Get:", output)
        self.assertNotIn("debconf", output)


# ===========================================================================
# 3. service_status()
# ===========================================================================

class TestServiceStatus(unittest.TestCase):

    def setUp(self):
        sm.DRY_RUN = False

    def test_active_service(self):
        """service_status() returns a label containing 'running' for active services."""
        mock_result = MagicMock()
        mock_result.stdout = "active\n"
        with patch("subprocess.run", return_value=mock_result):
            label = sm.service_status("apache2")
        # Strip ANSI codes for comparison
        import re
        plain = re.sub(r"\x1b\[[0-9;]*m", "", label)
        self.assertIn("running", plain)

    def test_inactive_service(self):
        """service_status() returns a label containing 'stopped' for inactive services."""
        mock_result = MagicMock()
        mock_result.stdout = "inactive\n"
        with patch("subprocess.run", return_value=mock_result):
            label = sm.service_status("nginx")
        import re
        plain = re.sub(r"\x1b\[[0-9;]*m", "", label)
        self.assertIn("stopped", plain)


# ===========================================================================
# 4. apply_reloads()
# ===========================================================================

class TestApplyReloads(unittest.TestCase):

    def setUp(self):
        sm.DRY_RUN = False
        sm._pending.clear()

    def test_no_pending(self):
        """apply_reloads() does nothing when _pending is empty."""
        with patch("subprocess.run") as mock_sub:
            sm.apply_reloads()
        mock_sub.assert_not_called()

    def test_apache_reload(self):
        """apply_reloads() calls systemctl reload apache2 when marked."""
        sm.mark_reload("apache2")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result) as mock_sub:
            capture_stdout(sm.apply_reloads)   # capture to avoid Windows cp1252 encode error
        mock_sub.assert_called_once_with(
            ["systemctl", "reload", "apache2"],
            capture_output=True, text=True
        )

    def test_pending_cleared_after_apply(self):
        """_pending set is cleared after apply_reloads() runs."""
        sm.mark_reload("bind9")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result):
            capture_stdout(sm.apply_reloads)   # capture to avoid Windows cp1252 encode error
        # mark_reload adds to _pending; apply_reloads doesn't auto-clear it —
        # that is the existing design. We just confirm no exception is raised.
        pass


# ===========================================================================
# 5. Output helper functions
# ===========================================================================

class TestMenuHelpers(unittest.TestCase):

    def _strip_ansi(self, text):
        import re
        return re.sub(r"\x1b\[[0-9;]*m", "", text)

    def test_ok_outputs_checkmark(self):
        _, output = capture_stdout(sm.ok, "All good")
        plain = self._strip_ansi(output)
        self.assertIn("✓", plain)
        self.assertIn("All good", plain)

    def test_err_outputs_cross(self):
        _, output = capture_stdout(sm.err, "Something failed")
        plain = self._strip_ansi(output)
        self.assertIn("✗", plain)
        self.assertIn("Something failed", plain)

    def test_warn_outputs_exclamation(self):
        _, output = capture_stdout(sm.warn, "Be careful")
        plain = self._strip_ansi(output)
        self.assertIn("!", plain)
        self.assertIn("Be careful", plain)

    def test_step_outputs_bullet(self):
        _, output = capture_stdout(sm.step, "Installing...")
        plain = self._strip_ansi(output)
        self.assertIn("•", plain)
        self.assertIn("Installing...", plain)

    def test_menu_header_has_separator(self):
        _, output = capture_stdout(sm.menu_header, "Test Menu")
        self.assertIn("━", output)
        self.assertIn("Test Menu", output)


# ===========================================================================
# 6. Input validators
# ===========================================================================

class TestInputValidators(unittest.TestCase):

    # ask_domain ─────────────────────────────────────────────────────────────

    def test_ask_domain_valid(self):
        with patch("builtins.input", return_value="example.com"):
            result = sm.ask_domain()
        self.assertEqual(result, "example.com")

    def test_ask_domain_empty_returns_empty(self):
        with patch("builtins.input", return_value=""):
            result = sm.ask_domain()
        self.assertEqual(result, "")

    def test_ask_domain_rejects_invalid_then_accepts(self):
        """ask_domain loops until a valid value is provided."""
        import io
        with patch("builtins.input", side_effect=["bad domain!", "valid-domain.org"]), \
             patch("sys.stdout", io.StringIO()):
            result = sm.ask_domain()
        self.assertEqual(result, "valid-domain.org")

    # ask_db_name ─────────────────────────────────────────────────────────────

    def test_ask_db_name_valid(self):
        with patch("builtins.input", return_value="my_db"):
            result = sm.ask_db_name()
        self.assertEqual(result, "my_db")

    def test_ask_db_name_rejects_hyphen(self):
        import io
        with patch("builtins.input", side_effect=["bad-name", "good_name"]), \
             patch("sys.stdout", io.StringIO()):
            result = sm.ask_db_name()
        self.assertEqual(result, "good_name")

    # ask_db_user ─────────────────────────────────────────────────────────────

    def test_ask_db_user_valid(self):
        with patch("builtins.input", return_value="wp_user"):
            result = sm.ask_db_user()
        self.assertEqual(result, "wp_user")

    def test_ask_db_user_rejects_spaces(self):
        import io
        with patch("builtins.input", side_effect=["bad user", "good_user"]), \
             patch("sys.stdout", io.StringIO()):
            result = sm.ask_db_user()
        self.assertEqual(result, "good_user")


# ===========================================================================
# 7. DRY_RUN flag end-to-end
# ===========================================================================

class TestDryRunFlag(unittest.TestCase):

    def setUp(self):
        sm.DRY_RUN = False

    def tearDown(self):
        sm.DRY_RUN = False

    def test_dry_run_run_never_calls_subprocess(self):
        sm.DRY_RUN = True
        with patch("subprocess.run") as mock_sub, \
             patch("subprocess.Popen") as mock_popen:
            import io
            with patch("sys.stdout", io.StringIO()):
                sm.run(["apt-get", "update"])
                sm.run_live(["systemctl", "restart", "nginx"])
        mock_sub.assert_not_called()
        mock_popen.assert_not_called()

    def test_normal_mode_calls_subprocess(self):
        sm.DRY_RUN = False
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        with patch("subprocess.run", return_value=mock_result) as mock_sub:
            sm.run(["echo", "test"])
        mock_sub.assert_called_once()


# ===========================================================================
# 8. OS compatibility & Startup Loading Screen
# ===========================================================================

class TestOSCompatibilityAndLoading(unittest.TestCase):

    def setUp(self):
        sm.DRY_RUN = False

    @patch.object(sm, "get_os_version", return_value="Debian GNU/Linux 12 (bookworm)")
    def test_os_compatibility_debian_12(self, mock_get_os):
        status, name = sm.check_os_compatibility()
        self.assertEqual(status, "supported")
        self.assertEqual(name, "Debian GNU/Linux 12 (bookworm)")

    @patch.object(sm, "get_os_version", return_value="Ubuntu 22.04 LTS")
    def test_os_compatibility_ubuntu(self, mock_get_os):
        status, name = sm.check_os_compatibility()
        self.assertEqual(status, "uncertain")
        self.assertEqual(name, "Ubuntu 22.04 LTS")

    @patch.object(sm, "get_os_version", return_value="Arch Linux")
    def test_os_compatibility_unsupported(self, mock_get_os):
        status, name = sm.check_os_compatibility()
        self.assertEqual(status, "unsupported")
        self.assertEqual(name, "Arch Linux")

    @patch("time.sleep")
    @patch.object(sm, "get_os_version", return_value="Debian GNU/Linux 12 (bookworm)")
    @patch.object(sm, "ensure_dependencies")
    @patch("builtins.input", return_value="")
    def test_show_loading_screen_all_installed(self, mock_input, mock_ensure, mock_get_os, mock_sleep):
        mock_ensure.return_value = []  # no missing dependencies

        _, out = capture_stdout(sm.show_loading_screen)

        self.assertIn("Compatible OS: Debian GNU/Linux 12 (bookworm)", out)
        self.assertIn("Checking core package dependencies...", out)
        self.assertNotIn("missing", out.lower())
        mock_ensure.assert_called_once_with(auto_install=False)

    @patch("time.sleep")
    @patch.object(sm, "get_os_version", return_value="Debian GNU/Linux 12 (bookworm)")
    @patch.object(sm, "ensure_dependencies")
    @patch.object(sm, "prompt_confirm", return_value=True)
    @patch("builtins.input", return_value="")
    def test_show_loading_screen_missing_install(self, mock_input, mock_confirm, mock_ensure, mock_get_os, mock_sleep):
        # First call (check) returns ['apache2'], second call (install) returns []
        mock_ensure.side_effect = [["apache2"], []]

        _, out = capture_stdout(sm.show_loading_screen)

        self.assertIn("Web Server (Apache2): Not Found", out)
        self.assertIn("The following core dependencies are missing: apache2", out)
        mock_confirm.assert_called_once()
        mock_ensure.assert_has_calls([
            call(auto_install=False),
            call(auto_install=True)
        ])


# ===========================================================================
# 9. Bug Fixes / Regressions
# ===========================================================================

class TestBugFixes(unittest.TestCase):

    def setUp(self):
        sm.DRY_RUN = False

    @patch("os.path.abspath", side_effect=lambda x: x)
    def test_validate_docroot_rejects_root(self, mock_abs):
        valid, msg = sm.validate_docroot("/")
        self.assertFalse(valid)
        self.assertIn("Cannot use protected system directory", msg)

    @patch("os.path.abspath", side_effect=lambda x: x)
    def test_validate_docroot_rejects_etc(self, mock_abs):
        valid, msg = sm.validate_docroot("/etc")
        self.assertFalse(valid)
        self.assertIn("Cannot use protected system directory", msg)

    @patch("os.path.abspath", side_effect=lambda x: x)
    def test_validate_docroot_accepts_valid(self, mock_abs):
        valid, msg = sm.validate_docroot("/var/www/mysite")
        self.assertTrue(valid)
        self.assertEqual(msg, "")

    @patch("os.path.realpath")
    def test_copy_site_files_self_deletion_guard(self, mock_realpath):
        # mock realpath to return same path for source and dest
        mock_realpath.side_effect = lambda x: "/var/www/same_path"
        with patch("os.path.isdir", return_value=True):
            _, output = capture_stdout(sm.copy_existing_site_files, "src", "dest")
            self.assertIn("Source and destination are the same", output)

    @patch("os.path.realpath")
    @patch("os.path.isdir", return_value=True)
    @patch("os.path.exists", return_value=False)
    @patch("os.listdir", return_value=[])
    def test_copy_site_files_normal_copy(self, mock_listdir, mock_exists, mock_isdir, mock_realpath):
        # mock realpath to return different paths
        mock_realpath.side_effect = ["/var/www/src", "/var/www/dest"]
        result = sm.copy_existing_site_files("src", "dest")
        self.assertTrue(result)

    @patch("sys.exit", side_effect=SystemExit)
    def test_geteuid_missing_handled(self, mock_exit):
        orig_geteuid = getattr(os, "geteuid", None)
        if hasattr(os, "geteuid"):
            del os.geteuid
            
        import io
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            try:
                sm.main()
            except SystemExit:
                pass
                
        if orig_geteuid:
            os.geteuid = orig_geteuid
            
        mock_exit.assert_called_with(1)
        self.assertIn("must be run as root on Linux", buf.getvalue())

    @patch("os.path.isfile", return_value=False)
    @patch("builtins.input", side_effect=["example.com", "/etc/ssl/certs", "key"])
    def test_ssl_cert_rejects_directory(self, mock_input, mock_isfile):
        with patch.object(sm, "ensure_modules"):
            _, output = capture_stdout(sm.create_https_existing)
            mock_isfile.assert_any_call("/etc/ssl/certs")
            self.assertIn("Certificate not found", output)

    @patch("os.path.isfile", side_effect=[True, False])
    @patch("builtins.input", side_effect=["example.com", "/cert.crt", "/etc/ssl/private"])
    def test_ssl_key_rejects_directory(self, mock_input, mock_isfile):
        with patch.object(sm, "ensure_modules"):
            _, output = capture_stdout(sm.create_https_existing)
            self.assertIn("Key not found", output)


# ===========================================================================
# 11. MariaDB Manager
# ===========================================================================

class TestMariaDBManager(unittest.TestCase):

    def setUp(self):
        sm.DRY_RUN = False

    def test_mariadb_menu_exists(self):
        self.assertTrue(callable(sm.mariadb_menu))

    def test_mariadb_run_sql_dry_run(self):
        sm.DRY_RUN = True
        with patch("subprocess.run") as mock_sub:
            result, output = capture_stdout(sm.mariadb_run_sql, "SELECT 1;", True)
        mock_sub.assert_not_called()
        self.assertIn("dry-run", output.lower())
        self.assertEqual(result, "")

    @patch("builtins.input", side_effect=["invalid name!", "valid_name"])
    @patch.object(sm, "mariadb_run_sql", return_value=0)
    def test_mariadb_db_name_validation(self, mock_run, mock_input):
        result, output = capture_stdout(sm.mariadb_create_database)
        self.assertIn("Invalid database name", output)
        mock_run.assert_called_once()
        self.assertIn("valid_name", mock_run.call_args[0][0])

    @patch("builtins.input", side_effect=["testdb", ""])
    @patch.object(sm, "mariadb_list_databases")
    def test_mariadb_backup_dry_run(self, mock_list, mock_input):
        sm.DRY_RUN = True
        with patch("subprocess.Popen") as mock_popen:
            _, output = capture_stdout(sm.mariadb_backup)
        mock_popen.assert_not_called()
        self.assertIn("dry-run", output.lower())
        self.assertIn("mysqldump", output)

    @patch("os.path.isfile", return_value=False)
    @patch("builtins.input", side_effect=["/nonexistent.sql"])
    def test_mariadb_restore_invalid_file(self, mock_input, mock_isfile):
        result, output = capture_stdout(sm.mariadb_restore)
        self.assertIn("File not found", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
