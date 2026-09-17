# ADR-0127 · 2026-09-17 · A passive escort child whose donor adult left the household anchors at a household surrogate's secondary location
- **Status:** active
- **Numbering:** This record was designed, implemented and drafted under the working id
  `ADR-0124` (spec and plan `docs/superpowers/specs/` and `docs/superpowers/plans/`,
  `2026-09-16-passive-escort-surrogate-anchor*`; the forward references in
  `braunschweig/synthesis/locations/passive_joint_links.py`,
  `braunschweig/synthesis/locations/secondary_chainsolvers/__init__.py`,
  `configs/base_bs.yml`, `scripts/measure_passive_joint_surrogate_adults.py`,
  `scripts/measure_passive_joint_surrogate_effect.py` and the run manifest all said
  `ADR-0124`, mirroring the same pattern ADR-0119's own Numbering paragraph records).
  RE-VERIFIED across ALL refs on 2026-09-17 (`git fetch origin`, then
  `git ls-tree -r --name-only <ref> -- docs/decisions` grepped for `ADR-[0-9]{4}` on every
  `refs/remotes/origin` and `refs/heads` ref): **`ADR-0124` is ALREADY TAKEN** by
  `docs/decisions/ADR-0124-srv-per-kreis-row-set-from-expected-geography.md` on
  `origin/main`, as is `ADR-0125`
  (`ADR-0125-gemeinde-bev-phev-composition-from-fz2717-level-from-the-2026-share.md`,
  merged as PR #411), and `ADR-0126` (`ADR-0126-resource-adaptive-configuration.md`) is
  taken on the unmerged branch `feature/resource-adaptive-config`. This record therefore
  uses **ADR-0127**, the next free id; the stale forward references were renumbered to
  `ADR-0127` in the same commit that adds this file, so no file points at `ADR-0124` for
  this record any more. Ids are append-only.
- **Context:** ADR-0119 anchors a PAIRED passive escort child's joint activity at the accompanying
  adult's placed secondary location, linked by PLAN-SOURCE IDENTITY inside the synthetic household.
  In the production configuration only 12 of 58 paired passive legs link (20.7 %, run manifest
  `i385-passive-joint-location-production-smoke-03101-2026-09-15`). Of the 46 unlinked legs, 27 fail
  because the per-person diary plan match remapped the child onto a foreign donor household's diary,
  so the paired adult does not exist in the synthetic household (`adult_not_in_household`); the
  remap rate is highest in the child bands this feature needs (issue #409's own analysis). Issue
  #409 lists three options to raise the ceiling; the user decided for option 1 after the headroom
  measurement below.

  Headroom measured by `scripts/measure_passive_joint_surrogate_adults.py` on the production smoke
  cache (46 unlinked legs, Kreis 03101, 1 %): by the child's OWN purpose education 19 / home 10 /
  other 13 / shop 4, so 17 legs are eligible at all, and 16 of those 17 have a household surrogate
  adult somewhere in the day (65 candidate activities). The time fit is the binding constraint: a
  household adult (>= 18) with a secondary activity within 15 / 30 min reaches 6 / 9 of the 46 legs;
  restricted to the SAME purpose as the child (the strict D2 rule shipped as
  `escort_passive_joint_surrogate_require_same_purpose`), 1 / 4; restricted to an adult who ALSO
  escorts somebody, 0 / 0. Those reachable legs by the child's own
  purpose are other 5 / shop 1 at 15 min and other 5 / shop 4 at 30 min. The issue's own table
  (13 / 20 and 10 / 14, median gap 4.92 min) is reproduced exactly by the script's reconciliation
  mode: it counts children whose own activity is education or home (which cannot change location)
  and, in its second row, pairs on the adult's ESCORT trip, which cannot carry an anchor (issue #201
  pins it to a school).
- **Decision:** A flag, `escort_passive_joint_surrogate`, extends the ADR-0119 link table with a
  RESCUE for exactly the `adult_not_in_household` legs (`passive_joint_links.rescue_with_surrogates`,
  composed with the identity links by `secondary_chainsolvers._passive_joint_link_table`).
  1. **Rescue set (D1).** Only legs whose identity link failed with `adult_not_in_household` and
     whose child activity is secondary (shop / leisure / other). `adult_leg_missing` legs keep a KNOWN
     paired adult a surrogate would override; `purpose_not_secondary` legs cannot be anchored on any
     adult; education / home children cannot change location.
  2. **Surrogate.** Another member of the SAME synthetic household, `HP_ALTER >=
     escort_passive_joint_surrogate_min_age_years` (default 14), who is not a linked child (identity
     or rescue set -- a surrogate must be solved in pass 1, a linked child in pass 2: the ADR-0119
     acyclic guard generalised), with a secondary activity that is NOT itself a paired passive leg
     (a person taken along is not the one travelling to the place), departing within
     `escort_passive_joint_surrogate_max_gap_minutes` (default 15) of the child's own leg. With
     `escort_passive_joint_surrogate_require_same_purpose` (default true) the surrogate's purpose must
     EQUAL the child's (D2). Nearest gap wins; ties break on the lowest adult person id, then that
     adult's earliest activity. No random draw anywhere.
  3. **Composition.** The rescue returns `SURROGATE_LINK_COLUMNS` (`LINK_COLUMNS` plus
     `link_source = "surrogate"` and the realised `gap_minutes`); the composition drops
     `gap_minutes`, for which the two-pass wiring has no use, and tags the identity rows
     `link_source = "plan_source"`. `_compose_two_pass` and `resolve_joint_anchors` are untouched
     (they read columns by name). With the flag OFF the stage hands on the identity table itself --
     exactly `LINK_COLUMNS`, no column added -- so the OFF path is the pre-#409 behaviour. Verified
     FRAME-EQUAL to the pre-change cached stage output on the Kreis-03101 smoke (3,096 location rows
     and 2,093 convergence rows on both sides; run manifest below).
  4. **Requires `escort_passive_joint_location`**; `configure()` raises naming both keys otherwise,
     and rejects non-positive gap or age. Code default False, production ON only through
     `configs/base_bs.yml` (family convention ADR-0112 C-R9 / ADR-0119).
  5. **Transparency.** One log line under `[passive_joint_links]` and one printed stage line carry the
     rescue rate `n_surrogate_linked/n_rescue_candidates`, the per-reason split (child purpose not
     secondary, no admissible candidate activity, gap exceeded), the purpose-mismatch and cyclic row
     drops, and the COMBINED total `plan-source + surrogate / paired`; WARNING when candidate
     activities existed and nothing linked.
- **Rejected alternatives:**
  - **Issue #409 option 2 (coherence-biased diary redraw).** Changes the donor draw for more than
    half of all children (remap rates 57.1 % / 54.2 % in the 0-5 / 6-13 bands, issue #409's own
    measurement), systematically prefers donors with passive legs (biasing the escorted share, which
    #227 / #332 control per Kreis), and would STILL need a relaxed link rule because the foreign
    adult is not in the household -- option 1 plus a riskier draw, for an unmeasured gain.
  - **Issue #409 option 3 (conditional pair construction with time shift).** Overwrites observed
    departure times calibrated against SrV (ADR-0114) for a gain of a handful of activities.
  - **Rewriting the child's PURPOSE to the surrogate's.** Purposes are set in
    `braunschweig.popsim.trips_stage`, upstream of the chainsolver; a cross-stage rewrite would move
    purpose shares calibrated under #373 for a location feature.
  - **The adult's ESCORT activity as the anchor.** Issue #201 anchors it at the escorted child's
    EDUCATION location; a shopping child would be placed at a school. Measured: no surrogate carrying
    a usable secondary anchor also escorts within 30 min (0 / 0).
  - **Re-pointing the ADULT's escort anchor to the child's location** (the inverse direction): a
    change to #201, out of scope; a separate issue if wanted. Measured
    (`summarise_inverse_anchor_headroom`, header of
    `scripts/measure_passive_joint_surrogate_adults.py`): of the 17 eligible legs, none pairs in
    time with its household adult's ESCORT trip, at either gap -- 0 / 46 unlinked legs (0.0 %) and
    0 / 17 eligible legs (0.0 %) at 15 min, the same 0 / 46 (0.0 %) / 0 / 17 (0.0 %) at 30 min.
  - **Lowering the donor-side pairing floor** (`escort_pairing.DEFAULT_ADULT_MIN_AGE = 18`): a change
    to ADR-0112 worth 15 of 10,905 raw legs at a floor of 14 (the histogram's floor-14 row); separate
    follow-up.
- **Consequences:**
  - The locations of the rescued child activities change (and their chain-neighbouring leg distances
    with them, as under ADR-0119). In the Kreis-03101 smoke: 1 of 27 rescue candidates rescued in the
    strict arm (3.7 %, combined 13/58 = 22.4 %) and 6 of 27 in the relaxed arm (22.2 %, combined
    18/58 = 31.0 %), against 12/58 = 20.7 % with the rescue off; anchors resolved 13/13 and 18/18
    (100 %, 0 unresolved, as in the OFF arm's 12/12). The realised distances per rescued activity are
    in the run manifest and are not restated here.
  - Runtime unchanged in kind (the same two passes; the rescue is a pandas join). RNG stream
    unchanged: the rescue draws nothing. Enabling the flag does, however, move more children
    into pass 2 and therefore re-partitions the two solver passes, so the unlinked persons'
    Monte-Carlo realisation shifts (the smoke's per-arm problem counts differ: see the run
    manifest) -- the ADR-0119 limitation applies here too (both passes share ONE
    `RandomState`, so an ON/OFF or arm comparison is a different Monte-Carlo realisation, not
    attributable to the anchoring mechanism alone; ADR-0119 Consequences).
  - Departure times and escort participation are untouched by construction (locations only).
  - **Smoke, not validation.** No observed reference exists for joint locations; the numbers are the
    mechanism's own rates on a 1 %-sampled single Kreis (27 rescue candidates; the 17 eligible legs
    of the headroom measurement sit in only 9 households).
  - The strict rule leaves almost nothing in the smoke: 1 rescued leg, and for that child the
    independent draw had already landed on the surrogate's facility, so nothing moved (child-surrogate
    and home distances identical to the OFF arm). The relaxed rule rescues 6, five of them the
    catch-all `other` purpose on the child's side, and by construction admits a surrogate whose
    activity purpose differs -- the relaxed arm dropped 0 purpose-mismatch candidate rows against 55
    in the strict arm -- so such a rescued child is placed at a facility chosen for the SURROGATE's
    purpose.
  - **Production setting: the RELAXED rule (`escort_passive_joint_surrogate_require_same_purpose:
    false` in `configs/base_bs.yml`), user decision 2026-09-17.** The reason is the semantics of the
    mechanism, not the larger count: a passively escorted child does not choose its destination --
    it travels where the accompanying adult travels (the same-destination reading carried over from
    ADR-0112 / ADR-0119, assumption 3 below). Requiring the surrogate's purpose to equal the
    CHILD's therefore imposes an agreement the mechanism never asserts, and it is near-inert where
    it was measured: the strict arm dropped 55 candidate rows and rescued one leg whose location did
    not move. Five of the six legs the relaxed rule rescues carry the catch-all `other` purpose on
    the child's side, where purpose equality is least meaningful in the first place.
    **The price, stated plainly:** a rescued child is placed at a facility chosen for the
    SURROGATE's purpose, so the child's own activity purpose and the facility type of its location
    can differ -- a purpose-specific destination statistic over the output will contain such rows.
    The code default stays `true` (the conservative value for anyone running without
    `configs/base_bs.yml`); only the production config carries the relaxed setting, per the family
    convention (ADR-0112 C-R9 / ADR-0119).
    **What this decision does NOT rest on:** the realised distances (the relaxed arm's mean home
    distance of 11.6 km against 40.7 km OFF for the five `other` legs) are DESCRIPTIVE. No observed
    reference for joint locations exists, so "more plausible distances" is an argument about
    plausibility, not a validated improvement -- see "Smoke, not validation" above.
- **Assumptions (explicit):**
  1. **Time proximity alone carries the substitution.** The measured median gap of the reachable
     legs is 10.56 min, not the 4.92 min of the issue's escort-trip row (escort trips cannot carry an
     anchor). The corroboration "both diaries attest travel at the same minute" is NOT available for
     these legs.
  2. **Age floor 14** for a surrogate: the user's judgment, bounded by the MiD probe
     (`scripts/measure_passive_joint_surrogate_adults.py --mid-dir`): of 562 minors' passive legs the
     production floor 18 leaves unpaired, a floor of 16 / 14 / 12 / 10 / 6 newly pairs 6 / 15 / 19 /
     27 / 56, and below about 12 the partners are 7- and 9-year-olds, i.e. co-travelling children.
     In the smoke no eligible household has a member aged 10-17, so the floor is inert there.
  3. **Same-destination reading** of a surrogate pair, carried over from ADR-0112 / ADR-0119.
- **Evidence:** run manifest `docs/runs/i409-passive-joint-surrogate-smoke-03101-2026-09-17.yml`
  (three arms -- off / strict / relaxed -- over ONE cached upstream population, Kreis 03101 at a 1 %
  sampling rate, host felix, 2026-09-17). HOW IT WAS RUN, in the manifest's own words: the runs
  "executed commit 0700430a -- the commit that produced cache_i385_smoke -- with the branch diff
  ... applied on top as a clean patch", because at the merged HEAD "19 stages devalidate -- the whole
  population chain ... because five config keys were added to main in the 101 commits between
  0700430a and the branch's merge-base, and the current stage code requests all five", so a HEAD
  smoke "means recomputing the whole population". "CONSEQUENCE, STATED PLAINLY: the merged HEAD is
  NOT smoke-tested on the server. Its unit suite passes (154 tests green on the server at HEAD,
  incl. the five passive-joint/measurement files), and the pipeline-behaviour evidence below is from
  the patched 0700430a tree." Headroom and MiD-probe figures: the header of
  `scripts/measure_passive_joint_surrogate_adults.py` (measured at commit `3efd8875`, re-measured on
  2026-09-17 and reproduced exactly per the manifest); realised-effect figures: the header of
  `scripts/measure_passive_joint_surrogate_effect.py`. Tests `tests/test_passive_joint_links.py`,
  `tests/test_passive_joint_two_pass.py`, `tests/test_configs_composed.py`,
  `tests/test_measure_passive_joint_surrogate_adults.py`,
  `tests/test_measure_passive_joint_surrogate_effect.py`. Issue **#409** (option 1), building on
  issue #385 / ADR-0119. Feature record
  `docs/registry/features/escort_passive_joint_surrogate.yml`; feature doc
  `docs/features/escort-purpose.md`, section "Surrogate anchor (ADR-0127)".
