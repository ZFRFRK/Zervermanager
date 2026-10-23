// Package web is the net/http replacement for the Python
// WebUIHandler. Guard order and response contracts replicate do_POST /
// do_GET of zervermanager.py (goplan.md §2.3, §2.4).
package web

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync/atomic"
	"time"

	"zervermanager/internal/auth"
	"zervermanager/internal/sites"
)

const (
	// APIVersion — python API_VERSION "2.0.0", sent as X-ZM-API-Version
	// on every authenticated /api GET response.
	APIVersion = "2.0.0"
	// BackendVersion — python SCRIPT_VERSION 1.3.27.
	BackendVersion = "1.3.27"
)

const (
	// SessionCookie is the only cookie the manager uses.
	SessionCookie = "zm_session"
	// CSRFHeader is the header name set by the frontend.
	CSRFHeader = "X-ZM-CSRF"
)

// safeModeAllowed mirrors SAFE_MODE_ALLOWED in do_POST.
var safeModeAllowed = []string{
	"/api/snapshots", "/api/changes", "/api/incidents",
	"/api/doctor", "/api/auth/reset", "/api/confirm",
	"/api/safe-mode",
}

// Server wires configuration into handlers.
type Server struct {
	Cfg      Config
	Auth     *auth.Authenticator
	safeMode atomic.Bool
}

// Config carries all runtime paths and flags.
type Config struct {
	Addr          string        // ":8090"
	WebDir        string        // web_config/
	AuthFile      string        // /etc/zervermanager/auth.json
	IncidentsFile string        // /var/lib/zervermanager/incidents.jsonl
	Sites         sites.Dirs    // apache/nginx available+enabled, meta dir
	StatusTTL     time.Duration // systemctl batch TTL (python 3.0)
	Shadow        bool          // read-only shadow mode (goplan §6)
}

// New builds a Server.
func New(cfg Config, a *auth.Authenticator) *Server {
	if cfg.StatusTTL <= 0 {
		cfg.StatusTTL = 3 * time.Second
	}
	return &Server{Cfg: cfg, Auth: a}
}

// SetSafeMode toggles the safe-mode gate (backend for /api/safe-mode).
func (s *Server) SetSafeMode(on bool) { s.safeMode.Store(on) }

// SafeMode reports the current gate state.
func (s *Server) SafeMode() bool { return s.safeMode.Load() }

// Handler returns the fully routed http.Handler.
func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/", s.getAPI)
	mux.HandleFunc("POST /api/", s.postAPI)
	mux.HandleFunc("/", s.serveStatic)
	return mux
}

// serveStatic — SimpleHTTPRequestHandler with directory=web_config.
// http.FileServer provides the same index.html-for-/ and
// traversal-refusal behaviour, with one tweak: FileServer 301-redirects
// every "…/index.html" to its directory, while the python handler serves
// it directly — the rewrite keeps those URLs byte-compatible.
func (s *Server) serveStatic(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/index.html" || strings.HasSuffix(r.URL.Path, "/index.html") {
		r.URL.Path = strings.TrimSuffix(r.URL.Path, "index.html")
	}
	http.FileServer(http.Dir(s.Cfg.WebDir)).ServeHTTP(w, r)
}

// getAPI replaces do_GET's api branch:
//   - /api/session + /api/health are auth-free
//   - every other /api/* requires a session → 401
//   - authenticated responses carry X-ZM-API-Version
func (s *Server) getAPI(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Path

	authFree := path == "/api/session" || path == "/api/health"
	if !authFree {
		if _, ok := s.sessionOf(r); !ok {
			unauthorized(w)
			return
		}
	}
	w.Header().Set("X-ZM-API-Version", APIVersion)

	switch path {
	case "/api/session":
		s.handleSession(w, r)
	case "/api/health":
		s.handleHealth(w)
	case "/api/status":
		s.handleStatus(w)
	case "/api/sites":
		writeJSON(w, http.StatusOK, mustJSON(s.handleSites()))
	default:
		// python handle_api_get: unhandled routes answer 200 with {}.
		writeJSON(w, http.StatusOK, []byte("{}"))
	}
}

