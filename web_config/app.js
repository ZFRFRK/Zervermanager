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

// Page Navigation
function switchPage(pageId) {
    const current = document.querySelector('.page-section.active');
    
    const finishSwitch = () => {
        // Hide all pages
        document.querySelectorAll('.page-section').forEach(p => {
            p.classList.remove('active');
            p.style.animation = ''; // clear pageOut
        });
        // Deactivate all nav items
        document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
        
        // Show selected page
        document.getElementById('page-' + pageId).classList.add('active');
        // Activate nav item
        const navItem = document.querySelector('[data-page="' + pageId + '"]');
        if (navItem) navItem.classList.add('active');
        
        // Load data for specific pages
        if (pageId === 'managesites') {
            loadSites();
        } else if (pageId === 'dns') {
            loadDNS();
        } else if (pageId === 'services') {
            loadMailUsers();
        }
    };

    if (current && current.id !== 'page-' + pageId && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        current.style.animation = 'pageOut 0.15s cubic-bezier(0.16, 1, 0.3, 1) forwards';
        setTimeout(finishSwitch, 140);
    } else {
        finishSwitch();
    }
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
    page.querySelector('[data-tab="' + tabId + '"]').classList.add('active');
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
    }
}

// Status Polling
async function fetchStatus() {
    try {
        const res = await fetch('/api/status');
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

        // Update Top Bar
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
        
    } catch (e) {
        console.error("Status fetch failed:", e);
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
        console.error('loadSites error:', e);
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--danger);padding:20px">Error loading sites: ' + e.message + '</td></tr>';
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
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center;padding:32px;color:var(--danger)">Error loading DNS zones.</td></tr>';
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

// --- Mail Users ---
async function loadMailUsers() {
    const tbody = document.getElementById('mail-users-tbody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="2" style="text-align:center;padding:16px;color:var(--text-muted)">Loading...</td></tr>';
    
    try {
        const res = await fetch('/api/manage/mail?action=list');
        const data = await res.json();
        
        if (!data || data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="2" style="text-align:center;padding:16px;color:var(--text-muted)">No mail users found.</td></tr>';
            return;
        }
        
        let rows = '';
        for (let i = 0; i < data.length; i++) {
            const user = data[i];
            rows += '<tr>';
            rows += '<td><span class="site-domain">' + user + '</span></td>';
            rows += '<td class="actions-cell">';
            rows += '<button class="btn-sm btn-delete" onclick="deleteMailUser(\'' + user + '\')">Delete</button>';
            rows += '</td>';
            rows += '</tr>';
        }
        tbody.innerHTML = rows;
    } catch (e) {
        tbody.innerHTML = '<tr><td colspan="2" style="text-align:center;padding:16px;color:var(--text-muted)">Mail server not installed or accessible.</td></tr>';
    }
}

async function deleteMailUser(email) {
    const ok = await showConfirm(
        'Delete Mail User',
        'Are you sure you want to permanently delete the mail account <strong>' + email + '</strong>?'
    );
    if (!ok) return;
    try {
        const res = await fetch('/api/manage/mail', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'delete_user', mail_user: email })
        });
        const text = await res.text();
        const parts = text.split('\n===RESULT===\n');
        const jsonStr = parts.length > 1 ? parts[1] : parts[0];
        const result = JSON.parse(jsonStr);
        
        if (result.success || result.status === 'success') {
            loadMailUsers();
            showToast('Mail user deleted.', 'success');
        } else {
            showAlert('Delete Failed', 'Could not delete the mail user. Please check the server logs.');
        }
    } catch (e) {
        showAlert('Request Failed', 'Network or server error: ' + e.message);
    }
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
    setupDropZone('lamp-dropzone', 'lamp-sitepath-input');
    setupDropZone('lemp-dropzone', 'lemp-sitepath-input');
    setupDropZone('static-dropzone', 'static-sitepath-input');
});
