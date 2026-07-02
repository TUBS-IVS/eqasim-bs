# KBA / MiD fleet reference data

This directory holds raw KBA and MiD source files (local-only, not committed to git)
and the derived tidy CSVs that **are** committed and consumed by the fleet synthesis.

All derived CSVs are produced by `scripts/extract_kba_fleet.py` (idempotent; re-run
to regenerate from the raw files).  Do **not** hand-edit the derived CSVs.

---

## raw/ subdirectory (local-only, gitignored)

The `raw/` subdirectory contains seven source files that must be present locally to
re-run the extractor.  They are **not committed** (the `eqasim-data` tree is gitignored
for large binary files).

| File | Source | Stichtag / Period | Content |
|------|--------|-------------------|---------|
| `regionalstatistik_46251_02_fuel_kreis_20250101.csv` | Destatis Regionalstatistik 46251-02 | 01.01.2025 | Per-Kreis Pkw counts by fuel type (Benzin, Diesel, Gas, Hybrid, PHEV, Elektro, sonstige); latin-1 encoding, semicolon-separated, 8-row header |
| `regionalstatistik_46251_03_euro_kreis_20250101.csv` | Destatis Regionalstatistik 46251-03 | 01.01.2025 | Per-Kreis Pkw counts by Euro emission group (Euro 1–6, Sonstige), two rows per Kreis (insgesamt + Diesel subset); latin-1 encoding, semicolon-separated, 8-row header |
| `statista_kba_3438_pkw_age_national_2026.xlsx` | KBA / Statista ID 3438 | 01.01.2026 | National Pkw age-band distribution (6 bands: under 2 yr, 2–4, 5–9, 10–14, 15–29, 30+) as shares in percent; sheet "Daten" |
| `kba_ev_gemeinde_timeseries_2023_2026.csv` | KBA open data portal (per-Gemeinde EV timeseries) | Latest period: 2026.04 (April 2026) | Per-Gemeinde cumulative EV/BEV/PHEV/fuel-cell share timeseries (all reporting dates 2023–2026); utf-8-sig encoding, comma-separated |
| `kba_modellreihen_bestand_2020_2026.csv` | KBA Fahrzeugzulassungen — Modellreihen Bestand | 01.01.2026 | Per-model-series (Marke + Modellreihe) registered stock with Anzahl, Diesel, Hybrid, Hybrid_Plugin, BEV counts; utf-8-sig encoding, semicolon-separated |
| `kba_ev_grid_5km_2026.gpkg` | KBA open data portal (5 km grid EV share) | April 2026 | 5 km × 5 km grid cells with per-cell EV share (``elektro_an`` in percent); EPSG:3857; suppressed cells carry ``ZS_Anteil_ == "-"`` |
| `fz27_202501.xlsx` | KBA FZ 27 (series 2025-01) | 01.01.2025 | Multi-sheet: FZ 27.10 segment × powertrain, FZ 27.15 Kreis × powertrain (ZGB only), FZ 27.17 Gemeinde × private BEV/PHEV (ZGB only), FZ 27.4 Niedersachsen fuel × Euro class, FZ 27.7 age band × fuel, FZ 27.11 brand × powertrain |

Legacy raw files (also local-only, used by earlier extractors):

| File | Source | Content |
|------|--------|---------|
| `fz12_2025.xlsx` | KBA FZ 12.1 | Segment × model (Modellreihe); superseded for the `kba_segment_model.csv` output by the 2026 Modellreihen CSV, but kept as a cross-check |
| `output_mit_2023_bundesland_fahrzeuge.xlsx` | MiD 2023 | Segment × economic status by Bundesland |
| `output_mit_2023_raumtyp_fahrzeuge.xlsx` | MiD 2023 | Segment × economic status by RegioStaR Raumtyp |

---

## derived/ subdirectory (committed to git)

The derived CSVs are committed so that the synthesis pipeline does not require the raw
files to run.  Re-running `scripts/extract_kba_fleet.py` regenerates them identically.

### New regionalization outputs (added 2026-07-02)

