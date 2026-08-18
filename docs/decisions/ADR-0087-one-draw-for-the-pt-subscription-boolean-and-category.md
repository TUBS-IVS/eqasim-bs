# ADR-0087 · 2026-08-18 · The PT-subscription boolean is derived from the resolved category, not drawn a second time (issue #319)

- **Status:** active
- **Context:** Under `popsim_mid` both PT-subscription attributes come from the donor's MiD
  answer `P_FKARTE`: `braunschweig.popsim.attributes.map_has_pt_subscription` produced the
  boolean `has_pt_subscription` and `map_pt_subscription_type` produced the categorical
  `pt_subscription_type`. Each declared the same missing policy — structural `402` (Kind unter
  14) deterministic, and `99` / `202` / `206` treated as item non-response — and each called
  `braunschweig.popsim.missing.resolve` SEPARATELY, with its own draw from the shared rng.
  `braunschweig.popsim.assembly` called them ~120 lines apart, so the two draws were also
  separated by every other attribute mapper's rng consumption.
- **The defect:** for every person carrying an imputed code the two attributes were drawn
  INDEPENDENTLY and could contradict each other. Measured on the 100 % population of run
  `synth-100pct-2.2.0-2026-07-23` (issue #307, manifest
  `docs/runs/i307-license-pt-measure-2026-08-18.yml`): **9,723 of 1,130,141 persons (0.86 %)
  disagreed, in both directions** — 4,782 held a flatrate category while the boolean said
  `False` (deutschlandticket 2,875 · jobticket_semesterticket 815 · monat_abo_jahreskarte 731 ·
  wochen_monat_ohne_abo 361), and 4,941 held a non-flatrate category while the boolean said
  `True` (fahre_nie 2,204 · einzelfahrschein 2,119 · anderes 328 · mehrfachkarte 290). The
  near-symmetry is the signature of two independent draws rather than a wrong mapping.
  It matters because the two representations feed different consumers:
  `org.eqasim.braunschweig.mode_choice.BraunschweigPtCostModel.calculateCost_MU` reads the
  BOOLEAN (holders pay zero fare on every PT trip) while the MiD P24.1 control fit and every
  #307-style measurement read the CATEGORY. The simulated and the validated population were
  therefore not the same people.
- **Decision:**
  1. **One draw, one owner.** `map_pt_subscription_type` is the sole resolver of `P_FKARTE`.
     `map_has_pt_subscription` becomes a pure, rng-free derivation:
     `pt_subscription_type in PT_TICKET_FLATRATE`. `PT_TICKET_FLATRATE`
     (`braunschweig.data.mid.reference_tables`) stays the single owner of "which ticket grants
     unlimited local rides", so the boolean cannot drift from the categorical semantics.
  2. **Absent category fails loudly.** If `pt_subscription_type` is missing,
     `map_has_pt_subscription` raises `KeyError` naming the fix instead of resolving `P_FKARTE`
     itself. A convenience fallback here would silently reintroduce the second draw — exactly
     the defect — which is the CLAUDE.md "no silent fallbacks" case.
  3. **`assembly` resolves the category first.** The category mapping moves to the position the
     boolean mapping had; the later separate call is removed. Nothing between the two former
     call sites reads either attribute (verified), so the reordering is behaviour-neutral apart
     from the rng-stream effect below.
  4. **The rng-stream shift is accepted and must be measured, not assumed.** Removing one draw
     and moving another changes every subsequent draw in the stream, so the synthetic population
     is NOT byte-identical — not even for persons whose two attributes already agreed. The
     deterministic part of the change is bounded by the measured disagreement set: 4,782 persons
     gain `has_pt_subscription = True` and 4,941 lose it, a net −159 persons (−0.014 pp) on the
     regional flatrate share. The reshuffle part is unquantified until the next full run and is
     recorded as owed evidence on #319, NOT claimed to be negligible.
  5. **The duplicated code set is removed.** `braunschweig.popsim.weekend_plan_match` held its
     own `PT_SUBSCRIPTION_CODES = frozenset({3, 4, 5, 6})` copy of
     `attributes.PT_SUBSCRIPTION_FKARTE`; it now imports the original.
- **Rejected alternatives:**
  - *Make the boolean the owner and derive the category from it.* Impossible without inventing
    information: the boolean is a 2-way collapse of a 9-way answer, so the flatrate categories
    could not be recovered.
  - *Keep both resolvers and make `map_pt_subscription_type` a no-op when the column exists.*
    Would make the order-independence implicit and hide a double resolution as a silent
    idempotence — the same class of invisible behaviour the defect came from.
  - *Keep two draws and reconcile afterwards* (e.g. overwrite the boolean at the end of
    assembly). Leaves the inconsistent intermediate state reachable by any new consumer and
    spends a draw to then discard it.
  - *Share one rng draw between the two resolvers.* Couples two public functions through draw
    order — fragile against any future reordering, and still two code paths for one fact.
- **Consequences:** the invariant `has_pt_subscription == (pt_subscription_type in
  PT_TICKET_FLATRATE)` now holds by construction and is asserted on a fixture that actually
  contains the codes 99 / 202 / 206 (a valid-codes-only fixture would pass vacuously — and a
  first version of that test DID pass vacuously, because both mappers defaulted to
  `RandomState(0)` and their independent draws coincided; the test now advances the rng first).
  `map_has_pt_subscription` loses its `rng` and `rs7_conditioning` parameters, a breaking
  signature change for the five in-repo callers, all updated. The realised flatrate share must
  be re-measured on the next full synthesis with
  `scripts/measure_license_pt_shares.py` before any PT control (#321) is designed on top of it,
  since a control must steer a well-defined quantity.
