// MariaDB provisioning — parity with ensure_mariadb / fix_dpkg /
// _ensure_certbot and the PHP-FPM socket detection helpers.
package creator

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// ensureMariaDB — python ensure_mariadb(): dpkg probe → fix_dpkg +
// apt install when missing.
func (f *Flow) ensureMariaDB() bool {
	if dpkgInstalled("mariadb-server") {
		return true
	}
	f.stream.info("MariaDB not found. Installing mariadb-server...")
	f.fixDpkg()
	f.runLiveTimeout(30*time.Minute, "apt-get", "update")
	rc, stderr := f.runLiveTimeout(30*time.Minute, "apt-get", "install", "-y", "--no-install-recommends",
		"mariadb-server", "mariadb-client")
	if rc != 0 {
		f.stream.err("Failed to install MariaDB: " + strings.TrimSpace(stderr))
		return false
	}
	r2 := f.run(120*time.Second, "systemctl", "enable", "--now", "mariadb")
	if r2.Exit == 0 {
		f.stream.ok("MariaDB installed and started.")
	} else {
		f.stream.err(strings.TrimSpace(r2.Stderr))
	}
	return r2.Exit == 0
}

// fixDpkg — python fix_dpkg().
func (f *Flow) fixDpkg() {
	f.stream.info("Repairing package state (dpkg --configure -a)...")
	rc, stderr := f.runLiveTimeout(30*time.Minute, "dpkg", "--configure", "-a")
	if rc != 0 && strings.TrimSpace(stderr) != "" {
		note := strings.TrimSpace(stderr)
		if len(note) > 200 {
			note = note[:200]
		}
		f.stream.warn("dpkg repair note: " + note)
	}
	f.stream.info("Running apt --fix-broken install -y...")
	f.runLiveTimeout(30*time.Minute, "apt", "--fix-broken", "install", "-y")
	f.stream.ok("OS Maintenance package repair complete.")
}

// ensureCertbot — python _ensure_certbot().
func (f *Flow) ensureCertbot(webServer string) {
	plugin := "python3-certbot-apache"
	if webServer == "nginx" {
		plugin = "python3-certbot-nginx"
	}
	if !dpkgInstalled("certbot") || !dpkgInstalled(plugin) {
		f.stream.info("Installing Certbot...")
		f.runLiveTimeout(30*time.Minute, "apt-get", "install", "-y", "certbot", plugin)
	}
}

// ─────────────────────────────────────────
//  PHP-FPM socket detection
// ─────────────────────────────────────────

// detectWebserverPhpSocket — python detect_webserver_php_socket().
func detectWebserverPhpSocket() string {
	if m := sortedReverse(glob("/run/php/php*-fpm.sock")); len(m) > 0 {
		return m[0]
	}
	return "/run/php/php8.2-fpm.sock"
}

// findPhpFpmSocket — python find_php_fpm_socket(): /var/run probe →
// /run/php probe (file check) → TCP fallback.
func findPhpFpmSocket() string {
	if m := sortedReverse(glob("/var/run/php/php*-fpm.sock")); len(m) > 0 {
		return m[0]
	}
	probe := detectWebserverPhpSocket()
	if info, err := os.Stat(probe); err == nil && info.Mode().IsRegular() {
		return probe
	}
	return "127.0.0.1:9000"
}

// phpFpmService — basename of the newest /lib/systemd/system/php*-fpm.service
// sans the ".service" suffix — empty when none found.
func phpFpmService() string {
	m := sortedReverse(glob("/lib/systemd/system/php*-fpm.service"))
	if len(m) > 0 {
		return strings.TrimSuffix(filepath.Base(m[0]), ".service")
	}
	return ""
}

// sortedReverse — sorted(glob, reverse=True).
func sortedReverse(v []string) []string {
	out := append([]string(nil), v...)
	sort.Strings(out)
	for i, j := 0, len(out)-1; i < j; i, j = i+1, j-1 {
		out[i], out[j] = out[j], out[i]
	}
	return out
}

