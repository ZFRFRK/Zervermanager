// The six site-creation flows — direct ports of create_lamp_site,
// create_lemp_site, create_full_site / create_nginx_full_site ("static"),
// create_apache_wordpress_site / create_nginx_wordpress_site,
// create_apache_reverse_proxy / create_nginx_reverse_proxy and
// create_container_site from zervermanager.py. Narrative bytes are kept
// identical (including the python inconsistencies that differ per flow).
package creator

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// errAbort signals the python `return` (no exception) — the request
// still reports success=true per handle_makesite_post semantics.
var errAbort = errors.New("creator aborted")

// ─────────────────────────────────────────
//  Shared askers
// ─────────────────────────────────────────

// promptForIP — python prompt_for_ip(): never re-prompts in web mode;
// an empty bridged answer stays empty (caller aborts for DNS).
func (f *Flow) promptForIP(msg string) (string, error) {
	autoIP := detectServerIP()
	prompt := msg + ": "
	if autoIP != "" {
		prompt = msg + " [" + autoIP + "]: "
	}
	val, err := f.answers.Ask(f.stream, prompt, "ip")
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(val), nil
}

// reloadNginx — python reload_nginx().
func (f *Flow) reloadNginx() { f.markReload("nginx") }

// dnsZones — the shared optional-DNS branch. fwdErr / revErr / bindErr
// are the ✗ message prefixes (python varies per creator: some print the
// bare message). Returning errAbort mirrors the python early-return.
func (f *Flow) dnsZones(domain, ip, fwdErr, revErr, bindErr string) (string, string, error) {
	s := f.stream

	if err := f.ensureBind9(); err != nil {
		return "", "", err
	}

	revZone := reverseZoneName(ip)
	fwdFile := bindDir + "/db." + domain
	revFile := reverseZoneFile(ip)

	if zoneExists(domain) {
		s.warn("Forward zone " + domain + " already exists, skipping.")
	} else {
		s.step("Writing forward zone...")
		f.writeForwardZone(domain, ip, fwdFile)

		okZ, msgZ := f.validateZone(domain, fwdFile)
		if !okZ {
			_ = os.Remove(fwdFile)
			s.err(fwdErr + msgZ)
			return "", "", errAbort
		}

		f.addZoneToConf(domain, fwdFile)
		s.ok("Forward zone created.")
	}

	if zoneExists(revZone) {
		s.warn("Reverse zone " + revZone + " already exists, skipping.")
	} else {
		s.step("Writing reverse zone...")
		f.writeReverseZone(domain, ip, revFile)

		okR, msgR := f.validateZone(revZone, revFile)
		if !okR {
			_ = os.Remove(revFile)
			f.removeZoneFromConf(domain)
			s.err(revErr + msgR)
			return "", "", errAbort
		}

		f.addZoneToConf(revZone, revFile)
		s.ok("Reverse zone created.")
	}

	okB, msgB := f.validateBind9()
	if !okB {
		s.err(bindErr + msgB)
		return "", "", errAbort
	}
	f.reloadBind9()

	return fwdFile, revFile, nil
}

// printDBSteps runs the 4-step lamp/lemp database setup — the sql/label
// pairs are identical for both flows.
func lampDBSteps(dbName, dbUser, dbPass string) [][2]string {
	return [][2]string{
		{"CREATE DATABASE IF NOT EXISTS `" + dbName + "`", "Creating database '" + dbName + "'"},
		{"CREATE USER IF NOT EXISTS '" + dbUser + "'@'localhost' IDENTIFIED BY '" + dbPass + "'", "Creating user '" + dbUser + "'"},
		{"GRANT ALL PRIVILEGES ON `" + dbName + "`.* TO '" + dbUser + "'@'localhost'", "Granting privileges on '" + dbName + "'.*"},
		{"FLUSH PRIVILEGES", "Flushing privileges"},
	}
}

// summarySeparator — the bold ─ x35 blocks opening/closing summaries.
func (f *Flow) summaryOpen() {
	f.stream.WriteRaw("\n" + cBOLD + strings.Repeat("─", 35) + cRESET + "\n")
}
func (f *Flow) summaryClose() {
	f.stream.WriteRaw(cBOLD + strings.Repeat("─", 35) + cRESET + "\n")
}

// ─────────────────────────────────────────
//  LAMP (Apache + PHP + MariaDB + optional DNS)
// ─────────────────────────────────────────

