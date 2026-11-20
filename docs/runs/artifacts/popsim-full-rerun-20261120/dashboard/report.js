// Population rerun report: drawings for the facts, controls, cells and plausibility datasets.
(function () {
  'use strict';

  // ---------- formatting ----------
  const nf = (o) => new Intl.NumberFormat('de-DE', o);
  const F = {
    int: nf({ maximumFractionDigits: 0 }),
    dec1: nf({ minimumFractionDigits: 1, maximumFractionDigits: 1 }),
    dec2: nf({ minimumFractionDigits: 2, maximumFractionDigits: 2 }),
    dec3: nf({ minimumFractionDigits: 3, maximumFractionDigits: 3 }),
    dec4: nf({ minimumFractionDigits: 4, maximumFractionDigits: 4 }),
    pct0: nf({ style: 'percent', maximumFractionDigits: 0 }),
    pct1: nf({ style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 }),
  };
  function num(v) { return v === null || v === undefined || v === '' ? NaN : +v; }
  function fmt(v, kind) {
    v = num(v);
    if (!Number.isFinite(v)) return '–';
    switch (kind) {
      case 'int': return F.int.format(v);
      case 'dec1': return F.dec1.format(v);
      case 'dec2': return F.dec2.format(v);
      case 'dec4': return F.dec4.format(v);
      case 'pct0': return F.pct0.format(v);
      case 'pct1': return F.pct1.format(v);
      case 'pp1': return F.dec1.format(v) + ' Pp.';
      case 'x1': return F.dec1.format(v) + '×';
      case 'signpct1': return (v > 0 ? '+' : v < 0 ? '−' : '') + F.pct1.format(Math.abs(v));
      case 'min': return v >= 90 ? Math.floor(v / 60) + ' h ' + Math.round(v % 60) + ' min' : F.dec1.format(v) + ' min';
      default: return F.dec3.format(v);
    }
  }
  const KREIS = { '03101': 'Braunschweig', '03102': 'Salzgitter', '03103': 'Wolfsburg', '03151': 'Gifhorn',
    '03153': 'Goslar', '03154': 'Helmstedt', '03157': 'Peine', '03158': 'Wolfenbüttel', zgb: 'Großraum (ZGB)' };
  const kreisName = (code) => KREIS[String(code).padStart(5, '0')] || KREIS[code] || String(code);

  // ---------- colors ----------
  const COL = () => ({ old: 'var(--cds-chart-muted)', neu: dash.colors[0], ref: 'var(--cds-chart-reference)',
    bad: 'var(--cds-chart-status-critical)', grid: 'var(--cds-chart-grid)', axis: 'var(--cds-chart-axis)' });
  function resolveVar(el, value) {
    const m = /var\((--[^),]+)/.exec(value || '');
    if (!m) return value;
    const v = getComputedStyle(el).getPropertyValue(m[1]).trim();
    return v || '#888';
  }

  // ---------- data access ----------
  let factIndex = null, factData = null;
  function facts() {
    const d = dash.data('facts');
    if (d.status !== 'ok') return null;
    if (d.data !== factData) { factData = d.data; factIndex = new Map(d.data.map((r) => [r.id, r])); }
    return factIndex;
  }
  function section(name) {
    const d = dash.data('facts');
    if (d.status !== 'ok') return null;
    return d.data.filter((r) => r.section === name);
  }
  function placeholder(el, status) {
    el.replaceChildren();
    const p = document.createElement('p');
    p.className = 'pr-muted pr-small';
    p.textContent = status === 'error' ? 'Diese Daten konnten nicht geladen werden.' : '—';
    el.appendChild(p);
  }
  function statusOf(id) { return dash.data(id).status; }

  // ---------- swatches and tile states (outside drawings: style and class only) ----------
  dash.onData(() => {
    document.querySelectorAll('[data-swatch="new"]').forEach((el) => { el.style.background = dash.colors[0]; });
    const F2 = facts();
    if (!F2) return;
    document.querySelectorAll('[data-tile]').forEach((tile) => {
      const r = F2.get(tile.getAttribute('data-tile'));
      const better = tile.getAttribute('data-better');
      tile.classList.remove('is-better', 'is-worse');
      if (!r || better === 'none') return;
      const o = num(r.old), n = num(r.new);
      if (!Number.isFinite(o) || !Number.isFinite(n) || o === n) return;
      const improved = better === 'higher' ? n > o : n < o;
      tile.classList.add(improved ? 'is-better' : 'is-worse');
    });
  });

  // ---------- single values ----------
  dash.draw('fact', (el) => {
    const F2 = facts();
    if (!F2) { el.textContent = '—'; return; }
    const id = (el.getAttribute('data-where') || '').replace(/^id=/, '');
    const r = F2.get(id);
    el.textContent = r ? fmt(r[el.getAttribute('data-field')], el.getAttribute('data-fmt')) : '–';
  });

  // ---------- chart helpers ----------
  function svgFor(el, width, height, label) {
    const svg = d3.select(el).append('svg').attr('width', width).attr('height', height)
      .attr('viewBox', `0 0 ${width} ${height}`).attr('role', 'img').attr('aria-label', label);
    return svg;
  }
  function tipFor(el) {
    const tip = document.createElement('div');
    tip.className = 'pr-tip';
    tip.hidden = true;
    el.appendChild(tip);
    return {
      show(html, event) {
        tip.replaceChildren();
        html.forEach((line, i) => {
          const row = document.createElement('div');
          if (i === 0) { const b = document.createElement('b'); b.textContent = line; row.appendChild(b); }
          else row.textContent = line;
          tip.appendChild(row);
        });
        tip.hidden = false;
        const box = el.getBoundingClientRect();
        let x = event.clientX - box.left + 12, y = event.clientY - box.top + 12;
        const w = tip.offsetWidth, h = tip.offsetHeight;
        if (x + w > box.width) x = Math.max(0, event.clientX - box.left - w - 12);
        if (y + h > box.height + 40) y = Math.max(0, event.clientY - box.top - h - 12);
        tip.style.left = x + 'px';
        tip.style.top = y + 'px';
      },
      hide() { tip.hidden = true; },
    };
  }
  function textWidth(svg, strings, cls) {
    const g = svg.append('g').attr('opacity', 0);
    let w = 0;
    strings.forEach((s) => { const t = g.append('text').attr('class', cls || null).text(s); w = Math.max(w, t.node().getComputedTextLength()); });
    g.remove();
    return w;
  }
  // Rotate band-axis labels that do not fit their band; grow the SVG so the rotated labels stay inside it.
  function fitBandLabels(svg, axisGroup, band) {
    let widest = 0;
    axisGroup.selectAll('text').each(function () { widest = Math.max(widest, this.getComputedTextLength()); });
    if (widest + 6 <= band) return;
    // Vertical labels: their bounding boxes are one line high, so neighbours never overlap.
    axisGroup.selectAll('text').attr('y', 0).attr('x', -9).attr('dy', '0.35em').attr('transform', 'rotate(-90)').attr('text-anchor', 'end');
    const extra = Math.ceil(widest);
    const h = +svg.attr('height') + extra, w = +svg.attr('width');
    svg.attr('height', h).attr('viewBox', `0 0 ${w} ${h}`);
  }
  function legendRow(svg, x, y, items) {
    const g = svg.append('g').attr('transform', `translate(${x},${y})`);
    let cx = 0;
    items.forEach((it) => {
      const gi = g.append('g').attr('transform', `translate(${cx},0)`);
      if (it.kind === 'line') gi.append('line').attr('x1', 0).attr('x2', 14).attr('y1', 0).attr('y2', 0).style('stroke', it.color).attr('stroke-width', 2.5).attr('stroke-dasharray', it.dash || null);
      else if (it.kind === 'ref') gi.append('rect').attr('x', 0).attr('y', -5).attr('width', 10).attr('height', 10).style('fill', 'none').style('stroke', it.color).attr('stroke-width', 2);
      else gi.append('circle').attr('cx', 5).attr('cy', 0).attr('r', 5).style('fill', it.color);
      const t = gi.append('text').attr('x', 19).attr('dy', '0.35em').text(it.label);
      cx += 19 + t.node().getComputedTextLength() + 16;
    });
    return g;
  }

  // ---------- Vielfalt: concentration curve ----------
  dash.draw('concentration', (el) => {
    const rows = section('concentration');
    if (!rows) { placeholder(el, statusOf('facts')); return; }
    el.replaceChildren();
    const c = COL();
    const data = rows.map((r) => ({ k: num(r.item), old: num(r.old), neu: num(r.new) })).filter((d) => d.k >= 1).sort((a, b) => a.k - b.k);
    const width = el.clientWidth || 360, height = 260, m = { t: 24, r: 16, b: 34, l: 44 };
    const svg = svgFor(el, width, height, 'Kumulierter Anteil der Kopien aus den häufigsten Vorlagen, Oktober und neu');
    const x = d3.scaleLog().domain([1, d3.max(data, (d) => d.k)]).range([m.l, width - m.r]);
    const y = d3.scaleLinear().domain([0, 1]).range([height - m.b, m.t]);
    svg.append('g').attr('transform', `translate(0,${height - m.b})`).call(d3.axisBottom(x).ticks(5, '~s').tickFormat((v) => F.int.format(v)).tickSizeOuter(0));
    svg.append('g').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickFormat((v) => F.pct0.format(v)).tickSize(-(width - m.l - m.r))).call((g) => g.select('.domain').remove());
    const line = (key) => d3.line().x((d) => x(d.k)).y((d) => y(d[key]));
    svg.append('path').datum(data).attr('d', line('old')).style('fill', 'none').style('stroke', c.old).attr('stroke-width', 2.5);
    svg.append('path').datum(data).attr('d', line('neu')).style('fill', 'none').style('stroke', c.neu).attr('stroke-width', 2.5);
    const at = (k, key) => { const d = data.reduce((a, b) => (Math.abs(b.k - k) < Math.abs(a.k - k) ? b : a)); return d; };
    [['old', c.old], ['neu', c.neu]].forEach(([key, col]) => {
      const d = at(1000, key);
      svg.append('circle').attr('cx', x(d.k)).attr('cy', y(d[key])).attr('r', 4).style('fill', col);
      svg.append('text').attr('x', x(d.k) + 7).attr('y', y(d[key]) + (key === 'old' ? -6 : 14)).attr('class', 'pr-strong').text(`Top 1.000: ${F.pct0.format(d[key])}`);
    });
    legendRow(svg, m.l, 10, [{ label: 'Oktober', color: c.old, kind: 'line' }, { label: 'Neu', color: c.neu, kind: 'line' }]);
    const tip = tipFor(el);
    svg.append('rect').attr('x', m.l).attr('y', m.t).attr('width', width - m.l - m.r).attr('height', height - m.t - m.b).style('fill', 'transparent')
      .on('mousemove', (ev) => { const k = x.invert(d3.pointer(ev)[0]); const d = at(k); tip.show([`Top ${F.int.format(d.k)} Vorlagen`, `Oktober: ${F.pct1.format(d.old)} der Kopien`, `Neu: ${F.pct1.format(d.neu)} der Kopien`], ev); })
      .on('mouseleave', () => tip.hide());
  });

  // ---------- Vielfalt: copies histogram ----------
  const COPY_BINS = ['1', '2', '3–5', '6–10', '11–50', '51–100', '101–500', '501+'];
  dash.draw('copies', (el) => {
    const rows = section('copies');
    if (!rows) { placeholder(el, statusOf('facts')); return; }
    el.replaceChildren();
    const c = COL();
    const share = new Map(rows.filter((r) => r.sub === 'copy_share').map((r) => [r.item, r]));
    const donors = new Map(rows.filter((r) => r.sub === 'n_donors').map((r) => [r.item, r]));
    const data = COPY_BINS.map((b) => ({ bin: b, old: num(share.get(b)?.old), neu: num(share.get(b)?.new), dOld: num(donors.get(b)?.old), dNew: num(donors.get(b)?.new) }));
    const width = el.clientWidth || 360, height = 260, m = { t: 24, r: 44, b: 22, l: 62 };
    const svg = svgFor(el, width, height, 'Anteil der künstlichen Haushalte nach Kopienzahl ihrer Vorlage');
    const y0 = d3.scaleBand().domain(COPY_BINS).range([m.t, height - m.b]).padding(0.25);
    const y1 = d3.scaleBand().domain(['old', 'neu']).range([0, y0.bandwidth()]).padding(0.08);
    const x = d3.scaleLinear().domain([0, d3.max(data, (d) => Math.max(d.old || 0, d.neu || 0)) * 1.12]).range([m.l, width - m.r]);
    svg.append('g').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y0).tickSize(0)).call((g) => g.select('.domain').remove());
    svg.append('text').attr('x', 0).attr('y', 12).text('Kopien je Vorlage');
    const tip = tipFor(el);
    data.forEach((d) => {
      [['old', c.old, d.old, d.dOld], ['neu', c.neu, d.neu, d.dNew]].forEach(([k, col, v, n]) => {
        if (!Number.isFinite(v)) return;
        svg.append('rect').attr('x', m.l).attr('y', y0(d.bin) + y1(k)).attr('height', y1.bandwidth()).attr('width', Math.max(0, x(v) - m.l)).style('fill', col)
          .on('mousemove', (ev) => tip.show([`${d.bin} Kopien · ${k === 'old' ? 'Oktober' : 'Neu'}`, `${F.pct1.format(v)} aller Haushalte`, `${F.int.format(n)} Vorlagen in dieser Klasse`], ev))
          .on('mouseleave', () => tip.hide());
        svg.append('text').attr('x', x(v) + 4).attr('y', y0(d.bin) + y1(k) + y1.bandwidth() / 2).attr('dy', '0.35em').text(F.pct0.format(v));
      });
    });
    legendRow(svg, Math.max(m.l, width - 170), 12, [{ label: 'Oktober', color: c.old }, { label: 'Neu', color: c.neu }]);
  });

  // ---------- Vielfalt: weight classes ----------
  const WCLASSES = ['<1', '1-2', '2-3', '3-5', '5-8', '8+'];
  dash.draw('weights', (el) => {
    const rows = section('weight_class');
    if (!rows) { placeholder(el, statusOf('facts')); return; }
    el.replaceChildren();
    const c = COL();
    const get = (cls, sub) => rows.find((r) => r.item === cls && r.sub === sub);
    const data = WCLASSES.map((w) => ({ w, ref: num(get(w, 'seed_weight_share')?.new), old: num(get(w, 'synthetic_share')?.old), neu: num(get(w, 'synthetic_share')?.new) }));
    const width = el.clientWidth || 360, height = 260, m = { t: 28, r: 10, b: 34, l: 40 };
    const svg = svgFor(el, width, height, 'Anteil der Kopien je MiD-Gewichtsklasse gegen den Anteil am MiD-Gewicht');
    const x0 = d3.scaleBand().domain(WCLASSES).range([m.l, width - m.r]).padding(0.22);
    const x1 = d3.scaleBand().domain(['ref', 'old', 'neu']).range([0, x0.bandwidth()]).padding(0.1);
    const y = d3.scaleLinear().domain([0, d3.max(data, (d) => Math.max(d.ref, d.old, d.neu)) * 1.1]).range([height - m.b, m.t]);
    fitBandLabels(svg, svg.append('g').attr('transform', `translate(0,${height - m.b})`).call(d3.axisBottom(x0).tickSizeOuter(0)), x0.bandwidth());
    svg.append('g').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickFormat((v) => F.pct0.format(v)).tickSize(-(width - m.l - m.r))).call((g) => g.select('.domain').remove());
    const tip = tipFor(el);
    data.forEach((d) => {
      [['ref', d.ref], ['old', d.old], ['neu', d.neu]].forEach(([k, v]) => {
        const r = svg.append('rect').attr('x', x0(d.w) + x1(k)).attr('width', x1.bandwidth()).attr('y', y(v)).attr('height', Math.max(0, y(0) - y(v)));
        if (k === 'ref') r.style('fill', 'none').style('stroke', c.ref).attr('stroke-width', 1.5);
        else r.style('fill', k === 'old' ? c.old : c.neu);
        r.on('mousemove', (ev) => tip.show([`Gewicht ${d.w}`, `Anteil am MiD-Gewicht: ${F.pct1.format(d.ref)}`, `Oktober: ${F.pct1.format(d.old)} der Kopien`, `Neu: ${F.pct1.format(d.neu)} der Kopien`], ev)).on('mouseleave', () => tip.hide());
      });
    });
    legendRow(svg, m.l, 10, [{ label: 'MiD-Gewicht', color: c.ref, kind: 'ref' }, { label: 'Oktober', color: c.old }, { label: 'Neu', color: c.neu }]);
  });

  // ---------- Vielfalt: attribute representativeness ----------
  let attrChoice = 'RegioStaR7';
  dash.draw('attrselect', (sel) => {
    const rows = section('attribute');
    const names = rows ? [...new Set(rows.map((r) => r.item))] : [];
    sel.replaceChildren(...names.map((n) => new Option(n, n)));
    if (names.length && !names.includes(attrChoice)) attrChoice = names[0];
    sel.value = attrChoice;
    sel.onchange = () => { attrChoice = sel.value; dash.draw('attributes'); };
  });
  dash.draw('attributes', (el) => {
    const rows = section('attribute');
    if (!rows) { placeholder(el, statusOf('facts')); return; }
    el.replaceChildren();
    const c = COL();
    const data = rows.filter((r) => r.item === attrChoice).map((r) => ({ v: r.sub, ref: num(r.reference), old: num(r.old) / num(r.reference), neu: num(r.new) / num(r.reference), so: num(r.old), sn: num(r.new) }));
    const width = el.clientWidth || 360, rowH = 26, m = { t: 26, r: 18, b: 30, l: 0 };
    const svg = svgFor(el, width, m.t + m.b + rowH * data.length, `Vertretung nach ${attrChoice}`);
    m.l = Math.min(140, textWidth(svg, data.map((d) => d.v)) + 14);
    const height = m.t + m.b + rowH * data.length;
    const vals = data.flatMap((d) => [d.old, d.neu]).filter((v) => Number.isFinite(v) && v > 0);
    const lo = Math.min(0.5, d3.min(vals) || 0.5), hi = Math.max(2, d3.max(vals) || 2);
    const x = d3.scaleLog().domain([lo * 0.9, hi * 1.1]).range([m.l, width - m.r]);
    const y = d3.scaleBand().domain(data.map((d) => d.v)).range([m.t, height - m.b]).padding(0.3);
    const ticks = [0.1, 0.2, 0.5, 1, 2, 5, 10].filter((v) => v >= x.domain()[0] && v <= x.domain()[1]);
    svg.append('g').attr('transform', `translate(0,${height - m.b})`).call(d3.axisBottom(x).tickValues(ticks).tickFormat((v) => F.dec1.format(v) + '×').tickSizeOuter(0));
    svg.append('g').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).tickSize(0)).call((g) => g.select('.domain').remove());
    svg.append('line').attr('x1', x(1)).attr('x2', x(1)).attr('y1', m.t - 6).attr('y2', height - m.b).style('stroke', c.ref).attr('stroke-dasharray', '3 3');
    const tip = tipFor(el);
    data.forEach((d) => {
      const cy = y(d.v) + y.bandwidth() / 2;
      if (Number.isFinite(d.old) && Number.isFinite(d.neu) && d.old > 0 && d.neu > 0) svg.append('line').attr('x1', x(d.old)).attr('x2', x(d.neu)).attr('y1', cy).attr('y2', cy).style('stroke', c.grid).attr('stroke-width', 2);
      [['old', c.old, d.old, d.so], ['neu', c.neu, d.neu, d.sn]].forEach(([k, col, v, s]) => {
        if (!Number.isFinite(v) || v <= 0) return;
        svg.append('circle').attr('cx', x(v)).attr('cy', cy).attr('r', 5).style('fill', col)
          .on('mousemove', (ev) => tip.show([`${attrChoice} = ${d.v} · ${k === 'old' ? 'Oktober' : 'Neu'}`, `Anteil der Kopien: ${F.pct1.format(s)}`, `Anteil am MiD-Gewicht: ${F.pct1.format(d.ref)}`, `Faktor: ${F.dec2.format(v)}`], ev))
          .on('mouseleave', () => tip.hide());
      });
    });
    legendRow(svg, m.l, 10, [{ label: 'Oktober', color: c.old }, { label: 'Neu', color: c.neu }]);
  });

  // ---------- Vielfalt: per Kreis ----------
  function kreisTable(sub) {
    return (tbody) => {
      const rows = section('kreis_reuse');
      tbody.replaceChildren();
      if (!rows) return;
      rows.filter((r) => r.sub === sub).sort((a, b) => a.item.localeCompare(b.item)).forEach((r) => {
        const tr = document.createElement('tr');
        tr.setAttribute('data-key', encodeURIComponent(r.id));
        [kreisName(r.item), fmt(r.old, 'int'), fmt(r.new, 'int')].forEach((t) => { const td = document.createElement('td'); td.textContent = t; tr.appendChild(td); });
        tbody.appendChild(tr);
      });
    };
  }
  dash.draw('kreisreuse', kreisTable('n_distinct_used'));
  dash.draw('kreiskish', kreisTable('kish_n_eff'));

  // ---------- Treffer: group dumbbell ----------
  const GEO_ORDER = { ZENSUS100m: 0, ZENSUS1km: 1, KREIS: 2 };
  const GEO_LABEL = { ZENSUS100m: '100 m', ZENSUS1km: '1 km', KREIS: 'Kreis' };
  dash.draw('groupfit', (el) => {
    const rows = section('group_fit');
    if (!rows) { placeholder(el, statusOf('facts')); return; }
    el.replaceChildren();
    const c = COL();
    const data = rows.map((r) => ({ id: r.id, label: r.label, geo: r.sub, old: num(r.old), neu: num(r.new), note: r.note }))
      .filter((d) => d.label.indexOf('Haushalte insgesamt') !== 0)
      .sort((a, b) => GEO_ORDER[a.geo] - GEO_ORDER[b.geo] || (b.neu || b.old) - (a.neu || a.old));
    const width = el.clientWidth || 600, rowH = 22, m = { t: 30, r: 54, b: 30, l: 0 };
    const height = m.t + m.b + rowH * data.length;
    const svg = svgFor(el, width, height, 'Abweichung je Vorgabengruppe und Ebene, Oktober und neu');
    m.l = Math.min(width * 0.45, textWidth(svg, data.map((d) => d.label.replace(/ · .*$/, '') + ' (nur Oktober)')) + 12);
    const vals = data.flatMap((d) => [d.old, d.neu]).filter((v) => Number.isFinite(v) && v > 0);
    const x = d3.scaleLog().domain([d3.min(vals) * 0.8, d3.max(vals) * 1.25]).range([m.l, width - m.r]);
    const y = d3.scaleBand().domain(data.map((d) => d.id)).range([m.t, height - m.b]).padding(0.35);
    const gTicks = (width - m.l - m.r < 320 ? [0.001, 0.01, 0.1] : [0.0005, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5]).filter((v) => v >= x.domain()[0] && v <= x.domain()[1]);
    svg.append('g').attr('transform', `translate(0,${height - m.b})`).call(d3.axisBottom(x).tickValues(gTicks).tickFormat((v) => String(v).replace('.', ',')).tickSize(-(height - m.t - m.b))).call((g) => g.select('.domain').remove());
    let lastGeo = null;
    const tip = tipFor(el);
    data.forEach((d) => {
      const cy = y(d.id) + y.bandwidth() / 2;
      if (d.geo !== lastGeo) {
        svg.append('text').attr('x', 0).attr('y', y(d.id) - 6).attr('class', 'pr-strong').style('font-weight', 600).text(GEO_LABEL[d.geo]);
        lastGeo = d.geo;
      }
      const only = /only in the new run/.test(d.note) ? ' (nur neu)' : /only in the October run/.test(d.note) ? ' (nur Oktober)' : '';
      svg.append('text').attr('x', m.l - 8).attr('y', cy).attr('dy', '0.35em').attr('text-anchor', 'end').text(d.label.replace(/ · .*$/, '') + only);
      if (Number.isFinite(d.old) && Number.isFinite(d.neu)) svg.append('line').attr('x1', x(d.old)).attr('x2', x(d.neu)).attr('y1', cy).attr('y2', cy).style('stroke', d.neu <= d.old ? 'var(--color-ok)' : 'var(--color-warn)').attr('stroke-width', 2);
      [['old', c.old, d.old], ['neu', c.neu, d.neu]].forEach(([k, col, v]) => {
        if (!Number.isFinite(v) || v <= 0) return;
        svg.append('circle').attr('cx', x(v)).attr('cy', cy).attr('r', 5).style('fill', col)
          .on('mousemove', (ev) => tip.show([d.label, `Oktober: ${fmt(d.old, 'dec4')}`, `Neu: ${fmt(d.neu, 'dec4')}`, d.note], ev)).on('mouseleave', () => tip.hide());
      });
      if (Number.isFinite(d.neu)) svg.append('text').attr('x', width - m.r + 6).attr('y', cy).attr('dy', '0.35em').attr('class', 'pr-strong').text(fmt(d.neu, 'dec3'));
    });
    legendRow(svg, m.l, 10, [{ label: 'Oktober', color: c.old }, { label: 'Neu', color: c.neu }]);
  });

  // ---------- Treffer: cell size small multiples ----------
  const SIZE_BINS = ['1-5', '6-10', '11-20', '21-30', '31-50', '51-100', '101+'];
  const SIZE_GROUPS = [['age_sex', 'Alter × Geschlecht'], ['household_size', 'Haushaltsgröße'], ['employed_grid', 'Erwerbstätige'], ['building_type', 'Gebäudetyp'], ['tenure', 'Eigentum / Miete']];
  dash.draw('cellsize', (el) => {
    const rows = section('cellsize');
    const cells = section('cellsize_cells');
    if (!rows) { placeholder(el, statusOf('facts')); return; }
    el.replaceChildren();
    const c = COL();
    const width = el.clientWidth || 600;
    const cols = width > 900 ? 5 : width > 560 ? 3 : width > 380 ? 2 : 1;
    const pw = Math.floor((width - (cols - 1) * 14) / cols), ph = 170;
    const nRows = Math.ceil(SIZE_GROUPS.length / cols);
    const height = nRows * (ph + 20) + 40;
    const svg = svgFor(el, width, height, 'Abweichung in 100-m-Zellen nach Zahl der Haushalte je Zelle, je Vorgabengruppe');
    const maxV = d3.max(rows.filter((r) => SIZE_BINS.includes(r.item) && SIZE_GROUPS.some((g) => g[0] === r.sub)), (r) => Math.max(num(r.old) || 0, num(r.new) || 0));
    const tip = tipFor(el);
    SIZE_GROUPS.forEach(([g, label], i) => {
      const gx = (i % cols) * (pw + 14), gy = Math.floor(i / cols) * (ph + 20) + 24;
      const p = svg.append('g').attr('transform', `translate(${gx},${gy})`);
      const m = { t: 18, r: 6, b: 48, l: 34 };
      const x = d3.scalePoint().domain(SIZE_BINS).range([m.l, pw - m.r]).padding(0.6);
      const y = d3.scaleLinear().domain([0, maxV * 1.05]).range([ph - m.b, m.t]);
      p.append('text').attr('x', m.l).attr('y', 8).attr('class', 'pr-strong').style('font-weight', 600).text(label);
      p.append('g').attr('transform', `translate(0,${ph - m.b})`).call(d3.axisBottom(x).tickSizeOuter(0)).selectAll('text').attr('y', 0).attr('x', -9).attr('dy', '0.35em').attr('transform', 'rotate(-90)').attr('text-anchor', 'end');
      p.append('g').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(4).tickFormat((v) => F.dec1.format(v)).tickSize(-(pw - m.l - m.r))).call((gg) => gg.select('.domain').remove());
      const pts = SIZE_BINS.map((b) => { const r = rows.find((rr) => rr.item === b && rr.sub === g); return { b, old: num(r?.old), neu: num(r?.new), n: num(cells?.find((cc) => cc.item === b)?.new) }; });
      [['old', c.old, '4 3'], ['neu', c.neu, null]].forEach(([k, col, dash_]) => {
        const valid = pts.filter((d) => Number.isFinite(d[k]));
        p.append('path').datum(valid).attr('d', d3.line().x((d) => x(d.b)).y((d) => y(d[k]))).style('fill', 'none').style('stroke', col).attr('stroke-width', 2.2).attr('stroke-dasharray', dash_);
        valid.forEach((d) => p.append('circle').attr('cx', x(d.b)).attr('cy', y(d[k])).attr('r', 3.2).style('fill', col)
          .on('mousemove', (ev) => tip.show([`${label}, Zellen mit ${d.b} Haushalten`, `Oktober: ${fmt(d.old, 'dec3')}`, `Neu: ${fmt(d.neu, 'dec3')}`, `Zellen: ${fmt(d.n, 'int')}`], ev)).on('mouseleave', () => tip.hide()));
      });
    });
    legendRow(svg, 0, 8, [{ label: 'Oktober', color: c.old, kind: 'line', dash: '4 3' }, { label: 'Neu', color: c.neu, kind: 'line' }]);
    svg.append('text').attr('x', width).attr('y', height - 4).attr('text-anchor', 'end').text('Haushalte je 100-m-Zelle laut Zensus');
  });

  // ---------- Karte ----------
  let mapMetric = 'household_size_100m_wape', mapRun = 'new';
  document.querySelectorAll('#pr-map-metric').forEach((sel) => sel.addEventListener('change', () => { mapMetric = sel.value; dash.draw('map'); }));
  document.querySelectorAll('[data-maprun]').forEach((btn) => btn.addEventListener('click', () => {
    mapRun = btn.getAttribute('data-maprun');
    document.querySelectorAll('[data-maprun]').forEach((b) => b.setAttribute('aria-pressed', String(b === btn)));
    dash.draw('map');
  }));
  const METRIC_LABEL = { household_size_100m_wape: 'Abweichung Haushaltsgröße', age_sex_100m_wape: 'Abweichung Alter × Geschlecht', employed_grid_100m_wape: 'Abweichung Erwerbstätige', building_type_100m_wape: 'Abweichung Gebäudetyp', tenure_100m_wape: 'Abweichung Eigentum/Miete', own_cars_1km_wape: 'Abweichung Autos', own_bikes_1km_wape: 'Abweichung Fahrräder', donors: 'Genutzte Vorlagen', kish: 'Kish-Größe', households: 'Haushalte' };
  dash.draw('map', (el) => {
    const d = dash.data('cells');
    if (d.status !== 'ok') { placeholder(el, d.status); return; }
    el.replaceChildren();
    const rows = d.data.map((r) => ({ zone: r.zone, kreis: String(r.kreis).padStart(5, '0'), x: num(r.x_km), y: num(r.y_km),
      o: num(r[mapMetric + '_old']), n: num(r[mapMetric + '_new']), hh: num(r.households_target_new), hhn: num(r.households_new) }));
    rows.forEach((r) => { r.v = mapRun === 'old' ? r.o : mapRun === 'new' ? r.n : r.n - r.o; });
    const isWape = /_wape$/.test(mapMetric);
    const xs = d3.extent(rows, (r) => r.x), ys = d3.extent(rows, (r) => r.y);
    const spanX = xs[1] - xs[0] + 1, spanY = ys[1] - ys[0] + 1;
    const width = Math.max(280, el.clientWidth || 600);
    const cell = Math.max(2, Math.min(9, Math.floor(width / spanX), Math.floor(680 / spanY)));
    const W = Math.ceil(spanX * cell), H = Math.ceil(spanY * cell);
    const wrap = document.createElement('div');
    wrap.style.position = 'relative';
    wrap.style.width = W + 'px';
    wrap.style.maxWidth = '100%';
    el.appendChild(wrap);
    const canvas = document.createElement('canvas');
    const dpr = window.devicePixelRatio || 1;
    canvas.width = W * dpr; canvas.height = H * dpr;
    canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', `Karte der 1-km-Zellen: ${METRIC_LABEL[mapMetric]}, ${mapRun === 'old' ? 'Oktober' : mapRun === 'new' ? 'neu' : 'Änderung'}`);
    wrap.appendChild(canvas);
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    const valid = rows.filter((r) => Number.isFinite(r.v));
    let color, domain;
    if (mapRun === 'diff') {
      const lim = d3.quantile(valid.map((r) => Math.abs(r.v)).sort(d3.ascending), 0.95) || 1;
      domain = [-lim, lim];
      const s = d3.scaleDiverging(isWape ? d3.interpolateRdBu : (t) => d3.interpolateRdBu(1 - t)).domain([lim, 0, -lim]);
      color = (v) => s(Math.max(-lim, Math.min(lim, v)));
    } else {
      const both = rows.flatMap((r) => [r.o, r.n]).filter(Number.isFinite).sort(d3.ascending);
      const hi = d3.quantile(both, 0.97) || 1;
      domain = [0, hi];
      const s = d3.scaleSequential(isWape ? d3.interpolateYlOrRd : d3.interpolateViridis).domain([0, hi]);
      color = (v) => s(Math.min(hi, v));
    }
    const px = (r) => (r.x - xs[0]) * cell, py = (r) => (ys[1] - r.y) * cell;
    const missing = resolveVar(el, 'var(--color-border-line)');
    rows.forEach((r) => { ctx.fillStyle = Number.isFinite(r.v) ? color(r.v) : missing; ctx.fillRect(px(r), py(r), cell - (cell > 4 ? 0.5 : 0), cell - (cell > 4 ? 0.5 : 0)); });
    // Kreis labels at the household-weighted centre of their cells.
    const fg = resolveVar(el, 'var(--color-fg)'), bg = resolveVar(el, 'var(--color-bg)');
    ctx.font = `600 ${cell > 5 ? 12 : 10}px system-ui, sans-serif`;
    ctx.textAlign = 'center';
    d3.groups(rows, (r) => r.kreis).forEach(([k, rs]) => {
      const w = d3.sum(rs, (r) => r.hh || 0) || rs.length;
      const cx = d3.sum(rs, (r) => (px(r) + cell / 2) * (r.hh || (w === rs.length ? 1 : 0))) / w;
      const cy = d3.sum(rs, (r) => (py(r) + cell / 2) * (r.hh || (w === rs.length ? 1 : 0))) / w;
      ctx.lineWidth = 3; ctx.strokeStyle = bg; ctx.strokeText(kreisName(k), cx, cy);
      ctx.fillStyle = fg; ctx.fillText(kreisName(k), cx, cy);
    });
    const index = new Map(rows.map((r) => [`${r.x}|${r.y}`, r]));
    const tip = tipFor(wrap);
    canvas.addEventListener('mousemove', (ev) => {
      const b = canvas.getBoundingClientRect();
      const sx = (ev.clientX - b.left) * (W / b.width), sy = (ev.clientY - b.top) * (H / b.height);
      const r = index.get(`${xs[0] + Math.floor(sx / cell)}|${ys[1] - Math.floor(sy / cell)}`);
      if (!r) { tip.hide(); return; }
      const kind = isWape ? 'dec3' : 'int';
      tip.show([`${kreisName(r.kreis)} · ${r.zone.replace('CRS3035RES1000m', '')}`, `${METRIC_LABEL[mapMetric]}: Oktober ${fmt(r.o, kind)}, neu ${fmt(r.n, kind)}`, `Haushalte laut Vorgabe: ${fmt(r.hh, 'int')}`], ev);
    });
    canvas.addEventListener('mouseleave', () => tip.hide());
    // Legend.
    const leg = document.createElement('div');
    leg.className = 'pr-legend';
    const lc = document.createElement('canvas');
    lc.width = 160; lc.height = 10;
    const lctx = lc.getContext('2d');
    for (let i = 0; i < 160; i++) { const v = domain[0] + (domain[1] - domain[0]) * (i / 159); lctx.fillStyle = color(v); lctx.fillRect(i, 0, 1, 10); }
    const lo = document.createElement('span'), hi = document.createElement('span'), what = document.createElement('span');
    const kind = isWape ? 'dec2' : 'int';
    lo.textContent = fmt(domain[0], kind); hi.textContent = (mapRun === 'diff' ? '+' : '') + fmt(domain[1], kind) + ' und mehr';
    what.textContent = `${METRIC_LABEL[mapMetric]}${mapRun === 'diff' ? ', neu minus Oktober' : ''} · grau: keine Vorgabe`;
    leg.append(what, lo, lc, hi);
    el.appendChild(leg);
  });

  // ---------- Heatmap der Aktivitäten ----------
  const HEAT_LAYERS = { workplace: 'Arbeitsplätze', work: 'Arbeit', education: 'Ausbildung', shop: 'Einkauf', leisure: 'Freizeit', other: 'Erledigung / Sonstiges', escort: 'Begleitung', nonhome: 'Alle Aktivitäten außer Zuhause', home: 'Zuhause' };
  const HEAT_VIEWS = { old: 'Oktober', new: 'Neu', diff: 'Änderung (neu minus Oktober)', pot: 'Gebäude-Potenzial', vspot: 'Neu gegen Potenzial (Anteile)' };
  let heatLayer = 'workplace', heatView = 'new', heatSmooth = true, heatTimer = null;
  const heatSelect = () => document.querySelector('#pr-heat-layer');
  const setPressed = (attr, value) => document.querySelectorAll(`[${attr}]`).forEach((b) => b.setAttribute('aria-pressed', String(b.getAttribute(attr) === value)));
  document.querySelectorAll('#pr-heat-layer').forEach((sel) => sel.addEventListener('change', () => { heatLayer = sel.value; dash.draw('heat'); }));
  document.querySelectorAll('[data-heatview]').forEach((btn) => btn.addEventListener('click', () => { heatView = btn.getAttribute('data-heatview'); setPressed('data-heatview', heatView); dash.draw('heat'); }));
  document.querySelectorAll('[data-heatsmooth]').forEach((btn) => btn.addEventListener('click', () => { heatSmooth = btn.getAttribute('data-heatsmooth') === '1'; setPressed('data-heatsmooth', btn.getAttribute('data-heatsmooth')); dash.draw('heat'); }));
  document.querySelectorAll('#pr-heat-play').forEach((btn) => btn.addEventListener('click', () => {
    if (heatTimer) { clearInterval(heatTimer); heatTimer = null; btn.setAttribute('aria-pressed', 'false'); return; }
    btn.setAttribute('aria-pressed', 'true');
    const keys = Object.keys(HEAT_LAYERS);
    heatTimer = setInterval(() => {
      heatLayer = keys[(keys.indexOf(heatLayer) + 1) % keys.length];
      const sel = heatSelect(); if (sel) sel.value = heatLayer;
      dash.draw('heat');
    }, 2600);
  }));
  dash.draw('heat', (el) => {
    const d = dash.data('grid');
    if (d.status !== 'ok') { placeholder(el, d.status); return; }
    el.replaceChildren();
    // Crop to the residents' area (cells with home activities) plus 2 km: work and other activities of residents
    // outside the model area are real, but they would shrink the region to a small spot in the middle.
    const homeCells = d.data.filter((r) => (num(r.home_new) || 0) + (num(r.home_old) || 0) > 0);
    const hx = d3.extent(homeCells, (r) => num(r.ix)), hy = d3.extent(homeCells, (r) => num(r.iy));
    const rows = d.data.filter((r) => num(r.ix) >= hx[0] - 4 && num(r.ix) <= hx[1] + 4 && num(r.iy) >= hy[0] - 4 && num(r.iy) <= hy[1] + 4);
    const outside = d.data.length - rows.length;
    const has = (col) => rows.length && Object.prototype.hasOwnProperty.call(rows[0], col);
    const title = document.createElement('div');
    title.className = 'pr-heat-title';
    title.textContent = `${HEAT_LAYERS[heatLayer]} · ${HEAT_VIEWS[heatView]}`;
    el.appendChild(title);
    if ((heatView === 'pot' || heatView === 'vspot') && !has(heatLayer + '_pot')) {
      const p = document.createElement('p'); p.className = 'pr-muted pr-small';
      p.textContent = 'Für diese Ebene gibt es in der Gebäudedatei kein Potenzial. Potenziale gibt es für Arbeitsplätze, Arbeit, Ausbildung, Einkauf, Freizeit und Erledigung.';
      el.appendChild(p); return;
    }
    const col = (run) => `${heatLayer}_${run}`;
    const total = (run) => d3.sum(rows, (r) => num(r[col(run)]) || 0) || 1;
    const tOld = has(col('old')) ? total('old') : 1, tNew = total('new'), tPot = has(col('pot')) ? total('pot') : 1;
    const value = (r) => {
      const o = num(r[col('old')]) || 0, n = num(r[col('new')]) || 0, p = num(r[col('pot')]) || 0;
      switch (heatView) {
        case 'old': return o;
        case 'new': return n;
        case 'pot': return p;
        case 'diff': return n - o;
        default: return 100 * (n / tNew - p / tPot);
      }
    };
    const diverging = heatView === 'diff' || heatView === 'vspot';
    const ixs = d3.extent(rows, (r) => num(r.ix)), iys = d3.extent(rows, (r) => num(r.iy));
    const nx = ixs[1] - ixs[0] + 1, ny = iys[1] - iys[0] + 1;
    const width = Math.max(280, el.clientWidth || 600);
    const scale = Math.min(width / nx, 720 / ny, 6);
    const W = Math.floor(nx * scale), H = Math.floor(ny * scale);
    const vals = rows.map(value);
    let colorOf, legendDomain;
    if (diverging) {
      const lim = d3.quantile(vals.map(Math.abs).filter((v) => v > 0).sort(d3.ascending), 0.98) || 1;
      legendDomain = [-lim, lim];
      const s = d3.scaleDiverging((t) => d3.interpolatePuOr(1 - t)).domain([-lim, 0, lim]);
      colorOf = (v) => { const c = d3.rgb(s(Math.max(-lim, Math.min(lim, v)))); const a = Math.min(1, 0.15 + 0.85 * Math.sqrt(Math.abs(v) / lim)); return [c.r, c.g, c.b, v === 0 ? 0 : a]; };
    } else {
      const pos = vals.filter((v) => v > 0).sort(d3.ascending);
      const hi = d3.quantile(pos, 0.995) || 1, lo = Math.max(d3.quantile(pos, 0.02) || 0.01, hi / 1e4);
      legendDomain = [lo, hi];
      const s = d3.scaleLog().domain([lo, hi]).range([0, 1]).clamp(true);
      colorOf = (v) => { if (!(v > 0)) return [0, 0, 0, 0]; const t = s(v); const c = d3.rgb(d3.interpolateInferno(0.12 + 0.86 * t)); return [c.r, c.g, c.b, Math.min(1, 0.25 + 0.75 * Math.sqrt(t))]; };
    }
    const off = document.createElement('canvas');
    off.width = nx; off.height = ny;
    const octx = off.getContext('2d');
    const img = octx.createImageData(nx, ny);
    const index = new Map();
    rows.forEach((r, i) => {
      const cx = num(r.ix) - ixs[0], cy = iys[1] - num(r.iy);
      index.set(`${cx}|${cy}`, r);
      const [cr, cg, cb, ca] = colorOf(vals[i]);
      const k = 4 * (cy * nx + cx);
      img.data[k] = cr; img.data[k + 1] = cg; img.data[k + 2] = cb; img.data[k + 3] = Math.round(255 * ca);
    });
    octx.putImageData(img, 0, 0);
    const wrap = document.createElement('div');
    wrap.style.position = 'relative'; wrap.style.width = W + 'px'; wrap.style.maxWidth = '100%';
    el.appendChild(wrap);
    const canvas = document.createElement('canvas');
    const dpr = window.devicePixelRatio || 1;
    canvas.width = W * dpr; canvas.height = H * dpr; canvas.style.width = W + 'px'; canvas.style.height = H + 'px';
    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', `Heatmap ${HEAT_LAYERS[heatLayer]}, ${HEAT_VIEWS[heatView]}, 500-m-Gitter`);
    wrap.appendChild(canvas);
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingEnabled = heatSmooth;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(off, 0, 0, W * dpr, H * dpr);
    const tip = tipFor(wrap);
    canvas.addEventListener('mousemove', (ev) => {
      const b = canvas.getBoundingClientRect();
      const cx = Math.floor((ev.clientX - b.left) / b.width * nx), cy = Math.floor((ev.clientY - b.top) / b.height * ny);
      const r = index.get(`${cx}|${cy}`);
      if (!r) { tip.hide(); return; }
      const lines = [`Zelle ${F.dec1.format(num(r.x_km))} / ${F.dec1.format(num(r.y_km))} km (UTM 32)`, `${HEAT_LAYERS[heatLayer]}: Oktober ${fmt(r[col('old')], 'int')}, neu ${fmt(r[col('new')], 'int')}`];
      if (has(col('pot'))) lines.push(`Potenzial: ${fmt(r[col('pot')], 'dec1')} (Anteil ${fmt(num(r[col('pot')]) / tPot, 'pct1')} gegen neu ${fmt(num(r[col('new')]) / tNew, 'pct1')})`);
      tip.show(lines, ev);
    });
    canvas.addEventListener('mouseleave', () => tip.hide());
    const leg = document.createElement('div');
    leg.className = 'pr-legend';
    const lc = document.createElement('canvas');
    lc.width = 160; lc.height = 10;
    const lctx = lc.getContext('2d');
    for (let i = 0; i < 160; i++) {
      const t = i / 159;
      const v = diverging ? legendDomain[0] + (legendDomain[1] - legendDomain[0]) * t : Math.exp(Math.log(legendDomain[0]) + (Math.log(legendDomain[1]) - Math.log(legendDomain[0])) * t);
      const [cr, cg, cb, ca] = colorOf(v);
      lctx.fillStyle = `rgba(${cr},${cg},${cb},${Math.max(ca, 0.15)})`; lctx.fillRect(i, 0, 1, 10);
    }
    const lo = document.createElement('span'), hi = document.createElement('span'), what = document.createElement('span');
    if (heatView === 'vspot') { lo.textContent = `−${F.dec2.format(legendDomain[1])} Pp. (Modell weniger)`; hi.textContent = `+${F.dec2.format(legendDomain[1])} Pp. (Modell mehr)`; what.textContent = 'Anteil neu minus Anteil Potenzial je Zelle'; }
    else if (heatView === 'diff') { lo.textContent = `−${F.int.format(legendDomain[1])} (weniger)`; hi.textContent = `+${F.int.format(legendDomain[1])} (mehr)`; what.textContent = 'neu minus Oktober je Zelle'; }
    else { lo.textContent = fmt(legendDomain[0], legendDomain[0] < 10 ? 'dec1' : 'int'); hi.textContent = fmt(legendDomain[1], 'int') + ' und mehr'; what.textContent = `${heatView === 'pot' ? 'Potenzial' : 'Anzahl'} je 500-m-Zelle, logarithmisch`; }
    const cut = document.createElement('span');
    cut.textContent = outside > 0 ? `· ${F.int.format(outside)} Zellen außerhalb des Wohngebiets nicht gezeigt` : '';
    leg.append(what, lo, lc, hi, cut);
    el.appendChild(leg);
  });

  // ---------- Alle Vorgaben ----------
  let ctlGroup = '', ctlLevel = '', ctlSort = { key: 'wape_new', dir: -1 };
  dash.draw('ctlgroup', (sel) => {
    const d = dash.data('controls');
    const groups = d.status === 'ok' ? [...new Set(d.data.map((r) => r.group_label))].sort((a, b) => a.localeCompare(b, 'de')) : [];
    sel.replaceChildren(new Option('alle Gruppen', ''), ...groups.map((g) => new Option(g, g)));
    sel.value = ctlGroup;
    sel.onchange = () => { ctlGroup = sel.value; dash.draw('ctlrows'); };
  });
  document.querySelectorAll('#pr-ctl-level').forEach((sel) => sel.addEventListener('change', () => { ctlLevel = sel.value; dash.draw('ctlrows'); }));
  document.querySelectorAll('#pr-ctl-table th[data-sort]').forEach((th) => {
    th.tabIndex = 0;
    const go = () => {
      const key = th.getAttribute('data-sort');
      ctlSort = { key, dir: ctlSort.key === key ? -ctlSort.dir : (['label', 'group_label', 'level'].includes(key) ? 1 : -1) };
      document.querySelectorAll('#pr-ctl-table th[data-sort]').forEach((h) => h.removeAttribute('aria-sort'));
      th.setAttribute('aria-sort', ctlSort.dir > 0 ? 'ascending' : 'descending');
      dash.draw('ctlrows');
    };
    th.addEventListener('click', go);
    th.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
  });
  dash.draw('ctlrows', (tbody) => {
    const d = dash.data('controls');
    tbody.replaceChildren();
    if (d.status !== 'ok') return;
    const text = ['label', 'group_label', 'level'].includes(ctlSort.key);
    const rows = d.data.filter((r) => (!ctlGroup || r.group_label === ctlGroup) && (!ctlLevel || r.geography === ctlLevel))
      .slice().sort((a, b) => {
        if (text) return ctlSort.dir * String(a[ctlSort.key]).localeCompare(String(b[ctlSort.key]), 'de');
        const va = num(a[ctlSort.key]), vb = num(b[ctlSort.key]);
        if (!Number.isFinite(va)) return 1; if (!Number.isFinite(vb)) return -1;
        return ctlSort.dir * (va - vb);
      });
    rows.forEach((r) => {
      const tr = document.createElement('tr');
      tr.setAttribute('data-key', encodeURIComponent(r.control) + ',' + encodeURIComponent(r.geography));
      const cells = [
        [r.label + (r.presence === 'only_new' ? ' (neu)' : r.presence === 'only_old' ? ' (nur Oktober)' : ''), ''],
        [r.group_label, ''], [r.level, ''],
        [fmt(r.target_sum_new, 'int'), ''], [fmt(r.synthetic_sum_new, 'int'), ''],
        [fmt(r.relative_error_new, 'signpct1'), ''],
        [fmt(r.wape_old, 'dec3'), ''], [fmt(r.wape_new, 'dec3'), ''],
        [Number.isFinite(num(r.wape_change)) ? (num(r.wape_change) > 0 ? '+' : '') + fmt(r.wape_change, 'dec3') : '–', num(r.wape_change) < -0.0005 ? 'pr-good' : num(r.wape_change) > 0.0005 ? 'pr-bad' : ''],
      ];
      cells.forEach(([t, cls], i) => { const td = document.createElement('td'); td.textContent = t; if (cls) td.className = cls; if (i < 3) td.classList.add('pr-left'); tr.appendChild(td); });
      tbody.appendChild(tr);
    });
  });

  // ---------- simple fact tables ----------
  function factTable(sectionName, columns, filter, order) {
    return (tbody) => {
      const rows = section(sectionName);
      tbody.replaceChildren();
      if (!rows) return;
      let list = filter ? rows.filter(filter) : rows.slice();
      if (order) list.sort(order);
      list.forEach((r) => {
        const tr = document.createElement('tr');
        tr.setAttribute('data-key', encodeURIComponent(r.id));
        columns.forEach(([field, kind, left]) => {
          const td = document.createElement('td');
          td.textContent = typeof kind === 'function' ? kind(r) : kind ? fmt(r[field], kind) : r[field];
          if (left) td.className = 'pr-left';
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    };
  }
  const rareKind = (r) => (r.sub === 'relative_error' ? 'signpct1' : r.sub === 'wape' ? 'dec3' : 'int');
  const RARE_ORDER = { target: 0, synthetic: 1, relative_error: 2, wape: 3 };
  dash.draw('rare', factTable('rare', [['item', null, true], ['label', null, true], ['old', (r) => fmt(r.old, rareKind(r))], ['new', (r) => fmt(r.new, rareKind(r))]], null,
    (a, b) => a.item.localeCompare(b.item, 'de') || RARE_ORDER[a.sub] - RARE_ORDER[b.sub]));
  const planKind = (r) => (/share|rate/.test(r.item) ? 'pct1' : 'dec2');
  dash.draw('plan', factTable('plan', [['label', null, true], ['reference', (r) => fmt(r.reference, planKind(r))], ['old', (r) => fmt(r.old, planKind(r))], ['new', (r) => fmt(r.new, planKind(r))]]));
  const SEG = { all: 'alle', employed: 'Erwerbstätige', adult_18_64_not_employed: '18–64, nicht erwerbstätig', child_0_5: 'Kinder 0–5', school_age_6_17_not_employed: 'Schulalter 6–17', senior_65plus_not_employed: '65+, nicht erwerbstätig' };
  dash.draw('departure', factTable('departure_emd', [['item', (r) => SEG[r.item] || r.item, true], ['sub', (r) => (r.sub === 'first' ? 'erster' : r.sub === 'later' ? 'spätere' : r.sub), true], ['old', 'dec4'], ['new', 'dec4']],
    (r) => SEG[r.item], (a, b) => Object.keys(SEG).indexOf(a.item) - Object.keys(SEG).indexOf(b.item) || a.sub.localeCompare(b.sub)));
  const runKind = (r) => (r.item === 'popsim_wall_minutes' ? 'min' : r.unit === 'Anteil' ? 'pct1' : r.unit === 'Prozesse' ? 'int' : 'dec2');
  dash.draw('runfacts', factTable('run', [['label', null, true], ['old', (r) => fmt(r.old, runKind(r))], ['new', (r) => fmt(r.new, runKind(r))], ['unit', (r) => (r.item === 'popsim_wall_minutes' ? '' : r.unit), true]]));

  // ---------- Plausibilität: dumbbell of mean |delta| ----------
  dash.draw('popval', (el) => {
    const rows = section('popval');
    if (!rows) { placeholder(el, statusOf('facts')); return; }
    el.replaceChildren();
    const c = COL();
    const data = rows.filter((r) => r.sub === 'mean_abs_delta_pp').map((r) => ({ id: r.id, label: r.label, old: num(r.old), neu: num(r.new), note: r.note }))
      .sort((a, b) => a.neu - b.neu);
    const width = el.clientWidth || 600, rowH = 38, m = { t: 30, r: 44, b: 30, l: 0 };
    const height = m.t + m.b + rowH * data.length;
    const svg = svgFor(el, width, height, 'Mittlere Abweichung der Anteile gegen Zensus und MiD, Oktober und neu');
    m.l = Math.min(width * 0.42, textWidth(svg, data.map((d) => d.label)) + 12);
    const x = d3.scaleLinear().domain([0, d3.max(data, (d) => Math.max(d.old, d.neu)) * 1.1]).range([m.l, width - m.r]);
    const y = d3.scaleBand().domain(data.map((d) => d.id)).range([m.t, height - m.b]).padding(0.4);
    svg.append('g').attr('transform', `translate(0,${height - m.b})`).call(d3.axisBottom(x).ticks(5).tickFormat((v) => F.dec1.format(v)).tickSize(-(height - m.t - m.b))).call((g) => g.select('.domain').remove());
    const tip = tipFor(el);
    data.forEach((d) => {
      const cy = y(d.id) + y.bandwidth() / 2;
      svg.append('text').attr('x', m.l - 8).attr('y', cy - 3).attr('text-anchor', 'end').attr('class', 'pr-strong').text(d.label);
      svg.append('text').attr('x', m.l - 8).attr('y', cy + 11).attr('text-anchor', 'end').style('font-size', '10px').text(d.note.split(' · ').pop().replace('fit_check', 'Abgleich').replace('partially_independent', 'teils unabhängig').replace('independent', 'unabhängig'));
      svg.append('line').attr('x1', x(d.old)).attr('x2', x(d.neu)).attr('y1', cy).attr('y2', cy).style('stroke', d.neu <= d.old ? 'var(--color-ok)' : 'var(--color-warn)').attr('stroke-width', 2);
      [['old', c.old, d.old], ['neu', c.neu, d.neu]].forEach(([k, col, v]) => svg.append('circle').attr('cx', x(v)).attr('cy', cy).attr('r', 5).style('fill', col)
        .on('mousemove', (ev) => tip.show([d.label, `Oktober: ${fmt(d.old, 'dec2')} Pp.`, `Neu: ${fmt(d.neu, 'dec2')} Pp.`, d.note.split(' · ')[0]], ev)).on('mouseleave', () => tip.hide()));
      svg.append('text').attr('x', width - m.r + 6).attr('y', cy).attr('dy', '0.35em').attr('class', 'pr-strong').text(fmt(d.neu, 'dec2'));
    });
    legendRow(svg, m.l, 10, [{ label: 'Oktober', color: c.old }, { label: 'Neu', color: c.neu }]);
  });

  // ---------- Plausibilität: distribution detail ----------
  const CONTROL_NAMES = { age_group: 'Altersgruppen', sex: 'Geschlecht', household_size: 'Haushaltsgröße', cars_per_hh: 'Autos je Haushalt', bicycles_per_hh: 'Fahrräder je Haushalt', employment: 'erwerbstätig ja/nein', employment_status: 'Erwerbsstatus', driving_license_type: 'Führerschein', pt_ticket_type: 'ÖV-Ticket', bev_share: 'Bevölkerungsanteil' };
  let plausControl = 'age_group', plausKreis = '03101';
  dash.draw('plauscontrol', (sel) => {
    const d = dash.data('plausibility');
    const names = d.status === 'ok' ? [...new Set(d.data.map((r) => r.control))].filter((n) => n !== 'bev_share') : [];
    sel.replaceChildren(...names.map((n) => new Option(CONTROL_NAMES[n] || n, n)));
    if (names.length && !names.includes(plausControl)) plausControl = names[0];
    sel.value = plausControl;
    sel.onchange = () => { plausControl = sel.value; dash.draw('plauskreis'); dash.draw('plaus'); };
  });
  dash.draw('plauskreis', (sel) => {
    const d = dash.data('plausibility');
    const geos = d.status === 'ok' ? [...new Set(d.data.filter((r) => r.control === plausControl).map((r) => String(r.geo_id)))].sort() : [];
    sel.replaceChildren(...geos.map((g) => new Option(g === 'region' ? 'Region (alle Gemeinden)' : kreisName(g), g)));
    if (geos.length && !geos.includes(plausKreis)) plausKreis = geos[0];
    sel.value = plausKreis;
    sel.onchange = () => { plausKreis = sel.value; dash.draw('plaus'); };
  });
  dash.draw('plaus', (el) => {
    const d = dash.data('plausibility');
    if (d.status !== 'ok') { placeholder(el, d.status); return; }
    el.replaceChildren();
    const c = COL();
    const data = d.data.filter((r) => r.control === plausControl && String(r.geo_id).padStart(5, '0') === String(plausKreis).padStart(5, '0'))
      .map((r) => ({ cat: String(r.category), ref: num(r.reference_pct), old: num(r.synthetic_pct_old), neu: num(r.synthetic_pct_new) }));
    if (!data.length) { placeholder(el, 'ok'); return; }
    const width = el.clientWidth || 600, height = 280, m = { t: 30, r: 10, b: 54, l: 40 };
    const svg = svgFor(el, width, height, `Anteile je Kategorie: Referenz, Oktober und neu`);
    const x0 = d3.scaleBand().domain(data.map((d) => d.cat)).range([m.l, width - m.r]).padding(0.2);
    const x1 = d3.scaleBand().domain(['ref', 'old', 'neu']).range([0, x0.bandwidth()]).padding(0.08);
    const y = d3.scaleLinear().domain([0, d3.max(data, (d) => Math.max(d.ref || 0, d.old || 0, d.neu || 0)) * 1.12]).range([height - m.b, m.t]);
    fitBandLabels(svg, svg.append('g').attr('transform', `translate(0,${height - m.b})`).call(d3.axisBottom(x0).tickSizeOuter(0)), x0.bandwidth());
    svg.append('g').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickFormat((v) => F.int.format(v) + ' %').tickSize(-(width - m.l - m.r))).call((g) => g.select('.domain').remove());
    const tip = tipFor(el);
    data.forEach((dd) => [['ref', dd.ref], ['old', dd.old], ['neu', dd.neu]].forEach(([k, v]) => {
      if (!Number.isFinite(v)) return;
      const r = svg.append('rect').attr('x', x0(dd.cat) + x1(k)).attr('width', x1.bandwidth()).attr('y', y(v)).attr('height', Math.max(0, y(0) - y(v)));
      if (k === 'ref') r.style('fill', 'none').style('stroke', c.ref).attr('stroke-width', 1.5); else r.style('fill', k === 'old' ? c.old : c.neu);
      r.on('mousemove', (ev) => tip.show([dd.cat, `Referenz: ${fmt(dd.ref, 'dec1')} %`, `Oktober: ${fmt(dd.old, 'dec1')} %`, `Neu: ${fmt(dd.neu, 'dec1')} %`], ev)).on('mouseleave', () => tip.hide());
    }));
    legendRow(svg, m.l, 10, [{ label: 'Referenz', color: c.ref, kind: 'ref' }, { label: 'Oktober', color: c.old }, { label: 'Neu', color: c.neu }]);
  });

  // ---------- Verhalten: purposes ----------
  const PURPOSE = { arbeit: 'Arbeit', ausbildung: 'Ausbildung', einkauf: 'Einkauf', freizeit: 'Freizeit', begleitung: 'Begleitung' };
  dash.draw('purpose', (el) => {
    const rows = section('purpose');
    if (!rows) { placeholder(el, statusOf('facts')); return; }
    el.replaceChildren();
    const c = COL();
    const data = rows.map((r) => ({ p: PURPOSE[r.item] || r.item, ref: num(r.reference), old: num(r.old), neu: num(r.new) }));
    const width = el.clientWidth || 360, height = 240, m = { t: 30, r: 10, b: 28, l: 40 };
    const svg = svgFor(el, width, height, 'Wegezwecke der Tagespläne gegen MiD W1');
    const x0 = d3.scaleBand().domain(data.map((d) => d.p)).range([m.l, width - m.r]).padding(0.2);
    const x1 = d3.scaleBand().domain(['ref', 'old', 'neu']).range([0, x0.bandwidth()]).padding(0.08);
    const y = d3.scaleLinear().domain([0, d3.max(data, (d) => Math.max(d.ref, d.old, d.neu)) * 1.12]).range([height - m.b, m.t]);
    fitBandLabels(svg, svg.append('g').attr('transform', `translate(0,${height - m.b})`).call(d3.axisBottom(x0).tickSizeOuter(0)), x0.bandwidth());
    svg.append('g').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickFormat((v) => F.pct0.format(v)).tickSize(-(width - m.l - m.r))).call((g) => g.select('.domain').remove());
    const tip = tipFor(el);
    data.forEach((dd) => [['ref', dd.ref], ['old', dd.old], ['neu', dd.neu]].forEach(([k, v]) => {
      const r = svg.append('rect').attr('x', x0(dd.p) + x1(k)).attr('width', x1.bandwidth()).attr('y', y(v)).attr('height', Math.max(0, y(0) - y(v)));
      if (k === 'ref') r.style('fill', 'none').style('stroke', c.ref).attr('stroke-width', 1.5); else r.style('fill', k === 'old' ? c.old : c.neu);
      r.on('mousemove', (ev) => tip.show([dd.p, `MiD W1: ${F.pct1.format(dd.ref)}`, `Oktober: ${F.pct1.format(dd.old)}`, `Neu: ${F.pct1.format(dd.neu)}`], ev)).on('mouseleave', () => tip.hide());
    }));
    legendRow(svg, m.l, 10, [{ label: 'MiD W1', color: c.ref, kind: 'ref' }, { label: 'Oktober', color: c.old }, { label: 'Neu', color: c.neu }]);
  });

  // ---------- Verhalten: dot rows against a reference (participation, work trips) ----------
  function dotRows(el, groups, unitKind, aria) {
    el.replaceChildren();
    const c = COL();
    const width = el.clientWidth || 600, rowH = 22;
    const cols = width > 860 ? groups.length : width > 520 ? 2 : 1;
    const pw = Math.floor((width - (cols - 1) * 16) / cols);
    const maxRows = d3.max(groups, (g) => g.rows.length);
    const ph = maxRows * rowH + 56;
    const height = Math.ceil(groups.length / cols) * (ph + 8) + 24;
    const svg = svgFor(el, width, height, aria);
    const all = groups.flatMap((g) => g.rows.flatMap((r) => [r.ref, r.old, r.neu])).filter(Number.isFinite);
    const tip = tipFor(el);
    const labelW = textWidth(svg, groups.flatMap((g) => g.rows.map((r) => r.label))) + 10;
    groups.forEach((g, i) => {
      const gx = (i % cols) * (pw + 16), gy = Math.floor(i / cols) * (ph + 8) + 24;
      const p = svg.append('g').attr('transform', `translate(${gx},${gy})`);
      const m = { t: 20, r: 10, b: 26, l: labelW };
      const ext = d3.extent(g.rows.flatMap((r) => [r.ref, r.old, r.neu]).filter(Number.isFinite));
      const pad = (ext[1] - ext[0]) * 0.15 || 0.02;
      const x = d3.scaleLinear().domain([Math.max(0, ext[0] - pad), Math.min(1, ext[1] + pad)]).range([m.l, pw - m.r]).nice();
      const y = d3.scaleBand().domain(g.rows.map((r) => r.label)).range([m.t, m.t + g.rows.length * rowH]).padding(0.3);
      p.append('text').attr('x', 0).attr('y', 8).attr('class', 'pr-strong').style('font-weight', 600).text(g.title);
      p.append('g').attr('transform', `translate(0,${m.t + g.rows.length * rowH})`).call(d3.axisBottom(x).ticks(pw - m.l < 200 ? 2 : 4).tickFormat((v) => F.pct0.format(v)).tickSize(-(g.rows.length * rowH))).call((gg) => gg.select('.domain').remove());
      g.rows.forEach((r) => {
        const cy = y(r.label) + y.bandwidth() / 2;
        p.append('text').attr('x', m.l - 6).attr('y', cy).attr('dy', '0.35em').attr('text-anchor', 'end').text(r.label);
        if (Number.isFinite(r.ref)) p.append('rect').attr('x', x(r.ref) - 1.5).attr('y', cy - 8).attr('width', 3).attr('height', 16).style('fill', c.ref);
        [['old', c.old, r.old], ['neu', c.neu, r.neu]].forEach(([k, col, v]) => {
          if (!Number.isFinite(v)) return;
          p.append('circle').attr('cx', x(v)).attr('cy', cy).attr('r', 4.5).style('fill', col)
            .on('mousemove', (ev) => tip.show([`${g.title} · ${r.label}`, `SrV: ${fmt(r.ref, unitKind)}`, `Oktober: ${fmt(r.old, unitKind)}`, `Neu: ${fmt(r.neu, unitKind)}`], ev)).on('mouseleave', () => tip.hide());
        });
      });
    });
    legendRow(svg, 0, 8, [{ label: 'SrV', color: c.ref, kind: 'ref' }, { label: 'Oktober', color: c.old }, { label: 'Neu', color: c.neu }]);
  }
  const PART = { work: 'Arbeit', education: 'Ausbildung', leisure: 'Freizeit', shop: 'Einkauf', escort: 'Begleitung', errand: 'Erledigung', other: 'Sonstiges', mobility: 'Mobil (mindestens ein Weg)' };
  dash.draw('participation', (el) => {
    const rows = section('participation');
    if (!rows) { placeholder(el, statusOf('facts')); return; }
    const purposes = [...new Set(rows.map((r) => r.sub))];
    const groups = purposes.map((p) => ({ title: PART[p] || p, rows: rows.filter((r) => r.sub === p).sort((a, b) => a.item.localeCompare(b.item)).map((r) => ({ label: kreisName(r.item), ref: num(r.reference), old: num(r.old), neu: num(r.new) })) }));
    dotRows(el, groups, 'pct1', 'Teilnahmequoten je Kreis und Zweck gegen SrV');
  });
  dash.draw('worktrip', (el) => {
    const rows = section('work_trip');
    if (!rows) { placeholder(el, statusOf('facts')); return; }
    dotRows(el, [{ title: 'Anteil der Erwerbstätigen mit Arbeitsweg', rows: rows.sort((a, b) => a.item.localeCompare(b.item)).map((r) => ({ label: kreisName(r.item), ref: num(r.reference), old: num(r.old), neu: num(r.new) })) }], 'pct1', 'Erwerbstätige mit Arbeitsweg je Kreis gegen SrV');
  });

  // ---------- Verhalten: commute EMD ----------
  const SCOPE = { inter: 'in andere Gemeinden', intra: 'innerhalb der Gemeinde', all: 'alle' };
  const EMD_GAP = 0.08;
  dash.draw('commute', (el) => {
    const rows = section('commute_emd');
    if (!rows) { placeholder(el, statusOf('facts')); return; }
    el.replaceChildren();
    const c = COL();
    const scopes = ['inter', 'intra', 'all'];
    const width = el.clientWidth || 400, rowH = 20, m = { t: 26, r: 12, b: 28, l: 0 };
    const kreise = [...new Set(rows.map((r) => r.item))].sort((a, b) => (a === 'zgb') - (b === 'zgb') || a.localeCompare(b));
    const blockH = kreise.length * rowH + 30;
    const height = m.t + m.b + scopes.length * blockH;
    const svg = svgFor(el, width, height, 'Abstand der Pendeldistanzen zu SrV je Kreis, Oktober und neu, mit Prüfschwelle 0,08');
    m.l = textWidth(svg, kreise.map(kreisName)) + 12;
    const maxV = d3.max(rows, (r) => Math.max(num(r.old) || 0, num(r.new) || 0, EMD_GAP)) * 1.1;
    const x = d3.scaleLinear().domain([0, maxV]).range([m.l, width - m.r]);
    const tip = tipFor(el);
    scopes.forEach((s, si) => {
      const top = m.t + si * blockH;
      svg.append('text').attr('x', 0).attr('y', top + 10).attr('class', 'pr-strong').style('font-weight', 600).text(`Pendeln ${SCOPE[s]}`);
      const y = d3.scaleBand().domain(kreise).range([top + 18, top + 18 + kreise.length * rowH]).padding(0.3);
      svg.append('line').attr('x1', x(EMD_GAP)).attr('x2', x(EMD_GAP)).attr('y1', top + 16).attr('y2', top + 18 + kreise.length * rowH).style('stroke', c.bad).attr('stroke-dasharray', '4 3');
      kreise.forEach((k) => {
        const r = rows.find((rr) => rr.item === k && rr.sub === s);
        if (!r) return;
        const cy = y(k) + y.bandwidth() / 2;
        svg.append('text').attr('x', m.l - 6).attr('y', cy).attr('dy', '0.35em').attr('text-anchor', 'end').text(kreisName(k));
        const nf_ = num(r.reference);
        if (Number.isFinite(nf_)) svg.append('rect').attr('x', m.l).attr('y', cy - 3).attr('width', Math.max(0, x(nf_) - m.l)).attr('height', 6).style('fill', c.grid);
        [['old', c.old, num(r.old)], ['neu', c.neu, num(r.new)]].forEach(([kk, col, v]) => {
          if (!Number.isFinite(v)) return;
          svg.append('circle').attr('cx', x(v)).attr('cy', cy).attr('r', 4.5).style('fill', col)
            .on('mousemove', (ev) => tip.show([`${kreisName(k)} · ${SCOPE[s]}`, `Oktober: ${fmt(r.old, 'dec3')}`, `Neu: ${fmt(r.new, 'dec3')}`, `Rauschen der SrV-Stichprobe: ${fmt(r.reference, 'dec3')}`, r.note.split(';').slice(1).join(';').trim()], ev)).on('mouseleave', () => tip.hide());
        });
      });
    });
    svg.append('g').attr('transform', `translate(0,${height - m.b})`).call(d3.axisBottom(x).ticks(5).tickFormat((v) => F.dec2.format(v)).tickSizeOuter(0));
    legendRow(svg, m.l, 8, [{ label: 'Oktober', color: c.old }, { label: 'Neu', color: c.neu }, { label: 'Schwelle 0,08', color: c.bad, kind: 'line', dash: '4 3' }]);
  });

  // ---------- Verhalten: commute distance bands ----------
  let commuteKreis = '03153', commuteScope = 'inter';
  document.querySelectorAll('#pr-commute-scope').forEach((sel) => sel.addEventListener('change', () => { commuteScope = sel.value; dash.draw('commutebands'); }));
  dash.draw('commutekreis', (sel) => {
    const rows = section('commute_emd');
    const ks = rows ? [...new Set(rows.map((r) => r.item))].sort((a, b) => (a === 'zgb') - (b === 'zgb') || a.localeCompare(b)) : [];
    sel.replaceChildren(...ks.map((k) => new Option(kreisName(k), k)));
    if (ks.length && !ks.includes(commuteKreis)) commuteKreis = ks[0];
    sel.value = commuteKreis;
    sel.onchange = () => { commuteKreis = sel.value; dash.draw('commutebands'); };
  });
  const BANDS = ['0_5', '5_10', '10_20', '20_30', '30_50', '50_100', '100_plus'];
  dash.draw('commutebands', (el) => {
    const rows = section('commute_band');
    if (!rows) { placeholder(el, statusOf('facts')); return; }
    el.replaceChildren();
    const c = COL();
    const data = BANDS.map((b) => { const r = rows.find((rr) => rr.item === `${commuteKreis}|${commuteScope}` && rr.sub === b); return { b: (r ? r.label : b).replace(' km', ''), ref: num(r?.reference), old: num(r?.old), neu: num(r?.new) }; });
    const width = el.clientWidth || 360, height = 250, m = { t: 30, r: 10, b: 30, l: 40 };
    const x0 = d3.scaleBand().domain(data.map((d) => d.b)).range([m.l, width - m.r]).padding(0.18);
    const svg = svgFor(el, width, height, `Verteilung der Pendeldistanzen, ${kreisName(commuteKreis)}, ${SCOPE[commuteScope]}`);
    const x1 = d3.scaleBand().domain(['ref', 'old', 'neu']).range([0, x0.bandwidth()]).padding(0.08);
    const y = d3.scaleLinear().domain([0, d3.max(data, (d) => Math.max(d.ref || 0, d.old || 0, d.neu || 0)) * 1.12 || 1]).range([height - m.b, m.t]);
    fitBandLabels(svg, svg.append('g').attr('transform', `translate(0,${height - m.b})`).call(d3.axisBottom(x0).tickSizeOuter(0)), x0.bandwidth());
    svg.append('g').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickFormat((v) => F.pct0.format(v)).tickSize(-(width - m.l - m.r))).call((g) => g.select('.domain').remove());
    const tip = tipFor(el);
    data.forEach((dd) => [['ref', dd.ref], ['old', dd.old], ['neu', dd.neu]].forEach(([k, v]) => {
      if (!Number.isFinite(v)) return;
      const r = svg.append('rect').attr('x', x0(dd.b) + x1(k)).attr('width', x1.bandwidth()).attr('y', y(v)).attr('height', Math.max(0, y(0) - y(v)));
      if (k === 'ref') r.style('fill', 'none').style('stroke', c.ref).attr('stroke-width', 1.5); else r.style('fill', k === 'old' ? c.old : c.neu);
      r.on('mousemove', (ev) => tip.show([dd.b, `SrV: ${fmt(dd.ref, 'pct1')}`, `Oktober: ${fmt(dd.old, 'pct1')}`, `Neu: ${fmt(dd.neu, 'pct1')}`], ev)).on('mouseleave', () => tip.hide());
    }));
    legendRow(svg, m.l, 10, [{ label: 'SrV', color: c.ref, kind: 'ref' }, { label: 'Oktober', color: c.old }, { label: 'Neu', color: c.neu }]);
  });

  // ---------- Lauf: household difference per batch ----------
  dash.draw('batchdiff', (el) => {
    const rows = section('batch');
    if (!rows) { placeholder(el, statusOf('facts')); return; }
    el.replaceChildren();
    const c = COL();
    const data = rows.filter((r) => r.sub === 'hh_diff').map((r) => ({ b: r.item, old: num(r.old), neu: num(r.new) })).sort((a, b) => a.b.localeCompare(b.b));
    const width = el.clientWidth || 360, height = 220, m = { t: 26, r: 10, b: 26, l: 40 };
    const svg = svgFor(el, width, height, 'Haushalte minus Vorgabe je Teilgebiet, Oktober und neu');
    const x0 = d3.scaleBand().domain(data.map((d) => d.b)).range([m.l, width - m.r]).padding(0.15);
    const lim = Math.max(5, d3.max(data, (d) => Math.max(Math.abs(d.old), Math.abs(d.neu)))) * 1.1;
    const y = d3.scaleLinear().domain([-lim, lim]).range([height - m.b, m.t]);
    svg.append('g').attr('transform', `translate(${m.l},0)`).call(d3.axisLeft(y).ticks(5).tickFormat((v) => F.int.format(v)).tickSize(-(width - m.l - m.r))).call((g) => g.select('.domain').remove());
    svg.append('line').attr('x1', m.l).attr('x2', width - m.r).attr('y1', y(0)).attr('y2', y(0)).style('stroke', c.axis);
    const tip = tipFor(el);
    const half = x0.bandwidth() / 2;
    data.forEach((d) => [['old', c.old, d.old, 0], ['neu', c.neu, d.neu, half]].forEach(([k, col, v, off]) => {
      if (!Number.isFinite(v) || v === 0) return;
      svg.append('rect').attr('x', x0(d.b) + off).attr('width', Math.max(1, half)).attr('y', Math.min(y(0), y(v))).attr('height', Math.max(1, Math.abs(y(v) - y(0)))).style('fill', col)
        .on('mousemove', (ev) => tip.show([d.b.replace('batch_', 'Teilgebiet '), `Oktober: ${d.old > 0 ? '+' : ''}${fmt(d.old, 'int')}`, `Neu: ${d.neu > 0 ? '+' : ''}${fmt(d.neu, 'int')}`], ev)).on('mouseleave', () => tip.hide());
    }));
    svg.append('text').attr('x', width - m.r).attr('y', height - 6).attr('text-anchor', 'end').text('Teilgebiete 0 bis 59');
    legendRow(svg, m.l, 10, [{ label: 'Oktober', color: c.old }, { label: 'Neu', color: c.neu }]);
  });
})();
