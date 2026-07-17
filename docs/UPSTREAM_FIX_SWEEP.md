# Upstream fix sweep — eqasim-france since fork point (#199)

> First bounded pass, 2026-07-17. Fork point with `eqasim-org/eqasim-bavaria` is
> `b20fbe6` (2025-10-06); active upstream development lives in `eqasim-org/eqasim-france`
> (renamed from ile-de-france). This table classifies the specific `fix:`/`feat:` PRs
> surfaced in the 2026-07-17 ecosystem survey against our code. It is **not** yet an
> exhaustive commit-by-commit walk of all ~200 upstream commits — that remains open
> under #199.

## Classification

| Upstream PR | Area | Applies to us? | Verdict / action |
|---|---|---|---|
| [#447](https://github.com/eqasim-org/eqasim-france/pull/447) multinomial normalization | `synthesis/.../primary/candidates.py` | **Yes** | **PORTED** (this branch): float64 `_normalize_weights` at both call sites. Defensive hardening, byte-identical for float64 (verified 0/5000). |
| [#447](https://github.com/eqasim-org/eqasim-france/pull/447) GTFS merge `astype(str)` NaN | `data/gtfs/utils.py` `merge_two_feeds` | **Yes** (live: `gtfs/cleaned.py:46` merges when >1 feed) | **Deferred to #200** — removing the coercions is behaviour-changing (dedup logic) and needs feed-level verification, which the #200 GTFS/Flexo work does anyway. |
| [#503](https://github.com/eqasim-org/eqasim-france/pull/503) `fix_origins` origin×category | `data/od/weighted.py` | **Legacy path only** | **Not ported.** All real configs override `data.od.weighted` with `braunschweig.gravity.model`, so `weighted.py` is the OFF/legacy census path and never runs in production. The fix also reworks French-census `category` (commute_mode/age_range) logic. Low value; port only if the legacy path is revived. |
| [#538](https://github.com/eqasim-org/eqasim-france/pull/538) OD cleaning memory | `data/od/cleaned.py` | **No** | **N/A.** French-census-specific (`TRANS` mode codes, `AGEREV10` age codes). Our OD comes from German data via the gravity model. |
| [#531](https://github.com/eqasim-org/eqasim-france/pull/531) configurable activity params | `matsim/simulation/prepare.py`, `data/hts/selected.py` | **Yes, but coupled** | **Deferred to #201 + #204.** Adds `--activity-types` to the eqasim Java config generation so custom purposes (escort, task) are declared. Tied to the escort purpose (#201) and bumps `DEFAULT_EQASIM_COMMIT`/version (#204). Port together with those. |
| [#509](https://github.com/eqasim-org/eqasim-france/pull/509) bike-availability NA drop | `synthesis/population/enriched.py` | **No** | **N/A.** The dropped-persons bug was introduced by France-specific #492 (`number_of_bikes` merge). Our `enriched.py` does not merge `number_of_bikes` (it appears only in `eqasim_common/analysis/marginals.py`, analysis-side). Our own key-matching audit (#191/#194) already hardened the enrichment merges. |
| [#512](https://github.com/eqasim-org/eqasim-france/pull/512) optional agency id in GTFS | `data/gtfs/*` | **Maybe** | **Deferred to #200** (GTFS import review). Verify against our feed handling. |
| [#521](https://github.com/eqasim-org/eqasim-france/pull/521) GTFS-related problems | `data/gtfs/*` | **Maybe** | **Deferred to #200.** |
| [#515](https://github.com/eqasim-org/eqasim-france/pull/515) population sampling & validation | `synthesis/population/sampled.py` etc. | **Maybe** | **Open** — uses `AGEREV` vs `AGED` (French census). Check whether the sampling/validation change (not the census-var change) applies. Low expected relevance. |

## Summary

- **1 ported** here (#447 multinomial).
- **3 deferred** to existing issues that own the same code (#200 GTFS x2, #201/#204 activity-types).
- **3 N/A** (French-census-specific or France-only enrichment).
- **1 open** for a closer look (#515), low expected relevance.

## Next (still open under #199)

An exhaustive walk of every upstream `fix:` commit since `b20fbe6` remains to be done.
When completed, update `docs/UPSTREAM_DELTA.md` with the sweep date so the next sweep
starts from there. Recompute the merge-base with:

```bash
git fetch upstream && git merge-base origin/main upstream/main
```
