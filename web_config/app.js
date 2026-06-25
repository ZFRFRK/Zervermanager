// Page Navigation
function switchPage(pageId) {
    // Hide all pages
    document.querySelectorAll('.page-section').forEach(p => p.classList.remove('active'));
    // Deactivate all nav items
    document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
    
    // Show selected page
    document.getElementById('page-' + pageId).classList.add('active');
    // Activate nav item
    document.querySelector('[data-page="' + pageId + '"]').classList.add('active');
    
    // Load data for specific pages
    if (pageId === 'managesites') {
        loadSites();
    } else if (pageId === 'dns') {
        loadDNS();
    } else if (pageId === 'services') {
        loadMailUsers();
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

    const originalText = btn.innerText;
    btn.innerText = "Executing...";
    btn.disabled = true;
    term.classList.add('visible');
    
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
        btn.innerText = originalText;
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
            if (s === 'running') {
                el.innerText = 'RUNNING';
                el.className = 'badge active';
            } else if (s === 'stopped') {
                el.innerText = 'STOPPED';
                el.className = 'badge inactive';
            } else if (s.includes('not installed') || s.includes('not found')) {
                el.innerText = 'NOT INSTALLED';
                el.className = 'badge warning';
            } else {
                el.innerText = status.toUpperCase();
                el.className = 'badge';
            }
        };

        updateBadge('apache2-val', data.apache2 || 'Unknown');
        updateBadge('nginx-val', data.nginx || 'Unknown');
        updateBadge('bind9-val', data.bind9 || 'Unknown');
        updateBadge('mariadb-val', data.mariadb || 'Unknown');
        updateBadge('postfix-val', data.postfix || 'Unknown');
        updateBadge('dovecot-val', data.dovecot || 'Unknown');
        updateBadge('ufw-val', data.ufw || 'Unknown');
        
    } catch (e) {
        console.error("Status fetch failed:", e);
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
            tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:20px">No sites found.</td></tr>';
            return;
        }

        let rows = '';
        for (let i = 0; i < sites.length; i++) {
            const s = sites[i];
            const statusClass = s.enabled ? 'active' : 'inactive';
            const statusText = s.enabled ? 'Enabled' : 'Disabled';
            const toggleAction = s.enabled ? 'disable' : 'enable';
            const toggleText = s.enabled ? 'Disable' : 'Enable';
            const d = s.domain;
            const sv = s.server;
            const tp = s.type;
            const dr = s.docroot;

            rows += '<tr>';
            rows += '<td style="font-weight:500">' + d + '</td>';
            rows += '<td><span class="badge" style="background:var(--bg-lighter)">' + sv + '</span></td>';
            rows += '<td><span class="badge" style="background:var(--bg-lighter)">' + tp + '</span></td>';
            rows += '<td><span class="badge ' + statusClass + '">' + statusText + '</span></td>';
            rows += '<td style="display:flex;gap:6px;flex-wrap:wrap">';
            rows += '<button class="btn btn-primary" style="padding:4px 10px;font-size:0.75rem" ';
            rows += 'onclick="toggleSite(\'' + sv + '\',\'' + d + '\',\'' + toggleAction + '\')">' + toggleText + '</button>';
            rows += '<button class="btn btn-primary" style="padding:4px 10px;font-size:0.75rem;background:var(--bg-lighter);color:var(--text-color)" ';
            rows += 'onclick="openModifyModal(\'' + sv + '\',\'' + d + '\',\'' + tp + '\',\'' + dr.replace(/\\/g, '\\\\').replace(/'/g, "\\'") + '\')">Modify</button>';
            rows += '<button class="btn btn-primary" style="padding:4px 10px;font-size:0.75rem;background:var(--danger);color:#fff" ';
            rows += 'onclick="deleteSite(\'' + sv + '\',\'' + d + '\')">Delete</button>';
            rows += '</td></tr>';
        }
        tbody.innerHTML = rows;

    } catch (e) {
        console.error('loadSites error:', e);
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--danger);padding:20px">Error loading sites: ' + e.message + '</td></tr>';
    }
}

async function siteAction(payload, confirmMsg) {
    if (confirmMsg && !confirm(confirmMsg)) return null;

    try {
        const res = await fetch('/api/manage/site_action', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        if (result.success) {
            if (payload.action !== 'get_vhost') {
                alert(result.message);
                loadSites();
            }
            return result;
        } else {
            alert('Error: ' + result.message);
        }
    } catch (e) {
        alert('Request failed: ' + e);
    }
    return null;
}

function toggleSite(server, domain, action) {
    siteAction({action: action, server: server, domain: domain});
}

function deleteSite(server, domain) {
    if (!confirm('Are you sure you want to completely delete ' + domain + '?')) return;
    const removeDocroot = confirm('Also delete the document root (website files) for ' + domain + '?');
    siteAction({action: 'delete', server: server, domain: domain, remove_docroot: removeDocroot});
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
    siteAction({action: 'get_vhost', server: server, domain: domain}).then(function(res) {
        if (res && res.success) {
            document.getElementById('mod-vhost-content').value = res.message;
        } else {
            document.getElementById('mod-vhost-content').value = 'Failed to load configuration.';
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
            tbody.innerHTML = '<tr><td colspan="3" style="text-align:center;padding:32px;color:var(--text-muted)">No DNS zones found.</td></tr>';
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
    if (!confirm('Are you sure you want to delete the ' + type + ' zone for ' + domain + '?')) return;
    
    try {
        const res = await fetch('/api/manage/dns', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'delete', domain: domain, type: type })
        });
        const text = await res.text();
        const parts = text.split('\\n===RESULT===\\n');
        const jsonStr = parts.length > 1 ? parts[1] : parts[0];
        const result = JSON.parse(jsonStr);
        
        if (result.success || result.status === 'success') {
            loadDNS();
        } else {
            alert('Error deleting zone');
        }
    } catch (e) {
        alert('Request failed');
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
    if (!confirm('Delete mail user ' + email + '?')) return;
    try {
        const res = await fetch('/api/manage/mail', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'delete_user', mail_user: email })
        });
        const text = await res.text();
        const parts = text.split('\\n===RESULT===\\n');
        const jsonStr = parts.length > 1 ? parts[1] : parts[0];
        const result = JSON.parse(jsonStr);
        
        if (result.success || result.status === 'success') {
            loadMailUsers();
        } else {
            alert('Error deleting user');
        }
    } catch (e) {
        alert('Request failed');
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
    }
}
