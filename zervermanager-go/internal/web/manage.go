// Manage Sites actions — the Go port of python handle_site_action_post
// (zervermanager.py:11411). Enable/disable/delete/get_vhost/update_vhost/
// update_docroot/update_db_pass, all answered as 200 application/json
// {"success": bool, "message": str}.
//
// Deliberate deviation: the python handler expresses reloads through
// reload_apache()/reload_nginx(), which are mark_reload() calls — the
// pending reload only fires when a console flow later calls
// apply_reloads(). Nothing in the web request path flushes it, so on
// python the UI's Enable/Disable does not take effect until an unrelated
// console run happens. Here the reload runs immediately (is-active →
// reload, else restart, exactly the apply_reloads logic but silent),
// because the point of this port is that the buttons work.
package web

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"zervermanager/internal/run"
)

// siteActionBody mirrors the keys python reads off the POST body.
// Config/Docroot/DBPass are read fresh inside each branch (python uses
// data.get), so they live here instead.
type siteActionBody struct {
	Action        string `json:"action"`
	Domain        string `json:"domain"`
	Server        string `json:"server"`
	RemoveDocroot bool   `json:"remove_docroot"`
	Config        string `json:"config"`
	Docroot       string `json:"docroot"`
	DBPass        string `json:"db_pass"`
}

func (s *Server) handleSiteAction(w http.ResponseWriter, r *http.Request) {
	var body siteActionBody
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		// python: json.loads outside the try → 500; a 400 JSON here is
		// strictly kinder (same call as handleLogin).
		writeJSON(w, http.StatusBadRequest, []byte(`{"error":"invalid JSON body"}`))
		return
	}

	if body.Server == "" {
		body.Server = "apache" // python data.get("server", "apache")
	}

	success, message := s.runSiteAction(&body)
	writeJSON(w, http.StatusOK, siteActionJSON(success, message))
}

// runSiteAction is the try/except body of handle_site_action_post:
// err != nil → success=false, message=str(e).
func (s *Server) runSiteAction(b *siteActionBody) (bool, string) {
	success, message, err := s.siteAction(b)
	if err != nil {
		// python traceback.print_exc() goes to the daemon log.
		log.Printf("[site_action] action=%s domain=%s error: %v", b.Action, b.Domain, err)
		return false, err.Error()
	}
	return success, message
}

