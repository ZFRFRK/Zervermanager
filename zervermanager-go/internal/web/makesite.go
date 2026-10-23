// POST /api/makesite/{lamp|lemp|wordpress|static|proxy|docker} and the
// /api/makesite_docker alias — the streamed Make-Site flows, mirroring
// handle_makesite_post / _setup_stream / _teardown_stream of
// zervermanager.py.
package web

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"

	"zervermanager/internal/creator"
)

// handleMakesiteRoute matches the python do_POST routing: any
// /api/makesite/... plus the bare alias (which becomes "docker").
func handleMakesiteRoute(path string) bool {
	return strings.HasPrefix(path, "/api/makesite/") || path == "/api/makesite_docker"
}

// makesiteType — python site_type = path.split("/")[-1]; the alias
// route rewrites itself to /api/makesite/docker before this point.
func makesiteType(path string) string {
	if path == "/api/makesite_docker" {
		return "docker"
	}
	if i := strings.LastIndex(path, "/"); i >= 0 {
		return path[i+1:]
	}
	return path
}

func (s *Server) handleMakesite(w http.ResponseWriter, r *http.Request, path string) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 8<<20))
	if err != nil {
		// python: a read failure kills the connection without a answer
		http.Error(w, "bad body", http.StatusBadRequest)
		return
	}

	var data map[string]any
	if err := json.Unmarshal(body, &data); err != nil {
		// python: json.loads raises INSIDE the handler before the
		// stream is set up → the request dies without a HTTP answer.
		writeJSON(w, http.StatusBadRequest, []byte(`{"error":"invalid JSON body"}`))
		return
	}

	domain := ""
	if v, ok := data["domain"].(string); ok {
		domain = v
	}

	// python _setup_stream(): 200 + text/plain + no-cache BEFORE any
	// console bytes go out; chunked encoding is implicit in both.
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	w.Header().Set("Cache-Control", "no-cache")
	w.WriteHeader(http.StatusOK)

	stream := creator.NewStream(w)
	ok := creator.Run(makesiteType(path), data, stream)

	// python _teardown_stream():
	//   result = {"success": success}
	//   if domain: result["domain"] = domain
	//   wfile.write(f"\n===RESULT===\n{json.dumps(result)}\n")
	var out strings.Builder
	out.WriteString("\n===RESULT===\n{\"success\": ")
	if ok {
		out.WriteString("true")
	} else {
		out.WriteString("false")
	}
	if domain != "" {
		enc, _ := json.Marshal(domain)
		out.WriteString(", \"domain\": ")
		out.Write(enc)
	}
	out.WriteString("}\n")
	_, _ = w.Write([]byte(out.String()))
	if fl, ok := w.(http.Flusher); ok {
		fl.Flush()
	}
}
