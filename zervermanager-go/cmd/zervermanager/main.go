// zervermanager — Go rewrite entry point (goplan.md M1 walking
// skeleton): serves web_config/ unchanged and the core API surface
// (auth/session/status/sites) with the Phase 0 security stack.
package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"time"

	"zervermanager/internal/auth"
	"zervermanager/internal/sites"
	"zervermanager/internal/web"
)

var versionString = "0.1.0-m1 (api " + web.APIVersion + ", python-parity " + web.BackendVersion + ")"

func main() {
	var (
		addr          = flag.String("addr", ":8090", "listen address")
		webDir        = flag.String("web-dir", "", "directory with the web UI assets (web_config/)")
		authFile      = flag.String("auth-file", "/etc/zervermanager/auth.json", "auth.json path")
		incidentsFile = flag.String("incidents-file", "/var/lib/zervermanager/incidents.jsonl", "incidents.jsonl path")
		metaDir       = flag.String("meta-dir", "/etc/servermanager/sites", "site metadata dir")
		apacheAvail   = flag.String("apache-sites", "/etc/apache2/sites-available", "apache sites-available")
		apacheEnab    = flag.String("apache-enabled", "/etc/apache2/sites-enabled", "apache sites-enabled")
		nginxAvail    = flag.String("nginx-sites", "/etc/nginx/sites-available", "nginx sites-available")
		nginxEnab     = flag.String("nginx-enabled", "/etc/nginx/sites-enabled", "nginx sites-enabled")
		statusTTL     = flag.Duration("status-ttl", 3*time.Second, "systemctl status batch cache TTL")
		shadow        = flag.Bool("shadow", false, "shadow mode: read-only API parity prober on a second port")
		initPassword  = flag.String("init-password", "", "one-shot: write auth.json with this admin password and exit (re-running resets the password)")
		showVersion   = flag.Bool("version", false, "print version and exit")
	)
	flag.Parse()

	if *showVersion {
		fmt.Println("zervermanager-go", versionString)
		return
	}

	// auth_init / auth_reset parity: {"admin": {hash, role}, "session_secret": 32B hex},
	// file chmod 600, parent dir 700. The file is Python-compatible in both
	// directions (rollback keeps working after the Go version ran).
	if *initPassword != "" {
		if err := os.MkdirAll(filepath.Dir(*authFile), 0o700); err != nil {
			log.Fatalf("cannot create %s: %v", filepath.Dir(*authFile), err)
		}
		cfg := map[string]any{
			"admin":          map[string]string{"hash": auth.HashPassword(*initPassword), "role": "admin"},
			"session_secret": randomHex(32),
		}
		data, err := json.MarshalIndent(cfg, "", "  ")
		if err != nil {
			log.Fatalf("auth config encode: %v", err)
		}
		if err := os.WriteFile(*authFile, data, 0o600); err != nil {
			log.Fatalf("cannot write %s: %v", *authFile, err)
		}
		log.Printf("auth file written: %s (admin/admin password set)", *authFile)
		return
	}

	if runtime.GOOS == "linux" && os.Geteuid() != 0 {
		log.Println("[warn] not running as root — privileged operations will fail")
	}

	if *webDir == "" {
		// Parity: python serves web_config/ relative to the script dir.
		*webDir = defaultWebDir()
	}
	if _, err := os.Stat(*webDir); err != nil {
		log.Fatalf("web config dir not found (%s): %v", *webDir, err)
	}

	a := auth.New(*authFile)

	srv := web.New(web.Config{
		Addr:          *addr,
		WebDir:        *webDir,
		AuthFile:      *authFile,
		IncidentsFile: *incidentsFile,
		Sites: sites.Dirs{
			ApacheAvailable: *apacheAvail,
			ApacheEnabled:   *apacheEnab,
			NginxAvailable:  *nginxAvail,
			NginxEnabled:    *nginxEnab,
			MetaDir:         *metaDir,
		},
		StatusTTL: *statusTTL,
		Shadow:    *shadow,
	}, a)

	// Session pruning — the python relies on lazy eviction; a periodic
	// sweep keeps the map bounded without changing observable behavior.
	go func() {
		for range time.Tick(time.Minute) {
			a.Prune()
		}
	}()

	httpServer := &http.Server{
		Addr:              *addr,
		Handler:           srv.Handler(),
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       2 * time.Minute,
		MaxHeaderBytes:    1 << 20,
		// ReadTimeout/WriteTimeout unset: site-create/install flows
		// stream for minutes (M2+); per-route deadlines come with the
		// streaming infrastructure instead of a global cap.
	}

	mode := "serve"
	if *shadow {
		mode = "shadow"
	}
	log.Printf("[zervermanager] %s listening on %s (web: %s, %s)", mode, *addr, *webDir, versionString)
	if err := httpServer.ListenAndServe(); err != http.ErrServerClosed {
		log.Fatalf("http server: %v", err)
	}
}

func randomHex(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		log.Fatalf("crypto/rand unreadable: %v", err)
	}
	return hex.EncodeToString(b)
}

// defaultWebDir mirrors python: <dir of the executable>/web_config,
// falling back to the working directory when the exe path is temporary
// (go run).
func defaultWebDir() string {
	exe, err := os.Executable()
	if err == nil {
		if wd := filepath.Dir(exe); wd != "" {
			if p := filepath.Join(wd, "web_config"); dirExists(p) {
				return p
			}
		}
	}
	if p, err := os.Getwd(); err == nil {
		if d := filepath.Join(p, "web_config"); dirExists(d) {
			return d
		}
		return p // serve CWD itself; caller validated existence upstream
	}
	return "web_config"
}

func dirExists(p string) bool {
	info, err := os.Stat(p)
	return err == nil && info.IsDir()
}
