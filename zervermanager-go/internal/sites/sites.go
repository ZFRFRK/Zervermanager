// Package sites lists registered vhosts from the apache/nginx
// sites-available directories plus per-site metadata files.
// Field names and defaults are parity with the /api/sites handler in
// zervermanager.py.
package sites

import (
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"zervermanager/internal/state"
)

// Dirs describes the filesystem layout the Python constants define.
type Dirs struct {
	ApacheAvailable string
	ApacheEnabled   string
	NginxAvailable  string
	NginxEnabled    string
	MetaDir         string
}

// API shape: container_name / host_port render as JSON null when absent
// (python meta.get → None), so they are pointers without omitempty.
type Site struct {
	Domain        string  `json:"domain"`
	Server        string  `json:"server"`
	Type          string  `json:"type"`
	Docroot       string  `json:"docroot"`
	ContainerName *string `json:"container_name"`
	HostPort      *string `json:"host_port"`
	Enabled       bool    `json:"enabled"`
}

// List mirrors the /api/sites handler: sorted .conf entries of
// sites-available for apache then nginx, enabled via sites-enabled.
func List(dirs Dirs) []Site {
	var out []Site
	out = appendServers(out, "apache", dirs.ApacheAvailable, dirs.ApacheEnabled, dirs.MetaDir)
	out = appendServers(out, "nginx", dirs.NginxAvailable, dirs.NginxEnabled, dirs.MetaDir)
	return out
}

func appendServers(out []Site, server, availableDir, enabledDir, metaDir string) []Site {
	entries, err := os.ReadDir(availableDir)
	if err != nil {
		return out // python: os.path.exists gate → silently empty
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		if !e.IsDir() && strings.HasSuffix(e.Name(), ".conf") {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)

	for _, file := range names {
		domain := strings.TrimSuffix(file, ".conf")
		meta := state.SiteMeta(metaDir, domain)

		site := Site{Domain: domain, Server: server}
		// meta.get("type", "unknown") etc. — defaults for absent keys.
		site.Type = metaStr(meta, "type", "unknown")
		site.Docroot = metaStr(meta, "docroot", "/var/www/"+domain)
		if mp := metaAny(meta, "container_name"); mp != nil {
			if s, ok := mp.(string); ok { // string expected; null-safe
				v := s
				site.ContainerName = &v
			}
		}
		if mp := metaAny(meta, "host_port"); mp != nil {
			switch v := mp.(type) {
			case string:
				site.HostPort = &v
			case float64: // JSON numbers surface as float64
				s := trimFloat(v)
				site.HostPort = &s
			}
		}
		site.Enabled = fileExists(filepath.Join(enabledDir, file))
		out = append(out, site)
	}
	return out
}

func metaStr(meta map[string]any, key, def string) string {
	if meta == nil {
		return def
	}
	if v, ok := meta[key].(string); ok {
		return v
	}
	return def
}

func metaAny(meta map[string]any, key string) any {
	if meta == nil {
		return nil
	}
	return meta[key]
}

func trimFloat(v float64) string {
	if v == float64(int64(v)) {
		return strconv.FormatInt(int64(v), 10)
	}
	return strconv.FormatFloat(v, 'g', -1, 64)
}

func fileExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}
