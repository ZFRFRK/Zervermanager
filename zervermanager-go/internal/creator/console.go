// Package creator ports the Python site-creation engine of
// zervermanager.py (`create_*` functions and their helpers) to Go for
// the web /api/makesite/* flows (goplan M2).
//
// Design: the Python version reuses its interactive CLI creators by
// driving their `_ask()` call sites with a keyed answer bridge while
// swapping sys.stdout for a streaming pipe into the HTTP response. Go
// has no stdout to swap, so the *same call graph* is reconstructed
// with an explicit bridge:
//
//	Answers        — the keyed prompt answers (web form values)
//	Stream         — the console writer piped to the response
//	flow functions — the create_* bodies, in the same order, emitting
//	                 the same narrative bytes
//
// Console narrative (colors, ✓/✗ glyphs, separators) is reproduced
// exactly — the frontend terminal colorizes the ANSI codes.
package creator

import (
	"fmt"
	"net/http"
	"regexp"
	"strings"
)

// ─────────────────────────────────────────
//  Console colors (python class C)
// ─────────────────────────────────────────

const (
	cRED    = "\x1b[91m"
	cGREEN  = "\x1b[92m"
	cYELLOW = "\x1b[93m"
	cBLUE   = "\x1b[94m"
	cCYAN   = "\x1b[96m"
	cBOLD   = "\x1b[1m"
	cDIM    = "\x1b[2m"
	cRESET  = "\x1b[0m"
)

// Stream is the console narrative sink — python's StreamingLogger.
type Stream struct {
	w http.ResponseWriter
}

// NewStream wraps the response the streamed narrative goes into.
func NewStream(w http.ResponseWriter) *Stream {
	return &Stream{w: w}
}

// WriteRaw writes bytes verbatim (ANSI codes preserved for the frontend).
func (s *Stream) WriteRaw(text string) {
	if text == "" {
		return
	}
	_, _ = s.w.Write([]byte(text))
	s.Flush()
}

// Flush pushes buffered bytes to the client.
func (s *Stream) Flush() {
	if f, ok := s.w.(http.Flusher); ok {
		f.Flush()
	}
}

// Exact prints of the python helpers ok/err/warn/info/step/bold.
func (s *Stream) ok(msg string)   { s.WriteRaw(cGREEN + "  ✓ " + msg + cRESET + "\n") }
func (s *Stream) err(msg string)  { s.WriteRaw(cRED + "  ✗ " + msg + cRESET + "\n") }
func (s *Stream) warn(msg string) { s.WriteRaw(cYELLOW + "  ! " + msg + cRESET + "\n") }
func (s *Stream) info(msg string) { s.WriteRaw(cBLUE + "  → " + msg + cRESET + "\n") }
func (s *Stream) step(msg string) { s.WriteRaw(cCYAN + "  • " + msg + cRESET + "\n") }
func (s *Stream) bold(msg string) { s.WriteRaw(cBOLD + msg + cRESET + "\n") }

// menuHeader — python menu_header(): bold title left, credit right,
// '━' separator sized to the layout.
func (s *Stream) menuHeader(title string) {
	clean := ansiRE.ReplaceAllString(title, "")
	creditLen := 14 // len("Made by ZFRFRK")
	width := 45
	if n := len(clean) + creditLen + 2; n > width {
		width = n
	}
	spacing := width - len(clean) - creditLen
	if spacing < 0 {
		spacing = 0
	}
	s.WriteRaw("\n" + cBOLD + title + strings.Repeat(" ", spacing) + cBLUE + "Made by ZFRFRK" + cRESET + "\n")
	s.WriteRaw(strings.Repeat("━", width) + "\n")
}

var ansiRE = regexp.MustCompile(`\x1b\[[0-9;]*m`)

// ─────────────────────────────────────────
//  Web→creator answer bridge (python _WEB_ANSWERS / _ask)
// ─────────────────────────────────────────

// ErrEOF stands in for the Python EOFError raised when a prompt runs
// with no bridged answer remaining and no real stdin.
var ErrEOF = fmt.Errorf("EOF when reading a line")

// Answers is the keyed prompt-response bridge — python's
// _WEB_ANSWERS dict, consumed BY KEY at every prompt point.
type Answers map[string]string

// Ask — python _ask(prompt, key): consumes the prefixed answer BY KEY
// from the bridge. When the key is absent, the Python interactive
// fallback is input() — with no TTY in web mode that raises
// EOFError, translated here to ErrEOF.
func (a Answers) Ask(s *Stream, prompt, key string) (string, error) {
	if a == nil {
		return "", ErrEOF
	}
	val, ok := a[key]
	if !ok {
		return "", ErrEOF
	}
	delete(a, key)
	// Narrate the prompt+answer so streamed web output stays readable.
	s.WriteRaw(prompt)
	if val != "" {
		s.WriteRaw(val + "\n")
	}
	return val, nil
}

