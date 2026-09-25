/* S3.4 approvals page. Endpoints:
   GET  /api/v1/approvals
   GET  /api/v1/approvals/{id}
   POST /api/v1/approvals/{id}/decide */
(function () {
  'use strict';
  const B = window.Barq;
  const { $, escapeHtml, fmtTime, api, toast, store, buildChrome, setConn } = B;

  buildChrome('approvals');

  const BRIEF_LABELS = [
    ['what_happened', 'What happened'],
    ['why_stopped', 'Why it stopped'],
    ['proposed_action', 'Would have done'],
    ['reviewer_question', 'You must judge'],
  ];

  let selected = decodeURIComponent(location.hash.slice(1)) || null;
  let items = [];
  let shown = null; // run currently in the review panel

  function fact(label, value) {
    const shown = value === null || value === undefined || value === '' ? '—' : value;
    return `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(shown)}</dd>`;
  }

  function section(title, body, open) {
    return `<details class="raw"${open ? ' open' : ''}><summary>${escapeHtml(title)}</summary><div class="raw-body">${body}</div></details>`;
  }

  /* ---------- list ---------- */
  function renderList() {
    const list = $('list');
    list.innerHTML = items.map((item) => `
      <button class="row-head kb${item.approval_id === selected ? ' selected' : ''}" type="button" data-id="${escapeHtml(item.approval_id)}">
        <div class="row-main">
          <div class="row-line"><span class="inc">${escapeHtml(item.incident_number || '—')}</span><span class="chip violet">${escapeHtml(item.gate || 'review')}</span></div>
          <div class="sub">paused ${escapeHtml(fmtTime(item.created_at))}${item.brief_status === 'generated' ? '' : ' · no AI summary'}</div>
        </div>
      </button>`).join('');
    list.querySelectorAll('.row-head').forEach((btn) => btn.addEventListener('click', () => select(btn.dataset.id)));

    const empty = $('empty');
    empty.hidden = items.length > 0;
    empty.innerHTML = '<strong>Nothing waiting</strong>Paused runs show up here when a gate needs a human.';
  }

  async function loadList() {
    try {
      const data = await api('/api/v1/approvals');
      items = data.items || [];
      setConn('ok', 'Connected');
      $('updated').textContent = 'Updated ' + fmtTime(new Date().toISOString());
      renderList();
      const target = selected || (items[0] && items[0].approval_id);
      if (target && items.some((i) => i.approval_id === target)) {
        if (shown !== target) select(target);
      } else if (selected) {
        clearReview();
      }
    } catch (err) {
      setConn('err', 'API unreachable');
      toast('Could not load approvals: ' + err.message, 'err');
    }
  }

  /* ---------- review panel ---------- */
  function clearReview() {
    selected = null;
    shown = null;
    history.replaceState(null, '', location.pathname);
    $('review').innerHTML = '<div class="empty"><strong>Nothing selected</strong>Pick a paused run on the left.</div>';
    renderList();
  }

  async function select(id) {
    selected = id;
    history.replaceState(null, '', '#' + encodeURIComponent(id));
    renderList();
    try {
      renderReview(await api('/api/v1/approvals/' + encodeURIComponent(id)));
    } catch (err) {
      toast(err.status === 409 ? 'This run is no longer waiting for a decision.' : 'Could not load the run: ' + err.message, 'err');
      loadList();
    }
  }

  function renderReview(d) {
    shown = d.approval_id;
    const p = d.payload || {};
    const inc = p.incident || {};

    const brief = d.brief
      ? `<dl class="facts brief">${BRIEF_LABELS.map(([k, label]) => fact(label, d.brief[k])).join('')}</dl>`
      : '<div class="notice">AI summary unavailable. Review the raw details below; they are the official record.</div>';

    const evidence = (p.evidence || []).length
      ? (p.evidence || []).map((e) => `<div class="result"><b>${escapeHtml(e.id)}</b><span>${escapeHtml(e.text)}</span></div>`).join('')
      : '<p class="hint">No knowledge base evidence.</p>';

    $('review').innerHTML = `
      <div class="review-pad">
        <div class="row-line"><span class="inc">${escapeHtml(d.incident_number || '—')}</span><span class="chip violet">${escapeHtml(d.gate || '')}</span></div>
        <p class="lede">${escapeHtml(d.reason_text || '')}</p>
        <h3 class="review-h">Brief <small>AI summary, for reading only</small></h3>
        ${brief}
        ${section('Incident', `<dl class="facts">${fact('Short description', inc.short_description)}${fact('Description', inc.description)}${fact('Priority', inc.priority)}${fact('sys_id', inc.sys_id)}</dl>`, true)}
        ${section(`Evidence (${(p.evidence || []).length})`, evidence)}
        ${section('Draft', `<dl class="facts">${fact('Diagnosis', (p.draft || {}).diagnosis)}${fact('Resolution', (p.draft || {}).resolution)}</dl>`)}
        ${section('Verdicts', `<pre class="result mono">${escapeHtml(JSON.stringify(p.verdicts || {}, null, 2))}</pre>`)}
        ${section('Raw payload (audit record)', `<pre class="result mono">${escapeHtml(JSON.stringify(p, null, 2))}</pre>`)}
        <div class="decide">
          <div class="row2">
            <div class="field"><label for="reviewer">Reviewer</label><input class="input" id="reviewer" type="text" placeholder="Your name" /></div>
            <div class="field"><label for="rationale">Comment (optional)</label><input class="input" id="rationale" type="text" placeholder="Why you decided this" /></div>
          </div>
          <div class="dialog-actions"><div class="right">
            <button class="btn" type="button" data-action="reject">Reject</button>
            <button class="btn btn-primary" type="button" data-action="approve">Approve</button>
          </div></div>
        </div>
      </div>`;

    $('reviewer').value = store.get('barq.reviewer', '');
    $('review').querySelectorAll('[data-action]').forEach((btn) => btn.addEventListener('click', () => decide(d, btn.dataset.action)));
  }

  async function decide(d, action) {
    const reviewer = $('reviewer').value.trim();
    if (!reviewer) { toast('Enter your name as reviewer first.', 'err'); $('reviewer').focus(); return; }
    if (!window.confirm(`${action === 'approve' ? 'Approve' : 'Reject'} ${d.incident_number || 'this run'}? The paused run will continue.`)) return;
    store.set('barq.reviewer', reviewer);

    $('review').querySelectorAll('[data-action]').forEach((b) => { b.disabled = true; });
    try {
      await api(`/api/v1/approvals/${encodeURIComponent(d.approval_id)}/decide`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, reviewer, rationale: $('rationale').value.trim() || null }),
      });
      toast(`${d.incident_number}: ${action === 'approve' ? 'approved' : 'rejected'}. The run is resuming.`);
    } catch (err) {
      toast(err.status === 409 ? 'Already decided: another decision was recorded first.' : 'Decision failed: ' + err.message, 'err');
    }
    clearReview();
    loadList();
  }

  $('refreshBtn').addEventListener('click', loadList);
  window.addEventListener('barq:api-changed', loadList);
  loadList();
  setInterval(() => { if (!document.hidden) loadList(); }, 15000);
})();
