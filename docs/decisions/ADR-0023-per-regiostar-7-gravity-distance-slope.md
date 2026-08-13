# ADR-0023 · 2026-06-01 · Per-RegioStaR-7 gravity distance slope
- **Status:** active
- **Context:** A single distance-decay slope `exp(slope·d)` decays urban and rural commutes at the
  same rate, which is unrealistic (urban origins have flatter slopes / longer commutes).
- **Decision:** Differentiate the slope by the origin Gemeinde's RegioStaR-7 class
  (`gravity_slope_by_regiostar7`), holding the flow-weighted mean equal to `gravity_slope=-0.065`
  so the regional mean commute is unchanged; only the sub-Kreis distribution is differentiated.
  Fill RS7-absent Gemeinden by geographic nearest neighbour.
- **Rationale:** A per-origin fit with destination FE is rank-deficient on the BA Pendleratlas
  data (distance collinear with per-destination dummies), so a single identified full-panel Poisson
  GLM pools within-origin distance variation; anchors chosen by an adaptive ring (CLAUDE.md
  "Gravity model").
- **Consequences:** Realistic sub-Kreis commute distribution; pinned values in run configs (re-run
  the script, do not hand-edit).
- **Evidence:** plan `docs/superpowers/plans/2026-06-01-per-regiostar7-gravity-slope.md`; spec
  `2026-06-01-per-regiostar7-gravity-slope-completion-design.md`; `tests/test_gravity_ring_calibration.py`;
  PROJECT_STATUS.md §2.4 (BA Pendleratlas Poisson GLM).

