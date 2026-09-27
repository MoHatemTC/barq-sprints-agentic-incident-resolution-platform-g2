/* Pipeline dashboard logic. Endpoints are unchanged:
   GET  /api/v1/dashboard/executions?limit=N
   POST /api/v1/dashboard/incidents
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
  };
  const CHEVRON = '<svg class="chev" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';

  const state = {
    limit: [5, 10, 20, 30].includes(parseInt(store.get('barq.limit', '10'), 10)) ? parseInt(store.get('barq.limit', '10'), 10) : 10,
    status: '',
    live: store.get('barq.live', '1') === '1',
    firstLoad: true,
    inflight: false,
    timer: null,
    rows: new Map(),
    latest: [],
  };

  /* ---------- pipeline stage logic (same rules as before) ---------- */
  function pipelineStages(exec) {
    const status = exec.status;
    const result = exec.latest_result || {};
    const out = result.outputs || {};
    // A finished run only marks the stages that really ran: a high-risk run
    // goes to a human before retrieve/diagnose/generate, so those stay grey
    if (status === 'succeeded' && result.action_taken) {
      const ran = [true, !!result.classification, !!(result.retrieved_evidence || []).length,
        !!out.diagnosis, !!out.resolution, !!out.resolution && result.action_taken !== 'rejected_by_human'];
      return ran.map((r) => (r ? 'done' : 'pending'));
    }
    let reached = 0;
    if (result.retrieved_evidence) reached = 2;
    if (result.classification) reached = 1;
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
  function buildRow(exec, index, animate) {
    const el = document.createElement('article');
    el.className = 'row' + (animate ? ' enter' : '');
    el.style.setProperty('--i', String(Math.min(index, 8)));
    el.dataset.id = exec.execution_id;
    el.innerHTML = `
      <button class="row-head" type="button" aria-expanded="false">
        <div class="row-main">
          <div class="row-line"><span class="inc"></span><span class="chip"></span></div>
          <div class="sub"><span class="when"></span></div>
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

  function detailsHtml(exec) {
    const result = exec.latest_result || {};
    const out = result.outputs || {};
    let html = '<dl class="facts">' +
      fact('Execution ID', exec.execution_id, true) +
      fact('ServiceNow sys_id', exec.incident_sys_id, true) +
      fact('Classification', result.classification) +
      fact('Risk', result.risk) +
      fact('Confidence', result.confidence) +
      fact('Eligibility', out.eligibility) +
      fact('Stopped at gate', result.gate) +
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

  function updateRow(el, exec) {
    el.querySelector('.inc').textContent = exec.incident_number || '\u2014';

    const [tone, label] = STATUS[exec.status] || ['', exec.status || 'unknown'];
    const chip = el.querySelector('.chip');
    chip.className = 'chip' + (tone ? ' ' + tone : '');
    chip.textContent = label;

    el.querySelector('.when').textContent = fmtTime(exec.started_at);
    el.querySelector('.dur').textContent = fmtDuration(exec.duration_seconds);

    const classes = pipelineStages(exec);
    el.querySelectorAll('.track li').forEach((li, i) => { li.className = classes[i]; });

    const html = detailsHtml(exec);
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

  function render(executions) {
    if (state.firstLoad) list.innerHTML = '';
    const ids = new Set(executions.map((e) => e.execution_id));

    state.rows.forEach((el, id) => {
      if (!ids.has(id)) { el.remove(); state.rows.delete(id); }
    });

    executions.forEach((exec, i) => {
      let el = state.rows.get(exec.execution_id);
      if (!el) {
        el = buildRow(exec, state.firstLoad ? i : 0, true);
        state.rows.set(exec.execution_id, el);
      }
      updateRow(el, exec);
      if (list.children[i] !== el) list.insertBefore(el, list.children[i] || null);
    });

    applyFilter(executions);
  }

  function applyFilter(executions) {
    let visible = 0;
    executions.forEach((exec) => {
      const el = state.rows.get(exec.execution_id);
      if (!el) return;
      const show = !state.status || exec.status === state.status;
      el.hidden = !show;
      if (show) visible += 1;
    });

    const box = $('empty');
    if (!executions.length) {
      showEmpty('No executions yet', 'Create an incident above, or send one from ServiceNow.');
    } else if (!visible) {
      showEmpty('Nothing matches this filter', 'Try another status, or show more executions.');
    } else {
      box.hidden = true;
    }
  }

  function renderMetrics(executions) {
    const count = (s) => executions.filter((e) => e.status === s).length;
    tween($('m-total'), executions.length);
    tween($('m-ok'), count('succeeded'));
    tween($('m-bad'), count('failed'));
    tween($('m-run'), count('started'));
    const durations = executions.filter((e) => e.duration_seconds !== null && e.duration_seconds !== undefined).map((e) => e.duration_seconds);
    $('m-avg').textContent = durations.length ? fmtDuration(durations.reduce((a, b) => a + b, 0) / durations.length) : '\u2014';
  }

  /* ---------- polling ---------- */
  async function poll() {
    if (state.inflight) return;
    state.inflight = true;
    try {
      const data = await api('/api/v1/dashboard/executions?limit=' + state.limit);
      state.latest = data.executions || [];
      setConn(state.live ? 'live' : 'ok', state.live ? 'Live' : 'Connected');
      render(state.latest);
      renderMetrics(state.latest);
      $('updated').textContent = 'Updated ' + fmtTime(new Date().toISOString());
    } catch (err) {
      setConn('err', 'API unreachable');
      if (state.firstLoad) {
        list.innerHTML = '';
        showEmpty('Could not reach the API', 'Check the address in Connection settings (top right) and that the API is running.');
      }
    } finally {
      state.inflight = false;
      state.firstLoad = false;
    }
  }

  function schedule() {
    clearInterval(state.timer);
    if (state.live) state.timer = setInterval(() => { if (!document.hidden) poll(); }, 3000);
  }

  /* ---------- controls ---------- */
  segmented($('limitSeg'), state.limit, (v) => {
    state.limit = parseInt(v, 10);
    store.set('barq.limit', String(state.limit));
    poll();
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

  $('newIncidentBtn').addEventListener('click', () => {
    ['f-short', 'f-sysid', 'f-number'].forEach((id) => { $(id).value = ''; });
    errEl.classList.remove('show');
    openOverlay(modal);
  });
  $('modalCancel').addEventListener('click', () => closeOverlay(modal));

  async function submitIncident() {
    const short = $('f-short').value.trim();
    if (!short) {
      errEl.textContent = 'Enter a short description first.';
      errEl.classList.add('show');
      $('f-short').focus();
      return;
    }
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
          sys_id: $('f-sysid').value.trim() || null,
          number: $('f-number').value.trim() || null,
        }),
      });
      toast(`Incident ${data.number} queued for processing`);
      closeOverlay(modal);
      setTimeout(poll, 800);
    } catch (err) {
      toast('Could not create incident: ' + err.message, 'err');
    } finally {
      btn.disabled = false;
      btn.textContent = 'Create and enqueue';
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
