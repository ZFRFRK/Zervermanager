// Package run wraps external command execution with a mandatory timeout.
//
// Go counterpart of the Python run() helper: non-zero exits are RESULTS,
// not errors; a stalled command cannot hang the caller (rc 124 semantics
// from Python Phase B are preserved via Result.TimedOut / Exit==124).
package run

import (
	"bytes"
	"context"
	"os/exec"
	"time"
)

// Result mirrors what call sites of the Python run() helper expect.
type Result struct {
	Stdout   string
	Stderr   string
	Exit     int  // process exit code; -1 when the command could not start
	TimedOut bool // set on context deadline; Exit is 124 in that case
}

// ErrTimedOutExit is the exit code reported for a timed-out command,
// matching the Python Phase B TimeoutExpired → rc-124 convention.
const ErrTimedOutExit = 124

// MaxBuffer caps captured stdout/stderr so a noisy child cannot exhaust
// memory. None of the manager's commands legitimately produce more.
const MaxBuffer = 32 << 20

// Run executes name with args under timeout. timeout must be > 0 —
// every subprocess in this codebase runs with a deadline by design.
func Run(timeout time.Duration, name string, args ...string) Result {
	if timeout <= 0 {
		timeout = 30 * time.Second
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, name, args...)
	var out, errb bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &errb

	err := cmd.Run()
	if ctx.Err() == context.DeadlineExceeded {
		return Result{
			Stdout:   clip(&out),
			Stderr:   appendNote(clip(&errb), "command timed out"),
			Exit:     ErrTimedOutExit,
			TimedOut: true,
		}
	}
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			return Result{Stdout: clip(&out), Stderr: clip(&errb), Exit: exitErr.ExitCode()}
		}
		// Command did not start (ENOENT, EACCES, ...).
		return Result{Exit: -1, Stderr: appendNote(clip(&errb), err.Error())}
	}
	return Result{Stdout: clip(&out), Stderr: clip(&errb), Exit: 0}
}

func clip(b *bytes.Buffer) string {
	if b.Len() > MaxBuffer {
		return b.String()[:MaxBuffer]
	}
	return b.String()
}

func appendNote(s, note string) string {
	if s == "" {
		return note
	}
	return s + "\n" + note
}
