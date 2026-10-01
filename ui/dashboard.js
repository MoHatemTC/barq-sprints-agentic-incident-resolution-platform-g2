/* Pipeline dashboard logic. Endpoints:
   GET  /api/v1/dashboard/incidents?limit=N     (ServiceNow incidents + latest AI run)
   GET  /api/v1/dashboard/incident-categories
   POST /api/v1/dashboard/incidents            (creates in ServiceNow only)
   GET  /api/v1/dashboard/incidents/{sys_id}/events
   POST /api/v1/dashboard/kb-sync */
(function () {
  'use strict';
  const B = window.Barq;
  const { $, escapeHtml, fmtDuration, fmtTime, api, toast, store, buildChrome, setConn, tween, segmented, openOverlay, closeOverlay } = B;

  buildChrome('pipeline');

  const STAGES = [
    ['received', 'Received'], ['classify', 'Classify'], ['retrieve', 'Retrieve'],
    ['diagnose', 'Diagnose'], ['generate', 'Generate'], ['resolved', 'Resolved'],
  ];
  const STATUS = {
    succeeded: ['ok', 'Succeeded'],
    failed: ['bad', 'Failed'],
    started: ['accent', 'In progress'],
    blocked: ['warn', 'Blocked'],
    awaiting_approval: ['violet', 'Awaiting approval'],
    human_rejected: ['bad', 'Human rejected'],
    not_sent: ['plain', 'Not sent to AI'],
    waiting: ['accent', 'Waiting for ServiceNow'],
  };
  const PAGE = 20;
  const CHEVRON = '<svg class="chev" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';

  const state = {
    limit: [5, 10, 20, 30].includes(parseInt(store.get('barq.limit', '10'), 10)) ? parseInt(store.get('barq.limit', '10'), 10) : 10,
    status: '',
    live: store.get('barq.live', '1') === '1',
    firstLoad: true,
    inflight: false,
    timer: null,
    extra: 0,        // incidents added by "Load more" on top of the chosen limit
    q: '',           // search box text; the search itself runs in ServiceNow
    again: false,    // a poll was asked for while one was in flight
    rows: new Map(), // sys_id -> row element
    waiting: new Set(), // sys_ids created here whose Business Rule has not fired yet
    latest: [],
    syncedAt: null,  // when the list on screen was read from ServiceNow
  };
  const shownLimit = () => state.limit + state.extra;
  const statusOf = (item) => {
    const aiState = (item.ai_processing_state || '').toLowerCase().replace(/[\s-]+/g, '_');
    if (aiState === 'human_rejected') {
      return 'human_rejected';
    }
    if (item.execution) {
      if (item.execution.latest_result && item.execution.latest_result.action_taken === 'rejected_by_human') {
        return 'human_rejected';
      }
      return item.execution.status;
    }
    return state.waiting.has(item.sys_id) ? 'waiting' : 'not_sent';
  };

  // Graph node running now (live_node from the API) -> the dot it belongs to.
  const NODE_STAGE = {
    load: 0, validate: 0,
    classify: 1, determine_risk: 1,
    retrieve: 2,
    diagnose: 3,
    generate: 4, verify_evidence: 4, safety_check: 4, confidence_check: 4,
    act: 5, knowledge_capture: 5,
  };

  /* ---------- pipeline stage logic ---------- */
  function pipelineStages(exec) {
    const status = exec.status;
    const result = exec.latest_result || {};
    const out = result.outputs || {};

    // Running: light the dot of the node that is executing right now.
    const live = NODE_STAGE[exec.live_node];
    if (status === 'started' && live !== undefined) {
      return STAGES.map((_, i) => (i < live ? 'done' : i === live ? 'active' : 'pending'));
    }

    // Check if it's a HITL completion (early escalation, resolution by human, etc)
    const isHITL = ['approved_by_human', 'rejected_by_human', 'knowledge_captured'].includes(result.action_taken);
    const awaiting = exec.status === 'awaiting_approval';

    // If finished, calculate what actually ran
    if (status === 'succeeded' && result.action_taken) {
      const ran = [
        true, // received
        !!result.classification, // classify
        !!(result.retrieved_evidence || []).length, // retrieve
        !!out.diagnosis, // diagnose
        !!out.resolution || isHITL, // generate (or human provided it)
        result.action_taken !== 'rejected_by_human' // resolved
      ];
      
      return ran.map((r, i) => {
        if (r) return 'done';
        // If it's HITL and a stage didn't run, it was deliberately skipped, not left pending
        if (isHITL) return 'skipped';
        return 'pending';
      });
    }

    // In-progress logic
    let reached = 0;
    if (result.classification) reached = 1;
    // Keep the original pipeline template. A high-risk pause is represented
    // by the classify stage remaining active; approval is a gate state, not a
    // new pipeline stage.
    if (awaiting) return STAGES.map((_, i) => i === 0 ? 'done' : i === 1 ? 'active' : 'pending');
    if (result.retrieved_evidence) reached = 2;
    if (out.diagnosis) reached = 3;
    if (out.resolution) reached = 4;
    if (status === 'succeeded') reached = 5;

    return STAGES.map((_, i) => {
      if (status === 'failed' && i === reached) return 'error';
      if (i < reached || status === 'succeeded') return 'done';
      if (i === reached && status === 'started') return 'active';
      if (i === 0) return 'done';
      return 'pending';
    });
  }

  /* ---------- rows ---------- */
  function buildRow(item, index, animate) {
    const el = document.createElement('article');
    el.className = 'row' + (animate ? ' enter' : '');
    el.style.setProperty('--i', String(Math.min(index, 8)));
    el.dataset.id = item.sys_id;
    el.innerHTML = `
      <button class="row-head" type="button" aria-expanded="false">
        <div class="row-main">
          <div class="row-line"><span class="inc"></span><span class="chip"></span></div>
          <div class="desc"></div>
          <div class="sub"><span class="meta"></span><span class="when"></span></div>
        </div>
        <ol class="track" aria-label="Pipeline progress">
          ${STAGES.map(([key, label]) => `<li data-k="${key}"><span class="dot"></span><span class="lbl">${label}</span></li>`).join('')}
        </ol>
        <span class="dur"></span>
        ${CHEVRON}
      </button>
      <div class="row-body"><div class="inner"><div class="inner-pad"></div></div></div>`;

    el.querySelector('.row-head').addEventListener('click', () => {
      const open = el.classList.toggle('open');
      el.querySelector('.row-head').setAttribute('aria-expanded', String(open));
    });
    return el;
  }

  function fact(label, value, mono) {
    const shown = value === null || value === undefined || value === '' ? '\u2014' : value;
    return `<dt>${label}</dt><dd${mono ? ' class="mono"' : ''}>${escapeHtml(shown)}</dd>`;
  }

  function openInServiceNowHtml(item) {
    if (!item.servicenow_url) return '';
    return `<div class="dialog-actions"><div class="right"><a class="btn btn-sm" href="${escapeHtml(item.servicenow_url)}" target="_blank" rel="noopener noreferrer">Open in ServiceNow \u2197</a></div></div>`;
  }

  function incidentFacts(item) {
    return '<dl class="facts">' +
      fact('ServiceNow sys_id', item.sys_id, true) +
      fact('Category', item.category) +
      fact('State', item.state) +
      fact('Priority', item.priority) +
      fact('Created', fmtTime(item.created_at)) +
      fact('AI processing state', item.ai_processing_state) +
      fact('AI enabled', item.ai_enabled ? 'Yes' : 'No') +
      fact('Human lock', item.human_lock ? 'On' : 'Off') +
      '</dl>';
  }

  // Why an incident has no AI run. The Business Rule decides; these are the
  // reasons visible on the record, the rest (category) live in ServiceNow.
  function notSentHtml(item) {
    const aiState = (item.ai_processing_state || '').toLowerCase().replace(/[\s-]+/g, '_');
    if (aiState === 'human_rejected') {
      return '<div class="result"><b>AI</b><span>AI processing was stopped because the incident was marked as rejected by a human.</span></div>';
    }
    let reason = 'The ServiceNow Business Rule did not send it: its category is not supported, ' +
      'it was created before the integration, or ServiceNow could not reach the webhook.';
    if (item.human_lock) reason = 'Human lock is on, so the Business Rule does not send it to the AI.';
    else if (!item.ai_enabled) reason = 'AI is disabled on this incident.';
    return `<div class="result"><b>AI</b><span>${escapeHtml(reason)}</span></div>`;
  }

  function detailsHtml(exec) {
    const result = exec.latest_result || {};
    const out = result.outputs || {};
    let html = '<dl class="facts">' +
      fact('Execution ID', exec.execution_id, true) +
      fact('Classification', result.classification) +
      fact('Risk', result.risk) +
      fact('Confidence', result.confidence) +
      fact('Eligibility', out.eligibility) +
      fact('Review gate', result.gate === 'high_risk' ? 'Human approval required (high risk)' : result.gate) +
      fact('Outcome', result.action_taken) +
      fact('ServiceNow write', result.servicenow_write) +
      fact('Node reached', exec.node_reached) +
      fact('Retry attempts', exec.retry_attempt_count) +
      '</dl>';
    if (out.diagnosis || out.resolution) {
      html += `<div class="result"><b>Diagnosis</b><span>${escapeHtml(out.diagnosis || '\u2014')}</span><b>Resolution</b><span>${escapeHtml(out.resolution || '\u2014')}</span></div>`;
    }
    if (exec.status === 'awaiting_approval') {
      html += `<div class="dialog-actions"><div class="right"><a class="btn btn-primary btn-sm" href="approvals.html#${encodeURIComponent(exec.execution_id)}">Review →</a></div></div>`;
    }
    (exec.failures || []).forEach((f) => {
      html += `<div class="fail"><strong>${escapeHtml(f.failing_node)}</strong> \u2014 ${escapeHtml(f.error_class)}: ${escapeHtml(f.message)}</div>`;
    });
    return html;
  }

  function updateRow(el, item) {
    const exec = item.execution;
    if (exec) state.waiting.delete(item.sys_id);
    el.querySelector('.inc').textContent = item.number || '\u2014';
    el.querySelector('.desc').textContent = item.short_description || '';
    el.querySelector('.meta').textContent = [item.category, item.state].filter(Boolean).join(' \u00b7 ');
    el.classList.toggle('no-ai', !exec && !state.waiting.has(item.sys_id));

    const status = statusOf(item);
    const [tone, label] = STATUS[status] || ['', status || 'unknown'];
    const chip = el.querySelector('.chip');
    chip.className = 'chip' + (tone ? ' ' + tone : '');
    chip.textContent = label;

    el.querySelector('.when').textContent = fmtTime(item.created_at);
    el.querySelector('.dur').textContent = exec ? fmtDuration(exec.duration_seconds) : '';

    const classes = exec ? pipelineStages(exec) : STAGES.map(() => 'pending');
    el.querySelectorAll('.track li').forEach((li, i) => { li.className = classes[i]; });

    const html = openInServiceNowHtml(item) + incidentFacts(item) + (exec ? detailsHtml(exec) : notSentHtml(item));
    if (el._details !== html) {
      el._details = html;
      el.querySelector('.inner-pad').innerHTML = html;
    }
  }

  /* ---------- list rendering (keyed, so polling never flickers) ---------- */
  const list = $('list');
  list.addEventListener('animationend', (e) => {
    if (e.target.classList && e.target.classList.contains('enter')) e.target.classList.remove('enter');
  });

  function showEmpty(title, text) {
    const box = $('empty');
    box.innerHTML = `<strong>${escapeHtml(title)}</strong>${escapeHtml(text)}`;
    box.hidden = false;
  }

  function renderSkeleton() {
    list.innerHTML = [0, 1, 2].map(() => `
      <div class="skel-row" aria-hidden="true">
        <div style="width:180px"><div class="skel" style="width:110px;margin-bottom:10px"></div><div class="skel" style="width:70px"></div></div>
        <div class="skel" style="flex:1"></div>
        <div class="skel" style="width:40px"></div>
      </div>`).join('');
  }

  function render(items) {
    if (state.firstLoad) list.innerHTML = '';
    // Rows missing from ServiceNow (deleted there) disappear here too.
    const ids = new Set(items.map((item) => item.sys_id));

    state.rows.forEach((el, id) => {
      if (!ids.has(id)) { el.remove(); state.rows.delete(id); }
    });

    items.forEach((item, i) => {
      let el = state.rows.get(item.sys_id);
      if (!el) {
        el = buildRow(item, state.firstLoad ? i : 0, true);
        state.rows.set(item.sys_id, el);
      }
      updateRow(el, item);
      if (list.children[i] !== el) list.insertBefore(el, list.children[i] || null);
    });

    applyFilter(items);
  }

  function applyFilter(items) {
    let visible = 0;
    items.forEach((item) => {
      const el = state.rows.get(item.sys_id);
      if (!el) return;
      const show = !state.status || statusOf(item) === state.status;
      el.hidden = !show;
      if (show) visible += 1;
    });

    const box = $('empty');
    if (!items.length && state.q) {
      showEmpty(`No incidents match "${state.q}"`, 'Search looks in the incident number, short description and description.');
    } else if (!items.length) {
      showEmpty('No incidents in ServiceNow yet', 'Create one above, or in ServiceNow.');
    } else if (!visible) {
      showEmpty('Nothing matches this filter', 'Try another status, or load more incidents.');
    } else {
      box.hidden = true;
    }
  }

  function renderMetrics(items) {
    const executions = items.map((item) => item.execution).filter(Boolean);
    const count = (s) => executions.filter((e) => e.status === s).length;
    tween($('m-total'), items.length);
    tween($('m-ok'), count('succeeded'));
    tween($('m-bad'), count('failed'));
    tween($('m-run'), count('started'));
    const durations = executions.filter((e) => e.duration_seconds !== null && e.duration_seconds !== undefined).map((e) => e.duration_seconds);
    $('m-avg').textContent = durations.length ? fmtDuration(durations.reduce((a, b) => a + b, 0) / durations.length) : '\u2014';
  }

  /* ---------- sync status ----------
     live: read from ServiceNow within the cache TTL (10s)
     delayed: last refresh failed, the copy shown is still recent (badge only)
     stale: the copy shown is older than stale_after_seconds (30s): warning banner */
  function syncNotice(text) {
    const box = $('syncNotice');
    box.textContent = text || '';
    box.hidden = !text;
  }

  function showSync(data) {
    const reason = data.sync_error ? ' Last refresh failed: ' + data.sync_error + '.' : '';
    if (data.stale) {
      setConn('warn', 'ServiceNow stale');
      syncNotice(`Showing ServiceNow data from ${fmtTime(data.synced_at)} (${Math.round(data.age_seconds)}s old).` +
        reason + ' Retrying automatically.');
    } else {
      if (data.delayed) setConn('warn', 'ServiceNow delayed');
      else setConn(state.live ? 'live' : 'ok', state.live ? 'Live' : 'Connected');
      syncNotice('');
    }
  }

  function showSyncFailure(err) {
    // No status: the API itself did not answer. 502: the API is up but ServiceNow
    // is not, and nothing is cached for this page yet (the message says why).
    const what = !err.status ? 'The API cannot be reached (' + err.message + ')'
      : err.status === 502 ? err.message : 'API error: ' + err.message;
    setConn('err', !err.status ? 'API unreachable' : err.status === 502 ? 'ServiceNow unreachable' : 'API error');
    if (state.syncedAt) {
      syncNotice(`Showing data last synced at ${fmtTime(state.syncedAt)}. ${what}. Retrying automatically.`);
    }
  }

  /* ---------- polling ---------- */
  async function poll() {
    if (state.inflight) { state.again = true; return; }
    state.inflight = true;
    const q = state.q;
    try {
      const data = await api('/api/v1/dashboard/incidents?limit=' + shownLimit() +
        (q ? '&q=' + encodeURIComponent(q) : ''));
      if (q !== state.q) return; // the search changed while this was loading; the next poll shows it
      state.latest = data.incidents || [];
      state.syncedAt = data.synced_at;
      showSync(data);
      render(state.latest);
      renderMetrics(state.latest);
      const total = data.total ?? state.latest.length;
      $('updated').textContent = `${state.latest.length} of ${total} ${q ? 'matching' : 'in ServiceNow'} · Synced ` +
        fmtTime(data.synced_at);
      $('loadMore').hidden = state.latest.length >= total || shownLimit() >= 500;
    } catch (err) {
      showSyncFailure(err);
      if (state.firstLoad) {
        list.innerHTML = '';
        if (err.status === 502) showEmpty('Could not reach ServiceNow', err.message + '. The list loads as soon as ServiceNow answers.');
        else showEmpty('Could not reach the API', 'Check the address in Connection settings (top right) and that the API is running.');
      }
    } finally {
      state.inflight = false;
      state.firstLoad = false;
      if (state.again) { state.again = false; poll(); }
    }
  }

  function schedule() {
    clearInterval(state.timer);
    if (state.live) state.timer = setInterval(() => { if (!document.hidden) poll(); }, 3000);
  }

  /* ---------- controls ---------- */
  segmented($('limitSeg'), state.limit, (v) => {
    state.limit = parseInt(v, 10);
    state.extra = 0;
    store.set('barq.limit', String(state.limit));
    poll();
  });

  $('loadMore').addEventListener('click', () => {
    state.extra += PAGE;
    poll();
  });

  // Search runs in ServiceNow (number, short description, description), so it
  // covers all history, not just the rows loaded here. Debounced per keystroke.
  const searchBox = $('searchBox');
  let searchTimer = null;
  function setSearch(value) {
    const q = value.trim();
    if (q === state.q) return;
    state.q = q;
    state.extra = 0;
    poll();
  }
  searchBox.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => setSearch(searchBox.value), 350);
  });
  searchBox.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { clearTimeout(searchTimer); setSearch(searchBox.value); }
    if (e.key === 'Escape') { clearTimeout(searchTimer); searchBox.value = ''; setSearch(''); }
  });

  $('statusFilter').addEventListener('change', (e) => {
    state.status = e.target.value;
    applyFilter(state.latest);
  });

  const liveSwitch = $('liveSwitch');
  liveSwitch.setAttribute('aria-checked', String(state.live));
  liveSwitch.addEventListener('click', () => {
    state.live = !state.live;
    liveSwitch.setAttribute('aria-checked', String(state.live));
    store.set('barq.live', state.live ? '1' : '0');
    schedule();
    if (state.live) poll(); else setConn('ok', 'Connected');
  });

  $('refreshBtn').addEventListener('click', poll);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && state.live) poll(); });
  window.addEventListener('barq:api-changed', () => { state.firstLoad = true; state.rows.clear(); renderSkeleton(); poll(); });

  /* ---------- Vector DB update ---------- */
  $('kbSyncBtn').addEventListener('click', async () => {
    const btn = $('kbSyncBtn');
    const label = $('kbSyncLabel');
    btn.disabled = true;
    label.innerHTML = '<span class="spin"></span> Updating';
    try {
      const data = await api('/api/v1/dashboard/kb-sync', { method: 'POST' });
      const r = data.result || {};
      toast(`Vector DB updated: ${r.added ?? 0} added, ${r.updated ?? 0} updated, ${r.deleted ?? 0} deleted, ${r.unchanged ?? 0} unchanged`);
    } catch (err) {
      toast('Vector DB update failed: ' + err.message, 'err');
    } finally {
      btn.disabled = false;
      label.textContent = 'Update Vector DB';
    }
  });

  /* ---------- new incident dialog ---------- */
  const modal = $('modal');
  const errEl = $('f-error');

  const categorySelect = $('f-category');
  let categoriesLoaded = false;
  // One id per opened form, sent with every Create click. If ServiceNow created
  // the incident but the answer was lost, the retry gets that incident back
  // instead of a duplicate (the API stores it in correlation_id).
  let requestId = null;
  const newRequestId = () => (window.crypto && crypto.randomUUID)
    ? crypto.randomUUID()
    : Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);

  function showFormError(message, focusId) {
    errEl.textContent = message;
    errEl.classList.add('show');
    if (focusId) $(focusId).focus();
  }

  // Same choices, labels and order as the Category field on the ServiceNow form.
  async function loadCategories() {
    if (categoriesLoaded) return;
    categorySelect.disabled = true;
    categorySelect.innerHTML = '<option value="">Loading categories from ServiceNow...</option>';
    try {
      const data = await api('/api/v1/dashboard/incident-categories');
      categorySelect.innerHTML = '<option value="">Select a category</option>' +
        (data.categories || []).map((c) =>
          `<option value="${escapeHtml(c.value)}">${escapeHtml(c.label)}</option>`).join('');
      categoriesLoaded = true;
    } catch (err) {
      categorySelect.innerHTML = '<option value="">Categories unavailable</option>';
      showFormError('Could not load categories from ServiceNow: ' + err.message);
    } finally {
      categorySelect.disabled = false;
    }
  }

  $('newIncidentBtn').addEventListener('click', () => {
    ['f-short', 'f-desc'].forEach((id) => { $(id).value = ''; });
    categorySelect.value = '';
    errEl.classList.remove('show');
    requestId = newRequestId();
    openOverlay(modal);
    loadCategories();
  });
  $('modalCancel').addEventListener('click', () => closeOverlay(modal));

  // The Business Rule sends the webhook asynchronously (executeAsync), so give
  // it a moment. No event means ServiceNow suppressed it or could not reach us.
  const BR_WAIT_MS = 20000;
  const BR_POLL_MS = 2000;

  async function watchBusinessRule(sysId, number) {
    state.waiting.add(sysId);
    // Sent: stays "waiting" until its run shows up (updateRow clears it).
    if (!(await waitForBusinessRule(sysId, number))) {
      state.waiting.delete(sysId);
      render(state.latest);
    }
  }

  async function waitForBusinessRule(sysId, number) {
    const deadline = Date.now() + BR_WAIT_MS;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, BR_POLL_MS));
      try {
        const data = await api('/api/v1/dashboard/incidents/' + encodeURIComponent(sysId) + '/events');
        if ((data.events || []).length) {
          toast(`${number} passed the ServiceNow Business Rule and was queued`);
          poll();
          return true;
        }
      } catch (err) { /* keep waiting; the API may be briefly busy */ }
    }
    toast(`${number} was not received from ServiceNow yet. If it is eligible, the delivery ` +
      'sweep picks it up within about 3 minutes. If it is not eligible (category, AI enabled, ' +
      'human lock), it stays with ServiceNow: see System Logs for "AI Orchestrator".', 'warn', 12000);
    return false;
  }

  async function submitIncident() {
    const short = $('f-short').value.trim();
    const category = categorySelect.value;
    if (!short) return showFormError('Enter a short description first.', 'f-short');
    if (!category) return showFormError('Choose a category.', 'f-category');
    errEl.classList.remove('show');

    const btn = $('modalSubmit');
    btn.disabled = true;
    btn.textContent = 'Creating...';
    try {
      const data = await api('/api/v1/dashboard/incidents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          short_description: short,
          description: $('f-desc').value.trim() || null,
          category,
          request_id: requestId,
        }),
      });
      toast(data.status === 'already_created'
        ? `${data.number} was already created by your earlier attempt; no duplicate made.`
        : `${data.number} created in ServiceNow. Waiting for the Business Rule...`);
      closeOverlay(modal);
      poll();
      watchBusinessRule(data.sys_id, data.number);
    } catch (err) {
      // The form stays open with the same request id, so trying again is safe.
      toast('Could not create incident: ' + err.message + '. Try again; it will not create a duplicate.', 'err', 10000);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Create in ServiceNow';
    }
  }
  $('modalSubmit').addEventListener('click', submitIncident);
  modal.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.tagName === 'INPUT') submitIncident(); });

  /* ---------- start ---------- */
  renderSkeleton();
  setConn('idle', 'Connecting');
  poll();
  schedule();
})();
