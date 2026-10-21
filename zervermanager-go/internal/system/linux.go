//go:build linux

package system

import (
	"os"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"zervermanager/internal/run"
)

// UptimeInfo reads /proc/uptime (parity with get_system_uptime).
func UptimeInfo() Uptime {
	data, err := os.ReadFile("/proc/uptime")
	if err != nil {
		return Uptime{Formatted: "N/A", BootTime: "N/A"}
	}
	secsFloat, ok := parseFirstFloat(string(data))
	if !ok {
		return Uptime{Formatted: "N/A", BootTime: "N/A"}
	}
	total := int64(secsFloat)
	boot := time.Now().Add(-time.Duration(secsFloat * float64(time.Second)))
	return Uptime{
		Seconds:   total,
		Formatted: FormatUptime(total),
		BootTime:  boot.Format("2006-01-02 15:04"),
	}
}

// RAMInfo reads /proc/meminfo in kB (parity with get_ram_usage).
func RAMInfo() RAM {
	data, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return RAM{Formatted: "N/A"}
	}
	mem := map[string]int64{}
	for _, line := range strings.Split(string(data), "\n") {
		name, val, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		v, err := strconv.ParseInt(strings.TrimSpace(strings.Fields(strings.TrimSpace(val))[0]), 10, 64)
		if err != nil {
			continue
		}
		mem[strings.TrimSpace(name)] = v
	}
	totalKB := mem["MemTotal"]
	availKB := mem["MemAvailable"]
	if availKB == 0 {
		availKB = mem["MemFree"] + mem["Buffers"] + mem["Cached"]
	}
	usedKB := totalKB - availKB
	if usedKB < 0 {
		usedKB = 0
	}
	if totalKB <= 0 {
		return RAM{Formatted: "N/A", Percent: 0, UsedGB: 0, TotalGB: 0, FreeGB: 0}
	}
	percent := round1(float64(usedKB) / float64(totalKB) * 100)
	totalGB := round2(float64(totalKB) / (1024 * 1024))
	usedGB := round2(float64(usedKB) / (1024 * 1024))
	freeGB := round2(float64(availKB) / (1024 * 1024))
	return RAM{
		Percent:   percent,
		UsedGB:    usedGB,
		TotalGB:   totalGB,
		FreeGB:    freeGB,
		Formatted: fmtGB(usedGB) + " GB / " + fmtGB(totalGB) + " GB",
	}
}

// fmtGB renders a float like python f"{v}" of round(v, 2): trailing ".0"
// is kept in python; the normalizer covers the difference, so plain %v
// formatting is intentionally NOT attempted here beyond default.
func fmtGB(v float64) string {
	return strconv.FormatFloat(v, 'g', -1, 64)
}

// CPU sampling state — parity of _last_cpu_sample (kept without
// staleness limit, like the Python).
var (
	cpuMu        sync.Mutex
	cpuPrevIdle  float64
	cpuPrevTotal float64
	cpuHasSample bool
)

// CPUInfo reads /proc/loadavg + /proc/stat (parity with get_cpu_usage,
// including the 50 ms double-sample fallback on the first ever call).
func CPUInfo() CPU {
	cores := runtime.NumCPU()
	if cores == 0 {
		cores = 1
	}
	loadAvg := []float64{0, 0, 0}
	if data, err := os.ReadFile("/proc/loadavg"); err == nil {
		fields := strings.Fields(string(data))
		if len(fields) >= 3 {
			for i := 0; i < 3; i++ {
				v, err := strconv.ParseFloat(fields[i], 64)
				if err == nil {
					loadAvg[i] = round2(v)
				}
			}
		}
	}

	percent := 0.0
	data, err := os.ReadFile("/proc/stat")
	if err == nil {
		total, idle := parseStatCPU(data)
		if total >= 0 {
			cpuMu.Lock()
			defer cpuMu.Unlock()
			if cpuHasSample {
				idleDelta := idle - cpuPrevIdle
				totalDelta := total - cpuPrevTotal
				if totalDelta > 0 {
					percent = round1(100.0 * (1.0 - idleDelta/totalDelta))
					percent = clamp01(percent)
				}
			} else {
				// Python first-call path: sleep 50 ms and sample again.
				time.Sleep(50 * time.Millisecond)
				if data2, err2 := os.ReadFile("/proc/stat"); err2 == nil {
					total2, idle2 := parseStatCPU(data2)
					if total2 >= 0 {
						totalDelta := total2 - total
						idleDelta := idle2 - idle
						if totalDelta > 0 {
							percent = round1(100.0 * (1.0 - idleDelta/totalDelta))
							percent = clamp01(percent)
						}
						total, idle = total2, idle2
					}
				}
			}
			cpuPrevIdle, cpuPrevTotal, cpuHasSample = idle, total, true
		}
	}

	loadStr := loadFormatted(loadAvg)
	return CPU{Percent: percent, Cores: cores, LoadStr: loadStr}
}

func loadFormatted(l []float64) string {
	return strconv.FormatFloat(l[0], 'f', 2, 64) + ", " +
		strconv.FormatFloat(l[1], 'f', 2, 64) + ", " +
		strconv.FormatFloat(l[2], 'f', 2, 64)
}

func clamp01(p float64) float64 {
	if p < 0 {
		return 0
	}
	if p > 100 {
		return 100
	}
	return p
}

// parseStatCPU parses the "cpu " aggregate line. Parity with the Python:
// fields[1:8] are user/nice/system/idle/iowait/irq/softirq;
// idle = fields[3] + fields[4], total = sum of all seven.
func parseStatCPU(data []byte) (total, idle float64) {
	for _, line := range strings.Split(string(data), "\n") {
		if !strings.HasPrefix(line, "cpu ") {
			continue
		}
		fields := strings.Fields(line)[1:8]
		if len(fields) != 7 {
			return -1, -1
		}
		for _, f := range fields {
			v, err := strconv.ParseFloat(f, 64)
			if err != nil {
				return -1, -1
			}
			total += v
		}
		idleF, _ := strconv.ParseFloat(fields[3], 64)
		iowaitF, _ := strconv.ParseFloat(fields[4], 64)
		idle = idleF + iowaitF
		return
	}
	return -1, -1
}

// OSVersion — /etc/os-release PRETTY_NAME, quoted-stripped.
func OSVersion() string {
	data, err := os.ReadFile("/etc/os-release")
	if err != nil {
		return "Unknown OS"
	}
	for _, line := range strings.Split(string(data), "\n") {
		if strings.HasPrefix(line, "PRETTY_NAME=") {
			v := strings.TrimSpace(strings.TrimPrefix(line, "PRETTY_NAME="))
			v = strings.Trim(v, `"`)
			return v
		}
	}
	return "Unknown OS"
}

// queryServiceStates performs one batched `systemctl is-active` for all
// units with a hard 10 s timeout; short output is padded with
// "inactive" (python service_statuses parity).
func queryServiceStates(names []string) map[string]string {
	out := make(map[string]string, len(names))
	if len(names) == 0 {
		return out
	}
	res := run.Run(10*time.Second, "systemctl", append([]string{"is-active"}, names...)...)
	lines := strings.Split(res.Stdout, "\n")
	parsed := make([]string, 0, len(names))
	for _, l := range lines {
		if l = strings.TrimSpace(l); l != "" {
			parsed = append(parsed, l)
		}
	}
	for len(parsed) < len(names) {
		parsed = append(parsed, "inactive")
	}
	for i, n := range names {
		out[n] = parsed[i]
	}
	return out
}