func (f *Flow) createLampSite() error {
	s := f.stream
	s.menuHeader("Create LAMP Site (Apache + PHP + MariaDB + optional DNS)")

	if !f.ensureMariaDB() {
		return nil
	}
	if !f.ensureNginx() {
		return nil
	}

	domain, err := askDomain(s, f.answers)
	if err != nil {
		return err
	}
	if domain == "" {
		return nil
	}

	dnsChoice, err := f.answers.Ask(s, "  Create DNS zones for this site? (y/N): ", "dns_choice")
	if err != nil {
		return err
	}
	createDNS := strings.ToLower(strings.TrimSpace(dnsChoice)) == "y"

	var ip string
	if createDNS {
		ip, err = f.promptForIP("Server IP")
		if err != nil {
			return err
		}
		if ip == "" {
			s.err("IP address required for DNS.")
			return nil
		}
	}

	// ── Database settings ──
	defaultDB := defaultDBName(domain)
	dbName, err := askDBName(s, f.answers, defaultDB)
	if err != nil {
		return err
	}
	dbUser, err := askDBUser(s, f.answers, defaultDB+"_user")
	if err != nil {
		return err
	}
	dbPass, err := f.answers.Ask(s, "  Database password: ", "db_pass")
	if err != nil {
		return err
	}
	dbPass = strings.TrimSpace(dbPass)
	if dbPass == "" {
		s.err("Password cannot be empty.")
		return nil
	}

	// ── SSL ──
	s.WriteRaw("\nSSL options:\n")
	s.WriteRaw("  1. HTTP only\n")
	s.WriteRaw("  2. HTTPS (self-signed cert)\n")

	sslChoice, err := f.answers.Ask(s, "  \nChoice [1]: ", "ssl")
	if err != nil {
		return err
	}
	sslChoice = strings.TrimSpace(sslChoice)
	if sslChoice == "" {
		sslChoice = "1"
	}

	docroot, err := askDocroot(s, f.answers, "Document root", "/var/www/"+domain)
	if err != nil {
		return err
	}
	s.WriteRaw("\n")

	// ── Install packages (live output) ──
	s.step("Installing MariaDB and PHP packages...")
	s.WriteRaw("  " + cDIM + "(this may take a minute)" + cRESET + "\n")

	rc, stderr := f.runLiveTimeout(30*time.Minute, "apt-get", "install", "-y",
		"mariadb-server", "php", "libapache2-mod-php", "php-mysql")
	if rc != 0 {
		s.err("Package install failed:\n" + stderr)
		return nil
	}
	s.ok("All packages installed.")
	f.configureApachePorts()

	// ── Start MariaDB ──
	s.step("Starting MariaDB...")
	f.run(60*time.Second, "systemctl", "start", "mariadb")
	f.run(60*time.Second, "systemctl", "enable", "mariadb")
	s.ok("MariaDB: " + f.serviceStatusColor("mariadb"))

	// ── Create database and user ──
	s.step("Setting up database...")
	if !f.runDBSteps(lampDBSteps(dbName, dbUser, dbPass)) {
		return nil
	}
	s.ok("Database and user ready.")

	// ── SSL cert ──
	cert, key := "", ""
	f.ensureModules()

	if sslChoice == "2" {
		s.step("Generating self-signed certificate...")
		var sslErr string
		cert, key, sslErr = f.makeSelfSigned(domain)
		if sslErr != "" {
			s.err("SSL error: " + sslErr)
			return nil
		}
		s.ok("Certificate generated.")
	}

	// ── Document root + index.php ──
	s.step("Creating document root and test page...")
	_ = os.MkdirAll(docroot, 0o755)

	custom, err := f.askForCustomSiteFiles(docroot)
	if err != nil {
		return err
	}
	if !custom {
		// Generate default index.php only if no custom files were copied
		index := filepath.Join(docroot, "index.php")
		if err := os.WriteFile(index, []byte(lampIndexPHP(domain, dbName, dbUser, dbPass)), 0o644); err != nil {
			return err
		}
		s.ok("Test page created at index.php.")
	}

	if err := f.promptDBImport(dbName, dbUser, dbPass, docroot); err != nil {
		return err
	}

	// ── Apache vhost ──
	s.step("Writing Apache vhost config...")

	config := httpVhost(domain, docroot)
	if sslChoice == "2" {
		config = httpsVhost(domain, docroot, cert, key)
	}
	vhostPath, _ := f.writeVhost(domain, config)

	okA, msgA := f.validateApache()
	if !okA {
		_ = os.Remove(vhostPath)
		s.err("Apache error:\n" + msgA)
		return nil
	}

	f.enableSite(domain)
	f.saveSiteMeta(domain, "lamp", docroot, dbName, dbUser, "apache", createDNS, "", 0)
	f.reloadApache()
	s.ok("Apache vhost created.")

	// ── Nginx proxy ──
	s.step("Configuring Nginx front-door proxy...")
	f.writeNginxVhost(domain, nginxProxyVhost(domain))
	f.nginxEnableSite(domain)
	f.reloadNginx()
	s.ok("Nginx proxy configured.")

	// ── BIND9 zones (only if requested) ──
	if createDNS {
		if _, _, err := f.dnsZones(domain, ip, "", "", ""); err != nil {
			return err
		}
	}

	f.applyReloads()

	// ── Summary ──
	proto := "http"
	if sslChoice == "2" {
		proto = "https"
	}
	dbStatus := f.serviceStatusColor("mariadb")

	f.summaryOpen()
	s.WriteRaw("  " + cBOLD + "Site:" + cRESET + "      " + cGREEN + domain + cRESET + "\n")
	s.WriteRaw("  Docroot:   " + docroot + "\n")
	s.WriteRaw("  " + cBOLD + "Database:" + cRESET + "  " + dbName + "\n")
	s.WriteRaw("  DB User:   " + dbUser + "\n")
	s.WriteRaw("  DB Pass:   " + dbPass + "\n")
	s.WriteRaw("  MariaDB:   " + dbStatus + "\n")
	if cert != "" {
		s.WriteRaw("  Cert:      " + cert + "\n")
	}
	if createDNS {
		s.WriteRaw("  DNS:       created\n")
	} else {
		s.WriteRaw("  DNS:       not created\n")
	}
	s.WriteRaw("  " + cBOLD + "URL:" + cRESET + "       " + cCYAN + proto + "://" + domain + cRESET + "\n")
	f.summaryClose()

	return nil
}

// ─────────────────────────────────────────
//  LEMP (Nginx + PHP-FPM + MariaDB + optional DNS)
// ─────────────────────────────────────────

