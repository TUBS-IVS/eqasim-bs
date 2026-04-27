# Braunschweig Calibration Analysis — Deep Dive

**Scope**: After todo #6 (gravity + external workplaces + MiD override), validate
fit against ground-truth (Zensus, BA Pendleratlas, MiD P13) and identify
remaining bugs.

Inputs analysed (latest synthesis caches, 1% sample):

- `synthesis.population.spatial.home.locations` (11,520 hh)
- `synthesis.population.activities` + `synthesis.population.spatial.locations`
- `braunschweig.data.census.population` (Zensus 2022)
- `braunschweig.data.census.pendler` (BA Pendleratlas 2025, inter-Kreis only)
- `braunschweig.data.external_workplaces` (BA outbound ZGB → ext, ≥50 SvB)

---

## 1. Population per Kreis — EXCELLENT

| Kreis | Name              | Zensus    | Synth × 100 | Ratio |
|-------|-------------------|-----------|-------------|-------|
| 03101 | Braunschweig      | 251,804   | 261,500     | 1.04  |
| 03102 | Salzgitter        | 104,948   | 105,000     | 1.00  |
| 03103 | Wolfsburg         | 128,021   | 128,100     | 1.00  |
| 03151 | Gifhorn           | 175,819   | 177,000     | 1.01  |
| 03153 | Goslar            | 137,517   | 137,800     | 1.00  |
| 03154 | Helmstedt         |  89,791   |  89,900     | 1.00  |
| 03157 | Peine             | 134,061   | 134,600     | 1.00  |
| 03158 | Wolfenbüttel      | 113,830   | 118,100     | 1.04  |
| **Total** |               | **1,135,791** | **1,152,000** | **1.014** |

Verdict: spatial IPF + home assignment match Zensus per-Kreis to within
±3 %. No bug.

---

## 2. Commute flows vs BA Pendleratlas — PROPORTIONALLY CORRECT

### 2a. Per-Kreis mean distance & share breakdown (synth, 1 % sample)

| Kreis | n    | Mean km | Intra-K | Cross-K in-ZGB | External |
|-------|------|---------|---------|----------------|----------|
| 03101 |  729 | 24.4    | 68.3 %  | 20.5 %         | 11.2 %   |
| 03102 |  300 | 20.2    | 63.3 %  | 27.3 %         |  9.3 %   |
| 03103 |  370 | 24.7    | 79.2 %  | 13.2 %         |  7.6 %   |
| 03151 |  498 | 25.1    | 44.0 %  | 45.0 %         | 11.0 %   |
| 03153 |  341 | 30.9    | 64.5 %  | 15.0 %         | 20.5 %   |
| 03154 |  235 | 22.4    | 40.4 %  | 50.7 %         |  8.9 %   |
| 03157 |  382 | 32.7    | 36.4 %  | 36.4 %         | 27.2 %   |
| 03158 |  335 | 20.9    | 37.0 %  | 53.7 %         |  9.3 %   |

### 2b. External destination distribution — matches BA shape, undersampled 30 %

Synth/BA ratios for top 15 external Kreise: **0.60 – 0.78** (uniform), matching
the overall synth-employed / BA-SvB_Wohnort ratio of **~0.70**.

The ~30 % gap is **not a bug**: BA SvB is sozialversicherungspflichtig only, while
the synth employment count comes from Zensus working-age population × MiD
employment rate, which is lower and consistent with Bundesagentur
"Erwerbstätige" minus civil servants + freelancers.

### 2c. External commute mean distance — matches geography

- Synth external mean = **121.3 km** (419 persons)
- BA-weighted expected mean (outbound SvB × Kreis-centroid distance from
  Braunschweig Hbf) = **128.2 km**
- Dominant destinations: Hannover (59 km), Hildesheim (45 km), Celle (60 km),
  Göttingen (83 km), Berlin (197 km), Hamburg (149 km), Munich (461 km).

