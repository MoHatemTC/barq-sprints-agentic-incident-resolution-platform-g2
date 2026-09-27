/* Knowledge base page logic. Endpoints are unchanged:
   GET  /api/v1/kb/articles
   POST /api/v1/kb/articles
   POST /api/v1/kb/articles/upload
   POST /api/v1/dashboard/kb-sync */
(function () {
  'use strict';
  const B = window.Barq;
  const { $, escapeHtml, api, toast, store, buildChrome, setConn, tween, segmented, openOverlay, closeOverlay } = B;

  buildChrome('kb');

  const LIMITS = { titleMin: 5, titleMax: 160, bodyMin: 30, bodyMax: 50000, serviceMin: 2, serviceMax: 60, fileMaxBytes: 200000 };
  const STATUS = {
    indexed: ['ok', 'Indexed'],
    pending_sync: ['warn', 'Pending sync'],
    not_indexed: ['violet', 'Not indexed'],
    unknown: ['plain', 'Unknown'],
  };
  const FIELDS = ['title', 'category', 'security_level', 'service', 'body'];
  const SERVICE_RE = /^[a-z0-9]+(?:[-_][a-z0-9]+)*$/;
  const CHEVRON = '<svg class="chev" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';

  let articles = [];
  let options = { categories: ['software', 'network', 'hardware', 'identity'], security_levels: ['public', 'internal', 'confidential'] };

  const savedSize = store.get('barq.kbsize', '10');
  const state = {
    size: ['10', '20', '50', 'all'].includes(savedSize) ? savedSize : '10',
    visible: 0,
    q: '',
    status: '',
    cat: '',
    open: new Set(),
    firstLoad: true,
    sourceAvailable: true,
    sourceError: '',
  };
  const pageSize = () => (state.size === 'all' ? Infinity : parseInt(state.size, 10));

  /* ---------- list ---------- */
  const list = $('list');
  list.addEventListener('animationend', (e) => {
    if (e.target.classList && e.target.classList.contains('enter')) e.target.classList.remove('enter');
  });
  list.addEventListener('click', (e) => {
    const head = e.target.closest('.row-head');
    if (!head) return;
    const row = head.closest('.row');
    const open = row.classList.toggle('open');
    head.setAttribute('aria-expanded', String(open));
    if (open) state.open.add(row.dataset.id); else state.open.delete(row.dataset.id);
  });

  function fillOptions() {
    const fill = (id, values, first) => {
      const el = $(id);
      const current = el.value;
      el.innerHTML = (first ? `<option value="">${first}</option>` : '') +
        values.map((v) => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join('');
      if (current && values.includes(current)) el.value = current;
    };
    fill('f-category', options.categories);
    fill('f-security', options.security_levels);
    fill('catFilter', options.categories, 'All categories');
    if (!$('f-security').value) $('f-security').value = options.security_levels.includes('internal') ? 'internal' : options.security_levels[0];
    $('serviceList').innerHTML = [...new Set(articles.map((a) => a.service).filter(Boolean))]
      .sort().map((s) => `<option value="${escapeHtml(s)}"></option>`).join('');
  }

  function renderMetrics(summary, total) {
    const s = summary || {};
    tween($('m-total'), total);
    tween($('m-indexed'), s.indexed || 0);
    tween($('m-pending'), s.pending_sync || 0);
    tween($('m-not'), s.not_indexed || 0);
    tween($('m-del'), s.to_delete || 0);
  }

  function filtered() {
    const q = state.q.trim().toLowerCase();
    return articles
      .filter((a) =>
        (!state.status || a.index_status === state.status) &&
        (!state.cat || a.category === state.cat) &&
        (!q || [a.number, a.title, a.service].some((v) => (v || '').toLowerCase().includes(q))))
      .sort((a, b) => (b.number || '').localeCompare(a.number || '', undefined, { numeric: true }));
  }

  function rowHtml(a, i, animate) {
    const [tone, label] = STATUS[a.index_status] || ['plain', a.index_status];
    const open = state.open.has(a.number);
    const preview = a.preview ? escapeHtml(a.preview) + (a.preview.length >= 400 ? '\u2026' : '') : '';
    return `
      <article class="row${open ? ' open' : ''}${animate ? ' enter' : ''}" data-id="${escapeHtml(a.number)}" style="--i:${Math.min(i, 8)}">
        <button class="row-head kb" type="button" aria-expanded="${open}">
          <div class="row-main">
            <div class="row-line"><span class="inc">${escapeHtml(a.number)}</span><span class="title">${escapeHtml(a.title)}</span></div>
            <div class="sub"><span class="chip plain">${escapeHtml(a.category || 'no category')}</span>${a.service ? `<span>${escapeHtml(a.service)}</span>` : ''}<span>v${escapeHtml(a.version)}</span></div>
          </div>
          <span class="chip row-status ${tone}">${label}</span>
          ${CHEVRON}
        </button>
        <div class="row-body"><div class="inner"><div class="inner-pad">
          <dl class="facts">
            <dt>ServiceNow sys_id</dt><dd class="mono">${escapeHtml(a.sys_id || '\u2014')}</dd>
            <dt>Service</dt><dd>${escapeHtml(a.service || '\u2014')}</dd>
            <dt>Security level</dt><dd>${escapeHtml(a.security_level || '\u2014')}</dd>
            <dt>Workflow state</dt><dd>${escapeHtml(a.workflow_state || '\u2014')}</dd>
          </dl>
          ${preview ? `<div class="result">${preview}</div>` : ''}
        </div></div></div>
      </article>`;
  }

  function render(animate) {
    const rows = filtered();
    const shown = rows.slice(0, state.visible);
    const empty = $('empty');

    list.innerHTML = shown.map((a, i) => rowHtml(a, i, animate)).join('');

    if (!state.sourceAvailable) {
      empty.innerHTML = `<strong>ServiceNow unavailable</strong>${escapeHtml(state.sourceError || 'Check ServiceNow OAuth settings, then refresh.')}`;
      empty.hidden = false;
    } else if (!articles.length) {
      empty.innerHTML = '<strong>No published articles yet</strong>Add the first one, or publish the corpus with publish_kb.';
      empty.hidden = false;
    } else if (!rows.length) {
      empty.innerHTML = '<strong>No articles match</strong>Try a different search or clear the filters.';
      empty.hidden = false;
    } else {
      empty.hidden = true;
    }

    $('count').textContent = articles.length
      ? `Showing ${shown.length} of ${rows.length}` + (rows.length !== articles.length ? ` (filtered from ${articles.length})` : '')
      : '';
    const remaining = rows.length - shown.length;
    const more = $('moreBtn');
    more.hidden = remaining <= 0;
    more.textContent = `Show ${Math.min(remaining, isFinite(pageSize()) ? pageSize() : 20)} more`;
  }

  function resetVisible() { state.visible = pageSize(); }

  function renderSkeleton() {
    list.innerHTML = [0, 1, 2, 3].map(() => `
      <div class="skel-row" aria-hidden="true">
        <div style="flex:1"><div class="skel" style="width:60%;margin-bottom:10px"></div><div class="skel" style="width:30%"></div></div>
        <div class="skel" style="width:80px"></div>
      </div>`).join('');
  }

  async function load() {
    const btn = $('refreshBtn');
    btn.disabled = true;
    try {
      const data = await api('/api/v1/kb/articles');
      articles = data.articles || [];
      state.sourceAvailable = data.source_available !== false;
      state.sourceError = data.source_error || '';
      if (data.options) options = data.options;
      if (!state.sourceAvailable) setConn('err', 'ServiceNow unavailable');
      else setConn(data.index_available ? 'ok' : 'err', data.index_available ? 'Connected' : 'Vector DB unreachable');
      fillOptions();
      renderMetrics(data.summary, data.count);
      resetVisible();
      render(state.firstLoad);
    } catch (err) {
      setConn('err', 'Could not load');
      list.innerHTML = '';
      const empty = $('empty');
      empty.innerHTML = `<strong>Could not load articles</strong>${escapeHtml(err.message)}`;
      empty.hidden = false;
    } finally {
      btn.disabled = false;
      state.firstLoad = false;
    }
  }

  /* ---------- toolbar ---------- */
  segmented($('sizeSeg'), state.size, (v) => {
    state.size = v;
    store.set('barq.kbsize', v);
    resetVisible();
    render(false);
  });

  let searchTimer;
  $('q').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.q = e.target.value; resetVisible(); render(false); }, 150);
  });
  $('statusFilter').addEventListener('change', (e) => { state.status = e.target.value; resetVisible(); render(false); });
  $('catFilter').addEventListener('change', (e) => { state.cat = e.target.value; resetVisible(); render(false); });
  $('moreBtn').addEventListener('click', () => {
    state.visible += isFinite(pageSize()) ? pageSize() : 20;
    render(false);
  });
  $('refreshBtn').addEventListener('click', load);
  window.addEventListener('barq:api-changed', () => { state.firstLoad = true; renderSkeleton(); load(); });

  /* ---------- Vector DB update ---------- */
  async function runSync() {
    const btn = $('syncBtn');
    const label = $('syncLabel');
    btn.disabled = true;
    label.innerHTML = '<span class="spin"></span> Updating';
    try {
      const data = await api('/api/v1/dashboard/kb-sync', { method: 'POST' });
      const r = data.result || {};
      toast(`Vector DB updated: ${r.added ?? 0} added, ${r.updated ?? 0} updated, ${r.deleted ?? 0} deleted, ${r.unchanged ?? 0} unchanged`);
      await load();
      return true;
    } catch (err) {
      toast('Vector DB update failed: ' + err.message, 'err');
      return false;
    } finally {
      btn.disabled = false;
      label.textContent = 'Update Vector DB';
    }
  }
  $('syncBtn').addEventListener('click', runSync);

  /* ---------- form ---------- */
  const modal = $('modal');
  const normalizeService = (v) => v.trim().toLowerCase().replace(/\s+/g, '-');

  function validate(v) {
    const e = {};
    if (v.title.length < LIMITS.titleMin) e.title = `Title needs at least ${LIMITS.titleMin} characters.`;
    else if (v.title.length > LIMITS.titleMax) e.title = `Title can be at most ${LIMITS.titleMax} characters.`;
    if (!options.categories.includes(v.category)) e.category = 'Choose a category.';
    if (!options.security_levels.includes(v.security_level)) e.security_level = 'Choose a security level.';
    if (v.service.length < LIMITS.serviceMin) e.service = `Service needs at least ${LIMITS.serviceMin} characters.`;
    else if (v.service.length > LIMITS.serviceMax) e.service = `Service can be at most ${LIMITS.serviceMax} characters.`;
    else if (!SERVICE_RE.test(v.service)) e.service = "Use lowercase letters, numbers, '-' or '_' (for example video-conferencing).";
    if (v.body.length < LIMITS.bodyMin) e.body = `Article text needs at least ${LIMITS.bodyMin} characters.`;
    else if (v.body.length > LIMITS.bodyMax) e.body = `Article text can be at most ${LIMITS.bodyMax.toLocaleString()} characters.`;
    return e;
  }

  function readForm() {
    return {
      title: $('f-title').value.trim(),
      category: $('f-category').value,
      security_level: $('f-security').value,
      service: normalizeService($('f-service').value),
      body: $('f-body').value.trim(),
    };
  }

  function showErrors(errors, general) {
    FIELDS.forEach((f) => {
      const msg = errors[f];
      const el = $('e-' + f);
      el.textContent = msg || '';
      el.classList.toggle('show', !!msg);
      $('fld-' + f).classList.toggle('invalid', !!msg);
    });
    const g = $('f-error');
    g.textContent = general || '';
    g.classList.toggle('show', !!general);
    const first = FIELDS.find((f) => errors[f]);
    if (first) $('fld-' + first).querySelector('input, select, textarea').focus();
  }

  $('newBtn').addEventListener('click', () => {
    $('f-title').value = '';
    $('f-body').value = '';
    $('f-service').value = 'general';
    $('f-category').value = options.categories[0];
    $('f-security').value = options.security_levels.includes('internal') ? 'internal' : options.security_levels[0];
    $('bodyCount').textContent = '0';
    $('upload-info').classList.remove('show');
    $('upload-error').classList.remove('show');
    showErrors({}, '');
    openOverlay(modal);
  });
  $('modalCancel').addEventListener('click', () => closeOverlay(modal));
  $('f-body').addEventListener('input', () => { $('bodyCount').textContent = $('f-body').value.length.toLocaleString(); });
  $('f-service').addEventListener('blur', () => { $('f-service').value = normalizeService($('f-service').value); });

  $('modalSubmit').addEventListener('click', async () => {
    const values = readForm();
    $('f-service').value = values.service;
    const errors = validate(values);
    if (Object.keys(errors).length) { showErrors(errors, 'Fix the highlighted fields and try again.'); return; }
    showErrors({}, '');

    const btn = $('modalSubmit');
    btn.disabled = true;
    btn.textContent = 'Saving...';
    try {
      const data = await api('/api/v1/kb/articles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(values),
      });
      closeOverlay(modal);
      toast(`${data.number} saved to ServiceNow`);
      if ($('syncAfter').checked) await runSync(); else await load();
    } catch (err) {
      if (err.status === 422 && err.data && Array.isArray(err.data.detail)) {
        const serverErrors = {};
        err.data.detail.forEach((d) => {
          const field = d.loc[d.loc.length - 1];
          if (FIELDS.includes(field)) serverErrors[field] = d.msg;
        });
        showErrors(serverErrors, 'The server rejected some fields.');
      } else {
        showErrors({}, err.message || 'Could not save the article.');
      }
    } finally {
      btn.disabled = false;
      btn.textContent = 'Save to ServiceNow';
    }
  });

  /* ---------- file upload (button or drag and drop) ---------- */
  function uploadError(msg) {
    $('upload-info').classList.remove('show');
    const el = $('upload-error');
    el.textContent = msg || '';
    el.classList.toggle('show', !!msg);
  }

  async function handleFile(file) {
    if (!file) return;
    uploadError('');
    const ext = file.name.includes('.') ? file.name.split('.').pop().toLowerCase() : '';
    if (!['txt', 'md'].includes(ext)) { uploadError('Only .txt and .md files are supported.'); return; }
    if (file.size > LIMITS.fileMaxBytes) { uploadError('File is too large (max 200 KB).'); return; }
    if (file.size === 0) { uploadError('The file is empty.'); return; }
    try {
      const content = await file.text();
      const data = await api('/api/v1/kb/articles/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name, content }),
      });
      $('f-title').value = data.title;
      $('f-body').value = data.body;
      $('bodyCount').textContent = data.body.length.toLocaleString();
      showErrors({}, '');
      const info = $('upload-info');
      info.textContent = `Filled from ${data.filename}` +
        (data.title_source === 'filename' ? ' (title taken from the file name)' : '') +
        '. Review the text, choose a category, then save.';
      info.classList.add('show');
    } catch (err) {
      uploadError(err.message || 'Could not read the file.');
    }
  }

  const drop = $('dropzone');
  $('pickFileBtn').addEventListener('click', () => $('fileInput').click());
  $('fileInput').addEventListener('change', () => {
    const file = $('fileInput').files && $('fileInput').files[0];
    $('fileInput').value = '';
    handleFile(file);
  });
  ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('drag'); }));
  drop.addEventListener('drop', (e) => handleFile(e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]));

  /* ---------- start ---------- */
  fillOptions();
  renderSkeleton();
  setConn('idle', 'Connecting');
  load();
})();
