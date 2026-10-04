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
  const PRIORITY_LABEL = { '1': 'P1 Critical', '2': 'P2 High', '3': 'P3 Moderate', '4': 'P4 Low', '5': 'P5 Planning' };
  const PAGE = 20;
  const CHEVRON = '<svg class="chev" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';


  const state = {
    limit: [5, 10, 20, 30].includes(parseInt(store.get('barq.limit', '10'), 10)) ? parseInt(store.get('barq.limit', '10'), 10) : 10,
    status: '',
    priority: '',  // priority filter: '', '1', '2', '3+'
    live: store.get('barq.live', '1') === '1',
    firstLoad: true,
    inflight: false,
    timer: null,
    extra: 0,
    q: '',
    again: false,
    rows: new Map(),
    waiting: new Set(),
    latest: [],
    selectedId: null,  // sys_id of the open drawer
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
  function priorityTier(priority) {
    const match = String(priority == null ? '' : priority).match(/^(\d)/);
    return match ? match[1] : '';
  }

  function prioBadgeHtml(priority) {
    if (!priority && priority !== 0) return '';
    const p = priorityTier(priority);
    if (!p) return '';
    const labels = { '1': 'P1 Critical', '2': 'P2 High', '3': 'P3 Moderate', '4': 'P4 Low', '5': 'P5 Planning' };
    const label = labels[p] || 'P' + p;
    return `<span class="prio-badge prio-badge-${p}">${label}</span>`;
  }

  function formatUsd(value) {
    if (value === null || value === undefined || value === '') return '—';
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    if (n < 0.001) return '$' + (n * 1000).toFixed(3) + 'm';
    if (n < 0.10) return (n * 100).toFixed(3) + '¢';
    return '$' + n.toFixed(4);
  }

  function tokenSummary(exec) {
    const tin = exec.total_tokens_in;
    const tout = exec.total_tokens_out;
    if ((tin == null || tin === 0) && (tout == null || tout === 0)) return '—';
    return (tin || 0) + ' in / ' + (tout || 0) + ' out';
  }

  function requestedDrawerId() {
    try {
      const params = new URLSearchParams(window.location.search);
      return params.get('sys_id') || params.get('id') || '';
    } catch (e) {
      return '';
    }
  }

  function setDrawerQuery(sysId) {
    const url = new URL(window.location.href);
    if (sysId) url.searchParams.set('sys_id', sysId);
    else url.searchParams.delete('sys_id');
    url.searchParams.delete('id');
    history.replaceState(null, '', url.pathname + url.search + url.hash);
  }

  function gateBadgeHtml(exec) {
    if (!exec) return '';
    const result = exec.latest_result || {};
    if (result.retrieval_cache_hit) return '<span class="gate-badge cache-hit">⚡ Cache Hit</span>';
    if (result.gate === 'high_risk') return '<span class="gate-badge high-risk">🛑 High Risk</span>';
    if (result.gate === 'low_confidence') return '<span class="gate-badge">⚠ Low Confidence</span>';
    if (result.gate === 'critic_exhausted') return '<span class="gate-badge">⚠ Critic Exhausted</span>';
    return '';
  }

  function buildRow(item, index, animate) {
    const el = document.createElement('article');
    el.className = 'row' + (animate ? ' enter' : '');
    el.style.setProperty('--i', String(Math.min(index, 8)));
    el.dataset.id = item.sys_id;
    el.innerHTML = `
      <button class="row-head" type="button" aria-expanded="false">
        <div class="row-main">
          <div class="row-line">
            <span class="prio-wrap"></span>
            <span class="inc"></span>
            <span class="chip"></span>
            <span class="gate-wrap"></span>
          </div>
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

    // Click: open drawer (primary), expand details (fallback / mobile)
    el.querySelector('.row-head').addEventListener('click', () => {
      openDrawer(item.sys_id);
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

    // Priority badge
    const prioWrap = el.querySelector('.prio-wrap');
    if (prioWrap) prioWrap.innerHTML = prioBadgeHtml(item.priority);

    // Gate / special state badge
    const gateWrap = el.querySelector('.gate-wrap');
    if (gateWrap) gateWrap.innerHTML = gateBadgeHtml(exec);

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

    // Refresh drawer if this is the selected incident
    if (state.selectedId === item.sys_id) {
      renderDrawer(item);
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

    // Open a linked incident, or the first row on initial load
    if (state.firstLoad && items.length > 0 && !state.selectedId) {
      const wanted = requestedDrawerId();
      const match = wanted && items.find((it) => it.sys_id === wanted || it.number === wanted);
      const target = match ? match.sys_id : items[0].sys_id;
      setTimeout(() => openDrawer(target), 80);
    }

    applyFilter(items);
  }


  function applyFilter(items) {
    let visible = 0;
    items.forEach((item) => {
      const el = state.rows.get(item.sys_id);
      if (!el) return;
      const matchStatus = !state.status || statusOf(item) === state.status;
      // Priority filter: '1'=P1, '2'=P2, '3'=P3+  (ServiceNow sends "1 - Critical")
      const tier = priorityTier(item.priority);
      let matchPriority = true;
      if (state.priority === '1') matchPriority = tier === '1';
      else if (state.priority === '2') matchPriority = tier === '2';
      else if (state.priority === '3') matchPriority = Number(tier) >= 3;
      const show = matchStatus && matchPriority;
      el.hidden = !show;
      if (show) visible += 1;
    });

    const box = $('empty');
    if (!items.length && state.q) {
      showEmpty(`No incidents match "${state.q}"`, 'Search looks in the incident number, short description and description.');
    } else if (!items.length) {
      showEmpty('No incidents in ServiceNow yet', 'Create one above, or in ServiceNow.');
    } else if (!visible) {
      showEmpty('Nothing matches this filter', 'Try another status or priority, or load more incidents.');
    } else {
      box.hidden = true;
    }
  }


  function renderMetrics(items) {
    const withExec = items.filter((item) => item.execution);
    const executions = withExec.map((item) => item.execution);
    const count = (s) => executions.filter((e) => e.status === s).length;
    const succeeded = count('succeeded');
    const inReview = executions.filter((e) => e.status === 'awaiting_approval').length;

    tween($('m-total'), items.length);
    tween($('m-ok'), succeeded);

    // Auto-resolved rate — % of AI-processed incidents that resolved automatically
    const autoRate = $('m-auto-rate');
    if (autoRate) {
      if (executions.length > 0) {
        const pct = Math.round((succeeded / executions.length) * 100);
        autoRate.textContent = `${pct}% of AI runs`;
      } else {
        autoRate.textContent = 'no AI runs yet';
      }
    }

    // Cache hits
    const cacheEl = $('m-cache');
    if (cacheEl) {
      const hits = executions.filter((e) => {
        const r = e.latest_result || {};
        return r.retrieval_cache_hit === true || r.retrieval_cache_hit === 'True';
      }).length;
      tween(cacheEl, hits);
    }

    // Needs review
    const reviewEl = $('m-review');
    if (reviewEl) tween(reviewEl, inReview);
    const reviewCard = $('kpi-review-card');
    if (reviewCard) reviewCard.classList.toggle('kpi-card-warn', inReview > 0);
    const reviewAction = $('kpi-review-action');
    if (reviewAction) reviewAction.hidden = inReview === 0;

    const durations = executions
      .filter((e) => e.duration_seconds !== null && e.duration_seconds !== undefined)
      .map((e) => e.duration_seconds);
    $('m-avg').textContent = durations.length
      ? fmtDuration(durations.reduce((a, b) => a + b, 0) / durations.length)
      : '\u2014';
  }



  /* ---------- drawer ---------- */
  let drawerActiveTab = 'summary';

  function renderDrawerTab(item, tab) {
    const exec = item.execution;
    const result = exec ? (exec.latest_result || {}) : {};
    const out = result.outputs || {};

    /* ── helpers ──────────────────────────────────────────── */
    function metaRow(label, value, extra) {
      const v = (value === null || value === undefined || value === '') ? '—' : value;
      return `<div class="di-row"><span class="di-label">${label}</span><span class="di-value${extra ? ' ' + extra : ''}">${escapeHtml(String(v))}</span></div>`;
    }
    function sectionCard(title, bodyHtml, tone) {
      return `<div class="drawer-section"><h4>${escapeHtml(title)}</h4><div class="drawer-block${tone ? ' ' + tone : ''}">${bodyHtml}</div></div>`;
    }
    function statusChip(label, tone) {
      return `<span class="chip ${tone || ''}" style="font-size:12px">${escapeHtml(label)}</span>`;
    }

    /* ═══════════════════════════════════════════════════════
       TAB: Summary
    ═══════════════════════════════════════════════════════ */
    if (tab === 'summary') {
      let html = '';

      /* ── 1. Incident details card ── */
      const prioLabels = { '1': 'P1 Critical', '2': 'P2 High', '3': 'P3 Moderate', '4': 'P4 Low', '5': 'P5 Planning' };
      const prioNum   = priorityTier(item.priority);
      const prioLabel = prioLabels[prioNum] || (item.priority ? String(item.priority) : '—');
      const prioTone  = prioNum === '1' ? 'color:var(--bad-ink)' : prioNum === '2' ? 'color:var(--warn-ink)' : '';

      html += `<div class="drawer-section">
        <h4>Incident Details</h4>
        <div class="drawer-block" style="padding:0">
          <div class="di-grid">
            ${metaRow('Number', item.number)}
            ${metaRow('Short Description', item.short_description)}
            ${metaRow('Category', item.category)}
            ${metaRow('State', item.state)}
            <div class="di-row"><span class="di-label">Priority</span><span class="di-value" style="${prioTone}">${escapeHtml(prioLabel)}</span></div>
            ${metaRow('Created', item.created_at ? new Date(item.created_at).toLocaleString() : '—')}
            ${item.description ? metaRow('Description', item.description) : ''}
          </div>
        </div>
      </div>`;

      /* ── 2. Gate / cache callout ── */
      const gate   = result.gate;
      const action = result.action_taken;
      if (gate === 'high_risk') {
        html += sectionCard('⚠ Stopped at Gate — High Risk',
          'This incident was classified as <strong>high risk</strong> and requires human approval before the AI can proceed.', 'bad');
      } else if (gate === 'low_confidence') {
        html += sectionCard('⚠ Stopped at Gate — Low Confidence',
          'The AI confidence score was below the minimum threshold. A human reviewer must validate the resolution before it is applied.', 'warn');
      } else if (gate === 'critic_exhausted') {
        html += sectionCard('⚠ Stopped at Gate — Critic Exhausted',
          'The Critic rejected all revision attempts. No valid evidence-backed resolution could be generated automatically.', 'warn');
      } else if (result.retrieval_cache_hit) {
        html += sectionCard('⚡ Cache Hit — Instant Resolution',
          'A previously approved human resolution was found in the knowledge base (KBHR) and reused directly — skipping diagnosis and generation.', 'violet');
      }

      /* ── 3. Diagnosis card ── */
      if (out.diagnosis) {
        html += `<div class="drawer-section">
          <h4>Diagnosis &amp; Root Cause</h4>
          <div class="drawer-block">${escapeHtml(out.diagnosis)}</div>
        </div>`;
      }

      /* ── 4. Resolution card ── */
      if (out.resolution) {
        html += `<div class="drawer-section">
          <h4>Proposed Resolution Steps</h4>
          <div class="drawer-block ok">${escapeHtml(out.resolution)}</div>
        </div>`;
      }

      /* ── 5. ServiceNow sync status ── */
      if (exec) {
        const snWrite = result.servicenow_write;
        let snHtml = '';
        if (snWrite === 'work_notes') {
          snHtml = `${statusChip('Work Notes written', 'ok')} <span style="font-size:12px;color:var(--ink-3);margin-left:6px">AI analysis posted to ServiceNow Work Notes.</span>`;
        } else if (snWrite === 'resolution' || snWrite === 'ai_resolution') {
          snHtml = `${statusChip('AI Resolution synced', 'ok')} <span style="font-size:12px;color:var(--ink-3);margin-left:6px">Resolution written back to ServiceNow.</span>`;
        } else if (snWrite === 'none' || !snWrite) {
          snHtml = `${statusChip('Not synced', '')} <span style="font-size:12px;color:var(--ink-3);margin-left:6px">No data has been written to ServiceNow yet.</span>`;
        } else {
          snHtml = `${statusChip(String(snWrite), 'accent')}`;
        }
        html += `<div class="drawer-section">
          <h4>ServiceNow Sync</h4>
          <div class="drawer-block" style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;white-space:normal">${snHtml}</div>
        </div>`;
      }

      if (!out.diagnosis && !out.resolution && !gate && !result.retrieval_cache_hit && exec) {
        html += sectionCard('Processing', 'No resolution drafted yet — the incident is still processing.', '');
      }
      if (!exec) {
        html += sectionCard('Not sent to AI', 'This incident was not sent to the AI pipeline.', '');
      }

      return html || '<div class="drawer-empty">No summary available yet.</div>';
    }

    /* ═══════════════════════════════════════════════════════
       TAB: AI Reasoning
    ═══════════════════════════════════════════════════════ */
    if (tab === 'ai') {
      if (!exec) return '<div class="drawer-empty">No AI run for this incident.</div>';
      let html = '';

      /* Classification + Risk */
      const riskVal  = (result.risk || '').toLowerCase();
      const riskTone = riskVal === 'high' ? 'color:var(--bad-ink);font-weight:700' : riskVal === 'low' ? 'color:var(--ok-ink);font-weight:700' : '';
      html += `<div class="drawer-section">
        <h4>Classification &amp; Risk</h4>
        <div class="drawer-block" style="padding:0">
          <div class="di-grid">
            ${metaRow('Classification', result.classification)}
            <div class="di-row"><span class="di-label">Risk Level</span><span class="di-value" style="${riskTone}">${escapeHtml(result.risk || '—')}</span></div>
            ${metaRow('Eligibility', out.eligibility)}
          </div>
        </div>
      </div>`;

      /* Confidence bar */
      if (result.confidence !== null && result.confidence !== undefined) {
        const conf = parseFloat(result.confidence);
        const pct  = Math.round(conf * 100);
        const cls  = conf >= 0.75 ? '' : conf >= 0.5 ? 'mid' : 'low';
        const label = cls === '' ? 'High confidence' : cls === 'mid' ? 'Medium confidence' : 'Low confidence';
        html += `<div class="drawer-section">
          <h4>Confidence Score</h4>
          <div class="drawer-block" style="white-space:normal">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
              <span style="font-size:12px;color:var(--ink-3)">${label}</span>
              <span class="confidence-val ${cls}">${pct}%</span>
            </div>
            <div class="confidence-bar"><div class="confidence-fill ${cls}" style="width:${pct}%"></div></div>
          </div>
        </div>`;
      }

      /* Guardrails / Critic */
      const gateVal = result.gate;
      const hasGuardrails = gateVal || (exec.failures && exec.failures.length) || result.action_taken;
      if (hasGuardrails) {
        let grHtml = '<div class="di-grid">';
        if (gateVal) {
          const gateTone = gateVal === 'high_risk' ? 'color:var(--bad-ink)' : 'color:var(--warn-ink)';
          grHtml += `<div class="di-row"><span class="di-label">Gate Triggered</span><span class="di-value" style="${gateTone}">${escapeHtml(gateVal)}</span></div>`;
        }
        if (result.action_taken) {
          grHtml += metaRow('Outcome', result.action_taken);
        }
        grHtml += metaRow('Retry Attempts', exec.retry_attempt_count);
        grHtml += metaRow('Node Reached', exec.node_reached);
        grHtml += '</div>';
        if (exec.failures && exec.failures.length) {
          exec.failures.forEach((f) => {
            grHtml += `<div class="drawer-block bad" style="margin-top:8px"><strong>${escapeHtml(f.failing_node)}</strong> — ${escapeHtml(f.error_class)}: ${escapeHtml(f.message)}</div>`;
          });
        }
        html += `<div class="drawer-section"><h4>Guardrails &amp; Critic</h4><div class="drawer-block" style="padding:0">${grHtml}</div></div>`;
      }

      /* Execution timeline */
      const STAGE_LABELS = ['Received', 'Classify', 'Retrieve', 'Diagnose', 'Generate', 'Resolved'];
      const stageStates  = exec ? pipelineStages(exec) : STAGE_LABELS.map(() => 'pending');
      let timelineHtml   = '<div class="exec-timeline">';
      STAGE_LABELS.forEach((label, i) => {
        const st     = stageStates[i] || 'pending';
        const icon   = st === 'done' ? '✓' : st === 'active' ? '●' : st === 'error' ? '✗' : st === 'skipped' ? '–' : '○';
        const color  = st === 'done' ? 'var(--ok)' : st === 'active' ? 'var(--accent)' : st === 'error' ? 'var(--bad)' : st === 'skipped' ? 'var(--ink-3)' : 'var(--line-strong)';
        timelineHtml += `<div class="exec-step">
          <span class="exec-dot" style="color:${color}">${icon}</span>
          <span class="exec-label" style="color:${st === 'pending' ? 'var(--ink-3)' : 'var(--ink)'}">${label}</span>
          ${st === 'active' ? '<span class="chip accent" style="font-size:10px;height:18px;padding:0 6px">Running</span>' : ''}
        </div>`;
      });
      timelineHtml += '</div>';
      if (exec.duration_seconds !== null && exec.duration_seconds !== undefined) {
        timelineHtml += `<div style="margin-top:8px;font-size:12px;color:var(--ink-3)">Total duration: <strong>${fmtDuration(exec.duration_seconds)}</strong></div>`;
      }
      html += `<div class="drawer-section"><h4>Execution Path</h4><div class="drawer-block" style="white-space:normal">${timelineHtml}</div></div>`;

      /* Execution ID */
      html += `<div class="drawer-section"><h4>Run Metadata</h4>
        <div class="drawer-block" style="padding:0"><div class="di-grid">
          ${metaRow('Execution ID', exec.execution_id)}
          ${metaRow('ServiceNow Write', result.servicenow_write)}
          ${metaRow('Estimated cost', formatUsd(exec.estimated_cost_usd))}
          ${metaRow('Tokens', tokenSummary(exec))}
        </div></div>
      </div>`;

      return html;
    }

    /* ═══════════════════════════════════════════════════════
       TAB: Evidence
    ═══════════════════════════════════════════════════════ */
    if (tab === 'evidence') {
      if (!exec) return '<div class="drawer-empty">No AI run for this incident.</div>';
      const evidence = result.retrieved_evidence || [];

      let html = '';

      /* Cache hit callout */
      if (result.retrieval_cache_hit) {
        html += `<div class="drawer-section">
          <div class="drawer-block violet" style="display:flex;align-items:flex-start;gap:10px;white-space:normal">
            <span style="font-size:18px">⚡</span>
            <div>
              <div style="font-weight:600;margin-bottom:2px">Cache Hit — KBHR Article Used</div>
              <div style="font-size:12px;color:var(--violet-ink)">A human-approved resolution was found in the Knowledge Base (KBHR) and applied directly. The full retrieval pipeline was bypassed for speed.</div>
            </div>
          </div>
        </div>`;
      }

      if (!evidence.length) {
        return html + '<div class="drawer-empty">No evidence was retrieved for this incident.</div>';
      }

      html += `<div class="drawer-section">
        <h4>${evidence.length} KB Article${evidence.length !== 1 ? 's' : ''} Retrieved</h4>
        <div class="evidence-list">`;

      evidence.forEach((ev, idx) => {
        const score   = parseFloat(ev.score || 0);
        const pct     = Math.min(100, Math.round((score > 1 ? score / 10 : score) * 100));
        const scoreLabel = score > 1 ? score.toFixed(2) : (score * 100).toFixed(0) + '%';
        const barColor = pct >= 70 ? 'var(--ok)' : pct >= 45 ? 'var(--warn)' : 'var(--bad)';
        const isKBHR  = ev.is_human_approved || (ev.id || '').includes('KBHR') || ev.human_approved;
        html += `<div class="evidence-item">
          <div class="evidence-item-header">
            <span class="evidence-id">${escapeHtml(ev.id || '#' + (idx + 1))}</span>
            ${isKBHR ? '<span class="chip violet" style="font-size:10px;height:18px;padding:0 6px">KBHR</span>' : ''}
            <div class="evidence-score-bar"><div class="evidence-score-fill" style="width:${pct}%;background:${barColor}"></div></div>
            <span class="evidence-score-val" style="color:${barColor}">${scoreLabel}</span>
          </div>
          ${ev.category ? `<div style="font-size:11px;color:var(--ink-3);margin-bottom:4px;text-transform:uppercase;letter-spacing:0.04em">${escapeHtml(ev.category)}</div>` : ''}
          <div class="evidence-text">${escapeHtml((ev.text || ev.snippet || '').slice(0, 400))}</div>
        </div>`;
      });

      html += '</div></div>';
      return html;
    }

    /* ── Raw tab ── */
    if (tab === 'raw') {
      if (!exec) return '<div class="drawer-empty">No AI run for this incident.</div>';
      return `<div class="drawer-section"><h4>Raw Result</h4>
        <pre class="drawer-block" style="font-size:11px;font-family:ui-monospace,monospace;overflow-x:auto">${escapeHtml(JSON.stringify(exec.latest_result || {}, null, 2))}</pre></div>`;
    }
    return '';
  }

  function renderDrawer(item) {
    const exec = item.execution;
    const result = exec ? (exec.latest_result || {}) : {};

    // Header
    $('drawerInc').textContent = item.number || '—';
    $('drawerBadge').innerHTML = prioBadgeHtml(item.priority);
    $('drawerDesc').textContent = item.short_description || '';
    const meta = [item.category, item.state, exec ? fmtDuration(exec.duration_seconds) : null].filter(Boolean);
    $('drawerMeta').innerHTML = meta.map((m) => `<span>${escapeHtml(m)}</span>`).join('');

    // Body
    $('drawerBody').innerHTML = renderDrawerTab(item, drawerActiveTab);

    // Actions
    const actEl = $('drawerActions');
    actEl.hidden = false;
    const snLink = $('drawerSNLink');
    if (item.servicenow_url) {
      snLink.href = item.servicenow_url;
      snLink.hidden = false;
    } else {
      snLink.hidden = true;
    }
    const reviewBtn = $('drawerReviewBtn');
    if (exec && exec.status === 'awaiting_approval') {
      reviewBtn.href = `approvals.html#${encodeURIComponent(exec.execution_id)}`;
      reviewBtn.hidden = false;
    } else {
      reviewBtn.hidden = true;
    }
  }

  function openDrawer(sysId) {
    const item = state.latest.find((i) => i.sys_id === sysId);
    if (!item) return;

    // Deselect previous
    if (state.selectedId && state.selectedId !== sysId) {
      const prev = state.rows.get(state.selectedId);
      if (prev) prev.classList.remove('drawer-selected');
    }

    state.selectedId = sysId;
    setDrawerQuery(sysId);
    const el = state.rows.get(sysId);
    if (el) el.classList.add('drawer-selected');

    renderDrawer(item);

    const drawer = $('drawer');
    drawer.setAttribute('aria-hidden', 'false');
    $('commandLayout').classList.add('drawer-open');
  }

  function closeDrawerPanel() {
    if (state.selectedId) {
      const prev = state.rows.get(state.selectedId);
      if (prev) prev.classList.remove('drawer-selected');
      state.selectedId = null;
    }
    setDrawerQuery(null);
    $('drawer').setAttribute('aria-hidden', 'true');
    $('commandLayout').classList.remove('drawer-open');
  }

  $('drawerClose').addEventListener('click', closeDrawerPanel);

  // Drawer tab switching
  document.querySelectorAll('.drawer-tab').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.drawer-tab').forEach((b) => {
        b.classList.remove('active');
        b.setAttribute('aria-selected', 'false');
      });
      btn.classList.add('active');
      btn.setAttribute('aria-selected', 'true');
      drawerActiveTab = btn.dataset.tab;
      if (state.selectedId) {
        const item = state.latest.find((i) => i.sys_id === state.selectedId);
        if (item) $('drawerBody').innerHTML = renderDrawerTab(item, drawerActiveTab);
      }
    });
  });

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
      if (data.stale) setConn('warn', 'ServiceNow delayed');
      else setConn(state.live ? 'live' : 'ok', state.live ? 'Live' : 'Connected');
      render(state.latest);
      renderMetrics(state.latest);
      const total = data.total ?? state.latest.length;
      $('updated').textContent = `${state.latest.length} of ${total} ${q ? 'matching' : 'in ServiceNow'} · Updated ` +
        fmtTime(new Date().toISOString());
      $('loadMore').hidden = state.latest.length >= total || shownLimit() >= 500;
    } catch (err) {
      setConn('err', 'API unreachable');
      if (state.firstLoad) {
        list.innerHTML = '';
        showEmpty('Could not reach the API', 'Check the address in Connection settings (top right) and that the API is running.');
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

  // Priority filter
  if ($('prioritySeg')) {
    segmented($('prioritySeg'), state.priority || '', (v) => {
      state.priority = v;
      applyFilter(state.latest);
    });
  }

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
      'sweep picks it up within about 30 seconds. If it is not eligible (category, AI enabled, ' +
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
        }),
      });
      toast(`${data.number} created in ServiceNow. Waiting for the Business Rule...`);
      closeOverlay(modal);
      poll();
      watchBusinessRule(data.sys_id, data.number);
    } catch (err) {
      toast('Could not create incident: ' + err.message, 'err');
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