func (f *Flow) createLempSite() error {
	s := f.stream
	s.menuHeader("Create LEMP Site (Nginx + PHP + MariaDB + optional DNS)")

	if !f.ensureNginx() {
		return nil
	}
	if !f.ensureMariaDB() {
		return nil
	}

	domain, err := askDomain(s, f.answers)
	if err != nil {
		return err
	}
	if domain == "" {
		return nil
	}

	dnsChoice, err := f.answers.Ask(s, "  Create DNS zones for this site? (y/N): ", "dns_choice")
	if err != nil {
		return err
	}
	createDNS := strings.ToLower(strings.TrimSpace(dnsChoice)) == "y"

	var ip string
	if createDNS {
		ip, err = f.promptForIP("Server IP")
		if err != nil {
			return err
		}
		if ip == "" {
			s.err("IP address required for DNS.")
			return nil
		}
	}

	defaultDB := defaultDBName(domain)
	dbName, err := askDBName(s, f.answers, defaultDB)
	if err != nil {
		return err
	}
	dbUser, err := askDBUser(s, f.answers, defaultDB+"_user")
	if err != nil {
		return err
	}
	dbPass, err := f.answers.Ask(s, "  Database password: ", "db_pass")
	if err != nil {
		return err
	}
	dbPass = strings.TrimSpace(dbPass)
	if dbPass == "" {
		s.err("Password cannot be empty.")
		return nil
	}

	s.WriteRaw("\nSSL options:\n")
	s.WriteRaw("  1. HTTP only\n")
	s.WriteRaw("  2. HTTPS (self-signed cert)\n")
	sslChoice, err := f.answers.Ask(s, "  \nChoice [1]: ", "ssl")
	if err != nil {
		return err
	}
	sslChoice = strings.TrimSpace(sslChoice)
	if sslChoice == "" {
		sslChoice = "1"
	}

	docroot, err := askDocroot(s, f.answers, "Document root", "/var/www/"+domain)
	if err != nil {
		return err
	}
	s.WriteRaw("\n")

	s.step("Installing PHP-FPM and MariaDB packages...")
	s.WriteRaw("  " + cDIM + "(this may take a minute)" + cRESET + "\n")

	rc, stderr := f.runLiveTimeout(30*time.Minute, "apt-get", "install", "-y",
		"mariadb-server", "php-fpm", "php-mysql", "php-curl", "php-mbstring", "php-xml")
	if rc != 0 {
		s.err("Package install failed:\n" + stderr)
		return nil
	}
	s.ok("All packages installed.")

	s.step("Starting MariaDB...")
	f.run(60*time.Second, "systemctl", "start", "mariadb")
	f.run(60*time.Second, "systemctl", "enable", "mariadb")
	s.ok("MariaDB: " + f.serviceStatusColor("mariadb"))

	s.step("Setting up database...")
	if !f.runDBSteps(lampDBSteps(dbName, dbUser, dbPass)) {
		return nil
	}
	s.ok("Database and user ready.")

	cert, key := "", ""
	if sslChoice == "2" {
		s.step("Generating self-signed certificate...")
		var sslErr string
		cert, key, sslErr = f.makeSelfSigned(domain)
		if sslErr != "" {
			s.err("SSL error: " + sslErr)
			return nil
		}
		s.ok("Certificate generated.")
	}

	s.step("Starting PHP-FPM...")
	fpmService := ""
	if m := glob("/lib/systemd/system/php*-fpm.service"); len(m) > 0 {
		fpmService = strings.TrimSuffix(filepath.Base(m[0]), ".service")
		f.run(60*time.Second, "systemctl", "enable", "--now", fpmService)
		s.ok("PHP-FPM (" + fpmService + "): " + f.serviceStatusColor(fpmService))
	} else {
		f.run(60*time.Second, "systemctl", "enable", "--now", "php-fpm")
		s.ok("PHP-FPM started.")
	}

	fpmSocket := findPhpFpmSocket()

	s.step("Creating document root and test page...")
	_ = os.MkdirAll(docroot, 0o755)
	custom, err := f.askForCustomSiteFiles(docroot)
	if err != nil {
		return err
	}

	if !custom {
		index := filepath.Join(docroot, "index.php")
		if err := os.WriteFile(index, []byte(lempIndexPHP(domain, dbName, dbUser, dbPass)), 0o644); err != nil {
			return err
		}
	}

	if err := f.promptDBImport(dbName, dbUser, dbPass, docroot); err != nil {
		return err
	}

	s.step("Writing Nginx vhost config...")
	config := nginxLempHttpVhost(domain, docroot, fpmSocket)
	if sslChoice == "2" {
		config = nginxLempHttpsVhost(domain, docroot, cert, key, fpmSocket)
	}
	vhostPath, _ := f.writeNginxVhost(domain, config)

	s.step("Validating Nginx config...")
	okN, msgN := f.validateNginx()
	if !okN {
		_ = os.Remove(vhostPath)
		s.err("Nginx config error:\n" + msgN)
		return nil
	}

	f.nginxEnableSite(domain)
	f.saveSiteMeta(domain, "lamp", docroot, dbName, dbUser, "nginx", createDNS, "", 0)
	f.reloadNginx()

	if createDNS {
		if _, _, err := f.dnsZones(domain, ip, "Zone error:\n", "Reverse zone error:\n", "BIND9 error:\n"); err != nil {
			return err
		}
	}

	f.applyReloads()

	proto := "http"
	if sslChoice == "2" {
		proto = "https"
	}

	f.summaryOpen()
	s.WriteRaw("  " + cBOLD + "Site:" + cRESET + "    " + cGREEN + domain + cRESET + "\n")
	s.WriteRaw("  Docroot: " + docroot + "\n")
	s.WriteRaw("  Config:  " + vhostPath + "\n")
	s.WriteRaw("  DB:      " + dbName + "  User: " + dbUser + "\n")
	s.WriteRaw("  PHP-FPM: " + fpmSocket + "\n")
	if createDNS {
		s.WriteRaw("  Fwd zone: " + fwdZoneFile(domain) + "\n")
		s.WriteRaw("  Rev zone: " + reverseZoneFile(ip) + "\n")
	}
	if cert != "" {
		s.WriteRaw("  Cert:    " + cert + "\n")
	}
	s.WriteRaw("  " + cBOLD + "URL:" + cRESET + "     " + cCYAN + proto + "://" + domain + cRESET + "\n")
	f.summaryClose()

	return nil
}

// fwdZoneFile — BIND_DIR/db.<domain>.
func fwdZoneFile(domain string) string { return bindDir + "/db." + domain }

// ─────────────────────────────────────────
//  Full Site (Apache static + optional DNS)
// ─────────────────────────────────────────

