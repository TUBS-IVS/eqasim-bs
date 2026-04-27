---
goal: Comprehensive validation of the Braunschweig 10 % synthetic population against MiD 2023, BA Pendleratlas, Zensus 2022 and INKAR
version: 1.0
date_created: 2026-04-25
last_updated: 2026-04-25
owner: BS calibration
status: 'Planned'
tags: [feature, analysis, validation, braunschweig]
---

# Introduction

![Status: Planned](https://img.shields.io/badge/status-Planned-blue)

This plan defines an end-to-end validation suite for the freshly produced
10 % Braunschweig synthetic population (`eqasim-data/output_bs_10pct/`).
The suite compares the synthetic agents against every quantitative input
the pipeline consumes, plus the MiD 2023 *Großraum Braunschweig* PDF
(infas sample 7555). All reference data are already on disk; the plan
adds a re-usable Python module, a CLI script and a reproducible HTML/PDF
report that summarises the calibration quality at a glance.

The deliverable is **one command** (`python -m scripts.validate_bs_10pct`)
that produces a single self-contained report covering population
structure, household structure, commute flows, mode choice, distance
distribution, license rates and income.

## 1. Requirements & Constraints

- **REQ-001**: Consume only files already present in `eqasim-data/output_bs_10pct/` and `eqasim-data/data/braunschweig/`. No new downloads.
- **REQ-002**: Compare against four reference sources:
  - MiD 2023 regional tables P9/P12_1/P13/P17_1 (CSV in `eqasim-data/data/braunschweig/mid/`).
  - BA Pendleratlas 2025 outbound + inbound (`statistik_pendler_2026042493412.csv`, `…430.csv`).
  - Zensus 2022 population (`12411-0018_de.csv`) and households (`5000H-2001_de_flat.csv`).
  - INKAR Haushaltseinkommen (`E_Haushaltseinkommen.xls`).
- **REQ-003**: All metrics computed at ZGB-8 Kreis level *and* aggregated for the region.
- **REQ-004**: Every comparison reports both absolute values and the relative deviation (`synth / reference - 1`).
- **REQ-005**: Output a single HTML report (`eqasim-data/output_bs_10pct/validation_report.html`) plus a machine-readable JSON summary (`validation_summary.json`).
- **REQ-006**: All plots saved as PNG + embedded into the HTML.
- **REQ-007**: Validator must finish in < 5 min on a workstation (no MATSim re-run).
- **SEC-001**: No code outside the workspace; respect the conda `eqasim` env (pandas 1.5, geopandas 1.0, matplotlib).
- **CON-001**: Sampling rate is 0.1 — every count must be expanded by ×10 before comparison with population totals.
- **CON-002**: ZGB-8 Kreis scope: `03101, 03102, 03103, 03151, 03153, 03154, 03157, 03158`.
- **CON-003**: External Kreise (outside ZGB-8) appear in commute flows but never as residence.
- **CON-004**: ENTD trip diaries (FR) drive activity chains; *trip-purpose* shares cannot be validated against MiD beyond commute trips.
- **GUD-001**: One pure-function per metric, stateless, returns a `pd.DataFrame`.
- **GUD-002**: Use `analysis/` style (pandas + numpy only) for table maths; matplotlib for plots.
- **GUD-003**: English code comments, German report headings (consistent with prior reports).
- **GUD-004**: NaN-safe — every division guarded with `np.where(denom > 0, …, np.nan)`.
- **PAT-001**: Follow existing pattern in [scripts/analyze_calibration.py](scripts/analyze_calibration.py) for cache loading and Kreis filtering.
- **PAT-002**: Follow existing pattern in [braunschweig/analysis/validation_mid2023.ipynb](braunschweig/analysis/validation_mid2023.ipynb) for MiD comparison maths (modal split, license rate, distance CDF).

## 2. Implementation Steps

### Implementation Phase 1 — Module scaffolding

- GOAL-001: Create the `scripts/validate_bs_10pct/` package with shared loaders, configurable paths, and a CLI entry point.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Create `scripts/validate_bs_10pct/__init__.py` with module docstring describing scope. |  |  |
| TASK-002 | Create `scripts/validate_bs_10pct/config.py` exposing constants `OUTPUT_DIR=Path("eqasim-data/output_bs_10pct")`, `DATA_DIR=Path("eqasim-data/data/braunschweig")`, `PREFIX="braunschweig_10pct_"`, `SAMPLING_RATE=0.1`, `ZGB8` dict (8 Kreise → name), `EPSG=25832`. |  |  |
| TASK-003 | Create `scripts/validate_bs_10pct/io.py` with `load_persons()`, `load_households()`, `load_trips()`, `load_homes_gdf()`, `load_commutes_gdf()`, each returning the synth CSV/GPKG with sane dtypes and a derived `kreis` (AGS-5) column joined from the homes GPKG. |  |  |
| TASK-004 | Create `scripts/validate_bs_10pct/references.py` with `load_mid()`, `load_pendler_ein()`, `load_pendler_aus()`, `load_zensus_population()`, `load_zensus_households()`, `load_inkar()` — each returning a clean `pd.DataFrame` keyed on `ars5`. |  |  |
| TASK-005 | Create `scripts/validate_bs_10pct/__main__.py` orchestrating phase 2/3/4 metric calls and writing the report. CLI flags: `--output-dir`, `--no-plots`, `--prefix`. |  |  |

### Implementation Phase 2 — Population & household structure

- GOAL-002: Validate population totals, age/sex pyramid, household size distribution against Zensus 2022 and INKAR.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-006 | `metrics/population.py::population_by_kreis(persons)` → returns `DataFrame[ars5, kreis_name, synth_persons, synth_expanded, zensus_2022, ratio, deviation_pct]`. Expansion = `len(group) / SAMPLING_RATE`. |  |  |
| TASK-007 | `metrics/population.py::age_sex_pyramid(persons)` → 5-year age bins × sex; cross-tab vs. Zensus `12411-0018` (if available) else only synth distribution + Pearson r per Kreis on age-share. |  |  |
| TASK-008 | `metrics/households.py::household_size_distribution(households, persons)` → DataFrame `[ars5, size_bin (1,2,3,4,5+), synth_share, zensus_share, deviation_pct]` sourced from `5000H-2001_de_flat.csv`. |  |  |
| TASK-009 | `metrics/households.py::income_quintiles(persons, households)` → mean monthly household income per Kreis from synthesis + INKAR reference; report ratio per Kreis. |  |  |
| TASK-010 | `metrics/employment.py::employment_share(persons)` → employed share by Kreis vs. MiD P9 (`erwerbstaetig` row). |  |  |

### Implementation Phase 3 — Commute structure (BA Pendleratlas)

- GOAL-003: Validate Kreis-pair commute flows against BA Pendleratlas 2025 inbound and outbound.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-011 | `metrics/commute.py::synth_kreis_pairs(persons, commutes)` → returns `DataFrame[home_ars5, work_ars5, synth_persons (×10)]` covering all ZGB-8 home Kreise; uses `commutes.gpkg` to map work facilities to Kreis. |  |  |
| TASK-012 | `metrics/commute.py::compare_outbound(synth, ba_aus)` → join on `(home_ars5, work_ars5)`; produce `DataFrame[..., synth, ba, ratio, deviation]` plus per-home-Kreis aggregate. Apply Pendleratlas SvB → all-employees scaling factor (≈1.20, document source). |  |  |
| TASK-013 | `metrics/commute.py::compare_inbound(synth, ba_ein)` → mirror analysis for inbound (work in ZGB-8). |  |  |
| TASK-014 | `metrics/commute.py::flow_correlation(comparison)` → returns Pearson r, RMSE on share, and total-flow ratio per Kreis pair. |  |  |
| TASK-015 | `metrics/commute.py::distance_distribution_vs_mid(persons, mid_p13)` → empirical commute distance from `commutes.gpkg` length / 1000; bin into MiD P13 classes (`<1`, `1-2`, `2-5`, `5-10`, `10-20`, `20-50`, `50-100`, `>=100` km, plus `unbekannt`); per Kreis χ² test against MiD shares. |  |  |
| TASK-016 | `metrics/commute.py::external_workplaces(commutes)` → count and share of outbound trips terminating at the synthetic external workplaces (commune_id starts with `EXT`). |  |  |

### Implementation Phase 4 — Travel demand (MiD 2023)

- GOAL-004: Validate mode share, license rate and trip rates against MiD 2023 regional tables.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-017 | `metrics/mode.py::commute_mode_share(persons, trips, mid_p12)` → restrict to `purpose=='work'` trips; map ENTD modes (`car`, `pt`, `bike`, `walk`, `car_passenger`) onto MiD 4-bucket schema (`zu_fuss`, `fahrrad`, `oeffentlich`, `auto`); compute share per Kreis and deviation from P12_1. |  |  |
| TASK-018 | `metrics/mode.py::license_rate(persons, mid_p17)` → share of `has_driving_license=True` among adults (≥17 y) per Kreis vs. P17_1 `ja`. |  |  |
| TASK-019 | `metrics/mode.py::trip_rate(persons, trips)` → trips per person per day, per Kreis, with confidence interval (Wilson on count). |  |  |
| TASK-020 | `metrics/mode.py::mid_pdf_baseline(pdf_path)` → optional: parse selected tables out of `Ergebnistabellen_MiD2023_…7555_Großraum_Braunschweig.pdf` via `pdfplumber` for the *region-level* MiV/ÖV/Rad/Fuß split, used as overall ZGB benchmark. Fallback to hard-coded baseline `(MiV 0.59, ÖV 0.10, Rad 0.13, Fuß 0.18)` if pdfplumber not installed. |  |  |

### Implementation Phase 4b — Verkehrliche Kenngrößen (all-purpose travel KPIs)

- GOAL-004B: Validate complete travel-demand picture (not only commute) against MiD 2023 region totals — distances, durations, modal split per purpose, trip-purpose mix, daily mileage.

Reference values (MiD 2023 Großraum Braunschweig, Werktag, alle Zwecke; baked into `metrics/travel.py::MID_BASELINE` and overridable via PDF extraction):

| Indicator | MiD ZGB | Source row |
|---|---|---|
| Wege pro Person und Tag | 3.1 | „Wege/Person/Tag, alle Zwecke" |
| Mittlere Wegelänge (km) | 12.6 | „Mittlere Wegelänge je Weg" |
| Mittlere Wegedauer (min) | 22 | „Mittlere Wegedauer je Weg" |
| Tagesstrecke (km/Person) | 39 | Wege × Länge |
| Modal Split MiV (Fahrer+Mitfahrer) | 0.59 | Hauptverkehrsmittel |
| Modal Split ÖV | 0.10 | Hauptverkehrsmittel |
| Modal Split Rad | 0.13 | Hauptverkehrsmittel |
| Modal Split Fuß | 0.18 | Hauptverkehrsmittel |
| Wegezweck-Anteil Arbeit | 0.16 | „Hauptzweck der Wege" |
| Wegezweck Ausbildung | 0.07 | „Hauptzweck der Wege" |
| Wegezweck Einkauf | 0.18 | „Hauptzweck der Wege" |
| Wegezweck Erledigung | 0.12 | „Hauptzweck der Wege" |
| Wegezweck Freizeit | 0.27 | „Hauptzweck der Wege" |
| Wegezweck Begleitung | 0.05 | „Hauptzweck der Wege" |
| Wegezweck Heimweg | 0.15 | „Hauptzweck der Wege" |

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-T01 | `metrics/travel.py::MID_BASELINE` constant dict with the values in the table above (region-level, alle Zwecke, Werktag). All downstream functions consume this dict, so an optional PDF-extracted override (TASK-020) just replaces values in-place. |  |  |
| TASK-T02 | `metrics/travel.py::trip_distance_distribution(trips)` → derive `distance_km` from `trips.csv` Euclidean (`network_distance_km` if available, else `crow_fly_km`); bin into MiD P13 classes; return per-Kreis and region-aggregate share + deviation from MiD distance distribution. Distinguish `purpose ∈ {work, education, leisure, shop, other, home}`. |  |  |
| TASK-T03 | `metrics/travel.py::trip_duration_distribution(trips)` → bin `travel_time_min = (arrival_time - departure_time) / 60` into MiD time classes (`<10`, `10-20`, `20-30`, `30-60`, `60-90`, `>=90` min); compare against MiD duration table (read from PDF or hard-coded `MID_DURATION_BASELINE`). |  |  |
| TASK-T04 | `metrics/travel.py::mode_share_overall(trips, mid_baseline)` → mode share over **all** trips (not just commute) per Kreis and region; deviation against MiD `MIV/ÖV/Rad/Fuß`. Map ENTD `car`+`car_passenger`→MiV. |  |  |
| TASK-T05 | `metrics/travel.py::mode_share_by_purpose(trips)` → 2-D table `purpose × mode → share`; visualised as stacked bar per purpose, optional MiD overlay if PDF parsed. |  |  |
| TASK-T06 | `metrics/travel.py::mode_share_by_distance_band(trips)` → modal split × distance band (matrix); identifies whether short trips are correctly walk/bike-dominated and long trips MiV/PT-dominated. |  |  |
| TASK-T07 | `metrics/travel.py::trip_purpose_mix(trips)` → share of trips by purpose vs. MiD `MID_BASELINE` (purpose anteile). |  |  |
| TASK-T08 | `metrics/travel.py::trips_per_person(trips, persons)` → mean trips/person/day per Kreis with 95 % CI; compare against MiD `3.1`. |  |  |
| TASK-T09 | `metrics/travel.py::daily_distance_per_person(trips, persons)` → mean km/person/day per Kreis; compare against MiD `~39 km`. |  |  |
| TASK-T10 | `metrics/travel.py::activity_chain_lengths(persons, trips)` → distribution of chain length (number of activities per person) and dominant chain types (`H-W-H`, `H-W-S-H`, `H-O-H`, …); descriptive only, no MiD ground truth. |  |  |
| TASK-T11 | `metrics/travel.py::departure_time_profile(trips)` → 24×1 histogram of departure hours; report morning peak (07–09) and evening peak (16–19) shares; compare against MiD `tagesganglinie` if PDF parsed, else descriptive only. |  |  |
| TASK-T12 | `metrics/travel.py::access_egress_times(trips, mode='pt')` → for PT trips, mean access (walk to stop) + egress times if MATSim routing already populated those columns; descriptive output, flag if column missing. |  |  |
| TASK-T13 | `metrics/travel.py::car_occupancy(trips, persons)` → mean occupancy of car trips (fahrer + mitfahrer) per Kreis. MiD reference ≈ 1.42 person/Pkw. |  |  |
| TASK-T14 | `metrics/travel.py::vmt_per_kreis(trips)` → vehicle-kilometres travelled per Kreis (car only), expanded ×10. Used downstream for emission-style sanity checks. |  |  |

### Implementation Phase 5 — Professional Reporting

- GOAL-005: Produce a publication-grade, self-contained HTML report (printable to PDF) plus a JSON summary. Visual identity must look professional: consistent typography, fixed colour palette, branded footer, KPI cards, conditional formatting.

#### 5a. Visual identity & shared style

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-021a | `report/style.py` — central style module. Defines `PALETTE` (synth = `#1f4e79` "BS-Blau", reference = `#c00000` "MiD-Rot", neutral = `#7f7f7f`, accent = `#2e8b57`, soft grid `#e6e6e6`); `MODE_COLORS = {miv: '#c00000', oev: '#1f4e79', rad: '#2e8b57', fuss: '#7f7f7f'}`; `PURPOSE_COLORS` (categorical Set2-like, 8 entries). Exposes `apply_mpl_style()` which sets `matplotlib.rcParams` to: font `DejaVu Sans` 10pt (titles 12pt bold), figure 7×4.2 in @ 150 dpi, `axes.spines.top/right = False`, light grid `0.6` alpha, tight layout. Every plot calls `apply_mpl_style()` first. |  |  |
| TASK-021b | `report/style.py::deviation_color(value, scale='pp')` — returns a hex colour from a diverging palette (red ↔ white ↔ green) for HTML cell-background conditional formatting based on absolute deviation; thresholds parameterised per metric (population ±2 %, mode ±5 pp, distance ±3 km, etc.). |  |  |

#### 5b. Plot catalogue (publication quality)

All plots: 7×4.2 in figure, 150 dpi, white background, BS-Blau for synth bars, MiD-Rot for reference, value labels on bars, source caption ("Quelle: synth 10 % vs. MiD 2023 / BA 2025 / Zensus 2022"), axis labels in German, title bold ohne Unterstreichung, legend in oberer rechter Ecke ohne Rahmen. Saved as `validation_plots/<name>.png` (also `.svg` for vector use).

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-021-01 | `plot_population_ratio` — horizontal bar chart per Kreis: `synth_expanded` vs. Zensus 2022; right panel shows deviation in % with red/green highlight. |  |  |
| TASK-021-02 | `plot_age_pyramid` — back-to-back horizontal bar pyramid (left = m, right = w) with synth solid + Zensus stepline overlay. |  |  |
| TASK-021-03 | `plot_household_size` — grouped vertical bars, sizes 1/2/3/4/5+, synth vs. Zensus, per Kreis facet (3×3 grid using `plt.subplots(3, 3, sharey=True)`). |  |  |
| TASK-021-04 | `plot_income_per_kreis` — synth mean income vs. INKAR; lollipop chart with deviation labels. |  |  |
| TASK-021-05 | `plot_employment_share` — bar synth vs. MiD P9 per Kreis. |  |  |
| TASK-021-06 | `plot_commute_flows_heatmap` — 8×N matrix (home Kreise × top-15 work destinations) showing log-scaled absolute flows; annotate cells > 1 000 SvB. |  |  |
| TASK-021-07 | `plot_commute_flows_scatter` — scatter `synth` vs. `BA` flow on log axes for all Kreis-pairs ≥ 50 SvB; identity line + Pearson-r and RMSE in title. |  |  |
| TASK-021-08 | `plot_commute_distance_cdf` — empirical CDF of commute distances (synth) overlaid with MiD P13 step CDF; shaded ±5 pp band; mean values as vertical lines. |  |  |
| TASK-021-09 | `plot_commute_mode_share` — stacked horizontal bars per Kreis, synth on top / MiD below; 4 modes coloured per `MODE_COLORS`. |  |  |
| TASK-021-10 | `plot_license_rate` — dot-plot per Kreis with two markers (synth, MiD), connected by a thin line; deviation in pp annotated. |  |  |
| TASK-021-11 | `plot_trip_distance_dist` — grouped bars, MiD P13 classes × {synth, MiD}; secondary panel shows distribution per purpose (small multiples 2×3). |  |  |
| TASK-021-12 | `plot_trip_duration_dist` — grouped bars, duration classes × {synth, MiD}. |  |  |
| TASK-021-13 | `plot_mode_share_overall` — donut chart synth (outer ring) vs. MiD (inner ring); values + deviation in legend. |  |  |
| TASK-021-14 | `plot_mode_share_by_purpose` — 100 % stacked bars per purpose, MiD overlay markers if available. |  |  |
| TASK-021-15 | `plot_mode_share_by_distance` — heatmap (distance band × mode) with `Blues`-`Reds` divergent colormap, annotate share %; row totals on right. |  |  |
| TASK-021-16 | `plot_trip_purpose_mix` — pie + table side-by-side, synth shares with MiD reference column. |  |  |
| TASK-021-17 | `plot_trips_per_person` — bar per Kreis with 95 % CI error bars; horizontal MiD reference line at 3.1. |  |  |
| TASK-021-18 | `plot_daily_distance` — bar per Kreis, MiD reference line ~39 km. |  |  |
| TASK-021-19 | `plot_departure_time_profile` — 24-h area chart with morning + evening peaks shaded; per-mode small multiples below. |  |  |
| TASK-021-20 | `plot_kreis_map` — choropleth map of ZGB-8 (loaded from `data/spatial/communes.gpkg`) coloured by selected metric (population deviation, mean commute distance, mode share MiV); uses geopandas `plot()` with `legend=True` and Kreis labels via `representative_point()`. |  |  |

#### 5c. HTML report

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-022a | `report/templates.py` — string-template constants for HTML head, header, KPI-card, section, table, footer. CSS embedded inline: system font stack, max-width 1100px, sticky chapter nav on the left, alternating row colours, `@media print` rules so each chapter starts on a new page. |  |  |
| TASK-022b | `report/html.py::render_kpi_card(label, synth, ref, unit, threshold)` — returns a styled `<div class="kpi">` with big synth value, smaller reference, deviation badge (green ✓ / amber ! / red ✗) based on threshold. Used in the top dashboard strip. |  |  |
| TASK-022c | `report/html.py::render_table(df, highlight_col=None, threshold=None)` — `df.to_html()` with conditional row colouring via `style.applymap` on the deviation column. |  |  |
| TASK-022d | `report/html.py::render(report_data, plots, summary)` — assembles the full document. Structure: cover (logo, title "Validierungsbericht — Synthetische Bevölkerung Großraum Braunschweig 10 %", date, sample size, git SHA from `subprocess git rev-parse HEAD`), executive-summary KPI strip (8 cards), chapter nav, chapters 1–7 (each with intro paragraph in German, table, plot(s), interpretation paragraph), appendix with data sources + methodology + limitations, footer with timestamp + repository link. Returns the HTML string. |  |  |
| TASK-022e | `report/html.py::write_report(html, path)` — writes UTF-8 HTML and a sibling `validation_report.pdf` if `weasyprint` is importable (best-effort, silently skipped otherwise; logs a hint that browser print-to-PDF is available). |  |  |
| TASK-023 | `report/json.py::dump_summary(report_data, path)` — flat dict with key metrics: `population_ratio`, `commute_outbound_r`, `commute_inbound_r`, `commute_mode_share_dev`, `license_rate_dev`, `mean_commute_distance_km`, `external_share`, `trips_per_person`, `mean_trip_distance_km`, `mean_trip_duration_min`, `daily_distance_km`, `mode_share_overall_{miv,oev,rad,fuss}`, `mode_share_dev_overall`, `purpose_mix_l1_dist`. Includes a `traffic_light: {green,amber,red}` per KPI based on threshold. |  |  |
| TASK-024 | `__main__.py` glues phases 2–5 together; prints a 15-line coloured console summary (KPI table with `traffic_light` flags); writes `validation_report.html`, `validation_report.pdf` (best-effort), `validation_summary.json` and `validation_plots/*.{png,svg}` next to the synth outputs. |  |  |

### Implementation Phase 6 — Verification

- GOAL-006: Tests + dry-run execution.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-025 | Add `tests/test_validate_bs_10pct.py` with one test per metric module — invoke each function on a small fixture loaded from the real outputs, assert column schema, assert no NaN in deviation columns, assert `synth_expanded.sum() ∈ [1.05M, 1.20M]`. |  |  |
| TASK-026 | Run `python -m scripts.validate_bs_10pct` end-to-end; verify HTML opens, summary JSON parses, all plots non-empty. |  |  |
| TASK-027 | Append validation findings (top-line metrics + interpretation) to `plan/calibration-analysis-2025.md` section 10. |  |  |

## 3. Alternatives

- **ALT-001**: Re-using the existing `validation_mid2023.ipynb` notebook only — rejected because notebooks are hard to re-run automatically and they don't cover BA flows / Zensus / INKAR.
- **ALT-002**: Generating a PDF report via WeasyPrint — rejected; HTML alone is enough and avoids an extra heavy dependency. User can print-to-PDF from the browser.
- **ALT-003**: Using `eqasim`'s legacy `documentation/paper.py` — rejected; that module is FR-pipeline specific and would need substantial rework for ZGB-8.
- **ALT-004**: Live extraction of every PDF table — rejected; the CSV mirrors of P9/P12_1/P13/P17_1 already exist (extracted by `scripts/extract_mid_tables.py`). Only the regional aggregate is fetched from the PDF.

## 4. Dependencies

- **DEP-001**: pandas 1.5.3, numpy 1.23.5, geopandas 1.0.1 (already in conda `eqasim` env).
- **DEP-002**: matplotlib (already present).
- **DEP-003**: optional `pdfplumber` for MiD PDF parsing; gracefully degrades if absent.
- **DEP-003b**: optional `weasyprint` for HTML→PDF rendering; gracefully degrades to HTML-only output if absent (user can browser-print).
- **DEP-004**: Synth outputs in `eqasim-data/output_bs_10pct/` (generated by `config_local_braunschweig_10pct.yml`).
- **DEP-005**: BA Pendleratlas CSVs and Zensus 2022 flatfiles must remain at their current paths.
- **DEP-006**: `data/spatial/communes.gpkg` (already produced by the pipeline) for choropleth maps.

## 5. Files

- **FILE-001**: `scripts/validate_bs_10pct/__init__.py` — package marker, exports `main`.
- **FILE-002**: `scripts/validate_bs_10pct/config.py` — paths, scope constants.
- **FILE-003**: `scripts/validate_bs_10pct/io.py` — synth output loaders.
- **FILE-004**: `scripts/validate_bs_10pct/references.py` — MiD/BA/Zensus/INKAR loaders.
- **FILE-005**: `scripts/validate_bs_10pct/metrics/population.py` — population & age/sex.
- **FILE-006**: `scripts/validate_bs_10pct/metrics/households.py` — household & income.
- **FILE-007**: `scripts/validate_bs_10pct/metrics/employment.py` — employment share.
- **FILE-008**: `scripts/validate_bs_10pct/metrics/commute.py` — flows + distance + external.
- **FILE-009**: `scripts/validate_bs_10pct/metrics/mode.py` — commute mode share + license + commute trip rate.
- **FILE-009b**: `scripts/validate_bs_10pct/metrics/travel.py` — verkehrliche Kenngrößen (alle Zwecke): distance/duration distributions, modal split overall + per purpose + per distance band, trips/person, daily km, departure profile, car occupancy, VMT, MID_BASELINE constant.
- **FILE-010**: `scripts/validate_bs_10pct/report/plots.py` — matplotlib helpers (one function per chart from TASK-021-01…-20).
- **FILE-010b**: `scripts/validate_bs_10pct/report/style.py` — PALETTE, MODE_COLORS, PURPOSE_COLORS, `apply_mpl_style()`, `deviation_color()`.
- **FILE-010c**: `scripts/validate_bs_10pct/report/templates.py` — HTML/CSS string templates.
- **FILE-011**: `scripts/validate_bs_10pct/report/html.py` — HTML rendering (cover, KPI cards, chapters, conditional formatting, optional PDF via WeasyPrint).
- **FILE-012**: `scripts/validate_bs_10pct/report/json.py` — JSON dumper with traffic-light flags.
- **FILE-013**: `scripts/validate_bs_10pct/__main__.py` — CLI orchestrator.
- **FILE-014**: `tests/test_validate_bs_10pct.py` — pytest module.
- **FILE-015** (output): `eqasim-data/output_bs_10pct/validation_report.html`.
- **FILE-015b** (output, optional): `eqasim-data/output_bs_10pct/validation_report.pdf` (only if WeasyPrint installed).
- **FILE-016** (output): `eqasim-data/output_bs_10pct/validation_summary.json`.
- **FILE-017** (output): `eqasim-data/output_bs_10pct/validation_plots/*.png` and `*.svg`.

## 6. Testing

- **TEST-001**: `test_population_by_kreis_schema` — schema + monotonic Kreis order + `synth_expanded` between 1.05 M and 1.20 M total.
- **TEST-002**: `test_population_ratio_within_5pct` — every Kreis ratio in [0.95, 1.05].
- **TEST-003**: `test_household_size_no_nan` — household-size DataFrame has no NaN deviation.
- **TEST-004**: `test_commute_outbound_correlation` — Pearson r ≥ 0.8 on Kreis-pair flows ≥ 50 SvB.
- **TEST-005**: `test_distance_mean_within_3km` — region mean commute distance within ±3 km of MiD P13 (20.7 km).
- **TEST-006**: `test_mode_share_within_5pct` — region-level commute mode shares (auto, oeffentlich, fahrrad, zu_fuss) within ±5 pp of MiD P12 region aggregate.
- **TEST-007**: `test_license_rate_within_3pct` — adult license rate within ±3 pp of MiD P17 region aggregate.
- **TEST-008**: `test_main_writes_html_and_json` — full CLI run produces both artefacts non-empty.
- **TEST-009**: `test_trips_per_person_within_15pct` — region trips/person/day in `[2.6, 3.6]` (MiD 3.1 ± 15 %).
- **TEST-010**: `test_mean_trip_distance_within_3km` — region mean trip length within ±3 km of MiD 12.6 km.
- **TEST-011**: `test_mean_trip_duration_within_5min` — region mean trip duration within ±5 min of MiD 22 min.
- **TEST-012**: `test_mode_share_overall_within_8pct` — region modal split (alle Zwecke) within ±8 pp of MiD baseline per mode.
- **TEST-013**: `test_purpose_mix_l1_below_15pct` — L1 distance between synth purpose mix and MiD baseline below 0.15.
- **TEST-014**: `test_distance_distribution_no_nan` — every distance bin has finite share, no NaN.
- **TEST-015**: `test_all_plots_exist_and_nonempty` — every PNG referenced by `report_data['plots']` exists and is > 5 KB.
- **TEST-016**: `test_html_contains_all_chapters` — rendered HTML contains every German chapter heading (`Bevölkerung`, `Haushalte`, `Erwerb`, `Pendlerströme`, `Verkehrsnachfrage Arbeitsweg`, `Verkehrliche Kenngrößen`, `Zusammenfassung`) and the KPI dashboard strip.
- **TEST-017**: `test_json_traffic_lights_complete` — every KPI in JSON has a `traffic_light` field in `{green, amber, red}`.

## 7. Risks & Assumptions

- **RISK-001**: ENTD trip mode mapping → MiD 4 buckets is lossy; document mapping table inside `mode.py`.
- **RISK-002**: `commutes.gpkg` may include external (non-ZGB) work locations whose `kreis` is `EXT…`; needs special handling in flow comparison.
- **RISK-003**: BA Pendleratlas counts socially-insured employees only (SvB), MiD covers all employed → expect synth/BA ratio ≈ 1.18-1.25; bake this factor into the comparison and explain.
- **RISK-004**: PDF parsing of the *Ergebnistabellen…Großraum Braunschweig* PDF may fail on Windows; implementation must fall back gracefully.
- **RISK-005**: `12411-0018_de.csv` age structure is age × sex × Kreis — but row labels may be German "Insgesamt" / "männlich" / "weiblich"; the loader must be tolerant.
- **RISK-006**: `trips.csv` may not contain a `network_distance_km` column — fall back to crow-fly distance from `trips.gpkg` linestrings (already EPSG:25832).
- **RISK-007**: `trips.csv` `arrival_time`/`departure_time` are seconds-since-midnight, can wrap past 24h; clamp `travel_time_min = max(0, (arrival - departure)/60)` and drop trips with negative duration.
- **RISK-008**: ENTD-derived activity chains may overrepresent 'leisure' compared with MiD; the report flags this rather than calibrates against it.
- **RISK-009**: PDF tagesganglinie (departure-time profile) is hard to extract; default to descriptive synth-only output.
- **ASSUMPTION-001**: ZGB-8 Zensus 2022 total ≈ 1.135 M; sampling-expanded synth must land within ±2 %.
- **ASSUMPTION-002**: User wants German section headings + English code comments (consistent with prior phases).
- **ASSUMPTION-003**: A single HTML file is acceptable as the "PDF" — user can print-to-PDF.
- **ASSUMPTION-004**: MiD region-aggregate values for *alle Zwecke* (3.1 trips, 12.6 km, 22 min) are taken from infas Ergebnistabellen Großraum Braunschweig 2023 — to be confirmed from PDF page 1–3 during TASK-020/T01.

## 8. Related Specifications / Further Reading

- [plan/calibration-analysis-2025.md](plan/calibration-analysis-2025.md) — prior calibration cycle.
- [plan/migration-braunschweig-1.md](plan/migration-braunschweig-1.md) — Braunschweig migration baseline.
- [scripts/analyze_calibration.py](scripts/analyze_calibration.py) — pattern source for loaders.
- [braunschweig/analysis/validation_mid2023.ipynb](braunschweig/analysis/validation_mid2023.ipynb) — pattern source for MiD maths.
- [eqasim-data/data/braunschweig/Ergebnistabellen_MiD2023_Version2_infas_7555_Großraum_Braunschweig.pdf](eqasim-data/data/braunschweig/Ergebnistabellen_MiD2023_Version2_infas_7555_Großraum_Braunschweig.pdf) — MiD regional reference.
- BA Pendleratlas 2025 (`statistik_pendler_2026042493412.csv`, `…430.csv`) — commuter ground truth.
- Zensus 2022 (`5000H-2001_de_flat.csv`, `12411-0018_de.csv`) — population & household ground truth.
