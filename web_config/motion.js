/* Zervermanager — motion.js
   Adds motion on top of app.js/fx.js. Reads state only; never changes app logic.
   Delete this file + its <script> tag + the "v14" block in style.css to roll back. */
(() => {
    'use strict';
    const reduce = () => document.documentElement.dataset.motion === 'reduce';
    /* Measured zoom: ratio of rendered to layout size, so it is right whichever way the browser reports rects. */
    const Z = () => { const b = document.body; return (b && b.offsetWidth) ? (b.getBoundingClientRect().width / b.offsetWidth) || 1 : 1; };
    const safe = (fn) => { try { fn(); } catch (e) { console.warn('[motion]', e); } };

    /* 0. Motion switch (top bar). Default: on. Choice is remembered. */
    safe(() => {
        const KEY = 'zm-motion';
        const get = () => { try { return localStorage.getItem(KEY); } catch (e) { return null; } };
        const root = document.documentElement;
        if (get() === 'reduce') root.dataset.motion = 'reduce';
        const anchor = document.getElementById('goldfish-btn') || document.querySelector('.top-bar-btn');
        if (!anchor || !anchor.parentElement) return;
        const b = document.createElement('button');
        b.type = 'button'; b.id = 'hy-motion-btn'; b.className = anchor.className.replace(/\bactive\b/, '').trim();
        const paint = () => {
            const on = root.dataset.motion !== 'reduce';
            b.setAttribute('aria-pressed', String(on));
            b.title = on ? 'Animations on — click to reduce motion' : 'Animations off — click to turn on';
            b.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 2L3 14h9l-1 8 10-12h-9z"/></svg><span>Motion</span>';
            b.querySelector('span').style.opacity = on ? 1 : .55;
        };
        b.addEventListener('click', () => {
            const off = root.dataset.motion !== 'reduce';
            if (off) root.dataset.motion = 'reduce'; else delete root.dataset.motion;
            try { localStorage.setItem(KEY, off ? 'reduce' : 'full'); } catch (e) {}
            paint();
        });
        paint();
        anchor.parentElement.insertBefore(b, anchor);
    });

    /* 1. Tab direction: new pane slides in from the side the tab sits on. */
    safe(() => {
        document.addEventListener('pointerdown', (e) => {
            const btn = e.target.closest && e.target.closest('.content-tab-btn');
            if (!btn) return;
            const tabs = Array.from(btn.parentElement.querySelectorAll('.content-tab-btn'));
            const cur = tabs.findIndex(b => b.classList.contains('active'));
            const next = tabs.indexOf(btn);
            if (cur !== -1 && next !== cur) document.documentElement.style.setProperty('--tab-dir', next > cur ? 1 : -1);
        }, true);
    });

    /* 2. Sliding indicator that follows the real position of the active item every frame.
          Replaces the CSS-transition glider: no overshoot, no snap-back, survives layout shifts. */
    safe(() => {
        const make = (container, itemSel, cls) => {
            if (!container) return;
            const p = document.createElement('span');
            p.className = 'hy-pill ' + cls; p.setAttribute('aria-hidden', 'true');
            container.prepend(p);
            container.classList.add('hy-has-pill');
            const s = { x: 0, y: 0, w: 0, h: 0, a: 0 };            // stage 1 (follows target)
            const d = { x: 0, y: 0, w: 0, h: 0, a: 0 };            // stage 2 (follows stage 1) → soft ease-in-out
            let t = null, raf = 0, until = 0, last = 0, placed = false;
            const measure = () => {
                const el = container.querySelector(itemSel + '.active');
                if (!el || !el.offsetWidth) return null;
                const cr = container.getBoundingClientRect(), r = el.getBoundingClientRect();
                const k = (container.offsetHeight ? cr.height / container.offsetHeight : 1) || 1;
                return {
                    x: (r.left - cr.left) / k - container.clientLeft + container.scrollLeft,
                    y: (r.top - cr.top) / k - container.clientTop + container.scrollTop,
                    w: r.width / k, h: r.height / k, a: 1
                };
            };
            const apply = () => {
                p.style.width = d.w + 'px'; p.style.height = d.h + 'px';
                p.style.transform = `translate3d(${d.x}px, ${d.y}px, 0)`; p.style.opacity = d.a;
            };
            const tick = (now) => {
                raf = 0;
                const dt = Math.min(0.05, (now - (last || now)) / 1000); last = now;
                t = measure();
                if (!t) { t = { ...s, a: 0 }; }
                if (!placed && t.a) { Object.assign(s, t); Object.assign(d, t); placed = true; }
                const k1 = 1 - Math.exp(-dt * 16), k2 = 1 - Math.exp(-dt * 13);
                let moving = false;
                for (const key of ['x', 'y', 'w', 'h', 'a']) {
                    s[key] += (t[key] - s[key]) * k1;
                    d[key] += (s[key] - d[key]) * k2;
                    if (Math.abs(t[key] - d[key]) > (key === 'a' ? 0.01 : 0.15)) moving = true;
                }
                if (!moving) { Object.assign(s, t); Object.assign(d, t); }
                apply();
                if (moving || now < until) raf = requestAnimationFrame(tick);
                else last = 0;
            };
            const kick = (ms = 800) => {
                until = performance.now() + ms;
                if (reduce()) { const m2 = measure(); if (m2) { Object.assign(s, m2); Object.assign(d, m2); placed = true; apply(); } return; }
                if (!raf) raf = requestAnimationFrame(tick);
            };
            const mo = new MutationObserver(() => kick());
            mo.observe(container, { attributes: true, attributeFilter: ['class'], subtree: true });
            if (typeof ResizeObserver !== 'undefined') new ResizeObserver(() => kick(300)).observe(container);
            container.addEventListener('transitionend', () => kick(200), true);
            addEventListener('resize', () => kick(300));
            const sb = container.closest('.sidebar');
            if (sb) new MutationObserver(() => kick(900)).observe(sb, { attributes: true, attributeFilter: ['class'] });
            if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => kick(300));
            kick(900);
        };
        make(document.querySelector('.nav-menu'), '.nav-item', 'hy-pill--nav');
        document.querySelectorAll('.content-tabs').forEach(c => make(c, '.content-tab-btn', 'hy-pill--tab'));
    });

    /* 3. Ripple on press for buttons, chips, tabs and nav rows. */
    safe(() => {
        const sel = '.btn, .btn-sm, .hy-key, .top-bar-btn, .content-tab-btn, .fx-select-opt';
        document.addEventListener('pointerdown', (e) => {
            if (reduce() || e.button > 0) return;
            const el = e.target.closest && e.target.closest(sel);
            if (!el || el.disabled) return;
            const r = el.getBoundingClientRect();
            const z = Z(); const d = Math.max(r.width, r.height) * 2 / z;
            const s = document.createElement('span');
            s.className = 'hy-ripple';
            s.style.cssText = `width:${d}px;height:${d}px;left:${(e.clientX - r.left) / z - d / 2}px;top:${(e.clientY - r.top) / z - d / 2}px`;
            el.appendChild(s);
            s.addEventListener('animationend', () => s.remove(), { once: true });
        }, true);
    });

    /* 4. Terminal: new output lines fade up as they arrive. */
    safe(() => {
        const t = document.getElementById('makesite-term');
        if (!t || reduce()) return;
        new MutationObserver((muts) => muts.forEach(m => m.addedNodes.forEach(n => {
            if (n.nodeType === 1) n.classList.add('hy-line');
        }))).observe(t, { childList: true });
    });

    /* ── UX layer ─────────────────────────────────────────────────────── */
    const $ = (s, r = document) => r.querySelector(s);
    const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
    const store = { get: (k) => { try { return localStorage.getItem(k); } catch (e) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} } };

    /* 5. Tab title shows where you are and how many services are down; nav marks the current page. */
    safe(() => {
        const base = 'Zervermanager';
        const svcIds = ['apache2', 'nginx', 'bind9', 'mariadb', 'postfix', 'dovecot', 'ufw'];
        const update = () => {
            const a = $('.nav-item.active');
            $$('.nav-item').forEach(n => n.removeAttribute('aria-current'));
            if (a) a.setAttribute('aria-current', 'page');
            const down = svcIds.filter(id => { const b = document.getElementById(id + '-val'); return b && b.classList.contains('inactive'); }).length;
            const name = a ? (a.dataset.title || a.textContent.trim()) : '';
            document.title = (down ? `(${down} down) ` : '') + (name ? `${name} · ` : '') + base;
            if (a && a.dataset.page) store.set('zm-last-page', a.dataset.page);
        };
        const mo = new MutationObserver(update);
        $$('.nav-item').forEach(n => mo.observe(n, { attributes: true, attributeFilter: ['class'] }));
        svcIds.forEach(id => { const b = document.getElementById(id + '-val'); if (b) mo.observe(b, { attributes: true, attributeFilter: ['class'] }); });
        update();
    });

    /* 6. Come back to the page and Make Site tab you left. */
    safe(() => {
        $$('.content-tab-btn').forEach(b => b.addEventListener('click', () => {
            if (b.dataset.tab && b.closest('#page-makesite')) store.set('zm-tab-makesite', b.dataset.tab);
        }));
        setTimeout(() => {
            const page = store.get('zm-last-page');
            const valid = page && $(`.nav-item[data-page="${page}"]`);
            const cur = $('.nav-item.active');
            if (valid && cur && cur.dataset.page !== page && typeof window.switchPage === 'function') window.switchPage(page);
        }, 450);
    });

    /* 6b. Restore the Make Site tab only once that page is actually showing (switching it earlier
           would change the breadcrumb of whatever page you're on). */
    safe(() => {
        const page = $('#page-makesite');
        if (!page) return;
        let done = false;
        const restore = () => {
            if (done || !page.classList.contains('active')) return;
            done = true;
            const tab = store.get('zm-tab-makesite');
            const btn = $('.content-tab-btn.active', page);
            if (tab && btn && btn.dataset.tab !== tab && $(`.content-tab-btn[data-tab="${tab}"]`, page) && typeof window.switchTab === 'function') window.switchTab('makesite', tab);
        };
        new MutationObserver(restore).observe(page, { attributes: true, attributeFilter: ['class'] });
        restore();
    });

    /* 6c. Document-root placeholder follows the domain you type. */
    safe(() => {
        $$('#page-makesite form').forEach(f => {
            const d = f.querySelector('[name="domain"]'), r = f.querySelector('[name="docroot"]');
            if (!d || !r) return;
            const base = r.getAttribute('placeholder') || '';
            const sync = () => { const v = d.value.trim().toLowerCase(); r.placeholder = v ? `Default: /var/www/${v}` : base; };
            d.addEventListener('input', sync);
        });
    });

    /* 7. Collapsed sidebar: a label appears next to the icon you're pointing at. */
    safe(() => {
        const tip = document.createElement('div');
        tip.className = 'hy-tip'; tip.setAttribute('role', 'tooltip');
        document.body.appendChild(tip);
        const show = (el) => {
            const sb = el.closest('.sidebar');
            if (!sb || !sb.classList.contains('collapsed')) return;
            tip.textContent = el.dataset.title || el.getAttribute('title') || el.textContent.trim();
            const r = el.getBoundingClientRect();
            tip.style.left = (r.right / Z() + 12) + 'px';
            tip.style.top = ((r.top + r.height / 2) / Z()) + 'px';
            tip.classList.add('show');
        };
        const hide = () => tip.classList.remove('show');
        document.addEventListener('pointerover', (e) => { const el = e.target.closest && e.target.closest('.sidebar .nav-item, .sidebar .sidebar-search'); if (el) show(el); else hide(); });
        document.addEventListener('focusin', (e) => { const el = e.target.closest && e.target.closest('.sidebar .nav-item'); if (el) show(el); });
        document.addEventListener('focusout', hide);
        document.addEventListener('pointerdown', hide, true);
    });

    /* 8. Ctrl/Cmd + Enter submits the form you're typing in. */
    safe(() => {
        document.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter' || !(e.ctrlKey || e.metaKey)) return;
            const f = e.target.closest && e.target.closest('form');
            if (f && typeof f.requestSubmit === 'function') { e.preventDefault(); f.requestSubmit(); }
        });
    });

    /* 9. Changing page starts you at the top. */
    safe(() => {
        document.addEventListener('click', (e) => {
            if (e.target.closest && e.target.closest('.nav-item')) { const m = $('.main-content'); if (m && m.scrollTo) m.scrollTo({ top: 0, behavior: reduce() ? 'auto' : 'smooth' }); }
        });
    });
})();

