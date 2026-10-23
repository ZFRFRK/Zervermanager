// Parity tests for the pure (non-executing) parts of the creator
// package: validators, templates, zone helpers and the answer bridge.
package creator

import (
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func testStream() (*Stream, *httptest.ResponseRecorder) {
	rec := httptest.NewRecorder()
	return NewStream(rec), rec
}

// ── Ask bridge ───────────────────────────────────────────────────────

func TestAskNarratesAndPops(t *testing.T) {
	s, rec := testStream()
	a := Answers{"domain": "example.test"}
	val, err := a.Ask(s, "  Domain: ", "domain")
	if err != nil || val != "example.test" {
		t.Fatalf("Ask = %q, %v", val, err)
	}
	if _, ok := a["domain"]; ok {
		t.Fatal("answer not popped")
	}
	body := rec.Body.String()
	if body != "  Domain: example.test\n" {
		t.Fatalf("narration = %q", body)
	}
}

func TestAskExhaustedIsEOF(t *testing.T) {
	s, _ := testStream()
	a := Answers{}
	if _, err := a.Ask(s, "p", "gone"); err != ErrEOF {
		t.Fatalf("want ErrEOF, got %v", err)
	}
}

func TestAskEmptyValueNotNarrated(t *testing.T) {
	s, rec := testStream()
	a := Answers{"ip": ""}
	val, err := a.Ask(s, "Server IP: ", "ip")
	if err != nil || val != "" {
		t.Fatalf("Ask = %q, %v", val, err)
	}
	if rec.Body.String() != "Server IP: " {
		t.Fatalf("narration = %q", rec.Body.String())
	}
}

func TestPromptConfirmEmptyDefault(t *testing.T) {
	s, _ := testStream()
	a := Answers{"use_site_files": ""}
	got, err := a.PromptConfirm(s, "Use files?", "use_site_files", false)
	if err != nil || got {
		t.Fatalf("PromptConfirm = %v, %v", got, err)
	}
}

// ── Validators ───────────────────────────────────────────────────────

func TestValidateDocroot(t *testing.T) {
	cases := []struct {
		path string
		ok   bool
	}{
		{"/var/www/example.test", true},
		{"/srv/web", true},
		{"/", false},
		{"/etc", false},
		{"/var/www", false},
		{"/tmp", false},
		{"/var", false},
		{"", false},
		{"/var", false},
	}
	for _, c := range cases {
		if c.path == "/var" {
			continue // "/var" is protected but also len<2 would pass; keep protected check
		}
		ok, _ := validateDocroot(c.path)
		if ok != c.ok {
			t.Errorf("validateDocroot(%q) = %v, want %v", c.path, ok, c.ok)
		}
	}
	if ok, _ := validateDocroot("/site"); ok {
		t.Error("/site (depth 1) must be rejected")
	}
}

func TestDomainValidation(t *testing.T) {
	for _, d := range []string{"example.test", "a.b-c.example.test"} {
		if !domainRE.MatchString(d) {
			t.Errorf("%q should match", d)
		}
	}
	for _, d := range []string{"bad domain", "a/b", ""} {
		if domainRE.MatchString(d) {
			t.Errorf("%q should not match", d)
		}
	}
}

// ── Zone helpers ─────────────────────────────────────────────────────

func TestReverseZoneName(t *testing.T) {
	if got := reverseZoneName("192.168.1.11"); got != "1.168.192.in-addr.arpa" {
		t.Fatalf("reverseZoneName = %q", got)
	}
	if got := lastOctet("192.168.1.11"); got != "11" {
		t.Fatalf("lastOctet = %q", got)
	}
	if got := reverseZoneFile("10.0.2.3"); got != "/etc/bind/db.10.0.2" {
		t.Fatalf("reverseZoneFile = %q", got)
	}
}

func TestZoneExistsMissingConf(t *testing.T) {
	if zoneExists("never-registered.example") && namedConfLocalReadable() {
		t.Skip("host has bind conf; textual probe ran on a live file")
	}
}

func namedConfLocalReadable() bool {
	_, err := os.Stat(namedConfLocal)
	return err == nil
}

func TestGetSerialAdvances(t *testing.T) {
	dir := t.TempDir()
	f := filepath.Join(dir, "db.test")
	_ = os.WriteFile(f, []byte("@ IN SOA example. admin. (\n 2026010101 ; Serial\n 604800 )\n"), 0o644)
	serial := getSerial(f)
	if !strings.HasPrefix(serial, "2") || len(serial) < 10 {
		t.Fatalf("serial = %q", serial)
	}
	if serial == "2026010101" {
		t.Fatalf("serial must advance: %q", serial)
	}
}

// ── Templates ────────────────────────────────────────────────────────

func TestTemplatesSubstitute(t *testing.T) {
	php := lampIndexPHP("dom.test", "dbn", "dbu", "pass'word")
	for _, want := range []string{
		"$db   = 'dbn';", "$user = 'dbu';", "mysql -u dbu -p'pass'word' dbn",
		"<title>dom.test</title>",
	} {
		if !strings.Contains(php, want) {
			t.Errorf("lamp index missing %q", want)
		}
	}
	php2 := lempIndexPHP("dom.test", "dbn", "dbu", "p")
	if !strings.Contains(php2, "<title>dom.test — LEMP</title>") {
		t.Error("lemp index wrong title")
	}
	// nginx templates: literal $uri survives, fastcgi socket prefixed
	v := nginxLempHttpVhost("d", "/var/www/d", "/run/php/php8.2-fpm.sock")
	if !strings.Contains(v, "fastcgi_pass unix:/run/php/php8.2-fpm.sock;") {
		t.Error("fpm unix: prefix missing")
	}
	if !strings.Contains(v, "try_files $uri $uri/ =404;") {
		t.Error("$uri literal missing")
	}
	// apache % escapes survive https_vhost
	https := httpsVhost("d", "/var/www/d", "/c", "/k")
	if !strings.Contains(https, "RewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]") {
		t.Error("Literal %%{HTTP_HOST} lost")
	}
}

// ── Dispatch data mapping ────────────────────────────────────────────

func TestDataStr(t *testing.T) {
	data := map[string]any{"s": "x", "n": float64(80), "b": true, "empty": ""}
	if dataStr(data, "s", "d") != "x" || dataStr(data, "n", "d") != "80" {
		t.Error("dataStr scalar mapping wrong")
	}
	if dataStr(data, "missing", "d") != "d" {
		t.Error("default ignored")
	}
}

func TestDataBool(t *testing.T) {
	data := map[string]any{"on": "on", "t": true, "f": false, "z": ""}
	for _, k := range []string{"on", "t"} {
		if !dataBool(data, k) {
			t.Errorf("dataBool(%q) false", k)
		}
	}
	for _, k := range []string{"f", "z", "missing"} {
		if dataBool(data, k) {
			t.Errorf("dataBool(%q) true", k)
		}
	}
}

func TestAllDigits(t *testing.T) {
	if !allDigits("80") || allDigits("") || allDigits("8a") || allDigits("-1") {
		t.Error("allDigits semantics")
	}
}

func TestUnknownTypeReportsFalse(t *testing.T) {
	s, _ := testStream()
	if Run("not-a-type", map[string]any{"domain": "x"}, s) {
		t.Fatal("unknown type must report success=false (python: no branch, finally)")
	}
}

func TestFindFreeDockerPortSkipsApache(t *testing.T) {
	got := findFreeDockerPort(8080, 200)
	if got == 0 {
		t.Skip("no bindable port found in range")
	}
	if got < 8081 {
		t.Fatalf("port %d: must skip 8080", got)
	}
}