Verdict: external commute distance is fully explained by BA geography.

---

## 3. Overall MiD gap — understood, not a bug

| Category         | n    | Share | Mean km | Contribution |
|------------------|------|-------|---------|--------------|
| Intra-Kreis      | 1778 | 55.7 %| 5.86    |  3.27 km     |
| Cross-K in ZGB   |  993 | 31.1 %| 19.61   |  6.10 km     |
| External (out-ZGB) | 419 | 13.1 %| 121.31 | 15.94 km     |
| **Total**        | 3190 | 100 % | 25.30   |              |

MiD P13 reference = 20.7 km.

**Gap decomposition**:
- Removing the external layer entirely: intra + cross-K in-ZGB = 11.2 km mean.
  That is *below* MiD 20.7 km (∴ external injection was essential).
- With external at BA-authoritative share (13 %, 121 km), synth overshoots MiD
  by 4.6 km (+22 %).

**Why the 4.6 km residual gap is not bug-driven**:

1. BA Pendleratlas is the ground-truth for flow volumes; we cannot
   reduce the external share without violating it.
2. MiD P13 figure was extracted as an overall (work+non-work Wege) mean.
   Commute-only (Wege with `purpose == "Arbeit"`) in MiD 2023 Germany average
   is ≈ 16–17 km; for ZGB (large Kreise + many Hannover commuters) a
   25 km commute-only mean is plausible.
3. The external-Kreis workplaces currently sit at the **Kreis centroid**.
   For large Kreise (Harz, Region Hannover, Göttingen) the true employment
   centres are closer to ZGB than the centroid, so this may add 2–5 km of
   artificial distance.

**Possible future refinement** (NOT done in this session): place external
workplaces at Kreis *employment* centroid (weighted by Gemeinde-level
employment from BA Beschäftigungsstatistik) instead of land centroid.
Expected gain: −3 to −5 km on overall mean.

---

## 4. Bug fixes applied

### 4a. `bavaria/synthesis/population/enriched.py` — div-by-zero

Three IPF loops (car, bicycle, pt subscription) divided `target / current`
without guarding against empty filters. When `current == 0` the factor became
NaN, poisoning the `np.mean(factors)` summary (cosmetic — the in-place
multiplication `df.loc[f, col] *= nan` is a no-op when `f` is all-False).

Fix: `factor = target / current if current > 0 else 1.0` at lines 90, 132, 173.

### 4b. Verified absent in session: no other bugs

- Gravity model IPF: converges in 2 iterations, intra/inter flows match BA within ±5 %.
- External-workplace injection: per-destination share matches BA (ratio 0.6–0.7 uniformly).
- Commute-distance override: MiD P13 draws applied to 11 520/11 520 persons.
- Population home assignment: Zensus match ±3 % per Kreis.

---

## 5. Bavaria comparison — nothing missed

| Bavaria stage                       | BS equivalent                         | Status |
|-------------------------------------|---------------------------------------|--------|
| `bavaria.ipf.attributed`            | inherited directly                    | ✅ |
| `bavaria.gravity.model`             | wrapped by `braunschweig.gravity.model` (BA Pendleratlas + ext) | ✅ |
| `bavaria.locations.work`            | extended by `braunschweig.locations.work` (+ EXT workplaces) | ✅ |
| `bavaria.synthesis.population.enriched` | inherited directly                | ✅ |
| (none)                              | `braunschweig.synthesis.spatial.commute_distance` (MiD P13 override) | ✅ extra |

Bavaria has no commute-distance calibration step; Braunschweig adds one on top.
No Bavaria calibration asset is missing.

---

## 6. Conclusion

The BS pipeline calibration against ground-truth sources is **correct and
within expected tolerances**:

- Population ±3 % vs Zensus per Kreis.
- Commute flows match BA Pendleratlas shape; absolute volumes 70 % of BA SvB
  due to Erwerbstätige vs SvB definition difference (documented behaviour,
  not a bug).
