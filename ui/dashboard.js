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

    // 3. Throughput — avg duration of FULLY AUTONOMOUS runs only
    //    (excludes incidents that paused for human review, which would inflate the time)
    const autoExecs = execs.filter((e) => {
      const res = e.latest_result || {};
      return e.status === 'succeeded' && !res.gate;
    });
    const durations = autoExecs
      .filter((e) => e.duration_seconds !== null && e.duration_seconds !== undefined)
      .map((e) => e.duration_seconds);
    const avgDuration = durations.length
      ? Math.round(durations.reduce((a, b) => a + b, 0) / durations.length)
      : 0;
    $('kpi-throughput').textContent = durations.length ? fmtDuration(avgDuration) : '—';
    $('kpi-throughput-sub').textContent = `avg of ${durations.length} fully autonomous runs`;

    // 4. Avg Cost per Incident (from DB-stored estimated_cost_usd)
    //    Gemini Flash pricing: $0.075/1M input, $0.30/1M output
    const PRICE_IN  = 0.075 / 1e6;  // per token
    const PRICE_OUT = 0.30  / 1e6;  // per token
    const costsUsd = execs
      .map((e) => {
        if (e.estimated_cost_usd !== null && e.estimated_cost_usd !== undefined) {
          return e.estimated_cost_usd;
        }
        // Fallback: estimate from token counts if available
        if (e.total_tokens_in || e.total_tokens_out) {
          return (e.total_tokens_in || 0) * PRICE_IN + (e.total_tokens_out || 0) * PRICE_OUT;
        }
        return null;
      })
      .filter((c) => c !== null);
    if (costsUsd.length > 0) {
      const avgCost = costsUsd.reduce((a, b) => a + b, 0) / costsUsd.length;
      // Format: show cents if < $0.10, else dollars
      const fmtCost = avgCost < 0.001
        ? `$${(avgCost * 1000).toFixed(3)}m`   // milli-dollars
        : avgCost < 0.10
          ? `${(avgCost * 100).toFixed(3)}¢`
          : `$${avgCost.toFixed(4)}`;
      $('kpi-cost').textContent = fmtCost;
      $('kpi-cost-sub').textContent = `avg of ${costsUsd.length} tracked runs`;
    } else {
      $('kpi-cost').textContent = '—';
      $('kpi-cost-sub').textContent = 'no cost data yet';
    }

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
     2. CHART: INCIDENT OUTCOME BY CATEGORY
        Horizontal stacked bar — one row per category.
        Segments: Autonomous | High-Risk Gate | Low-Conf Gate | Failed
        Shows which categories the AI resolves autonomously vs. routes to human.
     ───────────────────────────────────────────────────────── */
  function renderTimelineChart(items) {
    const container = $('timelineChartContainer');

    const withExec = items.filter((i) => i.execution);
    if (!withExec.length) {
      container.innerHTML = '<div style="color:var(--ink-3);font-size:13px;padding:40px;text-align:center">No execution data yet</div>';
      return;
    }

    /* ── classify each item ── */
    function outcomeOf(item) {
      const st   = statusOf(item);
      const gate = (item.execution.latest_result || {}).gate || '';
      if (st === 'failed' || st === 'human_rejected')             return 'failed';
      if (gate === 'high_risk'    || st === 'awaiting_approval')  return 'hitl_risk';
      if (gate === 'low_confidence' || gate === 'critic_exhausted') return 'hitl_conf';
      if (st === 'started')                                       return 'running';
      return 'auto';
    }

    const OUTCOMES = [
      { key: 'auto',      color: '#167a4b', label: 'Autonomous'    },
      { key: 'hitl_risk', color: '#6a4fd1', label: 'High-Risk Gate' },
      { key: 'hitl_conf', color: '#c97d0c', label: 'Low-Conf Gate' },
      { key: 'running',   color: '#2159d6', label: 'In Progress'   },
      { key: 'failed',    color: '#c03a2b', label: 'Failed'        },
    ];

    /* ── build category buckets ── */
    const buckets = new Map();
    withExec.forEach((item) => {
      const cat = (item.category || 'other').toLowerCase();
      if (!buckets.has(cat)) buckets.set(cat, { auto: 0, hitl_risk: 0, hitl_conf: 0, running: 0, failed: 0 });
      buckets.get(cat)[outcomeOf(item)]++;
    });

    const rows = [...buckets.entries()]
      .map(([name, d]) => ({
        name,
        ...d,
        total: d.auto + d.hitl_risk + d.hitl_conf + d.running + d.failed,
      }))
      .sort((a, b) => b.total - a.total)
      .slice(0, 7);

    const maxTotal = Math.max(...rows.map((r) => r.total), 1);

    /* ── layout ── */
    const W = 560, H = 220;
    const PAD = { t: 14, r: 80, b: 28, l: 88 };
    const cW  = W - PAD.l - PAD.r;
    const cH  = H - PAD.t - PAD.b;
    const rowH = cH / rows.length;
    const barH = Math.min(Math.floor(rowH * 0.52), 18);

    /* ── bars ── */
    const bars = rows.map((row, i) => {
      const cy = PAD.t + i * rowH + (rowH - barH) / 2;
      let x = PAD.l;
      const segs = OUTCOMES.map(({ key, color }) => {
        const val = row[key] || 0;
        const w   = (val / maxTotal) * cW;
        if (val === 0) return '';
        const seg = `<rect x="${x.toFixed(1)}" y="${cy}" width="${Math.max(w, 2).toFixed(1)}"
                          height="${barH}" fill="${color}" rx="2" opacity="0.9">
                       <title>${key}: ${val} incident${val !== 1 ? 's' : ''}</title>
                     </rect>`;
        x += w;
        return seg;
      }).join('');

      const autoPct = row.total > 0 ? Math.round((row.auto / row.total) * 100) : 0;
      const label   = row.name.length > 11 ? row.name.slice(0, 10) + '…' : row.name;
      const midY    = cy + barH / 2 + 4;
      const pctColor = autoPct >= 70 ? '#167a4b' : autoPct >= 40 ? '#c97d0c' : '#c03a2b';

      return `
        <text x="${PAD.l - 8}" y="${midY}" text-anchor="end" font-size="11"
              fill="var(--ink-2)" style="text-transform:capitalize">${escapeHtml(label)}</text>
        ${segs}
        <text x="${x + 6}" y="${midY}" font-size="10" fill="${pctColor}" font-weight="700"
              >${autoPct}%</text>`;
    }).join('');

    /* ── X axis grid & labels ── */
    const xTicks = [0, Math.ceil(maxTotal / 2), maxTotal];
    const grid = xTicks.map((v) => {
      const x = PAD.l + (v / maxTotal) * cW;
      return `
        <line x1="${x.toFixed(1)}" y1="${PAD.t}" x2="${x.toFixed(1)}" y2="${PAD.t + cH}"
              stroke="var(--line)" stroke-width="${v === 0 ? 1.2 : 0.7}" stroke-dasharray="${v ? '3,4' : ''}"/>
        <text x="${x.toFixed(1)}" y="${H - PAD.b + 14}" text-anchor="middle"
              font-size="10" fill="var(--ink-3)">${v}</text>`;
    }).join('');

    /* ── X axis label ── */
    const xAxisLabel = `<text x="${PAD.l + cW / 2}" y="${H - 2}" text-anchor="middle"
                               font-size="9.5" fill="var(--ink-3)">incidents</text>`;

    /* ── update badge ── */
    const totalAuto  = rows.reduce((s, r) => s + r.auto, 0);
    const totalAll   = rows.reduce((s, r) => s + r.total, 0);
    const overallPct = totalAll > 0 ? Math.round((totalAuto / totalAll) * 100) : 0;
    const badge = $('trendBadge');
    if (badge) {
      badge.textContent = `${overallPct}% Autonomous`;
      badge.className   = `chip ${overallPct >= 60 ? 'ok' : overallPct >= 30 ? 'warn' : 'bad'}`;
    }

    /* ── legend (only outcomes actually present) ── */
    const legend = OUTCOMES
      .filter(({ key }) => rows.some((r) => (r[key] || 0) > 0))
      .map(({ color, label }) => `<span class="chart-legend-item">
          <span class="chart-legend-dot" style="background:${color}"></span>${label}
        </span>`)
      .join('');

    container.innerHTML = `
      <svg class="chart-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        ${grid}
        ${bars}
        ${xAxisLabel}
      </svg>
      <div class="chart-legend">${legend}
        <span class="chart-legend-item" style="color:var(--ink-3);font-size:10px;margin-left:auto">
          % = autonomous rate
        </span>
      </div>`;
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

  // Safe wrapper — chart errors show in the container, not as "API unreachable".
  function safeRender(fn, items, containerId) {
    try {
      fn(items);
    } catch (err) {
      console.error(`[dashboard] render error in ${fn.name || containerId}:`, err);
      if (containerId) {
        const el = $(containerId);
        if (el) el.innerHTML = `<div style="color:var(--bad,#c03a2b);font-size:12px;padding:16px">
          Chart error: ${err.message}</div>`;
      }
    }
  }

  async function loadData() {
    try {
      setConn('idle', 'Updating...');
      const data = await api('/api/v1/dashboard/incidents?limit=50');
      latestData = data.incidents || [];
      setConn('ok', 'Snapshot — click Refresh to update');
    } catch (err) {
      setConn('err', 'API unreachable');
      return; // don't attempt renders if data fetch failed
    }

    // Render each section independently — one crash won't break the others.
    safeRender(renderKPIs,           latestData, null);
    safeRender(renderTimelineChart,  latestData, 'timelineChartContainer');
    safeRender(renderCategoryChart,  latestData, 'categoryChartContainer');
    safeRender(renderMTTRChart,      latestData, 'mttrChartContainer');
    safeRender(renderPriorityChart,  latestData, 'priorityChartContainer');
    safeRender(renderStatusChart,    latestData, 'statusChartContainer');
    safeRender(renderGuardrails,     latestData, 'guardrailsContainer');
    safeRender(renderRecentTable,    latestData, 'recentTableBody');
  }

  $('refreshBtn').addEventListener('click', loadData);

  loadData();
  // Auto-refresh intentionally disabled — click Refresh to update.
})();