/* ── Section labels, Sites search/filter, avatars (formerly separate) ── */
/* Zervermanager — revamp.js (additive layer; loads last, replaces nothing)
   Section labels, Sites search/filter + avatars, phone button placement. */
(() => {
    'use strict';
    const $ = (s, r = document) => r.querySelector(s);
    const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
    const safe = (fn) => { try { fn(); } catch (e) { console.warn('[revamp]', e); } };
    const PALETTE = ['#00f5d4', '#8b5cf6', '#f38ba8', '#89b4fa', '#fab387', '#a6e3a1'];

    /* 1. Every page title gets the sidebar section as a small label above it (wayfinding). */
    safe(() => {
        /* Sidebar group names are wayfinding, not page names — override where they'd read wrong. */
        const EYEBROW = { dns: 'DNS' };
        $$('.page-section').forEach(sec => {
            const head = $('.page-header', sec); if (!head || $('.hy-eyebrow', head)) return;
            const page = sec.id.replace('page-', '');
            const nav = $(`.nav-item[data-page="${page}"]`); if (!nav) return;
            let el = nav.previousElementSibling, label = EYEBROW[page] || '';
            const group = nav.closest('.nav-section, .nav-group') || nav.parentElement;
            const t = group && group.querySelector('.nav-section-title');
            if (t && !label) label = t.textContent.trim();
            else { while (el && !label) { if (el.classList && el.classList.contains('nav-section-title')) label = el.textContent.trim(); el = el.previousElementSibling; } }
            if (!label) return;
            const e = document.createElement('div'); e.className = 'hy-eyebrow'; e.textContent = label;
            (head.querySelector('.page-header-text') || head).prepend(e);
        });
    });

    /* 3. Sites: search, status filter, counts, avatars, copy. */
    safe(() => {
        const page = $('#page-managesites'); const card = page && $('.card', page); const wrap = card && $('.sites-table-wrap', card);
        const tbody = $('#sites-tbody'); if (!wrap || !tbody) return;
        const bar = document.createElement('div'); bar.className = 'hy-toolbar';
        bar.innerHTML = `
            <label class="hy-search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg><input type="search" placeholder="Filter sites…" aria-label="Filter sites" autocomplete="off" spellcheck="false"></label>
            <div class="hy-seg" role="group" aria-label="Status filter">
                <button type="button" data-f="all" class="on">All <b>0</b></button>
                <button type="button" data-f="active">Enabled <b>0</b></button>
                <button type="button" data-f="inactive">Disabled <b>0</b></button>
            </div>`;
        card.insertBefore(bar, wrap);
        const input = $('input', bar), btns = $$('.hy-seg button', bar);
        let filter = 'all';
        const miss = document.createElement('tr'); miss.className = 'hy-miss'; miss.innerHTML = '<td colspan="5"><div class="hy-none">No sites match that filter.</div></td>';
        const dataRows = () => $$('tr', tbody).filter(r => !r.classList.contains('skeleton-row') && !r.classList.contains('hy-miss') && $('.status-text', r));
        const apply = () => {
            const rows = dataRows(), q = input.value.trim().toLowerCase();
            const cnt = { all: rows.length, active: 0, inactive: 0 };
            let shown = 0;
            rows.forEach(r => {
                const st = $('.status-text', r).classList.contains('active') ? 'active' : 'inactive';
                cnt[st]++;
                const ok = (filter === 'all' || filter === st) && (!q || r.textContent.toLowerCase().includes(q));
                r.classList.toggle('hy-hide', !ok); if (ok) shown++;
            });
            btns.forEach(b => { $('b', b).textContent = cnt[b.dataset.f]; });
            if (rows.length && !shown) { if (!miss.isConnected) tbody.appendChild(miss); } else miss.remove();
            bar.hidden = !rows.length && !input.value;
        };
        const enhance = () => dataRows().forEach(r => {
            const a = $('.site-domain-link', r); if (!a || $('.hy-av', r)) return;
            const d = (a.textContent || '').trim();
            let h = 0; for (const ch of d) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
            const av = document.createElement('span'); av.className = 'hy-av'; av.textContent = (d[0] || '?').toUpperCase(); av.style.setProperty('--c', PALETTE[h % PALETTE.length]);
            a.parentNode.insertBefore(av, a);
            const cp = document.createElement('button'); cp.type = 'button'; cp.className = 'hy-copy'; cp.title = 'Copy domain'; cp.setAttribute('aria-label', 'Copy domain');
            cp.innerHTML = '<svg class="ic ic-a" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>'+ '<svg class="ic ic-b" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
            cp.addEventListener('click', () => { try { navigator.clipboard.writeText(d); } catch (e) {} cp.classList.add('done'); setTimeout(() => cp.classList.remove('done'), 1200); });
            a.after(cp);
        });
        let q = 0; const run = () => { if (q) return; q = requestAnimationFrame(() => { q = 0; enhance(); apply(); }); };
        new MutationObserver(run).observe(tbody, { childList: true });
        input.addEventListener('input', apply);
        btns.forEach(b => b.addEventListener('click', () => { filter = b.dataset.f; btns.forEach(x => x.classList.toggle('on', x === b)); apply(); }));
        run();
    });
})();