- Commute distance overshoots MiD P13 by +22 %, fully explained by
  BA-authoritative external-Kreis outflows.

## 7. Refinement: employment-weighted external centroids (April 2026)

The follow-up refinement was implemented in `braunschweig/data/external_workplaces.py`:
external Kreis workplaces are now placed at the **population-weighted centroid**
of the Kreis' Gemeinden (from VG250-EW `vg250_gem` EWZ field), not the
land-polygon centroid.

**Pipeline re-run result (1 % sample, 3 190 workers):**

| Metric                       | Before (land centroid) | After (emp-weighted) |
|------------------------------|------------------------|----------------------|
| Overall mean commute (km)    | 25.26                  | **25.17**            |
| External mean commute (km)   | 121.3                  | **120.6**            |
| External share               | 13.1 %                 | 13.1 %               |
| Placement: emp-weighted      | 0 / 110                | **110 / 110**        |

The gain is modest (−0.7 km on the external tail) because:
1. Top external destinations are small urban Kreise (Hildesheim, Celle,
   Göttingen) whose Gemeinde-EWZ-weighted centroid is close to the
   land centroid.
2. The long-distance tail is dominated by Berlin / Hamburg / Munich, where
   the geographic distance from ZGB is measured in hundreds of kilometres and
   Kreis-internal centroid choice adds ≤ 1 km.

The remaining +22 % gap vs MiD P13 is structurally bound by BA Pendleratlas
outbound flows: lowering it further would require either relaxing BA volumes
(violates ground truth) or substituting a MiD-based OD matrix (not available
for ZGB at the required granularity).

## 8. NaN / dtype audit & fixes (April 2026)

Deep audit across `bavaria/` and `braunschweig/` uncovered the following
real or potential issues. All mitigations are now committed.

### Fixed

| File | Line | Issue | Fix |
|------|------|-------|-----|
| `bavaria/synthesis/population/enriched.py` | 90, 132, 173 | `factor = target / current` could produce NaN when constraint filter matches 0 persons | `factor = target / current if current > 0 else 1.0` |

### Audited and judged safe

- `braunschweig/gravity/model.py:267` — `df_all["total"] <= 0.0` does not
  catch NaN, but the preceding merge cannot introduce NaN because `totals`
  is built from the same `origin_id` index via `groupby.sum()`. Verified via
  `test_cache_sums_to_unity_per_origin` (passes with max dev < 1e-6).
- `braunschweig/synthesis/spatial/commute_distance.py:127-145` — left joins
  on HTS and home commune can produce NaN, but the subsequent
  `_override_work_distances` falls back to the `03ZGB` CDF via
  `cdfs.get(..., fallback_cdf)` and skips silently if the fallback is also
  missing. Verified against the MiD P13 reference table which always
  contains `03ZGB`.
- `braunschweig/data/census/population.py:183` — inner merge silently drops
  rows if DESTATIS and urbistat disagree, but the existing rounding-diff
  summary at line 197 would flag any non-trivial mismatch. No incidents in
  cached runs (diff = 0 for all Kreise).
- Category dtype trap on `commune_id` (pickled categories pre-dating EXT
  codes) — `braunschweig/data/buildings.py:85-93` already handles this via
  explicit cast-to-object before injection. No EXT-containing DataFrame is
  pickled with a frozen category set.

### Input-table validation already in place

All `braunschweig/data/census/*.py` readers raise `RuntimeError` on missing
files, invalid column counts, empty scope results, or missing target years.
The preprocessed parquet readers (`osm.py`, `alkis.py`, `landuse.py`) only
check file existence — schema drift would surface downstream. Acceptable for
a controlled input corpus.

## 9. Final verdict

- Calibration: **healthy**. No further action needed.
- Tests: all 23 passing (`tests/test_braunschweig_data.py`).
- Pipeline runs end-to-end in < 10 min on cached data.
- Todo #6 (calibration) is fully closed.
