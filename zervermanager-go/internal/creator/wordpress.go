// WordPress installer — parity with python _install_wordpress().
package creator

import (
	"archive/tar"
	"compress/gzip"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// ensureWordPress — python _install_wordpress(): download latest.tar.gz,
// extract, move into docroot, write wp-config.php, chown/chmod.
func (f *Flow) ensureWordPress(docroot, dbName, dbUser, dbPass string) bool {
	s := f.stream
	wpTar := "/tmp/wordpress.tar.gz"

	s.step("Downloading WordPress...")
	if err := httpDownload("https://wordpress.org/latest.tar.gz", wpTar); err != nil {
		s.err("Download failed: " + err.Error())
		return false
	}

	s.step("Extracting WordPress...")
	if err := extractWordPress(wpTar, docroot); err != nil {
		s.err("Extract failed: " + err.Error())
		_ = os.Remove(wpTar)
		_ = os.RemoveAll("/tmp/wordpress")
		return false
	}
	_ = os.Remove(wpTar)
	_ = os.RemoveAll("/tmp/wordpress")

	// Write wp-config.php
	sample := filepath.Join(docroot, "wp-config-sample.php")
	dst := filepath.Join(docroot, "wp-config.php")
	if data, err := os.ReadFile(sample); err == nil {
		cfg := string(data)
		cfg = strings.ReplaceAll(cfg, "database_name_here", dbName)
		cfg = strings.ReplaceAll(cfg, "username_here", dbUser)
		cfg = strings.ReplaceAll(cfg, "password_here", dbPass)
		cfg = strings.ReplaceAll(cfg, "localhost", "localhost")
		if err := os.WriteFile(dst, []byte(cfg), 0o644); err == nil {
			s.ok("wp-config.php configured.")
		}
	} else {
		s.warn("wp-config-sample.php not found; configure wp-config.php manually.")
	}

	_ = f.run(5*time.Minute, "chown", "-R", "www-data:www-data", docroot)
	_ = f.run(5*time.Minute, "chmod", "-R", "755", docroot)
	s.ok("WordPress files extracted and configured.")
	return true
}

// httpDownload — urllib.request.urlretrieve equivalent with a hard cap.
func httpDownload(url, dest string) error {
	client := &http.Client{Timeout: 10 * time.Minute}
	resp, err := client.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("HTTP %d", resp.StatusCode)
	}
	out, err := os.Create(dest)
	if err != nil {
		return err
	}
	defer out.Close()
	_, err = io.Copy(out, resp.Body)
	return err
}

// extractWordPress — tarfile.extractall("/tmp/") then move every item
// of /tmp/wordpress into docroot (rmtree existing dest item first).
func extractWordPress(tarPath, docroot string) error {
	fh, err := os.Open(tarPath)
	if err != nil {
		return err
	}
	defer fh.Close()

	gz, err := gzip.NewReader(fh)
	if err != nil {
		return err
	}
	defer gz.Close()

	if err := os.MkdirAll("/tmp", 0o755); err != nil {
		return err
	}

	tr := tar.NewReader(gz)
	for {
		hdr, err := tr.Next()
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		// security: refuse traversal paths
		name := filepath.Clean(hdr.Name)
		if strings.HasPrefix(name, "..") || filepath.IsAbs(name) {
			continue
		}
		target := filepath.Join("/tmp", name)

		switch hdr.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(target, os.FileMode(hdr.Mode)); err != nil {
				return err
			}
		case tar.TypeReg:
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return err
			}
			out, err := os.OpenFile(target, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, os.FileMode(hdr.Mode))
			if err != nil {
				return err
			}
			_, err = io.Copy(out, tr)
			out.Close()
			if err != nil {
				return err
			}
			_ = os.Chtimes(target, hdr.ModTime, hdr.ModTime)
		case tar.TypeSymlink:
			_ = os.MkdirAll(filepath.Dir(target), 0o755)
			_ = os.Remove(target)
			_ = os.Symlink(hdr.Linkname, target)
		}
	}

	if err := os.MkdirAll(docroot, 0o755); err != nil {
		return err
	}
	entries, err := os.ReadDir("/tmp/wordpress")
	if err != nil {
		return err
	}
	for _, e := range entries {
		src := filepath.Join("/tmp/wordpress", e.Name())
		dest := filepath.Join(docroot, e.Name())
		_ = os.RemoveAll(dest) // python rmtree/unlink
		if err := os.Rename(src, dest); err != nil {
			// cross-device fallback: real copy
			if e.IsDir() {
				if err2 := copyTree(src, dest); err2 != nil {
					return err
				}
				_ = os.RemoveAll(src)
			} else if err2 := copyFileWithMeta(src, dest); err2 != nil {
				return err
			}
		}
	}
	return nil
}