// ─────────────────────────────────────────
//  DB naming default
// ─────────────────────────────────────────

// nonIdentRE — python re.sub(r"[^a-zA-Z0-9_]", "_", ...).
var nonIdentRE = regexp.MustCompile(`[^a-zA-Z0-9_]`)

// defaultDBName — the creators' generated default: first domain label.
func defaultDBName(domain string) string {
	label := strings.Split(domain, ".")[0]
	return nonIdentRE.ReplaceAllString(label, "_")
}

// ─────────────────────────────────────────
//  Site files + DB import (bridge mode)
// ─────────────────────────────────────────

// copyExistingSiteFiles — python copy_existing_site_files() with
// copy2 = copy preserving mtime; dirs copied recursively.
func (f *Flow) copyExistingSiteFiles(source, destination string, clearDest bool) bool {
	s := f.stream
	srcInfo, err := os.Stat(source)
	if err != nil || !srcInfo.IsDir() {
		s.err("Source directory does not exist: " + source)
		return false
	}

	realSrc, _ := filepath.EvalSymlinks(source)
	realDst, _ := filepath.EvalSymlinks(destination)
	if realSrc == realDst {
		s.err("Source and destination are the same. Cannot copy.")
		return false
	}

	if clearDest {
		if entries, err := os.ReadDir(destination); err == nil {
			for _, e := range entries {
				p := filepath.Join(destination, e.Name())
				_ = os.RemoveAll(p)
			}
		}
	} else if _, err := os.Stat(destination); err != nil {
		return false // python checks isfile(destination) — a bug it never hits
	}

	entries, err := os.ReadDir(source)
	if err != nil {
		s.err("Failed to copy site files: " + err.Error())
		return false
	}
	for _, e := range entries {
		src := filepath.Join(source, e.Name())
		dst := filepath.Join(destination, e.Name())
		if e.IsDir() {
			if err := copyTree(src, dst); err != nil {
				s.err("Failed to copy site files: " + err.Error())
				return false
			}
			continue
		}
		if err := copyFileWithMeta(src, dst); err != nil {
			s.err("Failed to copy site files: " + err.Error())
			return false
		}
	}
	return true
}

// copyTree — shutil.copytree(dirs_exist_ok=True).
func copyTree(src, dst string) error {
	entries, err := os.ReadDir(src)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(dst, 0o755); err != nil {
		return err
	}
	for _, e := range entries {
		s := filepath.Join(src, e.Name())
		d := filepath.Join(dst, e.Name())
		if e.IsDir() {
			if err := copyTree(s, d); err != nil {
				return err
			}
			continue
		}
		if e.Type()&os.ModeSymlink != 0 {
			// shutil.copytree copies the link target's content by default
			// (symlinks=False); resolve manually.
			target, err := os.Readlink(s)
			if err != nil {
				return err
			}
			if !filepath.IsAbs(target) {
				target = filepath.Join(filepath.Dir(s), target)
			}
			if info, err := os.Stat(target); err == nil && info.IsDir() {
				if err := copyTree(target, d); err != nil {
					return err
				}
				continue
			}
			if err := copyFileWithMeta(target, d); err != nil {
				return err
			}
			continue
		}
		if err := copyFileWithMeta(s, d); err != nil {
			return err
		}
	}
	return nil
}

// copyFileWithMeta — shutil.copy2(); mtime preserved.
func copyFileWithMeta(src, dst string) error {
	data, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	srcInfo, err := os.Stat(src)
	if err != nil {
		return err
	}
	if err := os.WriteFile(dst, data, srcInfo.Mode().Perm()); err != nil {
		return err
	}
	return os.Chtimes(dst, srcInfo.ModTime(), srcInfo.ModTime())
}