/* ── Workspace pages (phase 2): tab captions, action-aware buttons, Docker filter ── */
(() => {
    'use strict';
    const $ = (s, r = document) => r.querySelector(s);
    const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
    const safe = (fn) => { try { fn(); } catch (e) { console.warn('[workspace]', e); } };

    /* 1. Every tab strip gets a one-line caption per tab (same card style as Create site). */
    safe(() => {
        const SUB = {
            'server:service-ctrl': 'Start · stop · restart', 'server:firewall': 'UFW rules', 'server:reload': 'Reload configs', 'server:os': 'Repair dpkg / apt',
            'services:mail': 'Postfix · Dovecot', 'services:ftp': 'File transfer', 'services:pma': 'Database UI', 'services:samba': 'File sharing',
            'mariadb:db': 'Create · backup · drop', 'mariadb:users': 'Create · grant · revoke',
            'dns:list': 'Edit zones', 'dns:create': 'New zone', 'dns:mx': 'Mail routing', 'dns:test': 'Query a zone',
            'docker:containers': 'Running · stopped', 'docker:create': 'Proxy a container', 'docker:oneclick': 'Ready-made apps'
        };
        $$('.page-section:not(#page-makesite) .content-tab-btn').forEach(b => {
            const page = (b.closest('.page-section') || {}).id;
            const key = (page || '').replace('page-', '') + ':' + b.dataset.tab;
            if (SUB[key] && !b.dataset.sub) b.dataset.sub = SUB[key];
        });
    });

    /* 2. Action forms: the Run button says exactly what it will do, and warns when it is destructive. */
    safe(() => {
        const DANGER = /^(drop|stop|disable|revoke|restore|delete|remove|uninstall|prune|flush|deny)\b/i;
        const clean = (t) => t.replace(/\s*\(.*?\)\s*/g, ' ').replace(/\s+/g, ' ').trim();
        $$('#page-server form, #page-mariadb form, #page-dns form').forEach(f => {
            const act = f.querySelector('select[name="action"]');
            const btn = f.querySelector('button[type="submit"].btn-primary, button[type="submit"].btn');
            if (!act || !btn || btn.dataset.zxBound) return;
            btn.dataset.zxBound = '1';
            const textNode = () => Array.from(btn.childNodes).find(n => n.nodeType === 3 && n.nodeValue.trim());
            const first = textNode();
            if (!first) return;
            const base = first.nodeValue.trim();
            const svc = f.querySelector('select[name="service"]');
            const paint = () => {
                const a = act.selectedOptions[0];
                if (!a || btn.disabled || btn.querySelector('.btn-spinner')) return;   // app.js swaps the content while running
                const node = textNode();
                if (!node) return;
                const aval = a.value || '', atext = clean(a.textContent);
                const target = svc && svc.selectedOptions[0] ? clean(svc.selectedOptions[0].textContent) : '';
                let label = target ? `${atext} ${target}` : atext;
                if (!/^[A-Z]/.test(label)) label = base;
                node.nodeValue = ' ' + label + ' ';
                const danger = DANGER.test(aval) || DANGER.test(atext);
                btn.classList.toggle('is-danger', danger);
                f.classList.toggle('zx-danger', danger);
            };
            act.addEventListener('change', paint);
            new MutationObserver(() => { if (!btn.disabled) paint(); }).observe(btn, { childList: true, attributes: true, attributeFilter: ['disabled'] });
            if (svc) svc.addEventListener('change', paint);
            paint();
        });
    });

    /* 3. Docker containers: search + Running/Stopped filter, same bar as Sites. */
    safe(() => {
        const tbody = document.getElementById('docker-tbody');
        const card = tbody && tbody.closest('.card');
        const wrap = card && $('.sites-table-wrap', card);
        if (!wrap || $('.hy-toolbar', card)) return;
        const bar = document.createElement('div'); bar.className = 'hy-toolbar';
        bar.innerHTML = `
            <label class="hy-search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg><input type="search" placeholder="Filter containers…" aria-label="Filter containers" autocomplete="off" spellcheck="false"></label>
            <div class="hy-seg" role="group" aria-label="Status filter">
                <button type="button" data-f="all" class="on">All <b>0</b></button>
                <button type="button" data-f="active">Running <b>0</b></button>
                <button type="button" data-f="inactive">Stopped <b>0</b></button>
            </div>`;
        card.insertBefore(bar, wrap);
        const input = $('input', bar), btns = $$('.hy-seg button', bar);
        let filter = 'all';
        const miss = document.createElement('tr'); miss.className = 'hy-miss'; miss.innerHTML = '<td colspan="4"><div class="hy-none">No containers match that filter.</div></td>';
        const rows = () => $$('tr', tbody).filter(r => !r.classList.contains('skeleton-row') && !r.classList.contains('hy-miss') && $('.status-text', r));
        const apply = () => {
            const rs = rows(), q = input.value.trim().toLowerCase();
            const cnt = { all: rs.length, active: 0, inactive: 0 };
            let shown = 0;
            rs.forEach(r => {
                const st = $('.status-text', r).classList.contains('active') ? 'active' : 'inactive';
                cnt[st]++;
                const ok = (filter === 'all' || filter === st) && (!q || r.textContent.toLowerCase().includes(q));
                r.classList.toggle('hy-hide', !ok); if (ok) shown++;
            });
            btns.forEach(b => { $('b', b).textContent = cnt[b.dataset.f]; });
            if (rs.length && !shown) { if (!miss.isConnected) tbody.appendChild(miss); } else miss.remove();
            bar.hidden = !rs.length && !input.value;
        };
        let q = 0; const run = () => { if (!q) q = requestAnimationFrame(() => { q = 0; apply(); }); };
        new MutationObserver(run).observe(tbody, { childList: true });
        input.addEventListener('input', apply);
        btns.forEach(b => b.addEventListener('click', () => { filter = b.dataset.f; btns.forEach(x => x.classList.toggle('on', x === b)); apply(); }));
        run();
    });
})();

