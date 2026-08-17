# Upstream fix sweep — eqasim-france since fork point (#199)

> **Exhaustive sweep, 2026-07-23** (supersedes the bounded first pass of 2026-07-17).
> Active upstream development lives in `eqasim-org/eqasim-france` (renamed from
> ile-de-france). The true merge-base of `origin/main` with `france/main` is
> `62fa577` (2024-09-25) — older than our `eqasim-bavaria` fork point `b20fbe6`
> (2025-10-06), because bavaria incorporated some later france changes by content
> without sharing history. Every classification below was therefore checked **by
> content against our tree**, not by commit ancestry.
>
> Sweep range: `62fa577..france/main` (`6115005`, 2026-07-21) — 78 commits touching
> shared code (`data/`, `synthesis/`, `matsim/`, `analysis/`, `documentation/`).
> The next sweep starts from `6115005`.

## Ported (in our tree, with regression tests in `tests/test_upstream_ports_199.py`)

| Upstream PR | Area | What was fixed |
|---|---|---|
| [#447](https://github.com/eqasim-org/eqasim-france/pull/447) multinomial | `synthesis/.../primary/candidates.py` | Float64 `_normalize_weights` at both call sites (ported 2026-07-17, PR #206). |
| [#447](https://github.com/eqasim-org/eqasim-france/pull/447) GTFS merge | `data/gtfs/utils.py` | Removed the `astype(str)` coercions in `merge_two_feeds`: genuine NaN became the string `"nan"`, corrupting `isna()`-based logic (parent-station handling in `cut_feed`). Was deferred to #200 but NOT covered when #200 closed — recovered by this sweep. |
| [#428](https://github.com/eqasim-org/eqasim-france/pull/428) | `data/gtfs/utils.py` | `merge_two_feeds` tolerates collision slots without their identifier column (e.g. `attributions` without the optional `attribution_id`). |
| [#521](https://github.com/eqasim-org/eqasim-france/pull/521) | `data/gtfs/utils.py` | (1) Standalone stops (`location_type` 0, no parent station) are promoted to stations at read so `cut_feed` does not silently drop them wherever the feed also contains real stations; (2) duplicate-id reference rewrite only requires the slot in the second feed. Deviation: our port guards (1) on the `location_type` column existing (upstream would `KeyError` on feeds without it, which #309 explicitly supports). |
| [#512](https://github.com/eqasim-org/eqasim-france/pull/512) | `data/gtfs/utils.py` | `agency.txt` without an `agency_id` column (optional for single-agency feeds) gets `"generic"` instead of a `KeyError`. |
| [#427](https://github.com/eqasim-org/eqasim-france/pull/427) (partial) | `data/gtfs/utils.py` | `DTYPES`: id columns (`stop_id`, `parent_station`, `agency_id`) read as `str` so numeric-looking ids / NaN cells cannot flip dtypes between feeds. Same dtype=str-at-read pattern as our key-matching audit (#191/#194). Rest of #427 is warning cleanup — not ported. |
| [#309](https://github.com/eqasim-org/eqasim-france/pull/309) | `data/gtfs/utils.py` | `cut_feed` no longer crashes on valid feeds without a `location_type` column (falls back to keeping all stops, logged). |
| [#291](https://github.com/eqasim-org/eqasim-france/pull/291) (partial) | `synthesis/output.py` | `trips.parquet` was written with `to_csv` (CSV bytes in a `.parquet` file); now `to_parquet`. The education-weight part of #291 is French external-education data — N/A. |
| [#414](https://github.com/eqasim-org/eqasim-france/pull/414) | `synthesis/population/matched.py` | Statistical-matching sort gets identifier/weight tie-breakers, making the assignment invariant to donor input row order. **Behaviour change:** tie-break order differs from before, so matched donors (and downstream results) can differ between pipeline versions. |
| [#438](https://github.com/eqasim-org/eqasim-france/pull/438) | `matched.py`, `secondary/locations.py`, `supply/processed.py`, `simulation/prepare.py` | `processes` declared `volatile = True`: changing the process count no longer devalidates cached stages. Mirrors upstream's four files; our own stages (e.g. `matsim/simulation/run.py`, `braunschweig/`) can adopt the same pattern incrementally. |

## Already fixed independently / already present in our tree

| Upstream PR | Verdict |
|---|---|
| [#265](https://github.com/eqasim-org/eqasim-france/pull/265), [#387](https://github.com/eqasim-org/eqasim-france/pull/387) supply stages devalidated by population changes | Our `matsim/scenario/supply/{osm,gtfs}.py` already depend on `data.spatial.iris` for the CRS, not on home locations. |
| [#341](https://github.com/eqasim-org/eqasim-france/pull/341) OSM cut area `values[0]` | Our `data/osm/cleaned.py` dissolves before taking `values[0]` — equivalent to upstream's `union_all()`. |
| [#439](https://github.com/eqasim-org/eqasim-france/pull/439) runtime-stage restructuring | We solved the same synpp config-scoping problem (our #229) by delegating to the helpers' `configure()`. |
| [#522](https://github.com/eqasim-org/eqasim-france/pull/522) missing hts identifiers | Upstream repaired an inconsistency their own #346 rename introduced (`hts_id` vs `hts_person_id`). Our tree uses `hts_id` consistently, and `enriched.py` asserts person/household counts after every merge, so silent drops fail loudly. |
| [#509](https://github.com/eqasim-org/eqasim-france/pull/509) bike-availability NA drop | Introduced by France-only #492 merge; our `enriched.py` does not carry that merge and is assert-guarded (see above). |

## Deferred to owning issues

| Upstream PR | Owner | Note |
|---|---|---|
| [#531](https://github.com/eqasim-org/eqasim-france/pull/531) `--activity-types` for eqasim Java config | #201 (escort purpose) | eqasim-java side available since our 2.2.0 update (#204, closed); the pipeline flag lands with the escort purpose. |
| [#506](https://github.com/eqasim-org/eqasim-france/pull/506) pt2matsim build flag `-Dskip.surefire.tests` | pt2matsim upgrade | Only needed for pt2matsim > 22.3; we pin 22.3 where `-DskipTests=true` works. |
| [#407](https://github.com/eqasim-org/eqasim-france/pull/407) write CRS into MATSim files | pt2matsim upgrade | Coupled to `Gtfs2TransitScheduleWithParameters` (pt2matsim v26) and newer eqasim writers; revisit when bumping pt2matsim. |
| [#442](https://github.com/eqasim-org/eqasim-france/pull/442) pt2matsim v26.1 (drops `despace_stop_ids`, calendar reorder) | pt2matsim upgrade | Our workarounds are still required on 22.3. |

## Not applicable (French data / France-only code paths)

`fix:` commits: [#503](https://github.com/eqasim-org/eqasim-france/pull/503) (legacy `od/weighted.py`,
overridden by our gravity model), [#538](https://github.com/eqasim-org/eqasim-france/pull/538) (French
census OD cleaning), [#515](https://github.com/eqasim-org/eqasim-france/pull/515) (AGED→AGEREV census
variable + optional early-population debug output; we have our own population-validation tooling),
[#492](https://github.com/eqasim-org/eqasim-france/pull/492) (EGT children below 5; MiD covers all ages),
[#466](https://github.com/eqasim-org/eqasim-france/pull/466) (PCS2020), [#485](https://github.com/eqasim-org/eqasim-france/pull/485)
(MobiSurvStd urban type), [#379](https://github.com/eqasim-org/eqasim-france/pull/379) (EMP weekend
households; our MiD import handles day types itself), [#300](https://github.com/eqasim-org/eqasim-france/pull/300)
(BPE schools), [#299](https://github.com/eqasim-org/eqasim-france/pull/299) (French `analysis/synthesis/`
layer — not present in our tree), [#291](https://github.com/eqasim-org/eqasim-france/pull/291)-education
part, [#267](https://github.com/eqasim-org/eqasim-france/pull/267)/[#268](https://github.com/eqasim-org/eqasim-france/pull/268)
(EGT/EDGT), [#537](https://github.com/eqasim-org/eqasim-france/pull/537)/[#540](https://github.com/eqasim-org/eqasim-france/pull/540)/[#541](https://github.com/eqasim-org/eqasim-france/pull/541)/[#542](https://github.com/eqasim-org/eqasim-france/pull/542)
(French municipality/BPE/SIRENE/BDTOPO data updates), [#343](https://github.com/eqasim-org/eqasim-france/pull/343)
(HBEFA keys in the French `data/vehicles` registre path — unused by us; our own HBEFA mapping lives in
`braunschweig/synthesis/vehicles/hbefa.py` and is tested separately. Note for a future emissions run:
upstream settled on HBEFA-native strings like `"pass. car"`, we write `PASSENGER_CAR` — verify against
the MATSim emissions contrib version before use).

`feat:`/`chore:` commits (out of scope for a fix sweep, noted for completeness): feature work such as
MobiSurvStd import (#346), motorcycles (#384), escort purpose (#495 → our #201), secondary force field
(#385), uv packaging (#498), osmium (#321), parquet SIRENE (#348), numpy RNG modernisation (#423 —
would change RNG streams, do not adopt blindly); dependency pins are managed independently in our
`environment.yml` (synpp already 1.6.2).

## Verification

- New regression tests: `tests/test_upstream_ports_199.py` (8 tests, one per ported fix).
- Adjacent suites green: GTFS filter, matching keys, matched-reusable, runtime config
  declares, execute-context contract, output columns, determinism, cordon PT gates,
  PT reachability, popsim matching batch (127 tests).
- The #414 port changes matching tie-breaks: expect small, seed-like shifts in matched
  donors on the next full run (documented in the code comment; not a calibration change).

## Next sweep

Start from `6115005` (2026-07-21):

```bash
git fetch france
git log france/main --oneline 6115005.. -- data/ synthesis/ matsim/ analysis/ documentation/
```
