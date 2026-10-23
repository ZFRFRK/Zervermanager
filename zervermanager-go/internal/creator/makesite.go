// Site metadata (python save_site_meta) + the makesite dispatch
// (python handle_makesite_post body).
package creator

import (
	"encoding/json"
	"fmt"
	"net"
	"os"
	"strconv"
	"time"
)

// ─────────────────────────────────────────
//  Site metadata
// ─────────────────────────────────────────

// siteMeta — field order mirrors the python dict literal (json.dumps
// preserves insertion order); omitempty covers the `if x:` guards.
type siteMeta struct {
	Type          string `json:"type"`
	Domain        string `json:"domain"`
	Docroot       string `json:"docroot"`
	WebServer     string `json:"web_server"`
	Created       string `json:"created"`
	DNS           bool   `json:"dns"`
	DBName        string `json:"db_name,omitempty"`
	DBUser        string `json:"db_user,omitempty"`
	ContainerName string `json:"container_name,omitempty"`
	HostPort      int    `json:"host_port,omitempty"`
}

// saveSiteMeta — writes META_DIR/<domain>.json (safe_write: .json files
// are never backed up).
func (f *Flow) saveSiteMeta(domain, siteType, docroot, dbName, dbUser, webServer string, dns bool, containerName string, hostPort int) {
	_ = os.MkdirAll(metaDir, 0o755)

	meta := siteMeta{
		Type:          siteType,
		Domain:        domain,
		Docroot:       docroot,
		WebServer:     webServer,
		Created:       time.Now().Format("2006-01-02 15:04"),
		DNS:           dns,
		DBName:        dbName,
		DBUser:        dbUser,
		ContainerName: containerName,
		HostPort:      hostPort,
	}

	if dbName == "" {
		meta.DBName = ""
	}

	_ = f.safeWrite(metaDir+"/"+domain+".json", string(mustIndent(meta)))
}

func mustIndent(m siteMeta) []byte {
	b, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return []byte("{}")
	}
	return b
}

// ─────────────────────────────────────────
//  Dispatch (python handle_makesite_post)
// ─────────────────────────────────────────

// dataStr — data.get(key, def) for JSON bodies (numbers → python str()).
func dataStr(data map[string]any, key, def string) string {
	v, ok := data[key]
	if !ok || v == nil {
		return def
	}
	switch t := v.(type) {
	case string:
		return t
	case float64:
		if t == float64(int64(t)) {
			return strconv.FormatInt(int64(t), 10)
		}
		return fmt.Sprintf("%v", t)
	case bool:
		if t {
			return "True"
		}
		return "False"
	default:
		return fmt.Sprintf("%v", t)
	}
}

// dataBool — python truthiness of data.get(key).
func dataBool(data map[string]any, key string) bool {
	v, ok := data[key]
	if !ok || v == nil {
		return false
	}
	switch t := v.(type) {
	case string:
		return t != ""
	case bool:
		return t
	case float64:
		return t != 0
	default:
		return true
	}
}