| File | Stichtag | Provenance | Content |
|------|----------|------------|---------|
| `kba_kreis_fuel.csv` | 2025-01-01 | Destatis Regionalstatistik 46251-02 | Per-Kreis Pkw counts and shares by fuel type (petrol, diesel, gas, bev, phev, hybrid, other); ZGB Kreise only (8 rows) |
| `kba_kreis_euro.csv` | 2025-01-01 | Destatis Regionalstatistik 46251-03 | Per-Kreis Pkw counts and shares by Euro emission group (euro1–euro6, other); two rows per Kreis (teil=all / teil=diesel); ZGB Kreise only |
| `kba_age_national.csv` | 2026-01-01 | KBA / Statista ID 3438 | National Pkw age-band distribution (6 bands) as share_pct; VALIDATION ANCHOR only — not used as an IPF control; file is prepended with a `# mean_age_years=10.9 source=KBA/Statista ID3438 stichtag=2026-01-01` comment line |
| `kba_gemeinde_ev.csv` | 2026-04-01 | KBA per-Gemeinde EV timeseries | Per-Gemeinde EV/BEV/PHEV/fuel-cell shares (fractions); latest period only; ZGB Gemeinden only |
| `kba_model_fuel.csv` | 2026-01-01 | KBA Modellreihen Bestand 01.01.2026 | Per-model-series fuel shares (petrol, diesel, hybrid, phev, bev); used by fleet_sampling_de to regionalise model-specific powertrain mix |
| `kba_ev_grid.csv` | 2026-04-01 | KBA 5 km EV grid (April 2026) | Per-cell EV share (fraction), cell bounding box in EPSG:3857 (minx/miny/maxx/maxy), suppression flag; ZGB bbox clip applied |

### Legacy outputs (present before 2026-07-02)

| File | Source | Content |
|------|--------|---------|
| `kba_segment_powertrain.csv` | FZ 27.10, Stichtag 01.01.2025 | Segment × powertrain national shares |
| `kba_kreis_powertrain.csv` | FZ 27.15, Stichtag 01.01.2025 | Per-Kreis BEV/PHEV/alt share (ZGB only) |
| `kba_gemeinde_private_bev.csv` | FZ 27.17, Stichtag 01.01.2025 | Per-Gemeinde private BEV/PHEV counts (ZGB only) |
| `kba_fuel_euro_nds.csv` | FZ 27.4, Stichtag 01.01.2025 | Niedersachsen fuel × Euro class |
| `kba_age_fuel.csv` | FZ 27.7, Stichtag 01.01.2025 | National age band × fuel |
| `kba_brand_powertrain.csv` | FZ 27.11, Stichtag 01.01.2025 | National brand × powertrain |
| `kba_segment_model.csv` | KBA Modellreihen 01.01.2026 (replacing FZ 12.1) | Segment × model share |
| `mid2023_segment_by_status_bundesland.csv` | MiD 2023 | Segment × economic status by Bundesland |
| `mid2023_segment_by_status_raumtyp.csv` | MiD 2023 | Segment × economic status by RegioStaR Raumtyp |
| `mid2023_age_by_segment_status.csv` | MiD 2023 | Age band × segment × economic status |

---

## Column conventions

- `stichtag`: ISO date string (`YYYY-MM-DD`) of the KBA/Destatis reference date.
- `kreis_ags5`: 5-digit Amtlicher Gemeindeschlüssel for the Kreis.
- `ags8`: 8-digit AGS (Gemeinde-level).
- `*_share`: share as a fraction [0, 1] (not percent).
- `cell_id`: KBA 5 km grid cell identifier (e.g. `5kmN2695E4340`).
- `suppressed`: True if the KBA cell carries the `-` placeholder (small-count suppression).

---

## Provenance note

Multiple Stichtag vintages are present by design (see ADR-0050).  The 46251 fuel and euro
data share Stichtag 2025-01-01 and are combinable.  The EV tilt data (Gemeinde and grid)
carry Stichtag 2026-04-01 and are used as ratio tilts, not hard counts, so the ~15-month lag
to the 46251 base is scientifically acceptable.  The Modellreihen and age data carry
Stichtag 2026-01-01.  No reconciliation of absolute counts across vintages is performed.

KBA register data counts vehicles REGISTERED in a Kreis (including company/leasing cars at
the business-seat address).  The synthesis produces HOUSEHOLD cars.  Spatial patterns and
order-of-magnitude comparisons are valid; exact numeric equality between KBA counts and
synthesised fleet counts is not expected and must not be asserted.
