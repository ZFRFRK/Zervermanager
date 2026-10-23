// Flow — the per-request creator context: the streamed narrative sink
// plus the keyed web answers, threading through every flow function.
package creator

import (
	"strings"
	"time"
)

// Flow bundles the python globals the creators implicitly share:
// stdout (→ stream), the web answer bridge (→ answers), and the
// reload tracker (_pending set).
type Flow struct {
	stream  *Stream
	answers Answers
	tracker *ReloadTracker
}

// NewFlow builds a request-scoped Flow.
func NewFlow(s *Stream, a Answers) *Flow {
	return &Flow{stream: s, answers: a}
}

// ReloadTracker — python _pending set from mark_reload().
type ReloadTracker struct {
	pending map[string]bool
}

// markReload — python mark_reload(service).
func (f *Flow) markReload(service string) {
	if f.tracker == nil {
		f.tracker = &ReloadTracker{pending: map[string]bool{}}
	}
	f.tracker.pending[service] = true
}

// applyReloads — python apply_reloads(): fixed order apache2 → bind9
// → nginx; reload when active, restart otherwise; ✓/✗ inline verdict.
func (f *Flow) applyReloads() {
	if f.tracker == nil || len(f.tracker.pending) == 0 {
		return
	}

	s := f.stream
	s.WriteRaw("\n")

	if f.tracker.pending["apache2"] {
		s.WriteRaw("  " + cCYAN + "Reloading Apache2..." + cRESET)
		active := f.isActive("apache2")
		action := "reload"
		if !active {
			action = "restart"
		}
		res := f.run(60*time.Second, "systemctl", action, "apache2")
		if res.Exit == 0 {
			s.WriteRaw(cGREEN + "✓" + cRESET + "\n")
		} else {
			s.WriteRaw(cRED + "✗" + cRESET + "\n")
			s.err(strings.TrimSpace(res.Stderr))
		}
	}

	if f.tracker.pending["bind9"] {
		s.WriteRaw("  " + cCYAN + "Reloading BIND9..." + cRESET)
		svc := f.getBind9ServiceName()
		active := f.isActive(svc)
		action := "reload"
		if !active {
			action = "restart"
		}
		res := f.run(60*time.Second, "systemctl", action, svc)
		if res.Exit == 0 {
			s.WriteRaw(cGREEN + "✓" + cRESET + "\n")
		} else {
			s.WriteRaw(cRED + "✗" + cRESET + "\n")
			s.err(strings.TrimSpace(res.Stderr))
		}
	}

	if f.tracker.pending["nginx"] {
		s.WriteRaw("  " + cCYAN + "Reloading Nginx..." + cRESET)
		active := f.isActive("nginx")
		action := "reload"
		if !active {
			action = "restart"
		}
		res := f.run(60*time.Second, "systemctl", action, "nginx")
		if res.Exit == 0 {
			s.WriteRaw(cGREEN + "✓" + cRESET + "\n")
		} else {
			s.WriteRaw(cRED + "✗" + cRESET + "\n")
			s.err(strings.TrimSpace(res.Stderr))
		}
	}
}

// isActive — python's apply_reloads uses a direct (uncached)
// `systemctl is-active` so the reload verdict is fresh.
func (f *Flow) isActive(unit string) bool {
	res := f.run(30*time.Second, "systemctl", "is-active", unit)
	return strings.TrimSpace(res.Stdout) == "active"
}