func (s *Server) siteAction(b *siteActionBody) (bool, string, error) {
	domain := b.Domain
	if domain == "" {
		return false, "", fmt.Errorf("Domain is required.")
	}

	dirs := s.Cfg.Sites
	confDir := dirs.NginxAvailable
	enabledDir := dirs.NginxEnabled
	if b.Server == "apache" {
		confDir = dirs.ApacheAvailable
		enabledDir = dirs.ApacheEnabled
	}
	confPath := filepath.Join(confDir, domain+".conf")
	linkPath := filepath.Join(enabledDir, domain+".conf")

	switch b.Action {
	case "enable":
		if b.Server == "apache" {
			// python enable_site(): result not checked (parity).
			_ = run.Run(60*time.Second, "/usr/sbin/a2ensite", domain+".conf")
			reloadNow("apache2")
		} else {
			if !pathExists(linkPath) && fileExists(confPath) {
				if err := os.Symlink(confPath, linkPath); err != nil {
					return false, "", err
				}
			}
			reloadNow("nginx")
		}
		return true, fmt.Sprintf("Enabled %s.", domain), nil

	case "disable":
		if b.Server == "apache" {
			_ = run.Run(60*time.Second, "/usr/sbin/a2dissite", domain+".conf")
			reloadNow("apache2")
		} else {
			if isSymlink(linkPath) {
				if err := os.Remove(linkPath); err != nil {
					return false, "", err
				}
			}
			reloadNow("nginx")
		}
		return true, fmt.Sprintf("Disabled %s.", domain), nil

	case "delete":
		meta := getSiteMeta(dirs.MetaDir, domain)
		docroot := fmt.Sprintf("/var/www/%s", domain)
		if meta != nil {
			if dr, ok := meta["docroot"].(string); ok && dr != "" {
				docroot = dr
			}
		}

		// Drop DB only for lamp/lemp sites with a full record; the mysql
		// result is not checked (python parity).
		if meta != nil && metaIsType(meta, "lamp", "lemp") {
			dbName, _ := meta["db_name"].(string)
			dbUser, _ := meta["db_user"].(string)
			if dbName != "" && dbUser != "" {
				sql := fmt.Sprintf("DROP DATABASE IF EXISTS `%s`; DROP USER IF EXISTS '%s'@'localhost'; FLUSH PRIVILEGES;", dbName, dbUser)
				_ = run.Run(60*time.Second, "mysql", "-e", sql)
			}
		}

		if b.Server == "apache" {
			_ = run.Run(60*time.Second, "/usr/sbin/a2dissite", domain+".conf")
		}
		if isSymlink(linkPath) {
			_ = os.Remove(linkPath)
		}
		if fileExists(confPath) {
			if err := os.Remove(confPath); err != nil {
				return false, "", err
			}
		}
		if b.Server == "apache" {
			reloadNow("apache2")
		} else {
			reloadNow("nginx")
		}

		if b.RemoveDocroot && pathExists(docroot) {
			if err := os.RemoveAll(docroot); err != nil {
				return false, "", err
			}
		}

		deleteSiteMeta(dirs.MetaDir, domain)
		return true, fmt.Sprintf("Deleted %s.", domain), nil

	case "get_vhost":
		if fileExists(confPath) {
			data, err := os.ReadFile(confPath)
			if err != nil {
				return false, "", err
			}
			return true, string(data), nil
		}
		return false, "", fmt.Errorf("Config file not found.")

	case "update_vhost":
		if fileExists(confPath) {
			if err := os.WriteFile(confPath, []byte(b.Config), 0o644); err != nil {
				return false, "", err
			}
			reloadEither(b.Server)
			return true, fmt.Sprintf("Updated vhost configuration for %s.", domain), nil
		}
		return false, "", fmt.Errorf("Config file not found.")

	case "update_docroot":
		newDocroot := b.Docroot
		if newDocroot == "" {
			return false, "", fmt.Errorf("New document root cannot be empty.")
		}
		meta := getSiteMeta(dirs.MetaDir, domain)
		if meta == nil {
			meta = map[string]any{}
		}
		meta["docroot"] = newDocroot

		contentBytes, err := os.ReadFile(confPath)
		if err != nil {
			return false, "", pyOSError(err, confPath)
		}
		content := string(contentBytes)
		if b.Server == "apache" {
			content = reDocumentRoot.ReplaceAllLiteralString(content, "DocumentRoot "+newDocroot)
			content = reDirectoryTag.ReplaceAllLiteralString(content, "<Directory "+newDocroot+">")
		} else {
			content = reNginxRoot.ReplaceAllLiteralString(content, "root "+newDocroot+";")
		}
		if err := os.WriteFile(confPath, []byte(content), 0o644); err != nil {
			return false, "", pyOSError(err, confPath)
		}

		if err := os.MkdirAll(newDocroot, 0o755); err != nil {
			return false, "", pyOSError(err, newDocroot)
		}

		// python quirk: update_docroot rewrites the meta JSON with
		// indent=4 while save_site_meta used indent=2. Key order is
		// Go-map random (documented deviation).
		metaBytes, err := json.MarshalIndent(meta, "", "    ")
		if err != nil {
			return false, "", err
		}
		metaPath := filepath.Join(dirs.MetaDir, domain+".json")
		if err := os.WriteFile(metaPath, metaBytes, 0o644); err != nil {
			return false, "", pyOSError(err, metaPath)
		}

		reloadEither(b.Server)
		return true, fmt.Sprintf("Updated DocumentRoot to %s.", newDocroot), nil

	case "update_db_pass":
		meta := getSiteMeta(dirs.MetaDir, domain)
		if meta == nil || !metaIsType(meta, "lamp", "lemp", "wordpress") {
			return false, "", fmt.Errorf("Site does not have an active database.")
		}
		dbUser, _ := meta["db_user"].(string)
		if dbUser == "" {
			return false, "", fmt.Errorf("No DB user recorded for this site.")
		}

		sql := fmt.Sprintf("ALTER USER '%s'@'localhost' IDENTIFIED BY '%s'; FLUSH PRIVILEGES;", dbUser, b.DBPass)
		res := run.Run(60*time.Second, "mysql", "-e", sql)
		if res.Exit != 0 {
			return false, "", fmt.Errorf("MariaDB error: %s", res.Stderr)
		}

		docroot := fmt.Sprintf("/var/www/%s", domain)
		if dr, ok := meta["docroot"].(string); ok && dr != "" {
			docroot = dr
		}
		wpCfg := filepath.Join(docroot, "wp-config.php")
		idxPHP := filepath.Join(docroot, "index.php")

		if fileExists(wpCfg) {
			content, err := os.ReadFile(wpCfg)
			if err != nil {
				return false, "", pyOSError(err, wpCfg)
			}
			updated := reWpDbPass.ReplaceAllLiteralString(string(content),
				fmt.Sprintf("define( 'DB_PASSWORD', '%s' );", b.DBPass))
			if err := os.WriteFile(wpCfg, []byte(updated), 0o644); err != nil {
				return false, "", pyOSError(err, wpCfg)
			}
		} else if fileExists(idxPHP) {
			content, err := os.ReadFile(idxPHP)
			if err != nil {
				return false, "", pyOSError(err, idxPHP)
			}
			updated := reIdxPassDouble.ReplaceAllLiteralString(string(content),
				fmt.Sprintf("$password = %q;", b.DBPass))
			updated = reIdxPassSingle.ReplaceAllLiteralString(updated,
				fmt.Sprintf("$password = '%s';", b.DBPass))
			if err := os.WriteFile(idxPHP, []byte(updated), 0o644); err != nil {
				return false, "", pyOSError(err, idxPHP)
			}
		}

		return true, "Database password updated successfully.", nil

	default:
		return false, "", fmt.Errorf("Unknown action.")
	}
}

