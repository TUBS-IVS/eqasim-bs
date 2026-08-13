# ADR-0052 — Explicit value_map codes win over the generic MiD nonresponse set (issue #96 fix)

> ADR-0051 is reserved for the fleet-quality branch (`fix/fleet-age-joint-ipf`), not yet merged; this
> ADR takes 0052 to avoid a second number collision (cf. the ADR-0050 fleet/TAZ collision noted above).

- **Decision:** in `braunschweig/popsim/missing.resolve`, an attribute's explicitly enumerated
  `value_map` codes are removed from the generic item-nonresponse set before classification:
  `nonresponse_set = (NONRESPONSE_CODES - set(spec.value_map)) | set(spec.impute_codes)`. An explicit
  substantive code always beats the generic convention; a per-spec `impute_codes` entry still forces
  imputation.
- **Why:** MiD missing codes are field-width dependent (Handbuch Kap. 5.1: "index digit 9 prefixed to
  the field width" -> bare `9` = keine Angabe only for single-digit fields). The flat
  `NONRESPONSE_CODES = {9, 99, ...}` ignored width, so for two-digit `P_TAET` (1..17) the substantive
  code **9 = Schueler/in** (keine Angabe = 99) was classified as nonresponse and imputed. Because
  `resolve` classifies nonresponse before the `value_map` lookup, every pupil was imputed from the
  non-pupil valid pool of its `alter_gr1` band (14-17 dominated by Azubis P_TAET=8 -> True), inflating
  the written `employed` flag for 14-17yo to ~96% and the region rate ~7-9pp over Zensus (issue #96).
  The same latent collision affected `hheink_gr1=9` (4000-4600 EUR) and `H_ANZAUTO/H_ANZRAD=9`.
- **Scope:** OUTPUT attribute mappers only. The popsim Tier-3 employment control (`control_spec.py`)
  evaluates raw `P_TAET.isin([1,2,3,4,6,8])` and was already correct; the population-validation
  employment control read the broken `employed` column and inherited the inflation. So this was neither
  the controls nor the calibration — it was `attributes.map_employed`.
- **Consequence / follow-up:** may change scientific outputs (minor employment -> ~0, region rate
  -7-9pp closer to Zensus, income distribution slightly corrected; 20+ rate ~unchanged). A 100% re-run
  is required to regenerate corrected outputs (Phase-0 blocker for #99). A new minor-employment
  plausibility guard (`controls.check_minor_employment`, PR #102) watches the under-15 employed rate,
  default WARN; flip to `raise=True` after the re-run measures the true post-fix rate
  (`feedback-measure-before-calibrating`; the 0.5% bound is an ASSUMPTION).
- **Evidence:** PR **#101** (merged `8f652c4`); canonical pytest on felix 320 passed; TDD tests in
  `tests/test_popsim_missing.py` + `test_popsim_attributes_missing.py`; real pre-fix 100% output
  measured at age<=14 employed = 19.84%. Distinct from **#25** (stale erwerb test, fixed independently
  by `d6556b6`+`aaafc60`). Memory `project-employed-code9-fix`, `feedback-no-silent-fallbacks`.