/* ── Help: turn the text list into a launchpad (cards that open the page) + shortcut strip ── */
(() => {
    'use strict';
    try {
        const page = document.getElementById('page-help');
        const card = page && page.querySelector('.card');
        const ul = card && card.querySelector('ul');
        if (!ul || card.querySelector('.hy-help-grid')) return;
        const KEYS = { dashboard: 'd', managesites: 'm', makesite: 's', dns: 'z', mariadb: 'b', services: 'i', server: 'v', terminal: 't', network: 'n', docker: 'k', help: 'h' };
        const grid = document.createElement('div'); grid.className = 'hy-help-grid';
        Array.from(ul.querySelectorAll('li')).forEach((li, i) => {
            const strong = li.querySelector('strong'); if (!strong) return;
            const name = strong.textContent.trim();
            const desc = li.textContent.replace(strong.textContent, '').replace(/^\s*:\s*/, '').trim();
            const nav = Array.from(document.querySelectorAll('.nav-item')).find(n => (n.dataset.title || '').trim().toLowerCase() === name.toLowerCase());
            const b = document.createElement('button'); b.type = 'button'; b.className = 'hy-help-card'; b.style.animationDelay = (i * 45) + 'ms';
            const ico = document.createElement('span'); ico.className = 'hy-help-ico';
            const svg = nav && nav.querySelector('svg'); if (svg) ico.appendChild(svg.cloneNode(true));
            const t = document.createElement('b'); t.textContent = name;
            const d = document.createElement('span'); d.className = 'd'; d.textContent = desc;
            b.append(ico, t, d);
            const key = nav && KEYS[nav.dataset.page];
            if (key) { const k = document.createElement('kbd'); k.innerHTML = '<i>g</i><i>' + key + '</i>'; b.appendChild(k); }
            if (nav) b.addEventListener('click', () => { if (typeof window.switchPage === 'function') window.switchPage(nav.dataset.page); });
            grid.appendChild(b);
        });
        if (!grid.children.length) return;
        ul.hidden = true; ul.after(grid);
        const sc = document.createElement('div'); sc.className = 'hy-shortcuts';
        [['<kbd>Ctrl</kbd><kbd>K</kbd>', 'Jump to any page or action'], ['<kbd>g</kbd><kbd>…</kbd>', 'Go to a page (letters on the cards)'], ['<kbd>?</kbd>', 'Show all shortcuts'], ['<kbd>R</kbd>', 'Reload data'], ['<kbd>Ctrl</kbd><kbd>Enter</kbd>', 'Submit the form you are in']]
            .forEach(([k, label]) => { const s = document.createElement('span'); s.className = 'hy-sc'; s.innerHTML = k + ' ' + label; sc.appendChild(s); });
        const tutorial = Array.from(card.querySelectorAll('p')).find(p => p.querySelector('button'));
        (tutorial || grid).before(sc);
    } catch (e) { console.warn('[help]', e); }
})();

