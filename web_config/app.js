// ── Startup Splash ──────────────────────────────────────────────────────────
(function () {
    const splash = document.getElementById('startup-splash');
    if (!splash) return;

    // Populate version badge
    fetch('/api/status')
        .then(r => r.json())
        .then(d => {
            const el = document.getElementById('splash-ver');
            if (el && d.version) el.textContent = d.version;
        })
        .catch(() => {});

    // ── Image slideshow ──
    const photos = splash.querySelectorAll('.splash-photo');
    const dots   = splash.querySelectorAll('.splash-dot');
    let current  = 0;
    const SLIDE_INTERVAL = 2400; // ms per image

    function showSlide(idx) {
        photos.forEach((p, i) => p.classList.toggle('active', i === idx));
        dots.forEach((d, i) => d.classList.toggle('active', i === idx));
        current = idx;
    }

    // Start cycling after first image has settled (~1.3s)
    let slideTimer = null;
    setTimeout(() => {
        slideTimer = setInterval(() => {
            const next = (current + 1) % photos.length;
            showSlide(next);
        }, SLIDE_INTERVAL);
    }, 1300);

    // ── Dismiss after full animation: 8s delay + 0.7s fade ──
    const TOTAL_MS = 8800;
    const dismiss = () => {
        if (slideTimer) clearInterval(slideTimer);
        splash.classList.add('hidden');
        splash.remove();
    };

    // Primary trigger: when CSS fade-out animation completes
    splash.addEventListener('animationend', (e) => {
        if (e.animationName === 'splashFadeOut') dismiss();
    }, { once: true });

    // Fallback
    setTimeout(dismiss, TOTAL_MS);
}());


// ── Custom Dialog Engine ─────────────────────────────────────────────────────
// Promise-based drop-in replacement for confirm() and alert().
// Usage:
//   const ok = await showConfirm('Title', 'Message text', 'Delete');
//   await showAlert('Title', 'Message text');

let _dialogResolve = null;

function _openDialog({ title, msg, okLabel = 'OK', okClass = 'danger-solid', hasCancel = true, extraText = null, isInfo = false }) {
    return new Promise(resolve => {
        _dialogResolve = resolve;
        const overlay = document.getElementById('dialog-overlay');
        const box     = document.getElementById('dialog-box');
        const titleEl = document.getElementById('dialog-title');
        const msgEl   = document.getElementById('dialog-msg');
        const okBtn   = document.getElementById('dialog-ok-btn');
        const cancelBtn = document.getElementById('dialog-cancel-btn');
        const extraDiv = document.getElementById('dialog-extra');
        const extraCheck = document.getElementById('dialog-extra-check');
        const extraTextEl = document.getElementById('dialog-extra-text');
        const iconWarn = document.getElementById('dialog-icon-warn');
        const iconInfo = document.getElementById('dialog-icon-info');
        const dialogIcon = document.getElementById('dialog-icon');

        titleEl.textContent = title;
        msgEl.innerHTML = msg;
        okBtn.textContent = okLabel;
        okBtn.className = 'btn ' + (okClass === 'danger-solid' ? 'btn-danger' : 'btn-' + okClass);
        if (okClass === 'danger-solid') okBtn.classList.add('danger-solid');

        // Info vs destructive variant
        box.classList.toggle('dialog-info', isInfo);
        iconWarn.style.display = isInfo ? 'none' : '';
        iconInfo.style.display = isInfo ? '' : 'none';

        cancelBtn.style.display = hasCancel ? '' : 'none';

        // Optional extra checkbox
        if (extraText) {
            extraTextEl.textContent = extraText;
            extraCheck.checked = false;
            extraDiv.classList.remove('hidden');
        } else {
            extraDiv.classList.add('hidden');
        }

        overlay.classList.add('open');
        // Focus the primary action button
        setTimeout(() => (hasCancel ? cancelBtn : okBtn).focus(), 80);
    });
}

function dialogOk() {
    const extraCheck = document.getElementById('dialog-extra-check');
    const extraDiv = document.getElementById('dialog-extra');
    const hasExtra = !extraDiv.classList.contains('hidden');
    document.getElementById('dialog-overlay').classList.remove('open');
    if (_dialogResolve) {
        _dialogResolve(hasExtra ? { confirmed: true, extra: extraCheck.checked } : true);
        _dialogResolve = null;
    }
}

function dialogCancel() {
    document.getElementById('dialog-overlay').classList.remove('open');
    if (_dialogResolve) {
        _dialogResolve(false);
        _dialogResolve = null;
    }
}

function dialogOverlayClick(e) {
    // Close on backdrop click (same as cancel)
    if (e.target === document.getElementById('dialog-overlay')) dialogCancel();
}

// Keyboard: Enter = OK, Escape = Cancel
document.addEventListener('keydown', e => {
    if (!document.getElementById('dialog-overlay').classList.contains('open')) return;
    if (e.key === 'Enter')  { e.preventDefault(); dialogOk(); }
    if (e.key === 'Escape') { e.preventDefault(); dialogCancel(); }
});

/**
 * Show a confirmation dialog.
 * @returns {Promise<true|false>} true if user clicked OK, false if cancelled.
 */
function showConfirm(title, msg, okLabel = 'Confirm') {
    return _openDialog({ title, msg, okLabel, okClass: 'danger-solid', hasCancel: true, isInfo: false });
}

/**
 * Show a confirmation with an optional checkbox for a secondary choice.
 * @returns {Promise<{confirmed:true,extra:boolean}|false>}
 */
function showConfirmWithExtra(title, msg, okLabel, extraText) {
    return _openDialog({ title, msg, okLabel, okClass: 'danger-solid', hasCancel: true, extraText, isInfo: false });
}

/**
 * Show an informational alert (no cancel button).
 * @returns {Promise<void>}
 */
function showAlert(title, msg) {
    return _openDialog({ title, msg, okLabel: 'OK', okClass: 'primary', hasCancel: false, isInfo: true });
}

// ── Toast Notification System ────────────────────────────────────────────────
// Drop-in replacement — called from siteAction, quickServiceAction, deleteDnsZone, etc.
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const icons = {
        success: '✓',
        error:   '✕',
        info:    'ℹ',
        warning: '⚠'
    };
    const titles = {
        success: 'Success',
        error:   'Error',
        info:    'Info',
        warning: 'Warning'
    };

    const toast = document.createElement('div');
    toast.className = 'toast ' + type;
    toast.innerHTML =
        '<span class="toast-icon">' + (icons[type] || icons.info) + '</span>' +
        '<div class="toast-body">' +
            '<div class="toast-title">' + (titles[type] || titles.info) + '</div>' +
            '<div class="toast-msg">' + message + '</div>' +
        '</div>' +
        '<div class="toast-progress"></div>';

    toast.addEventListener('click', () => dismissToast(toast));
    container.appendChild(toast);

    // Cap at 3 visible toasts
    const toasts = container.querySelectorAll('.toast:not(.toast-out)');
    if (toasts.length > 3) dismissToast(toasts[0]);

    // Auto-dismiss after 4s
    setTimeout(() => dismissToast(toast), 4000);
}

function dismissToast(el) {
    if (!el || el.classList.contains('toast-out')) return;
    el.classList.add('toast-out');
    el.addEventListener('animationend', () => el.remove(), { once: true });
    // Fallback removal
    setTimeout(() => { if (el.parentNode) el.remove(); }, 400);
}

// ── Goldfish Mode ────────────────────────────────────────────────────────────
// Toggles "Simple Mode" — hides advanced form fields site-wide.
// State is persisted to localStorage.

let _goldfishToastTimer = null;

