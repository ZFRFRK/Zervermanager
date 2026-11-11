/* Zervermanager — motion.js
   Adds motion on top of app.js/fx.js. Reads state only; never changes app logic.
   Delete this file + its <script> tag + the "v14" block in style.css to roll back. */
(() => {
    'use strict';
    const reduce = () => !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
    const safe = (fn) => { try { fn(); } catch (e) { console.warn('[motion]', e); } };

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

    /* 2. Sidebar glider: one highlight slides between nav items. */
    safe(() => {
        const menu = document.querySelector('.nav-menu');
        if (!menu) return;
        const g = document.createElement('div');
        g.className = 'hy-nav-glider';
        menu.prepend(g);
        menu.classList.add('hy-has-glider');
        const place = (animate = true) => {
            const a = menu.querySelector('.nav-item.active');
            if (!a) { g.style.opacity = 0; return; }
            if (!animate) g.style.transition = 'none';
            g.style.opacity = 1;
            g.style.height = a.offsetHeight + 'px';
            g.style.width = a.offsetWidth + 'px';
            g.style.transform = `translate(${a.offsetLeft}px, ${a.offsetTop}px)`;
            if (!animate) { void g.offsetWidth; g.style.transition = ''; }
        };
        place(false);
        const mo = new MutationObserver(() => place(true));
        menu.querySelectorAll('.nav-item').forEach(n => mo.observe(n, { attributes: true, attributeFilter: ['class'] }));
        const sb = document.querySelector('.sidebar');
        if (sb) { new MutationObserver(() => { place(false); setTimeout(() => place(false), 380); }).observe(sb, { attributes: true, attributeFilter: ['class'] }); }
        addEventListener('resize', () => place(false));
        setTimeout(() => place(false), 300);
    });

    /* 3. Ripple on press for buttons, chips, tabs and nav rows. */
    safe(() => {
        const sel = '.btn, .btn-sm, .hy-key, .top-bar-btn, .content-tab-btn, .fx-select-opt';
        document.addEventListener('pointerdown', (e) => {
            if (reduce() || e.button > 0) return;
            const el = e.target.closest && e.target.closest(sel);
            if (!el || el.disabled) return;
            const r = el.getBoundingClientRect();
            const d = Math.max(r.width, r.height) * 2;
            const s = document.createElement('span');
            s.className = 'hy-ripple';
            s.style.cssText = `width:${d}px;height:${d}px;left:${e.clientX - r.left - d / 2}px;top:${e.clientY - r.top - d / 2}px`;
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
            const tab = store.get('zm-tab-makesite');
            if (tab && $(`#page-makesite .content-tab-btn[data-tab="${tab}"]`) && typeof window.switchTab === 'function') {
                const btn = $('#page-makesite .content-tab-btn.active');
                if (btn && btn.dataset.tab !== tab) window.switchTab('makesite', tab);
            }
        }, 450);
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
            tip.style.left = (r.right + 12) + 'px';
            tip.style.top = (r.top + r.height / 2) + 'px';
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