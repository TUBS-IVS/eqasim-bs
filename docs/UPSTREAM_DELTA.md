# Upstream delta — eqasim-bs vs. eqasim-bavaria

> What `TUBS-IVS/eqasim-bs` adds on top of its upstream parent
> `eqasim-org/eqasim-bavaria`. This anchors the project history (and `ADR-0000` in
> [DECISIONS.md](DECISIONS.md)) at the fork point, so every later change is traceable
> as a delta on a known baseline.

## Fork point (pinned)

| | Value |
|---|---|
| Upstream | `eqasim-org/eqasim-bavaria` (remote `upstream`) |
| Fork (this repo) | `TUBS-IVS/eqasim-bs` (remote `origin`) |
| **Merge-base commit** | **`b20fbe6`** — "Merge pull request #14 from eqasim-org/chore/rename" (2025-10-06) |
| Commits on `origin/main` since fork | ~776 |
| New `braunschweig/` module | 303 files, ~70,446 insertions |
| Total tree delta since fork | 903 files changed, ~160,973 (+) / ~35,690 (−) |

Recompute the merge-base any time with:

```bash
git fetch upstream
git merge-base origin/main upstream/main
```

## Upstream fix sweeps (eqasim-france)

Active upstream development lives in `eqasim-org/eqasim-france` (remote `france`);
its true merge-base with `origin/main` is `62fa577` (2024-09-25). Bug fixes in shared
pipeline code are swept periodically and classified in
[UPSTREAM_FIX_SWEEP.md](UPSTREAM_FIX_SWEEP.md).

| Sweep date | Range covered | Result |
|---|---|---|
| 2026-07-17 | bounded first pass (survey PRs only) | 1 ported, 3 deferred, 3 N/A (PR #206) |
| 2026-07-23 | `62fa577..6115005` exhaustive (78 shared-code commits) | 10 ported (incl. recovered #447-GTFS), 5 already fixed, 4 deferred, rest N/A (#199) |

The next sweep starts from `6115005` (2026-07-21).

## What eqasim-bavaria provides (the baseline)

The upstream is the eqasim pipeline configured for **Bavaria/Munich**: the eqasim
Python synpp pipeline (population synthesis from the French ENTD trip donor + Bavarian
census), the eqasim Java MATSim modules (mode choice, scoring, simulation), and the
Bavaria/Munich scenario configs. eqasim-bs inherits this entire scientific machinery.

## What eqasim-bs adds (the delta)

A new region (**Zweckverband Großraum Braunschweig, ZGB-8, Niedersachsen**) plus a large
body of data-driven realism and tracking infrastructure, almost all flag-gated with a
byte-identical OFF path. The substantive areas (each detailed in `docs/features/*` and
recorded as an ADR in [DECISIONS.md](DECISIONS.md)):

- **`braunschweig/` Python module** — the entire ZGB adaptation: MiD 2023 / Zensus 2022 /
  KBA / INKAR / BA Pendleratlas / Destatis reference data layer, population synthesis
  (IPF + PopulationSim `popsim_mid`), attribute enrichment, location/gravity/education
  models, cordon/einpendler, freight, analysis + SimWrapper export, calibration corner.
- **`eqasim-java-bs` fork** — our own editable Java project (`braunschweig` module), wired
  via `eqasim_source_path`, MATSim `2025.0-PR3568` (parking, freight injection,
  mode-availability, SimWrapper contrib).
- **PM / tracking layer** — `PROJECT_STATUS.md`, `PROJECT_BACKLOG.md`, `RUNS.md`,
  `docs/DECISIONS.md`, `docs/features/*`, `CONTRIBUTING.md`, `.github/` templates,
  `docs/codebase/`.

> The full feature inventory with status lives in [../PROJECT_STATUS.md](../PROJECT_STATUS.md);
> the per-decision history (with commit/PR links) in [DECISIONS.md](DECISIONS.md).