function applyGoldfishMode(isActive, showToastMsg = false) {
    document.body.classList.toggle('goldfish-mode', isActive);

    const btn = document.getElementById('goldfish-btn');
    if (btn) btn.classList.toggle('is-active', isActive);

    // If LEMP tab is active and we're entering goldfish mode, switch to LAMP
    if (isActive) {
        const lempPane = document.getElementById('tab-makesite-lemp');
        if (lempPane && lempPane.classList.contains('active')) {
            // Switch to LAMP tab
            const lampBtn = document.querySelector('.content-tab-btn[data-tab="lamp"]');
            if (lampBtn) lampBtn.click();
        }
    }

    if (showToastMsg) {
        const toast = document.getElementById('goldfish-toast');
        const msgEl = document.getElementById('goldfish-toast-msg');
        if (toast) {
            if (msgEl) msgEl.textContent = isActive
                ? '🐟 Simple Mode ON — advanced options hidden'
                : '⚙️ Simple Mode OFF — all options restored';
            toast.classList.add('show');
            clearTimeout(_goldfishToastTimer);
            _goldfishToastTimer = setTimeout(() => toast.classList.remove('show'), 2800);
        }
    }
}

function toggleGoldfishMode() {
    const isNowActive = !document.body.classList.contains('goldfish-mode');
    localStorage.setItem('goldfish-mode', isNowActive ? '1' : '0');
    applyGoldfishMode(isNowActive, true);
}

// Restore goldfish mode on page load
(function () {
    if (localStorage.getItem('goldfish-mode') === '1') {
        // Apply after DOM is fully ready
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', () => applyGoldfishMode(true, false));
        } else {
            applyGoldfishMode(true, false);
        }
    }
})();

// ── Top Bar Reload All ───────────────────────────────────────────────────────
async function topBarReloadAll() {
    const term = document.getElementById('reload-term');
    if (term) term.classList.add('visible');

    const fakeForm = document.createElement('form');
    const btn = document.createElement('button');
    btn.type = 'submit';
    btn.innerHTML = 'Reload All';
    fakeForm.appendChild(btn);

    const event = { preventDefault: () => {}, target: fakeForm };
    await submitApiForm(event, '/api/manage/reload', 'reload-term');
}

// Page Navigation
function switchPage(pageId) {
    const current = document.querySelector('.page-section.active');
    const target = document.getElementById('page-' + pageId);
    if (!target || (current && current.id === 'page-' + pageId)) return;

    const navItem = document.querySelector('[data-page="' + pageId + '"]');
    const pageTitle = navItem ? navItem.getAttribute('data-title') : pageId;

    let switched = false;
    const finishSwitch = () => {
        if (switched) return;   // animationend AND the 250ms fallback both call this — run once
        switched = true;
        // Hide all pages
        document.querySelectorAll('.page-section').forEach(p => {
            p.classList.remove('active', 'exiting');
            p.style.animation = '';
        });
        // Deactivate all nav items
        document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
        
        // Show selected page
        target.classList.add('active');
        // Activate nav item
        if (navItem) navItem.classList.add('active');
        
        // Update breadcrumb
        updateBreadcrumb(pageTitle);
        // Load data for specific pages
        if (pageId === 'managesites') {
            loadSites();
        } else if (pageId === 'dns') {
            loadDNS();
        } else if (pageId === 'services') {
            loadMailUsers();
        } else if (pageId === 'docker') {
            loadDocker();
        }
    };

    if (current && current.id !== 'page-' + pageId && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        current.classList.add('exiting');
        current.style.animation = 'pageOut 0.18s cubic-bezier(0.4, 0, 1, 1) forwards';
        current.addEventListener('animationend', function handler(e) {
            if (e.animationName === 'pageOut') {
                current.removeEventListener('animationend', handler);
                finishSwitch();
            }
        });
        // Fallback in case animationend doesn't fire
        setTimeout(finishSwitch, 250);
    } else {
        finishSwitch();
    }
}

// Breadcrumb
function updateBreadcrumb(pageTitle, tabTitle) {
    const crumb = document.getElementById('breadcrumb-trail');
    if (!crumb) return;
    let html = '<span class="breadcrumb-item">' + pageTitle + '</span>';
    if (tabTitle) {
        html += '<svg class="breadcrumb-sep" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"></polyline></svg>';
        html += '<span class="breadcrumb-item active">' + tabTitle + '</span>';
    }
    crumb.innerHTML = html;
}

// Tab Navigation inside Pages
function switchTab(pageId, tabId) {
    const page = document.getElementById('page-' + pageId);
    // Hide all tabs
    page.querySelectorAll('.tab-pane').forEach(t => t.classList.remove('active'));
    // Deactivate all tab buttons
    page.querySelectorAll('.content-tab-btn').forEach(b => b.classList.remove('active'));
    
    // Show selected tab
    document.getElementById('tab-' + pageId + '-' + tabId).classList.add('active');
    // Activate tab button
    const activeBtn = page.querySelector('[data-tab="' + tabId + '"]');
    activeBtn.classList.add('active');

    // Update breadcrumb with tab context
    const navItem = document.querySelector('[data-page="' + pageId + '"]');
    const pageTitle = navItem ? navItem.getAttribute('data-title') : pageId;
    const tabTitle = activeBtn.textContent.trim();
    updateBreadcrumb(pageTitle, tabTitle);

    // Show/hide tab description
    page.querySelectorAll('.tab-desc').forEach(d => d.classList.remove('active'));
    const desc = page.querySelector('.tab-desc[data-tab="' + tabId + '"]');
    if (desc) desc.classList.add('active');
}

// Generic API Form Submitter
async function submitApiForm(event, endpoint, terminalId) {
    event.preventDefault();
    const form = event.target;
    const btn = form.querySelector('button[type="submit"]');
    const term = document.getElementById(terminalId);
    
    // Clear previous errors
    form.querySelectorAll('.field-error').forEach(el => el.classList.remove('field-error'));
    
    // Client-side validation
    let isValid = true;
    let firstErrorField = null;
    form.querySelectorAll('[required]').forEach(field => {
        if (!field.value.trim()) {
            isValid = false;
            const group = field.closest('.form-group');
            if (group) {
                group.classList.add('field-error');
                if (!firstErrorField) firstErrorField = field;
                if (!group.querySelector('.field-error-msg')) {
                    const msg = document.createElement('span');
                    msg.className = 'field-error-msg';
                    msg.innerText = 'This field is required';
                    group.appendChild(msg);
                }
            }
        }
    });

    form.querySelectorAll('[required]').forEach(field => {
        field.addEventListener('input', function() {
            const group = this.closest('.form-group');
            if (group) group.classList.remove('field-error');
        }, { once: true });
    });

    if (!isValid) {
        if (firstErrorField) firstErrorField.focus();
        return;
    }

    // Gather form data into JSON
    const formData = new FormData(form);
    const payload = {};
    for (let [key, value] of formData.entries()) {
        payload[key] = value;
    }
    // Handle checkboxes
    form.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        if (!cb.checked) delete payload[cb.name];
        else payload[cb.name] = "on";
    });

    const originalHTML = btn.innerHTML;
    let execSeconds = 0;
    btn.innerHTML = `<div class="btn-spinner"></div> Executing... <span class="btn-timer">(0s)</span>`;
    btn.disabled = true;
    term.classList.add('visible');
    
    const execTimer = setInterval(() => {
        execSeconds++;
        const t = btn.querySelector('.btn-timer');
        if (t) t.innerText = `(${execSeconds}s)`;
    }, 1000);
    
    function setTerminalHTML(text) {
        let escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/\n/g, '<br>');
        
        const colorMap = {
            '91': 'var(--danger)',
            '92': 'var(--success)',
            '93': 'var(--warning)',
            '94': '#3b82f6',
            '96': '#06b6d4',
            '1': 'font-weight: 600;',
            '2': 'opacity: 0.5;'
        };
        
        let openSpans = 0;
        escaped = escaped.replace(/\x1b\[([0-9;]*)m/g, (match, codes) => {
            if (codes === '0' || codes === '') {
                let res = '</span>'.repeat(openSpans);
                openSpans = 0;
                return res;
            }
            let style = '';
            for (let code of codes.split(';')) {
                if (colorMap[code]) {
                    if (code === '1' || code === '2') style += colorMap[code] + ' ';
                    else style += 'color: ' + colorMap[code] + '; ';
                }
            }
            if (style) {
                openSpans++;
                return '<span style="' + style + '">';
            }
            return '';
        });
        escaped += '</span>'.repeat(openSpans);

        term.innerHTML = '<span style="color:var(--text-muted);font-size:0.72rem">running...</span><br><br>' + escaped;
        term.scrollTop = term.scrollHeight;
    }

    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");
        let fullText = "";
        
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            fullText += decoder.decode(value, {stream: true});
            
            let displayParts = fullText.split('\n===RESULT===\n');
            setTerminalHTML(displayParts[0]);
            
            if (displayParts.length > 1) {
                try {
                    const result = JSON.parse(displayParts[1]);
                    if (result.success) {
                        term.innerHTML += "<br><br><span style='color: var(--success); font-weight: 600;'>&#10003; Task completed successfully.</span>";
                        if (result.domain) {
                            const proto = payload.ssl === '2' ? 'https' : 'http';
                            term.innerHTML += '<br><br>&#128279; <b>Site Ready:</b> <a href="' + proto + '://' + result.domain + '" target="_blank" style="color: var(--accent); text-decoration: underline;">' + proto + '://' + result.domain + '</a>';
                        }
                        form.reset();
                    } else {
                        term.innerHTML += "<br><br><span style='color: var(--danger); font-weight: 600;'>&#10007; Task failed or encountered an error.</span>";
                    }
                } catch(e) {
                    term.innerHTML += "<br><br><span style='color: var(--danger); font-weight: 600;'>&#10007; Failed to parse server result.</span>";
                }
            }
        }
    } catch (err) {
        term.innerHTML += '<br><br><span style=\'color: var(--danger); font-weight: 600;\'>[!] Network or Server Error: ' + err.message + '</span>';
    } finally {
        clearInterval(execTimer);
        btn.innerHTML = originalHTML;
        btn.disabled = false;
        term.scrollTop = term.scrollHeight;
        
        // Refresh status if we are on server management or dashboard
        fetchStatus();
        // Installing / removing the mail server changes the domain and account list
        if (/\/api\/(install|uninstall)\/mail/.test(endpoint)) loadMailUsers();
    }
}

