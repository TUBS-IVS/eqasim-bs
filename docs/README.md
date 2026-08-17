# docs/ — map

Governance: [`DOCUMENTATION_GOVERNANCE.md`](DOCUMENTATION_GOVERNANCE.md)
(who owns which fact). Scientific mental model:
[`MODEL_OVERVIEW.md`](MODEL_OVERVIEW.md).

| Path | What it is | Maintained |
|---|---|---|
| `registry/features/*.yml` | Feature Registry (semantics, evidence, applicability) | by hand, checker-verified |
| `registry/stages/*.yml` | Stage Registry (semantics, Bavaria lineage, alias seams) | by hand, checker-verified |
| `registry/data/*.yml` | Data Registry (provenance, licensing, exact paths) | by hand, checker-verified |
| `registry/dag/*.json` | Extracted synpp DAG snapshots per workflow | `python -m braunschweig.documentation dag` |
| `decisions/ADR-*.md` | One file per architecture decision record | by hand (append-only ids) |
| `runs/*.yml` | One manifest per significant run (validation evidence) | by hand at run close-out |
| `generated/*.md` | **Generated** views (STATUS, PIPELINE, STAGES, FEATURES, DATA, LINEAGE, DECISIONS, RUNS) | `python -m braunschweig.documentation build` — never edit |
| `features/*.md` | Per-feature scientific method deep-dives (no live state) | by hand |
| `codebase/*.md` | Architecture/conventions/testing notes for contributors | by hand |
| `population/`, `data/`, `runs/*.md`, `simulation.md`, `population.md` | Workflow deep-dives and run monitor artifacts | by hand |
| `MODEL_OVERVIEW.md` | The 10-minute model overview | by hand |
| `UPSTREAM_DELTA.md`, `UPSTREAM_FIX_SWEEP.md` | Pinned fork point + upstream fix sweeps | by hand |
| `archive/` | Historical documents (banner-marked, not authoritative) | frozen |
| `superpowers/` (gitignored) | Session design specs & plans | local |
