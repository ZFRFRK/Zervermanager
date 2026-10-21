// Package system collects the machine facts behind /api/status, ported
// from zervermanager.py (get_system_uptime / get_ram_usage /
// get_cpu_usage / get_os_version / service_statuses).
//
// Number formatting follows the Python semantics: percent rounded to 1
// decimal, GB values to 2. JSON float *rendering* differences (e.g.
// "4.0" vs "4") are covered by the §7 normalizer, not here.
package system

import (
	"fmt"
	"math"
	"os/exec"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Uptime mirrors get_system_uptime().
type Uptime struct {
	Seconds   int64  // total_secs (floored int, python parity)
	Formatted string // "2d 3h 15m" style
	BootTime  string // "2006-01-02 15:04"
}

// RAM mirrors get_ram_usage().
type RAM struct {
	Percent   float64
	UsedGB    float64
	TotalGB   float64
	FreeGB    float64
	Formatted string // "3.25 GB / 8.0 GB"
}

// CPU mirrors get_cpu_usage().
type CPU struct {
	Percent float64
	Cores   int
	LoadStr string // "0.12, 0.34, 0.56"
}

func round1(x float64) float64 { return math.Round(x*10) / 10 }
func round2(x float64) float64 { return math.Round(x*100) / 100 }

// FormatUptime builds the python f-string chain:
//   - "{days}d"  if days > 0
//   - "{hours}h" if hours > 0 or days > 0
//   - "{minutes}m" always
//   - fallback "{secs}s" (unreachable in practice, kept for parity)
func FormatUptime(totalSecs int64) string {
	days := totalSecs / 86400
	hours := (totalSecs % 86400) / 3600
	minutes := (totalSecs % 3600) / 60
	secs := totalSecs % 60

	var parts []string
	if days > 0 {
		parts = append(parts, fmt.Sprintf("%dd", days))
	}
	if hours > 0 || days > 0 {
		parts = append(parts, fmt.Sprintf("%dh", hours))
	}
	parts = append(parts, fmt.Sprintf("%dm", minutes))
	if len(parts) == 0 {
		return fmt.Sprintf("%ds", secs)
	}
	return strings.Join(parts, " ")
}

// IsDockerInstalled — parity with shutil.which("docker").
func IsDockerInstalled() bool {
	_, err := exec.LookPath("docker")
	return err == nil
}

// svcCache is the batched, TTL-cached systemctl state store (Phase B
// semantics: one spawn for N units, hard timeout inside the impl).
var svcCache struct {
	sync.Mutex
	at  time.Time
	ttl time.Duration
	res map[string]string
}

// ServiceStatuses returns raw systemd tokens (active/inactive/failed...)
// for the named units, using a cached batch if younger than ttl.
// Linux implementation is in linux.go; the generic version here is the
// fallback used while the semantics are developed on a non-Linux host.
func ServiceStatuses(names []string, ttl time.Duration) map[string]string {
	now := time.Now()

	svcCache.Lock()
	defer svcCache.Unlock()
	if svcCache.res != nil && now.Sub(svcCache.at) < ttl {
		return submap(svcCache.res, names)
	}
	res := queryServiceStates(names)
	svcCache.at = now
	svcCache.ttl = ttl
	svcCache.res = res
	return submap(res, names)
}

func submap(src map[string]string, names []string) map[string]string {
	out := make(map[string]string, len(names))
	for _, n := range names {
		if v, ok := src[n]; ok {
			out[n] = v
		} else {
			out[n] = "inactive" // python cache-miss default
		}
	}
	return out
}

// ServiceStatusPlain — python service_status(name, color=False):
// "running" when systemd says "active", "stopped" otherwise.
func ServiceStatusPlain(name string) string {
	raw := ServiceStatuses([]string{name}, 3*time.Second)[name]
	if raw == "active" {
		return "running"
	}
	return "stopped"
}

// SortFileNames orders directory entries exactly like sorted(os.listdir).
func SortFileNames(v []string) {
	sort.Strings(v)
}

func parseFirstFloat(s string) (float64, bool) {
	f := strings.Fields(s)
	if len(f) == 0 {
		return 0, false
	}
	v, err := strconv.ParseFloat(f[0], 64)
	if err != nil {
		return 0, false
	}
	return v, true
}