// Status Polling
let isOffline = false;
async function fetchStatus() {
    try {
        const res = await fetch('/api/status');
        if (!res.ok) throw new Error('Offline');
        const data = await res.json();
        
        document.getElementById('version').innerText = data.version || 'Unknown';
        document.getElementById('os-val').innerText = data.os || 'Unknown';
        
        const updateBadge = (id, status) => {
            const el = document.getElementById(id);
            if (!el) return;
            const s = status.toLowerCase();
            const isLg = el.classList.contains('badge-lg');
            const baseClass = isLg ? 'badge badge-lg' : 'badge';

            if (s === 'running') {
                el.innerText = 'RUNNING';
                el.className = baseClass + ' active';
            } else if (s === 'stopped') {
                el.innerText = 'STOPPED';
                el.className = baseClass + ' inactive';
            } else if (s.includes('not installed') || s.includes('not found')) {
                el.innerText = 'NOT INSTALLED';
                el.className = baseClass + ' warning';
            } else {
                el.innerText = status.toUpperCase();
                el.className = baseClass;
            }
        };

        updateBadge('apache2-val', data.apache2 || 'Unknown');
        updateBadge('nginx-val', data.nginx || 'Unknown');
        updateBadge('bind9-val', data.bind9 || 'Unknown');
        updateBadge('mariadb-val', data.mariadb || 'Unknown');
        updateBadge('postfix-val', data.postfix || 'Unknown');
        updateBadge('dovecot-val', data.dovecot || 'Unknown');
        updateBadge('ufw-val', data.ufw || 'Unknown');

        // Docker status
        const dockerEl = document.getElementById('docker-val');
        if (dockerEl) {
            const isDocker = data.docker_installed;
            dockerEl.innerText = isDocker ? 'INSTALLED' : 'NOT INSTALLED';
            dockerEl.className = isDocker ? 'badge badge-lg active' : 'badge badge-lg warning';

            const installCard = document.getElementById('docker-install-card');
            const mainContent = document.getElementById('docker-main-content');
            if (installCard) installCard.style.display = isDocker ? 'none' : 'block';
            if (mainContent) mainContent.style.display = isDocker ? 'block' : 'none';
        }

        const updateTb = (id, st, serviceName) => {
            const el = document.getElementById(id);
            if(!el) return;
            const s = st.toLowerCase();
            el.className = 'top-bar-svc ' + (s === 'running' ? 'is-running' : (s === 'stopped' ? 'is-stopped' : 'is-warning'));
            
            if (serviceName) {
                el.style.cursor = 'pointer';
                el.onclick = () => {
                    switchPage('server');
                    const sel = document.getElementById('manage-service');
                    if (sel) sel.value = serviceName;
                };
            }
        };
        updateTb('tb-apache2', data.apache2 || '', 'apache2');
        updateTb('tb-nginx', data.nginx || '', 'nginx');
        updateTb('tb-bind9', data.bind9 || '', 'bind9');
        updateTb('tb-mariadb', data.mariadb || '', 'mariadb');
        updateTb('tb-postfix', data.postfix || '', 'postfix');
        updateTb('tb-ufw', data.ufw || '', 'ufw');
        
        // ── Server Hardware & Telemetry Metrics ──
        const cpuPercent = typeof data.cpu === 'number' ? data.cpu : parseFloat(data.cpu || 0);
        const ramPercent = typeof data.ram === 'number' ? data.ram : parseFloat(data.ram || 0);
        const uptimeStr  = data.uptime || '—';

        // CPU Metric
        const cpuValEl = document.getElementById('metric-cpu-val');
        if (cpuValEl) cpuValEl.textContent = isNaN(cpuPercent) ? '0.0' : cpuPercent.toFixed(1);

        const cpuBarEl = document.getElementById('metric-cpu-bar');
        if (cpuBarEl) {
            const pct = isNaN(cpuPercent) ? 0 : Math.min(100, Math.max(0, cpuPercent));
            cpuBarEl.style.width = pct + '%';
            cpuBarEl.classList.toggle('is-warning', pct >= 70 && pct < 85);
            cpuBarEl.classList.toggle('is-danger', pct >= 85);
        }

        const cpuStateEl = document.getElementById('metric-cpu-state');
        if (cpuStateEl) {
            if (cpuPercent >= 85) {
                cpuStateEl.textContent = 'HIGH';
                cpuStateEl.className = 'metric-state-pill is-danger';
            } else if (cpuPercent >= 70) {
                cpuStateEl.textContent = 'MODERATE';
                cpuStateEl.className = 'metric-state-pill is-warning';
            } else {
                cpuStateEl.textContent = 'NORMAL';
                cpuStateEl.className = 'metric-state-pill is-normal';
            }
        }

        const cpuCoresEl = document.getElementById('metric-cpu-cores');
        if (cpuCoresEl && data.cpu_cores) {
            cpuCoresEl.textContent = `${data.cpu_cores} Core${data.cpu_cores > 1 ? 's' : ''}`;
        }

        const cpuLoadEl = document.getElementById('metric-cpu-load');
        if (cpuLoadEl && data.cpu_load) {
            cpuLoadEl.textContent = `Load avg: ${data.cpu_load}`;
        }

        // RAM Metric
        const ramValEl = document.getElementById('metric-ram-val');
        if (ramValEl) ramValEl.textContent = isNaN(ramPercent) ? '0.0' : ramPercent.toFixed(1);

        const ramBarEl = document.getElementById('metric-ram-bar');
        if (ramBarEl) {
            const pct = isNaN(ramPercent) ? 0 : Math.min(100, Math.max(0, ramPercent));
            ramBarEl.style.width = pct + '%';
            ramBarEl.classList.toggle('is-warning', pct >= 75 && pct < 90);
            ramBarEl.classList.toggle('is-danger', pct >= 90);
        }

        const ramStateEl = document.getElementById('metric-ram-state');
        if (ramStateEl) {
            if (ramPercent >= 90) {
                ramStateEl.textContent = 'CRITICAL';
                ramStateEl.className = 'metric-state-pill is-danger';
            } else if (ramPercent >= 75) {
                ramStateEl.textContent = 'ELEVATED';
                ramStateEl.className = 'metric-state-pill is-warning';
            } else {
                ramStateEl.textContent = 'NORMAL';
                ramStateEl.className = 'metric-state-pill is-normal';
            }
        }

        const ramExtraEl = document.getElementById('metric-ram-extra');
        if (ramExtraEl && data.ram_formatted) {
            ramExtraEl.textContent = `Used: ${data.ram_formatted}`;
        }

        // Uptime Metric
        const uptimeValEl = document.getElementById('metric-uptime-val');
        if (uptimeValEl) uptimeValEl.textContent = uptimeStr;

        const uptimeBootEl = document.getElementById('metric-uptime-boot');
        if (uptimeBootEl && data.boot_time && data.boot_time !== 'N/A') {
            uptimeBootEl.textContent = `Booted: ${data.boot_time}`;
        }

        const uptimeExtraEl = document.getElementById('metric-uptime-extra');
        if (uptimeExtraEl && typeof data.uptime_seconds === 'number') {
            const hrs = Math.floor(data.uptime_seconds / 3600);
            uptimeExtraEl.textContent = `${hrs} total operating hours`;
        }

        // ── Dashboard Resources Viewer Pills ──
        // RAM Pill
        const pillRamVal = document.getElementById('pill-ram-val');
        const pillRamPip = document.getElementById('pill-ram-pip');
        const pillRam = document.getElementById('pill-ram');
        if (pillRamVal) {
            pillRamVal.textContent = isNaN(ramPercent) ? '—' : `${ramPercent.toFixed(1)}%`;
        }
        if (pillRamPip) {
            pillRamPip.className = 'res-pip ' + (ramPercent >= 90 ? 'is-danger' : (ramPercent >= 75 ? 'is-warning' : 'is-normal'));
        }
        if (pillRam && data.ram_formatted) {
            pillRam.title = `RAM Usage: ${ramPercent.toFixed(1)}% (${data.ram_formatted})`;
        }

        // CPU Usage Pill
        const pillCpuVal = document.getElementById('pill-cpu-val');
        const pillCpuPip = document.getElementById('pill-cpu-pip');
        const pillCpu = document.getElementById('pill-cpu');
        if (pillCpuVal) {
            pillCpuVal.textContent = isNaN(cpuPercent) ? '—' : `${cpuPercent.toFixed(1)}%`;
        }
        if (pillCpuPip) {
            pillCpuPip.className = 'res-pip ' + (cpuPercent >= 85 ? 'is-danger' : (cpuPercent >= 70 ? 'is-warning' : 'is-normal'));
        }
        if (pillCpu) {
            const coresTxt = data.cpu_cores ? `${data.cpu_cores} Core${data.cpu_cores > 1 ? 's' : ''}` : '';
            const loadTxt = data.cpu_load ? `Load: ${data.cpu_load}` : '';
            pillCpu.title = `CPU: ${cpuPercent.toFixed(1)}%${coresTxt ? ' (' + coresTxt + (loadTxt ? ', ' + loadTxt : '') + ')' : ''}`;
        }

        // CPU Temperature Pill
        const pillTempVal = document.getElementById('pill-temp-val');
        const pillTempPip = document.getElementById('pill-temp-pip');
        const pillTemp = document.getElementById('pill-temp');
        if (pillTempVal) {
            if (typeof data.cpu_temp === 'number') {
                pillTempVal.textContent = `${data.cpu_temp.toFixed(1)}°C`;
                if (pillTempPip) {
                    pillTempPip.className = 'res-pip ' + (data.cpu_temp >= 75 ? 'is-danger' : (data.cpu_temp >= 60 ? 'is-warning' : 'is-normal'));
                }
                if (pillTemp) pillTemp.title = `CPU Temperature: ${data.cpu_temp.toFixed(1)}°C`;
            } else if (data.cpu_temp) {
                pillTempVal.textContent = `${data.cpu_temp}°C`;
                if (pillTempPip) pillTempPip.className = 'res-pip is-normal';
            } else {
                pillTempVal.textContent = 'N/A';
                if (pillTempPip) pillTempPip.className = 'res-pip';
                if (pillTemp) pillTemp.title = 'CPU Temperature: sensor unavailable';
            }
        }

        // Uptime Pill
        const pillUptimeVal = document.getElementById('pill-uptime-val');
        const pillUptimePip = document.getElementById('pill-uptime-pip');
        const pillUptime = document.getElementById('pill-uptime');
        if (pillUptimeVal) {
            let shortUptime = uptimeStr;
            if (typeof data.uptime_seconds === 'number') {
                const s = data.uptime_seconds;
                const days = Math.floor(s / 86400);
                const hrs = Math.floor((s % 86400) / 3600);
                const mins = Math.floor((s % 3600) / 60);
                if (days > 0) {
                    shortUptime = `${days}d ${hrs}h`;
                } else if (hrs > 0) {
                    shortUptime = `${hrs}h ${mins}m`;
                } else {
                    shortUptime = `${mins}m`;
                }
            }
            pillUptimeVal.textContent = shortUptime;
        }
        if (pillUptimePip) {
            pillUptimePip.className = 'res-pip is-normal';
        }
        if (pillUptime) {
            pillUptime.title = `System Uptime: ${uptimeStr}${data.boot_time ? ' (Booted: ' + data.boot_time + ')' : ''}`;
        }

        // Waybar hardware pills in top-bar
        const tbCpuVal = document.getElementById('tb-hw-cpu-val');
        if (tbCpuVal) tbCpuVal.textContent = isNaN(cpuPercent) ? '—' : `${Math.round(cpuPercent)}%`;

        const tbRamVal = document.getElementById('tb-hw-ram-val');
        if (tbRamVal) tbRamVal.textContent = isNaN(ramPercent) ? '—' : `${Math.round(ramPercent)}%`;

        const tbUptimeVal = document.getElementById('tb-hw-uptime-val');
        if (tbUptimeVal) tbUptimeVal.textContent = uptimeStr.split(' ')[0] || uptimeStr;

        if(isOffline) {
            isOffline = false;
            const ob = document.getElementById('offline-banner');
            if(ob) ob.classList.add('hidden');
        }
    } catch (e) {
        if(!isOffline) {
            isOffline = true;
            const ob = document.getElementById('offline-banner');
            if(ob) ob.classList.remove('hidden');
            
            // Update statuses to OFFLINE
            const badges = ['apache2-val', 'nginx-val', 'bind9-val', 'mariadb-val', 'postfix-val', 'dovecot-val', 'ufw-val'];
            badges.forEach(id => {
                const el = document.getElementById(id);
                if(el) { el.innerText = 'OFFLINE'; el.className = (el.classList.contains('badge-lg') ? 'badge badge-lg inactive' : 'badge inactive'); }
            });
            const tbs = ['tb-apache2', 'tb-nginx', 'tb-bind9', 'tb-mariadb', 'tb-postfix', 'tb-ufw'];
            tbs.forEach(id => {
                const el = document.getElementById(id);
                if(el) el.className = 'top-bar-svc is-stopped';
            });

            // Offline fallback for hardware metrics
            const cpuValEl = document.getElementById('metric-cpu-val');
            if (cpuValEl) cpuValEl.textContent = '—';
            const ramValEl = document.getElementById('metric-ram-val');
            if (ramValEl) ramValEl.textContent = '—';
            const uptimeValEl = document.getElementById('metric-uptime-val');
            if (uptimeValEl) uptimeValEl.textContent = '—';
            const tbCpuVal = document.getElementById('tb-hw-cpu-val');
            if (tbCpuVal) tbCpuVal.textContent = '—';
            const tbRamVal = document.getElementById('tb-hw-ram-val');
            if (tbRamVal) tbRamVal.textContent = '—';
            const tbUptimeVal = document.getElementById('tb-hw-uptime-val');
            if (tbUptimeVal) tbUptimeVal.textContent = '—';

            // Resource pills offline fallback
            const pillRamVal = document.getElementById('pill-ram-val');
            if (pillRamVal) pillRamVal.textContent = '—';
            const pillCpuVal = document.getElementById('pill-cpu-val');
            if (pillCpuVal) pillCpuVal.textContent = '—';
            const pillTempVal = document.getElementById('pill-temp-val');
            if (pillTempVal) pillTempVal.textContent = '—';
            const pillUptimeVal = document.getElementById('pill-uptime-val');
            if (pillUptimeVal) pillUptimeVal.textContent = '—';
            ['pill-ram-pip', 'pill-cpu-pip', 'pill-temp-pip', 'pill-uptime-pip'].forEach(id => {
                const p = document.getElementById(id);
                if (p) p.className = 'res-pip';
            });
        }
    }
}

