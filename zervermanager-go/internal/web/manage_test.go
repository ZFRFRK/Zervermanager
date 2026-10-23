package web

import (
	"strings"
	"testing"
)

func TestSiteActionJSONSpacing(t *testing.T) {
	got := string(siteActionJSON(true, "Deleted lamp2.lan."))
	want := `{"success": true, "message": "Deleted lamp2.lan."}`
	if got != want {
		t.Fatalf("got %q want %q", got, want)
	}
}

func TestSiteActionJSONVhostUnescaped(t *testing.T) {
	// get_vhost returns raw vhost content: python json.dumps leaves
	// < > & unescaped, so Go must not HTML-escape them either.
	got := string(siteActionJSON(true, "<VirtualHost *:80> & </VirtualHost>"))
	if !strings.Contains(got, `"message": "<VirtualHost *:80> & </VirtualHost>"`) {
		t.Fatalf("HTML-escaped or mangled message: %q", got)
	}
}

func TestUpdateDocrootRegexes(t *testing.T) {
	apache := "ServerName x\nDocumentRoot /var/www/old\n<Directory /var/www/old>\n"
	apache = reDocumentRoot.ReplaceAllLiteralString(apache, "DocumentRoot /var/www/new")
	apache = reDirectoryTag.ReplaceAllLiteralString(apache, "<Directory /var/www/new>")
	if !strings.Contains(apache, "DocumentRoot /var/www/new") ||
		!strings.Contains(apache, "<Directory /var/www/new>") {
		t.Fatalf("apache rewrite failed: %q", apache)
	}

	nginx := `server { root /var/www/old; index index.php; }`
	nginx = reNginxRoot.ReplaceAllLiteralString(nginx, "root /var/www/new;")
	if !strings.Contains(nginx, "root /var/www/new;") {
		t.Fatalf("nginx rewrite failed: %q", nginx)
	}
}

func TestUpdateDbPassRegexes(t *testing.T) {
	wp := "define( 'DB_PASSWORD', 'old' );"
	wp = reWpDbPass.ReplaceAllLiteralString(wp, "define( 'DB_PASSWORD', 'p$1' );")
	if wp != "define( 'DB_PASSWORD', 'p$1' );" {
		t.Fatalf("wp-config rewrite failed: %q", wp)
	}

	idx := `$password = "old";`
	idx = reIdxPassDouble.ReplaceAllLiteralString(idx, `$password = "p$1";`)
	if idx != `$password = "p$1";` {
		t.Fatalf("index.php double-quote rewrite failed: %q", idx)
	}

	idx2 := `$password = 'old';`
	idx2 = reIdxPassSingle.ReplaceAllLiteralString(idx2, `$password = 'p$1';`)
	if idx2 != `$password = 'p$1';` {
		t.Fatalf("index.php single-quote rewrite failed: %q", idx2)
	}

	// Regression: generated LAMP index.php uses $pass, which python's
	// update_db_pass never touched (upstream bug) — the port must.
	gen := `$pass = 'test1234';`
	gen = reIdxPassVarSingle.ReplaceAllLiteralString(gen, `$pass = 'np$1';`)
	if gen != `$pass = 'np$1';` {
		t.Fatalf("$pass single-quote rewrite failed: %q", gen)
	}

	// $password must NOT be swallowed by the $pass patterns.
	notTouched := `$password = 'aaa';`
	notTouched = reIdxPassVarSingle.ReplaceAllLiteralString(notTouched, `$pass = 'np';`)
	if notTouched != `$password = 'aaa';` {
		t.Fatalf("$pass pattern wrongly matched $password: %q", notTouched)
	}
}