/* ── Theme switch, serif accent words, count-up numbers (Glass theme) ── */
(() => {
    'use strict';
    const safe = (fn) => { try { fn(); } catch (e) { console.warn('[theme]', e); } };
    const root = document.documentElement;
    const reduce = () => root.dataset.motion === 'reduce';

    /* theme button next to Motion */
    safe(() => {
        const anchor = document.getElementById('hy-motion-btn') || document.getElementById('goldfish-btn');
        if (!anchor || document.getElementById('zx-theme-btn')) return;
        const b = document.createElement('button');
        b.type = 'button'; b.id = 'zx-theme-btn'; b.className = anchor.className.replace(/\bactive\b/, '').trim();
        const paint = () => {
            const glass = root.dataset.theme === 'glass';
            b.title = glass ? 'Theme: Glass — switch to flat dark' : 'Theme: flat dark — switch to Glass';
            b.setAttribute('aria-pressed', String(glass));
            b.innerHTML = '<svg class="ic ic-a" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5" style="fill:none;stroke:currentColor;stroke-width:2"/><path d="M12 3.5a8.5 8.5 0 0 0 0 17z" style="fill:currentColor;stroke:none"/></svg>' + '<svg class="ic ic-b" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5" style="fill:none;stroke:currentColor;stroke-width:2"/></svg>';
        };
        b.addEventListener('click', () => {
            const next = root.dataset.theme === 'glass' ? 'hypr' : 'glass';
            root.dataset.theme = next;
            try { localStorage.setItem('zm-theme2', next); } catch (e) {}
            paint();
        });
        paint();
        anchor.parentElement.insertBefore(b, anchor);
    });

    /* last word of every page title in an italic serif (shows in the Glass theme) */
    safe(() => {
        document.querySelectorAll('.page-header h2').forEach(h => {
            if (h.querySelector('.zx-serif')) return;
            const m = h.textContent.trim().match(/^(.*\s)?(\S+)$/);
            if (!m || !m[2]) return;
            h.textContent = '';
            if (m[1]) h.appendChild(document.createTextNode(m[1]));
            const em = document.createElement('em'); em.className = 'zx-serif'; em.textContent = m[2]; h.appendChild(em);
        });
    });

    /* numbers count up from zero when data first arrives and whenever the Dashboard opens */
    safe(() => {
        const els = Array.from(document.querySelectorAll('.fx-metric-main b'));
        const finals = new Map(); let writing = false;
        const write = (el, t) => { writing = true; el.textContent = t; writing = false; };
        const run = (el) => {
            if (reduce() || el.dataset.zxA) return;
            const txt = el.textContent.trim().replace(',', '.');
            const n = parseFloat(txt);
            if (!isFinite(n) || !/^-?\d+(\.\d+)?$/.test(txt)) return;
            const dec = (txt.split('.')[1] || '').length;
            finals.set(el, el.textContent); el.dataset.zxA = '1';
            const t0 = performance.now(), dur = 950;
            const step = (now) => {
                const p = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - p, 4);
                if (p < 1) { write(el, (n * e).toFixed(dec)); requestAnimationFrame(step); }
                else { write(el, finals.get(el)); delete el.dataset.zxA; }
            };
            requestAnimationFrame(step);
        };
        els.forEach(el => {
            let prevNumeric = /^-?\d/.test(el.textContent.trim());
            new MutationObserver(() => {
                if (writing) return;
                if (el.dataset.zxA) { finals.set(el, el.textContent); return; }
                const now = /^-?\d/.test(el.textContent.trim());
                if (now && !prevNumeric) run(el);
                prevNumeric = now;
            }).observe(el, { childList: true, characterData: true, subtree: true });
        });
        const page = document.getElementById('page-dashboard');
        if (page) new MutationObserver(() => { if (page.classList.contains('active')) setTimeout(() => els.forEach(run), 180); }).observe(page, { attributes: true, attributeFilter: ['class'] });
    });
})();

