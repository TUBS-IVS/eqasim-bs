"""HTML report builder for the Braunschweig 10 % validation."""
from __future__ import annotations

import datetime as dt
import html
import json
import subprocess
from pathlib import Path

import pandas as pd

from . import metrics, diagnostics
from .config import MID_BASELINE, SAMPLING_RATE, THRESHOLDS, ZGB8
from .style import deviation_class


CSS = """
:root { --synth:#1f4e79; --ref:#c00000; --ok:#2e7d32; --warn:#ed8936; --bad:#c00000;
        --bg:#fbfbfd; --muted:#6c757d; }
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', system-ui, sans-serif; color:#1a1a1a;
       background:var(--bg); margin:0; padding:0; line-height:1.55; }
.container { max-width: 1180px; margin: 0 auto; padding: 0 32px; }
header.cover { background: linear-gradient(135deg, var(--synth), #2c5d8f);
               color:white; padding: 60px 32px 70px; }
header.cover h1 { margin:0; font-size:2.4em; font-weight: 600; }
header.cover .meta { opacity:0.85; margin-top: 12px; font-size:0.95em; }
nav.toc { position:sticky; top:0; z-index:5; background:white;
          border-bottom:1px solid #e5e5e5; padding: 12px 32px; }
nav.toc a { display:inline-block; margin-right:18px; color:var(--synth);
            text-decoration:none; font-weight:500; font-size:0.92em; }
nav.toc a:hover { text-decoration:underline; }
section { padding: 36px 0; border-bottom: 1px solid #ececec; }
section h2 { color:var(--synth); margin-top:0; font-size:1.6em; }
section h3 { color:#333; margin-top:1.6em; font-size:1.15em; }
.kpi-grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
            gap:14px; margin-top:18px; }
.kpi { background:white; border:1px solid #e5e5e5; border-radius:8px; padding:14px 16px;
       box-shadow: 0 1px 3px rgba(0,0,0,0.04); }
.kpi .label { font-size:0.82em; color:var(--muted); text-transform:uppercase;
              letter-spacing: 0.04em; }
.kpi .value { font-size:1.7em; font-weight:600; color:var(--synth); margin-top:4px; }
.kpi .delta { font-size:0.85em; margin-top:6px; }
.badge { display:inline-block; padding:2px 8px; border-radius:10px; font-size:0.78em;
         color:white; }
.badge.ok { background:var(--ok); }
.badge.warn { background:var(--warn); }
.badge.bad { background:var(--bad); }
table { border-collapse: collapse; width:100%; margin-top:12px; font-size:0.92em; }
th { text-align:left; padding:8px; background:#f4f6fa; color:#222;
     border-bottom: 2px solid #d8d8d8; font-weight:600; }
td { padding: 6px 8px; border-bottom:1px solid #eee; }
tr.total td { font-weight:600; background:#f9f9fc; }
td.ok { color:var(--ok); font-weight:600; }
td.warn { color:var(--warn); font-weight:600; }
td.bad { color:var(--bad); font-weight:600; }
.figure { margin: 16px 0; text-align:center; }
.figure img { max-width:100%; border:1px solid #eee; border-radius:6px; }
.figure .caption { margin-top:6px; color:var(--muted); font-size:0.9em; }
.note { background:#f7f9fc; border-left:4px solid var(--synth);
        padding:10px 14px; margin: 14px 0; font-size:0.92em; color:#333; }
footer { background:#1a2940; color:#a8b3c4; padding: 22px 32px; font-size:0.85em; }
@media print {
    nav.toc { display:none; }
    section { break-inside: avoid-page; }
    body { background:white; }
}
"""


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        return out
    except Exception:
        return "unknown"


def _kpi_card(label: str, value: str, delta_pct: float | None,
              threshold_key: str | None = None) -> str:
    delta_html = ""
    if delta_pct is not None:
        cls = deviation_class(delta_pct, THRESHOLDS.get(threshold_key, (5.0, 10.0)))
        delta_html = (f'<div class="delta">'
                      f'<span class="badge {cls}">{delta_pct:+.1f} %</span></div>')
    return (f'<div class="kpi"><div class="label">{html.escape(label)}</div>'
            f'<div class="value">{html.escape(value)}</div>{delta_html}</div>')


