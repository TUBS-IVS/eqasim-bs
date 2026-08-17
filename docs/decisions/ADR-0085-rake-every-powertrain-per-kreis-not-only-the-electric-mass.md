# ADR-0085 · 2026-08-17 · Rake every powertrain per Kreis, not only the electric mass (issue #277)

- **Status:** active
- **Context:** The v2 powertrain chain works in two stages. `PowertrainModel`
  biproportionally rakes `P(powertrain | segment)` onto the per-Kreis KBA marginal
  (Destatis 46251-02 since ADR-0081), so the per-car pmf matches the reference
  before any masking. The draw then (a) masks each car's pmf to the powertrains its
  model is actually registered with (feasible-fuels mask) and (b) weights the
  surviving mass by the per-model fuel mix (`kba_model_fuel.csv`). Both distort the
  distribution, and "Task 7" corrected only the **electric** mass afterwards,
  targeting the mean unmasked pmf. ADR-0082 recorded the rest as an accepted quirk:
  "after the model-fuel weighting only the ELECTRIC mass is re-raked per Kreis, so
  the per-Kreis combustion split may drift from 46251-02 (spot-check in the run
  summary)". Nobody had ever measured that drift, because `kba_model_fuel.csv` did
  not exist until issue #277 generated the derived tables — with the table absent
  the weights were all-ones and the quirk was invisible.
- **Measurement (the reason this record exists):** with the table present, the
  realised petrol share of combustion cars in the ZGB was **0.772 against a
  reference of 0.670 — a drift of +10.2 pp**, i.e. the diesel share of the
  combustion fleet pushed from 33 % down to 23 %. Attribution by toggling each
  feature (`scripts/measure_combustion_split.py`):

  | configuration | petrol share | deviation |
  |---|---|---|
  | reference (46251-02, ZGB aggregate) | 0.6700 | — |
  | mask + model-fuel weights (production, electric-only rake) | 0.7717 | **+10.2 pp** |
  | mask only, no model-fuel weights | 0.6776 | +0.8 pp |
  | model-fuel weights only, no mask | 0.6666 | −0.3 pp |
  | neither | 0.6666 | −0.3 pp |

  The drift needs BOTH features: the weights are applied only inside the feasible
  set, so without the mask they cancel under renormalisation. Nothing in the model
  could see it — the realised-margin validator compares against the EFFECTIVE
  (post-mask, post-weight) targets by design (ADR-0082 finding 2, to stop the
  segment dimension crying wolf), so a deviation from the raw KBA marginal is
  invisible to it by construction.
- **Decision:** the per-Kreis post-mask rake targets **every** powertrain, not just
  bev/phev. `_electric_rake_factors` becomes `_powertrain_rake_factors` and is
  called with the full powertrain index; the target stays the **mean unmasked
  (tilt-carrying) pmf** of the Kreis, which is exactly the vector `PowertrainModel`
  already raked onto the KBA marginal. Consequences of that choice:
  * the reference distribution is restored (**−0.3 pp** after the change),
  * the Gemeinde / grid / EV-income tilts are preserved, because they live in the
    unmasked pmf that IS the target,
  * the per-model fuel weights keep their intended role — a preference for the
    powertrains a model is actually registered with, WITHIN the feasible set — but
    can no longer move the Kreis aggregate,
  * feasibility is still preserved by construction: a multiplicative factor cannot
    resurrect mass a car does not have (`pmf_i[e] == 0` stays 0).
  The existing per-powertrain unreachable-target WARNINGs (under- and over-shoot,
  with the residual and the Kreis) now cover all powertrains instead of two.
- **Rationale:** a fleet whose petrol/diesel split is 10 pp off its own committed
  reference cannot support an emissions statement, and "spot-check it in the run
  summary" is not a control — under the project's fallback rule a deviation that
  cannot be scientifically defensible is corrected, not logged. Raking all
  powertrains is not a new mechanism: it is the mechanism already trusted for the
  electric mass, applied to the axis it was arbitrarily excluded from. The target
  choice matters as much as the raking: taking the mean UNMASKED pmf (rather than
  re-reading the KBA table) keeps ADR-0082's finding-3 property that a tilt must
  not be undone by the rake.
- **Consequences:** every `consistency_v2` fleet changes — the combustion split
  moves by ~10 pp toward the reference, and with it `hbefa_tech`,
  `hbefa_emission`, `type_id` and the age/euro draws conditioned on the powertrain.
  Any cached `braunschweig.synthesis.vehicles.cars.household` stage is devalidated
  and previously produced fleets are not comparable. The legacy
  (`consistency_v2=False`) path is untouched: it has neither mask nor weights.
  Runtime is unchanged in practice (the fixed-point iteration now updates 8 columns
  instead of 2, on the same per-Kreis matrices).
- **Evidence:** `scripts/measure_combustion_split.py` (committed) reproduces the
  table above from the committed `kba_kreis_fuel.csv`; after the change the
  production configuration reports −0.3 pp (12 000 cars) and −0.5 pp (8 000 cars),
  within sampling noise of the reference.
  `tests/test_fleet_sampling_de.py::test_electric_rake_warns_on_overshoot` and
  `::test_electric_rake_warns_on_undershoot` still pin the unreachable-target
  WARNINGs (now via `_powertrain_rake_factors`);
  `tests/test_fleet_ev_income_tilt.py` still pins the EV-income aggregate
  preservation, which is what proves the tilts survive the wider rake.
- **Alternatives rejected:** (a) Leave the quirk and widen the test tolerance —
  keeps a 10 pp bias in an emissions-relevant attribute and would have to be
  restated as a limitation in every result. (b) Drop the per-model fuel weights
  entirely (the drift disappears: +0.8 pp) — throws away real information about
  which powertrains a model series is actually registered with, which is precisely
  what makes a Golf petrol/diesel and a Model Y a BEV. (c) Re-read the KBA marginal
  as the rake target instead of the mean unmasked pmf — that would silently undo
  the Gemeinde, grid and EV-income tilts, reintroducing the defect ADR-0082
  finding 3 fixed. (d) Add the drift as a flagged validator dimension without
  correcting it — reporting a known, correctable bias instead of correcting it.
- **Issue / PR:** #277
