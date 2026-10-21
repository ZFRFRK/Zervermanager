package web

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"zervermanager/internal/auth"
	"zervermanager/internal/sites"
)

func testServer(t *testing.T) *Server {
	t.Helper()
	dir := t.TempDir()
	webDir := filepath.Join(dir, "web_config")
	if err := os.Mkdir(webDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(webDir, "index.html"), []byte("<html>ui</html>"), 0o644); err != nil {
		t.Fatal(err)
	}

	acct := `{"admin": {"hash": "` + auth.HashPassword("pw123") + `", "role": "admin"}}`
	authFile := filepath.Join(dir, "auth.json")
	if err := os.WriteFile(authFile, []byte(acct), 0o600); err != nil {
		t.Fatal(err)
	}

	cfg := Config{
		Addr:          ":0",
		WebDir:        webDir,
		AuthFile:      authFile,
		IncidentsFile: filepath.Join(dir, "incidents.jsonl"), // absent → empty
		Sites: sites.Dirs{
			ApacheAvailable: filepath.Join(dir, "apache-avail"),
			ApacheEnabled:   filepath.Join(dir, "apache-enab"),
			NginxAvailable:  filepath.Join(dir, "nginx-avail"),
			NginxEnabled:    filepath.Join(dir, "nginx-enab"),
			MetaDir:         filepath.Join(dir, "meta"),
		},
		StatusTTL: time.Second,
	}
	return New(cfg, auth.New(authFile))
}

func doJSON(t *testing.T, h http.Handler, method, target, cookie, body string) *httptest.ResponseRecorder {
	t.Helper()
	var reader *bytes.Reader
	if body == "" {
		reader = bytes.NewReader(nil)
	} else {
		reader = bytes.NewReader([]byte(body))
	}
	req := httptest.NewRequest(method, target, reader)
	if cookie != "" {
		req.Header.Set("Cookie", cookie)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func loginCookie(t *testing.T, h http.Handler) string {
	t.Helper()
	rec := doJSON(t, h, http.MethodPost, "/api/login", "", `{"username":"admin","password":"pw123"}`)
	if rec.Code != 200 {
		t.Fatalf("login status = %d body=%s", rec.Code, rec.Body)
	}
	setCookie := rec.Header().Get("Set-Cookie")
	want := "; HttpOnly; SameSite=Strict; Path=/"
	if len(setCookie) < 32 || !hasSuffix(setCookie, want) {
		t.Fatalf("Set-Cookie attribute mismatch: %q (want suffix %q)", setCookie, want)
	}
	if !hasPrefix(setCookie, "zm_session=") {
		t.Fatalf("cookie name wrong: %q", setCookie)
	}
	return setCookie[len("zm_session=") : len("zm_session=")+32]
}

func hasPrefix(s, p string) bool { return len(s) >= len(p) && s[:len(p)] == p }
func hasSuffix(s, p string) bool { return len(s) >= len(p) && s[len(s)-len(p):] == p }

func TestUnauthenticatedAPIIsRejected(t *testing.T) {
	srv := testServer(t)
	h := srv.Handler()

	rec := doJSON(t, h, http.MethodGet, "/api/status", "", "")
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("status code = %d, want 401", rec.Code)
	}
	if got := rec.Body.String(); got != `{"error":"login required"}` {
		t.Fatalf("401 body = %q (parity mismatch)", got)
	}
	if rec.Header().Get("X-ZM-API-Version") != "" {
		t.Fatal("401 must not carry X-ZM-API-Version (python sends header only after auth)")
	}
}

func TestLoginSetsSessionAndStatusAPI(t *testing.T) {
	srv := testServer(t)
	h := srv.Handler()
	sid := loginCookie(t, h)
	cookie := "zm_session=" + sid

	rec := doJSON(t, h, http.MethodGet, "/api/status", cookie, "")
	if rec.Code != 200 {
		t.Fatalf("status code = %d", rec.Code)
	}
	if rec.Header().Get("X-ZM-API-Version") != APIVersion {
		t.Fatal("missing X-ZM-API-Version header")
	}
	var status map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &status); err != nil {
		t.Fatalf("status body not json: %v", err)
	}
	for _, key := range []string{"os", "apache2", "nginx", "mariadb", "docker_installed",
		"version", "api_version", "safe_mode", "open_incidents", "high_severity",
		"uptime", "uptime_seconds", "boot_time", "cpu", "cpu_cores", "cpu_load",
		"ram", "ram_used_gb", "ram_total_gb", "ram_formatted"} {
		if _, ok := status[key]; !ok {
			t.Fatalf("status missing key %q", key)
		}
	}
	if status["apache2"] != "stopped" || status["nginx"] != "stopped" {
		t.Fatalf("service tokens = %v / %v, want stopped tokens on unmanaged probe", status["apache2"], status["nginx"])
	}
	if status["version"] != BackendVersion {
		t.Fatalf("version = %v, want %q", status["version"], BackendVersion)
	}
}