// Quick service action from dashboard
async function quickServiceAction(service, action) {
    const actionCap = action.charAt(0).toUpperCase() + action.slice(1);
    const ok = await showConfirm(
        actionCap + ' ' + service,
        'Are you sure you want to <strong>' + action + '</strong> ' + service + '?'
    );
    if (!ok) return;

    try {
        const res = await fetch('/api/manage/service', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({service: service, action: action})
        });
        const text = await res.text();
        const parts = text.split('\n===RESULT===\n');
        const jsonStr = parts.length > 1 ? parts[1] : parts[0];
        const data = JSON.parse(jsonStr);

        if (data.success || data.status === 'success') {
            showToast(service + ' ' + action + ' successful.', 'success');
            fetchStatus();
        } else {
            showAlert('Action Failed', 'Failed to ' + action + ' ' + service + '. Check logs.');
        }
    } catch (e) {
        showAlert('Error', 'Network error: ' + e.message);
    }
}

// Initial fetch
fetchStatus();
// Poll every 10 seconds
setInterval(fetchStatus, 10000);

// MariaDB UI toggles
function toggleMariadbDbFields() {
    const action = document.getElementById('mariadb-db-action').value;
    document.getElementById('mariadb-db-name').classList.toggle('hidden', action === 'list');
    document.getElementById('mariadb-db-path').classList.toggle('hidden', !['backup', 'restore'].includes(action));
    document.getElementById('mariadb-db-confirm').classList.toggle('hidden', !['drop', 'restore'].includes(action));
}

