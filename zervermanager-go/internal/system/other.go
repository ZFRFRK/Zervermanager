//go:build !linux

package system

// Non-Linux fallback. Values mirror the Python fallback paths
// ("N/A" / zeros) so the binary builds anywhere; real data comes from
// the linux.go implementation when deployed. Dev-box builds are for
// compilation and unit tests only — same discipline as the Python.

import "runtime"

func UptimeInfo() Uptime { return Uptime{Formatted: "N/A", BootTime: "N/A"} }

func RAMInfo() RAM { return RAM{Formatted: "N/A"} }

func CPUInfo() CPU {
	cores := runtimeNumCPU()
	return CPU{Percent: 0.0, Cores: cores, LoadStr: "0.00, 0.00, 0.00"}
}

func OSVersion() string { return "Unknown OS" }

func queryServiceStates(names []string) map[string]string {
	out := make(map[string]string, len(names))
	for _, n := range names {
		out[n] = "inactive"
	}
	return out
}

func runtimeNumCPU() int { return runtime.NumCPU() }
