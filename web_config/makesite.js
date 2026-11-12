/* Zervermanager — makesite.js
   UX layer for the Create site page: live plan, validation, password tools, port chips, remembered values.
   Reads/writes form fields only; submit handlers and endpoints are untouched. */
(() => {
    'use strict';
    const $ = (s, r = document) => r.querySelector(s);
    const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
    const page = $('#page-makesite');
    if (!page) return;
    const safe = (fn) => { try { fn(); } catch (e) { console.warn('[makesite]', e); } };
    const fire = (el, type) => el.dispatchEvent(new Event(type, { bubbles: true }));

    const active = () => {
        const b = $('.content-tab-btn.active', page);
        const tab = b ? b.dataset.tab : 'lamp';
        const pane = document.getElementById('tab-makesite-' + tab);
        return { tab, pane, form: pane ? $('form', pane) : null };
    };
    const val = (f, n) => { const e = f.elements[n]; return e ? String(e.value || '').trim() : ''; };
    const sel = (f, n) => { const e = f.elements[n]; return e && e.selectedOptions && e.selectedOptions[0] ? e.selectedOptions[0].textContent.trim() : ''; };

    /* ── Validation (warns, never blocks) ───────────────────────────── */
    const V = {
        domain: (v) => !v ? null : /^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$/.test(v) ? ['ok', 'Looks good'] : ['warn', 'Use a full domain, like example.com'],
        ip: (v) => !v ? null : /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/.test(v) ? ['ok', ''] : ['warn', 'Not a valid IPv4 address'],
        backend: (v) => !v ? null : /^https?:\/\/[^\s/:]+(:\d{1,5})?(\/\S*)?$/.test(v) ? ['ok', ''] : ['warn', 'Use a URL like http://127.0.0.1:3000'],
    };
    const cleanDomain = (s) => s.trim().toLowerCase().replace(/^[a-z]+:\/\//, '').replace(/[\/?#].*$/, '').replace(/\s+/g, '');
    const check = (input) => {
        const fn = V[input.name]; if (!fn) return;
        const grp = input.closest('.form-group'); if (!grp) return;
        let msg = $('.ms-msg', grp);
        if (!msg) { msg = document.createElement('div'); msg.className = 'ms-msg'; grp.appendChild(msg); }
        const r = fn(input.value.trim().toLowerCase() === input.value.trim() || input.name !== 'domain' ? input.value.trim() : input.value.trim().toLowerCase());
        if (!r) { delete grp.dataset.state; msg.textContent = ''; return; }
        grp.dataset.state = r[0]; msg.textContent = r[1];
    };

    /* ── Plan card ──────────────────────────────────────────────────── */
    const plan = document.getElementById('ms-plan');
    let lastKey = '', last = {};
    const rowsFor = (tab, f) => {
        const d = val(f, 'domain').toLowerCase();
        const pre = (d.split('.')[0] || '').replace(/[^a-z0-9_]/g, '_');
        const R = [['Domain', d || null, 1]];
        R.push(['Web server', tab === 'lamp' ? 'Apache' : tab === 'lemp' ? 'Nginx' : (sel(f, 'server') || null)]);
        if (tab === 'proxy') R.push(['Backend', val(f, 'backend') || null, 1]);
        else R.push(['Doc root', val(f, 'docroot') || (d ? '/var/www/' + d : null), 1]);
        if (tab === 'lamp' || tab === 'lemp') {
            R.push(['Database', val(f, 'db_name') || pre || null, 1]);
            R.push(['DB user', val(f, 'db_user') || (pre ? pre + '_user' : null), 1]);
        }
        if (tab === 'wordpress') R.push(['Database', 'Created automatically']);
        if (f.elements.ssl) R.push(['SSL', sel(f, 'ssl') || null]);
        const ip = val(f, 'ip'); if (f.elements.ip) R.push(['Server IP', ip || 'Current server']);
        const uf = f.elements.use_site_files; if (uf && uf.checked) R.push(['Site files', val(f, 'site_files_path') || 'Existing files', 1]);
        const sq = f.elements.import_sql; if (sq && sq.checked) R.push(['SQL import', val(f, 'sql_path') || 'Dump file', 1]);
        return R;
    };
    const render = () => safe(() => {
        const { tab, form } = active();
        if (!form) return;
        const req = $$('[required]', form).filter(e => !e.closest('.hidden'));
        const done = req.filter(e => String(e.value).trim()).length;
        const ready = done === req.length;
        form.classList.toggle('is-ready', ready); form.classList.toggle('is-incomplete', !ready);
        updateSummaries(form);
        if (!plan) return;
        const R = rowsFor(tab, form);
        const fresh = lastKey !== tab; lastKey = tab;
        plan.innerHTML = '';
        const head = document.createElement('div'); head.className = 'ms-plan-head';
        const t = document.createElement('span'); t.className = 'ms-eyebrow'; t.textContent = 'Plan';
        const rd = document.createElement('span'); rd.className = 'ms-ready' + (ready ? ' is-ready' : '');
        rd.textContent = ready ? 'Ready to create' : `${req.length - done} required left`;
        head.append(t, rd);
        const meter = document.createElement('div'); meter.className = 'ms-meter';
        const bar = document.createElement('i'); bar.style.width = (req.length ? Math.round(done / req.length * 100) : 100) + '%'; meter.appendChild(bar);
        const ul = document.createElement('ul'); ul.className = 'ms-rows';
        const now = {};
        R.forEach(([k, v, mono]) => {
            const li = document.createElement('li');
            const kk = document.createElement('span'); kk.className = 'k'; kk.textContent = k;
            const vv = document.createElement('span'); vv.className = 'v' + (mono ? ' is-mono' : ''); vv.textContent = v || '—'; vv.title = v || '';
            if (!v) li.className = 'is-empty';
            now[k] = v || '';
            if (!fresh && last[k] !== undefined && last[k] !== now[k]) li.classList.add('flash');
            li.append(kk, vv); ul.appendChild(li);
        });
        last = now;
        plan.append(head, meter, ul);
    });
    let q = 0; const schedule = () => { if (!q) q = requestAnimationFrame(() => { q = 0; render(); }); };

    /* ── Collapsed sections show their current value ───────────────── */
    const updateSummaries = (form) => {
        $$('details.form-details', form).forEach(det => {
            const s = $('summary', det); if (!s) return;
            let chip = $('.ms-sum', s);
            if (!chip) { chip = document.createElement('span'); chip.className = 'ms-sum'; s.appendChild(chip); }
            const title = (s.firstChild ? s.firstChild.textContent : '').trim().toLowerCase();
            let t = '';
            if (title.startsWith('database')) t = val(form, 'db_name') || 'auto names';
            else if (title.startsWith('ssl')) t = [sel(form, 'ssl'), val(form, 'ip')].filter(Boolean).join(' · ');
            else if (title.startsWith('advanced')) { const a = form.elements.use_site_files, b = form.elements.import_sql; t = (a && a.checked ? 'existing files' : '') + (b && b.checked ? (a && a.checked ? ' + SQL' : 'SQL import') : '') || 'defaults'; }
            chip.textContent = t; chip.hidden = !t;
        });
    };

    /* ── One-time enhancements ──────────────────────────────────────── */
    safe(() => {
        // hints: drop the leading dash, keep the full text as a tooltip
        $$('.label-hint', page).forEach(h => { const t = h.textContent.replace(/^\s*[—–-]\s*/, ''); h.title = t; h.textContent = t; });
        // domain: example placeholder
        $$('input[name="domain"]', page).forEach(i => { if (!i.placeholder) i.placeholder = 'example.com'; i.autocomplete = 'off'; i.spellcheck = false; });
    });

    // password tools: show, generate, strength
    safe(() => {
        $$('input[name="db_pass"]', page).forEach(inp => {
            if (inp.closest('.ms-field')) return;
            const wrap = document.createElement('div'); wrap.className = 'ms-field';
            inp.parentNode.insertBefore(wrap, inp); wrap.appendChild(inp);
            const tools = document.createElement('div'); tools.className = 'ms-tools';
            const mk = (label, svg) => { const b = document.createElement('button'); b.type = 'button'; b.className = 'ms-tool'; b.title = label; b.setAttribute('aria-label', label); b.innerHTML = svg; return b; };
            const eye = mk('Show password', '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>');
            const gen = mk('Generate a strong password', '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>');
            tools.append(eye, gen); wrap.appendChild(tools);
            const st = document.createElement('div'); st.className = 'ms-strength'; st.innerHTML = '<i></i>'; wrap.after(st);
            const score = (p) => { let s = 0; if (p.length >= 8) s++; if (p.length >= 14) s++; if (/[a-z]/.test(p) && /[A-Z]/.test(p)) s++; if (/\d/.test(p) && /[^\w]/.test(p)) s++; return p ? Math.max(1, s) : 0; };
            const paint = () => { const s = score(inp.value); st.dataset.level = s; st.firstChild.style.width = (s * 25) + '%'; };
            eye.addEventListener('click', () => { const show = inp.type === 'password'; inp.type = show ? 'text' : 'password'; eye.classList.toggle('on', show); eye.title = show ? 'Hide password' : 'Show password'; });
            gen.addEventListener('click', () => {
                const cs = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%^*-_';
                const a = new Uint32Array(20); (window.crypto || {}).getRandomValues ? crypto.getRandomValues(a) : a.forEach((_, i) => a[i] = Math.random() * 4e9);
                inp.value = Array.from(a, n => cs[n % cs.length]).join('');
                inp.type = 'text'; eye.classList.add('on'); fire(inp, 'input'); inp.focus({ preventScroll: true });
            });
            inp.addEventListener('input', paint); paint();
        });
    });

    // reverse proxy: port chips
    safe(() => {
        $$('input[name="backend"]', page).forEach(inp => {
            const row = document.createElement('div'); row.className = 'ms-chips';
            [['3000', 'Node / React'], ['5173', 'Vite'], ['8000', 'Django'], ['8080', 'Alt HTTP'], ['9000', 'PHP-FPM']].forEach(([p, n]) => {
                const b = document.createElement('button'); b.type = 'button'; b.className = 'ms-chip';
                b.innerHTML = `<b>:${p}</b><span>${n}</span>`;
                b.addEventListener('click', () => { inp.value = 'http://127.0.0.1:' + p; fire(inp, 'input'); inp.focus({ preventScroll: true }); });
                row.appendChild(b);
            });
            inp.closest('.form-group').appendChild(row);
        });
    });

    // remembered values (never domain, passwords or paths)
    const KEY = 'zm-ms-pref', PREF = ['ip', 'server', 'ssl', 'le_email'];
    const prefs = () => { try { return JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { return {}; } };
    safe(() => {
        setTimeout(() => safe(() => {
            const p = prefs();
            $$('form', page).forEach(f => PREF.forEach(n => {
                const e = f.elements[n];
                if (!e || p[n] == null || e.value === p[n]) return;
                if (e.tagName === 'SELECT' && !Array.from(e.options).some(o => o.value === p[n])) return;
                e.value = p[n]; fire(e, 'change'); fire(e, 'input');
            }));
            schedule();
        }), 800);
        page.addEventListener('change', (e) => {
            const t = e.target; if (!t.name || PREF.indexOf(t.name) < 0) return;
            const p = prefs(); p[t.name] = t.value;
            try { localStorage.setItem(KEY, JSON.stringify(p)); } catch (x) {}
        });
    });

    // events
    page.addEventListener('input', (e) => { if (e.target.name in V) check(e.target); schedule(); });
    page.addEventListener('change', (e) => {
        const t = e.target;
        if (t.name === 'domain') { const c = cleanDomain(t.value); if (c !== t.value) { t.value = c; fire(t, 'input'); } }
        schedule();
    });
    page.addEventListener('click', (e) => {
        if (e.target.closest('.content-tab-btn')) {
            schedule();
            setTimeout(() => { const f = active().form; const d = f && f.querySelector('[name="domain"]'); if (d && !d.value) d.focus({ preventScroll: true }); schedule(); }, 280);
        }
    });
    $$('.content-tab-btn', page).forEach(b => new MutationObserver(schedule).observe(b, { attributes: true, attributeFilter: ['class'] }));
    $$('details.form-details', page).forEach(d => d.addEventListener('toggle', schedule));

    // reset button inside each action bar (the bar is built by fx.js, so wait for it)
    const addReset = () => $$('.fx-actionbar', page).forEach(bar => {
        if (bar.querySelector('.ms-reset')) return;
        const b = document.createElement('button'); b.type = 'button'; b.className = 'btn btn-sm ms-reset'; b.textContent = 'Reset';
        b.addEventListener('click', () => {
            const f = bar.closest('form'); if (!f) return;
            f.reset();
            $$('select, input[type="checkbox"]', f).forEach(el => fire(el, 'change'));
            $$('.form-group', f).forEach(g => { delete g.dataset.state; const m = $('.ms-msg', g); if (m) m.textContent = ''; });
            $$('input[name="db_pass"]', f).forEach(i => { i.type = 'password'; fire(i, 'input'); });
            schedule();
        });
        bar.appendChild(b);
    });
    setTimeout(addReset, 700); setTimeout(addReset, 2000);
    render();
    setTimeout(render, 900);
})();