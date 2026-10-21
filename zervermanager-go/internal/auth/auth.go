// Package auth is the Go port of Phase 0 of zervermanager.py:
// scrypt password hashing, in-memory session store, per-IP login rate
// limiting and CSRF token comparison. Every constant below is
// load-bearing parity; see goplan.md §2.3.
package auth

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"sync"
	"time"

	"golang.org/x/crypto/scrypt"
)

// Parity constants (zervermanager.py lines ~60-64).
const (
	SessionTTL  = 7 * 24 * time.Hour // _SESSION_TTL
	LoginRate   = 10                 // _LOGIN_RATE: max attempts per window
	LoginWindow = 300 * time.Second  // _LOGIN_WINDOW: 5 minutes
)

// scrypt parameters: hashlib.scrypt(pw, salt=salt, n=16384, r=8, p=1)
// — default key length in Python is 64 bytes.
const (
	scryptN = 16384
	scryptR = 8
	scryptP = 1
	keyLen  = 64
	saltLen = 16
)

// Error messages are parity — the frontend displays them verbatim.
const (
	ErrRateLimited  = "Too many attempts — try again later."
	ErrInvalidCreds = "Invalid credentials."
)

// HashPassword — storage format is hex(salt) + ":" + hex(dk), identical
// to _hash_password(). Files written by Python verify here unchanged.
func HashPassword(password string) string {
	salt := make([]byte, saltLen)
	if _, err := rand.Read(salt); err != nil {
		panic("crypto/rand unavailable: " + err.Error())
	}
	dk, err := scrypt.Key([]byte(password), salt, scryptN, scryptR, scryptP, keyLen)
	if err != nil {
		panic("scrypt failed: " + err.Error())
	}
	return hex.EncodeToString(salt) + ":" + hex.EncodeToString(dk)
}

// VerifyPassword — constant-time compare, identical to _verify_password().
// Malformed stored strings verify false (no panic), like the Python except.
func VerifyPassword(password, stored string) bool {
	saltHex, dkHex, ok := split2(stored, ":")
	if !ok {
		return false
	}
	salt, err1 := hex.DecodeString(saltHex)
	dk, err2 := hex.DecodeString(dkHex)
	if err1 != nil || err2 != nil {
		return false
	}
	candidate, err := scrypt.Key([]byte(password), salt, scryptN, scryptR, scryptP, keyLen)
	if err != nil {
		return false
	}
	return subtle.ConstantTimeCompare(candidate, dk) == 1
}

func split2(s, sep string) (string, string, bool) {
	for i := 0; i < len(s); i++ {
		if s[i] == sep[0] {
			return s[:i], s[i+1:], true
		}
	}
	return "", "", false
}

// Account is one entry of /etc/zervermanager/auth.json.
type Account struct {
	Hash string `json:"hash"`
	Role string `json:"role"`
}

// Session mirrors the _SESSION_STORE dicts: actor, role, ts, csrf.
type Session struct {
	Actor     string
	Role      string
	CSRF      string
	CreatedAt time.Time
}

// Authenticator holds all mutable auth state. Safe for concurrent use.
type Authenticator struct {
	mu       sync.Mutex
	sessions map[string]*Session // session id -> session
	attempts map[string][]time.Time
	authFile string
	now      func() time.Time // overridable in tests
}

// New returns an Authenticator reading auth.json from authFile on every
// login attempt (parity: Python re-reads the file per login, so a
// `zervermanager auth reset` on the CLI takes effect without a restart).
func New(authFile string) *Authenticator {
	return &Authenticator{
		sessions: map[string]*Session{},
		attempts: map[string][]time.Time{},
		authFile: authFile,
		now:      time.Now,
	}
}

// LoadConfig parses auth.json. Top-level non-object values (the
// "session_secret" key) are skipped — Python would never match them
// as accounts either.
func (a *Authenticator) LoadConfig() (map[string]Account, error) {
	data, err := os.ReadFile(a.authFile)
	if err != nil {
		return nil, err
	}
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, err
	}
	cfg := map[string]Account{}
	for name, v := range raw {
		if len(v) == 0 || v[0] != '{' {
			continue
		}
		var acct Account
		if err := json.Unmarshal(v, &acct); err != nil {
			continue
		}
		cfg[name] = acct
	}
	return cfg, nil
}

// Login attempts a login for ip. On success it returns the new session
// id and csrf; on failure ok=false with the exact Python error string.
func (a *Authenticator) Login(username, password, ip string) (sid, csrf string, ok bool, errText string) {
	now := a.now()

	a.mu.Lock()
	defer a.mu.Unlock()

	// Rate limiting — same accounting as Python: the attempt is recorded
	// BEFORE the credential check, so successes consume a slot too.
	bucket := a.attempts[ip]
	kept := bucket[:0]
	for _, t := range bucket {
		if now.Sub(t) < LoginWindow {
			kept = append(kept, t)
		}
	}
	a.attempts[ip] = kept
	if len(kept) >= LoginRate {
		return "", "", false, ErrRateLimited
	}
	a.attempts[ip] = append(kept, now)

	cfg, err := a.LoadConfig()
	if err != nil {
		return "", "", false, ErrInvalidCreds
	}
	acct, found := cfg[username]
	if !found || !VerifyPassword(password, acct.Hash) {
		return "", "", false, ErrInvalidCreds
	}

	sid = uuidHex()
	csrf = uuidHex()
	a.sessions[sid] = &Session{
		Actor:     username,
		Role:      acct.Role,
		CSRF:      csrf,
		CreatedAt: now,
	}
	return sid, csrf, true, ""
}

// Logout removes a session (parity: auth_logout; no error on unknown id).
func (a *Authenticator) Logout(sid string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	delete(a.sessions, sid)
}

// Session returns the session if valid. Sliding TTL: each valid check
// refreshes the timestamp (parity with auth_session). Expired/unknown
// ids are evicted.
func (a *Authenticator) Session(sid string) (*Session, bool) {
	if sid == "" {
		return nil, false
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	sess, ok := a.sessions[sid]
	if !ok {
		return nil, false
	}
	if a.now().Sub(sess.CreatedAt) >= SessionTTL {
		delete(a.sessions, sid)
		return nil, false
	}
	sess.CreatedAt = a.now()
	return sess, true
}

// CompareCSRF — constant-time comparison against the session's token
// (parity with auth_csrf_ok).
func (a *Authenticator) CompareCSRF(sid, token string) bool {
	a.mu.Lock()
	sess, ok := a.sessions[sid]
	a.mu.Unlock()
	if !ok {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(sess.CSRF), []byte(token)) == 1
}

// Prune drops expired sessions; called periodically from the server.
func (a *Authenticator) Prune() {
	a.mu.Lock()
	defer a.mu.Unlock()
	now := a.now()
	for sid, sess := range a.sessions {
		if now.Sub(sess.CreatedAt) >= SessionTTL {
			delete(a.sessions, sid)
		}
	}
}

func uuidHex() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		panic(fmt.Sprintf("crypto/rand unreadable: %v", err))
	}
	return hex.EncodeToString(b)
}
