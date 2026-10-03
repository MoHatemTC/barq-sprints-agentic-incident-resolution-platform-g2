/* Barq Analytics Dashboard logic.
   Fetches incident telemetry and renders advanced metrics, operational charts,
   and agentic governance KPIs. */
(function () {
  'use strict';
  const B = window.Barq;
  const { $, escapeHtml, fmtDuration, fmtTime, api, setConn, tween } = B;

  B.buildChrome('dashboard');

  const PRIORITY_LABEL = { '1': 'P1 Critical', '2': 'P2 High', '3': 'P3 Moderate', '4': 'P4 Low', '5': 'P5 Planning' };
  const STATUS_CONFIG = {
    succeeded: { tone: 'ok', label: 'Succeeded', color: '#167a4b' },
    started: { tone: 'accent', label: 'In progress', color: '#2159d6' },
    failed: { tone: 'bad', label: 'Failed', color: '#c03a2b' },
    blocked: { tone: 'warn', label: 'Blocked', color: '#b26a0a' },
    awaiting_approval: { tone: 'violet', label: 'Awaiting approval', color: '#6a4fd1' },
    human_rejected: { tone: 'bad', label: 'Human rejected', color: '#c03a2b' },
    not_sent: { tone: 'plain', label: 'Not sent to AI', color: '#667085' },
  };

  const CATEGORY_COLORS = [
    '#2159d6', '#167a4b', '#6a4fd1', '#e8a94a', '#ef7b6c', '#0097a7', '#8e24aa', '#546e7a'
  ];

  let latestData = [];

  function prioBadgeHtml(priority) {
    if (!priority && priority !== 0) return '—';
    const s = String(priority);
    const match = s.match(/^(\d)/);
    if (!match) return escapeHtml(s);
    const p = match[1];
    const label = PRIORITY_LABEL[p] || 'P' + p;
    return `<span class="prio-badge prio-badge-${p}">${escapeHtml(label)}</span>`;
  }

  function statusOf(item) {
    const aiState = (item.ai_processing_state || '').toLowerCase().replace(/[\s-]+/g, '_');
    if (aiState === 'human_rejected') return 'human_rejected';
    if (item.execution) {
      if (item.execution.latest_result && item.execution.latest_result.action_taken === 'rejected_by_human') {
        return 'human_rejected';
      }
      return item.execution.status || 'started';
    }
    return 'not_sent';
  }

  /* ─────────────────────────────────────────────────────────
     1. RENDER ADVANCED KPIS
     ───────────────────────────────────────────────────────── */
  function renderKPIs(items) {
    const total = items.length;
    $('kpi-total').textContent = String(total);
    $('kpi-total-sub').textContent = `${total} incidents ingested`;

    const withExec = items.filter((i) => i.execution);
    const execs = withExec.map((i) => i.execution);
    const succeeded = execs.filter((e) => e.status === 'succeeded').length;

    // 2. Autonomous Resolution Rate
    const autoRate = execs.length > 0 ? Math.round((succeeded / execs.length) * 100) : 0;
    $('kpi-auto-rate').textContent = `${autoRate}%`;
    $('kpi-auto-count').textContent = `${succeeded} of ${execs.length} AI runs`;

    // 3. Throughput (Avg Duration)
    const durations = execs
      .filter((e) => e.duration_seconds !== null && e.duration_seconds !== undefined)
      .map((e) => e.duration_seconds);
    const avgDuration = durations.length
      ? Math.round(durations.reduce((a, b) => a + b, 0) / durations.length)
      : 0;
    $('kpi-throughput').textContent = durations.length ? fmtDuration(avgDuration) : '—';

    // 4. SLA Compliance (resolutions <= 300s)
    const underSla = durations.filter((d) => d <= 300).length;
    const slaRate = durations.length > 0 ? Math.round((underSla / durations.length) * 100) : 100;
    $('kpi-sla').textContent = `${slaRate}%`;

    // 5. Critic Gate / Rejection Rate
    const criticGated = execs.filter((e) => {
      const res = e.latest_result || {};
      return res.gate === 'critic_exhausted' ||
             res.gate === 'high_risk' ||
             res.gate === 'low_confidence' ||
             (e.retry_attempt_count && e.retry_attempt_count > 0) ||
             (e.failures && e.failures.length > 0);
    }).length;
    const criticRate = execs.length > 0 ? Math.round((criticGated / execs.length) * 100) : 0;
    $('kpi-critic').textContent = `${criticRate}%`;
    $('kpi-critic-sub').textContent = `${criticGated} interventions / gates`;

    // 6. Cache Hits
    const cacheHits = execs.filter((e) => {
      const res = e.latest_result || {};
      return res.retrieval_cache_hit === true || res.retrieval_cache_hit === 'True';
    }).length;
    const cacheRate = execs.length > 0 ? Math.round((cacheHits / execs.length) * 100) : 0;
    $('kpi-cache').textContent = `${cacheRate}%`;
    $('kpi-cache-sub').textContent = `${cacheHits} instant KBHR hits`;
  }

  /* ─────────────────────────────────────────────────────────
     2. CHART: RESOLUTION TREND OVER TIME (SVG Line/Area Chart)
     ───────────────────────────────────────────────────────── */
  function renderTimelineChart(items) {
    const container = $('timelineChartContainer');
    if (!items.length) {
      container.innerHTML = '<div style="color:var(--ink-3);font-size:13px;padding:30px;text-align:center">No incident timeline data yet</div>';
      return;
    }

    // Group items by day
    const dateGroups = new Map();
    const sorted = [...items].sort((a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0));

    sorted.forEach((item) => {
      const d = item.created_at ? new Date(item.created_at) : new Date();
      const key = `${d.getMonth() + 1}/${d.getDate()}`;
      if (!dateGroups.has(key)) dateGroups.set(key, { total: 0, resolved: 0 });
      const g = dateGroups.get(key);
      g.total += 1;
      if (item.execution && item.execution.status === 'succeeded') g.resolved += 1;
    });

    let keys = Array.from(dateGroups.keys());
    if (keys.length === 1) {
      keys = ['Earlier', keys[0], 'Today'];
      dateGroups.set('Earlier', { total: 0, resolved: 0 });
      dateGroups.set('Today', { total: dateGroups.get(keys[1]).total, resolved: dateGroups.get(keys[1]).resolved });
    }

    const dataPoints = keys.map((k) => dateGroups.get(k) || { total: 0, resolved: 0 });
    const maxVal = Math.max(...dataPoints.map((d) => d.total), 4);

    // SVG coordinates
    const width = 520;
    const height = 180;
    const padL = 44;
    const padR = 24;
    const padT = 20;
    const padB = 32;

    const chartW = width - padL - padR;
    const chartH = height - padT - padB;

    const getX = (i) => padL + (i / Math.max(dataPoints.length - 1, 1)) * chartW;
    const getY = (val) => padT + chartH - (val / maxVal) * chartH;

    // Generate paths for Total and Resolved
    const totalPoints = dataPoints.map((d, i) => `${getX(i)},${getY(d.total)}`).join(' ');
    const resolvedPoints = dataPoints.map((d, i) => `${getX(i)},${getY(d.resolved)}`).join(' ');

    const areaPoints = `${getX(0)},${padT + chartH} ${totalPoints} ${getX(dataPoints.length - 1)},${padT + chartH}`;

    let svg = `
      <svg class="chart-svg" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">
        <defs>
          <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.25" />
            <stop offset="100%" stop-color="var(--accent)" stop-opacity="0.0" />
          </linearGradient>
        </defs>
        <!-- Horizontal Grid Lines -->
        <line x1="${padL}" y1="${getY(0)}" x2="${width - padR}" y2="${getY(0)}" stroke="var(--line)" stroke-width="1" />
        <line x1="${padL}" y1="${getY(maxVal / 2)}" x2="${width - padR}" y2="${getY(maxVal / 2)}" stroke="var(--line)" stroke-dasharray="3,3" stroke-width="1" />
        <line x1="${padL}" y1="${getY(maxVal)}" x2="${width - padR}" y2="${getY(maxVal)}" stroke="var(--line)" stroke-dasharray="3,3" stroke-width="1" />

        <!-- Area Fill -->
        <polygon points="${areaPoints}" fill="url(#areaGrad)" />

        <!-- Lines -->
        <polyline points="${totalPoints}" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />
        <polyline points="${resolvedPoints}" fill="none" stroke="var(--ok)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" />

        <!-- Dots -->
        ${dataPoints.map((d, i) => `
          <circle cx="${getX(i)}" cy="${getY(d.total)}" r="4.5" fill="var(--surface)" stroke="var(--accent)" stroke-width="2.2" />
          <circle cx="${getX(i)}" cy="${getY(d.resolved)}" r="4" fill="var(--surface)" stroke="var(--ok)" stroke-width="2.2" />
        `).join('')}

        <!-- X Labels -->
        ${keys.map((k, i) => {
          const anchor = i === 0 ? 'start' : i === keys.length - 1 ? 'end' : 'middle';
          return `<text x="${getX(i)}" y="${height - 10}" text-anchor="${anchor}" font-size="11" fill="var(--ink-3)">${escapeHtml(k)}</text>`;
        }).join('')}

        <!-- Y Labels -->
        <text x="${padL - 10}" y="${getY(maxVal) + 4}" text-anchor="end" font-size="10.5" font-weight="600" fill="var(--ink-3)">${maxVal}</text>
        <text x="${padL - 10}" y="${getY(0) + 3}" text-anchor="end" font-size="10.5" font-weight="600" fill="var(--ink-3)">0</text>
      </svg>
      <div class="chart-legend">
        <span class="chart-legend-item"><span class="chart-legend-dot" style="background:var(--accent)"></span> Total Ingested</span>
        <span class="chart-legend-item"><span class="chart-legend-dot" style="background:var(--ok)"></span> Autonomous Resolved</span>
      </div>`;

    container.innerHTML = svg;
  }

  /* ─────────────────────────────────────────────────────────
     3. CHART: CATEGORY DISTRIBUTION (Donut + Legend)
     ───────────────────────────────────────────────────────── */
  function renderCategoryChart(items) {
    const container = $('categoryChartContainer');
    const catMap = new Map();
    items.forEach((item) => {
      const c = item.category || 'General';
      catMap.set(c, (catMap.get(c) || 0) + 1);
    });

    const entries = Array.from(catMap.entries()).sort((a, b) => b[1] - a[1]);
    $('categoryCountBadge').textContent = `${entries.length} Categories`;

    if (!entries.length) {
      container.innerHTML = '<div style="color:var(--ink-3);font-size:13px;padding:30px;text-align:center">No categories recorded</div>';
      return;
    }

    const total = items.length;
    let accumulatedAngle = 0;
    const radius = 64;
    const strokeWidth = 24;
    const center = 80;
    const circumference = 2 * Math.PI * radius;

    let donutSegments = '';
    entries.forEach(([cat, count], idx) => {
      const pct = count / total;
      const strokeDash = pct * circumference;
      const strokeOffset = -accumulatedAngle * circumference;
      const color = CATEGORY_COLORS[idx % CATEGORY_COLORS.length];

      donutSegments += `<circle cx="${center}" cy="${center}" r="${radius}"
        fill="transparent"
        stroke="${color}"
        stroke-width="${strokeWidth}"
        stroke-dasharray="${strokeDash} ${circumference}"
        stroke-dashoffset="${strokeOffset}"
        transform="rotate(-90 ${center} ${center})" />`;

      accumulatedAngle += pct;
    });

    const legendHtml = entries.map(([cat, count], idx) => {
      const pct = Math.round((count / total) * 100);
      const color = CATEGORY_COLORS[idx % CATEGORY_COLORS.length];
      return `
        <div class="donut-legend-item">
          <div style="display:flex;align-items:center;gap:8px;overflow:hidden">
            <span class="donut-legend-dot" style="background:${color}"></span>
            <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ink-2)">${escapeHtml(cat)}</span>
          </div>
          <span style="font-weight:600;font-variant-numeric:tabular-nums;color:var(--ink)">${count} <span style="font-weight:400;color:var(--ink-3);font-size:11px">(${pct}%)</span></span>
        </div>`;
    }).join('');

    container.innerHTML = `
      <div class="donut-layout">
        <div class="donut-chart-box">
          <svg width="160" height="160" viewBox="0 0 160 160">
            ${donutSegments}
          </svg>
          <div class="donut-center-text">
            <div class="donut-center-val">${total}</div>
            <div class="donut-center-lbl">Total</div>
          </div>
        </div>
        <div class="donut-legend">
          ${legendHtml}
        </div>
      </div>`;
  }

  /* ─────────────────────────────────────────────────────────
     4. CHART: RESOLUTION SPEED & MTTR (Column Bar Chart)
     ───────────────────────────────────────────────────────── */
  function renderMTTRChart(items) {
    const container = $('mttrChartContainer');
    const withExec = items.filter((i) => i.execution);
    const execs = withExec.map((i) => i.execution);

    const buckets = [
      { label: '< 30s', sub: 'Instant/Cache', count: 0, tone: 'violet' },
      { label: '30s - 1m', sub: 'Rapid AI', count: 0, tone: 'ok' },
      { label: '1m - 3m', sub: 'Standard', count: 0, tone: 'accent' },
      { label: '3m - 5m', sub: 'Complex', count: 0, tone: 'warn' },
      { label: '> 5m', sub: 'Review/Queue', count: 0, tone: 'warn' },
    ];

    execs.forEach((e) => {
      const dur = e.duration_seconds;
      if (dur === null || dur === undefined) return;
      if (dur < 30) buckets[0].count += 1;
      else if (dur <= 60) buckets[1].count += 1;
      else if (dur <= 180) buckets[2].count += 1;
      else if (dur <= 300) buckets[3].count += 1;
      else buckets[4].count += 1;
    });

    const maxCount = Math.max(...buckets.map((b) => b.count), 1);
    const totalCount = execs.filter((e) => e.duration_seconds !== null && e.duration_seconds !== undefined).length || 1;

    const barsHtml = buckets.map((b) => {
      const heightPct = Math.round((b.count / maxCount) * 100);
      const pctOfTotal = Math.round((b.count / totalCount) * 100);
      return `
        <div class="bar-column">
          <span class="bar-val-lbl">${b.count} <span style="font-weight:400;color:var(--ink-3);font-size:10px">(${pctOfTotal}%)</span></span>
          <div class="bar-track" style="height:120px">
            <div class="bar-fill ${b.tone}" style="height:${Math.max(heightPct, 6)}%"></div>
          </div>
          <div class="bar-x-lbl">
            <strong>${b.label}</strong>
            <div style="font-size:9.5px;color:var(--ink-3)">${b.sub}</div>
          </div>
        </div>`;
    }).join('');

    container.innerHTML = `
      <div class="bar-chart-layout">
        ${barsHtml}
      </div>
      <div class="chart-legend">
        <span class="chart-legend-item"><span class="chart-legend-dot" style="background:var(--violet)"></span> Instant &lt;30s</span>
        <span class="chart-legend-item"><span class="chart-legend-dot" style="background:var(--ok)"></span> Rapid &lt;1m</span>
        <span class="chart-legend-item"><span class="chart-legend-dot" style="background:var(--accent)"></span> Standard &lt;3m</span>
        <span class="chart-legend-item"><span class="chart-legend-dot" style="background:var(--warn)"></span> Deep &gt;3m</span>
      </div>`;
  }

  /* ─────────────────────────────────────────────────────────
     5. CHART: PRIORITY & SEVERITY RESOLUTION MATRIX (Stacked Bars)
     ───────────────────────────────────────────────────────── */
  function renderPriorityChart(items) {
    const container = $('priorityChartContainer');
    const priorities = [
      { id: '1', label: 'P1 Critical', resolved: 0, review: 0, other: 0 },
      { id: '2', label: 'P2 High', resolved: 0, review: 0, other: 0 },
      { id: '3', label: 'P3 Moderate', resolved: 0, review: 0, other: 0 },
      { id: '4', label: 'P4 Low', resolved: 0, review: 0, other: 0 },
      { id: '5', label: 'P5 Planning', resolved: 0, review: 0, other: 0 },
    ];

    items.forEach((item) => {
      const pStr = String(item.priority || '');
      const match = pStr.match(/^(\d)/);
      const p = match ? match[1] : '3';
      const tier = priorities.find((x) => x.id === p) || priorities[2];
      const st = statusOf(item);
      if (st === 'succeeded') tier.resolved += 1;
      else if (st === 'awaiting_approval' || st === 'blocked' || st === 'human_rejected') tier.review += 1;
      else tier.other += 1;
    });

    const rowsHtml = priorities.map((tier) => {
      const total = tier.resolved + tier.review + tier.other;
      const resPct = total ? Math.round((tier.resolved / total) * 100) : 0;
      const revPct = total ? Math.round((tier.review / total) * 100) : 0;
      const otherPct = total ? Math.max(0, 100 - resPct - revPct) : 0;

      return `
        <div class="priority-row">
          <div class="priority-meta">
            <span style="font-weight:600;display:inline-flex;align-items:center;gap:6px">
              <span class="prio-badge prio-badge-${tier.id}">${tier.label}</span>
              <span style="font-weight:400;color:var(--ink-3);font-size:11px">${total} total</span>
            </span>
            <span style="font-size:11px;font-weight:600">
              <span style="color:var(--ok)">${resPct}% Resolved</span>
              ${revPct ? ` · <span style="color:var(--violet)">${revPct}% Review</span>` : ''}
            </span>
          </div>
          <div class="priority-stacked-track">
            ${resPct ? `<div class="priority-seg-resolved" style="width:${resPct}%" title="${tier.resolved} Resolved"></div>` : ''}
            ${revPct ? `<div class="priority-seg-review" style="width:${revPct}%" title="${tier.review} Human Review"></div>` : ''}
            ${otherPct ? `<div class="priority-seg-other" style="width:${otherPct}%"></div>` : ''}
          </div>
        </div>`;
    }).join('');

    container.innerHTML = `
      <div style="display:flex;flex-direction:column;width:100%;padding:4px 0">
        ${rowsHtml}
      </div>
      <div class="chart-legend" style="margin-top:10px">
        <span class="chart-legend-item"><span class="chart-legend-dot" style="background:var(--ok)"></span> Autonomous Resolved</span>
        <span class="chart-legend-item"><span class="chart-legend-dot" style="background:var(--violet)"></span> Escalated / Human Review</span>
        <span class="chart-legend-item"><span class="chart-legend-dot" style="background:var(--line-strong)"></span> In Progress / Other</span>
      </div>`;
  }

  /* ─────────────────────────────────────────────────────────
     6. CHART: PIPELINE STATUS BREAKDOWN
     ───────────────────────────────────────────────────────── */
  function renderStatusChart(items) {
    const container = $('statusChartContainer');
    const counts = {
      succeeded: 0,
      awaiting_approval: 0,
      started: 0,
      blocked: 0,
      human_rejected: 0,
      not_sent: 0,
    };

    items.forEach((item) => {
      const st = statusOf(item);
      if (counts[st] !== undefined) counts[st] += 1;
      else counts.not_sent += 1;
    });

    const total = items.length || 1;
    const listHtml = Object.entries(STATUS_CONFIG).map(([stKey, cfg]) => {
      const count = counts[stKey] || 0;
      const pct = Math.round((count / total) * 100);
      return `
        <div class="breakdown-row">
          <div class="breakdown-meta">
            <span class="breakdown-label"><i style="width:8px;height:8px;border-radius:50%;background:${cfg.color};display:inline-block"></i> ${cfg.label}</span>
            <span class="breakdown-val">${count} <span style="font-size:11px;font-weight:400;color:var(--ink-3)">(${pct}%)</span></span>
          </div>
          <div class="breakdown-track">
            <div class="breakdown-fill" style="width:${pct}%;background:${cfg.color}"></div>
          </div>
        </div>`;
    }).join('');

    container.innerHTML = `<div class="breakdown-list">${listHtml}</div>`;
  }

  /* ─────────────────────────────────────────────────────────
     7. CHART: AGENTIC QUALITY & GUARDRAIL METRICS
     ───────────────────────────────────────────────────────── */
  function renderGuardrails(items) {
    const container = $('guardrailsContainer');
    const withExec = items.filter((i) => i.execution);
    const execs = withExec.map((i) => i.execution);

    let highRisk = 0;
    let lowConf = 0;
    let criticExhausted = 0;
    let cacheHits = 0;
    let totalConf = 0;
    let confCount = 0;

    execs.forEach((e) => {
      const res = e.latest_result || {};
      if (res.gate === 'high_risk') highRisk += 1;
      if (res.gate === 'low_confidence') lowConf += 1;
      if (res.gate === 'critic_exhausted') criticExhausted += 1;
      if (res.retrieval_cache_hit) cacheHits += 1;
      if (res.confidence !== null && res.confidence !== undefined) {
        totalConf += parseFloat(res.confidence);
        confCount += 1;
      }
    });

    const avgConf = confCount > 0 ? Math.round((totalConf / confCount) * 100) : 85;

    container.innerHTML = `
      <div style="width:100%;display:grid;grid-template-columns:1fr 1fr;gap:12px;">
        <div style="padding:14px;background:var(--surface-2);border:1px solid var(--line);border-radius:8px">
          <div style="font-size:11.5px;color:var(--ink-3);text-transform:uppercase;letter-spacing:0.04em">Avg Model Confidence</div>
          <div style="font-size:24px;font-weight:700;color:var(--ok);margin-top:4px">${avgConf}%</div>
          <div style="font-size:11.5px;color:var(--ink-3);margin-top:2px">Verified by critic model</div>
        </div>
        <div style="padding:14px;background:var(--surface-2);border:1px solid var(--line);border-radius:8px">
          <div style="font-size:11.5px;color:var(--ink-3);text-transform:uppercase;letter-spacing:0.04em">High Risk Pauses</div>
          <div style="font-size:24px;font-weight:700;color:var(--bad);margin-top:4px">${highRisk}</div>
          <div style="font-size:11.5px;color:var(--ink-3);margin-top:2px">Escalated to human lead</div>
        </div>
        <div style="padding:14px;background:var(--surface-2);border:1px solid var(--line);border-radius:8px">
          <div style="font-size:11.5px;color:var(--ink-3);text-transform:uppercase;letter-spacing:0.04em">Low Confidence Checks</div>
          <div style="font-size:24px;font-weight:700;color:var(--warn);margin-top:4px">${lowConf}</div>
          <div style="font-size:11.5px;color:var(--ink-3);margin-top:2px">Safety threshold &lt; 0.70</div>
        </div>
        <div style="padding:14px;background:var(--surface-2);border:1px solid var(--line);border-radius:8px">
          <div style="font-size:11.5px;color:var(--ink-3);text-transform:uppercase;letter-spacing:0.04em">KBHR Cache Resolutions</div>
          <div style="font-size:24px;font-weight:700;color:var(--violet);margin-top:4px">${cacheHits}</div>
          <div style="font-size:11.5px;color:var(--ink-3);margin-top:2px">Instant human verified reuse</div>
        </div>
      </div>`;
  }

  /* ─────────────────────────────────────────────────────────
     8. TABLE: RECENT INCIDENT ACTIVITY
     ───────────────────────────────────────────────────────── */
  function renderRecentTable(items) {
    const tbody = $('recentTableBody');
    if (!items.length) {
      tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;padding:24px;color:var(--ink-3)">No incidents found</td></tr>';
      return;
    }

    const rows = items.slice(0, 8).map((item) => {
      const exec = item.execution;
      const st = statusOf(item);
      const [tone, label] = STATUS_CONFIG[st] ? [STATUS_CONFIG[st].tone, STATUS_CONFIG[st].label] : ['plain', st];
      const res = exec ? (exec.latest_result || {}) : {};

      let confCell = '—';
      if (res.confidence !== null && res.confidence !== undefined) {
        const conf = parseFloat(res.confidence);
        const pct = Math.round(conf * 100);
        const col = conf >= 0.75 ? 'var(--ok)' : conf >= 0.5 ? 'var(--warn)' : 'var(--bad)';
        confCell = `<span style="font-weight:600;color:${col}">${pct}%</span>`;
      }

      return `
        <tr>
          <td><strong style="color:var(--accent);font-family:ui-monospace,monospace">${escapeHtml(item.number || '—')}</strong></td>
          <td style="max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(item.short_description || '—')}</td>
          <td><span style="color:var(--ink-2)">${escapeHtml(item.category || '—')}</span></td>
          <td>${prioBadgeHtml(item.priority)}</td>
          <td><span class="chip ${tone}" style="font-size:11px">${escapeHtml(label)}</span></td>
          <td>${confCell}</td>
          <td style="font-variant-numeric:tabular-nums;color:var(--ink-3)">${exec ? fmtDuration(exec.duration_seconds) : '—'}</td>
          <td>
            <a class="btn btn-sm" href="pipeline.html" style="padding:0 8px;font-size:11px">Inspect →</a>
          </td>
        </tr>`;
    }).join('');

    tbody.innerHTML = rows;
  }

  /* ─────────────────────────────────────────────────────────
     LOAD & REFRESH DATA
     ───────────────────────────────────────────────────────── */
  async function loadData() {
    try {
      setConn('idle', 'Updating...');
      const data = await api('/api/v1/dashboard/incidents?limit=50');
      latestData = data.incidents || [];
      setConn('ok', 'Live Analytics');

      renderKPIs(latestData);
      renderTimelineChart(latestData);
      renderCategoryChart(latestData);
      renderMTTRChart(latestData);
      renderPriorityChart(latestData);
      renderStatusChart(latestData);
      renderGuardrails(latestData);
      renderRecentTable(latestData);
    } catch (err) {
      setConn('err', 'API unreachable');
    }
  }

  $('refreshBtn').addEventListener('click', loadData);

  loadData();
  // Refresh telemetry every 10 seconds
  setInterval(() => {
    if (!document.hidden) loadData();
  }, 10000);
})();