def _df_to_html(df: pd.DataFrame, dev_cols: dict[str, str] | None = None,
                fmt: dict[str, str] | None = None) -> str:
    """Render DataFrame with traffic-light cells.

    dev_cols: mapping column name -> threshold key for colouring.
    fmt: mapping column name -> format spec (e.g. ',.1f').
    """
    fmt = fmt or {}
    dev_cols = dev_cols or {}
    cols = list(df.columns)
    out = ["<table><thead><tr>"] + [f"<th>{html.escape(str(c))}</th>" for c in cols] + ["</tr></thead><tbody>"]
    for _, row in df.iterrows():
        is_total = str(row.iloc[0]).upper() == "TOTAL"
        out.append(f'<tr class="{"total" if is_total else ""}">')
        for c in cols:
            v = row[c]
            if isinstance(v, float):
                fmtspec = fmt.get(c, ",.2f")
                cell = f"{v:{fmtspec}}"
            else:
                cell = html.escape(str(v))
            cls = ""
            if c in dev_cols and isinstance(v, (int, float)) and pd.notna(v):
                cls = deviation_class(float(v), THRESHOLDS.get(dev_cols[c], (5.0, 10.0)))
            out.append(f'<td class="{cls}">{cell}</td>')
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def build_report(plots: dict[str, str], out_dir: Path) -> Path:
    """Assemble the full HTML and write it to out_dir/report.html."""
    pop = metrics.population_per_kreis()
    pop_total = pop[pop["ars5"] == "TOTAL"].iloc[0]
    summary = metrics.trip_summary()
    mode_share = metrics.mode_share_overall()
    purpose = metrics.purpose_mix()
    duration = metrics.trip_duration_distribution()
    distance = metrics.trip_distance_distribution()
    od = metrics.commute_od_kreis().head(20)
    hh = metrics.household_size_per_kreis()
    employment = metrics.employment_summary()
    commute = metrics.commute_distance_summary()

    now = dt.datetime.now().strftime("%d. %B %Y, %H:%M")
    git = _git_sha()

    # ----- KPI cards
    kpis = []
    kpis.append(_kpi_card("Population (×10)",
                          f"{int(pop_total['synth_expanded']):,}".replace(",", ","),
                          float(pop_total["deviation_pct"]),
                          "population_ratio_pct"))
    kpis.append(_kpi_card("Trips per person",
                          f"{summary['trips_per_person']:.2f}",
                          (summary["trips_per_person"] / MID_BASELINE["trips_per_person"] - 1) * 100,
                          "trips_per_person_pct"))
    kpis.append(_kpi_card("Mean trip distance",
                          f"{summary['mean_distance_km']:.1f} km",
                          summary["mean_distance_km"] - MID_BASELINE["mean_trip_distance_km"],
                          "trip_distance_km"))
    kpis.append(_kpi_card("Mean travel time",
                          f"{summary['mean_duration_min']:.1f} min",
                          summary["mean_duration_min"] - MID_BASELINE["mean_trip_duration_min"],
                          "trip_duration_min"))
    kpis.append(_kpi_card("Daily distance per person",
                          f"{summary['daily_distance_km']:.1f} km",
                          summary["daily_distance_km"] - MID_BASELINE["daily_distance_km"],
                          "trip_distance_km"))

    # Mode-share KPIs
    for _, r in mode_share.iterrows():
        kpis.append(_kpi_card(f"Mode share {r['mode'].upper()}",
                              f"{r['synth_share']*100:.1f} %",
                              r["deviation_pp"],
                              "mode_share_pp"))

    # ----- Sections
    def fig(name: str, caption: str) -> str:
        path = plots.get(name)
        if not path:
            return ""
        return (f'<div class="figure"><img src="{path}" alt="{html.escape(caption)}"/>'
                f'<div class="caption">{html.escape(caption)}</div></div>')

    pop_disp = pop.copy()
    pop_disp["zensus_2022"] = pop_disp["zensus_2022"].astype(int)
    pop_disp["synth_sample"] = pop_disp["synth_sample"].astype(int)
    pop_disp["synth_expanded"] = pop_disp["synth_expanded"].astype(int)

    sections: list[str] = []

    sections.append(f"""
    <section id="executive-summary"><div class="container">
        <h2>1. Executive summary</h2>
        <p>This report compares the synthetic population and modelled travel
        demand for the Greater Braunschweig region (8 districts) against
        Census 2022, the BA commuter atlas, and MiD 2023. Sample size
        {SAMPLING_RATE*100:.0f} % ({int(pop_total['synth_sample']):,} synthetic
        persons).</p>
        <div class="kpi-grid">{"".join(kpis)}</div>
    </div></section>
    """)

    sections.append(f"""
    <section id="population"><div class="container">
        <h2>2. Population — Synthesis vs. Census 2022</h2>
        <p>For each district the expanded synthetic population
        (sample × {1/SAMPLING_RATE:.0f}) is compared against the DESTATIS
        Census 2022.</p>
        {fig("population_per_kreis", "Population per ZGB district (thousand persons)")}
        {fig("choropleth_population", "Spatial deviation of expanded synthesis")}
        {_df_to_html(pop_disp, dev_cols={"deviation_pct": "population_ratio_pct"},
                     fmt={"deviation_pct": "+.2f", "zensus_2022": ",.0f",
                          "synth_sample": ",.0f", "synth_expanded": ",.0f"})}
        {fig("age_pyramid", "Age and sex distribution of the synthesis")}
    </div></section>
    """)

    sections.append(f"""
    <section id="households"><div class="container">
        <h2>3. Households &amp; employment</h2>
        {fig("household_size", "Household-size distribution — ZGB-8 mean")}
        <h3>3.1 Household size per district (excerpt)</h3>
        {_df_to_html(hh.head(20), dev_cols={"deviation_pp": "mode_share_pp"},
                     fmt={"synth_share": ".3f", "zensus_share": ".3f", "deviation_pp": "+.2f",
                          "synth_count": ",.0f"})}
        {fig("employment_rate", "Employment rate by age group and district")}
    </div></section>
    """)

    sections.append(f"""
    <section id="commute"><div class="container">
        <h2>4. Commuting behaviour</h2>
        <p>Commute trips are analysed as the crow-fly distance between home
        and primary workplace (eqasim {{commutes.gpkg}}).</p>
        {fig("commute_distance", "Commute distance distribution")}
        {fig("commute_per_kreis", "Commute distance by home district")}
        {fig("commute_heatmap", "Internal ZGB-8 commuter flows")}
        {fig("commute_scatter_ba", "Synthesis vs. BA commuter atlas (log-log)")}
        <h3>4.1 Commute statistics by district</h3>
        {_df_to_html(commute, fmt={"mean_km": ".2f", "median_km": ".2f", "p90_km": ".2f"})}
        <h3>4.2 Top-20 district → district flows</h3>
        {_df_to_html(od, dev_cols={"deviation_pct": "mode_share_pp"},
                     fmt={"synth_flow": ",.0f", "synth_flow_expanded": ",.0f",
                          "ba_flow": ",.0f", "deviation_pct": "+.1f"})}
    </div></section>
    """)

    sections.append(f"""
    <section id="travel"><div class="container">
        <h2>5. Travel-demand indicators</h2>
        <p>All-purpose trips from the MATSim selected plan (modes extracted
        from {{population.xml.gz}}). Reference: MiD 2023 Greater Braunschweig.</p>
        {fig("mode_share_donut", "Modal split — all trips (donut)")}
        {fig("mode_x_distance", "Modal split by distance class")}
        {fig("mode_x_purpose", "Modal split by purpose")}
        <h3>5.1 Modal split — detail</h3>
        {_df_to_html(mode_share, dev_cols={"deviation_pp": "mode_share_pp"},
                     fmt={"synth_share": ".3f", "mid_share": ".3f", "deviation_pp": "+.2f"})}
        {fig("distance_distribution", "Trip distance histogram")}
        {fig("distance_cdf", "Trip distance CDF (log)")}
        {fig("duration_distribution", "Travel time histogram")}
        {fig("departure_profile", "Hourly departure profile (24 h)")}
        {fig("purpose_mix", "Activity-purpose mix")}
        <h3>5.2 Activity-purpose mix</h3>
        {_df_to_html(purpose, dev_cols={"deviation_pp": "purpose_mix_l1"},
                     fmt={"synth_share": ".3f", "mid_share": ".3f", "deviation_pp": "+.2f"})}
    </div></section>
    """)

    notes = """
    <section id="notes"><div class="container">
        <h2>6. Methodological notes</h2>
        <ul>
            <li>Sample size: 10 % of the population (113,973 persons);
                expansion to total population by factor 10.</li>
            <li>BA commuter flows cover only employees subject to social-security
                contributions (SvB); self-employed and civil servants are missing.
                Expected structural offset of about +20 % synthesis vs. BA.</li>
            <li>Modes are extracted from the selected MATSim plan. The main
                mode of each logical trip is chosen by priority
                pt &gt; car &gt; car_passenger &gt; bicycle &gt; walk
                (access/egress walk legs are not counted as separate trips).</li>
            <li>Distances are crow-fly straight lines between home and
                activity locations, not routed network distances.</li>
        </ul>
    </div></section>
    """
    sections.append(notes)

    # ------------------------------------------------------------------
    # Section 7 — Calibration diagnostics (validation harness)
    # ------------------------------------------------------------------
    od_top, od_stats = diagnostics.od_fit_stats(top_n=200)
    hh_summary, _ = diagnostics.hh_size_fit_per_kreis()
    purpose_remap = diagnostics.purpose_mix_remapped()

    # Build regression-guard table from the JSON payload (build it now in-memory).
    json_payload = _build_json_payload(od_stats, hh_summary)
    guard = diagnostics.regression_guard_status(json_payload, od_stats)

    def _guard_row(r):
        cls = "ok" if r["status"] == "ok" else "bad"
        if r["kpi"] == "od_top200_r2":
            val = f"{r['value']:.3f}"
            tol = f"≥ {r['tolerance']:.2f}"
        else:
            val = f"{r['value']:.2f}"
            tol = f"≤ {r['tolerance']:.2f}"
        return (f"<tr><td>{html.escape(r['description'])}</td>"
                f"<td>{val}</td><td>{tol}</td>"
                f"<td class='{cls}'>{r['status'].upper()}</td></tr>")

    guard_rows = "".join(_guard_row(r) for _, r in guard.iterrows())

    sections.append(f"""
    <section id="diagnostics"><div class="container">
        <h2>7. Calibration diagnostics</h2>
        <p>Quantitative fit indicators used to track refactor progress
        (R-A gravity, R-C household size, R-D purpose remap). Targets are
        deliberately conservative; bicycle / walk are documented residuals
        because the mode-choice utility re-estimation (R-E) is deferred.</p>

        <h3>7.1 Regression guard</h3>
        <table><thead><tr><th>KPI</th><th>Value</th><th>Tolerance</th><th>Status</th></tr></thead>
        <tbody>{guard_rows}</tbody></table>

        <h3>7.2 OD fit — synth vs BA Pendleratlas</h3>
        <p>Top-200 Kreis-pairs by BA flow.
        n = {od_stats['n_pairs']}, R² = {od_stats['r2']:.3f},
        RMSE = {od_stats['rmse']:,.0f}, MAPE = {od_stats['mape_pct']:.1f}%,
        Bias = {od_stats['bias_pct']:+.1f}%.</p>
        {fig("od_scatter_top200", "Synth (expanded) vs BA Pendleratlas — top-200 pairs")}
        {fig("od_outbound_top20", "Top-20 outbound commuter flows ZGB → external")}

        <h3>7.3 Household-size fit per district</h3>
        {fig("hh_size_per_kreis", "Per-Kreis HH-size: Synth vs Zensus 2022 with χ² / TVD")}
        {_df_to_html(hh_summary, fmt={"chi2": ",.0f", "tvd_pp": ".2f", "n_synth_hh": ",.0f"})}

        <h3>7.4 Purpose-mix remap (H1 preview)</h3>
        <p>eqasim assigns <code>following_purpose = home</code> on every return-home leg,
        while MiD attributes the trip to the originating activity. Remapping
        <code>home → preceding_purpose</code> brings the synth purpose mix close to MiD
        without touching the synthesis itself — this is a reporting-only fix
        (R-D).</p>
        {fig("purpose_remap", "Activity-purpose mix — raw vs MiD-aligned remap")}
        {_df_to_html(purpose_remap, dev_cols={"deviation_pp": "purpose_mix_l1"},
                     fmt={"synth_share": ".3f", "mid_share": ".3f", "deviation_pp": "+.2f"})}
    </div></section>
    """)

    nav = ('<nav class="toc"><div class="container">'
           '<a href="#executive-summary">Summary</a>'
           '<a href="#population">Population</a>'
           '<a href="#households">Households</a>'
           '<a href="#commute">Commuting</a>'
           '<a href="#travel">Travel demand</a>'
           '<a href="#diagnostics">Diagnostics</a>'
           '<a href="#notes">Methodology</a>'
           '</div></nav>')

    head = f"""
    <header class="cover"><div class="container">
        <h1>Braunschweig 10 % Validation Report</h1>
        <div class="meta">
            Sample {SAMPLING_RATE*100:.0f} % &nbsp;·&nbsp;
            {int(pop_total['synth_sample']):,} persons &nbsp;·&nbsp;
            Generated {now} &nbsp;·&nbsp;
            git {git}
        </div>
    </div></header>
    """

    html_doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<title>Braunschweig 10 % Validation Report</title>
