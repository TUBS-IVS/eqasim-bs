"""HTML template for the Braunschweig run-comparison dashboard.

This module holds only the ``HTML_TEMPLATE`` string literal, moved here
byte-for-byte from ``build_dashboard.py``. ``build_dashboard.render_dashboard``
fills it via ``HTML_TEMPLATE.replace("__RUNS_JSON__", runs_json)``; the
literal also embeds CSS/JS braces that are not format placeholders, so this
file must never be reformatted or re-indented.

This module must not import ``build_dashboard`` -- that would create an
import cycle between the facade and this leaf module.
"""

from __future__ import annotations


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Braunschweig — Simulation Dashboard</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.6/dist/chart.umd.min.js"></script>
<style>
:root {
  --bg: #f5f5f7;
  --bg-card: #ffffff;
  --bg-soft: #fbfbfd;
  --text: #1d1d1f;
  --text-soft: #6e6e73;
  --line: rgba(0,0,0,0.06);
  --accent: #0066cc;
  --good: #28a745;
  --bad: #d70015;
  --warn: #c79100;
  --shadow: 0 1px 3px rgba(0,0,0,0.04), 0 8px 32px rgba(0,0,0,0.04);
  --radius: 18px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #000;
    --bg-card: #1c1c1e;
    --bg-soft: #2c2c2e;
    --text: #f5f5f7;
    --text-soft: #98989d;
    --line: rgba(255,255,255,0.08);
    --shadow: 0 1px 3px rgba(0,0,0,0.6), 0 8px 32px rgba(0,0,0,0.4);
  }
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text",
               "Helvetica Neue", "Inter", system-ui, sans-serif;
  background: var(--bg);
  color: var(--text);
  -webkit-font-smoothing: antialiased;
  font-feature-settings: "ss01", "tnum";
  letter-spacing: -0.01em;
}
.layout { display: grid; grid-template-columns: 280px 1fr; min-height: 100vh; }
.sidebar {
  background: var(--bg-card);
  border-right: 1px solid var(--line);
  padding: 24px 18px;
  position: sticky;
  top: 0;
  height: 100vh;
  overflow-y: auto;
}
.sidebar h1 {
  font-size: 17px;
  font-weight: 700;
  margin: 0 0 4px 4px;
  letter-spacing: -0.02em;
}
.sidebar p.tag {
  font-size: 11px;
  color: var(--text-soft);
  margin: 0 0 24px 4px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.sidebar h2 {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--text-soft);
  margin: 16px 4px 8px;
}
.run-item {
  display: block;
  padding: 10px 12px;
  border-radius: 10px;
  cursor: pointer;
  margin-bottom: 4px;
  transition: background 120ms;
  border: 1px solid transparent;
}
.run-item:hover { background: var(--bg-soft); }
.run-item.active {
  background: var(--bg-soft);
  border-color: var(--line);
}
.run-item .label { font-size: 13px; font-weight: 600; }
.run-item .meta { font-size: 11px; color: var(--text-soft); margin-top: 2px; }
.run-item .swatch {
  display: inline-block;
  width: 8px; height: 8px;
  border-radius: 50%;
  margin-right: 8px;
  vertical-align: middle;
}

.main { padding: 40px 56px 80px; max-width: 1400px; }
.hero { margin-bottom: 32px; }
.hero h1 {
  font-size: 38px;
  font-weight: 700;
  letter-spacing: -0.03em;
  margin: 0 0 6px;
}
.hero .sub { color: var(--text-soft); font-size: 15px; }

.kpi-grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); margin-bottom: 28px; }
.card {
  background: var(--bg-card);
  border-radius: var(--radius);
  padding: 22px 24px;
  box-shadow: var(--shadow);
}
.card h3 {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-soft);
  margin: 0 0 12px;
}
.kpi { font-size: 36px; font-weight: 700; letter-spacing: -0.02em; line-height: 1.05; }
.kpi small { font-size: 14px; font-weight: 500; color: var(--text-soft); margin-left: 4px; }
.kpi-sub { font-size: 12px; color: var(--text-soft); margin-top: 6px; }
.kpi-delta { display: inline-block; font-size: 12px; font-weight: 600; padding: 3px 8px; border-radius: 999px; margin-left: 8px; }
.kpi-delta.good { background: rgba(40,167,69,0.15); color: var(--good); }
.kpi-delta.bad  { background: rgba(215,0,21,0.15); color: var(--bad); }
.kpi-delta.warn { background: rgba(199,145,0,0.15); color: var(--warn); }
.kpi-delta.flat { background: var(--bg-soft); color: var(--text-soft); }

