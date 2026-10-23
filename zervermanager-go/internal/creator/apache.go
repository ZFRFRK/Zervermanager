// Apache helpers — parity with the python "Apache helpers" block:
// validate/enable/ensure_sites/templates, plus the vhost templates.
package creator

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"zervermanager/internal/run"
)

// ensureModules — python ensure_modules(): a2enmod rewrite + ssl.
func (f *Flow) ensureModules() {
	_ = f.run(60*time.Second, "/usr/sbin/a2enmod", "rewrite")
	_ = f.run(60*time.Second, "/usr/sbin/a2enmod", "ssl")
}

// validateApache — python validate_apache() via apache2ctl configtest.
func (f *Flow) validateApache() (bool, string) {
	test := f.run(120*time.Second, "/usr/sbin/apache2ctl", "configtest")
	if test.Exit != 0 {
		return false, test.Stdout + test.Stderr
	}
	return true, "Syntax OK"
}

// reloadApache / writeVhost / enableSite / disableSiteCmd.
func (f *Flow) reloadApache() { f.markReload("apache2") }
func (f *Flow) enableSite(d string) run.Result {
	return f.run(60*time.Second, "/usr/sbin/a2ensite", d+".conf")
}
func (f *Flow) disableSiteCmd(d string) run.Result {
	return f.run(60*time.Second, "/usr/sbin/a2dissite", d+".conf")
}

// writeVhost — python write_vhost().
func (f *Flow) writeVhost(domain, content string) (string, bool) {
	path := apacheSitesAvailable + "/" + domain + ".conf"
	return path, f.safeWrite(path, content)
}

// createDocroot — python create_docroot().
func (f *Flow) createDocroot(docroot, domain string) {
	_ = os.MkdirAll(docroot, 0o755)
	index := filepath.Join(docroot, "index.html")
	if _, err := os.Stat(index); os.IsNotExist(err) {
		_ = os.WriteFile(index, []byte(fmt.Sprintf("<html><body><h1>%s</h1></body></html>", domain)), 0o644)
	}
}

// httpVhost — python http_vhost(). NOTE: apache sits on :8080 behind
// the nginx front door (configure_apache_ports contract).
func httpVhost(domain, docroot string) string {
	return fmt.Sprintf(`
<VirtualHost *:8080>
    ServerName %s
    ServerAlias www.%s

    DocumentRoot "%s"

    <Directory "%s">
        Options Indexes FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>
</VirtualHost>
`, domain, domain, docroot, docroot)
}

// httpsVhost — python https_vhost().
func httpsVhost(domain, docroot, cert, key string) string {
	return fmt.Sprintf(`
<VirtualHost *:8080>
    ServerName %s
    ServerAlias www.%s

    RewriteEngine On
    RewriteRule ^ https://%%{HTTP_HOST}%%{REQUEST_URI} [R=301,L]
</VirtualHost>

<VirtualHost *:8443>
    ServerName %s
    ServerAlias www.%s

    DocumentRoot "%s"

    SSLEngine On
    SSLCertificateFile %s
    SSLCertificateKeyFile %s

    <Directory "%s">
        Options Indexes FollowSymLinks
        AllowOverride All
        Require all granted
    </Directory>
</VirtualHost>
`, domain, domain, domain, domain, docroot, cert, key, docroot)
}

// makeSelfSigned — python make_self_signed().
func (f *Flow) makeSelfSigned(domain string) (cert, key, stderr string) {
	sslDir := "/etc/ssl/" + domain
	_ = os.MkdirAll(sslDir, 0o755)

	cert = sslDir + "/" + domain + ".crt"
	key = sslDir + "/" + domain + ".key"

	res := f.run(120*time.Second, "openssl", "req", "-x509", "-nodes",
		"-days", "365", "-newkey", "rsa:2048",
		"-keyout", key,
		"-out", cert,
		"-subj", "/CN="+domain)

	if res.Exit != 0 {
		return "", "", res.Stderr
	}
	return cert, key, ""
}