// askForCustomSiteFiles — python ask_for_custom_site_files() in
// bridge mode: copies nothing interactively when the answer is absent.
func (f *Flow) askForCustomSiteFiles(docroot string) (bool, error) {
	s := f.stream
	if entries, err := os.ReadDir(docroot); err == nil && len(entries) > 0 {
		s.ok("Existing site files detected in " + docroot + ".")
		return true, nil
	}

	s.WriteRaw("\n")
	use, err := f.answers.PromptConfirm(s, cBOLD+"Use existing site files?"+cRESET, "use_site_files", false)
	if err != nil {
		return false, err
	}
	if !use {
		return false, nil
	}

	src, err := f.answers.Ask(s, "  Path to existing site files: ", "site_files_path")
	if err != nil {
		return false, err
	}
	src = strings.TrimSpace(src)

	if src == "" {
		s.warn("No path provided, using default page.")
		return false, nil
	}

	if info, err := os.Stat(src); err != nil || !info.IsDir() {
		s.err("Path does not exist or is not a directory: " + src)
		return false, nil
	}

	s.step("Copying files from " + src + " to " + docroot + "...")
	if f.copyExistingSiteFiles(src, docroot, true) {
		s.ok("Site files copied.")
		return true, nil
	}
	s.warn("Failed to copy custom files. Default page will be used.")
	return false, nil
}

// promptDBImport — python prompt_db_import() in bridge mode: at most
// one bridged attempt; a retry loop would hit the python EOFError.
func (f *Flow) promptDBImport(dbName, dbUser, dbPass, docroot string) error {
	s := f.stream
	s.WriteRaw("\n")
	use, err := f.answers.PromptConfirm(s, cBOLD+"Import an existing SQL database dump?"+cRESET, "import_sql", false)
	if err != nil {
		return err
	}
	if !use {
		return nil
	}

	sqlFile, err := f.answers.Ask(s, "  SQL file path (or just filename if in "+docroot+"): ", "sql_path")
	if err != nil {
		return err
	}
	sqlFile = strings.TrimSpace(sqlFile)
	if sqlFile == "" {
		s.warn("Database import cancelled.")
		return nil
	}

	if !filepath.IsAbs(sqlFile) && !strings.HasPrefix(sqlFile, "./") {
		sqlFile = filepath.Join(docroot, sqlFile)
	}

	if info, err := os.Stat(sqlFile); err != nil || info.IsDir() {
		s.err("File does not exist: " + sqlFile)
		// python re-prompts → input() → EOFError in web mode
		return ErrEOF
	}

	s.step("Importing " + sqlFile + " into " + dbName + "...")
	s.WriteRaw("  " + cDIM + "(this may take a moment)" + cRESET)
	s.WriteRaw(" ")

	res := f.run(30*time.Minute, "mysql",
		"-u", dbUser, "-p"+dbPass, dbName, "-e", "SOURCE "+sqlFile+";")

	if res.Exit != 0 {
		s.WriteRaw(cRED + "✗" + cRESET + "\n")
		s.err("Import error:\n" + res.Stderr)
		return nil
	}

	s.WriteRaw(cGREEN + "✓" + cRESET + "\n")
	s.ok("Database imported successfully.")
	return nil
}

// runDBSteps — the LAMP/LEMP database-setup loop with the inline
// dim-label ✓/✗ verdict prints.
func (f *Flow) runDBSteps(steps [][2]string) bool {
	s := f.stream
	for _, st := range steps {
		sql, label := st[0], st[1]
		s.WriteRaw("    " + cDIM + label + "..." + cRESET)
		s.WriteRaw(" ")
		res := f.run(5*time.Minute, "mysql", "-e", sql+";")
		if res.Exit != 0 {
			s.WriteRaw(cRED + "✗" + cRESET + "\n")
			s.err("MariaDB error:\n" + res.Stderr)
			return false
		}
		s.WriteRaw(cGREEN + "✓" + cRESET + "\n")
	}
	return true
}
