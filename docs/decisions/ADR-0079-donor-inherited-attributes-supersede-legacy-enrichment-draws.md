# ADR-0079 · 2026-08-17 · Donor-inherited attributes supersede the legacy enrichment draws under popsim_mid
- **Status:** active
- **Context:** Issue #255 found seven `[Attrs]` features implemented entirely inside the legacy
  `synthesis.population.enriched` stage (`braunschweig/synthesis/population/enriched/`, plus
  `braunschweig/ipf/attributed.py`). Under the production method `popsim_mid` that stage name is
  aliased to `braunschweig.popsim.enriched_adapter`, so the feature code never executes; the
  features are live only under the `simple_ipf_open` fixture. The issue asks, per feature, to
  either (a) port it into the `popsim_mid` path or (b) document that it is not part of production.
  The premise needing scrutiny is that non-execution implies a missing attribute: under
  `popsim_mid` every synthetic household is a copy of a real MiD household, and
  `braunschweig/popsim/attributes.py` maps that household's OBSERVED values into the eqasim
  schema, with several of them additionally raked to per-Kreis targets by
  `braunschweig/popsim/kreis_attribute_control.py`.
- **Decision:** Do NOT port any of the seven. The `popsim_mid` attribute path is the productive
  implementation; the legacy draws stay `simple_ipf_open`-only and are recorded as `not_used`
  under both PopulationSim pipelines in the Feature Registry. Per feature:
  1. **`status_from_hhtype`** (economic status, ADR-0011) — superseded by
     `attributes.map_economic_status` (donor `oek_status`, which the MiD itself derives from
     household income) plus the HARD KREIS control `economic_status` in
     `kreis_attribute_control.REGISTRY` against `target2026_economic_status_by_kreis.csv`.
     Observation plus regional raking replaces a Bayes draw from marginal tables.
  2. **`income_eur_from_distribution`** (household income €, ADR-0017) — superseded by
     `attributes.map_household_income_eur` (donor income class × INKAR per-Kreis factor) plus
     `income_spatial_tilt`, `income_kreis_control`, `placement_income` (ADR-0069) and the
     open-top Pareto tail, all active in `configs/base_bs.yml`.
  3. **`cars_income_aware`** (income-aware #cars, ADR-0017) — superseded by the donor's own
     `H_ANZAUTO` (`attributes.map_number_of_cars`): income and car count are jointly OBSERVED in
     the same real household, so the coupling a conditioned pmf approximates is present by
     construction, and `number_of_cars` is additionally a HARD KREIS control. Porting would be a
     regression.
  4. **`consistent_car_availability`** (ADR-0017) — deliberately NOT ported for a substantive
     reason, not merely because a donor equivalent exists. The legacy semantics are
     PERSON-level: a person without a licence receives `car_availability = "none"` (asserted by
     `tests/test_car_availability_consistency.py`). In
     `org.eqasim.braunschweig.mode_choice.BraunschweigModeAvailability` the value `"none"` closes
     the entire car block INCLUDING `car_passenger`, while the same class already applies the
     licence separately as the inner gate that opens `car` on top of `car_passenger`. Porting the
     legacy rule would therefore filter the licence twice and suppress passenger trips for
     children and non-driving adults, whose real car contact is precisely as passengers.
     Independently, `BraunschweigPredictorUtils.hasCarAvailability` compares only against
     `"none"`, so the `some`/`all` boundary — and hence a P19 rake on the "jederzeit" share — is
     simulation-neutral and would change only the validation figure.
  5. **`pt_subscription_conditioned`** (P24.1 3-margin IPF, ADR-0012) — the popsim path derives
     `pt_subscription_type` / `has_pt_subscription` from the donor's `P_FKARTE` with
     RegioStaR7/age-conditioned imputation of the coverage codes
     (`attributes.map_pt_subscription_type`), but does NOT rake to the P24.1 margins. Recorded as
     an OPEN question rather than a port: the subscription enters
     `BraunschweigPtCostModel.calculateCost_MU`, where holders pay zero fare on every PT trip, so
     it acts continuously on every PT decision. The realised regional share must be MEASURED
     before any control is designed (see Consequences).
  6. **Driving licence** (P17.1 3-margin IPF, ADR-0013) — the popsim path uses
     `attributes.map_has_license` (donor `P_FSCHEIN`, coverage codes 202/404 imputed rather than
     forced to False). No per-Kreis control is adopted, from EITHER source: the committed MiD
     P17.1 ZGB table gives 86.9 % (ja/(ja+nein), 18+) while the committed SrV table gives 92.4 %
     (17+), a 5.5–9.3 pp contradiction whose cause is a between-survey measurement/selectivity
     difference (MiD self-reporters only and mode-dependent; SrV household-reported with no item
     non-response), not regional variation. KBA FE4 cannot arbitrate: it holds only licences
     issued or exchanged after 1999 (the mandatory exchange runs to 2033), giving Braunschweig
     128 168 holders in the "Fahrerlaubnisse bzw. Führerscheine" column against roughly 215 000
     persons aged 17+. The dominant predictor age × sex is already controlled at 100 m resolution
     (ADR-0016), so the residual regional degree of freedom is small.
  7. **`reactivate_person_attributes`** (couple/studies/SPC, ADR-0018) — two of the three
     attributes are produced under `popsim_mid` by a different mechanism:
     `attributes.map_studies` (from `P_TAET`) and `attributes.map_socioprofessional_class`, which
     calls the very same `braunschweig.ipf.attributed.derive_socioprofessional_class`. Only
     `couple` is absent, and it is not needed: it appears in no writer field
     (`matsim/scenario/population.PERSON_FIELDS`), in no schema list
     (`braunschweig/population/schema.py`) and in no eqasim-java consumer; the sole reference is a
     plausibility check in `braunschweig/popsim/plausibility.py`. The registry value stays
     `not_used` because the FLAG governs nothing under `popsim_mid`; the description carries the
     substitute mechanism so the value is not misread as "attribute missing".
- **Rationale:** For 1–3 and 7 the donor path uses observed joint values where the legacy path
  drew from marginals, and adds per-Kreis raking on top; for 4 porting is actively harmful given
  the Braunschweig mode-availability staffing; for 5 and 6 the mechanism question is separable
  from an unresolved reference question and must not be settled by porting code. `inactive` was
  also factually wrong per `braunschweig/documentation/schema.py`, which defines it as "wired into
  that pipeline but disabled there" — the feature code is not wired there at all.
- **Consequences:** The `[Attrs]` rows no longer imply production activity that does not exist,
  and the seven registry records name the mechanism that actually runs. Three items stay open and
  are NOT claimed as settled: (i) the realised licence and PT-subscription shares of the
  synthetic population are UNMEASURED, per Kreis and by age × sex; (ii) the PT subscription is
  the serious control candidate because of the fare channel, but a regional target requires
  extracting the full SrV `E_OEV_FK` category set — the committed SrV aggregate covers only the
  Deutschlandticket and over a different universe (all persons vs. MiD's 14+); (iii) a licence
  control is excluded by this ADR, so a licence misfit would be reported as a documented
  measurement-difference limitation, not calibrated away. `simple_ipf_open` keeps all seven; the
  legacy KBA FE4 loader `braunschweig/data/census/licenses.py` was verified to run against the
  committed `eqasim-data/data/germany/fe4_2024.xlsx` (all eight ZGB Kreise resolved), so that
  pipeline's `active` claims remain valid.
- **Evidence:** issue #255; `braunschweig/popsim/attributes.py`,
  `braunschweig/popsim/kreis_attribute_control.py`, `braunschweig/popsim/enriched_adapter.py`;
  `configs/base_bs.yml` (`braunschweig.population.method: popsim_mid`, the aliases block);
  `tests/test_car_availability_consistency.py`; eqasim-java-bs
  `BraunschweigModeAvailability` / `BraunschweigPredictorUtils` / `BraunschweigPtCostModel`;
  committed references `eqasim-data/data/braunschweig/mid/mid2023_P17_1.csv`,
  `eqasim-data/data/braunschweig/srv/srv2023_car_license_17plus_by_kreis.csv`,
  `eqasim-data/data/germany/fe4_2024.xlsx`; ADR-0011, ADR-0012, ADR-0013, ADR-0016, ADR-0017,
  ADR-0018, ADR-0069.