function toggleMariadbUserFields() {
    const action = document.getElementById('mariadb-user-action').value;
    document.getElementById('mariadb-user-name').classList.toggle('hidden', action === 'list');
    document.getElementById('mariadb-user-pass').classList.toggle('hidden', !['create', 'password'].includes(action));
    document.getElementById('mariadb-user-db').classList.toggle('hidden', !['grant', 'revoke'].includes(action));
}

// Custom action for buttons outside normal form submit
function submitCustomAction(btn, endpoint) {
    const form = btn.closest('form');
    const fakeEvent = {
        preventDefault: () => {},
        target: form
    };
    
    const originalType = btn.type;
    btn.type = 'submit';
    
    const terminalId = form.closest('.card').querySelector('.terminal').id;
    
    submitApiForm(fakeEvent, endpoint, terminalId).finally(() => {
        btn.type = originalType;
    });
}

// --- Manage Sites ---

async function loadSites() {
    const tbody = document.getElementById('sites-tbody');
    if (!tbody) return;

    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:20px">Loading...</td></tr>';

    try {
        const res = await fetch('/api/sites');
        const sites = await res.json();

        if (sites.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" style="padding:0">
                <div class="empty-state">
                    <div class="empty-state-icon">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>
                    </div>
                    <div class="empty-state-title">No sites configured yet</div>
                    <div class="empty-state-sub">Create your first site with the Make Site tool.</div>
                    <button class="btn btn-primary" onclick="switchPage('makesite')">Make Site</button>
                </div>
            </td></tr>`;
            return;
        }

        let rows = '';
        for (let i = 0; i < sites.length; i++) {
            const s = sites[i];
            const statusClass = s.enabled ? 'active' : 'inactive';
            const statusText = s.enabled ? 'Enabled' : 'Disabled';
            const toggleAction = s.enabled ? 'disable' : 'enable';
            const toggleText = s.enabled ? 'Disable' : 'Enable';
            const toggleBtnClass = s.enabled ? '' : 'btn-toggle';
            const d = s.domain;
            const sv = s.server;
            const tp = s.type;
            const dr = s.docroot;

            rows += '<tr>';
            rows += `<td><a href="http://${d}" target="_blank" class="site-domain-link"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg> ${d}</a></td>`;
            rows += '<td><span class="badge" style="background:var(--bg-lighter)">' + sv + '</span></td>';
            rows += '<td><span class="badge" style="background:var(--bg-lighter)">' + tp + '</span></td>';
            rows += '<td><span class="badge ' + statusClass + '">' + statusText + '</span></td>';
            rows += '<td class="actions-cell" style="display:flex;gap:6px;flex-wrap:wrap">';
            rows += `<button class="btn-sm ${toggleBtnClass}" onclick="toggleSite('${sv}','${d}','${toggleAction}')">${toggleText}</button>`;
            rows += `<button class="btn-sm btn-modify" onclick="openModifyModal('${sv}','${d}','${tp}','${dr.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}')">Modify</button>`;
            rows += `<button class="btn-sm btn-delete" onclick="deleteSite('${sv}','${d}')">Delete</button>`;
            rows += '</td></tr>';
        }
        tbody.innerHTML = rows;

    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--danger);padding:20px">Disconnected from server. Retrying...</td></tr>';
    }
}

async function siteAction(payload, confirmMsg) {
    if (confirmMsg) {
        const ok = await showConfirm('Confirm Action', confirmMsg, 'Proceed');
        if (!ok) return null;
    }

    try {
        const res = await fetch('/api/manage/site_action', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        if (result.success) {
            if (payload.action !== 'get_vhost') {
                showToast(result.message, 'success');
                loadSites();
            }
            return result;
        } else {
            showToast('Error: ' + result.message, 'error');
        }
    } catch (e) {
        showToast('Request failed: ' + e.message, 'error');
    }
    return null;
}

function toggleSite(server, domain, action) {
    siteAction({action: action, server: server, domain: domain});
}

async function deleteSite(server, domain) {
    const result = await showConfirmWithExtra(
        'Delete Site',
        'Are you sure you want to completely delete <strong>' + domain + '</strong>?<br><br>This will remove the web server configuration and all associated files.',
        'Delete Site',
        'Also delete the document root (website files) for ' + domain
    );
    if (!result) return;
    siteAction({ action: 'delete', server: server, domain: domain, remove_docroot: result.extra });
}

function openModifyModal(server, domain, type, docroot) {
    document.getElementById('modify-site-modal').classList.add('open');
    document.getElementById('modify-site-title').innerText = 'Modify ' + domain;

    document.getElementById('mod-domain').value = domain;
    document.getElementById('mod-server').value = server;
    document.getElementById('mod-docroot-input').value = docroot;
    document.getElementById('mod-domain-db').value = domain;
    document.getElementById('mod-domain-vhost').value = domain;
    document.getElementById('mod-server-vhost').value = server;

    const hasDb = (type === 'lamp' || type === 'lemp' || type === 'wordpress');
    const dbBtn = document.querySelector('#modify-site-modal .content-tab-btn[data-tab="dbpass"]');
    if (dbBtn) dbBtn.style.display = hasDb ? '' : 'none';

    document.getElementById('mod-vhost-content').value = 'Loading configuration...';
    updateHighlighting('Loading configuration...');
    
    siteAction({action: 'get_vhost', server: server, domain: domain}).then(function(res) {
        if (res && res.success) {
            document.getElementById('mod-vhost-content').value = res.message;
            updateHighlighting(res.message);
        } else {
            document.getElementById('mod-vhost-content').value = 'Failed to load configuration.';
            updateHighlighting('Failed to load configuration.');
        }
    });

    switchModifyTab('docroot');
}

function closeModifyModal() {
    document.getElementById('modify-site-modal').classList.remove('open');
}

function switchModifyTab(tabId) {
    const tabs = ['docroot', 'dbpass', 'vhost'];
    for (let i=0; i<tabs.length; i++) {
        const t = tabs[i];
        document.getElementById('mod-tab-' + t).classList.remove('active');
        document.querySelector('.content-tab-btn[data-tab="' + t + '"]').classList.remove('active');
    }
    document.getElementById('mod-tab-' + tabId).classList.add('active');
    document.querySelector('.content-tab-btn[data-tab="' + tabId + '"]').classList.add('active');
}

// --- DNS Management ---
async function loadDNS() {
    const tbody = document.getElementById('dns-tbody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="3" style="text-align:center;padding:32px;color:var(--text-muted)">Loading...</td></tr>';
    
    try {
        const res = await fetch('/api/manage/dns?action=list');
        const data = await res.json();
        
        if (!data || data.length === 0) {
            tbody.innerHTML = `<tr><td colspan="3" style="padding:0">
                <div class="empty-state">
                    <div class="empty-state-icon">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2H3v16h5v4l4-4h5l4-4V2zM11 11V7M16 11V7"/></svg>
                    </div>
                    <div class="empty-state-title">No DNS zones found</div>
                    <div class="empty-state-sub">Zones are automatically created when making a site, or you can add them manually.</div>
                </div>
            </td></tr>`;
            return;
        }
        
        let rows = '';
        for (let i = 0; i < data.length; i++) {
            const z = data[i];
            rows += '<tr>';
            rows += '<td><span class="site-domain">' + z.domain + '</span></td>';
            rows += '<td><span class="site-tag">' + z.type + '</span></td>';
            rows += '<td class="actions-cell">';
            rows += '<button class="btn-sm btn-delete" onclick="deleteDnsZone(\'' + z.domain + '\', \'' + z.type + '\')">Delete</button>';
            rows += '</td>';
            rows += '</tr>';
        }
        tbody.innerHTML = rows;
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center;padding:32px;color:var(--danger)">Disconnected from server. Retrying...</td></tr>';
    }
}

async function deleteDnsZone(domain, type) {
    const ok = await showConfirm(
        'Delete DNS Zone',
        'Are you sure you want to delete the <strong>' + type + '</strong> zone for <strong>' + domain + '</strong>?'
    );
    if (!ok) return;
    
    try {
        const res = await fetch('/api/manage/dns', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'delete', domain: domain, type: type })
        });
        const text = await res.text();
        const parts = text.split('\n===RESULT===\n');
        const jsonStr = parts.length > 1 ? parts[1] : parts[0];
        const result = JSON.parse(jsonStr);
        
        if (result.success || result.status === 'success') {
            loadDNS();
            showToast('DNS zone deleted.', 'success');
        } else {
            showAlert('Delete Failed', 'Could not delete the DNS zone. Please check the server logs.');
        }
    } catch (e) {
        showAlert('Request Failed', 'Network or server error: ' + e.message);
    }
}

// --- Mail accounts ---
// An account is a system user in the "mailuser" group; the address is <username>@<mail domain>.
let _mailDomain = '';

function mailParseResult(text) {
    const parts = text.split('\n===RESULT===\n');
    try { return JSON.parse(parts.length > 1 ? parts[1] : parts[0]); } catch (e) { return {}; }
}

// The server streams console output, then "===RESULT===" + a JSON verdict. The text after the
// last "✗" is the human-readable reason, so show that instead of a generic message.
function mailErrorFrom(text) {
    const body = (text.split('\n===RESULT===\n')[0] || '').replace(/\x1b\[[0-9;]*m/g, '');
    const at = body.lastIndexOf('✗');
    if (at < 0) return 'Something went wrong. Check the server logs.';
    return body.slice(at + 1).trim().replace(/\s*\n\s*/g, ' — ').slice(0, 300);
}

function renderMailAccounts(info) {
    const list   = document.getElementById('mail-users-list');
    const chip   = document.getElementById('mail-domain-chip');
    const note   = document.getElementById('mail-accounts-note');
    const suffix = document.getElementById('mail-add-suffix');
    const form   = document.getElementById('mail-add-form');
    if (!list) return;

    _mailDomain = info.domain || '';
    const installed = !!info.installed;
    const users = info.users || [];

    chip.hidden = !_mailDomain;
    chip.textContent = _mailDomain ? '@' + _mailDomain : '';
    suffix.textContent = _mailDomain ? '@' + _mailDomain : '';
    suffix.hidden = !_mailDomain;
    note.hidden = installed;
    note.textContent = installed ? '' : 'The mail server isn\u2019t installed yet. Install it above, then add accounts here.';
    form.classList.toggle('is-disabled', !installed);
    Array.from(form.elements).forEach(el => { el.disabled = !installed; });

    list.textContent = '';
    if (!users.length) {
        const li = document.createElement('li');
        li.className = 'mail-empty';
        li.textContent = installed ? 'No accounts yet \u2014 add the first one below.' : '';
        if (installed) list.appendChild(li);
        return;
    }
    users.forEach(user => {
        const email = _mailDomain ? user + '@' + _mailDomain : user;
        const li = document.createElement('li');
        li.className = 'mail-account';

        const av = document.createElement('span');
        av.className = 'mail-avatar';
        av.textContent = user.charAt(0).toUpperCase();

        const txt = document.createElement('span');
        txt.className = 'mail-account-text';
        const addr = document.createElement('span');
        addr.className = 'mail-address';
        addr.textContent = email;
        const sub = document.createElement('span');
        sub.className = 'mail-login';
        sub.textContent = 'webmail login: ' + user;
        txt.append(addr, sub);

        const del = document.createElement('button');
        del.type = 'button';
        del.className = 'btn-sm btn-delete';
        del.textContent = 'Delete';
        del.addEventListener('click', () => deleteMailUser(user));

        li.append(av, txt, del);
        list.appendChild(li);
    });
}

async function loadMailUsers() {
    const list = document.getElementById('mail-users-list');
    if (!list) return;
    try {
        const res = await fetch('/api/manage/mail?action=info');
        renderMailAccounts(await res.json());
    } catch (e) {
        list.textContent = '';
        const li = document.createElement('li');
        li.className = 'mail-empty is-error';
        li.textContent = 'Can\u2019t reach the server right now.';
        list.appendChild(li);
    }
}

async function addMailUser(event) {
    event.preventDefault();
    const form = event.target;
    const btn = document.getElementById('mail-add-btn');
    const userEl = form.elements.mail_user, passEl = form.elements.mail_pass;

    let user = userEl.value.trim().toLowerCase();
    if (_mailDomain && user.endsWith('@' + _mailDomain)) user = user.slice(0, -(_mailDomain.length + 1));
    if (!user) { showToast('Enter a username.', 'error'); userEl.focus(); return; }
    if (user.includes('@')) { showToast('Enter just the part before the @.', 'error'); userEl.focus(); return; }
    if (passEl.value.length < 6) { showToast('Password must be at least 6 characters.', 'error'); passEl.focus(); return; }

    const label = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Adding\u2026';
    try {
        const res = await fetch('/api/manage/mail', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'add_user', mail_user: user, mail_pass: passEl.value })
        });
        const text = await res.text();
        if (mailParseResult(text).success) {
            showToast('Created ' + (_mailDomain ? user + '@' + _mailDomain : user), 'success');
            form.reset();
            await loadMailUsers();
            userEl.focus();
        } else {
            showAlert('Could not add the account', escapeHtml(mailErrorFrom(text)));
        }
    } catch (e) {
        showAlert('Request failed', 'Network or server error: ' + escapeHtml(e.message));
    } finally {
        btn.disabled = false;
        btn.textContent = label;
    }
}

async function deleteMailUser(user) {
    const email = _mailDomain ? user + '@' + _mailDomain : user;
    const ok = await showConfirm(
        'Delete mail account',
        'Delete <strong>' + escapeHtml(email) + '</strong> and all of its mail? This can\u2019t be undone.'
    );
    if (!ok) return;
    try {
        const res = await fetch('/api/manage/mail', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'delete_user', mail_user: user })
        });
        const text = await res.text();
        if (mailParseResult(text).success) {
            showToast('Deleted ' + email, 'success');
            loadMailUsers();
        } else {
            showAlert('Could not delete the account', escapeHtml(mailErrorFrom(text)));
        }
    } catch (e) {
        showAlert('Request failed', 'Network or server error: ' + escapeHtml(e.message));
    }
}

// "Check mail" / "Repair mail": stream the server's report into the output terminal.
// (submitCustomAction needs its button inside a form, so these use a small hidden one.)
function runMailTool(btn, endpoint) {
    const form = document.getElementById('mail-tool-form');
    const buttons = document.querySelectorAll('.mail-tools-actions .btn-sm');
    buttons.forEach(b => { b.disabled = true; });
    btn.classList.add('is-busy');
    return submitApiForm({ preventDefault() {}, target: form }, endpoint, 'services-term').finally(() => {
        buttons.forEach(b => { b.disabled = false; });
        btn.classList.remove('is-busy');
    });
}

async function submitModifySite(event, actionType) {
    event.preventDefault();
    const form = event.target;
    const btn = form.querySelector('button[type="submit"]');
    const originalText = btn.innerText;
    btn.innerText = 'Saving...';
    btn.disabled = true;

    const formData = new FormData(form);
    const payload = {action: actionType};
    for (let [key, value] of formData.entries()) {
        payload[key] = value;
    }

    try {
        await siteAction(payload);
    } finally {
        btn.innerText = originalText;
        btn.disabled = false;
        closeModifyModal();
    }
}

// --- VHost Pseudo-Editor Syntax Highlighting ---
function escapeHtml(text) {
    return text.replace(/&/g, '&amp;')
               .replace(/</g, '&lt;')
               .replace(/>/g, '&gt;');
}

function updateHighlighting(text) {
    let html = escapeHtml(text || '');
    
    // Nginx / Apache highlighting rules
    
    // 1. Comments
    html = html.replace(/(#.*)/g, '<span class="hl-comment">$1</span>');
    
    // 2. Directives (e.g., ServerName, listen, root)
    html = html.replace(/^([ \t]*)([a-zA-Z_]+)(?=\s)/gm, '$1<span class="hl-directive">$2</span>');
    
    // 3. Keywords/Blocks (e.g., server {, <VirtualHost>)
    html = html.replace(/(server|location|upstream|http|events)\b(?=\s*\{)/g, '<span class="hl-keyword">$1</span>');
    html = html.replace(/&lt;(\/?(?:VirtualHost|Directory|Files|Location)[^&]*)&gt;/gi, '<span class="hl-keyword">&lt;$1&gt;</span>');
    
    // 4. Variables (e.g., $host, %{HTTP_HOST})
    html = html.replace(/(\$[\w_]+)/g, '<span class="hl-variable">$1</span>');
    html = html.replace(/(%\{[^}]+\})/g, '<span class="hl-variable">$1</span>');

    // 5. Strings
    html = html.replace(/(".*?")/g, '<span class="hl-string">$1</span>');
    html = html.replace(/('.*?')/g, '<span class="hl-string">$1</span>');
    
    // 6. Values (numbers, paths, ips) - simplified catch-all for remaining tokens
    // html = html.replace(/([ \t]+)([a-zA-Z0-9_\-\.\/:]+)(?=;|\s|$)/gm, '$1<span class="hl-value">$2</span>');

    const codeEl = document.getElementById('mod-vhost-highlight');
    if (codeEl) {
        codeEl.innerHTML = html + '\n';
    }
}

function syncScroll(el) {
    const pre = el.nextElementSibling;
    if (pre && pre.classList.contains('editor-pre')) {
        pre.scrollTop = el.scrollTop;
        pre.scrollLeft = el.scrollLeft;
    }
}

// Drop zone setup
function setupDropZone(dropZoneId, inputPathId) {
    const dropZone = document.getElementById(dropZoneId);
    const inputPath = document.getElementById(inputPathId);
    
    if (!dropZone || !inputPath) return;

    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }

    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => dropZone.classList.add('drag-active'), false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => dropZone.classList.remove('drag-active'), false);
    });

    dropZone.addEventListener('drop', handleDrop, false);

    async function handleDrop(e) {
        const dt = e.dataTransfer;
        const file = dt.files[0];
        
        if (!file) return;
        
        if (!file.name.endsWith('.zip')) {
            showAlert('Invalid File', 'Please drop a <strong>.zip</strong> file containing your site files.');
            return;
        }

        const originalHtml = dropZone.innerHTML;
        dropZone.innerHTML = 'Uploading ' + file.name + '... <div class="btn-spinner" style="display:inline-block;border-color:var(--accent);border-right-color:transparent;width:14px;height:14px;margin-left:8px;"></div>';
        
        try {
            const response = await fetch('/api/upload_site?filename=' + encodeURIComponent(file.name), {
                method: 'POST',
                body: file
            });
            const result = await response.json();
            if (result.success) {
                dropZone.innerHTML = '<span style="color:var(--success)">&#10003; Uploaded successfully!</span><br><span style="font-size:0.8rem;opacity:0.7">Path: ' + result.path + '</span>';
                inputPath.value = result.path;
                setTimeout(() => { dropZone.innerHTML = originalHtml; }, 5000);
            } else {
                dropZone.innerHTML = '<span style="color:var(--danger)">Upload failed: ' + result.error + '</span>';
                setTimeout(() => { dropZone.innerHTML = originalHtml; }, 5000);
            }
        } catch (err) {
            dropZone.innerHTML = '<span style="color:var(--danger)">Upload error: ' + err.message + '</span>';
            setTimeout(() => { dropZone.innerHTML = originalHtml; }, 5000);
        }
    }
}

document.addEventListener("DOMContentLoaded", () => {
    // Sidebar logic
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) {
        const savedState = localStorage.getItem('sidebar-collapsed');
        let collapsed = false;
        if (savedState === 'true') {
            collapsed = true;
        } else if (savedState !== 'false' && window.innerWidth < 1024) {
            collapsed = true;
        }
        if (collapsed) {
            sidebar.classList.add('collapsed');
            // Already collapsed on load: allow tooltips immediately
            sidebar.style.overflow = 'visible';
        }

        // Sync UI state
        const syncSidebarUI = () => {
            const isCollapsed = sidebar.classList.contains('collapsed');
            const toggleBtn = document.getElementById('sidebar-toggle-btn');
            const expandBtn = document.getElementById('top-bar-expand-btn');
            const expandDivider = document.getElementById('top-bar-expand-divider');
            if (toggleBtn) toggleBtn.setAttribute('aria-expanded', String(!isCollapsed));
            if (expandBtn) {
                expandBtn.classList.toggle('hidden', !isCollapsed);
                if (expandDivider) expandDivider.style.display = isCollapsed ? '' : 'none';
            }
        };
        syncSidebarUI();

        window.addEventListener('resize', () => {
            if (window.innerWidth < 1024 && !localStorage.getItem('sidebar-collapsed')) {
                sidebar.classList.add('collapsed');
                sidebar.style.overflow = 'hidden';
                sidebar.addEventListener('transitionend', function onResizeEnd(e) {
                    if (e.propertyName === 'width') {
                        sidebar.style.overflow = 'visible';
                        sidebar.removeEventListener('transitionend', onResizeEnd);
                    }
                });
                syncSidebarUI();
            }
        });
    }

    setupDropZone('lamp-dropzone', 'lamp-sitepath-input');
    setupDropZone('lemp-dropzone', 'lemp-sitepath-input');
    setupDropZone('static-dropzone', 'static-sitepath-input');

    // Dynamic Form Placeholders for Domain inputs + live validation
    const forms = document.querySelectorAll('#page-makesite form');
    forms.forEach(form => {
        const domainInput = form.querySelector('input[name="domain"]');
        if (domainInput) {
            domainInput.addEventListener('input', (e) => {
                let d = e.target.value.trim().toLowerCase();
                let prefix = d.split('.')[0];
                if (!prefix) prefix = 'mysite';
                
                const dbName = form.querySelector('input[name="db_name"]');
                if (dbName) dbName.placeholder = prefix + '_db';
                
                const dbUser = form.querySelector('input[name="db_user"]');
                if (dbUser) dbUser.placeholder = prefix + '_usr';
                
                const docRoot = form.querySelector('input[name="docroot"]');
                if (docRoot && d) docRoot.placeholder = '/var/www/' + d;
                else if(docRoot) docRoot.placeholder = '/var/www/mysite';

                // Real-time domain validation
                const group = domainInput.closest('.form-group');
                if (d.length === 0) {
                    group.classList.remove('field-valid', 'field-invalid');
                } else if (/^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/.test(d)) {
                    group.classList.add('field-valid');
                    group.classList.remove('field-invalid');
                } else {
                    group.classList.add('field-invalid');
                    group.classList.remove('field-valid');
                }
            });
        }
    });

    // Terminal toolbar: clear and copy buttons
    document.querySelectorAll('.term-toolbar-clear').forEach(btn => {
        btn.addEventListener('click', () => {
            const termId = btn.getAttribute('data-term');
            const term = document.getElementById(termId);
            if (term) {
                term.innerHTML = '';
                term.classList.remove('visible');
            }
        });
    });
    document.querySelectorAll('.term-toolbar-copy').forEach(btn => {
        btn.addEventListener('click', () => {
            const termId = btn.getAttribute('data-term');
            const term = document.getElementById(termId);
            if (term) {
                const text = term.innerText || term.textContent;
                navigator.clipboard.writeText(text).then(() => {
                    const orig = btn.innerHTML;
                    btn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg> Copied';
                    setTimeout(() => { btn.innerHTML = orig; }, 1500);
                }).catch(() => {});
            }
        });
    });

    // Set initial breadcrumb
    updateBreadcrumb('Dashboard');
});

// Sidebar Toggle
function toggleSidebar() {
    const sidebar = document.querySelector('.sidebar');
    const toggleBtn = document.getElementById('sidebar-toggle-btn');
    const expandBtn = document.getElementById('top-bar-expand-btn');
    const expandDivider = document.getElementById('top-bar-expand-divider');
    if (sidebar) {
        const isCollapsed = sidebar.classList.toggle('collapsed');
        localStorage.setItem('sidebar-collapsed', isCollapsed);
        if (toggleBtn) toggleBtn.setAttribute('aria-expanded', String(!isCollapsed));
        if (expandBtn) {
            expandBtn.classList.toggle('hidden', !isCollapsed);
            if (expandDivider) expandDivider.style.display = isCollapsed ? '' : 'none';
        }

        // During the transition: clip content so nothing bleeds outside the sidebar.
        // After collapsing finishes: switch to overflow:visible so tooltips can escape.
        // After expanding finishes: switch back to hidden to clip properly.
        sidebar.style.overflow = 'hidden';
        sidebar.addEventListener('transitionend', function onEnd(e) {
            if (e.propertyName === 'width') {
                sidebar.style.overflow = isCollapsed ? 'visible' : 'hidden';
                sidebar.removeEventListener('transitionend', onEnd);
            }
        });
    }
}

// Keyboard Shortcuts functions
function openKbdOverlay() {
    const el = document.getElementById('kbd-overlay');
    if(el) el.classList.add('open');
}

function closeKbdOverlay() {
    const el = document.getElementById('kbd-overlay');
    if(el) el.classList.remove('open');
}

// Global keyboard shortcuts
document.addEventListener('keydown', (e) => {
    // Only capture if not in an input or textarea
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
    
    // Help modal
    if (e.key === '?') { openKbdOverlay(); }
    if (e.key === 'Escape') { 
        closeKbdOverlay(); 
        closeModifyModal();
        if (typeof closeVideo === 'function') closeVideo();
    }

    // Action 'r' - Reload All
    if ((e.key === 'r' || e.key === 'R') && !e.ctrlKey && !e.altKey && !e.metaKey && !window._kbdSeq) {
        topBarReloadAll();
    }
    
    // Sequence 'g' + something
    if (e.key === 'g' && !e.ctrlKey && !e.altKey && !e.metaKey) {
        window._kbdTimer = setTimeout(() => { window._kbdSeq = false; }, 1000);
        window._kbdSeq = true;
    } else if (window._kbdSeq) {
        clearTimeout(window._kbdTimer);
        window._kbdSeq = false;
        const pageMap = {
            'd': 'dashboard',
            'm': 'managesites',
            's': 'makesite',
            'z': 'dns',
            'b': 'mariadb',
            'i': 'services',
            'v': 'server',
            'n': 'network',
            'k': 'docker',
            'h': 'help'
        };
        if (pageMap[e.key]) switchPage(pageMap[e.key]);
    }
});

async function loadDocker() {
    const installCard  = document.getElementById('docker-install-card');
    const mainContent  = document.getElementById('docker-main-content');
    const tbody        = document.getElementById('docker-tbody');
    const dockerValEl  = document.getElementById('docker-val');

    // Show a loading state while we query the server
    if (installCard)  installCard.style.display  = 'none';
    if (mainContent)  mainContent.style.display  = 'none';

    let isDockerInstalled = false;
    try {
        const statusRes = await fetch('/api/status');
        const statusData = await statusRes.json();
        isDockerInstalled = !!statusData.docker_installed;

        // Update the dashboard badge too if visible
        if (dockerValEl) {
            dockerValEl.innerText   = isDockerInstalled ? 'INSTALLED' : 'NOT INSTALLED';
            dockerValEl.className   = isDockerInstalled ? 'badge badge-lg active' : 'badge badge-lg warning';
        }
    } catch (e) {
        // Server offline — show install card as fallback
    }

    if (!isDockerInstalled) {
        if (installCard) installCard.style.display = 'block';
        if (mainContent) mainContent.style.display = 'none';
        return;
    }

    // Docker is installed — show management panel
    if (installCard) installCard.style.display = 'none';
    if (mainContent) mainContent.style.display = 'block';

    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="4" class="text-center" style="padding:20px"><div class="btn-spinner"></div> Loading containers...</td></tr>';

    try {
        const res = await fetch('/api/docker_containers');
        const containers = await res.json();

        tbody.innerHTML = '';
        if (containers.length === 0) {
            tbody.innerHTML = '<tr><td colspan="4" class="text-center" style="padding:20px; color:var(--text-muted)">No containers found.</td></tr>';
            return;
        }

        containers.forEach(c => {
            const tr = document.createElement('tr');
            const isUp = c.status.toLowerCase().startsWith('up');
            const statusBadge = `<span class="badge ${isUp ? 'success' : 'danger'}">${escapeHtml(c.status)}</span>`;
            tr.innerHTML = `
                <td style="font-weight:500">${escapeHtml(c.name)}</td>
                <td style="color:var(--text-muted)">${escapeHtml(c.image)}</td>
                <td>${statusBadge}</td>
                <td style="text-align:right">
                    ${isUp ?
                        `<button class="btn-sm" onclick="dockerAction('stop', '${c.name}')">Stop</button>
                         <button class="btn-sm" onclick="dockerAction('restart', '${c.name}')">Restart</button>` :
                        `<button class="btn-sm" onclick="dockerAction('start', '${c.name}')">Start</button>`
                    }
                    <button class="btn-sm danger" style="margin-left:8px" onclick="dockerAction('rm', '${c.name}')">Delete</button>
                </td>
            `;
            tbody.appendChild(tr);
        });
    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="4" class="text-center" style="padding:20px; color:var(--danger)">Error loading containers</td></tr>`;
    }
}

async function dockerAction(action, target) {
    if (action === 'rm') {
        const confirmed = await showConfirm(
            'Delete Container', 
            `Are you sure you want to forcefully remove container <strong>${escapeHtml(target)}</strong>?`, 
            'Delete'
        );
        if (!confirmed) return;
    }
    
    const form = document.createElement('form');
    form.innerHTML = `
        <input type="hidden" name="action" value="${action}">
        <input type="hidden" name="target" value="${target}">
    `;
    
    const event = {
        preventDefault: () => {},
        target: form
    };
    
    // Add a fake submit button for the generic submit handler
    const btn = document.createElement('button');
    btn.type = 'submit';
    form.appendChild(btn);
    
    await submitApiForm(event, '/api/manage/docker', 'term-docker-main');
    setTimeout(loadDocker, 1000);
}

// Video overlay controls
function openVideo() {
    const overlay = document.getElementById('video-overlay');
    const video = document.getElementById('tutorial-video');
    if (!overlay || !video) return;
    overlay.classList.add('open');
    video.play().catch(() => {});
}

function closeVideo() {
    const overlay = document.getElementById('video-overlay');
    const video = document.getElementById('tutorial-video');
    if (!overlay || !video) return;
    overlay.classList.remove('open');
    video.pause();
    video.currentTime = 0;
}
