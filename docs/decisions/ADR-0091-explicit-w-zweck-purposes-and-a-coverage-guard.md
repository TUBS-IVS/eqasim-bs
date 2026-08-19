# ADR-0091 · 2026-08-18 · Every documented W_ZWECK code is mapped explicitly, and an unknown one is reported (issue #241)

- **Status:** active
- **Context:** `braunschweig.popsim.trips.map_purpose` mapped W_ZWECK 1-12 and sent everything
  else to `"other"` in one expression:
  `out[zweck_col].map(PURPOSE_BY_W_ZWECK).fillna(DEFAULT_PURPOSE)`. No counter, no log line.
  Re-verified on the raw MiD 2023 Wege file (1,087,393 legs, `W_GEW`-weighted, 2026-08-18):
  **25,688 legs = 3.03 % of all donor legs arrived at their purpose through that fallback** —
  code 13 (1.38 %), 14 (0.45 %), 15 (0.50 %), 16 (0.11 %), 99 (0.59 %). The issue's Impact
  section estimated 1.6 % for the leisure part; the measured figure is **1.06 %**.
  This is not a cosmetic mapping question. The purpose selects the candidate destinations, the
  distance distribution and the secondary-location scoring, so ~1 % of legs were being sent to
  the wrong class of destination — and the silence is the actual defect: MiD 2023 introduced
  these codes and the model absorbed them without a word, so the next edition's new code would
  do the same.
- **Evidence base changed: the labels are now READ, not inferred.** The MiD codebook
  (`MiD2023_Codeplaene_B1_Standard_v1.1.xlsx`, sheet `Wege`) was not in the repo when #241 was
  written, so the issue inferred the semantics from MiD's own derived variables (`zweck`,
  `hwzweck1`). The codebook is now synced (local-only, see the `mid2023_b1` Data Registry
  entry) and states: **13 Begleitung Erwachsener · 14 Sport/Sportverein · 15 Freunde
  besuchen/treffen · 16 Unterricht (nicht Schule) · 99 keine Angabe**. The escort classification
  of code 13 that #201/#256 had inferred is thereby confirmed.
- **Decision:**
  1. **Map every documented code explicitly** in `PURPOSE_BY_W_ZWECK`, with the codebook label
     in a comment beside each: 13 → `other` (→ `escort` under the #201 flag, which owns that
     override), 14/15/16 → `leisure`, 99 → `other`. 13 and 99 produce the same value the
     fallback produced, so that half changes nothing — the point is that it is now stated.
  2. **Add a coverage guard.** An unmapped code still falls back to `"other"` (dropping the leg
     would be worse) but is now COUNTED and NAMED in a `WARNING` with its `W_GEW`-weighted
     share. Every code the codebook documents is mapped, so the warning can only fire on a
     genuinely new code.
  3. **Flag-gate the part that changes the population.** Only 14/15/16 → `leisure` moves legs
     (1.06 %); `explicit_round_trip_purposes` (default `true`) switches them back to `"other"`
     for the A/B. With the flag off they are remapped EXPLICITLY rather than un-mapped, so the
     coverage guard stays meaningful in both modes.
  4. **Code 16 maps to `leisure` despite an educational label, and the reason is what
     `education` MEANS in eqasim:** the person's ASSIGNED educational facility, which the
     primary-location machinery anchors (school / Kita). An evening class, driving lesson or
     music lesson is not that facility, so mapping it to `education` would place the activity at
     the wrong location. MiD's own `zweck` derivation makes the same choice (16 → 7 Freizeit).
- **A latent apples-to-apples defect this fixes, and one that remains:**
  `braunschweig.popsim.mid.participation.PARTICIPATION_W_ZWECK` is DERIVED from
  `PURPOSE_BY_W_ZWECK`, so the `leisure_participation` control's universe widens from `{7}` to
  `{7, 14, 15, 16}` as a consequence. That is a repair, not a side effect. The control's target
  is built from SrV `E_ZWECK_9`, whose leisure is ONE coarse bucket (code 7) fed by the fine
  purposes 13-18 (Kultur, Gaststätte, Privater Besuch, Erholung/Sport, Sportstätte, Andere
  Freizeit) — verified by cross-tabulating `V_ZWECK` against `E_ZWECK_9` on the SrV microdata.
  While MiD's 14/15/16 sat in `"other"`, the synthetic side of a HARD control counted a
  narrower leisure than its own target: the #96 / #169 error class, undetected because both
  sides were labelled "leisure".
  One construct difference remains and is accepted deliberately: MiD 16 corresponds to SrV
  `V_ZWECK 7` "Andere Bildungseinrichtung", which SrV rolls into `E_ZWECK_9 = 4` (education)
  while we map it to leisure. It is 0.11 % of MiD legs, and the alternative — mapping 16 to
  `education` to match the SrV roll-up — would anchor evening classes at the person's assigned
  school and make the seed disagree with the realised attribute. Recorded rather than removed.
- **Rejected alternatives:**
  - *Leave the fallback and only add the guard.* Would make the 1.06 % visible while still
    filing sport and visits as `"other"`, and would leave the participation-control mismatch in
    place.
  - *Map 14/15/16 to `leisure` without a flag.* Changes the population, so it must be
    separable for an A/B; the repository convention is a default-on flag with a documented OFF
    path.
  - *Raise on an unknown code instead of warning.* A new MiD edition would then stop the
    pipeline mid-run. The purpose is to be seen, not to block; the fallback rate belongs in the
    run's validation summary.
  - *Map 16 to `education`* — see decision 4.
- **Consequences:** the population changes with the flag on (1.06 % of legs move `other` →
  `leisure`, and the `leisure_participation` control's universe widens with it), so this needs
  its own A/B and a re-measured trip-purpose mix before anything is claimed about the fit. Not
  yet run. The A/B must vary this flag alone: ADR-0088 and ADR-0089 change the same population
  in the same release.
