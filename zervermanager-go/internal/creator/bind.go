// BIND9 zone operations — parity with the python block at
// validate_zone / zone_exists / add_zone_to_conf / remove_zone_from_conf
// / reverse_zone_name / reverse_zone_file / write_forward_zone /
// write_reverse_zone / validate_bind9 / reload_bind9.
package creator

import (
	"fmt"
	"os"
	"regexp"
	"strings"
	"time"
)

// validateZone — python validate_zone(): named-checkzone.
func (f *Flow) validateZone(domain, zonefile string) (bool, string) {
	res := f.run(60*time.Second, "named-checkzone", domain, zonefile)
	if res.Exit != 0 {
		return false, res.Stdout + res.Stderr
	}
	return true, "Zone OK"
}

// ensureBind9 — the bind9 branch of python ensure_dependencies(), run
// before any Make-Site DNS write so zone creation works on a box that
// never installed BIND (previously it died on the missing
// named-checkzone binary). Messages are python parity.
func (f *Flow) ensureBind9() error {
	s := f.stream
	if dpkgInstalled("bind9") {
		return nil
	}

	s.WriteRaw("\n" + cYELLOW + "  Missing packages: bind9" + cRESET + "\n")
	s.WriteRaw("  " + cCYAN + "Auto-installing..." + cRESET + "\n\n")

	f.runLiveTimeout(30*time.Minute, "apt-get", "update")
	rc, stderr := f.runLiveTimeout(30*time.Minute, "apt-get",
		"install", "-y", "--no-install-recommends", "bind9")
	if rc != 0 {
		s.err("Install failed:\n" + strings.TrimSpace(stderr))
		return errAbort
	}
	s.ok("Installed: bind9")

	r := f.run(120*time.Second, "systemctl", "enable", "--now", "bind9")
	if r.Exit != 0 {
		// Found live on Debian 12: bind9.service is an alias of
		// named.service and `systemctl enable` refuses aliases
		// ("Refusing to operate on alias name or linked unit file").
		// Retry against the real unit so the flow ends green.
		r = f.run(120*time.Second, "systemctl", "enable", "--now", "named")
	}
	if r.Exit == 0 {
		s.ok("BIND9 enabled.")
	} else {
		s.err(strings.TrimSpace(r.Stderr))
	}

	if _, err := os.Stat(namedConfLocal); os.IsNotExist(err) {
		f.backupConfig(namedConfLocal)
		if err := os.WriteFile(namedConfLocal, []byte("// Local zones\n"), 0o644); err != nil {
			s.err(fmt.Sprintf("Could not create %s: %v", namedConfLocal, err))
			return errAbort
		}
		s.ok("Created empty named.conf.local.")
	}
	return nil
}

// reloadBind9 — mark_reload("bind9").
func (f *Flow) reloadBind9() { f.markReload("bind9") }

// zoneExists — textual probe of named.conf.local.
func zoneExists(domain string) bool {
	data, err := os.ReadFile(namedConfLocal)
	if err != nil {
		return false
	}
	return strings.Contains(string(data), fmt.Sprintf("zone %q", domain))
}

// addZoneToConf — python add_zone_to_conf(): append a block; always
// back up the conf first.
func (f *Flow) addZoneToConf(domain, zonefile string) {
	block := fmt.Sprintf(`
zone "%s" {
    type master;
    file "%s";
};
`, domain, zonefile)

	f.backupConfig(namedConfLocal)
	fh, err := os.OpenFile(namedConfLocal, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	_, _ = fh.WriteString(block)
	_ = fh.Close()
}

// removeZoneFromConf — python remove_zone_from_conf(): regex-extract
// the zone block and rewrite the file.
func (f *Flow) removeZoneFromConf(domain string) {
	data, err := os.ReadFile(namedConfLocal)
	if err != nil {
		return
	}
	content := string(data)

	pattern := regexp.MustCompile(`(?s)\nzone "` + regexp.QuoteMeta(domain) + `" \{[^}]*\};\n`)
	newContent := pattern.ReplaceAllString(content, "\n")

	f.backupConfig(namedConfLocal)
	_ = os.WriteFile(namedConfLocal, []byte(newContent), 0o644)
}

// reverseZoneName — python reverse_zone_name().
func reverseZoneName(ip string) string {
	parts := strings.Split(ip, ".")
	return fmt.Sprintf("%s.%s.%s.in-addr.arpa", parts[2], parts[1], parts[0])
}

// reverseZoneFile — python reverse_zone_file().
func reverseZoneFile(ip string) string {
	parts := strings.Split(ip, ".")
	return fmt.Sprintf("%s/db.%s.%s.%s", bindDir, parts[0], parts[1], parts[2])
}

// lastOctet — python last_octet().
func lastOctet(ip string) string {
	parts := strings.Split(ip, ".")
	return parts[len(parts)-1]
}

// writeForwardZone — python write_forward_zone().
func (f *Flow) writeForwardZone(domain, ip, zonefile string) {
	serial := getSerial(zonefile)
	content := fmt.Sprintf(`; Forward zone for %s
$TTL    604800
@   IN  SOA     %s. admin.%s. (
                %s    ; Serial
                604800      ; Refresh
                86400       ; Retry
                2419200     ; Expire
                604800 )    ; Negative TTL

@   IN  NS      %s.
@   IN  A       %s
www IN  A       %s
`, domain, domain, domain, serial, domain, ip, ip)

	f.backupConfig(zonefile)
	_ = os.WriteFile(zonefile, []byte(content), 0o644)
}

// writeReverseZone — python write_reverse_zone().
func (f *Flow) writeReverseZone(domain, ip, zonefile string) {
	serial := getSerial(zonefile)
	octet := lastOctet(ip)
	content := fmt.Sprintf(`; Reverse zone for %s
$TTL    604800
@   IN  SOA     %s. admin.%s. (
                %s    ; Serial
                604800      ; Refresh
                86400       ; Retry
                2419200     ; Expire
                604800 )    ; Negative TTL

@       IN  NS      %s.
%s     IN  PTR     %s.
`, domain, domain, domain, serial, domain, octet, domain)

	f.backupConfig(zonefile)
	_ = os.WriteFile(zonefile, []byte(content), 0o644)
}

// validateBind9 — python validate_bind9(): named-checkconf.
func (f *Flow) validateBind9() (bool, string) {
	res := f.run(60*time.Second, "named-checkconf")
	if res.Exit != 0 {
		return false, res.Stdout + res.Stderr
	}
	return true, "Syntax OK"
}