// InputPause — python input_pause(): no-op read when bridged.
func (a Answers) InputPause(s *Stream, prompt string) {
	s.WriteRaw(prompt + "\n")
}

// PromptConfirm — python prompt_confirm(msg, default="no", key=key).
func (a Answers) PromptConfirm(s *Stream, msg, key string, def bool) (bool, error) {
	prompt := "  " + msg + " (y/N): "
	val, err := a.Ask(s, prompt, key)
	if err != nil {
		return false, err
	}
	val = strings.ToLower(strings.TrimSpace(val))
	if val == "" {
		return def, nil
	}
	return val == "y" || val == "yes", nil
}

// ─────────────────────────────────────────
//  Askers (validation parity with python)
// ─────────────────────────────────────────

var domainRE = regexp.MustCompile(`^[a-zA-Z0-9.-]+$`)
var dbIdentRE = regexp.MustCompile(`^[a-zA-Z0-9_]+$`)
var ipv4RE = regexp.MustCompile(`^\d{1,3}(\.\d{1,3}){3}$`)

// askDomain — python ask_domain().
func askDomain(s *Stream, a Answers) (string, error) {
	val, err := a.Ask(s, "  Domain: ", "domain")
	if err != nil {
		return "", err
	}
	val = strings.TrimSpace(val)
	// Web bridge path: single validation, no re-prompt.
	if !domainRE.MatchString(val) {
		if val != "" {
			s.err("Invalid domain. Only letters, numbers, dots, and hyphens are allowed.")
		}
		return "", nil
	}
	return val, nil
}

// bridgeValid — python _bridge_valid(): refuse re-prompting on a
// bridged-but-invalid value.
func bridgeValid(s *Stream, val, what string, re *regexp.Regexp) string {
	val = strings.TrimSpace(val)
	if val == "" {
		return ""
	}
	if !re.MatchString(val) {
		s.err(fmt.Sprintf("Invalid %s. Using empty value.", what))
		return ""
	}
	return val
}

// askDBName — python ask_db_name (bridge mode only: this package only
// drives the web flows).
func askDBName(s *Stream, a Answers, def string) (string, error) {
	prompt := "  Database name: "
	if def != "" {
		prompt = "  Database name [" + def + "]: "
	}
	val, err := a.Ask(s, prompt, "db_name")
	if err != nil {
		return "", err
	}
	val = bridgeValid(s, val, "database name", dbIdentRE)
	if val == "" {
		return def, nil
	}
	return val, nil
}

// askDBUser — python ask_db_user (bridge mode).
func askDBUser(s *Stream, a Answers, def string) (string, error) {
	prompt := "  Database user: "
	if def != "" {
		prompt = "  Database user [" + def + "]: "
	}
	val, err := a.Ask(s, prompt, "db_user")
	if err != nil {
		return "", err
	}
	val = bridgeValid(s, val, "database user", dbIdentRE)
	if val == "" {
		return def, nil
	}
	return val, nil
}

// validateDocroot — python validate_docroot(): absolute-path sanity,
// protected-prefix blacklist, minimum depth.
func validateDocroot(path string) (bool, string) {
	if path == "" {
		return false, "Document root cannot be empty."
	}
	abs := filepathAbs(path)
	protected := map[string]bool{
		"/": true, "/etc": true, "/var": true, "/usr": true, "/bin": true,
		"/sbin": true, "/lib": true, "/boot": true, "/proc": true,
		"/sys": true, "/dev": true, "/home": true, "/root": true,
		"/run": true, "/tmp": true, "/srv": true, "/var/www": true,
	}
	if protected[abs] {
		return false, "Cannot use protected system directory: " + abs
	}
	parts := splitParts(abs)
	if len(parts) < 2 {
		return false, "Path too shallow: " + abs
	}
	return true, ""
}

// askDocroot — python ask_docroot (bridge mode).
func askDocroot(s *Stream, a Answers, prompt, def string) (string, error) {
	clean := strings.TrimSpace(prompt)
	p := "  " + clean + ": "
	if def != "" {
		p = "  " + clean + " [" + def + "]: "
	}
	val, err := a.Ask(s, p, "docroot")
	if err != nil {
		return "", err
	}
	val = strings.TrimSpace(val)
	if val == "" {
		val = def
	}
	valid, msg := validateDocroot(val)
	if valid {
		return val, nil
	}
	s.WriteRaw("  " + cRED + "✗ " + msg + cRESET + "\n")
	return "", nil
}
