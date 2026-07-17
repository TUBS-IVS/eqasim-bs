# Gravity model: per-RegioStaR-7 distance slope


`braunschweig.gravity.model` distributes work/education trips with a
distance-decay friction `exp(slope * d_ij)`. The `slope` is differentiated by
the **RegioStaR-7** class (BMV/BBSR urban-rural typology, codes 71-77) of the
origin Gemeinde, so urban origins (flatter slope, longer commutes) and rural
origins (steeper slope, shorter commutes) decay at their own rate. The
flow-weighted mean of the per-class slopes is held equal to `gravity_slope`
(-0.065), so the regional mean commute distance is unchanged; only the
sub-Kreis distribution is differentiated (the commute-distance KPI itself is
MiD-P13-overridden, see `commute_distance.py`).

Calibration (`scripts/calibrate_gravity_per_rs7.py --anchor-scope ring`) fits a
single **identified full-panel Poisson GLM** on the BA Pendleratlas Kreis-pair
flows:

```
log E[flow_ij] = origin_FE_i + dest_FE_j + sum_c delta_c * d_ij * 1[RS7(i)=c]
```

A per-origin fit with destination fixed effects is rank deficient on this data
(one flow row per origin-destination pair makes distance collinear with the
per-destination dummies), so the full panel is used: each `delta_c` is
identified from within-origin distance variation pooled across the many origins
of class `c`. The anchor Kreise are chosen by an adaptive ring that grows around
ZGB until every RS7 code present in ZGB has at least 5 anchors (225 km / 141
Kreise at present). Pinned values live in `config_*braunschweig*.yml` under
`gravity_slope_by_regiostar7` (do not hand-edit; re-run the script and paste its
YAML). `braunschweig.data.bbsr.regiostar` assigns every in-scope Gemeinde an RS7
code, filling Gemeinden absent from the RegioStaR-2020 reference (e.g.
Langelsheim, 03153019) by geographic nearest neighbour, so all 123 gravity
origins receive a typed slope.

Tests: `tests/test_gravity_ring_calibration.py` (ring selection + panel
recovery), `tests/test_regiostar_fill.py` (nearest-neighbour fill),
`tests/test_gravity_slope_config.py` (the `None` default / flatten contract).

## Sector-aware attraction tilt (PARKED, default OFF — ADR-0065)

`braunschweig.gravity.sector_aware_enabled` (default `False`) tilts the
per-Gemeinde work attraction by establishment density (`n_betriebe` per
employee vs the Kreis mean; Kreis totals preserved). Measured 2026-07-15
(issue #128, gravity-only A/B on ZGB-8): no effect on the commute distance
distribution, but a 9x worse fit of per-Gemeinde work inflows against the
OBSERVED SvB-am-Arbeitsort counts — the attraction vector IS that observation,
so the tilt can only distort it. PARKED; do not enable in run configs. The
underlying concern is covered by building work potentials (within-Gemeinde)
and, if ever needed, WZ-sectoral friction (issue #128 phase 2, deferred).
See ADR-0065 in docs/DECISIONS.md. Entry point:
`build_destination_attraction` in `braunschweig/gravity/model.py`; tests in
`tests/test_work_sector_aware.py`.

## Inner VerBindungen calibration anchor (#193)

Flag `braunschweig.gravity.verbindungen_anchor_enabled` (default `False` --
the OFF path is byte-identical; the anchor CHANGES the work OD when ON).
Transfers the VerBindungen 2019 within-Kreis-pair ROW-CONDITIONAL destination
shares (QZM, comparison-zone level: stadtteil cells collapsed to their parent
commune, vg250 cells kept, 41 zones on ZGB-8) into the CALIBRATED Gemeinde
work OD, between `_calibrate` and `_append_outbound_flows`. Vintage
hierarchy: the 2025 Pendleratlas wins across Kreise; the 2019 QZM only
refines structure WITHIN a Kreis pair -- every Kreis-pair block total is
conserved (asserted to 1e-9 relative, violations raise; per-row observed
mass likewise). Censoring rule A: only observed relations (>= 10 commuters
in 2019) are re-weighted; censored relations and the observed-vs-censored
split stay gravity-driven; every skip/fallback is counted and logged
(coverage skips, zero-mass rows, partial-zero renormalisation, model-side
join coverage). Coverage guard
`braunschweig.verbindungen.anchor_min_observed_commuters` = 30, measured on
the 2019 QZM ZGB coverage distribution (holdout run 2026-07-17): 3x the
censoring bound; keeps 205/239 rows (85.8%) and 98.2% of the anchorable
observed mass (row-mass p10=18.8, p25=73, p50=277, n=239).

With the anchor ON, the VerBindungen validation (check B) is a FIT metric --
the validation stage and the cache runner stamp `reference_role=fit`
(`--reference-role` is REQUIRED on the cache runner; a silent `independent`
against an anchor-ON cache would overstate validity). Independent validation
moves to the MiD distance axes.

Pre-registered decision rule v2 (amended 2026-07-17 BEFORE any measurement
run; v1's held-out-CV criterion was proven structurally inert for this
in-sample anchor -- the anchor never touches held-out flows, see
`test_heldout_cv_is_inert_by_construction`): the default flips to ON only if
(i') the zone-level AO-margin share srmse improves beyond its measured fold
noise (the anchor never fits destination margins -- corroboration on a
non-fitted axis) AND (ii) no P13-by-RS7 EMD regresses beyond its class's
measured fold noise. P38.2 per-Kreis vs the MiD reference (via the tested
`p38_2_band_target` loader) is directional evidence only; the held-out CV is
retained purely as a harness-leak detector (equality is the designed
expectation).

MEASURED VERDICT (2026-07-17, 100pct cache, seeds 20260716 + 42, identical
gates -> seed-stable): `default_flip_supported = False`. Fit axis (LABELLED
FIT) improves 0.114 -> 0.081; P13 EMD improves in 5/6 RS7 classes and P38.2
in 6/9 regions (03ZGB 0.229 -> 0.225), BUT (i') fails (AO srmse 0.1300 ->
0.1316, a slight worsening beyond fold noise ~0.003) and (ii) flags class 72
(0.1724 -> 0.1760 > noise). The default therefore STAYS OFF
(measured-and-parked). Artefacts: `~/wt/verbindungen-anchor/holdout_out_seed*/`
on the run server (verdict.md, coverage, censored-bound, intra-Kreis, P38
tables). Spec/plan: docs/superpowers/{specs,plans}/2026-07-16-verbindungen-
calibration-anchor-*.md (local); issue #193. Entry points:
`braunschweig/gravity/verbindungen_anchor.py` (anchor + diagnostics),
`braunschweig/calibration/anchor_holdout.py` + `scripts/run_anchor_holdout.py`
(CV/verdict); tests in `tests/test_verbindungen_anchor.py`.