func (f *Flow) createFullSite() error {
	s := f.stream
	s.menuHeader("Create Full Site (Apache + optional DNS)")

	if !f.ensureNginx() {
		return nil
	}

	domain, err := askDomain(s, f.answers)
	if err != nil {
		return err
	}
	if domain == "" {
		return nil
	}

	dnsChoice, err := f.answers.Ask(s, "  Create DNS zones for this site? (y/N): ", "dns_choice")
	if err != nil {
		return err
	}
	createDNS := strings.ToLower(strings.TrimSpace(dnsChoice)) == "y"

	var ip string
	if createDNS {
		ip, err = f.promptForIP("Server IP")
		if err != nil {
			return err
		}
		if ip == "" {
			s.err("IP address required for DNS.")
			return nil
		}
	}

	s.WriteRaw("\nSSL options:\n")
	s.WriteRaw("  1. HTTP only\n")
	s.WriteRaw("  2. HTTPS (self-signed cert)\n")
	s.WriteRaw("  3. HTTPS (existing cert)\n")

	sslChoice, err := f.answers.Ask(s, "  \nChoice: ", "ssl")
	if err != nil {
		return err
	}
	sslChoice = strings.TrimSpace(sslChoice)

	docroot, err := askDocroot(s, f.answers, "Document root", "/var/www/"+domain)
	if err != nil {
		return err
	}
	s.WriteRaw("\n")

	// ── SSL setup ──
	cert, key := "", ""

	if sslChoice == "2" {
		f.ensureModules()
		s.step("Generating self-signed certificate...")
		var sslErr string
		cert, key, sslErr = f.makeSelfSigned(domain)
		if sslErr != "" {
			s.err("SSL error: " + sslErr)
			return nil
		}
		s.ok("Certificate generated.")
	} else if sslChoice == "3" {
		f.ensureModules()
		cert, err = f.answers.Ask(s, "  Certificate path: ", "cert_path")
		if err != nil {
			return err
		}
		cert = strings.TrimSpace(cert)
		key, err = f.answers.Ask(s, "  Key path: ", "key_path")
		if err != nil {
			return err
		}
		key = strings.TrimSpace(key)

		if info, err := os.Stat(cert); err != nil || info.IsDir() {
			s.err("Certificate not found.")
			return nil
		}
		if info, err := os.Stat(key); err != nil || info.IsDir() {
			s.err("Key not found.")
			return nil
		}
	}

	// ── Document root ──
	s.step("Creating document root...")
	_ = os.MkdirAll(docroot, 0o755)

	custom, err := f.askForCustomSiteFiles(docroot)
	if err != nil {
		return err
	}
	if !custom {
		// No custom files — write the default placeholder index.html
		index := filepath.Join(docroot, "index.html")
		if _, err := os.Stat(index); os.IsNotExist(err) {
			_ = os.WriteFile(index, []byte(fmt.Sprintf("<html><body><h1>%s</h1></body></html>", domain)), 0o644)
		}
	}

	// ── Apache vhost ──
	s.step("Writing Apache vhost config...")

	config := httpVhost(domain, docroot)
	if sslChoice == "2" || sslChoice == "3" {
		config = httpsVhost(domain, docroot, cert, key)
	}
	vhostPath, _ := f.writeVhost(domain, config)

	s.step("Validating Apache config...")
	okA, msgA := f.validateApache()
	if !okA {
		_ = os.Remove(vhostPath)
		s.err("Apache config error:\n" + msgA)
		return nil
	}

	f.enableSite(domain)
	f.saveSiteMeta(domain, "static", docroot, "", "", "apache", createDNS, "", 0)
	f.configureApachePorts()
	s.ok("Apache vhost created.")
	f.reloadApache()

	// ── Nginx proxy ──
	s.step("Configuring Nginx front-door proxy...")
	f.writeNginxVhost(domain, nginxProxyVhost(domain))
	f.nginxEnableSite(domain)
	f.reloadNginx()
	s.ok("Nginx proxy configured.")

	// ── BIND9 zones (only if requested) ──
	var fwdFile, revFile string
	if createDNS {
		fwdFile, revFile, err = f.dnsZones(domain, ip, "Zone error:\n", "Reverse zone error:\n", "BIND9 error:\n")
		if err != nil {
			return err
		}
	}

	// ── Auto-reload ──
	f.applyReloads()

	// ── Summary ──
	proto := "http"
	if sslChoice == "2" || sslChoice == "3" {
		proto = "https"
	}

	f.summaryOpen()
	s.WriteRaw("  " + cBOLD + "Site:" + cRESET + "         " + cGREEN + domain + cRESET + "\n")
	s.WriteRaw("  Docroot:      " + docroot + "\n")
	s.WriteRaw("  Apache conf:  " + vhostPath + "\n")
	if createDNS {
		s.WriteRaw("  Forward zone: " + fwdFile + "\n")
		s.WriteRaw("  Reverse zone: " + revFile + "\n")
	} else {
		s.WriteRaw("  DNS:          " + cDIM + "not created" + cRESET + "\n")
	}
	if cert != "" {
		s.WriteRaw("  Certificate:  " + cert + "\n")
	}
	s.WriteRaw("  " + cBOLD + "URL:" + cRESET + "          " + cCYAN + proto + "://" + domain + cRESET + "\n")
	f.summaryClose()

	return nil
}

// ─────────────────────────────────────────
//  Full Site (Nginx static + optional DNS)
// ─────────────────────────────────────────

func (f *Flow) createNginxFullSite() error {
	s := f.stream
	s.menuHeader("Create Full Nginx Site (Nginx + optional DNS)")

	if !f.ensureNginx() {
		return nil
	}

	domain, err := askDomain(s, f.answers)
	if err != nil {
		return err
	}
	if domain == "" {
		return nil
	}

	dnsChoice, err := f.answers.Ask(s, "  Create DNS zones for this site? (y/N): ", "dns_choice")
	if err != nil {
		return err
	}
	createDNS := strings.ToLower(strings.TrimSpace(dnsChoice)) == "y"

	var ip string
	if createDNS {
		ip, err = f.promptForIP("Server IP")
		if err != nil {
			return err
		}
		if ip == "" {
			s.err("IP address required for DNS.")
			return nil
		}
	}

	s.WriteRaw("\nSSL options:\n")
	s.WriteRaw("  1. HTTP only\n")
	s.WriteRaw("  2. HTTPS (self-signed cert)\n")
	s.WriteRaw("  3. HTTPS (existing cert)\n")

	sslChoice, err := f.answers.Ask(s, "  \nChoice: ", "ssl")
	if err != nil {
		return err
	}
	sslChoice = strings.TrimSpace(sslChoice)

	docroot, err := askDocroot(s, f.answers, "Document root", "/var/www/"+domain)
	if err != nil {
		return err
	}
	s.WriteRaw("\n")

	cert, key := "", ""

	if sslChoice == "2" {
		s.step("Generating self-signed certificate...")
		var sslErr string
		cert, key, sslErr = f.makeSelfSigned(domain)
		if sslErr != "" {
			s.err("SSL error: " + sslErr)
			return nil
		}
		s.ok("Certificate generated.")
	} else if sslChoice == "3" {
		cert, err = f.answers.Ask(s, "  Certificate path: ", "cert_path")
		if err != nil {
			return err
		}
		cert = strings.TrimSpace(cert)
		key, err = f.answers.Ask(s, "  Key path: ", "key_path")
		if err != nil {
			return err
		}
		key = strings.TrimSpace(key)
		if info, err := os.Stat(cert); err != nil || info.IsDir() {
			s.err("Certificate not found.")
			return nil
		}
		if info, err := os.Stat(key); err != nil || info.IsDir() {
			s.err("Key not found.")
			return nil
		}
	}

	s.step("Creating document root...")
	_ = os.MkdirAll(docroot, 0o755)

	custom, err := f.askForCustomSiteFiles(docroot)
	if err != nil {
		return err
	}
	if !custom {
		index := filepath.Join(docroot, "index.html")
		if _, err := os.Stat(index); os.IsNotExist(err) {
			_ = os.WriteFile(index, []byte(fmt.Sprintf("<html><body><h1>%s</h1></body></html>", domain)), 0o644)
		}
	}

	s.step("Writing Nginx vhost config...")
	config := nginxHttpVhost(domain, docroot)
	if sslChoice == "2" || sslChoice == "3" {
		config = nginxHttpsVhost(domain, docroot, cert, key)
	}
	vhostPath, _ := f.writeNginxVhost(domain, config)

	s.step("Validating Nginx config...")
	okN, msgN := f.validateNginx()
	if !okN {
		_ = os.Remove(vhostPath)
		s.err("Nginx config error:\n" + msgN)
		return nil
	}

	f.nginxEnableSite(domain)
	f.saveSiteMeta(domain, "static", docroot, "", "", "nginx", createDNS, "", 0)
	s.ok("Nginx vhost created.")
	f.reloadNginx()

	var fwdFile, revFile string
	if createDNS {
		fwdFile, revFile, err = f.dnsZones(domain, ip, "Zone error:\n", "Reverse zone error:\n", "BIND9 error:\n")
		if err != nil {
			return err
		}
	}

	f.applyReloads()

	proto := "http"
	if sslChoice == "2" || sslChoice == "3" {
		proto = "https"
	}

	f.summaryOpen()
	s.WriteRaw("  " + cBOLD + "Site:" + cRESET + "    " + cGREEN + domain + cRESET + "\n")
	s.WriteRaw("  Docroot: " + docroot + "\n")
	s.WriteRaw("  Config:  " + vhostPath + "\n")
	if createDNS {
		s.WriteRaw("  Fwd zone: " + fwdFile + "\n")
		s.WriteRaw("  Rev zone: " + revFile + "\n")
	} else {
		s.WriteRaw("  DNS:     " + cDIM + "not created" + cRESET + "\n")
	}
	if cert != "" {
		s.WriteRaw("  Cert:    " + cert + "\n")
	}
	s.WriteRaw("  " + cBOLD + "URL:" + cRESET + "     " + cCYAN + proto + "://" + domain + cRESET + "\n")
	f.summaryClose()

	return nil
}

