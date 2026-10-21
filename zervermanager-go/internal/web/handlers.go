package web

// Data handlers: /api/health, /api/status, /api/sites — field order and
// semantics mirror handle_api_get() in zervermanager.py (the Phase B
// fixed semantics: ONE batched cached systemctl query for all units).

import (
	"encoding/json"
	"net/http"

	"zervermanager/internal/sites"
	"zervermanager/internal/state"
	"zervermanager/internal/system"
)

func writeJSON(w http.ResponseWriter, code int, body []byte) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_, _ = w.Write(body)
}

func mustJSON(v any) []byte {
	b, err := json.Marshal(v)
	if err != nil {
		panic("json.Marshal failed: " + err.Error())
	}
	return b
}

// openIncidents returns the in-memory open-incident store. Parity with
// python Phase 3: _INCIDENTS starts empty each boot (the jsonl log is
// written but never replayed) — so health/status counts only reflect
// what THIS boot raised. Historic-replay semantics land in M2/M6 with
// the incident persistence redesign; for M1 shadow parity this is the
// honest, matching behavior.
func (s *Server) openIncidents() []state.Incident {
	return []state.Incident{}
}

// handleHealth — parity with the /api/health branch of
// web_handle_new_api.
func (s *Server) handleHealth(w http.ResponseWriter) {
	open := state.WithStatus(s.openIncidents(), "open")
	writeJSON(w, http.StatusOK, mustJSON(healthResp{
		OK:            len(open) == 0,
		OpenIncidents: len(open),
		HighSeverity:  state.HighSeverity(open),
		SafeMode:      s.SafeMode(),
		APIVersion:    APIVersion,
		BackendVer:    BackendVersion,
	}))
}

type healthResp struct {
	OK            bool   `json:"ok"`
	OpenIncidents int    `json:"open_incidents"`
	HighSeverity  int    `json:"high_severity"`
	SafeMode      bool   `json:"safe_mode"`
	APIVersion    string `json:"api_version"`
	BackendVer    string `json:"backend_version"`
}

// statusResp field order is the python dict insertion order.
type statusResp struct {
	OS              string  `json:"os"`
	Apache2         string  `json:"apache2"`
	Nginx           string  `json:"nginx"`
	Bind9           string  `json:"bind9"`
	MariaDB         string  `json:"mariadb"`
	Postfix         string  `json:"postfix"`
	Dovecot         string  `json:"dovecot"`
	UFW             string  `json:"ufw"`
	DockerInstalled bool    `json:"docker_installed"`
	Version         string  `json:"version"`
	APIVersion      string  `json:"api_version"`
	SafeMode        bool    `json:"safe_mode"`
	OpenIncidents   int     `json:"open_incidents"`
	HighSeverity    int     `json:"high_severity"`
	Uptime          string  `json:"uptime"`
	UptimeSeconds   int64   `json:"uptime_seconds"`
	BootTime        string  `json:"boot_time"`
	CPU             float64 `json:"cpu"`
	CPUCores        int     `json:"cpu_cores"`
	CPULoad         string  `json:"cpu_load"`
	RAM             float64 `json:"ram"`
	RamUsedGB       float64 `json:"ram_used_gb"`
	RamTotalGB      float64 `json:"ram_total_gb"`
	RamFormatted    string  `json:"ram_formatted"`
}

var statusServices = []string{"apache2", "nginx", "bind9", "mariadb", "postfix", "dovecot", "ufw"}

// handleStatus — one batched, TTL-cached systemctl query for ALL units
// (the correct Phase B semantics; see the matching python fix: per-unit
// service_status calls inside a warm cache would report cache-miss
// "inactive" for 6 of the 7 services).
func (s *Server) handleStatus(w http.ResponseWriter) {
	raw := system.ServiceStatuses(statusServices, s.Cfg.StatusTTL)
	token := func(name string) string {
		if raw[name] == "active" {
			return "running"
		}
		return "stopped"
	}

	up := system.UptimeInfo()
	ram := system.RAMInfo()
	cpu := system.CPUInfo()
	open := state.WithStatus(s.openIncidents(), "open")

	writeJSON(w, http.StatusOK, mustJSON(statusResp{
		OS:              system.OSVersion(),
		Apache2:         token("apache2"),
		Nginx:           token("nginx"),
		Bind9:           token("bind9"),
		MariaDB:         token("mariadb"),
		Postfix:         token("postfix"),
		Dovecot:         token("dovecot"),
		UFW:             token("ufw"),
		DockerInstalled: system.IsDockerInstalled(),
		Version:         BackendVersion,
		APIVersion:      APIVersion,
		SafeMode:        s.SafeMode(),
		OpenIncidents:   len(open),
		HighSeverity:    state.HighSeverity(open),
		Uptime:          up.Formatted,
		UptimeSeconds:   up.Seconds,
		BootTime:        up.BootTime,
		CPU:             cpu.Percent,
		CPUCores:        cpu.Cores,
		CPULoad:         cpu.LoadStr,
		RAM:             ram.Percent,
		RamUsedGB:       ram.UsedGB,
		RamTotalGB:      ram.TotalGB,
		RamFormatted:    ram.Formatted,
	}))
}

// handleSites returns the /api/sites list (apache then nginx, sorted).
func (s *Server) handleSites() []sites.Site {
	list := sites.List(s.Cfg.Sites)
	if list == nil {
		return []sites.Site{}
	}
	return list
}
