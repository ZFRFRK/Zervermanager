/* ─────────────────────────────────────────────────────────────────────────
   Zervermanager — web terminal (term.js v1)
   A real root shell served by /api/terminal/*:
     · output streams in via a long-poll loop (survives page reloads — the
       shell keeps running server-side and the buffer replays on reconnect)
     · a hidden textarea captures typing, pasting (Ctrl+V / right-click /
       the Paste button) and control keys, which are forwarded verbatim to
       the PTY — the shell itself echoes, so what you see is what it does
     · a small ANSI renderer turns the PTY stream into colored scrollback
       (SGR colors, CR overwrite for progress bars, backspace)
   No dependencies. Fails soft in dev previews (no backend → notice).
   ───────────────────────────────────────────────────────────────────────── */
(() => {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const scroll = $('term-scroll'), out = $('term-out');
    const caret = document.querySelector('.zx-term-caret');
    const input = $('term-input'), stateEl = $('term-state'), dot = $('term-dot'), title = $('term-title');
    if (!scroll || !out || !input) return;

    const MAX_LINES = 1500;
    let cursor = 0;              // server buffer offset we have consumed
    let pumpOn = false;
    let alive = false;
    const decoder = new TextDecoder('utf-8');   // streaming: handles UTF-8 split across chunks

    /* ── ANSI → scrollback ──────────────────────────────────────────────── */
    // xterm 256-color palette: 16 base + 6×6×6 cube + 24 grays.
    const PAL = (() => {
        const base = ['2e3440', 'cc4444', '5fb26a', 'd8a24a', '5d9dd6', 'a37fd6', '54b8bb', 'c8cfdd',
                      '6d7a8d', 'f07885', '8bd49c', 'f2c879', '33a6ff', '8b72f8', '55d6e8', 'e8edf5'];
        const p = base.slice();
        const h = (v) => Math.round(v).toString(16).padStart(2, '0');
        for (let r = 0; r < 6; r++) for (let g = 0; g < 6; g++) for (let b = 0; b < 6; b++)
            p.push(h(47 + r * 42) + h(47 + g * 42) + h(47 + b * 42));
        for (let i = 0; i < 24; i++) {
            const v = h(8 + i * 10);
            p.push(v + v + v);
        }
        return p;                                  // index = 0..255
    })();
    const hex = (c) => '#' + c;

    // SGR state + direct-DOM rendering: one div per line, spans per style run.
    let sgr = { fg: null, bg: null, bold: false, dim: false, italic: false, underline: false };
    let curDiv = null;      // the <div> receiving output right now (null = line finalized by \n)
    let lastSpan = null;    // last span appended to curDiv, for batching same-style text
    let lastStyleKey = '';
    let crPending = false;  // saw \r: next char rewrites the line from column 0

    const styleKey = () => (sgr.fg || '') + '|' + (sgr.bg || '') + '|' + (sgr.bold ? 'b' : '') +
        (sgr.dim ? 'd' : '') + (sgr.italic ? 'i' : '') + (sgr.underline ? 'u' : '');

    function applySGR(params) {
        const p = params === '' ? ['0'] : params.split(';');
        for (let i = 0; i < p.length; i++) {
            const n = parseInt(p[i] || '0', 10);
            if (n === 0) { sgr = { fg: null, bg: null, bold: false, dim: false, italic: false, underline: false }; }
            else if (n === 1) sgr.bold = true;
            else if (n === 2) sgr.dim = true;
            else if (n === 3) sgr.italic = true;
            else if (n === 4) sgr.underline = true;
            else if (n === 22) { sgr.bold = false; sgr.dim = false; }
            else if (n === 23) sgr.italic = false;
            else if (n === 24) sgr.underline = false;
            else if (n === 39) sgr.fg = null;
            else if (n === 49) sgr.bg = null;
            else if ((n >= 30 && n <= 37) || (n >= 90 && n <= 97)) sgr.fg = PAL[n >= 90 ? n - 90 + 8 : n - 30];
            else if ((n >= 40 && n <= 47) || (n >= 100 && n <= 107)) sgr.bg = PAL[n >= 100 ? n - 100 + 8 : n - 40];
            else if (n === 38 || n === 48) {
                const isFg = n === 38, mode = p[i + 1];
                let col = null;
                if (mode === '5') { col = PAL[parseInt(p[i + 2] || '0', 10)] || null; i += 2; }
                else if (mode === '2') {
                    col = [p[i + 2], p[i + 3], p[i + 4]]
                        .map(v => Math.max(0, Math.min(255, parseInt(v || '0', 10))))
                        .map(v => v.toString(16).padStart(2, '0')).join('');
                    i += 4;
                }
                if (col) { if (isFg) sgr.fg = col; else sgr.bg = col; }
            }
        }
        lastStyleKey = styleKey();
    }

    function getCur() {
        if (!curDiv) {
            curDiv = document.createElement('div');
            curDiv.className = 'zx-term-line';
            out.appendChild(curDiv);
            lastSpan = null; lastStyleKey = styleKey();
            while (out.children.length > MAX_LINES) out.firstChild.remove();
        }
        return curDiv;
    }

    function pushText(t) {
        for (const ch of t) {
            if (ch === '\n') { crPending = false; curDiv = null; lastSpan = null; continue; }  // finalize line
            if (ch === '\r') { crPending = true; continue; }   // cursor to col 0 — erase only if chars follow
            if (ch === '\b') {                                              // backspace: erase one char
                crPending = false;
                const d = getCur();
                if (lastSpan && lastSpan.textContent.length) {
                    lastSpan.textContent = lastSpan.textContent.slice(0, -1);
                    if (!lastSpan.textContent) { lastSpan.remove(); lastSpan = null; }
                }
                continue;
            }
            if (ch === '\x07') continue;                                    // bell
            if (crPending) {                                                // a char after \r overwrites the line
                getCur().textContent = '';
                lastSpan = null;
                crPending = false;
            }
            const txt = ch === '\t' ? '    ' : ch;
            const d = getCur();
            if (lastSpan && styleKey() === lastStyleKey) {
                lastSpan.textContent += txt;                                // batch same-style runs
            } else {
                const sp = document.createElement('span');
                let css = '';
                if (sgr.fg) css += 'color:' + hex(sgr.fg) + ';';
                else if (sgr.dim) css += 'color:var(--text-dim);';
                if (sgr.bg) css += 'background:' + hex(sgr.bg) + ';';
                if (sgr.bold) css += 'font-weight:700;';
                if (sgr.italic) css += 'font-style:italic;';
                if (sgr.underline) css += 'text-decoration:underline;';
                if (css) sp.style.cssText = css;
                sp.textContent = txt;
                d.appendChild(sp);
                lastSpan = sp; lastStyleKey = styleKey();
            }
        }
    }

    // escape sequences: CSI … final | OSC … BEL/ST | two-char escapes
    const ESC_RE = /\x1b\[([\?<>=0-9;]*)([A-Za-z])|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[^\[\\\x1b]/g;
    let carry = '';   // a trailing partial escape sequence waits for the next chunk

    function feed(text) {
        carry += text;
        let stop = carry.length;
        const tail = carry.match(/\x1b(?:\[[\?<>=0-9;]*[A-Za-z]?|\][^\x07]*)?$/);
        if (tail && tail[0].length) stop = carry.length - tail[0].length;
        const chunkText = carry.slice(0, stop);
        carry = carry.slice(stop);
        if (!chunkText) return;

        let last = 0, m;
        ESC_RE.lastIndex = 0;
        while ((m = ESC_RE.exec(chunkText))) {
            if (m.index > last) pushText(chunkText.slice(last, m.index));
            last = ESC_RE.lastIndex;
            if (m[2] === 'm') applySGR(m[1]);
            else if (m[2] === 'J') {                       // ED: clear screen (from "clear"/dmesg)
                if (curDiv) { curDiv = null; }
                if (curDiv === null) { /* next output starts a new line */ }
            }
            // other CSI (cursor addressing etc.) is dropped: this is a scrollback, not a grid
        }
        if (last < chunkText.length) pushText(chunkText.slice(last));
    }

    function flush() {
        placeCaret();
        stickScroll();
    }

    // the caret lives at the end of the current line (right after the prompt) —
    // re-placed after every chunk, since new spans are appended after it
    function placeCaret() {
        if (!caret) return;
        if (devFailed) { caret.style.display = 'none'; return; }
        (curDiv || out).appendChild(caret);
        caret.style.display = '';
    }

    let stick = true;
    scroll.addEventListener('scroll', () => {
        stick = scroll.scrollTop + scroll.clientHeight >= scroll.scrollHeight - 24;
    });
    function stickScroll() { if (stick) scroll.scrollTop = scroll.scrollHeight; }

    /* ── server connection ─────────────────────────────────────────────── */
    const setState = (txt, cls) => {
        stateEl.textContent = txt;
        stateEl.className = 'zx-term-state' + (cls ? ' ' + cls : '');
        dot.className = 'zx-term-dot' + (cls ? ' ' + cls : '');
    };

    async function pump() {
        if (pumpOn) return;
        pumpOn = true;
        while (pumpOn) {
            try {
                const res = await fetch('/api/terminal/stream?after=' + cursor, { cache: 'no-store' });
                if (!res.ok) throw new Error(res.status);
                const j = await res.json();
                if (j && j.ok) {
                    cursor = j.next || cursor;
                    alive = !!j.alive;
                    if (j.data) {
                        feed(decoder.decode(Uint8Array.from(atob(j.data), c => c.charCodeAt(0)), { stream: true }));
                        flush();
                    }
                    setState(alive ? 'connected' : 'shell exited — press Restart', alive ? 'ok' : 'err');
                }
            } catch (e) {
                pumpOn = false;
                devFail('Lost connection to the terminal service.');
                return;
            }
        }
    }

    const send = (data) => {
        if (!data) return;
        return fetch('/api/terminal/input', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ data })
        }).catch(() => { pumpOn = false; devFail('Lost connection to the terminal service.'); });
    };

    let devFailed = false;
    function devFail(msg) {
        if (devFailed) return;
        devFailed = true;
        setState('offline', 'err');
        if (caret) caret.style.display = 'none';
        const note = document.createElement('div');
        note.className = 'zx-term-devnote';
        note.textContent = msg + ' The live shell only runs under the Zervermanager server itself.';
        out.appendChild(note);
    }

    function note(msg) {
        let n = document.querySelector('.zx-term-note');
        if (!n) {
            n = document.createElement('div');
            n.className = 'zx-term-note';
            $('term-foot').prepend(n);
        }
        n.textContent = msg;
        clearTimeout(note.t);
        note.t = setTimeout(() => n.remove(), 4200);
    }

    const focusInput = () => input.focus({ preventScroll: true });

    function sendSize() {
        const measure = document.createElement('span');
        measure.textContent = 'x'.repeat(40);
        measure.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;font:13px/1.45 var(--font-mono)';
        document.body.appendChild(measure);
        const w = measure.getBoundingClientRect().width / 40;
        measure.remove();
        const cols = Math.max(20, Math.min(500, Math.floor(scroll.clientWidth / w) - 1));
        return fetch('/api/terminal/size', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cols, rows: 40 })
        }).catch(() => {});
    }

    async function boot() {
        let st = null;
        try {
            const res = await fetch('/api/terminal/state', { cache: 'no-store' });
            st = await res.json();
        } catch (e) { st = null; }
        if (!st || !st.ok) { devFail('Terminal service not reachable — restart the server with the updated zervermanager.py (the terminal lives in the Python backend).'); return; }
        if (st.error) { setState('unsupported', 'err'); devFail(st.error); return; }
        sendSize();
        if (!st.alive) {
            try {
                const r = await fetch('/api/terminal/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
                const j = await r.json();
                if (!j.ok) { setState('error', 'err'); devFail(j.error || 'Could not start the shell.'); return; }
            } catch (e) { setState('error', 'err'); devFail('Could not start the shell.'); return; }
        }
        setState('connected', 'ok');
        title.textContent = 'shell — ' + location.hostname;
        pump();
        focusInput();
    }

    /* ── input capture ─────────────────────────────────────────────────── */
    scroll.addEventListener('mousedown', (e) => { if (getSelection().isCollapsed) { e.preventDefault(); focusInput(); } });
    scroll.addEventListener('mouseup', () => { if (getSelection().isCollapsed) focusInput(); });

    input.addEventListener('paste', (e) => {          // the reliable paste path on plain HTTP
        const text = (e.clipboardData || window.clipboardData).getData('text');
        if (text) {
            e.preventDefault();
            send(text.replace(/\r\n?/g, '\n').replace(/\n/g, '\r'));
            stick = true;
        }
    });
    input.addEventListener('input', () => {           // typing / right-click paste fallback
        if (input.value) {
            const v = input.value;
            input.value = '';
            send(v.replace(/\r\n?/g, '\n').replace(/\n/g, '\r'));
        }
    });
    input.addEventListener('keydown', (e) => {
        const seq = {
            Enter: '\r', Backspace: '\x7f', Tab: e.shiftKey ? '\x1b[Z' : '\t',
            ArrowUp: '\x1b[A', ArrowDown: '\x1b[B', ArrowRight: '\x1b[C', ArrowLeft: '\x1b[D',
            Home: '\x1b[H', End: '\x1b[F', PageUp: '\x1b[5~', PageDown: '\x1b[6~', Delete: '\x1b[3~'
        }[e.key];
        if (e.ctrlKey && !e.shiftKey && e.key.length === 1) {
            const k = e.key.toLowerCase();
            if (k === 'v') return;                                          // paste event handles it
            if (k === 'c') {
                if (!getSelection().isCollapsed) return;                    // let the browser copy
                e.preventDefault(); send('\x03'); return;
            }
            if (k === 'l') { e.preventDefault(); send('\x0c'); return; }
            if (k === 'd') { e.preventDefault(); send('\x04'); return; }
            if (k === 'u') { e.preventDefault(); send('\x15'); return; }
            if (k === 'w') { e.preventDefault(); send('\x17'); return; }
            const code = k.charCodeAt(0) - 96;
            if (code >= 1 && code <= 26) { e.preventDefault(); send(String.fromCharCode(code)); return; }
        }
        if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'c') {       // terminal-style copy
            const sel = getSelection().toString();
            if (sel) { navigator.clipboard?.writeText?.(sel).catch(() => {}); e.preventDefault(); }
            return;
        }
        if (seq !== undefined) {
            e.preventDefault();
            send(seq);
            if (e.key === 'Enter') stick = true;
        }
    });

    /* ── toolbar ───────────────────────────────────────────────────────── */
    $('term-paste-btn')?.addEventListener('click', async () => {
        focusInput();
        try {
            const text = await navigator.clipboard.readText();
            if (text) send(text.replace(/\r\n?/g, '\n').replace(/\n/g, '\r'));
            else note('Clipboard is empty.');
        } catch (e) {
            note('Clipboard blocked here — use Ctrl+V inside the terminal instead.');
        }
    });
    $('term-ctrlc-btn')?.addEventListener('click', () => { send('\x03'); focusInput(); });
    $('term-clear-btn')?.addEventListener('click', () => {
        out.textContent = ''; curDiv = null; lastSpan = null; placeCaret(); focusInput();
    });
    $('term-restart-btn')?.addEventListener('click', async () => {
        setState('restarting…');
        try {
            await fetch('/api/terminal/stop', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
            const r = await fetch('/api/terminal/start', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
            const j = await r.json();
            if (!j.ok) { setState('error', 'err'); note(j.error || 'Could not restart the shell.'); return; }
            out.textContent = ''; curDiv = null; lastSpan = null;
            placeCaret();
            cursor = 0;
            pump();
        } catch (e) { setState('offline', 'err'); note('Lost connection to the terminal service.'); }
    });

    // boot when the Terminal page is opened; focus it
    new MutationObserver(() => {
        if (document.getElementById('page-terminal')?.classList.contains('active')) {
            if (!devFailed) boot();
            setTimeout(focusInput, 500);
        }
    }).observe(document.getElementById('page-terminal') || document.body,
               { attributes: true, attributeFilter: ['class'] });

    // stray keys on the terminal page land in the shell
    document.addEventListener('keydown', (e) => {
        if (!document.getElementById('page-terminal')?.classList.contains('active')) return;
        if (e.target === input || e.target.closest('input, textarea, select, button, [contenteditable]')) return;
        if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) focusInput();
    });
})();