// ensureNginx — python ensure_nginx(): dpkg probe, silent auto-install,
// site dirs.
func (f *Flow) ensureNginx() bool {
	if dpkgInstalled("nginx") {
		_ = os.MkdirAll(nginxSitesAvailable, 0o755)
		_ = os.MkdirAll(nginxSitesEnabled, 0o755)
		return true
	}

	s := f.stream
	s.WriteRaw("\n" + cYELLOW + "  Nginx is not installed." + cRESET + "\n")
	s.WriteRaw("  " + cCYAN + "Auto-installing..." + cRESET + "\n\n")

	rc, stderr := f.runLiveTimeout(30*time.Minute, "apt-get", "install", "-y", "--no-install-recommends", "nginx")
	if rc != 0 {
		s.err("Install failed:\n" + strings.TrimSpace(stderr))
		return false
	}

	r := f.run(120*time.Second, "systemctl", "enable", "--now", "nginx")
	if r.Exit == 0 {
		s.ok("Nginx installed and enabled.")
	} else {
		s.err(strings.TrimSpace(r.Stderr))
	}

	_ = os.MkdirAll(nginxSitesAvailable, 0o755)
	_ = os.MkdirAll(nginxSitesEnabled, 0o755)
	return true
}

// validateNginx — python validate_nginx().
func (f *Flow) validateNginx() (bool, string) {
	res := f.run(120*time.Second, "/usr/sbin/nginx", "-t")
	if res.Exit != 0 {
		return false, res.Stdout + res.Stderr
	}
	return true, "Syntax OK"
}

// nginxSiteEnabled / enable / disable — symlink semantics.
func (f *Flow) nginxEnableSite(domain string) {
	src := nginxSitesAvailable + "/" + domain + ".conf"
	dest := nginxSitesEnabled + "/" + domain + ".conf"
	if _, err := os.Lstat(dest); err == nil {
		return // symlink already present
	}
	_ = os.Symlink(src, dest)
}

func (f *Flow) nginxDisableSite(domain string) {
	dest := nginxSitesEnabled + "/" + domain + ".conf"
	if _, err := os.Lstat(dest); err == nil {
		_ = os.Remove(dest)
	}
}

// writeNginxVhost — python write_nginx_vhost().
func (f *Flow) writeNginxVhost(domain, content string) (string, bool) {
	_ = os.MkdirAll(nginxSitesAvailable, 0o755)
	path := nginxSitesAvailable + "/" + domain + ".conf"
	return path, f.safeWrite(path, content)
}

// nginxHttpVhost — python nginx_http_vhost().
func nginxHttpVhost(domain, docroot string) string {
	return fmt.Sprintf(`server {
    listen 80;
    server_name %s www.%s;

    root %s;
    index index.html index.php;

    location / {
        try_files $uri $uri/ =404;
    }
}
`, domain, domain, docroot)
}

// nginxHttpsVhost — python nginx_https_vhost().
func nginxHttpsVhost(domain, docroot, cert, key string) string {
	return fmt.Sprintf(`server {
    listen 80;
    server_name %s www.%s;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name %s www.%s;

    root %s;
    index index.html index.php;

    ssl_certificate     %s;
    ssl_certificate_key %s;

    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    location / {
        try_files $uri $uri/ =404;
    }
}
`, domain, domain, domain, domain, docroot, cert, key)
}

// nginxLempHttpVhost — python nginx_lemp_http_vhost().
func nginxLempHttpVhost(domain, docroot, fpmSocket string) string {
	fpm := fpmSocket
	if strings.HasPrefix(fpmSocket, "/") {
		fpm = "unix:" + fpmSocket
	}
	return fmt.Sprintf(`server {
    listen 80;
    server_name %s www.%s;

    root %s;
    index index.php index.html;

    location / {
        try_files $uri $uri/ =404;
    }

    location ~ \.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass %s;
    }

    location ~ /\.ht {
        deny all;
    }
}
`, domain, domain, docroot, fpm)
}

// nginxLempHttpsVhost — python nginx_lemp_https_vhost().
func nginxLempHttpsVhost(domain, docroot, cert, key, fpmSocket string) string {
	fpm := fpmSocket
	if strings.HasPrefix(fpmSocket, "/") {
		fpm = "unix:" + fpmSocket
	}
	return fmt.Sprintf(`server {
    listen 80;
    server_name %s www.%s;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name %s www.%s;

    root %s;
    index index.php index.html;

    ssl_certificate     %s;
    ssl_certificate_key %s;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    location / {
        try_files $uri $uri/ =404;
    }

    location ~ \.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass %s;
    }

    location ~ /\.ht {
        deny all;
    }
}
`, domain, domain, domain, domain, docroot, cert, key, fpm)
}

// nginxProxyVhost — the front-door proxy template used by the static,
// lamp and wordpress apache flows.
func nginxProxyVhost(domain string) string {
	return fmt.Sprintf(`server {
    listen 80;
    server_name %s www.%s;
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}`, domain, domain)
}
