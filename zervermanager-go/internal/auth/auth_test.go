package auth

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// writeAuthFile writes a Python-compatible auth.json and returns its path.
func writeAuthFile(t *testing.T, password string) string {
	t.Helper()
	cfg := `{"admin": {"hash": "` + HashPassword(password) + `", "role": "admin"}, "session_secret": "0123456789abcdef"}`
	path := filepath.Join(t.TempDir(), "auth.json")
	if err := os.WriteFile(path, []byte(cfg), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestHashPasswordFormatParity(t *testing.T) {
	h := HashPassword("s3cret")
	// salt.hex(32 chars) + ":" + dk.hex(128 chars)
	if len(h) != 32+1+128 {
		t.Fatalf("stored length = %d, want 161", len(h))
	}
	parts := strings.SplitN(h, ":", 2)
	if len(parts) != 2 || len(parts[0]) != 32 || len(parts[1]) != 128 {
		t.Fatalf("not salt.hex:dk.hex — %q", h[:20]+"...")
	}
	if !VerifyPassword("s3cret", h) {
		t.Fatal("verify roundtrip failed")
	}
	if VerifyPassword("wrong", h) {
		t.Fatal("wrong password verified")
	}
}

func TestVerifyMalformed(t *testing.T) {
	for _, bad := range []string{"", "zz", "aa:", ":bb", "aa:bb:cc"} {
		if VerifyPassword("x", bad) {
			t.Fatalf("malformed %q verified", bad)
		}
	}
}

func TestLoginFlow(t *testing.T) {
	a := New(writeAuthFile(t, "pw123"))

	sid, csrf, ok, errText := a.Login("admin", "pw123", "10.0.0.1")
	if !ok || len(sid) != 32 || len(csrf) != 32 {
		t.Fatalf("login failed: ok=%v err=%q sid=%q", ok, errText, sid)
	}
	sess, good := a.Session(sid)
	if !good || sess.Actor != "admin" {
		t.Fatalf("session lookup failed: %+v", sess)
	}
	if sess.Role == "" {
		t.Fatal("role must be carried through from auth.json")
	}
	if !a.CompareCSRF(sid, csrf) {
		t.Fatal("csrf did not match")
	}
	if a.CompareCSRF(sid, "0f") {
		t.Fatal("bogus csrf matched")
	}

	if _, ok2, err2 := loginErr(a, "admin", "nope", "10.0.0.1"); ok2 || err2 != ErrInvalidCreds {
		t.Fatalf("wrong password: ok=%v err=%q (want %q)", ok2, err2, ErrInvalidCreds)
	}
	a.Logout(sid)
	if _, good := a.Session(sid); good {
		t.Fatal("session survived logout")
	}
}

func loginErr(a *Authenticator, user, pw, ip string) (string, bool, string) {
	sid, _, ok, errText := a.Login(user, pw, ip)
	return sid, ok, errText
}

func TestLoginRateLimitParity(t *testing.T) {
	a := New(writeAuthFile(t, "pw123"))
	ip := "203.0.113.9"
	for i := 0; i < LoginRate; i++ {
		if _, ok, err := loginErr(a, "admin", "bad", ip); ok || err != ErrInvalidCreds {
			t.Fatalf("attempt %d: ok=%v err=%q", i, ok, err)
		}
	}
	_, ok, err := loginErr(a, "admin", "pw123", ip)
	if ok || err != ErrRateLimited {
		t.Fatalf("post-limit login: ok=%v err=%q (want %q)", ok, err, ErrRateLimited)
	}
	// Other IPs are unaffected.
	if _, ok, _ := loginErr(a, "admin", "pw123", "198.51.100.1"); !ok {
		t.Fatal("other IP got rate limited")
	}
}

func TestRateLimitWindowReset(t *testing.T) {
	a := New(writeAuthFile(t, "pw123"))
	ip := "203.0.113.7"
	now := time.Unix(1_700_000_000, 0)
	a.now = func() time.Time { return now }
	for i := 0; i < LoginRate; i++ {
		loginErr(a, "admin", "bad", ip)
	}
	if _, ok, _ := loginErr(a, "admin", "bad", ip); ok {
		t.Fatal("limit did not engage")
	}
	now = now.Add(LoginWindow + time.Second)
	if _, ok, err := loginErr(a, "admin", "pw123", ip); !ok || err != "" {
		t.Fatalf("window reset did not re-enable: ok=%v err=%q", ok, err)
	}
}

func TestSessionExpiry(t *testing.T) {
	a := New(writeAuthFile(t, "pw123"))
	now := time.Unix(1_700_000_000, 0)
	a.now = func() time.Time { return now }
	sid, _, ok, _ := a.Login("admin", "pw123", "10.1.1.1")
	if !ok {
		t.Fatal("setup login failed")
	}
	now = now.Add(SessionTTL - time.Minute)
	if _, good := a.Session(sid); !good {
		t.Fatal("session expired early")
	}
	now = now.Add(SessionTTL + time.Minute)
	if _, good := a.Session(sid); good {
		t.Fatal("stale session survived TTL")
	}
}

func TestSessionTTLIsSevenDays(t *testing.T) {
	if SessionTTL != 7*24*time.Hour {
		t.Fatalf("SessionTTL = %v (parity target: 7 days)", SessionTTL)
	}
}
