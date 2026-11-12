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