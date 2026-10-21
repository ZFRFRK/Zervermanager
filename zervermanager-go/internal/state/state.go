// Package state is the Go port of the incident log and site metadata
// store (Phase 3 of the Python: ZM_INCIDENTS_LOG + META_DIR).
package state

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
)

// Incident mirrors the record _raise_incident() appends to
// incidents.jsonl (field names are the JSON contract).
type Incident struct {
	ID              string `json:"id"`
	Severity        string `json:"severity"`
	Source          string `json:"source"`
	FirstSeen       string `json:"first_seen"`
	LastSeen        string `json:"last_seen"`
	Count           int    `json:"count"`
	Evidence        string `json:"evidence"`
	SuggestedAction string `json:"suggested_action"`
	Tx              string `json:"tx"`
	Status          string `json:"status"`
}

// LoadIncidents replays incidents.jsonl: last record per id wins, and
// the result keeps first-occurrence order (parity with the python
// in-memory dict). Lines that fail to parse are skipped, like the
// python tolerant readers.
func LoadIncidents(path string) []Incident {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	order := []string{}
	byID := map[string]Incident{}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var inc Incident
		if json.Unmarshal([]byte(line), &inc) != nil || inc.ID == "" {
			continue
		}
		if _, seen := byID[inc.ID]; !seen {
			order = append(order, inc.ID)
		}
		byID[inc.ID] = inc
	}
	out := make([]Incident, 0, len(order))
	for _, id := range order {
		out = append(out, byID[id])
	}
	return out
}

// WithStatus filters, parity with incidents_list(status).
func WithStatus(all []Incident, status string) []Incident {
	out := []Incident{}
	for _, inc := range all {
		if inc.Status == status {
			out = append(out, inc)
		}
	}
	return out
}

// HighSeverity counts open incidents with severity high or critical —
// the /api/status "high_severity" definition.
func HighSeverity(open []Incident) int {
	n := 0
	for _, inc := range open {
		if inc.Severity == "high" || inc.Severity == "critical" {
			n++
		}
	}
	return n
}

// SiteMeta reads META_DIR/<domain>.json (parity with get_site_meta).
// Returns nil when missing or unparsable — callers apply defaults.
func SiteMeta(metaDir, domain string) map[string]any {
	data, err := os.ReadFile(filepath.Join(metaDir, domain+".json"))
	if err != nil {
		return nil
	}
	var meta map[string]any
	if json.Unmarshal(data, &meta) != nil {
		return nil
	}
	return meta
}
