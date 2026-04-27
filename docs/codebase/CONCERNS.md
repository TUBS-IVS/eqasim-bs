# CONCERNS

> Verified hypotheses (H1..H4) and known residual deviations. Evidence-backed.

## H1 — Purpose `home` overshoot (+27 pp) is a **reporting artefact**, not a synthesis bug. ✅ CONFIRMED

**Evidence**:
- [synthesis/population/activities.py](synthesis/population/activities.py) line 22 — for every trip, `purpose = preceding_purpose` (origin activity); line 35 — for the **last** trip-leg, `purpose = following_purpose`. Result: each closed trip-chain ends with a row where the activity at the destination is `home`.
- [scripts/validate_bs_10pct/metrics.py](scripts/validate_bs_10pct/metrics.py) line 246 — `purpose_mix()` counts `following_purpose` (destination of each trip leg). Every return-home leg → `following_purpose == "home"`.
- MiD's `Wegezweck` convention reports the *purpose of the activity at destination*, but classifies the **return-home leg** under the *originating activity's purpose* (e.g. "Rückweg von der Arbeit" → `Arbeit`). eqasim does not.
- Therefore: synth `home` 42.4 % vs MiD 15 % is largely a coding-convention mismatch.

**Fix scope (R-D)**: reporting-only. In `metrics.purpose_mix()` and `metrics.mode_share_by_purpose()`, replace `following_purpose` with a derived `mid_purpose`:
```
mid_purpose = following_purpose if following_purpose != "home" else preceding_purpose
```
Optionally drop trips where both ends are `home` (zero-length).

**No synthesis change**, no Bavaria change, no Java change.

## H2 — Mode-share bias (bike −10 pp, walk +9.9 pp) — IDF-inherited utilities. **DEFERRED** (CON-002, no Java)

**Evidence (indirect)**: mode choice happens in `eqasim-java`, which we cannot touch in this iteration. Document residual; revisit in next cycle.

## H3 — Mean trip distance 8.94 km < MiD 12.6 km. **PARTIALLY EXPLAINED**

**Evidence**:
- 1 % cache analysis ([plan/calibration-analysis-2025.md](plan/calibration-analysis-2025.md) §3) showed total commute mean ~25 km after EXT injection; the **non-commute** secondary trips drag the all-trip mean down because `mode == walk` is overrepresented (H2). 
- Gravity `DEFAULT_DIAGONAL=1.0` favours intra-commune attraction, concentrating short-distance flows.
- **Hypothesis to test in R-A**: lowering `DEFAULT_DIAGONAL` and/or re-fitting `DEFAULT_SLOPE` against ZGB-internal BA Pendleratlas inter-Gemeinde flow length distribution should shift the gravity output toward longer trips.

**Fix scope (R-A)**: re-fit `gravity_slope` / `gravity_constant` / `gravity_diagonal` against BA inter-Gemeinde flows; expose new BS-level config keys (do not modify Bavaria defaults).

## H4 — HH-size IPF uses 5-bin (1/2/3/4/5+) although Zensus has 6 bins. ✅ CONFIRMED

**Evidence**:
- [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py) lines 28-35 — `SIZE_BINS` deliberately merges `"5 Personen"` and `"6 und mehr Personen"` into `"5+"`. Comment: "Bavaria's 5-bin scheme: 1, 2, 3, 4, 5+ (Zensus 5P and 6+P merged)".
- The loader emits **one global distribution for the whole ZGB-8 scope** (`_load_region_distribution` sums across all `ars5` in scope). The IPF therefore cannot fit per-Kreis HH structure.

**Fix scope (R-C)**:
1. Split `5+` into `5` and `6+` (data already in the CSV; trivial).
2. Switch loader to emit per-`ars5` distributions (group by `ars5` instead of summing). Wire downstream IPF to consume per-Kreis margins. **This may require a new `braunschweig.ipf.*` shim** so we don't modify `bavaria.ipf.*` (CON-001).

**No new data needed** — Zensus 5000H-2001 already contains 6+ bin and per-Gemeinde rows.

## Residual deviations (10 % run, [report.json](eqasim-data/output_bs_10pct/validation/report.json))

| KPI | Synth | Ref | Δ | Disposition |
|---|---|---|---|---|
| Population per Kreis | ±2 % | Zensus 2022 | OK | keep |
| Trips/person | 3.10 | MiD 3.1 | OK | keep |
| Bike share | 3.0 % | 13 % | −10 pp | R-E (deferred) |
| Walk share | 27.9 % | 18 % | +9.9 pp | R-E (deferred) |
| Mean trip dist | 8.94 km | 12.6 km | −3.7 km | R-A |
| Daily km | 32.5 | 39 | −6.5 | partially R-A, R-E residual |
| Purpose `home` | 42.4 % | 15 % | +27 pp | R-D (reporting only) |
| Purpose `leisure` | 14.8 % | 27 % | −12 pp | R-D (mostly absorbed by H1 fix) |
| HH-size dist (per-Kreis) | unknown — must be measured | Zensus per-Kreis | — | R-C (instrument first) |
| OD top-200 R² | unknown | BA | — | new validation, then R-A |

## Tech debt / fragile spots
- [braunschweig/gravity/model.py:267](braunschweig/gravity/model.py) — comment notes `df_all["total"] <= 0.0` does not catch NaN. Audited safe but worth a unit test.
- [bavaria/synthesis/population/enriched.py:90,132,173](bavaria/synthesis/population/enriched.py) — already fixed (div-by-zero guard) per [plan/calibration-analysis-2025.md §4a](plan/calibration-analysis-2025.md). Bavaria CON-001 means we do **not** maintain that fix in this cycle.
- The validation script's `commute_od_kreis` builds GeoDataFrame end-points lazily — slow on 10 %. Cache opportunity later.

## Evidence
- File/line citations above; raw KPIs in [eqasim-data/output_bs_10pct/validation/report.json](eqasim-data/output_bs_10pct/validation/report.json).