// postAPI — do_POST in order: login/logout are exempt; then
// session → CSRF → safe-mode → Origin/Host → routes.
func (s *Server) postAPI(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Path

	if path == "/api/login" || path == "/api/logout" {
		switch path {
		case "/api/login":
			s.handleLogin(w, r)
		case "/api/logout":
			s.handleLogout(w, r)
		}
		return
	}

	// ── Session enforcement ───────────────────────────────────────────
	if _, ok := s.sessionOf(r); !ok {
		unauthorized(w)
		return
	}

	// ── CSRF ─────────────────────────────────────────────────────────
	if !s.Auth.CompareCSRF(sessionID(r), r.Header.Get(CSRFHeader)) {
		writeJSON(w, http.StatusForbidden, []byte(`{"error":"invalid CSRF token"}`))
		return
	}

	// ── Safe-mode gate (prefix set; parity) ──────────────────────────
	if s.SafeMode() {
		allowed := false
		for _, p := range safeModeAllowed {
			if path == p || strings.HasPrefix(path, p+"/") {
				allowed = true
				break
			}
		}
		if !allowed {
			writeJSON(w, http.StatusServiceUnavailable,
				[]byte(`{"error":"safe mode active - only recovery routes are available"}`))
			return
		}
	}

	// ── Origin / Host (CSRF + DNS-rebinding) ─────────────────────────
	origin := r.Header.Get("Origin")
	host := r.Host
	if origin != "" && !strings.HasPrefix(origin, "http://") && !strings.HasPrefix(origin, "https://") {
		http.Error(w, "Forbidden: bad Origin", http.StatusForbidden)
		return
	}
	if origin != "" && host != "" && !strings.Contains(origin, host) {
		http.Error(w, "Forbidden: cross-origin request", http.StatusForbidden)
		return
	}

	// ── Shadow mode (goplan §6): read-only parity prober ────────────
	if s.Cfg.Shadow {
		writeJSON(w, http.StatusServiceUnavailable,
			[]byte(`{"error":"shadow mode - read only"}`))
		return
	}

	// ── M2: Make Site (python handle_makesite_post, streamed) ────────
	if handleMakesiteRoute(path) {
		s.handleMakesite(w, r, path)
		return
	}

	// Routes land here from M3 on. Until then the python contract for
	// unknown POST paths applies: 404. The body is JSON (not python's
	// send_error HTML/text) so the web UI's res.json() renders a clean
	// toast instead of "unexpected character at line 1 column 1".
	writeJSON(w, http.StatusNotFound,
		[]byte(`{"success": false, "message": "Not implemented yet on the Go port (M3)."}`))
}

// sessionOf extracts and validates the session for the request.
func (s *Server) sessionOf(r *http.Request) (*auth.Session, bool) {
	return s.Auth.Session(sessionID(r))
}

// sessionID parses the Cookie header char-for-char like the Python
// (split ';', strip, "zm_session=" prefix).
func sessionID(r *http.Request) string {
	for _, part := range strings.Split(r.Header.Get("Cookie"), ";") {
		part = strings.TrimSpace(part)
		if strings.HasPrefix(part, SessionCookie+"=") {
			id := part[len(SessionCookie)+1:]
			if id == "" {
				return ""
			}
			return id
		}
	}
	return ""
}

// unauthorized writes the exact bytes of the python 401 body.
func unauthorized(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusUnauthorized)
	_, _ = w.Write([]byte(`{"error":"login required"}`))
}

// login sets the cookie with the identical attribute string.
func (s *Server) handleLogin(w http.ResponseWriter, r *http.Request) {
	var creds struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	if err := json.NewDecoder(r.Body).Decode(&creds); err != nil {
		// Unreachable for a well-behaved frontend; python would raise
		// on a malformed body — a 400 is strictly kinder here.
		writeJSON(w, http.StatusBadRequest, []byte(`{"error":"invalid JSON body"}`))
		return
	}
	ip := hostOf(r)

	sid, csrf, ok, errText := s.Auth.Login(creds.Username, creds.Password, ip)
	if !ok {
		writeJSON(w, http.StatusUnauthorized, mustJSON(loginFail{OK: false, Error: errText}))
		return
	}

	sessRow, good := s.Auth.Session(sid)
	if !good {
		http.Error(w, "session vanished", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Set-Cookie", fmt.Sprintf("%s=%s; HttpOnly; SameSite=Strict; Path=/", SessionCookie, sid))
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(mustJSON(loginOK{
		OK:    true,
		CSRF:  csrf,
		Actor: sessRow.Actor,
		Role:  sessRow.Role,
	}))
}

// Response field order matches python dict insertion order so the JSON
// is byte-close to json.dumps() (normalizer-exempt anyway, but the
// frontend devtools diffs read nicer).
type loginOK struct {
	OK    bool   `json:"ok"`
	CSRF  string `json:"csrf"`
	Actor string `json:"actor"`
	Role  string `json:"role"`
}

type loginFail struct {
	OK    bool   `json:"ok"`
	Error string `json:"error"`
}

func (s *Server) handleLogout(w http.ResponseWriter, r *http.Request) {
	s.Auth.Logout(sessionID(r))
	// Parity: cookie cleared with Max-Age=0; empty 200 body.
	w.Header().Set("Set-Cookie", fmt.Sprintf("%s=; Max-Age=0; Path=/", SessionCookie))
	w.WriteHeader(http.StatusOK)
}

func (s *Server) handleSession(w http.ResponseWriter, r *http.Request) {
	sess, ok := s.sessionOf(r)
	if !ok {
		writeJSON(w, http.StatusUnauthorized, []byte(`{"ok":false}`))
		return
	}
	writeJSON(w, http.StatusOK, mustJSON(sessionOK{
		OK:    true,
		Actor: sess.Actor,
		Role:  sess.Role,
		CSRF:  sess.CSRF,
	}))
}

type sessionOK struct {
	OK    bool   `json:"ok"`
	Actor string `json:"actor"`
	Role  string `json:"role"`
	CSRF  string `json:"csrf"`
}

func hostOf(r *http.Request) string {
	ip := r.RemoteAddr
	if i := strings.LastIndex(ip, ":"); i > 0 {
		ip = ip[:i]
	}
	return strings.Trim(ip, "[]")
}