/* ── Rule 9: a specific headline. The Dashboard title says what is true right now. ── */
(() => {
    'use strict';
    try {
        const h2 = document.querySelector('#page-dashboard .page-header h2');
        if (!h2) return;
        const IDS = ['apache2', 'nginx', 'bind9', 'mariadb', 'postfix', 'dovecot', 'ufw'];
        const down = () => IDS.filter(id => { const b = document.getElementById(id + '-val'); return b && b.classList.contains('inactive'); }).length;
        let last = '';
        const set = (pre, word) => {
            const key = pre + '|' + word; if (key === last) return; last = key;
            h2.classList.remove('zx-swap'); void h2.offsetWidth;
            h2.textContent = '';
            if (pre) h2.appendChild(document.createTextNode(pre + ' '));
            const em = document.createElement('em'); em.className = 'zx-serif'; em.textContent = word; h2.appendChild(em);
            h2.classList.add('zx-swap');
        };
        const update = () => {
            const tile = document.querySelector('#page-dashboard .fx-health');
            const st = tile ? tile.dataset.state : 'loading';
            if (st === 'offline') set('Can’t reach the', 'server');
            else if (st === 'ok') set('All systems', 'running');
            else if (st === 'partial' || st === 'down') { const n = down(); set(n > 1 ? n + ' services' : n === 1 ? '1 service' : 'Services', 'down'); }
            else if (st === 'idle') set('Nothing', 'installed');
            else set('Server', 'overview');
        };
        let tries = 0;
        const iv = setInterval(() => {
            const tile = document.querySelector('#page-dashboard .fx-health');
            if (tile || ++tries > 30) {
                clearInterval(iv);
                if (tile) { new MutationObserver(update).observe(tile, { attributes: true, attributeFilter: ['data-state'] }); update(); }
            }
        }, 200);
    } catch (e) { console.warn('[headline]', e); }
})();