func reloadEither(server string) {
	if server == "apache" {
		reloadNow("apache2")
	} else {
		reloadNow("nginx")
	}
}

// reloadNow — the apply_reloads() reload-vs-restart logic run inline and
// silent (python would merely mark the service pending; see header).
func reloadNow(service string) {
	res := run.Run(30*time.Second, "systemctl", "is-active", service)
	action := "restart"
	if strings.TrimSpace(res.Stdout) == "active" {
		action = "reload"
	}
	_ = run.Run(60*time.Second, "systemctl", action, service)
}

// ── FS / meta helpers ─────────────────────────────────────────────────

func pathExists(p string) bool {
	_, err := os.Lstat(p)
	return err == nil
}

func fileExists(p string) bool {
	st, err := os.Stat(p)
	return err == nil && !st.IsDir()
}

func isSymlink(p string) bool {
	st, err := os.Lstat(p)
	return err == nil && st.Mode()&os.ModeSymlink != 0
}

// getSiteMeta — python get_site_meta(): nil when missing/unreadable.
func getSiteMeta(metaDir, domain string) map[string]any {
	if metaDir == "" {
		return nil
	}
	data, err := os.ReadFile(filepath.Join(metaDir, domain+".json"))
	if err != nil {
		return nil
	}
	var meta map[string]any
	if json.Unmarshal(data, &meta) != nil {
		return nil
	}
	return meta
}

// deleteSiteMeta removes the meta file silently (python delete_site_meta).
func deleteSiteMeta(metaDir, domain string) {
	if metaDir == "" {
		return
	}
	_ = os.Remove(filepath.Join(metaDir, domain+".json"))
}

func metaIsType(meta map[string]any, types ...string) bool {
	t, _ := meta["type"].(string)
	for _, want := range types {
		if t == want {
			return true
		}
	}
	return false
}

// pyOSError renders ENOENT failures with python's OSError text (the
// update_docroot paths surface str(e) directly, so a missing conf reads
// "[Errno 2] No such file or directory: '<path>'" as on python); other
// errors fall back to the raw Go error string.
func pyOSError(err error, path string) error {
	if os.IsNotExist(err) {
		return fmt.Errorf("[Errno 2] No such file or directory: '%s'", path)
	}
	return err
}

// siteActionJSON — python json.dumps({"success": b, "message": s}):
// two-space-after separators, HTML characters unescaped (python leaves
// < > & raw; get_vhost messages are full vhost files).
func siteActionJSON(success bool, message string) []byte {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(message)
	msgJSON := strings.TrimRight(buf.String(), "\n")
	ok := "false"
	if success {
		ok = "true"
	}
	return []byte(`{"success": ` + ok + `, "message": ` + msgJSON + `}`)
}

// Regex set for update_docroot / update_db_pass — verbatim python
// patterns from handle_site_action_post. Replacements are literal so
// docroot/pass values containing $ signs pass through untouched.
var (
	reDocumentRoot  = regexp.MustCompile(`DocumentRoot\s+.*`)
	reDirectoryTag  = regexp.MustCompile(`<Directory\s+[^>]+>`)
	reNginxRoot     = regexp.MustCompile(`root\s+[^;]+;`)
	reWpDbPass      = regexp.MustCompile(`define\(\s*'DB_PASSWORD'\s*,\s*'.*?'\s*\);`)
	reIdxPassDouble = regexp.MustCompile(`\$password\s*=\s*".*?";`)
	reIdxPassSingle = regexp.MustCompile(`\$password\s*=\s*'.*?';`)
)
