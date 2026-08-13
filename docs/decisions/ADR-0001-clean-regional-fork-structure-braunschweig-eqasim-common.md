# ADR-0001 · 2026-04-27 · Clean regional fork structure (`braunschweig/` + `eqasim_common/`)
- **Status:** active
- **Context:** The upstream code was fenced inside a `bavaria/` package; a Braunschweig model
  needed its own clearly separated module while keeping shared helpers reusable.
- **Decision:** Lock the region to ZGB-8 (ARS prefixes 03101/03102/03103/03151/03153/03154/
  03157/03158), introduce a region-neutral `eqasim_common/` package (shared OSM, gravity-distance,
  spatial-code, location helpers) and a new `braunschweig/` package (IPF, location, gravity,
  enrichment, MATSim simulation); migrate stage names `bavaria.* → braunschweig.*` with aliases
  only where the DAG still consumes upstream leaf modules.
- **Rationale:** Clean separation of region-specific from shared code, per the MATSim/eqasim
  modularity convention in `CLAUDE.md`.
- **Consequences:** First tagged release `v0.1.0-bs`; new configs (1%/10%/25%/dryrun), seed
  `1234`, gravity slope `-0.065`; test suite rewritten around BS configs.
- **Evidence:** `CHANGELOG.md` v0.1.0-bs (2026-04-27); produced by branch
  `refactor/braunschweig-clean-fork`.

---

## Population synthesis

