/* Barq console: shared helpers. Load this in <head> before the page script. */
(function () {
  'use strict';

  /* ---------- storage (safe when blocked) ---------- */
  const store = {
    get(key, fallback) {
      try { const v = localStorage.getItem(key); return v === null ? fallback : v; } catch (e) { return fallback; }
    },
    set(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* ignore */ } },
  };

  /* ---------- theme (runs immediately to avoid a flash) ---------- */
  const mq = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  function resolveTheme() {
    const pref = store.get('barq.theme', 'auto');
    if (pref === 'light' || pref === 'dark') return pref;
    return mq && mq.matches ? 'dark' : 'light';
  }
  function applyTheme() { document.documentElement.setAttribute('data-mode', resolveTheme()); }
  applyTheme();
  if (mq && mq.addEventListener) {
    mq.addEventListener('change', () => { if (store.get('barq.theme', 'auto') === 'auto') applyTheme(); });
  }

  /* ---------- small helpers ---------- */
  const $ = (id) => document.getElementById(id);

  function escapeHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function fmtDuration(s) {
    if (s === null || s === undefined) return '\u2014';
    if (s < 60) return Math.round(s) + 's';
    return Math.floor(s / 60) + 'm ' + Math.round(s % 60) + 's';
  }
  function fmtTime(iso) {
    if (!iso) return '\u2014';
    const d = new Date(iso);
    if (isNaN(d)) return '\u2014';
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }
  function errorMessage(data, fallback) {
    if (!data) return fallback;
    if (typeof data.detail === 'string') return data.detail;
    if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg).join('; ');
    return fallback;
  }

  /* ---------- API ---------- */
  function apiBase() { return String(store.get('barq.api', 'http://localhost:8000')).replace(/\/$/, ''); }
  async function api(path, opts) {
    const res = await fetch(apiBase() + path, opts);
    let data = null;
    try { data = await res.json(); } catch (e) { /* not json */ }
    if (!res.ok) {
      const err = new Error(errorMessage(data, 'HTTP ' + res.status));
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  /* ---------- toasts ---------- */
  function toast(message, kind, ms) {
    let box = $('toasts');
    if (!box) {
      box = document.createElement('div');
      box.id = 'toasts';
      box.className = 'toasts';
      box.setAttribute('role', 'status');
      document.body.appendChild(box);
    }
    const el = document.createElement('div');
    el.className = 'toast' + (kind === 'err' ? ' err' : '');
    el.textContent = message;
    box.appendChild(el);
    requestAnimationFrame(() => requestAnimationFrame(() => el.classList.add('in')));
    const remove = () => {
      el.classList.remove('in');
      setTimeout(() => el.remove(), 320);
    };
    setTimeout(remove, ms || (kind === 'err' ? 8000 : 5000));
    el.addEventListener('click', remove);
  }

  /* ---------- overlays ---------- */
  function openOverlay(el) {
    el._prev = document.activeElement;
    el.classList.add('open');
    el.setAttribute('aria-hidden', 'false');
    const first = el.querySelector('[data-autofocus]') || el.querySelector('input, select, textarea, button');
    setTimeout(() => { if (first) first.focus(); }, 60);
  }
  function closeOverlay(el) {
    el.classList.remove('open');
    el.setAttribute('aria-hidden', 'true');
    if (el._prev && el._prev.focus) el._prev.focus();
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') document.querySelectorAll('.overlay.open').forEach(closeOverlay);
  });
  document.addEventListener('mousedown', (e) => {
    if (e.target.classList && e.target.classList.contains('overlay')) closeOverlay(e.target);
  });

  /* ---------- controls ---------- */
  function segmented(root, value, onChange) {
    const buttons = Array.from(root.querySelectorAll('button'));
    const set = (v) => buttons.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.value === String(v))));
    set(value);
    root.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b || !root.contains(b)) return;
      set(b.dataset.value);
      onChange(b.dataset.value);
    });
    return set;
  }

  const reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function tween(el, to, ms) {
    const from = parseFloat(el.dataset.v);
    el.dataset.v = String(to);
    if (isNaN(from) || from === to || reduceMotion) { el.textContent = String(to); return; }
    const start = performance.now();
    const dur = ms || 450;
    function step(now) {
      const t = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - t, 3);
      el.textContent = String(Math.round(from + (to - from) * eased));
      if (t < 1) requestAnimationFrame(step); else el.textContent = String(to);
    }
    requestAnimationFrame(step);
  }

  /* ---------- top bar ---------- */
  function setConn(state, text) {
    const pill = $('conn');
    if (!pill) return;
    pill.dataset.state = state;
    $('connText').textContent = text;
  }

  function buildChrome(active) {
    const host = $('chrome');
    if (!host) return;
    host.className = 'topbar';
    host.innerHTML = `
      <div class="topbar-in">
        <a class="brand" href="dashboard.html" aria-label="Barq home">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true"><path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z"/></svg>
          <span>Barq</span><small>Incident resolution</small>
        </a>
        <nav class="nav" aria-label="Main">
          <a href="dashboard.html" ${active === 'pipeline' ? 'aria-current="page"' : ''}>Pipeline</a>
          <a href="kb.html" ${active === 'kb' ? 'aria-current="page"' : ''}>Knowledge base</a>
          <a href="approvals.html" ${active === 'approvals' ? 'aria-current="page"' : ''}>Approvals <span class="badge" id="approvalCount" hidden></span></a>
        </nav>
        <div class="topbar-right">
          <span class="conn" id="conn" data-state="idle"><i></i><span id="connText">Connecting</span></span>
          <button class="icon-btn" id="themeBtn" type="button" aria-label="Switch light or dark theme" title="Switch theme">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/></svg>
          </button>
          <button class="icon-btn" id="settingsBtn" type="button" aria-label="Connection settings" title="Connection settings">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/></svg>
          </button>
        </div>
      </div>`;

    // how many runs wait for a human decision (S3.4)
    api('/api/v1/approvals').then((data) => {
      const badge = $('approvalCount');
      badge.textContent = String(data.total || 0);
      badge.hidden = !data.total;
    }).catch(() => {});

    $('themeBtn').addEventListener('click', () => {
      store.set('barq.theme', resolveTheme() === 'dark' ? 'light' : 'dark');
      applyTheme();
    });

    const dlg = document.createElement('div');
    dlg.className = 'overlay';
    dlg.id = 'settingsOverlay';
    dlg.setAttribute('aria-hidden', 'true');
    dlg.innerHTML = `
      <div class="dialog" role="dialog" aria-modal="true" aria-labelledby="settingsTitle">
        <h3 id="settingsTitle">Connection</h3>
        <div class="field">
          <label for="apiBaseInput">API base URL</label>
          <input class="input" id="apiBaseInput" type="text" placeholder="http://localhost:8000" data-autofocus />
          <div class="hint">Where the Barq API is running. Saved in this browser.</div>
        </div>
        <div class="dialog-actions">
          <div class="right">
            <button class="btn" type="button" id="settingsCancel">Cancel</button>
            <button class="btn btn-primary" type="button" id="settingsSave">Save</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(dlg);

    $('settingsBtn').addEventListener('click', () => {
      $('apiBaseInput').value = apiBase();
      openOverlay(dlg);
    });
    $('settingsCancel').addEventListener('click', () => closeOverlay(dlg));
    function save() {
      const v = $('apiBaseInput').value.trim().replace(/\/$/, '');
      if (!/^https?:\/\//i.test(v)) { toast('Enter a full URL such as http://localhost:8000', 'err'); return; }
      store.set('barq.api', v);
      closeOverlay(dlg);
      window.dispatchEvent(new Event('barq:api-changed'));
    }
    $('settingsSave').addEventListener('click', save);
    $('apiBaseInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') save(); });
  }

  window.Barq = {
    $, store, escapeHtml, fmtDuration, fmtTime, errorMessage,
    api, apiBase, toast, openOverlay, closeOverlay, segmented, tween, setConn, buildChrome,
  };
})();
