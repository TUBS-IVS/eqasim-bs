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
