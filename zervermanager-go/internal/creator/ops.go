// Filesystem + process operations shared by the creators. Parity with
// the python helpers safe_write / backup_config / get_serial / run /
// run_live / mark_reload / apply_reloads / configure_apache_ports.
package creator

import (
	"bufio"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"time"

	"zervermanager/internal/run"
	"zervermanager/internal/system"
)

// ─────────────────────────────────────────
//  Paths (python constants)
// ─────────────────────────────────────────

const (
	apacheSitesAvailable = "/etc/apache2/sites-available"
	apacheSitesEnabled   = "/etc/apache2/sites-enabled"
	namedConfLocal       = "/etc/bind/named.conf.local"
	bindDir              = "/etc/bind"
	metaDir              = "/etc/servermanager/sites"
	nginxSitesAvailable  = "/etc/nginx/sites-available"
	nginxSitesEnabled    = "/etc/nginx/sites-enabled"
)

// apacheBackendPorts — python APACHE_BACKEND_PORTS.
var apacheBackendPorts = map[int]bool{8080: true, 8443: true}

// ─────────────────────────────────────────
//  Path string helpers (python uses os.path)
// ─────────────────────────────────────────

// filepathAbs — os.path.abspath, normalized for POSIX targets even
// when the dev box builds on Windows.
func filepathAbs(p string) string {
	if runtime.GOOS == "windows" {
		// Tests exercise validation strings that must stay POSIX.
		p = strings.ReplaceAll(p, "\\", "/")
		if !strings.HasPrefix(p, "/") {
			p = "/" + p
		}
	}
	abs, err := filepath.Abs(p)
	if err != nil {
		abs = p
	}
	if runtime.GOOS == "windows" {
		if len(abs) > 2 && abs[1] == ':' {
			abs = abs[2:] // /C:/x → /x handling: Abs prepends a drive
		}
		abs = strings.ReplaceAll(abs, "\\", "/")
	}
	return filepath.ToSlash(filepath.Clean(strings.ReplaceAll(abs, "\\", "/")))
}

// splitParts — [p for p in path.split("/") if p].
func splitParts(p string) []string {
	var out []string
	for _, part := range strings.Split(p, "/") {
		if part != "" {
			out = append(out, part)
		}
	}
	return out
}

// ─────────────────────────────────────────
//  safe_write / backup_config
// ─────────────────────────────────────────

// safeWrite — python safe_write(): mkdir parents, backup existing
// non-.json targets, then write. Returns false on error (narrated).
func (f *Flow) safeWrite(path, content string) bool {
	if dir := filepath.Dir(path); dir != "" {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			f.stream.err("Error writing to " + path + ": " + err.Error())
			return false
		}
	}
	if !strings.HasSuffix(path, ".json") {
		f.backupConfig(path)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		f.stream.err("Error writing to " + path + ": " + err.Error())
		return false
	}
	return true
}

// backupConfig — python backup_config(): timestamped .bak copy before
// an overwrite; nothing to do when the file does not exist yet.
func (f *Flow) backupConfig(path string) {
	info, err := os.Stat(path)
	if err != nil {
		return
	}
	if info.IsDir() {
		return
	}
	backupPath := path + "." + time.Now().Format("20060102_150405") + ".bak"
	data, err := os.ReadFile(path)
	if err != nil {
		return
	}
	if err := os.WriteFile(backupPath, data, info.Mode().Perm()); err != nil {
		return
	}
	f.stream.step("Backed up: " + backupPath)
}

// ─────────────────────────────────────────
//  get_serial
// ─────────────────────────────────────────

var serialRE = regexp.MustCompile(`(?m)^\s*(\d{1,10})\s*;\s*Serial`)

// getSerial — python get_serial(): SOA serial that always advances —
// max(today*100, existing serial + 1).
func getSerial(zonefile string) string {
	bn := atoiStrict(time.Now().Format("20060102")) * 100
	if zonefile != "" {
		if data, err := os.ReadFile(zonefile); err == nil {
			if m := serialRE.FindSubmatch(data); m != nil {
				if n := atoiStrict(string(m[1])); n+1 > bn {
					bn = n + 1
				}
			}
		}
	}
	return fmt.Sprintf("%d", bn)
}

func atoiStrict(s string) int {
	n := 0
	for _, c := range s {
		if c < '0' || c > '9' {
			return 0
		}
		n = n*10 + int(c-'0')
	}
	return n
}

// ─────────────────────────────────────────
//  run / run_live
// ─────────────────────────────────────────

// run — python run(): silent; nonzero exit is data, not an error.
// Long defaults per call site; apt-family calls pass their own.
func (f *Flow) run(timeout time.Duration, name string, args ...string) run.Result {
	return run.Run(timeout, name, args...)
}

// runLiveTimeout — python run_live() with a deadline (apt installs,
// docker pull, certbot). Streams the selected stdout lines live (the
// apt/dpkg SHOW_PREFIXES filter), returns (rc, stderr).
func (f *Flow) runLiveTimeout(timeout time.Duration, name string, args ...string) (int, string) {
	cmd := exec.Command(name, args...)
	var stderrBuf strings.Builder
	pipe, err := cmd.StdoutPipe()
	if err != nil {
		return 127, "Command not found: " + name
	}
	cmd.Stderr = &stderrBuf

	if err := cmd.Start(); err != nil {
		return 127, "Command not found: " + name
	}

	done := make(chan struct{})
	go func() {
		scanner := bufio.NewScanner(pipe)
		scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
		for scanner.Scan() {
			line := strings.TrimRight(scanner.Text(), "\r")
			if line != "" && showPrefix(line) {
				f.stream.WriteRaw("    " + cDIM + line + cRESET + "\n")
			}
		}
		close(done)
	}()

	wait := timeout
	if wait <= 0 {
		wait = 30 * time.Minute
	}
	select {
	case <-done:
	case <-time.After(wait):
		_ = cmd.Process.Kill()
		<-done
		_ = cmd.Wait()
		return 124, "command timed out"
	}

	werr := cmd.Wait()
	if werr != nil {
		if ee, ok := werr.(*exec.ExitError); ok {
			return ee.ExitCode(), stderrBuf.String()
		}
		return 127, "Command not found: " + name
	}
	return 0, stderrBuf.String()
}

