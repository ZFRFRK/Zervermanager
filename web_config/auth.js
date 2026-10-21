// ── Zervermanager Auth Layer ─────────────────────────────────────────────────
// Loaded before app.js and fx.js.
// Patches window.fetch so every API request automatically:
//   1. Sends credentials (session cookie)
//   2. Adds X-ZM-CSRF header
//   3. Shows the login modal on 401 then retries transparently
//   4. Shows a toast on 403 / 503 (safe mode)
// ────────────────────────────────────────────────────────────────────────────

(function () {
    'use strict';

    // ── State ─────────────────────────────────────────────────────────────────
    let _csrfToken  = '';
    let _actor      = '';
    let _loginQueue = [];   // pending fetch resolvers waiting for login
    let _loginOpen  = false;

    // ── Session bootstrap ─────────────────────────────────────────────────────
    async function _bootstrapSession() {
        try {
            const r = await _rawFetch('/api/session', { credentials: 'include' });
            if (r.ok) {
                const d = await r.json();
                if (d.ok) {
                    _csrfToken = d.csrf || '';
                    _actor     = d.actor || '';
                    _hideLogin();
                    return true;
                }
            }
        } catch (_) {}
        return false;
    }

    // ── Keep a reference to the real fetch ───────────────────────────────────
    const _rawFetch = window.fetch.bind(window);

    // ── Patched fetch ─────────────────────────────────────────────────────────
    window.fetch = async function zmFetch(input, init = {}) {
        // Only intercept /api/* requests
        const url = typeof input === 'string' ? input : (input.url || '');
        if (!url.startsWith('/api/')) {
            return _rawFetch(input, init);
        }

        // Always send cookies
        init.credentials = 'include';

        // Add CSRF on mutating methods
        const method = (init.method || 'GET').toUpperCase();
        if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method) && _csrfToken) {
            init.headers = Object.assign({}, init.headers || {}, {
                'X-ZM-CSRF': _csrfToken,
            });
        }

        let response = await _rawFetch(input, init);

        // 401 → show login, then retry
        if (response.status === 401) {
            await _requireLogin();
            // After login, re-add CSRF and retry
            if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method) && _csrfToken) {
                init.headers = Object.assign({}, init.headers || {}, {
                    'X-ZM-CSRF': _csrfToken,
                });
            }
            response = await _rawFetch(input, init);
        }

        // 403 → probably stale CSRF; refresh session then retry once
        if (response.status === 403) {
            const refreshed = await _bootstrapSession();
            if (refreshed && _csrfToken) {
                if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
                    init.headers = Object.assign({}, init.headers || {}, {
                        'X-ZM-CSRF': _csrfToken,
                    });
                }
                response = await _rawFetch(input, init);
            }
        }

        // 503 → safe mode
        if (response.status === 503) {
            _showToast('Server is in Safe Mode — only recovery actions are allowed.', 'warning');
        }

        return response;
    };

    // ── Login gate: returns a promise that resolves after successful login ────
    function _requireLogin() {
        return new Promise(resolve => {
            _loginQueue.push(resolve);
            _showLoginModal();
        });
    }

    // ── Login modal UI ────────────────────────────────────────────────────────
    function _showLoginModal() {
        if (_loginOpen) return;
        _loginOpen = true;
        const modal = document.getElementById('zm-login-modal');
        if (modal) {
            modal.classList.add('open');
            setTimeout(() => {
                const pw = document.getElementById('zm-login-password');
                if (pw) pw.focus();
            }, 120);
        }
    }

    function _hideLogin() {
        _loginOpen = false;
        const modal = document.getElementById('zm-login-modal');
        if (modal) modal.classList.remove('open');
        // Update actor badge
        const badge = document.getElementById('zm-actor-badge');
        if (badge && _actor) badge.textContent = _actor;
    }

    // Called by the login form submit button (wired via onclick in HTML)
    window.zmSubmitLogin = async function () {
        const userEl = document.getElementById('zm-login-username');
        const passEl = document.getElementById('zm-login-password');
        const errEl  = document.getElementById('zm-login-error');
        const btn    = document.getElementById('zm-login-btn');

        const username = (userEl ? userEl.value : 'admin').trim() || 'admin';
        const password = passEl ? passEl.value : '';

        if (!password) {
            if (errEl) errEl.textContent = 'Password is required.';
            return;
        }

        if (btn) { btn.disabled = true; btn.textContent = 'Signing in…'; }
        if (errEl) errEl.textContent = '';

        try {
            const r = await _rawFetch('/api/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ username, password }),
            });
            const d = await r.json();
            if (d.ok) {
                _csrfToken = d.csrf || '';
                _actor     = d.actor || username;
                _hideLogin();
                if (passEl) passEl.value = '';
                // Drain the queue — resolve all waiting fetches
                const q = _loginQueue.splice(0);
                q.forEach(resolve => resolve());
                // Refresh the page data
                if (typeof loadDashboard === 'function') loadDashboard();
                if (typeof loadSites    === 'function') loadSites();
            } else {
                if (errEl) errEl.textContent = d.error || 'Invalid credentials.';
            }
        } catch (e) {
            if (errEl) errEl.textContent = 'Could not reach server.';
        } finally {
            if (btn) { btn.disabled = false; btn.textContent = 'Sign in'; }
        }
    };

    // Enter key in password field
    window.zmLoginKeydown = function (e) {
        if (e.key === 'Enter') window.zmSubmitLogin();
    };

    // Logout
    window.zmLogout = async function () {
        await _rawFetch('/api/logout', { credentials: 'include' });
        _csrfToken = '';
        _actor     = '';
        _showLoginModal();
    };

    // ── Toast fallback (before app.js loads) ─────────────────────────────────
    function _showToast(msg, type) {
        if (typeof showToast === 'function') {
            showToast(msg, type);
        } else {
            console.warn('[ZM Auth]', msg);
        }
    }

    // ── Auto-bootstrap on page load ───────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', async () => {
        const ok = await _bootstrapSession();
        if (!ok) _showLoginModal();
    });

}());