/* ── Intro loading screen, nav stagger index, cursor sheen on boxes ── */
(() => {
    'use strict';
    const safe = (fn) => { try { fn(); } catch (e) { console.warn('[intro]', e); } };
    const root = document.documentElement;
    const Z = () => { const b = document.body; return (b && b.offsetWidth) ? (b.getBoundingClientRect().width / b.offsetWidth) || 1 : 1; };

    /* nav items get an index for the staggered arrival */
    safe(() => document.querySelectorAll('.nav-menu .nav-item').forEach((n, i) => n.style.setProperty('--i', i)));

    /* splash: runs ~1.9 s, status line cycles, click/key skips, then an iris closes onto the app */
    safe(() => {
        const sp = document.getElementById('zx-splash');
        if (!sp) return;
        if (root.classList.contains('zx-nosplash') || root.dataset.motion === 'reduce') { sp.remove(); return; }
        const status = document.getElementById('zx-sp-status');
        const steps = ['starting panel', 'loading interface', 'linking services', 'ready'];
        steps.forEach((t, i) => setTimeout(() => { if (status && sp.isConnected) status.textContent = t; }, i * 520));
        let done = false;
        const finish = () => {
            if (done) return; done = true;
            root.classList.add('zx-reveal');
            sp.classList.add('out');
            setTimeout(() => { sp.remove(); root.classList.remove('zx-reveal'); }, 1000);
        };
        const minimum = new Promise(r => setTimeout(r, 1900));
        const ready = Promise.race([
            Promise.all([document.readyState === 'complete' ? 1 : new Promise(r => addEventListener('load', r, { once: true })), document.fonts && document.fonts.ready ? document.fonts.ready : 1]),
            new Promise(r => setTimeout(r, 3200))
        ]);
        Promise.all([minimum, ready]).then(finish);
        sp.addEventListener('click', finish);
        addEventListener('keydown', finish, { once: true });
    });

    /* light that follows the cursor on tiles and cards */
    safe(() => {
        const SEL = '.fx-metric, .status-box:not(.fx-health), .hy-help-card, .hy-key, .ms-plan';
        let raf = 0, last = null;
        document.addEventListener('pointermove', (e) => {
            last = e;
            if (raf) return;
            raf = requestAnimationFrame(() => {
                raf = 0;
                const el = last.target.closest && last.target.closest(SEL);
                if (!el) return;
                if (!el.querySelector(':scope > .zx-sheen')) { const s = document.createElement('i'); s.className = 'zx-sheen'; s.setAttribute('aria-hidden', 'true'); el.appendChild(s); }
                const r = el.getBoundingClientRect(), z = Z();
                el.style.setProperty('--mx', ((last.clientX - r.left) / z) + 'px');
                el.style.setProperty('--my', ((last.clientY - r.top) / z) + 'px');
            });
        }, { passive: true });
    });
})();