.section { margin-top: 36px; }
.section h2 {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
  margin: 0 0 16px;
}
.charts-grid { display: grid; gap: 16px; grid-template-columns: 1fr 1fr; }
.charts-grid .card.full { grid-column: 1 / -1; }
.chart-wrap { position: relative; height: 300px; }

table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; font-weight: 600; color: var(--text-soft); padding: 8px 10px; border-bottom: 1px solid var(--line); }
td { padding: 8px 10px; border-bottom: 1px solid var(--line); }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
tr:last-child td { border-bottom: none; }

.pill {
  display: inline-block;
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
}
.pill.ok  { background: rgba(40,167,69,0.15); color: var(--good); }
.pill.fail{ background: rgba(215,0,21,0.15); color: var(--bad); }

.legend { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 8px; font-size: 12px; color: var(--text-soft); }
.legend span::before { content: "■"; margin-right: 4px; }

.toggle-row { display:flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px;}
.toggle {
  font-size: 12px; font-weight: 600;
  padding: 6px 12px; border-radius: 999px;
  background: var(--bg-soft); color: var(--text-soft);
  cursor: pointer; user-select: none;
  border: 1px solid var(--line);
}
.toggle.on { background: var(--accent); color: #fff; border-color: var(--accent); }

.muted { color: var(--text-soft); font-size: 12px; }
.empty { color: var(--text-soft); font-size: 13px; padding: 12px; }
hr.soft { border: none; border-top: 1px solid var(--line); margin: 24px 0; }
</style>
</head>
<body>
<div class="layout">
  <aside class="sidebar">
    <h1>BS Simulation</h1>
    <p class="tag">Dashboard · MiD 2023 / eqasim</p>

    <h2>Runs</h2>
    <div id="run-list"></div>

    <h2 style="margin-top:24px">Active</h2>
    <div id="active-list" class="muted">—</div>
  </aside>
  <main class="main" id="main"></main>
</div>

<script>
const RUNS_DATA = __RUNS_JSON__;
const PALETTE = ['#0066cc', '#ff9500', '#34c759', '#af52de', '#ff3b30', '#5ac8fa', '#ffcc00'];
const MODE_COLORS = {
  car: '#0066cc', pt: '#34c759', bicycle: '#ff9500', walk: '#af52de', car_passenger: '#5ac8fa',
};

let activeIds = [];

function $(sel, root=document) { return root.querySelector(sel); }
function el(tag, cls, html) { const e = document.createElement(tag); if (cls) e.className = cls; if (html !== undefined) e.innerHTML = html; return e; }
function fmt(n, dp=0) { if (n === null || n === undefined || Number.isNaN(n)) return '—'; return Number(n).toLocaleString('de-DE', {minimumFractionDigits: dp, maximumFractionDigits: dp}); }
function fmtPct(n, dp=1) { if (n===null||n===undefined||Number.isNaN(n)) return '—'; return Number(n).toFixed(dp) + '%'; }
function deltaPill(diff, unit='') {
  if (diff === null || diff === undefined || Number.isNaN(diff)) return '';
  const abs = Math.abs(diff);
  const cls = abs < 1 ? 'flat' : abs < 5 ? 'warn' : (diff > 0 ? 'bad' : 'good');
  const sign = diff > 0 ? '+' : '';
  return `<span class="kpi-delta ${cls}">${sign}${diff.toFixed(1)}${unit}</span>`;
}

function renderSidebar() {
  const list = $('#run-list');
  list.innerHTML = '';
  if (RUNS_DATA.length === 0) {
    list.innerHTML = '<p class="muted" style="margin:8px 4px">No runs yet.</p>';
    return;
  }
  RUNS_DATA.slice().reverse().forEach((r, idx) => {
    const item = el('div', 'run-item');
    if (activeIds.includes(r.run_id)) item.classList.add('active');
    const colorIdx = RUNS_DATA.findIndex(x => x.run_id === r.run_id) % PALETTE.length;
    const color = PALETTE[colorIdx];
    item.innerHTML = `
      <div class="label"><span class="swatch" style="background:${color}"></span>${r.label || r.run_id}</div>
      <div class="meta">${r.created_at?.replace('T',' ').slice(0,16) ?? ''} · ${r.sample_rate ? (r.sample_rate*100)+'%' : '—'}</div>
    `;
    item.onclick = (e) => {
      if (e.shiftKey || e.metaKey || e.ctrlKey) {
        if (activeIds.includes(r.run_id)) activeIds = activeIds.filter(x => x !== r.run_id);
        else activeIds.push(r.run_id);
      } else {
        activeIds = [r.run_id];
      }
      if (activeIds.length === 0) activeIds = [r.run_id];
      render();
    };
    list.appendChild(item);
  });
}

function renderActiveList() {
  const a = $('#active-list');
  a.innerHTML = '';
  if (activeIds.length === 0) { a.textContent = '—'; return; }
  activeIds.forEach((id, i) => {
    const r = RUNS_DATA.find(x => x.run_id === id);
    if (!r) return;
    const colorIdx = RUNS_DATA.findIndex(x => x.run_id === r.run_id) % PALETTE.length;
    a.appendChild(el('div', '', `
      <div style="font-size:12px;margin-bottom:3px">
        <span class="swatch" style="background:${PALETTE[colorIdx]}"></span>
        <strong>${r.label || r.run_id}</strong>
      </div>
    `));
  });
  a.appendChild(el('div', 'muted', '<br>Tip: <em>Shift</em>+click to compare multiple runs.'));
}

function activeRuns() { return activeIds.map(id => RUNS_DATA.find(r => r.run_id === id)).filter(Boolean); }
function colorFor(run) { const i = RUNS_DATA.findIndex(r => r.run_id === run.run_id) % PALETTE.length; return PALETTE[i]; }

function render() {
  if (RUNS_DATA.length === 0) {
    $('#main').innerHTML = `<div class="hero"><h1>No data</h1><p class="sub">Create a run with:<br><code>python -m braunschweig.analysis.dashboard.build_dashboard --output-dir … --sim-cache … --label "v1"</code></p></div>`;
    renderSidebar();
    renderActiveList();
    return;
  }
  if (activeIds.length === 0) activeIds = [RUNS_DATA[RUNS_DATA.length-1].run_id];
  renderSidebar();
  renderActiveList();
  const runs = activeRuns();
  const main = $('#main');
  main.innerHTML = '';

  // Hero
  const hero = el('div', 'hero');
  const lead = runs[0];
  hero.innerHTML = `
    <h1>Braunschweig Simulation</h1>
    <p class="sub">${runs.length === 1 ? 'Run' : runs.length+' runs compared'} · MiD 2023 ZGB as reference · ${lead.created_at?.replace('T',' ').slice(0,16) ?? ''}</p>
  `;
  main.appendChild(hero);

  main.appendChild(renderKPIGrid(runs));
  main.appendChild(renderModeSection(runs));
  main.appendChild(renderDistanceSection(runs));
  main.appendChild(renderTimeOfDaySection(runs));
  main.appendChild(renderConvergenceSection(runs));
  main.appendChild(renderPerKreisSimSection(runs));
  main.appendChild(renderODSection(runs));
  main.appendChild(renderPerKreisSection(runs));
  main.appendChild(renderQualitySection(runs));
}

function renderKPIGrid(runs) {
  const sec = el('div', 'kpi-grid');

  // KPI 1 — Persons
  sec.appendChild(kpiCard(
    'Persons',
    runs.map(r => ({label: r.label, value: r.eqasim?.n_persons, color: colorFor(r)})),
    v => fmt(v),
    runs[0].eqasim?.sample_rate ? `Sampling: ${(runs[0].eqasim.sample_rate*100).toFixed(0)} %` : ''
  ));

  // Trips per person
  sec.appendChild(kpiCard(
    'Trips / person',
    runs.map(r => ({label: r.label, value: r.eqasim?.trips_per_person, color: colorFor(r)})),
    v => fmt(v, 2),
    'MiD-DE mean ≈ 3.0–3.5'
  ));

  // Mean trip km
  sec.appendChild(kpiCard(
    'Mean trip distance',
    runs.map(r => ({label: r.label, value: r.matsim?.mean_trip_km, color: colorFor(r)})),
    v => fmt(v, 1) + ' km',
    'from eqasim_trips.csv'
  ));

  // Commute mean km vs MiD
  sec.appendChild(kpiCard(
    'Mean commute',
    runs.map(r => {
      const c = r.comparisons?.commute_mean_km;
      return {label: r.label, value: c?.sim, sub: c ? deltaPill(c.diff_pct, '%') : '', color: colorFor(r)};
    }),
    v => fmt(v, 1) + ' km',
    `MiD P13 target: ${runs[0].mid_reference?.p13_mean_km_zgb?.toFixed(1)} km`
  ));

  // Earth-mover dist
  const emds = runs.map(r => r.comparisons?.distance_distribution?.emd);
  sec.appendChild(kpiCard(
    'Distance EMD vs MiD',
    runs.map((r, i) => ({label: r.label, value: emds[i], color: colorFor(r)})),
    v => v == null ? '—' : v.toFixed(3),
    'Quality threshold ≤ 0.080'
  ));

  // Final score
  sec.appendChild(kpiCard(
    'Final score',
    runs.map(r => ({label: r.label, value: r.matsim?.score_final, color: colorFor(r)})),
    v => v == null ? '—' : v.toFixed(2),
    runs[0].matsim?.last_iteration != null ? `after iter ${runs[0].matsim.last_iteration}${runs[0].matsim.terminated_early ? ' (early stop)' : ''}` : ''
  ));

  // Iteration count
  sec.appendChild(kpiCard(
    'Iterations',
    runs.map(r => ({label: r.label, value: r.matsim?.last_iteration, color: colorFor(r)})),
    v => v == null ? '—' : (v + 1) + '',
    runs[0].matsim?.mean_iter_minutes ? `⌀ ${runs[0].matsim.mean_iter_minutes} min/iter` : ''
  ));

  // Female / urban / employed share
  sec.appendChild(kpiCard(
    'Employed',
    runs.map(r => ({label: r.label, value: r.eqasim?.share_employed_pct, color: colorFor(r)})),
    v => fmtPct(v, 1),
    'MiD P9: ~50 %'
  ));

  return sec;
}

function kpiCard(title, items, fmtFn, sub='') {
  const c = el('div', 'card');
  c.appendChild(el('h3', '', title));
  items.forEach((it, i) => {
    const row = el('div', '', '');
    if (items.length === 1) {
      row.innerHTML = `<div class="kpi" style="color:${it.color}">${fmtFn(it.value)}${it.sub || ''}</div>`;
    } else {
      row.style.display = 'flex'; row.style.alignItems = 'baseline'; row.style.justifyContent = 'space-between';
      row.style.marginBottom = i === items.length-1 ? '0' : '4px';
      row.innerHTML = `
        <div style="font-size:12px;color:var(--text-soft);max-width:60%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
          <span class="swatch" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${it.color};margin-right:6px"></span>${it.label}
        </div>
        <div style="font-size:18px;font-weight:600">${fmtFn(it.value)} ${it.sub || ''}</div>
      `;
    }
    c.appendChild(row);
  });
  if (sub) c.appendChild(el('div', 'kpi-sub', sub));
  return c;
}

function sectionWrap(title) {
  const s = el('div', 'section');
  s.appendChild(el('h2', '', title));
  return s;
}

function renderModeSection(runs) {
  const s = sectionWrap('Mode share');
  const grid = el('div', 'charts-grid');

  // Card 1: All-trip mode share — sim final
  const c1 = el('div', 'card');
  c1.appendChild(el('h3','', 'All trips share (final)'));
  const wrap1 = el('div', 'chart-wrap'); c1.appendChild(wrap1); grid.appendChild(c1);
  const allModes = Array.from(new Set(runs.flatMap(r => Object.keys(r.matsim?.mode_share_pct_final || {}))));
  new Chart(wrap1.appendChild(document.createElement('canvas')), {
    type: 'bar',
    data: {
      labels: allModes,
      datasets: runs.map(r => ({
        label: r.label,
        data: allModes.map(m => r.matsim?.mode_share_pct_final?.[m] ?? 0),
        backgroundColor: colorFor(r),
        borderRadius: 6,
      })),
    },
    options: chartOpts({yLabel: '%'}),
  });

  // Card 2: Work-commute sim vs MiD
  const c2 = el('div', 'card');
  c2.appendChild(el('h3','', 'Commute trips — Sim vs. MiD P12_1'));
  const wrap2 = el('div', 'chart-wrap'); c2.appendChild(wrap2); grid.appendChild(c2);
  const cmp0 = runs[0].comparisons?.work_mode_share;
  if (cmp0) {
    const datasets = [];
    runs.forEach(r => {
      const cmp = r.comparisons?.work_mode_share;
      if (!cmp) return;
      datasets.push({label: r.label + ' (Sim)', data: cmp.sim_pct, backgroundColor: colorFor(r), borderRadius: 6});
    });
    datasets.push({label: 'MiD 2023', data: cmp0.mid_pct, backgroundColor: '#999', borderRadius: 6, borderColor: '#000', borderWidth: 1});
    new Chart(wrap2.appendChild(document.createElement('canvas')), {
      type: 'bar',
      data: { labels: cmp0.modes, datasets },
      options: chartOpts({yLabel: '%'}),
    });
    c2.appendChild(el('p','muted', cmp0.note || ''));
  } else {
    c2.appendChild(el('p','empty','No commute mode-share data.'));
  }

  // Card 3: Mode share evolution (single run only — fan out per mode)
  const c3 = el('div', 'card full');
  c3.appendChild(el('h3','', 'Mode share evolution (across iterations)'));
  const wrap3 = el('div', 'chart-wrap'); c3.appendChild(wrap3); grid.appendChild(c3);
  const datasets3 = [];
  runs.forEach(r => {
    const ev = r.matsim?.mode_share_evolution;
    if (!ev) return;
    (r.matsim.modes || []).forEach((m, i) => {
      datasets3.push({
        label: `${r.label} · ${m}`,
        data: ev[m],
        borderColor: MODE_COLORS[m] || PALETTE[i],
        backgroundColor: 'transparent',
        borderWidth: 2,
        pointRadius: 0,
        borderDash: runs.length > 1 && r === runs[0] ? [] : (runs.length > 1 ? [4,3] : []),
      });
    });
  });
  if (datasets3.length) {
    new Chart(wrap3.appendChild(document.createElement('canvas')), {
      type: 'line',
      data: { labels: runs[0].matsim.mode_share_evolution.iterations, datasets: datasets3 },
      options: chartOpts({yLabel: '%', xLabel: 'Iteration'}),
    });
  } else {
    c3.appendChild(el('p','empty','—'));
  }

  s.appendChild(grid);
  return s;
}

function renderDistanceSection(runs) {
  const s = sectionWrap('Trip and commute distances');
  const grid = el('div', 'charts-grid');

  // Card 1: commute distance distribution vs MiD bands
  const c1 = el('div', 'card full');
  c1.appendChild(el('h3', '', 'Commute distance distribution — Sim vs. MiD P13 (ZGB)'));
  const wrap1 = el('div', 'chart-wrap'); c1.appendChild(wrap1); grid.appendChild(c1);
  const dd = runs[0].comparisons?.distance_distribution;
  if (dd) {
    const datasets = [];
    runs.forEach(r => {
      const d = r.comparisons?.distance_distribution;
      if (!d) return;
      datasets.push({label: r.label + ' (Sim)', data: d.sim_pct, backgroundColor: colorFor(r), borderRadius: 6});
    });
    datasets.push({label: 'MiD 2023', data: dd.mid_pct, backgroundColor: '#bbb', borderRadius: 6});
    new Chart(wrap1.appendChild(document.createElement('canvas')), {
      type: 'bar',
      data: { labels: dd.bands, datasets },
      options: chartOpts({yLabel: '%', xLabel: 'km class'}),
    });
    const emd = runs[0].comparisons.distance_distribution.emd;
    const ok = runs[0].comparisons.distance_distribution.ok;
    c1.appendChild(el('p','muted',`EMD = ${emd.toFixed(3)} (threshold ≤ 0.08) <span class="pill ${ok?'ok':'fail'}">${ok?'OK':'FAIL'}</span>`));
  } else {
    c1.appendChild(el('p','empty','—'));
  }

  // Card 2: mean km by mode
  const c2 = el('div', 'card');
  c2.appendChild(el('h3','', 'Mean distance by mode'));
  const wrap2 = el('div', 'chart-wrap'); c2.appendChild(wrap2); grid.appendChild(c2);
  const allModes = Array.from(new Set(runs.flatMap(r => Object.keys(r.matsim?.mean_km_by_mode || {}))));
  new Chart(wrap2.appendChild(document.createElement('canvas')), {
    type: 'bar',
    data: {
      labels: allModes,
      datasets: runs.map(r => ({
        label: r.label,
        data: allModes.map(m => r.matsim?.mean_km_by_mode?.[m] ?? null),
        backgroundColor: colorFor(r),
        borderRadius: 6,
      })),
    },
    options: chartOpts({yLabel: 'km'}),
  });

  // Card 3: mean km by purpose
  const c3 = el('div', 'card');
  c3.appendChild(el('h3','', 'Mean distance by purpose'));
  const wrap3 = el('div', 'chart-wrap'); c3.appendChild(wrap3); grid.appendChild(c3);
  const allPur = Array.from(new Set(runs.flatMap(r => Object.keys(r.matsim?.mean_km_by_purpose || {}))));
  new Chart(wrap3.appendChild(document.createElement('canvas')), {
    type: 'bar',
    data: {
      labels: allPur,
      datasets: runs.map(r => ({
        label: r.label,
        data: allPur.map(p => r.matsim?.mean_km_by_purpose?.[p] ?? null),
        backgroundColor: colorFor(r),
        borderRadius: 6,
      })),
    },
    options: chartOpts({yLabel: 'km'}),
  });

  s.appendChild(grid);
  return s;
}

function renderConvergenceSection(runs) {
  const s = sectionWrap('Convergence');
  const grid = el('div', 'charts-grid');

  const c1 = el('div', 'card');
  c1.appendChild(el('h3','', 'Score (avg_executed)'));
  const wrap1 = el('div', 'chart-wrap'); c1.appendChild(wrap1); grid.appendChild(c1);
  const ds1 = runs.map(r => r.matsim?.score_evolution ? ({
    label: r.label,
    data: r.matsim.score_evolution.avg_executed,
    borderColor: colorFor(r),
    backgroundColor: 'transparent',
    borderWidth: 2,
    pointRadius: 0,
  }) : null).filter(Boolean);
  if (ds1.length) {
    new Chart(wrap1.appendChild(document.createElement('canvas')), {
      type: 'line',
      data: { labels: runs[0].matsim.score_evolution.iterations, datasets: ds1 },
      options: chartOpts({yLabel: 'score', xLabel: 'Iteration'}),
    });
  } else c1.appendChild(el('p','empty','—'));

  const c2 = el('div', 'card');
  c2.appendChild(el('h3','', 'Mean iter trip distance'));
  const wrap2 = el('div', 'chart-wrap'); c2.appendChild(wrap2); grid.appendChild(c2);
  const ds2 = runs.map(r => r.matsim?.distance_evolution ? ({
    label: r.label,
    data: r.matsim.distance_evolution.avg_trip_km,
    borderColor: colorFor(r),
    backgroundColor: 'transparent',
    borderWidth: 2,
    pointRadius: 0,
  }) : null).filter(Boolean);
  if (ds2.length) {
    new Chart(wrap2.appendChild(document.createElement('canvas')), {
      type: 'line',
      data: { labels: runs[0].matsim.distance_evolution.iterations, datasets: ds2 },
      options: chartOpts({yLabel: 'km', xLabel: 'Iteration'}),
    });
  } else c2.appendChild(el('p','empty','—'));

  s.appendChild(grid);
  return s;
}

function renderPerKreisSection(runs) {
  const s = sectionWrap('Per-Kreis reference (MiD)');
  const card = el('div', 'card full');
  const ref = runs[0].mid_reference;
  if (!ref?.available || !ref.p13_per_kreis?.length) {
    card.appendChild(el('p','empty','MiD reference not loaded.'));
    s.appendChild(card);
    return s;
  }
  const tbl = el('table');
  tbl.innerHTML = `
    <thead><tr>
      <th>Kreis</th><th>ARS</th>
      <th class="num">MiD mean commute km</th>
      <th class="num">MiD car %</th>
      <th class="num">MiD PT %</th>
      <th class="num">MiD bicycle %</th>
      <th class="num">MiD walk %</th>
      <th class="num">n (weighted)</th>
    </tr></thead>
    <tbody></tbody>
  `;
  const tb = tbl.querySelector('tbody');
  ref.p13_per_kreis.forEach(k => {
    const p12 = (ref.p12_per_kreis || []).find(x => x.ars5 === k.ars5) || {};
    const tr = el('tr');
    tr.innerHTML = `
      <td>${k.name}</td><td>${k.ars5}</td>
      <td class="num">${k.mean_km.toFixed(1)}</td>
      <td class="num">${p12.auto?.toFixed(0) ?? '—'}</td>
      <td class="num">${p12.oeffentlich?.toFixed(0) ?? '—'}</td>
      <td class="num">${p12.fahrrad?.toFixed(0) ?? '—'}</td>
      <td class="num">${p12.zu_fuss?.toFixed(0) ?? '—'}</td>
      <td class="num">${k.n_weighted.toFixed(0)}</td>
    `;
    tb.appendChild(tr);
  });
  card.appendChild(tbl);
  card.appendChild(el('p','muted','Note: P12_1 reports “every mode used” per commute, so rows can sum >100 %. Per-Kreis sim values are shown in the dedicated Sim section above.'));
  s.appendChild(card);
  return s;
}

function renderQualitySection(runs) {
  const s = sectionWrap('Quality checks');
  const grid = el('div', 'charts-grid');
  runs.forEach(r => {
    const c = el('div', 'card');
    c.appendChild(el('h3','', r.label));
    const checks = [
      ['EMD ≤ 0.08 (MiD distance)', r.comparisons?.distance_distribution?.ok, r.comparisons?.distance_distribution?.emd?.toFixed(3)],
      ['Mean commute within ±20 % of MiD', Math.abs(r.comparisons?.commute_mean_km?.diff_pct ?? 999) <= 20, (r.comparisons?.commute_mean_km?.diff_pct?.toFixed(1) ?? '—') + '%'],
      ['Trips/person 2.5–4.0', r.eqasim?.trips_per_person >= 2.5 && r.eqasim?.trips_per_person <= 4.0, r.eqasim?.trips_per_person],
      ['p95 commute ≤ 200 km', (r.matsim?.commute?.p95_km ?? 999) <= 200, r.matsim?.commute?.p95_km?.toFixed(0) + ' km'],
      ['Score increasing (final > iter 0)', (r.matsim?.score_evolution?.avg_executed?.slice(-1)[0] ?? -1e9) > (r.matsim?.score_evolution?.avg_executed?.[0] ?? 0), r.matsim?.score_final],
    ];
    const tbl = el('table');
    tbl.innerHTML = '<thead><tr><th>Check</th><th class="num">Value</th><th>Status</th></tr></thead><tbody></tbody>';
    const tb = tbl.querySelector('tbody');
    checks.forEach(([name, ok, val]) => {
      const tr = el('tr');
      tr.innerHTML = `<td>${name}</td><td class="num">${val ?? '—'}</td><td><span class="pill ${ok?'ok':'fail'}">${ok?'OK':'FAIL'}</span></td>`;
      tb.appendChild(tr);
    });
    c.appendChild(tbl);
    grid.appendChild(c);
  });
  s.appendChild(grid);
  return s;
}

function renderTimeOfDaySection(runs) {
  const s = sectionWrap('Time-of-day distribution');
  const grid = el('div', 'charts-grid');
  const lead = runs[0];
  const tod = lead.matsim?.time_of_day;
  if (!tod) {
    const c = el('div','card full');
    c.appendChild(el('p','empty','No time-of-day data (run an updated dashboard build).'));
    grid.appendChild(c); s.appendChild(grid); return s;
  }

  // Trips per hour (totals across runs, line chart)
  const c1 = el('div','card full');
  c1.appendChild(el('h3','', 'Trips per hour (totals)'));
  const w1 = el('div','chart-wrap'); c1.appendChild(w1); grid.appendChild(c1);
  const ds1 = runs.map(r => r.matsim?.time_of_day ? ({
    label: r.label,
    data: r.matsim.time_of_day.total_per_hour,
    borderColor: colorFor(r),
    backgroundColor: colorFor(r) + '22',
    borderWidth: 2, pointRadius: 0, fill: true, tension: 0.25,
  }) : null).filter(Boolean);
  new Chart(w1.appendChild(document.createElement('canvas')), {
    type: 'line',
    data: { labels: tod.hours.map(h => h.toString().padStart(2,'0')+':00'), datasets: ds1 },
    options: chartOpts({yLabel: 'trips', xLabel: 'hour'}),
  });

  // Stacked-by-mode (lead run only)
  const c2 = el('div','card');
  c2.appendChild(el('h3','', `Trips per hour by mode \u2014 ${lead.label}`));
  const w2 = el('div','chart-wrap'); c2.appendChild(w2); grid.appendChild(c2);
  const modes = Object.keys(tod.by_mode);
  new Chart(w2.appendChild(document.createElement('canvas')), {
    type: 'bar',
    data: {
      labels: tod.hours,
      datasets: modes.map((m, i) => ({
        label: m,
        data: tod.by_mode[m],
        backgroundColor: MODE_COLORS[m] || PALETTE[i % PALETTE.length],
        stack: 'm',
      })),
    },
    options: { ...chartOpts({yLabel: 'trips', xLabel: 'hour'}),
      scales: { x: {stacked: true}, y: {stacked: true, beginAtZero: true} } },
  });

  // Stacked-by-purpose
  const c3 = el('div','card');
  c3.appendChild(el('h3','', `Trips per hour by purpose \u2014 ${lead.label}`));
  const w3 = el('div','chart-wrap'); c3.appendChild(w3); grid.appendChild(c3);
  const purs = Object.keys(tod.by_purpose);
  new Chart(w3.appendChild(document.createElement('canvas')), {
    type: 'bar',
    data: {
      labels: tod.hours,
      datasets: purs.map((p, i) => ({
        label: p, data: tod.by_purpose[p],
        backgroundColor: PALETTE[i % PALETTE.length], stack: 'p',
      })),
    },
    options: { ...chartOpts({yLabel: 'trips', xLabel: 'hour'}),
      scales: { x: {stacked: true}, y: {stacked: true, beginAtZero: true} } },
  });

  s.appendChild(grid);
  return s;
}

function renderPerKreisSimSection(runs) {
  const s = sectionWrap('Per-Kreis simulation values');
  const card = el('div','card full');
  const lead = runs[0];
  const sim = lead.matsim?.per_kreis_sim;
  const ref = lead.mid_reference;
  if (!sim || Object.keys(sim).length === 0) {
    card.appendChild(el('p','empty','Per-Kreis spatial join unavailable (VG250 missing or geopandas error). Re-run the dashboard build to populate.'));
    s.appendChild(card); return s;
  }
  const tbl = el('table');
  tbl.innerHTML = `
    <thead><tr>
      <th>Kreis</th><th class="num">Sim n trips</th>
      <th class="num">Sim mean km</th><th class="num">MiD mean km</th><th class="num">\u0394 km</th>
      <th class="num">Sim car %</th><th class="num">Sim PT %</th>
      <th class="num">Sim bicycle %</th><th class="num">Sim walk %</th>
    </tr></thead><tbody></tbody>`;
  const tb = tbl.querySelector('tbody');
  Object.entries(sim).forEach(([ars5, k]) => {
    const refKreis = (ref?.p13_per_kreis || []).find(x => x.ars5 === ars5);
    const dKm = refKreis ? (k.mean_km - refKreis.mean_km) : null;
    const ms = k.mode_share_pct || {};
    const tr = el('tr');
    tr.innerHTML = `
      <td>${k.name}</td>
      <td class="num">${k.n_trips.toLocaleString()}</td>
      <td class="num">${k.mean_km.toFixed(1)}</td>
      <td class="num">${refKreis ? refKreis.mean_km.toFixed(1) : '\u2014'}</td>
      <td class="num">${dKm == null ? '\u2014' : (dKm > 0 ? '+' : '') + dKm.toFixed(1)}</td>
      <td class="num">${(ms.car ?? 0).toFixed(0)}</td>
      <td class="num">${(ms.pt ?? 0).toFixed(0)}</td>
      <td class="num">${(ms.bicycle ?? 0).toFixed(0)}</td>
      <td class="num">${(ms.walk ?? 0).toFixed(0)}</td>
    `;
    tb.appendChild(tr);
  });
  card.appendChild(tbl);
  card.appendChild(el('p','muted','Sim trips classified by spatial join of the trip origin (home end of commute) against VG250 Kreis polygons. Sim mode-share is the main mode; MiD P12_1 is any-mode-used and not directly comparable.'));
  s.appendChild(card);
  return s;
}

function renderODSection(runs) {
  const s = sectionWrap('Origin-Destination by activity type');
  const lead = runs[0];
  const od = lead.matsim?.od_matrix;
  const card = el('div','card full');
  if (!od || !od.purposes?.length) {
    card.appendChild(el('p','empty','OD matrix unavailable for this run.'));
    s.appendChild(card); return s;
  }
  // Controls
  const ctrl = el('div','toggle-row');
  od.purposes.forEach((p, i) => {
    const t = el('span','toggle' + (i === 0 ? ' on' : ''), p);
    t.dataset.pur = p;
    t.onclick = () => {
      ctrl.querySelectorAll('.toggle').forEach(x => x.classList.remove('on'));
      t.classList.add('on');
      drawHeat(p);
    };
    ctrl.appendChild(t);
  });
  card.appendChild(ctrl);
  const heat = el('div'); heat.style.overflowX = 'auto';
  card.appendChild(heat);
  card.appendChild(el('p','muted','Rows = origin Kreis, columns = destination Kreis. Cells encode trip counts for the selected purpose; colour intensity is normalised per matrix. \u201cOutside ZGB\u201d aggregates trips touching Kreise outside the 8-Kreis ZGB area.'));

  function drawHeat(pur) {
    const m = od.matrices[pur];
    const max = Math.max(1, ...m.flat());
    let html = '<table style="border-collapse:collapse;font-size:11px;font-variant-numeric:tabular-nums">';
    html += '<thead><tr><th></th>';
    od.zone_names.forEach(n => { html += `<th style="padding:4px 6px;writing-mode:vertical-rl;transform:rotate(180deg);height:90px">${n}</th>`; });
    html += '</tr></thead><tbody>';
    m.forEach((row, i) => {
      html += `<tr><td style="padding:4px 8px;font-weight:600">${od.zone_names[i]}</td>`;
      row.forEach(v => {
        const a = v / max;
        const c = `rgba(0, 102, 204, ${a.toFixed(2)})`;
        const txt = v >= 100 ? Math.round(v).toLocaleString() : (v > 0 ? v.toString() : '');
        html += `<td style="padding:4px 6px;text-align:right;background:${c};color:${a>0.5?'#fff':'inherit'};border:1px solid var(--line)">${txt}</td>`;
      });
      html += '</tr>';
    });
    html += '</tbody></table>';
    heat.innerHTML = html;
  }
  drawHeat(od.purposes[0]);

  s.appendChild(card);
  return s;
}

function chartOpts({yLabel='', xLabel=''}={}) {
  const isDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  const grid = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';
  const tick = isDark ? '#98989d' : '#6e6e73';
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { labels: { color: tick, font: {size: 11, family: '-apple-system, BlinkMacSystemFont, system-ui, sans-serif'} } },
      tooltip: { mode: 'index', intersect: false }
    },
    scales: {
      x: { grid: { color: grid, drawBorder: false }, ticks: { color: tick, font: {size: 11} }, title: { display: !!xLabel, text: xLabel, color: tick } },
      y: { grid: { color: grid, drawBorder: false }, ticks: { color: tick, font: {size: 11} }, title: { display: !!yLabel, text: yLabel, color: tick }, beginAtZero: true },
    },
  };
}

render();
</script>
</body>
</html>"""

