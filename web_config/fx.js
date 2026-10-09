/* ─────────────────────────────────────────────────────────────────────
   Zervermanager — fx.js  (v3)
   Motion + UX layer on top of app.js.

   Contract with app.js:
     · it never edits app state. It READS classes/text that app.js sets
       (badge .active/.inactive/.warning, #offline-banner.hidden, .active on
       pages/tabs) and calls app.js's own public functions
       (switchPage, fetchStatus, toggleSidebar, toggleGoldfishMode, …).
     · the only app.js function it wraps is switchPage(), solely to learn
       which direction you're navigating so pages can slide that way.
     · every module is isolated in try/catch — one failing never takes the
       others (or the app) down.

   Roll back: delete this file, its <script> tag, and the "v2"/"v3" blocks at
   the bottom of style.css.
───────────────────────────────────────────────────────────────────── */
(() => {
    'use strict';

    /* ═══════════════════════════════════════════════
       0. TINY HELPERS
    ═══════════════════════════════════════════════ */
    const $   = (s, r = document) => r.querySelector(s);
    const $$  = (s, r = document) => Array.from(r.querySelectorAll(s));
    const raf = (fn) => requestAnimationFrame(fn);
    const reduce = () => !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
    const typing = (t) => !!t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));
    const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const clamp = (n, a = 0, b = 100) => Math.min(b, Math.max(a, n));
    const isMac = /Mac|iPhone|iPad/.test(navigator.platform || '');

    function h(tag, cls, html) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (html != null) e.innerHTML = html;
        return e;
    }

    const IC = {
        chevron: '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>',
        check:   '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="20 6 9 17 4 12"/></svg>',
        arrow:   '<svg class="fx-go" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="7" y1="17" x2="17" y2="7"/><polyline points="8 7 17 7 17 16"/></svg>',
        search:  '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
        bolt:    '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>',
        tab:     '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/></svg>',
    };

    // page → "g + key" (mirrors the sequence map in app.js)
    const PAGE_KEYS = { dashboard: 'd', managesites: 'm', makesite: 's', dns: 'z', mariadb: 'b', services: 'i', server: 'v', network: 'n', docker: 'k', help: 'h' };

    /* ═══════════════════════════════════════════════
       1. SPLASH — skippable
    ═══════════════════════════════════════════════ */
    function initSplash() {
        const s = $('#startup-splash');
        if (!s) return;
        s.appendChild(h('div', 'fx-skip-hint', 'Press any key or click to skip'));

        const cleanup = () => {
            window.removeEventListener('keydown', skip, true);
            s.removeEventListener('pointerdown', skip);
        };
        function skip(e) {
            if (!s.isConnected || s.classList.contains('fx-skip')) return cleanup();
            if (e && e.type === 'keydown') {
                if (e.ctrlKey || e.metaKey || e.altKey || /^(Shift|Control|Alt|Meta)$/.test(e.key)) return;
                // the key that dismisses the splash must not also fire an app shortcut (e.g. "r" = reload)
                e.preventDefault();
                e.stopImmediatePropagation();
            }
            s.classList.add('fx-skip');
            setTimeout(() => s.classList.add('hidden'), 520);
            cleanup();
        }
        window.addEventListener('keydown', skip, true);
        s.addEventListener('pointerdown', skip);
    }

    /* ═══════════════════════════════════════════════
       2. SLIDING INDICATORS (sidebar nav, tab bars)
    ═══════════════════════════════════════════════ */
    const glideUpdaters = [];

    function addGlider(container, itemSel) {
        if (!container || container.__fxGlider) return;
        const g = h('span', 'fx-glider');
        g.setAttribute('aria-hidden', 'true');
        container.prepend(g);
        container.classList.add('has-glider');
        container.__fxGlider = g;

        let placed = false;
        const place = (animate) => {
            const el = container.querySelector(itemSel + '.active');
            if (!el || !el.offsetWidth) { g.style.opacity = '0'; placed = false; return; }
            g.style.transition = (animate && placed && !reduce()) ? '' : 'none';
            g.style.width  = el.offsetWidth + 'px';
            g.style.height = el.offsetHeight + 'px';
            g.style.transform = `translate(${el.offsetLeft}px, ${el.offsetTop}px)`;
            g.style.opacity = '1';
            placed = true;
        };
        place(false);

        new MutationObserver((muts) => { if (!muts.every(m => m.target === g)) place(true); })
            .observe(container, { attributes: true, attributeFilter: ['class'], subtree: true });
        if (typeof ResizeObserver !== 'undefined') new ResizeObserver(() => place(false)).observe(container);
        glideUpdaters.push(() => place(false));
    }

    function initGliders() {
        addGlider($('.nav-menu'), '.nav-item');
        $$('.content-tabs').forEach(t => addGlider(t, '.content-tab-btn'));
        window.addEventListener('resize', () => glideUpdaters.forEach(fn => fn()), { passive: true });
        if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => glideUpdaters.forEach(fn => fn()));
    }

    /* ═══════════════════════════════════════════════
       3. WAYBAR — docked state, clock
    ═══════════════════════════════════════════════ */
    function initWaybar() {
        const bar = $('#top-bar'), main = $('.main-content');
        if (bar && main && typeof IntersectionObserver !== 'undefined') {
            // an invisible marker covering the gap above the bar: once it has scrolled
            // out of view the bar has reached the top edge and is "docked"
            const marker = h('div', 'fx-sentinel');
            marker.style.cssText = 'position:absolute;left:0;right:0;top:0;pointer-events:none;visibility:hidden';
            main.prepend(marker);
            const size = () => { marker.style.height = Math.max(1, bar.offsetTop) + 'px'; };
            size();
            window.addEventListener('resize', size, { passive: true });
            new IntersectionObserver(([e]) => bar.classList.toggle('is-stuck', !e.isIntersecting && main.scrollTop > 2),
                { root: main, threshold: 0 }).observe(marker);
        }

        const clock = $('#waybar-clock');
        if (clock) {
            const d = $('.clock-date', clock), t = $('.clock-time', clock);
            const tick = () => {
                const n = new Date();
                d.textContent = n.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
                t.textContent = n.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
                clock.title = n.toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'short' });
                setTimeout(tick, 60000 - (n.getSeconds() * 1000 + n.getMilliseconds()) + 40);   // re-align to the minute
            };
            tick();
        }
    }

    /* ═══════════════════════════════════════════════
       4. OFFLINE BANNER — retry + reconnect feedback
    ═══════════════════════════════════════════════ */
    function initBanner() {
        const banner = $('#offline-banner');
        if (!banner) return;
        const btn = $('#offline-retry');
        const toast = (m, t) => { if (typeof window.showToast === 'function') window.showToast(m, t); };
        let wasOffline = !banner.classList.contains('hidden');

        if (btn) btn.addEventListener('click', () => {
            btn.classList.add('is-busy');
            try { if (typeof window.fetchStatus === 'function') window.fetchStatus(); } catch (_) {}
            setTimeout(() => {
                btn.classList.remove('is-busy');
                if (!banner.classList.contains('hidden')) toast('Still can’t reach the server', 'error');
            }, 1500);
        });

        new MutationObserver(() => {
            const off = !banner.classList.contains('hidden');
            if (wasOffline && !off) toast('Reconnected to server', 'success');
            wasOffline = off;
        }).observe(banner, { attributes: true, attributeFilter: ['class'] });
    }

    /* ═══════════════════════════════════════════════
       5. DASHBOARD — cards, badges, pills, health tile
    ═══════════════════════════════════════════════ */
    function enhanceCards() {
        $$('#page-dashboard .status-box-clickable').forEach(box => {
            const tip = box.getAttribute('title');
            if (tip) { box.dataset.tip = tip; box.removeAttribute('title'); }   // native tooltip covered the buttons
            box.tabIndex = 0;
            box.insertAdjacentHTML('beforeend', IC.arrow);
            box.addEventListener('keydown', (e) => {
                if (e.target === box && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); box.click(); }
            });
        });
    }

    // Tag each badge with a semantic state + flip it when the state really changes.
    // app.js rewrites class/text every 10s, so we compare a signature, not mutations.
    function watchBadges() {
        $$('#page-dashboard .badge-lg').forEach(b => {
            const state = () => {
                const c = ['active', 'inactive', 'warning'].find(x => b.classList.contains(x));
                if (c) return c;
                return /load/i.test(b.textContent) ? 'loading' : 'unknown';
            };
            const sig = () => state() + '|' + b.textContent.trim();
            let last = sig();
            b.dataset.fx = state();

            new MutationObserver(() => {
                b.dataset.fx = state();
                const now = sig();
                if (now === last) return;
                last = now;
                if (reduce()) return;
                b.classList.remove('fx-flip');
                void b.offsetWidth;
                b.classList.add('fx-flip');
            }).observe(b, { attributes: true, attributeFilter: ['class'], childList: true, characterData: true, subtree: true });

            b.addEventListener('animationend', (e) => { if (e.animationName === 'fxFlip') b.classList.remove('fx-flip'); });
        });
    }

    // numbers glide to their new value instead of snapping
    function tween(el, to, ms = 700, fmt = (v) => Math.round(v)) {
        const from = parseFloat(el.dataset.v);
        el.dataset.v = to;
        if (reduce() || isNaN(from) || from === to) { el._tw = null; el.textContent = fmt(to); return; }
        const token = (el._tw = {});
        const t0 = performance.now();
        const step = (t) => {
            if (el._tw !== token) return;
            const k = Math.min(1, (t - t0) / ms);
            el.textContent = fmt(from + (to - from) * (1 - Math.pow(1 - k, 3)));
            if (k < 1) raf(step);
        };
        raf(step);
    }
    const setText = (el, v) => { el._tw = null; el.dataset.v = ''; el.textContent = v; };

    function initPills() {
        $$('.res-pill').forEach(pill => {
            const val = $('.res-pill-val', pill), pip = $('.res-pip', pill);
            if (!val) return;
            if (pill.id === 'pill-uptime') pill.dataset.meter = 'none';

            const sync = () => {
                if (pip) {
                    pill.dataset.state = pip.classList.contains('is-danger') ? 'danger'
                        : pip.classList.contains('is-warning') ? 'warning'
                        : pip.classList.contains('is-normal') ? 'normal' : 'idle';
                }
                const m = /-?\d+(?:\.\d+)?/.exec(val.textContent);
                let pct = 0;
                if (m && pill.id !== 'pill-uptime') {
                    const n = parseFloat(m[0]);
                    pct = pill.id === 'pill-temp' ? clamp((n - 30) / 60 * 100) : clamp(n);
                }
                pill.style.setProperty('--pct', pct);
            };

            let shown = null;                                   // number currently displayed
            const mo = new MutationObserver(() => {
                const m = /^(\s*)(-?\d+(?:\.\d+)?)(.*)$/.exec(val.textContent);
                if (!m) { shown = null; sync(); return; }
                const to = parseFloat(m[2]), suffix = m[3], dec = (m[2].split('.')[1] || '').length;
                if (shown !== null && shown !== to && !reduce()) {
                    const from = shown, token = (val._tw = {}), t0 = performance.now();
                    const step = (t) => {
                        if (val._tw !== token) return;
                        const k = Math.min(1, (t - t0) / 800);
                        const v = from + (to - from) * (1 - Math.pow(1 - k, 3));
                        val.textContent = v.toFixed(dec) + suffix;
                        mo.takeRecords();                       // ignore our own writes
                        shown = v;
                        if (k < 1) raf(step);
                    };
                    val.textContent = from.toFixed(dec) + suffix; // undo app.js's snap, then glide
                    mo.takeRecords();
                    raf(step);
                } else {
                    shown = to;
                }
                sync();
            });
            mo.observe(val, { childList: true, characterData: true, subtree: true });
            if (pip) new MutationObserver(sync).observe(pip, { attributes: true, attributeFilter: ['class'] });
            sync();
        });
    }

    function buildHealth() {
        const grid = $('#page-dashboard .status-grid');
        if (!grid) return;
        const badges = $$('.status-box', grid).map(b => $('.badge-lg', b)).filter(Boolean);
        if (!badges.length) return;

        const tile = h('div', 'status-box fx-health', `
            <div class="fx-health-top">
                <div class="fx-ring" aria-hidden="true">
                    <svg viewBox="0 0 100 100">
                        <defs><linearGradient id="fx-grad" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#00f5d4"/><stop offset="1" stop-color="#8b5cf6"/></linearGradient></defs>
                        <circle class="fx-ring-track" cx="50" cy="50" r="42"/>
                        <circle class="fx-ring-bar" cx="50" cy="50" r="42" pathLength="100"/>
                    </svg>
                    <div class="fx-ring-num"><b>…</b><small></small></div>
                </div>
                <div class="fx-health-text" role="status" aria-live="polite">
                    <strong>Checking services</strong>
                    <span>Waiting for the first response.</span>
                </div>
            </div>
            <button type="button" class="fx-reload" title="Reload all services [r]">
                <svg width="12" height="12" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M4 2a1 1 0 011 1v2.101a7.002 7.002 0 0111.601 2.566 1 1 0 11-1.885.666A5.002 5.002 0 005.999 7H9a1 1 0 010 2H4a1 1 0 01-1-1V3a1 1 0 011-1zm.008 9.057a1 1 0 011.276.61A5.002 5.002 0 0014.001 13H11a1 1 0 110-2h5a1 1 0 011 1v5a1 1 0 11-2 0v-2.101a7.002 7.002 0 01-11.601-2.566 1 1 0 01.61-1.276z" clip-rule="evenodd"/></svg>
                <span>Reload all</span><kbd class="waybar-kbd">r</kbd>
            </button>`);
        tile.dataset.state = 'loading';
        grid.prepend(tile);

        const bar = $('.fx-ring-bar', tile), num = $('.fx-ring-num b', tile), small = $('.fx-ring-num small', tile);
        const title = $('.fx-health-text strong', tile), sub = $('.fx-health-text span', tile);
        let lastKey = '';

        const render = () => {
            const banner = $('#offline-banner');
            const offline = !!banner && !banner.classList.contains('hidden');
            let up = 0, down = 0, na = 0;
            badges.forEach(b => {
                if (b.classList.contains('active')) up++;
                else if (b.classList.contains('inactive')) down++;
                else if (b.classList.contains('warning')) na++;          // "not installed" — not a fault
            });
            const total = up + down;
            let state, head, text, pct, big, tiny = '';

            if (offline) {
                state = 'offline'; head = 'No connection'; text = 'Service status can’t be read right now.'; pct = 0; big = '—';
            } else if (up + down + na === 0) {
                state = 'loading'; head = 'Checking services'; text = 'Waiting for the first response.'; pct = 0; big = '…';
            } else if (total === 0) {
                state = 'idle'; head = 'Nothing installed'; text = `${na} service${na === 1 ? '' : 's'} not installed`; pct = 0; big = 0; tiny = 'of 0';
            } else {
                state = down === 0 ? 'ok' : (up === 0 ? 'down' : 'partial');
                head = state === 'ok' ? 'All services running'
                     : state === 'down' ? 'Nothing is running'
                     : `${down} service${down === 1 ? '' : 's'} stopped`;
                text = `${up} of ${total} running` + (na ? ` · ${na} not installed` : '');
                pct = (up / total) * 100; big = up; tiny = `of ${total}`;
            }

            const key = [state, up, down, na].join('|');
            if (key === lastKey) return;
            lastKey = key;

            tile.dataset.state = state;
            bar.style.setProperty('--off', pct <= 0 ? 101 : 100 - pct);
            if (title.textContent !== head) title.textContent = head;
            if (sub.textContent !== text) sub.textContent = text;
            small.textContent = tiny;
            typeof big === 'number' ? tween(num, big) : setText(num, big);
        };

        let queued = false;
        const schedule = () => { if (!queued) { queued = true; raf(() => { queued = false; render(); }); } };
        badges.forEach(b => new MutationObserver(schedule).observe(b, { attributes: true, attributeFilter: ['class'], childList: true, characterData: true, subtree: true }));
        const banner = $('#offline-banner');
        if (banner) new MutationObserver(schedule).observe(banner, { attributes: true, attributeFilter: ['class'] });
        new MutationObserver(() => { if (!tile.isConnected) grid.prepend(tile); }).observe(grid, { childList: true });

        $('.fx-reload', tile).addEventListener('click', () => { const b = $('#tb-reload-btn'); if (b) b.click(); });
        render();
    }

    function bindSpotlight() {
        let ticking = false, ev = null;
        document.addEventListener('pointermove', (e) => {
            if (e.pointerType === 'touch') return;
            const box = e.target.closest && e.target.closest('.status-box');
            if (!box) return;
            ev = { box, x: e.clientX, y: e.clientY };
            if (ticking) return;
            ticking = true;
            raf(() => {
                ticking = false;
                if (!ev) return;
                const r = ev.box.getBoundingClientRect();
                ev.box.style.setProperty('--mx', (ev.x - r.left) + 'px');
                ev.box.style.setProperty('--my', (ev.y - r.top) + 'px');
            });
        }, { passive: true });
    }

    /* ═══════════════════════════════════════════════
       6. PAGE FLOW — slide direction + staggered entrance
    ═══════════════════════════════════════════════ */
    const SEL = '.page-header, .status-box, .card, .content-tabs, .fx-metric, .fx-section-head';
    let revealed = false;

    function targetsFor(section) {
        return $$(SEL, section).filter(el =>
            el.offsetParent !== null &&
            !el.closest('.modal-overlay') &&
            !(el.parentElement && el.parentElement.closest(SEL)));
    }

    function stagger(els) {
        els.forEach((el, i) => {
            el.classList.remove('fx-pre', 'fx-in');
            void el.offsetWidth;
            el.style.setProperty('--i', Math.min(i, 8));
            el.classList.add('fx-in');
            const done = (e) => {
                if (e.target !== el || e.animationName !== 'fxRise') return;
                el.classList.remove('fx-in');
                el.style.removeProperty('--i');
                el.removeEventListener('animationend', done);
            };
            el.addEventListener('animationend', done);
        });
    }

    function markFresh(section) {
        section.setAttribute('data-fx-fresh', '');
        clearTimeout(section.__fxFresh);
        section.__fxFresh = setTimeout(() => section.removeAttribute('data-fx-fresh'), 1600);
    }

    function whenSplashDone(cb) {
        const s = $('#startup-splash');
        if (!s) return cb();
        const t0 = performance.now();
        const check = () => {
            if (!s.isConnected) return cb();
            const cs = getComputedStyle(s);
            if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) < 0.55) return cb();
            if (performance.now() - t0 > 20000) return cb();
            setTimeout(check, 90);
        };
        check();
    }

    // pages slide the way you're navigating, like Hyprland workspaces
    function wrapSwitchPage() {
        const orig = window.switchPage;
        if (typeof orig !== 'function') return;
        const order = $$('.nav-item[data-page]').map(b => b.dataset.page);
        window.switchPage = function (id) {
            const cur = $('.page-section.active');
            const from = cur ? cur.id.replace('page-', '') : null;
            if (from && from !== id) {
                document.documentElement.style.setProperty('--fx-dir', order.indexOf(id) >= order.indexOf(from) ? 1 : -1);
            }
            return orig.apply(this, arguments);
        };
    }

    function initEntrance() {
        const sections = $$('.page-section');
        const watcher = new MutationObserver((muts) => {
            muts.forEach(m => {
                const sec = m.target;
                const was = (m.oldValue || '').split(/\s+/).includes('active');
                if (was || !sec.classList.contains('active')) return;
                const main = $('.main-content');
                if (main && main.scrollTop) main.scrollTo({ top: 0, behavior: 'instant' });   // new page starts at the top, no scroll animation
                if (!revealed || reduce()) return;
                markFresh(sec);                       // table rows still ease in; the page slide is pure CSS
            });
        });
        sections.forEach(sec => watcher.observe(sec, { attributes: true, attributeFilter: ['class'], attributeOldValue: true }));

        const first = $('.page-section.active');
        if (reduce() || !first) { revealed = true; return; }

        const list = targetsFor(first);
        list.forEach(el => el.classList.add('fx-pre'));
        const failsafe = setTimeout(() => list.forEach(el => el.classList.remove('fx-pre')), 26000);
        whenSplashDone(() => {
            clearTimeout(failsafe);
            revealed = true;
            stagger(list.filter(el => el.isConnected));
            markFresh(first);
        });
    }

    // Simple (Goldfish) mode hides/shows fields instantly — re-flow the form so it doesn't feel like a jump cut
    function watchGoldfish() {
        new MutationObserver((muts) => {
            muts.forEach(m => {
                const had = (m.oldValue || '').split(/\s+/).includes('goldfish-mode');
                const has = document.body.classList.contains('goldfish-mode');
                if (had === has || reduce()) return;
                const sec = $('.page-section.active');
                if (!sec) return;
                const pane = $('.tab-pane.active', sec) || sec;
                stagger($$('.tab-desc.active, .form-group, .form-details, .btn-primary', pane).filter(el => el.offsetParent !== null));
            });
        }).observe(document.body, { attributes: true, attributeFilter: ['class'], attributeOldValue: true });
    }

    /* ═══════════════════════════════════════════════
       7. CUSTOM SELECT — replaces the OS dropdown
       The native <select> stays in the DOM (hidden): FormData, `required`,
       inline onchange="…this.value…", form.reset() and `select.value = x`
       all keep working. We fire real input/change events on selection.
    ═══════════════════════════════════════════════ */
    function initSelects() {
        const selects = $$('select.form-control');
        if (!selects.length) return;

        const menu = h('div', 'fx-select-menu');
        menu.setAttribute('role', 'listbox');
        menu.tabIndex = -1;
        const hl = h('span', 'fx-select-hl');
        menu.appendChild(hl);
        document.body.appendChild(menu);

        const protoVal = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value');
        const protoIdx = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'selectedIndex');
        let cur = null, tBuf = '', tTimer = 0, uid = 0;

        const labelOf = (sel) => { const o = sel.options[sel.selectedIndex]; return o ? o.text.trim() : ''; };

        function enhance(sel) {
            if (sel.__fx) return;
            sel.__fx = true;
            const wrap = h('div', 'fx-select');
            sel.parentNode.insertBefore(wrap, sel);
            wrap.appendChild(sel);
            sel.classList.add('fx-select-native');
            sel.tabIndex = -1;
            sel.setAttribute('aria-hidden', 'true');

            const btn = h('button', 'form-control fx-select-trigger', '<span class="fx-select-value"></span>' + IC.chevron);
            btn.type = 'button';
            btn.setAttribute('role', 'combobox');
            btn.setAttribute('aria-haspopup', 'listbox');
            btn.setAttribute('aria-expanded', 'false');
            const lab = sel.closest('.form-group') && $('label', sel.closest('.form-group'));
            if (lab) { lab.id = lab.id || 'fx-lab-' + (++uid); btn.setAttribute('aria-labelledby', lab.id); }
            wrap.appendChild(btn);

            const sync = () => {
                $('.fx-select-value', btn).textContent = labelOf(sel);
                btn.disabled = sel.disabled;
                if (cur && cur.sel === sel) cur.opts.forEach(o => o.el.setAttribute('aria-selected', String(o.i === sel.selectedIndex)));
            };
            sel.__fxSync = sync;

            // programmatic changes (select.value = 'x') must update the visible label too
            Object.defineProperty(sel, 'value', { configurable: true, get() { return protoVal.get.call(this); }, set(v) { protoVal.set.call(this, v); sync(); } });
            Object.defineProperty(sel, 'selectedIndex', { configurable: true, get() { return protoIdx.get.call(this); }, set(v) { protoIdx.set.call(this, v); sync(); } });
            sel.addEventListener('change', sync);
            if (sel.form) sel.form.addEventListener('reset', () => setTimeout(sync, 0));
            new MutationObserver(sync).observe(sel, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled', 'selected'] });

            btn.addEventListener('click', () => (cur && cur.btn === btn) ? close() : open(sel, btn));
            btn.addEventListener('keydown', (e) => onKey(e, sel, btn));
            sync();
        }

        function open(sel, btn) {
            close();
            $$('.fx-select-opt, .fx-select-group', menu).forEach(n => n.remove());
            const opts = [];
            const add = (o) => {
                const d = h('div', 'fx-select-opt', `<span>${esc(o.text.trim())}</span>${IC.check}`);
                d.id = 'fx-opt-' + opts.length;
                d.setAttribute('role', 'option');
                d.setAttribute('aria-selected', String(o.index === sel.selectedIndex));
                if (o.disabled) d.setAttribute('aria-disabled', 'true');
                menu.appendChild(d);
                opts.push({ i: o.index, el: d, disabled: o.disabled });
            };
            Array.from(sel.children).forEach(c => {
                if (c.tagName === 'OPTGROUP') { menu.appendChild(h('div', 'fx-select-group', esc(c.label))); Array.from(c.children).forEach(add); }
                else add(c);
            });
            cur = { sel, btn, opts, active: Math.max(0, opts.findIndex(o => o.i === sel.selectedIndex)) };
            btn.setAttribute('aria-expanded', 'true');
            position();
            menu.classList.add('open');
            setActive(cur.active, true);
        }

        function close() {
            if (!cur) return;
            cur.btn.setAttribute('aria-expanded', 'false');
            cur.btn.removeAttribute('aria-activedescendant');
            menu.classList.remove('open');
            hl.style.opacity = '0';
            cur = null;
        }

        function position() {
            if (!cur) return;
            if (!cur.btn.offsetParent) return close();          // trigger vanished (page switched, etc.)
            const r = cur.btn.getBoundingClientRect();
            const below = innerHeight - r.bottom - 14, above = r.top - 14;
            const down = below >= 200 || below >= above;
            menu.style.maxHeight = clamp(down ? below : above, 120, 280) + 'px';
            menu.style.width = r.width + 'px';
            menu.style.left = r.left + 'px';
            const hgt = Math.min(menu.scrollHeight, parseFloat(menu.style.maxHeight));
            menu.style.top = (down ? r.bottom + 6 : r.top - 6 - hgt) + 'px';
            menu.dataset.side = down ? 'bottom' : 'top';
        }

        function setActive(i, instant) {
            if (!cur) return;
            const o = cur.opts[i];
            if (!o) return;
            cur.opts.forEach((x, j) => x.el.classList.toggle('is-active', j === i));
            cur.active = i;
            cur.btn.setAttribute('aria-activedescendant', o.el.id);
            const top = o.el.offsetTop, bot = top + o.el.offsetHeight;
            if (top < menu.scrollTop + 4) menu.scrollTop = top - 6;
            else if (bot > menu.scrollTop + menu.clientHeight - 4) menu.scrollTop = bot - menu.clientHeight + 6;
            if (instant) hl.style.transition = 'none';
            hl.style.height = o.el.offsetHeight + 'px';
            hl.style.transform = `translateY(${o.el.offsetTop}px)`;
            hl.style.opacity = '1';
            if (instant) { void hl.offsetWidth; hl.style.transition = ''; }
        }

        function move(d) {
            if (!cur) return;
            const n = cur.opts.length;
            let j = cur.active;
            for (let k = 0; k < n; k++) { j = (j + d + n) % n; if (!cur.opts[j].disabled) break; }
            setActive(j);
        }

        function choose(i) {
            const o = cur && cur.opts[i];
            if (!o || o.disabled) return;
            const { sel, btn } = cur;
            const changed = sel.selectedIndex !== o.i;
            protoIdx.set.call(sel, o.i);
            close();
            sel.__fxSync();
            if (changed) {
                sel.dispatchEvent(new Event('input',  { bubbles: true }));
                sel.dispatchEvent(new Event('change', { bubbles: true }));   // fires the inline onchange="…" handlers
            }
            btn.focus();
        }

        function typeahead(k) {
            clearTimeout(tTimer);
            tBuf += k.toLowerCase();
            tTimer = setTimeout(() => { tBuf = ''; }, 650);
            const n = cur.opts.length, start = tBuf.length === 1 ? cur.active + 1 : cur.active;
            for (let d = 0; d < n; d++) {
                const j = (start + d) % n, o = cur.opts[j];
                if (!o.disabled && o.el.textContent.trim().toLowerCase().startsWith(tBuf)) return setActive(j);
            }
        }

        function onKey(e, sel, btn) {
            const k = e.key, isOpen = cur && cur.btn === btn;
            const plain = k.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey;
            const stop = () => { e.preventDefault(); e.stopPropagation(); };
            if (!isOpen) {
                if (k === 'ArrowDown' || k === 'ArrowUp' || k === 'Enter' || k === ' ') { stop(); open(sel, btn); }
                else if (plain) { stop(); open(sel, btn); typeahead(k); }    // also keeps app shortcuts (r, g, ?) from firing
                return;
            }
            switch (k) {
                case 'ArrowDown': stop(); move(1); break;
                case 'ArrowUp':   stop(); move(-1); break;
                case 'Home':      stop(); setActive(cur.opts.findIndex(o => !o.disabled)); break;
                case 'End':       stop(); setActive(cur.opts.length - 1 - [...cur.opts].reverse().findIndex(o => !o.disabled)); break;
                case 'Enter': case ' ': stop(); choose(cur.active); break;
                case 'Escape':    stop(); close(); break;
                case 'Tab':       close(); break;
                default: if (plain) { stop(); typeahead(k); }
            }
        }

        menu.addEventListener('pointermove', (e) => {
            const d = e.target.closest('.fx-select-opt');
            if (!d || !cur) return;
            const j = cur.opts.findIndex(o => o.el === d);
            if (j >= 0 && j !== cur.active && !cur.opts[j].disabled) setActive(j);
        });
        menu.addEventListener('click', (e) => {
            const d = e.target.closest('.fx-select-opt');
            if (d && cur) choose(cur.opts.findIndex(o => o.el === d));
        });
        document.addEventListener('pointerdown', (e) => {
            if (cur && !menu.contains(e.target) && !cur.btn.contains(e.target)) close();
        }, true);
        window.addEventListener('resize', position, { passive: true });
        document.addEventListener('scroll', (e) => { if (cur && e.target !== menu) position(); }, true);

        selects.forEach(enhance);
    }

    /* ═══════════════════════════════════════════════
       8. COMMAND PALETTE — Ctrl/⌘ K  or  /
    ═══════════════════════════════════════════════ */
    function initPalette() {
        const overlay = h('div', 'fx-pal-overlay');
        overlay.innerHTML = `
            <div class="fx-pal" role="dialog" aria-modal="true" aria-label="Command palette">
                <div class="fx-pal-input">
                    ${IC.search}
                    <input type="text" placeholder="Jump to a page or run an action…" spellcheck="false" autocomplete="off" aria-label="Search commands">
                    <kbd>esc</kbd>
                </div>
                <div class="fx-pal-list" role="listbox"><span class="fx-pal-hl"></span></div>
                <div class="fx-pal-foot">
                    <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
                    <span><kbd>↵</kbd> open</span>
                    <span><kbd>esc</kbd> close</span>
                </div>
            </div>`;
        document.body.appendChild(overlay);

        const input = $('input', overlay), list = $('.fx-pal-list', overlay), hl = $('.fx-pal-hl', overlay);
        let items = [], shown = [], active = 0, prevFocus = null;
        const isOpen = () => overlay.classList.contains('open');
        const call = (name, ...a) => (typeof window[name] === 'function') ? window[name](...a) : undefined;

        function buildItems() {
            const out = [];
            const curPage = ($('.page-section.active') || {}).id;
            $$('.nav-menu .nav-section').forEach(sec => {
                $$('.nav-item[data-page]', sec).forEach(b => {
                    const page = b.dataset.page, ic = $('.nav-icon', b);
                    out.push({
                        label: b.dataset.title, group: 'Go to',
                        icon: ic ? ic.outerHTML.replace('nav-icon', 'ico') : IC.tab,
                        key: PAGE_KEYS[page] ? 'g ' + PAGE_KEYS[page] : '',
                        current: curPage === 'page-' + page,
                        run: () => call('switchPage', page),
                    });
                });
            });
            $$('.page-section').forEach(sec => {
                const page = sec.id.replace('page-', '');
                const nav = $(`.nav-item[data-page="${page}"]`);
                const pTitle = nav ? nav.dataset.title : page;
                $$('.content-tabs .content-tab-btn', sec).forEach(btn => {
                    if (btn.closest('.modal-overlay') || btn.closest('[style*="display: none"], [style*="display:none"]')) return;
                    if (getComputedStyle(btn).display === 'none') return;                // e.g. LEMP tab in Simple mode
                    out.push({
                        label: `${pTitle} · ${btn.textContent.trim()}`, group: 'Tabs', icon: IC.tab, key: '',
                        run: () => {
                            call('switchPage', page);
                            let tries = 0;
                            const go = () => { if (sec.classList.contains('active') || tries++ > 20) btn.click(); else setTimeout(go, 60); };
                            setTimeout(go, 60);                                          // switchPage plays an exit animation first
                        },
                    });
                });
            });

            const banner = $('#offline-banner');
            const offline = banner && !banner.classList.contains('hidden');
            const simple = document.body.classList.contains('goldfish-mode');
            const sb = $('.sidebar');
            const collapsed = !!sb && sb.classList.contains('collapsed');
            out.push({ label: 'Reload all services', group: 'Actions', icon: IC.bolt, key: 'r', run: () => { const b = $('#tb-reload-btn'); if (b) b.click(); } });
            if (offline) out.push({ label: 'Retry connection', group: 'Actions', icon: IC.bolt, key: '', run: () => call('fetchStatus') });
            out.push({ label: simple ? 'Turn Simple mode off' : 'Turn Simple mode on', group: 'Actions', icon: IC.bolt, key: '', run: () => call('toggleGoldfishMode') });
            out.push({ label: collapsed ? 'Expand sidebar' : 'Collapse sidebar', group: 'Actions', icon: IC.bolt, key: '', run: () => call('toggleSidebar') });
            out.push({ label: 'Keyboard shortcuts', group: 'Actions', icon: IC.bolt, key: '?', run: () => call('openKbdOverlay') });
            return out;
        }

        // substring beats word-start beats loose subsequence
        function score(q, text) {
            const t = text.toLowerCase(), i = t.indexOf(q);
            if (i >= 0) return { s: i === 0 ? 0 : (/[\s·]/.test(t[i - 1]) ? 1 : 2), r: [[i, i + q.length]] };
            // loose (subsequence) match: only when the letters sit close together, so "dns" can't match "Turn Simple mode on"
            if (q.length < 2) return null;
            let p = 0; const r = [];
            for (const ch of q) { const j = t.indexOf(ch, p); if (j < 0) return null; r.push([j, j + 1]); p = j + 1; }
            const span = r[r.length - 1][0] - r[0][0];
            if (span > q.length * 2) return null;
            return { s: 3 + span, r };
        }

        function marked(label, ranges) {
            if (!ranges || !ranges.length) return esc(label);
            let o = '', p = 0;
            ranges.filter(([a]) => a < label.length).forEach(([a, b]) => { b = Math.min(b, label.length); o += esc(label.slice(p, a)) + '<mark>' + esc(label.slice(a, b)) + '</mark>'; p = b; });
            return o + esc(label.slice(p));
        }

        function render() {
            const q = input.value.trim().toLowerCase();
            let rows;
            if (!q) rows = items.map(it => ({ it, r: null }));
            else {
                const toks = q.split(/\s+/);
                rows = items.map((it, n) => {
                    let total = 0, ranges = null;
                    for (const tk of toks) {
                        const m = score(tk, it.label + ' ' + it.group);
                        if (!m) return null;
                        total += m.s; if (!ranges) ranges = m.r;
                    }
                    return { it, r: ranges, s: total, n };
                }).filter(Boolean).sort((a, b) => a.s - b.s || a.n - b.n);
            }
            shown = rows;
            $$('.fx-pal-item, .fx-pal-group, .fx-pal-empty', list).forEach(n => n.remove());

            if (!rows.length) {
                list.appendChild(h('div', 'fx-pal-empty', `No matches for “${esc(input.value.trim())}”`));
                hl.style.opacity = '0';
                return;
            }
            let lastGroup = null, n = 0;
            rows.forEach((row, idx) => {
                if (!q && row.it.group !== lastGroup) { lastGroup = row.it.group; list.appendChild(h('div', 'fx-pal-group', esc(row.it.group))); }
                const meta = (row.it.current ? '<span class="fx-pal-cur">current</span>' : '')
                    + (q && row.it.group !== 'Go to' ? `<span>${esc(row.it.group.toLowerCase())}</span>` : '')
                    + (row.it.key ? row.it.key.split(' ').map(k => `<kbd class="fx-key">${esc(k)}</kbd>`).join('') : '');
                const d = h('div', 'fx-pal-item', `${row.it.icon}<span class="fx-pal-label">${marked(row.it.label, row.r)}</span><span class="fx-pal-meta">${meta}</span>`);
                d.setAttribute('role', 'option');
                d.dataset.i = idx;
                d.style.setProperty('--n', Math.min(n++, 12));
                list.appendChild(d);
            });
            active = 0;
            setActive(0, true);
        }

        const rowEls = () => $$('.fx-pal-item', list);

        function setActive(i, instant) {
            const els = rowEls();
            if (!els.length) return;
            active = (i + els.length) % els.length;
            els.forEach((e, j) => { e.classList.toggle('is-active', j === active); e.setAttribute('aria-selected', String(j === active)); });
            const el = els[active];
            const top = el.offsetTop, bot = top + el.offsetHeight;
            if (top < list.scrollTop + 4) list.scrollTop = Math.max(0, top - (active === 0 ? 40 : 8));
            else if (bot > list.scrollTop + list.clientHeight - 4) list.scrollTop = bot - list.clientHeight + 8;
            if (instant) hl.style.transition = 'none';
            hl.style.height = el.offsetHeight + 'px';
            hl.style.transform = `translateY(${el.offsetTop}px)`;
            hl.style.opacity = '1';
            if (instant) { void hl.offsetWidth; hl.style.transition = ''; }
        }

        function open() {
            if (isOpen()) return;
            call('closeKbdOverlay');
            prevFocus = document.activeElement;
            items = buildItems();
            input.value = '';
            render();
            list.classList.add('is-first');                     // row entrance plays on open only…
            setTimeout(() => list.classList.remove('is-first'), 500);   // …never while typing
            overlay.classList.add('open');
            setTimeout(() => input.focus(), 30);
        }

        function close(restore = true) {
            if (!isOpen()) return;
            overlay.classList.remove('open');
            input.blur();
            if (restore && prevFocus && prevFocus.isConnected && typeof prevFocus.focus === 'function') prevFocus.focus({ preventScroll: true });
        }

        function runActive() {
            const row = shown[active];
            if (!row) return;
            close(false);
            setTimeout(() => row.it.run(), 160);                    // let the palette start closing first
        }

        input.addEventListener('input', render);
        overlay.addEventListener('keydown', (e) => {
            const k = e.key, ctrl = e.ctrlKey || e.metaKey;
            if (k === 'Escape') { e.preventDefault(); close(); }
            else if (k === 'ArrowDown' || (ctrl && (k === 'n' || k === 'j'))) { e.preventDefault(); setActive(active + 1); }
            else if (k === 'ArrowUp'   || (ctrl && (k === 'p' || k === 'k'))) { e.preventDefault(); setActive(active - 1); }
            else if (k === 'Tab') { e.preventDefault(); setActive(active + (e.shiftKey ? -1 : 1)); }
            else if (k === 'Enter') { e.preventDefault(); runActive(); }
            e.stopPropagation();
        });
        overlay.addEventListener('pointerdown', (e) => { if (e.target === overlay) close(); });
        list.addEventListener('pointermove', (e) => {
            const d = e.target.closest('.fx-pal-item');
            if (d && +d.dataset.i !== active) setActive(+d.dataset.i);
        });
        list.addEventListener('click', (e) => {
            const d = e.target.closest('.fx-pal-item');
            if (d) { setActive(+d.dataset.i); runActive(); }
        });

        window.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'k') {
                e.preventDefault();
                if (isOpen()) { close(); } else { open(); }
            } else if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey && !typing(e.target) && !isOpen()) {
                e.preventDefault();
                open();
            }
        }, true);

        const trigger = $('#sidebar-search');
        if (trigger) {
            const kbd = $('kbd', trigger);
            if (kbd) kbd.textContent = isMac ? '⌘ K' : 'Ctrl K';
            trigger.title = `Jump to… (${isMac ? '⌘' : 'Ctrl'} K)`;
            trigger.addEventListener('click', open);
        }
    }

    /* ═══════════════════════════════════════════════
       9. "g" KEY HINTS — shows each page's shortcut for a second
    ═══════════════════════════════════════════════ */
    function initKeyHints() {
        $$('.nav-item[data-page]').forEach(b => {
            const k = PAGE_KEYS[b.dataset.page];
            if (k) b.insertAdjacentHTML('beforeend', `<kbd class="fx-key">g ${k}</kbd>`);
        });
        let t = 0;
        document.addEventListener('keydown', (e) => {
            if (typing(e.target) || e.ctrlKey || e.metaKey || e.altKey) return;
            if (e.key === 'g') {
                document.body.classList.add('fx-gmode');
                clearTimeout(t);
                t = setTimeout(() => document.body.classList.remove('fx-gmode'), 1100);
            } else if (document.body.classList.contains('fx-gmode')) {
                document.body.classList.remove('fx-gmode');
            }
        });
    }


    /* ═══════════════════════════════════════════════
       10. DASHBOARD TELEMETRY
       Fills the System panel. app.js already writes the numbers into the
       metric-* elements on every poll; we add history sparklines, the
       temperature tile (mirrored from the header pill) and an "updated Ns
       ago" heartbeat. Samples live in sessionStorage so a refresh keeps the graph.
    ═══════════════════════════════════════════════ */
    function initTelemetry() {
        const WINDOW = 6 * 60 * 1000;                    // graph spans the last 6 minutes
        const KEY = 'fx:hist:v1';
        const hist = { cpu: [], ram: [] };
        try {
            const saved = JSON.parse(sessionStorage.getItem(KEY) || 'null');
            if (saved) ['cpu', 'ram'].forEach(k => { hist[k] = (saved[k] || []).filter(p => Date.now() - p[0] < WINDOW); });
        } catch (_) {}
        const persist = () => { try { sessionStorage.setItem(KEY, JSON.stringify(hist)); } catch (_) {} };

        const NS = 'http://www.w3.org/2000/svg';
        const mk = (n, a) => { const e = document.createElementNS(NS, n); for (const k in a) e.setAttribute(k, a[k]); return e; };

        function draw(key) {
            const svg = $(`.fx-spark[data-spark="${key}"]`);
            if (!svg) return;
            const pts = hist[key].filter(p => Date.now() - p[0] < WINDOW);
            svg.textContent = '';
            if (!pts.length) return;
            const vals = pts.map(p => p[1]);
            let lo = 0, hi = Math.max(20, Math.ceil(Math.max(...vals) * 1.25));      // CPU: 0 → a bit above the peak
            if (key === 'ram') {                                                      // RAM: zoom in so trends are visible
                lo = Math.max(0, Math.min(...vals) - 6); hi = Math.min(100, Math.max(...vals) + 6);
                if (hi - lo < 12) { const mid = (hi + lo) / 2; lo = Math.max(0, mid - 6); hi = Math.min(100, lo + 12); }
            }
            const W = 120, H = 40, pad = 4, now = Date.now();
            const X = (t) => W * (1 - (now - t) / WINDOW);
            const Y = (v) => H - pad - ((v - lo) / (hi - lo)) * (H - pad * 2);
            svg.appendChild(mk('line', { class: 'fx-spark-base', x1: 0, x2: W, y1: Y(lo), y2: Y(lo) }));
            if (pts.length > 1) {
                let d = `M${X(pts[0][0]).toFixed(1)} ${Y(pts[0][1]).toFixed(1)}`;
                for (let i = 1; i < pts.length; i++) {                                // gentle curve through the samples
                    const x0 = X(pts[i - 1][0]), y0 = Y(pts[i - 1][1]), x1 = X(pts[i][0]), y1 = Y(pts[i][1]), mx = (x0 + x1) / 2;
                    d += ` C${mx.toFixed(1)} ${y0.toFixed(1)} ${mx.toFixed(1)} ${y1.toFixed(1)} ${x1.toFixed(1)} ${y1.toFixed(1)}`;
                }
                svg.appendChild(mk('path', { class: 'fx-spark-line', d }));
            }
            const last = pts[pts.length - 1];
            svg.appendChild(mk('path', { class: 'fx-spark-dot', d: `M${X(last[0]).toFixed(1)} ${Y(last[1]).toFixed(1)} h0.01` }));
        }

        let lastPoll = 0;
        const updated = $('#fx-updated'), label = updated && updated.lastElementChild, beat = updated && $('.fx-beat', updated);
        const ago = () => {
            if (!updated || !lastPoll) return;
            const s = Math.round((Date.now() - lastPoll) / 1000);
            const txt = s < 4 ? 'just now' : s < 60 ? `${s}s ago` : s < 3600 ? `${Math.floor(s / 60)}m ago` : `${Math.floor(s / 3600)}h ago`;
            label.textContent = (s > 35 ? 'last update ' : 'updated ') + txt;
            updated.classList.toggle('is-stale', s > 35);
        };
        const pulse = () => {
            lastPoll = Date.now();
            ago();
            if (beat && !reduce()) { beat.classList.remove('is-beat'); void beat.offsetWidth; beat.classList.add('is-beat'); }
        };
        if (beat) beat.addEventListener('animationend', () => beat.classList.remove('is-beat'));
        if (updated) { updated.classList.add('is-idle'); setInterval(ago, 5000); }

        // each poll rewrites #metric-*-val → that mutation is our "new sample" signal
        ['cpu', 'ram'].forEach(key => {
            const val = $(`#metric-${key}-val`), tile = $(`#fx-m-${key}`), pill = $(`#metric-${key}-state`);
            if (!val) return;
            const onSample = () => {
                const v = parseFloat(val.textContent);
                if (isNaN(v)) return;
                const now = Date.now(), arr = hist[key];
                if (arr.length && now - arr[arr.length - 1][0] < 1500) arr[arr.length - 1] = [now, v];
                else arr.push([now, v]);
                while (arr.length > 60 || (arr.length && now - arr[0][0] > WINDOW)) arr.shift();
                if (tile && pill) tile.dataset.state = pill.classList.contains('is-danger') ? 'danger' : pill.classList.contains('is-warning') ? 'warning' : '';
                if (updated) updated.classList.remove('is-idle');
                pulse(); draw(key); persist();
            };
            new MutationObserver(onSample).observe(val, { childList: true, characterData: true, subtree: true });
            draw(key);                                                    // restore the graph after a refresh
        });

        // temperature tile mirrors the header pill (app.js only writes the pill)
        const pv = $('#pill-temp-val'), pp = $('#pill-temp-pip'), tv = $('#fx-temp-val'), tile = $('#fx-m-temp');
        if (pv && tv && tile) {
            const unit = $('#fx-temp-unit'), st = $('#fx-temp-state'), bar = $('#fx-temp-bar'), note = $('#fx-temp-note');
            const sync = () => {
                const n = parseFloat(pv.textContent);
                if (isNaN(n)) {
                    tv.textContent = pv.textContent.trim() && pv.textContent.trim() !== '—' ? pv.textContent.trim() : '—';
                    unit.style.display = 'none';
                    bar.style.width = '0%'; bar.className = '';
                    st.textContent = ''; tile.dataset.state = 'na';
                    note.textContent = /n\/?a/i.test(tv.textContent) ? 'No sensor reported by this host' : 'Waiting for sensor…';
                    return;
                }
                unit.style.display = '';
                tv.textContent = n.toFixed(1);
                const danger = pp && pp.classList.contains('is-danger'), warn = pp && pp.classList.contains('is-warning');
                tile.dataset.state = danger ? 'danger' : warn ? 'warning' : '';
                st.className = 'metric-state-pill ' + (danger ? 'is-danger' : warn ? 'is-warning' : 'is-normal');
                st.textContent = danger ? 'Hot' : warn ? 'Warm' : 'Normal';
                bar.style.width = clamp((n - 30) / 60 * 100) + '%';
                bar.className = danger ? 'is-danger' : warn ? 'is-warning' : '';
                note.textContent = 'CPU package temperature';
            };
            new MutationObserver(sync).observe(pv, { childList: true, characterData: true, subtree: true });
            if (pp) new MutationObserver(sync).observe(pp, { attributes: true, attributeFilter: ['class'] });
            sync();
        }
    }

    /* ═══════════════════════════════════════════════
       11. MAKE SITE — stack icons, sticky action bar, output status
    ═══════════════════════════════════════════════ */
    function initMakeSite() {
        const page = $('#page-makesite');
        if (!page) return;

        // icons for the stack picker
        const svg = (inner) => `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`;
        const ICONS = {
            lamp:      svg('<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>'),
            lemp:      svg('<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>'),
            wordpress: svg('<rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/>'),
            static:    svg('<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>'),
            proxy:     svg('<polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>'),
        };
        $$('.content-tabs .content-tab-btn', page).forEach(b => {
            const ic = ICONS[b.dataset.tab];
            if (ic && !b.querySelector('svg')) b.insertAdjacentHTML('afterbegin', ic);
        });

        // output header: live state
        $$('.terminal-label', page).forEach(l => {
            if (!l.querySelector('.fx-term-state')) l.appendChild(h('span', 'fx-term-state'));
        });

        // sticky action bar with a "what's missing" hint
        const forms = $$('form', page);
        const hints = [];
        forms.forEach(form => {
            const btn = form.querySelector('button[type="submit"]');
            if (!btn || btn.closest('.fx-actionbar')) return;
            const bar = h('div', 'fx-actionbar');
            const hint = h('span', 'fx-hint');
            btn.parentNode.insertBefore(bar, btn);
            bar.appendChild(btn);
            bar.appendChild(hint);
            hints.push({ form, hint });
        });

        // a field counts unless something INSIDE its form hides it (Simple mode, etc.).
        // offsetParent can't be used: it's null for everything while the whole page is hidden.
        const shown = (f, form) => { for (let n = f; n && n !== form; n = n.parentElement) if (getComputedStyle(n).display === 'none') return false; return true; };
        const update = () => hints.forEach(({ form, hint }) => {
            const missing = $$('[required]', form).filter(f => shown(f, form) && !String(f.value || '').trim());
            if (missing.length) {
                const names = missing.map(f => { const g = f.closest('.form-group'); const l = g && $('label', g); return l ? l.childNodes[0].textContent.trim() : f.name; });
                hint.classList.remove('is-ready');
                hint.textContent = missing.length === 1 ? `${names[0]} is required` : `${missing.length} required fields left`;
            } else {
                hint.classList.add('is-ready');
                hint.innerHTML = 'Ready · <kbd>Ctrl</kbd> <kbd>↵</kbd> to run';
            }
        });
        page.addEventListener('input', update);
        page.addEventListener('change', update);
        forms.forEach(f => f.addEventListener('reset', () => setTimeout(update, 0)));
        new MutationObserver(update).observe(document.body, { attributes: true, attributeFilter: ['class'] });   // Simple mode changes which fields count
        update();

        // Ctrl/⌘ + Enter submits the form you're in
        page.addEventListener('keydown', (e) => {
            if (e.key !== 'Enter' || !(e.ctrlKey || e.metaKey)) return;
            const form = e.target.closest && e.target.closest('form');
            if (form && typeof form.requestSubmit === 'function') { e.preventDefault(); form.requestSubmit(); }
        });
    }

    /* ═══════════════════════════════════════════════
       BOOT — each module isolated
    ═══════════════════════════════════════════════ */
    function boot() {
        const run = (fn) => { try { fn(); } catch (err) { console.warn('[fx]', fn.name, err); } };
        run(initSplash);
        run(buildHealth);        // before arming, so the tile animates in with everything else
        run(enhanceCards);
        run(watchBadges);
        run(initPills);
        run(bindSpotlight);
        run(initGliders);
        run(initWaybar);
        run(initBanner);
        run(initSelects);
        run(initPalette);
        run(initKeyHints);
        run(initTelemetry);
        run(initMakeSite);
        run(wrapSwitchPage);
        run(watchGoldfish);
        run(initEntrance);       // last: arms the first page
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
    else boot();
})();