var showPrefixes = []string{
	"Get:", "Fetched", "Unpacking", "Setting up",
	"Processing", "Reading", "Building", "Selecting",
	"Preparing", "Removing", "Update",
}

func showPrefix(line string) bool {
	for _, p := range showPrefixes {
		if strings.HasPrefix(line, p) {
			return true
		}
	}
	return false
}

// ─────────────────────────────────────────
//  detect_server_ip
// ─────────────────────────────────────────

// detectServerIP — python detect_server_ip(): UDP-socket trick to
// learn the outbound interface's IPv4.
func detectServerIP() string {
	conn, err := net.Dial("udp", "8.8.8.8:80")
	if err != nil {
		return ""
	}
	defer conn.Close()
	if addr, ok := conn.LocalAddr().(*net.UDPAddr); ok {
		return addr.IP.String()
	}
	return ""
}

// ─────────────────────────────────────────
//  Service/state probes
// ─────────────────────────────────────────

// serviceStatusPlain — python service_status(name, color=False): the
// shared ttl-cached service_statuses() probe.
func (f *Flow) serviceStatusPlain(name string) string {
	states := system.ServiceStatuses([]string{name}, 3*time.Second)
	if states[name] == "active" {
		return "running"
	}
	return "stopped"
}

// serviceStatusColor — python service_status(name) with colors.
func (f *Flow) serviceStatusColor(name string) string {
	if f.serviceStatusPlain(name) == "running" {
		return cGREEN + "running" + cRESET
	}
	return cRED + "stopped" + cRESET
}

// getBind9ServiceName — python get_bind9_service_name(): fall back to
// "named" when the "bind9" unit is unknown to systemd.
func (f *Flow) getBind9ServiceName() string {
	res := f.run(10*time.Second, "systemctl", "is-active", "bind9")
	if strings.Contains(res.Stdout, "unknown") || strings.Contains(res.Stderr, "not-found") {
		return "named"
	}
	return "bind9"
}

// installed — python `_installed(pkg)`: dpkg -s status check.
func dpkgInstalled(name string) bool {
	r := run.Run(30*time.Second, "dpkg", "-s", name)
	return r.Exit == 0 && strings.Contains(r.Stdout, "Status: install ok installed")
}

// ─────────────────────────────────────────
//  configure_apache_ports
// ─────────────────────────────────────────

var (
	listen80RE   = regexp.MustCompile(`(?m)^(Listen\s+)80$`)
	listen443RE  = regexp.MustCompile(`(?m)^(Listen\s+)443$`)
	vhost80RE    = regexp.MustCompile(`<VirtualHost \*:80>`)
	serverNameRE = regexp.MustCompile(`(?m)^\s*ServerName\s`)
)

// configureApachePorts — python configure_apache_ports(): move apache
// to 8080/8443 behind the nginx front door; silence AH00558.
func (f *Flow) configureApachePorts() {
	portsConf := "/etc/apache2/ports.conf"
	if data, err := os.ReadFile(portsConf); err == nil {
		content := string(data)
		if !strings.Contains(content, "Listen 8080") {
			content = listen80RE.ReplaceAllString(content, "${1}8080")
			content = listen443RE.ReplaceAllString(content, "${1}8443")
			_ = os.WriteFile(portsConf, []byte(content), 0o644)
		}
	}
	defConf := "/etc/apache2/sites-available/000-default.conf"
	if data, err := os.ReadFile(defConf); err == nil {
		content := string(data)
		if !strings.Contains(content, "<VirtualHost *:8080>") {
			content = vhost80RE.ReplaceAllString(content, "<VirtualHost *:8080>")
			_ = os.WriteFile(defConf, []byte(content), 0o644)
		}
	}
	f.silenceServername()
}

// silenceServername writes servername.conf + a2enconf it when no
// global ServerName exists anywhere (AH00558 noise).
func (f *Flow) silenceServername() {
	servername := "/etc/apache2/conf-available/servername.conf"
	if info, err := os.Stat("/etc/apache2/conf-available"); err != nil || !info.IsDir() {
		return
	}
	if _, err := os.Stat(servername); err == nil {
		return
	}
	existing := ""
	for _, pth := range append([]string{"/etc/apache2/apache2.conf"}, glob("/etc/apache2/conf-enabled/*.conf")...) {
		if data, err := os.ReadFile(pth); err == nil {
			existing += string(data)
		}
	}
	if serverNameRE.MatchString(existing) {
		return
	}
	_ = os.WriteFile(servername, []byte("ServerName localhost\n"), 0o644)
	_ = run.Run(30*time.Second, "a2enconf", "servername")
}

// glob — filepath.Glob for POSIX paths.
func glob(pattern string) []string {
	m, err := filepath.Glob(pattern)
	if err != nil {
		return nil
	}
	return m
}