// Run — stream one makesite flow to completion. The returned bool is
// the python success flag: false only when an exception-equivalent
// happened (EOF bridge, unexpected error) — early ✗ returns still
// report success=true, exactly like handle_makesite_post.
func Run(siteType string, data map[string]any, s *Stream) bool {
	success := false

	domain := dataStr(data, "domain", "")

	var dnsEnabled bool
	if _, ok := data["create_dns"]; ok {
		dnsEnabled = dataBool(data, "create_dns")
	} else {
		dnsEnabled = domain != "" && !ipv4RE.MatchString(domain)
	}

	answers := Answers{
		"domain":      domain,
		"dns_choice":  yn(dnsEnabled),
		"ip":          dataStr(data, "ip", ""),
		"db_pass":     dataStr(data, "db_pass", ""),
		"press_enter": "",
	}

	sslChoice := dataStr(data, "ssl", "1")
	leEmail := dataStr(data, "le_email", "")
	actualSSL := sslChoice
	if sslChoice == "4" {
		actualSSL = "1"
	}

	f := NewFlow(s, answers)

	switch siteType {
	case "lamp":
		f.answers["db_name"] = dataStr(data, "db_name", "")
		f.answers["db_user"] = dataStr(data, "db_user", "")
		f.answers["ssl"] = actualSSL
		f.answers["docroot"] = dataStr(data, "docroot", "")
		f.answers["use_site_files"] = yn(dataBool(data, "use_site_files") && dataBool(data, "site_files_path"))
		f.answers["site_files_path"] = dataStr(data, "site_files_path", "")
		f.answers["import_sql"] = yn(dataBool(data, "import_sql") && dataBool(data, "sql_path"))
		f.answers["sql_path"] = dataStr(data, "sql_path", "")

		if err := f.createLampSite(); err != nil {
			return fatal(s, err)
		}
		if sslChoice == "4" {
			f.ensureCertbot("apache")
			certbotIssue(s, domain, leEmail, "--apache")
		}
		success = true

	case "lemp":
		f.answers["db_name"] = dataStr(data, "db_name", "")
		f.answers["db_user"] = dataStr(data, "db_user", "")
		f.answers["ssl"] = actualSSL
		f.answers["docroot"] = dataStr(data, "docroot", "")
		f.answers["use_site_files"] = yn(dataBool(data, "use_site_files") && dataBool(data, "site_files_path"))
		f.answers["site_files_path"] = dataStr(data, "site_files_path", "")
		f.answers["import_sql"] = yn(dataBool(data, "import_sql") && dataBool(data, "sql_path"))
		f.answers["sql_path"] = dataStr(data, "sql_path", "")

		if err := f.createLempSite(); err != nil {
			return fatal(s, err)
		}
		if sslChoice == "4" {
			f.ensureCertbot("nginx")
			certbotIssue(s, domain, leEmail, "--nginx")
		}
		success = true

	case "wordpress":
		// db_name/db_user come through empty so the creator applies
		// its own generated defaults (same as the old ["", "", pass]).
		f.answers["db_name"] = dataStr(data, "db_name", "")
		f.answers["db_user"] = dataStr(data, "db_user", "")
		server := dataStr(data, "server", "apache")

		var err error
		if server == "apache" {
			err = f.createApacheWordPress()
		} else {
			err = f.createNginxWordPress()
		}
		if err != nil {
			return fatal(s, err)
		}
		success = true

	case "static":
		server := dataStr(data, "server", "apache")
		f.answers["ssl"] = actualSSL
		f.answers["cert_path"] = dataStr(data, "cert_path", "")
		f.answers["key_path"] = dataStr(data, "key_path", "")
		f.answers["docroot"] = dataStr(data, "docroot", "")
		f.answers["use_site_files"] = yn(dataBool(data, "use_site_files") && dataBool(data, "site_files_path"))
		f.answers["site_files_path"] = dataStr(data, "site_files_path", "")

		var err error
		if server == "apache" {
			err = f.createFullSite()
		} else {
			err = f.createNginxFullSite()
		}
		if err != nil {
			return fatal(s, err)
		}
		if sslChoice == "4" {
			f.ensureCertbot(server)
			certbotIssue(s, domain, leEmail, "--"+server)
		}
		success = true

	case "proxy":
		server := dataStr(data, "server", "apache")
		f.answers["backend"] = dataStr(data, "backend", "")

		var err error
		if server == "apache" {
			err = f.createApacheProxy()
		} else {
			err = f.createNginxProxy()
		}
		if err != nil {
			return fatal(s, err)
		}
		success = true

	case "docker":
		f.answers["image"] = dataStr(data, "image", "")
		f.answers["port"] = dataStr(data, "port", "80")
		f.answers["dns_choice"] = yn(dataBool(data, "dns"))

		if err := f.createContainerSite(); err != nil {
			return fatal(s, err)
		}
		success = true
	}

	return success
}

// certbotIssue — the handler's post-flow Let's Encrypt call
// (run with timeout=600, silent, then ✓/✗).
func certbotIssue(s *Stream, domain, leEmail, plugin string) {
	s.step("Running Certbot for Let's Encrypt...")
	f := &Flow{stream: s}
	r := f.run(10*time.Minute, "certbot", plugin, "-d", domain, "-d", "www."+domain,
		"--non-interactive", "--agree-tos", "-m", leEmail, "--redirect")
	if r.Exit == 0 {
		s.ok("SSL certificate issued for " + domain + ".")
	} else {
		s.err("Certbot failed: " + r.Stderr)
	}
}

// fatal — python `except Exception as e:` block.
func fatal(s *Stream, err error) bool {
	if err == errAbort {
		// python `return` — success stays true
		return true
	}
	s.WriteRaw("\n[!] Server Error: " + err.Error() + "\n")
	return false
}

func yn(b bool) string {
	if b {
		return "y"
	}
	return "n"
}

// ─────────────────────────────────────────
//  Small shared helpers
// ─────────────────────────────────────────

// netListen — the python socket bind probe.
func netListen(addr string) (net.Listener, error) {
	return net.Listen("tcp4", addr)
}

// allDigits — python str.isdigit() for the ASCII port case.
func allDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}
