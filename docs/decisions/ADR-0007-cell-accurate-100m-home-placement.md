# ADR-0007 · 2026-06 · Cell-accurate (100m) home placement
- **Status:** active
- **Context:** PopulationSim expands households per 100m Zensus cell, so homes should be placed
  within the household's own 100m cell, not just its commune.
- **Decision:** Place each household in a real building inside its `ZENSUS100m` cell
  (`synthesis/locations/home_cell.py`), using intersection-based footprint→cell membership to
  reduce boundary orphans, with a commune-level area-weighted fallback for empty cells.
- **Rationale:** Uses the Zensus 100m grid (committed); intersection join reduces orphans vs a
  centroid test (commit `73c8acf`).
- **Consequences:** Active on the popsim path; the legacy area-weighted draw (with the 400m² cap)
  is retained only for the non-popsim path; later refined by ALKIS-typed matching (ADR-0008).
- **Evidence:** commits `73c8acf`, `88078d3`, `bf1be42`; PROJECT_STATUS.md §2.1 (Zensus 100m grid).

