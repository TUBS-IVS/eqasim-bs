# ADR-0081 · 2026-07-02 · Fleet data-regionalization ceiling + multi-vintage provenance discipline (originally numbered ADR-0050 on `feature/fleet-quality-and-data`; renumbered at merge -- ADR-0050 on `main` is a different, unrelated record)

- **Status:** active — fleet regionalization complete on `feature/fleet-quality-and-data`.
- **Context:** The household-fleet synthesis requires per-Kreis vehicle characteristics to
  regionalise the national-level KBA reference tables.  Four dimensions were evaluated:
  fuel/powertrain type, Euro emission class, vehicle age band, and segment.  Regional
  open-data coverage differs by dimension: Destatis Regionalstatistik 46251 publishes per-Kreis
  fuel (series -02) and Euro class (series -03); KBA publishes per-Gemeinde EV share
  (``kba_ev_gemeinde_timeseries_2023_2026.csv``) and a per-model-series fuel breakdown
  (Modellreihen Bestand).  Per-Kreis AGE and SEGMENT distributions are unavailable in any open
  German source (KBA FZ series, LSN, Regionalstatistik 46251 all confirmed).
- **Decision:** (1) Regionalise per-Kreis FUEL via Destatis Regionalstatistik 46251-02 (Stichtag
  01.01.2025) -> ``kba_kreis_fuel.csv``; per-Kreis EURO via 46251-03 (01.01.2025) ->
  ``kba_kreis_euro.csv``.  (2) Refresh per-Gemeinde EV tilt to KBA April 2026 (BEV/PHEV/fuel-cell)
  -> ``kba_gemeinde_ev.csv``.  (3) Refresh segment-model distribution and per-model fuel weights to
  KBA Modellreihen 01.01.2026 -> ``kba_model_fuel.csv``, ``kba_segment_model.csv``.  (4) Use
  KBA/Statista ID3438 (01.01.2026) as the national Pkw age validation anchor ->
  ``kba_age_national.csv`` (not an IPF control).  (5) Wire a 5 km grid EV tilt from
  ``kba_ev_grid_5km_2026.gpkg`` (KBA, April 2026) as a sub-communal within-Gemeinde EV re-weight
  (``kba_ev_grid.csv``), default-ON with graceful fallback to Gemeinde-only.  (6) Keep vehicle
  AGE and SEGMENT at the national/MiD-derived level — these two axes are at their ceiling and no
  open regional source exists.
- **Rationale:**
  - *Data ceiling (age + segment):* KBA publishes no per-Kreis age or segment table in any of its
    open FZ series (FZ 27, FZ 12, FZ 1).  LSN Niedersachsen and Destatis Regionalstatistik 46251
    do not cover those dimensions either.  The national distributions (``kba_age_fuel.csv``,
    ``kba_segment_model.csv``) remain the best available reference.
  - *Multi-vintage provenance:* the supplied statistics differ by Stichtag and regulatory
    definition (46251 = vehicle register at business seat; FZ 27.17 = private cars only; Modellreihen
    = current registered stock).  This is handled as PROVENANCE not reconciliation: each derived CSV
    carries its own ``stichtag`` column; the fuel/euro axes both carry Stichtag 2025-01-01 and are
    combinable (same register, same date); the EV share is a tilt (ratio) not a hard count, so a
    one-quarter lag between the Modellreihen and the EV grid is scientifically acceptable; no silent
    reconciliation of counts across different Stichtag vintages is performed.
  - *KBA register-vs-household caveat:* KBA counts vehicles REGISTERED in a Kreis (including
    company and leasing cars registered at the business seat), whereas the synthesis produces
    HOUSEHOLD cars.  Spatial patterns and order-of-magnitude comparisons are valid; exact equality
    is not.
  - *5 km grid EV tilt:* initially considered redundant given the per-Gemeinde tilt.  A
    household-weighted variance decomposition on the full 100% population (558,281 households)
    showed that **76.1% of the EV-share spatial variance is WITHIN-Gemeinde** (23.9% between-Gemeinde,
    already captured by the Gemeinde tilt), concentrated in the three large cities BS/WOB/SZ (~45%
    of households) where EV ownership clusters in high-income neighbourhoods.  The within-Gemeinde
    signal is currently uncaptured, making the grid tilt scientifically valuable.  The light
    implementation (per-household ``grid_ev_share`` injected at the home-location stage ->
    within-Gemeinde EV re-weight, Gemeinde aggregate preserved) is the minimum sufficient change.
  - *``euro="electric"`` (A4):* pure-electric drivetrains (BEV + fuel-cell) carry a real
    ``"electric"`` euro category; PHEV/hybrid retain their real combustion euro class.  No
    ``"na"`` or missing markers appear anywhere in the fleet output: the full-imputation /
    consistent-fleet requirement (A4) is satisfied.  The legacy ``consistency_v2=False`` path
    stays a verbatim byte-identical copy.
- **Consequences:** Fleet synthesis now uses regionalised fuel+euro distributions per Kreis (not a
  uniform national prior); within-Gemeinde EV placement is driven by real spatial data; age and
  segment remain national (at their ceiling); every derived CSV carries a ``stichtag`` column for
  provenance tracking; the ``kba/raw/`` subdirectory documents the 7 new raw inputs (local-only,
  not committed).
- **Evidence:** branch ``feature/fleet-quality-and-data``; extractors ``extract_kreis_fuel_46251``,
  ``extract_kreis_euro_46251``, ``extract_age_national``, ``extract_gemeinde_ev``,
  ``extract_model_fuel``, ``extract_ev_grid`` in ``scripts/extract_kba_fleet.py``; provenance tests
  ``tests/test_kba_provenance.py``; ``eqasim-data/data/braunschweig/kba/README.md``.