// ─────────────────────────────────────────
//  WordPress (Apache / Nginx)
// ─────────────────────────────────────────

// wpDNSSimple — the WordPress creators' DNS block: no zone validation,
// direct write + conf append + ok lines + reload_bind9.
func (f *Flow) wpDNSSimple(domain, ip string) (string, string) {
	if err := f.ensureBind9(); err != nil {
		f.stream.err("BIND9 unavailable — skipping DNS zones.")
		return "", ""
	}

	fwdFile := "/etc/bind/db." + domain
	revFile := reverseZoneFile(ip)
	if !zoneExists(domain) {
		f.writeForwardZone(domain, ip, fwdFile)
		f.addZoneToConf(domain, fwdFile)
		f.stream.ok("Forward zone created: " + fwdFile)
	}
	if !zoneExists(reverseZoneName(ip)) {
		f.writeReverseZone(domain, ip, revFile)
		f.addZoneToConf(reverseZoneName(ip), revFile)
		f.stream.ok("Reverse zone created: " + revFile)
	}
	f.reloadBind9()
	return fwdFile, revFile
}

func (f *Flow) createApacheWordPress() error {
	s := f.stream
	s.menuHeader("Create WordPress Site (Apache)")
	if !f.ensureMariaDB() {
		return nil
	}
	if !f.ensureNginx() {
		return nil
	}

	domain, err := askDomain(s, f.answers)
	if err != nil {
		return err
	}
	if domain == "" {
		return nil
	}
	docroot := "/var/www/" + domain

	dnsChoice, err := f.answers.Ask(s, "  Create DNS zones for this site? (y/N): ", "dns_choice")
	if err != nil {
		return err
	}
	createDNS := strings.ToLower(strings.TrimSpace(dnsChoice)) == "y"

	var ip string
	if createDNS {
		ip, err = f.promptForIP("Server IP")
		if err != nil {
			return err
		}
		if ip == "" {
			s.err("IP address required for DNS.")
			return nil
		}
	} else {
		ip = detectServerIP()
	}

	defaultDB := defaultDBName(domain)
	dbName, err := askDBName(s, f.answers, defaultDB)
	if err != nil {
		return err
	}
	dbUser, err := askDBUser(s, f.answers, defaultDB+"_user")
	if err != nil {
		return err
	}
	dbPass, err := f.answers.Ask(s, "  Database password: ", "db_pass")
	if err != nil {
		return err
	}
	dbPass = strings.TrimSpace(dbPass)
	if dbPass == "" {
		s.err("Password is required.")
		return nil
	}

	// Create DB
	r := f.run(5*time.Minute, "mysql", "-uroot", "-e",
		"CREATE DATABASE IF NOT EXISTS `"+dbName+"` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
	if r.Exit != 0 {
		s.err("DB creation failed: " + strings.TrimSpace(r.Stderr))
	} else {
		s.ok("Database '" + dbName + "' ready.")
	}
	f.run(5*time.Minute, "mysql", "-uroot", "-e",
		"CREATE USER IF NOT EXISTS '"+dbUser+"'@'localhost' IDENTIFIED BY '"+dbPass+"';")
	f.run(5*time.Minute, "mysql", "-uroot", "-e",
		"GRANT ALL PRIVILEGES ON `"+dbName+"`.* TO '"+dbUser+"'@'localhost'; FLUSH PRIVILEGES;")
	s.ok("DB user '" + dbUser + "' created.")

	// Ensure PHP
	s.step("Installing PHP and Apache module...")
	r = f.run(30*time.Minute, "apt-get", "install", "-y", "--no-install-recommends",
		"php", "php-mysql", "php-curl", "php-gd", "php-mbstring",
		"php-xml", "php-zip", "libapache2-mod-php")
	if r.Exit != 0 {
		s.err("Failed to install PHP packages:\n" + r.Stderr)
		return nil
	}
	f.configureApachePorts()

	// Download WordPress
	if !f.ensureWordPress(docroot, dbName, dbUser, dbPass) {
		return nil
	}

	// VHost
	vhost := fmt.Sprintf(`<VirtualHost *:8080>
    ServerName %s
    ServerAlias www.%s
    DocumentRoot %s

    <Directory %s>
        AllowOverride All
        Require all granted
    </Directory>

    ErrorLog ${APACHE_LOG_DIR}/%s-error.log
    CustomLog ${APACHE_LOG_DIR}/%s-access.log combined
</VirtualHost>
`, domain, domain, docroot, docroot, domain, domain)

	f.writeVhost(domain, vhost)
	f.run(60*time.Second, "/usr/sbin/a2enmod", "rewrite")
	f.enableSite(domain)
	okA, msgA := f.validateApache()
	if !okA {
		s.err("Apache config error: " + msgA)
		return nil
	}
	f.reloadApache()

	// Optional DNS
	fwdFile, revFile := "", ""
	if createDNS && ip != "" {
		fwdFile, revFile = f.wpDNSSimple(domain, ip)
	}

	f.saveSiteMeta(domain, "wordpress", docroot, dbName, dbUser, "apache", createDNS, "", 0)

	// ── Nginx proxy ──
	s.step("Configuring Nginx front-door proxy...")
	f.writeNginxVhost(domain, nginxProxyVhost(domain))
	f.nginxEnableSite(domain)
	f.reloadNginx()
	s.ok("Nginx proxy configured.")

	// Restart related services
	s.step("Restarting related services...")
	f.run(60*time.Second, "systemctl", "restart", "mariadb")
	s.ok("MariaDB restarted.")
	f.run(60*time.Second, "systemctl", "restart", "apache2")
	s.ok("Apache2 restarted.")
	if createDNS {
		f.run(60*time.Second, "systemctl", "restart", f.getBind9ServiceName())
		s.ok("BIND9 restarted.")
	}

	f.summaryOpen()
	s.WriteRaw("  " + cBOLD + "Site:" + cRESET + "         " + cGREEN + domain + cRESET + "\n")
	s.WriteRaw("  Docroot:      " + docroot + "\n")
	s.WriteRaw("  Apache conf:  /etc/apache2/sites-available/" + domain + ".conf\n")
	s.WriteRaw("  Database:     " + dbName + "\n")
	s.WriteRaw("  DB User:      " + dbUser + "\n")
	if createDNS && fwdFile != "" {
		s.WriteRaw("  Forward zone: " + fwdFile + "\n")
		s.WriteRaw("  Reverse zone: " + revFile + "\n")
		s.WriteRaw("  " + cBOLD + "URL:" + cRESET + "          " + cCYAN + "http://" + domain + cRESET + "\n")
	} else {
		s.WriteRaw("  DNS:          " + cDIM + "not created" + cRESET + "\n")
		s.WriteRaw("  " + cBOLD + "URL (IP):" + cRESET + "     " + cCYAN + "http://" + ip + cRESET + "\n")
		s.WriteRaw("  " + cDIM + "To use the domain, add to your PC hosts file:" + cRESET + "\n")
		s.WriteRaw("  " + cYELLOW + "  " + ip + "  " + domain + cRESET + "\n")
	}
	s.WriteRaw("  " + cDIM + "Open in browser to complete WordPress setup wizard." + cRESET + "\n")
	f.summaryClose()
	f.answers.InputPause(s, "\n  Press Enter to continue...")

	return nil
}

func (f *Flow) createNginxWordPress() error {
	s := f.stream
	s.menuHeader("Create WordPress Site (Nginx)")
	if !f.ensureNginx() {
		return nil
	}
	if !f.ensureMariaDB() {
		return nil
	}
	domain, err := askDomain(s, f.answers)
	if err != nil {
		return err
	}
	if domain == "" {
		return nil
	}
	docroot := "/var/www/" + domain

	dnsChoice, err := f.answers.Ask(s, "  Create DNS zones for this site? (y/N): ", "dns_choice")
	if err != nil {
		return err
	}
	createDNS := strings.ToLower(strings.TrimSpace(dnsChoice)) == "y"

	var ip string
	if createDNS {
		ip, err = f.promptForIP("Server IP")
		if err != nil {
			return err
		}
		if ip == "" {
			s.err("IP address required for DNS.")
			return nil
		}
	} else {
		ip = detectServerIP()
	}

	defaultDB := defaultDBName(domain)
	dbName, err := askDBName(s, f.answers, defaultDB)
	if err != nil {
		return err
	}
	dbUser, err := askDBUser(s, f.answers, defaultDB+"_user")
	if err != nil {
		return err
	}
	dbPass, err := f.answers.Ask(s, "  Database password: ", "db_pass")
	if err != nil {
		return err
	}
	dbPass = strings.TrimSpace(dbPass)
	if dbPass == "" {
		s.err("Password is required.")
		return nil
	}

	r := f.run(5*time.Minute, "mysql", "-uroot", "-e",
		"CREATE DATABASE IF NOT EXISTS `"+dbName+"` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
	if r.Exit != 0 {
		s.err("DB creation failed: " + strings.TrimSpace(r.Stderr))
	} else {
		s.ok("Database '" + dbName + "' ready.")
	}
	f.run(5*time.Minute, "mysql", "-uroot", "-e",
		"CREATE USER IF NOT EXISTS '"+dbUser+"'@'localhost' IDENTIFIED BY '"+dbPass+"';")
	f.run(5*time.Minute, "mysql", "-uroot", "-e",
		"GRANT ALL PRIVILEGES ON `"+dbName+"`.* TO '"+dbUser+"'@'localhost'; FLUSH PRIVILEGES;")
	s.ok("DB user '" + dbUser + "' created.")

	fpmSocket := findPhpFpmSocket()
	if fpmSocket == "127.0.0.1:9000" { // Means unix socket not found
		rc, stderr := f.runLiveTimeout(30*time.Minute, "apt-get", "install", "-y", "--no-install-recommends",
			"php-fpm", "php-mysql", "php-curl", "php-gd",
			"php-mbstring", "php-xml", "php-zip")
		if rc != 0 {
			s.err("Failed to install PHP-FPM packages:\n" + stderr)
			return nil
		}

		s.step("Starting PHP-FPM...")
		fpmSvc := ""
		if m := glob("/lib/systemd/system/php*-fpm.service"); len(m) > 0 {
			fpmSvc = strings.TrimSuffix(filepath.Base(m[0]), ".service")
			f.run(60*time.Second, "systemctl", "enable", "--now", fpmSvc)
			s.ok("PHP-FPM (" + fpmSvc + "): " + f.serviceStatusColor(fpmSvc))
		} else {
			f.run(60*time.Second, "systemctl", "enable", "--now", "php-fpm")
			s.ok("PHP-FPM started.")
		}

		fpmSocket = findPhpFpmSocket()
		if fpmSocket == "127.0.0.1:9000" {
			switch codename() {
			case "bullseye":
				fpmSocket = "/run/php/php7.4-fpm.sock"
			case "trixie":
				fpmSocket = "/run/php/php8.3-fpm.sock"
			default: // bookworm (default/reference)
				fpmSocket = "/run/php/php8.2-fpm.sock"
			}
		}
	}

	if !f.ensureWordPress(docroot, dbName, dbUser, dbPass) {
		return nil
	}

	fpmRef := fpmSocket
	if strings.HasPrefix(fpmSocket, "/") {
		fpmRef = "unix:" + fpmSocket
	}
	vhost := fmt.Sprintf(`server {
    listen 80;
    server_name %s www.%s;
    root %s;
    index index.php index.html;

    location / {
        try_files $uri $uri/ /index.php?$args;
    }

    location ~ \.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass %s;
    }

    location ~ /\.ht {
        deny all;
    }

    access_log /var/log/nginx/%s-access.log;
    error_log  /var/log/nginx/%s-error.log;
}
`, domain, domain, docroot, fpmRef, domain, domain)

	f.writeNginxVhost(domain, vhost)
	f.nginxEnableSite(domain)
	okN, msgN := f.validateNginx()
	if !okN {
		s.err("Nginx config error: " + msgN)
		f.nginxDisableSite(domain)
		return nil
	}
	f.reloadNginx()

	// Optional DNS
	fwdFile, revFile := "", ""
	if createDNS && ip != "" {
		fwdFile, revFile = f.wpDNSSimple(domain, ip)
	}

	f.saveSiteMeta(domain, "wordpress", docroot, dbName, dbUser, "nginx", createDNS, "", 0)

	// Restart related services
	s.step("Restarting related services...")
	f.run(60*time.Second, "systemctl", "restart", "mariadb")
	s.ok("MariaDB restarted.")
	f.run(60*time.Second, "systemctl", "restart", "nginx")
	s.ok("Nginx restarted.")
	if createDNS {
		f.run(60*time.Second, "systemctl", "restart", f.getBind9ServiceName())
		s.ok("BIND9 restarted.")
	}

	f.summaryOpen()
	s.WriteRaw("  " + cBOLD + "Site:" + cRESET + "         " + cGREEN + domain + cRESET + "\n")
	s.WriteRaw("  Docroot:      " + docroot + "\n")
	s.WriteRaw("  Nginx conf:   /etc/nginx/sites-available/" + domain + ".conf\n")
	s.WriteRaw("  Database:     " + dbName + "\n")
	s.WriteRaw("  DB User:      " + dbUser + "\n")
	if createDNS && fwdFile != "" {
		s.WriteRaw("  Forward zone: " + fwdFile + "\n")
		s.WriteRaw("  Reverse zone: " + revFile + "\n")
		s.WriteRaw("  " + cBOLD + "URL:" + cRESET + "          " + cCYAN + "http://" + domain + cRESET + "\n")
	} else {
		s.WriteRaw("  DNS:          " + cDIM + "not created" + cRESET + "\n")
		s.WriteRaw("  " + cBOLD + "URL (IP):" + cRESET + "     " + cCYAN + "http://" + ip + cRESET + "\n")
		s.WriteRaw("  " + cDIM + "To use the domain, add to your PC hosts file:" + cRESET + "\n")
		s.WriteRaw("  " + cYELLOW + "  " + ip + "  " + domain + cRESET + "\n")
	}
	s.WriteRaw("  " + cDIM + "Open in browser to complete WordPress setup wizard." + cRESET + "\n")
	f.summaryClose()
	f.answers.InputPause(s, "\n  Press Enter to continue...")

	return nil
}

// codename — python detect_codename(): VERSION_CODENAME from os-release.
func codename() string {
	data, err := os.ReadFile("/etc/os-release")
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(data), "\n") {
		if strings.HasPrefix(line, "VERSION_CODENAME=") {
			return strings.Trim(strings.TrimPrefix(line, "VERSION_CODENAME="), `"`)
		}
	}
	return ""
}

// ─────────────────────────────────────────
//  Reverse proxies
// ─────────────────────────────────────────

func (f *Flow) createApacheProxy() error {
	s := f.stream
	s.menuHeader("Create Reverse Proxy Site (Apache)")

	domain, err := askDomain(s, f.answers)
	if err != nil {
		return err
	}
	if domain == "" {
		return nil
	}

	dnsChoice, err := f.answers.Ask(s, "  Create DNS zones for this site? (y/N): ", "dns_choice")
	if err != nil {
		return err
	}
	createDNS := strings.ToLower(strings.TrimSpace(dnsChoice)) == "y"

	var ip string
	if createDNS {
		ip, err = f.promptForIP("Server IP")
		if err != nil {
			return err
		}
		if ip == "" {
			createDNS = false
		}
	}

	target, err := f.answers.Ask(s, "  Proxy target [localhost:3000]: ", "backend")
	if err != nil {
		return err
	}
	target = strings.TrimSpace(target)
	if target == "" {
		target = "localhost:3000"
	}
	if !strings.HasPrefix(target, "http://") && !strings.HasPrefix(target, "https://") {
		target = "http://" + target
	}

	f.run(60*time.Second, "/usr/sbin/a2enmod", "proxy")
	f.run(60*time.Second, "/usr/sbin/a2enmod", "proxy_http")

	vhost := fmt.Sprintf(`<VirtualHost *:80>
    ServerName %s
    ServerAlias www.%s

    ProxyPreserveHost On
    ProxyPass / %s/
    ProxyPassReverse / %s/

    ErrorLog ${APACHE_LOG_DIR}/%s-error.log
    CustomLog ${APACHE_LOG_DIR}/%s-access.log combined
</VirtualHost>
`, domain, domain, target, target, domain, domain)

	f.writeVhost(domain, vhost)
	f.enableSite(domain)
	okA, msgA := f.validateApache()
	if !okA {
		s.err("Apache config error: " + msgA)
		f.disableSiteCmd(domain)
		return nil
	}
	f.reloadApache()
	f.saveSiteMeta(domain, "proxy", target, "", "", "apache", createDNS, "", 0)

	if createDNS && ip != "" {
		if _, _, err := f.dnsZones(domain, ip, "", "", ""); err != nil {
			return err
		}
	}

	f.applyReloads()

	s.ok("Reverse proxy created: http://" + domain + " -> " + target)
	f.answers.InputPause(s, "\n  Press Enter to continue...")

	return nil
}

func (f *Flow) createNginxProxy() error {
	s := f.stream
	s.menuHeader("Create Reverse Proxy Site (Nginx)")

	domain, err := askDomain(s, f.answers)
	if err != nil {
		return err
	}
	if domain == "" {
		return nil
	}

	dnsChoice, err := f.answers.Ask(s, "  Create DNS zones for this site? (y/N): ", "dns_choice")
	if err != nil {
		return err
	}
	createDNS := strings.ToLower(strings.TrimSpace(dnsChoice)) == "y"

	var ip string
	if createDNS {
		ip, err = f.promptForIP("Server IP")
		if err != nil {
			return err
		}
		if ip == "" {
			createDNS = false
		}
	}

	target, err := f.answers.Ask(s, "  Proxy target [localhost:3000]: ", "backend")
	if err != nil {
		return err
	}
	target = strings.TrimSpace(target)
	if target == "" {
		target = "localhost:3000"
	}
	if !strings.HasPrefix(target, "http://") && !strings.HasPrefix(target, "https://") {
		target = "http://" + target
	}

	vhost := fmt.Sprintf(`server {
    listen 80;
    server_name %s www.%s;

    location / {
        proxy_pass %s;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    access_log /var/log/nginx/%s-access.log;
    error_log  /var/log/nginx/%s-error.log;
}
`, domain, domain, target, domain, domain)

	f.writeNginxVhost(domain, vhost)
	f.nginxEnableSite(domain)
	okN, msgN := f.validateNginx()
	if !okN {
		s.err("Nginx config error: " + msgN)
		f.nginxDisableSite(domain)
		return nil
	}
	f.reloadNginx()
	f.saveSiteMeta(domain, "proxy", target, "", "", "nginx", createDNS, "", 0)

	if createDNS && ip != "" {
		if _, _, err := f.dnsZones(domain, ip, "", "", ""); err != nil {
			return err
		}
	}

	f.applyReloads()

	s.ok("Reverse proxy created: http://" + domain + " -> " + target)
	if createDNS {
		s.WriteRaw("  DNS:       created\n")
	}
	f.answers.InputPause(s, "\n  Press Enter to continue...")

	return nil
}

// ─────────────────────────────────────────
//  Container site (Nginx proxy to docker)
// ─────────────────────────────────────────

// isDockerInstalled — python is_docker_installed().
func isDockerInstalled() bool {
	_, err := exec.LookPath("docker")
	return err == nil
}

// findFreeDockerPort — python _find_free_docker_port(): first bindable
// 127.0.0.1 port from start, skipping the apache backends.
func findFreeDockerPort(start, maxTries int) int {
	if start == 0 {
		start = 8080
	}
	if maxTries == 0 {
		maxTries = 200
	}
	for port := start; port < start+maxTries; port++ {
		if apacheBackendPorts[port] {
			continue // Apache lives here
		}
		l, err := netListen("127.0.0.1:" + strconv.Itoa(port))
		if err != nil {
			continue
		}
		_ = l.Close()
		return port
	}
	return 0
}

func (f *Flow) createContainerSite() error {
	s := f.stream
	s.menuHeader("Create Container Site (Nginx Proxy)")

	if !isDockerInstalled() {
		s.err("Docker is not installed. Please install it first.")
		f.answers.InputPause(s, "\n  Press Enter to continue...")
		return nil
	}

	if !f.ensureNginx() {
		return nil
	}

	domain, err := askDomain(s, f.answers)
	if err != nil {
		return err
	}
	if domain == "" {
		return nil
	}

	image, err := f.answers.Ask(s, "  Container image [e.g. nginx:alpine]: ", "image")
	if err != nil {
		return err
	}
	image = strings.TrimSpace(image)
	if image == "" {
		return nil
	}

	intPortStr, err := f.answers.Ask(s, "  Internal port [80]: ", "port")
	if err != nil {
		return err
	}
	intPortStr = strings.TrimSpace(intPortStr)
	if intPortStr == "" {
		intPortStr = "80"
	}
	if !allDigits(intPortStr) {
		s.err("Port must be a number.")
		return nil
	}
	internalPort := intPortStr

	hostPort := findFreeDockerPort(8080, 200)
	if hostPort == 0 {
		s.err("Could not find a free host port.")
		return nil
	}

	netChk := f.run(60*time.Second, "docker", "network", "inspect", "zervermanager_net")
	if netChk.Exit != 0 {
		s.step("Creating Docker network 'zervermanager_net'...")
		f.run(60*time.Second, "docker", "network", "create", "zervermanager_net")
	}

	containerName := "site-" + strings.ReplaceAll(domain, ".", "-")

	s.step(fmt.Sprintf("Starting container %s on port %d...", containerName, hostPort))
	rc, e := f.runLiveTimeout(10*time.Minute, "docker", "run", "-d", "--name", containerName,
		"--network", "zervermanager_net",
		"-p", fmt.Sprintf("127.0.0.1:%d:%s", hostPort, internalPort),
		"--restart", "unless-stopped",
		image)
	if rc != 0 {
		s.err("Container failed to start:\n" + e)
		return nil
	}

	target := fmt.Sprintf("http://127.0.0.1:%d", hostPort)

	dnsChoice, err := f.answers.Ask(s, "  Create DNS zones for this site? (y/N): ", "dns_choice")
	if err != nil {
		return err
	}
	createDNS := strings.ToLower(strings.TrimSpace(dnsChoice)) == "y"

	var ip string
	if createDNS {
		ip, err = f.promptForIP("Server IP")
		if err != nil {
			return err
		}
		if ip == "" {
			createDNS = false
		}
	}

	vhost := fmt.Sprintf(`server {
    listen 80;
    server_name %s www.%s;

    location / {
        proxy_pass %s;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    access_log /var/log/nginx/%s-access.log;
    error_log  /var/log/nginx/%s-error.log;
}
`, domain, domain, target, domain, domain)

	s.step("Writing Nginx vhost config...")
	f.writeNginxVhost(domain, vhost)
	f.nginxEnableSite(domain)

	okN, msgN := f.validateNginx()
	if !okN {
		s.err("Nginx config error: " + msgN)
		f.nginxDisableSite(domain)
		f.run(60*time.Second, "docker", "stop", containerName)
		f.run(60*time.Second, "docker", "rm", containerName)
		return nil
	}

	f.reloadNginx()
	f.saveSiteMeta(domain, "docker", "container:"+image, "", "", "nginx", createDNS, containerName, hostPort)

	if createDNS && ip != "" {
		revZone := reverseZoneName(ip)
		fwdFile := fwdZoneFile(domain)
		revFile := reverseZoneFile(ip)

		if err := f.ensureBind9(); err != nil {
			s.err("BIND9 unavailable — skipping DNS zones.")
		} else {
			if !zoneExists(domain) {
				s.step("Writing forward zone...")
				f.writeForwardZone(domain, ip, fwdFile)
				okZ, msgZ := f.validateZone(domain, fwdFile)
				if okZ {
					f.addZoneToConf(domain, fwdFile)
					s.ok("Forward zone created.")
				} else {
					_ = os.Remove(fwdFile)
					s.err(msgZ)
				}
			}

			if !zoneExists(revZone) {
				s.step("Writing reverse zone...")
				f.writeReverseZone(domain, ip, revFile)
				okR, msgR := f.validateZone(revZone, revFile)
				if okR {
					f.addZoneToConf(revZone, revFile)
					s.ok("Reverse zone created.")
				} else {
					_ = os.Remove(revFile)
					f.removeZoneFromConf(domain)
					s.err(msgR)
				}
			}

			okB, msgB := f.validateBind9()
			if !okB {
				s.err(msgB)
			} else {
				f.reloadBind9()
			}
		}
	}

	f.applyReloads()
	s.ok(fmt.Sprintf("Container site created: http://%s -> %s", domain, image))
	f.answers.InputPause(s, "\n  Press Enter to continue...")

	return nil
}