<style>{CSS}</style>
</head><body>
{head}
{nav}
{"".join(sections)}
<footer><div class="container">eqasim-bs · Validation report · {now} · git {git}</div></footer>
</body></html>
"""

    out_path = out_dir / "report.html"
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path


def _build_json_payload(od_stats: dict | None = None,
                       hh_summary: pd.DataFrame | None = None) -> dict:
    """Assemble the machine-readable payload (shared by HTML guard table and JSON dump)."""
    summary = metrics.trip_summary()
    pop = metrics.population_per_kreis()
    mode = metrics.mode_share_overall()
    purpose_raw = metrics.purpose_mix_raw()
    purpose = metrics.purpose_mix()
    purpose_remap = diagnostics.purpose_mix_remapped()
    if od_stats is None:
        _, od_stats = diagnostics.od_fit_stats(top_n=200)
    if hh_summary is None:
        hh_summary, _ = diagnostics.hh_size_fit_per_kreis()
    return {
        "generated_at": dt.datetime.now().isoformat(),
        "sampling_rate": SAMPLING_RATE,
        "population": pop.to_dict(orient="records"),
        "trip_summary": summary,
        "mode_share": mode.to_dict(orient="records"),
        "purpose_mix_raw": purpose_raw.to_dict(orient="records"),
        "purpose_mix": purpose.to_dict(orient="records"),
        "purpose_mix_remapped": purpose_remap.to_dict(orient="records"),
        "od_fit": od_stats,
        "hh_size_per_kreis": hh_summary.to_dict(orient="records"),
        "kreise": ZGB8,
        "git": _git_sha(),
    }


def write_json_summary(out_dir: Path) -> Path:
    """Machine-readable KPI dump (incl. calibration diagnostics)."""
    payload = _build_json_payload()
    payload["regression_guard"] = diagnostics.regression_guard_status(
        payload, payload["od_fit"]
    ).to_dict(orient="records")
    # TASK-006: per-Kreis HH bootstrap CIs.
    try:
        from . import bootstrap
        payload["bootstrap_ci"] = bootstrap.run()
    except Exception as e:  # noqa: BLE001
        payload["bootstrap_ci_error"] = str(e)
    path = out_dir / "report.json"
    path.write_text(json.dumps(payload, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    return path
