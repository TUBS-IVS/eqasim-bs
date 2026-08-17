# `braunschweig/synthesis/population/enriched/` package split

## What it is

The attribute-enrichment stage for the `simple_ipf_open` production method:
income, driving licence, PT subscription, car/bike ownership, economic status,
housing tenure and RegioStaR-7 augmentation. It merges the inherited
eqasim-bavaria enrichment base with Braunschweig-specific overlays (MiD-2023
vehicle counts, INKAR income scaling, BS-resident flag). Under the active
production methods `popsim_mid`/`popsim_open` this stage is bypassed entirely
(the `synthesis.population.enriched` alias resolves instead to the thin
`braunschweig.popsim.enriched_adapter`, which passes donor attributes through
without running this stage's logic — see
`docs/registry/stages/synthesis.population.enriched.yml`), so it is only live
via the `simple_ipf_open` fixture.

## Split shape and import path

Package conversion: the flat module (2890 lines, per PR #270) — itself a merge
of the inherited eqasim-bavaria enrichment and the legacy Braunschweig
`enriched.py` overlay — became a package. `__init__.py` (321 lines) is the
synpp stage (`configure`/`execute`/`validate`) and re-exports every name its 6
submodules define: `availability` (PT-subscription conditioning + consistent
`car_availability`), `base` (the inherited eqasim-bavaria `configure`/`execute`
— car/bike/PT IPF, household-size and household-income sampling),
`economic_status` (5-class MiD economic status, income-class-derived and
MiD-hhtype-x-region-Bayes-derived variants), `housing_tenure`,
`income_distribution` (MiD income-bracket draw with the INKAR Kreis tilt, and
the legacy INKAR class-midpoint scaling), `vehicle_ownership` (MiD H7/H12.3
vehicle-count sampling and income-aware `number_of_cars`).

The import path is unchanged: `braunschweig.synthesis.population.enriched`
resolves to the package's `__init__.py`.

## Cache / `validate()` consequences

The stage gained a `validate()` hook it never had before: `_HELPER_MODULES` in
`__init__.py` lists all 6 submodules, and `validate()` hashes their combined
source into the synpp validation token. As with the other stage-package
splits, the first run after this change recomputes the stage and everything
downstream of it once (a new token has nothing stored to compare against);
every run after that is cache-stable.

**Known gap (found by the #290 audit).** The token covers the 6 in-package
submodules. Four first-party modules imported from outside the package are not
in the tuple and therefore outside it: `braunschweig.data.mid.reference_tables`
(module level, and again inside `availability`) plus
`braunschweig.data.mid.{income_by_size,income_by_status,tenure_by_income}`
(imported inside functions). Editing any of them does not devalidate this stage.
Note the contrast with `braunschweig.data.mid.{data,zones}`, which this stage
declares properly via `context.stage(...)` and which therefore need no token
coverage at all. See `synpp-helper-hash-audit.md`.

## Standing rules

- Every submodule extracted from this package **must** be listed in
  `_HELPER_MODULES` in `__init__.py`.
- **Facade re-export / monkeypatch surface (verified in code):**
  `enriched.delegate`, `enriched.pd`, `enriched.np` and `enriched.gpd` are
  re-exported from `.base` — `__init__.py` itself does not import `pandas`,
  `numpy` or `geopandas` directly. These re-exports are bindings taken **only
  at import time**: a submodule that already imported the original (e.g.
  `enriched.base`, which does its own `import pandas as pd`) keeps its own,
  separate bound reference. A test or patch must therefore target the
  submodule attribute (`enriched.base.pd`), not the facade (`enriched.pd`) —
  patching the facade silently patches nothing that `base.py`'s own code
  actually reads. The same shape (and the same caveat) applies to the other
  stage/helper packages split under issue #267 — see
  `secondary-chainsolvers-split.md` and `popsim-mid-split.md`.

## PR / issue reference

PR #270 (`refactor/split-enriched`), part of the collective oversized-module
backlog issue #267.