func TestSessionEndpoint(t *testing.T) {
	srv := testServer(t)
	h := srv.Handler()
	sid := loginCookie(t, h)

	rec := doJSON(t, h, http.MethodGet, "/api/session", "zm_session="+sid, "")
	var sess struct {
		OK    bool   `json:"ok"`
		Actor string `json:"actor"`
		Role  string `json:"role"`
		CSRF  string `json:"csrf"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &sess); err != nil {
		t.Fatal(err)
	}
	if !sess.OK || sess.Actor != "admin" || sess.Role != "admin" || len(sess.CSRF) != 32 {
		t.Fatalf("session = %+v", sess)
	}

	rec = doJSON(t, h, http.MethodGet, "/api/session", "zm_session=bogus", "")
	if rec.Code != 401 || rec.Body.String() != `{"ok":false}` {
		t.Fatalf("bad session: code=%d body=%q", rec.Code, rec.Body)
	}
}

func TestLogout(t *testing.T) {
	srv := testServer(t)
	h := srv.Handler()
	sid := loginCookie(t, h)

	rec := doJSON(t, h, http.MethodPost, "/api/logout", "zm_session="+sid, "")
	if rec.Code != 200 {
		t.Fatalf("logout code = %d", rec.Code)
	}
	if got := rec.Header().Get("Set-Cookie"); got != "zm_session=; Max-Age=0; Path=/" {
		t.Fatalf("clear cookie = %q (exact python string required)", got)
	}
	rec2 := doJSON(t, h, http.MethodGet, "/api/session", "zm_session="+sid, "")
	if rec2.Code != 401 {
		t.Fatal("session survived logout")
	}
}

func TestCSRFGuardOnPost(t *testing.T) {
	srv := testServer(t)
	h := srv.Handler()
	sid := loginCookie(t, h)

	rec := doJSON(t, h, http.MethodPost, "/api/manage/nothing", "zm_session="+sid, "{}")
	if rec.Code != 403 || rec.Body.String() != `{"error":"invalid CSRF token"}` {
		t.Fatalf("csrf guard: code=%d body=%q", rec.Code, rec.Body)
	}

	// With a good CSRF, unknown POST routes 404 after the guard chain.
	req := httptest.NewRequest(http.MethodPost, "/api/manage/nothing", bytes.NewReader([]byte("{}")))
	req.Header.Set("Cookie", "zm_session="+sid)
	req.Header.Set("X-ZM-CSRF", mustCSRF(t, srv, sid))
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusNotFound {
		t.Fatalf("unknown POST = %d, want 404 (python send_error(404))", rec.Code)
	}
}

func mustCSRF(t *testing.T, srv *Server, sid string) string {
	t.Helper()
	sess, ok := srv.Auth.Session(sid)
	if !ok {
		t.Fatal("session missing")
	}
	return sess.CSRF
}

func TestHealthEndpoint(t *testing.T) {
	srv := testServer(t)
	h := srv.Handler()

	rec := doJSON(t, h, http.MethodGet, "/api/health", "", "")
	var health struct {
		OK            bool   `json:"ok"`
		OpenIncidents int    `json:"open_incidents"`
		HighSeverity  int    `json:"high_severity"`
		SafeMode      bool   `json:"safe_mode"`
		APIVersion    string `json:"api_version"`
		BackendVer    string `json:"backend_version"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &health); err != nil {
		t.Fatal(err)
	}
	if !health.OK || health.OpenIncidents != 0 || health.SafeMode ||
		health.APIVersion != APIVersion || health.BackendVer != BackendVersion {
		t.Fatalf("health = %+v", health)
	}
}

func TestUnknownAPIGetReturnsEmptyObject(t *testing.T) {
	srv := testServer(t)
	h := srv.Handler()
	sid := loginCookie(t, h)
	rec := doJSON(t, h, http.MethodGet, "/api/does-not-exist", "zm_session="+sid, "")
	if rec.Code != 200 || rec.Body.String() != "{}" {
		t.Fatalf("unknown GET = %d %q, want 200 {}", rec.Code, rec.Body)
	}
}

func TestStaticServingAndTraversal(t *testing.T) {
	srv := testServer(t)
	h := srv.Handler()

	rec := doJSON(t, h, http.MethodGet, "/index.html", "", "")
	if rec.Code != 200 || rec.Body.String() != "<html>ui</html>" {
		t.Fatalf("static = %d %q", rec.Code, rec.Body)
	}
	rec = doJSON(t, h, http.MethodGet, "/..%2f..%2ftesting", "", "")
	if rec.Code != http.StatusMovedPermanently && rec.Code != http.StatusNotFound {
		t.Fatalf("traversal probe = %d, want redirect/404", rec.Code)
	}
}

func TestSafeModeGate(t *testing.T) {
	srv := testServer(t)
	h := srv.Handler()
	sid := loginCookie(t, h)
	srv.SetSafeMode(true)

	req := httptest.NewRequest(http.MethodPost, "/api/install/mail", bytes.NewReader([]byte("{}")))
	req.Header.Set("Cookie", "zm_session="+sid)
	req.Header.Set("X-ZM-CSRF", mustCSRF(t, srv, sid))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusServiceUnavailable ||
		rec.Body.String() != `{"error":"safe mode active - only recovery routes are available"}` {
		t.Fatalf("safe-mode gate: code=%d body=%q", rec.Code, rec.Body)
	}
}

func TestOriginGuardOnPost(t *testing.T) {
	srv := testServer(t)
	h := srv.Handler()
	sid := loginCookie(t, h)
	csrf := mustCSRF(t, srv, sid)

	req := httptest.NewRequest(http.MethodPost, "/api/install/mail", bytes.NewReader([]byte("{}")))
	req.Header.Set("Cookie", "zm_session="+sid)
	req.Header.Set("X-ZM-CSRF", csrf)
	req.Header.Set("Origin", "ftp://evil.example")
	req.Host = "192.168.1.8:8090"
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 403 {
		t.Fatalf("bad origin scheme = %d, want 403", rec.Code)
	}

	req = httptest.NewRequest(http.MethodPost, "/api/install/mail", bytes.NewReader([]byte("{}")))
	req.Header.Set("Cookie", "zm_session="+sid)
	req.Header.Set("X-ZM-CSRF", csrf)
	req.Header.Set("Origin", "http://evil.example")
	req.Host = "192.168.1.8:8090"
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 403 {
		t.Fatalf("foreign origin host = %d, want 403", rec.Code)
	}
}
