# ADR-0119 · 2026-09-11 · Passive escort joint activities anchor at the accompanying adult's secondary location
- **Status:** active
- **Numbering:** This record was designed and implemented under the working id `ADR-0118`
  (`docs/superpowers/specs/2026-09-11-passive-escort-joint-location-design.md`; the code's forward
  references in `braunschweig/synthesis/locations/passive_joint_links.py`,
  `braunschweig/synthesis/locations/secondary_chainsolvers/__init__.py`,
  `braunschweig/synthesis/locations/secondary_chainsolvers/escort.py`,
  `synthesis/population/spatial/secondary/problems.py` and `configs/base_bs.yml` all said
  `ADR-0118`, mirroring the same pattern ADR-0112's own Numbering paragraph records). RE-VERIFIED
  across ALL branches on 2026-09-11 (`git fetch origin`, then `git ls-tree -r --name-only <ref> --
  docs/decisions` grepped for `ADR-[0-9]{4}` on every `refs/remotes/origin` and `refs/heads` ref):
  **`ADR-0118` is ALREADY TAKEN** by `docs/decisions/ADR-0118-donor-match-fine-child-age-bands.md`
  (issue #386, `feature/i386-donor-match-fine-child-age-bands`), already merged to `origin/main`.
  The highest id otherwise found is ADR-0117. This record therefore uses **ADR-0119**, the next
  free id. Those stale forward references were corrected to `ADR-0119` across `braunschweig/`,
  `synthesis/` and `configs/` by commit `fb59fd12` on this branch, so no code file points at
  `ADR-0118` for this record any more; the single `ADR-0118` string left in the code tree is
  `configs/base_bs.yml`'s own, legitimate reference to #386's record. Ids are append-only.
- **Context:** Phase 1 (#372 / ADR-0112) gives a PAIRED passive escort leg (MiD `W_ZWECK` 13, the
  escorted child's own leg, 100 % minors) the PURPOSE of the same-household adult leg it travels
  with -- the adult shops, so the child taken along gets `shop` too. The child's LOCATION was still
  chosen independently by the chainsolver: the adult could be placed at supermarket A and the child
  at supermarket B, although both travelled in one car. The purpose was right, the place was not.

  Issue #201 / ADR-0072 / ADR-0073 already solve the INVERSE problem for the ACTIVE side: an
  escorting adult's plan-level `escort` activity is anchored at the linked child's EDUCATION
  location, because a school is a PRIMARY location known before the chainsolver runs. The adult's
  shop/leisure/other destination is a SECONDARY location, chosen by the very stage that also places
  the child -- so anchoring the child on the adult requires the adult to be placed FIRST. Hence two
  solver passes: pass 1 solves everybody who is not a linked child (with their #201 escort anchors
  travelling as before), pass 2 solves the linked children with the joint anchors resolved from
  pass 1's output.
- **Decision:** A flag, `escort_passive_joint_location`, anchors a linked child's joint activity at
  the accompanying adult's PLACED secondary location.

  1. **Link table** (pure module `braunschweig/synthesis/locations/passive_joint_links.py`,
     `build_passive_joint_links`). For each PAIRED passive leg (`passive_pair_status == "paired"`,
     phase 1's own pairing), the paired adult is identified via PLAN-SOURCE identity
     (`source_H_ID` + the paired adult's `P_ID`) inside the SAME synthetic household -- not via the
     trips frame, because a member-completion filler, a diary-plan remap, or the day-absence model
     can separate the donor pair after synthesis (reason `adult_not_in_household`). The adult's
     paired leg is then found by its `W_ID` (reason `adult_leg_missing` when a spliced commute day
     or a dropped leg removed it). A link is kept only when BOTH the child's and the adult's
     activity purpose are in `SECONDARY_JOINT_PURPOSES = {shop, leisure, other}` (reason
     `purpose_not_secondary` otherwise: the adult's trip home, the adult's own Bringen/Holen leg
     already handled by the #201 link, and the adult's work/education leg are all excluded by
     construction). A defensive guard drops a person that would be linked as BOTH adult and child in
     the same run (`adult_is_linked_child`, expected 0 since passive legs are 100 % minors and
     paired adults are `>= adult_min_age`). Every exclusion reason and the link rate are logged as
     one line (CLAUDE.md fallback transparency).
  2. **Anchor resolution** (`resolve_joint_anchors`): after pass 1 places the adults, each link's
     adult activity is looked up in the pass-1 output (chainsolver rows AND fallback rows are both
     placements) to yield the child's anchor `(location_id, geometry)`. A link whose adult activity
     has no pass-1 row stays unresolved and the child keeps the independent draw in pass 2 (counted,
     logged; the resolution rate escalates to WARNING at or above
     `DEFAULT_UNRESOLVED_ANCHOR_WARNING_SHARE` = 50 % unresolved -- a `>=` comparison, the
     convention every sibling rate instrument of this stage uses).
  3. **Two-pass composition** (`secondary_chainsolvers._compose_two_pass`, built on
     `_solve_problem_set`, the solve body extracted verbatim from the pre-refactor `execute()` so
     the RNG call order and the early-return path are unchanged): pass 1 solves everybody who is not
     a linked child, carrying their own #201 escort anchors; pass 2 solves the linked children with
     the fixed boundary purpose `passive_linked` on BOTH sides of the anchored activity
     (`escort.rewrite_anchored_activities`, purpose-agnostic unlike the #201
     `rewrite_linked_escort_trips`, which only rewrites rows already carrying `escort`) plus their
     own #201 escort anchors. The anchored child rows carry the adult's `location_id` and geometry
     verbatim, so the child references a facility that provably exists (the same id the adult's own
     row references). `synthesis/population/spatial/secondary/problems.py` resolves the boundary via
     a new `ANCHORED_PURPOSES = ("escort_linked", "passive_linked")` tuple consulted through the
     existing `activity_anchors` mapping (`_anchor_coordinates`, unchanged fail-fast contract: a
     boundary purpose in `ANCHORED_PURPOSES` without an anchor entry raises, naming the person and
     activity). `passive_linked` never persists -- plans keep the real purpose (`shop` etc.),
     exactly like `escort_linked`.
  4. **Requires `escort_passive_from_adult`** (code default `False`, mirroring ADR-0112's own
     convention ruling C-R9): the link rule reads the phase-1 pairing columns, so
     `secondary_chainsolvers.configure` raises naming both keys when
     `escort_passive_joint_location` is on and `escort_passive_from_adult` (or, transitively,
     `escort_purpose`) is off. The production ON state is realised ONLY by the `configs/base_bs.yml`
     line this record's package adds; no overlay change (flags live only in the base).
  5. **OFF path is frame-equal (confirmed on the Kreis-03101 smoke).** With the flag off,
     `execute()` runs exactly one `_solve_problem_set` call over the whole population, the same
     solve body the two-pass path calls twice; the extraction that made this possible preserves
     the pre-refactor RNG call order and the early-return path verbatim, so the OFF path is
     INTENDED, by construction, to be frame-equal to the pre-refactor stage output. The empirical
     frame-equality result on the Kreis-03101 smoke (Task 5) CONFIRMS this: FRAME-EQUAL between
     the pre-refactor baseline (commit b5b94d6a) and the refactored stage with the flag OFF
     (commit ef75223a) -- 3,415 location rows each, `assert_frame_equal` passing on the locations
     frame (minus geometry), per-row geometry equality, and the convergence frame; both runs
     2,237 problems, success rate 1.0000, carla placed 2,236/2,237, fallback 1 (the single
     unbounded chain). Full detail in the run manifest below.
- **Rejected alternatives:**
  - **Post-hoc overwrite of the child's location after a single pass (rejected).** Moving the child
    to the adult's location AFTER both were independently placed would leave the child's
    NEIGHBOURING legs (the trip before and after the joint activity) built against the original,
    now-discarded distance -- breaking the distance consistency the chainsolver's chain-based
    placement exists to guarantee. Two passes with the anchor resolved BEFORE pass 2 solves keeps
    every leg's distance consistent with where the child actually ends up.
  - **A dedicated `escorted` activity type (rejected).** Renaming the child's activity would only
    relabel the question; it would not answer WHERE the escorted child's shop/leisure/other activity
    happens, which is the actual defect being fixed.
  - **Anchoring at the adult's PRIMARY location for adult `W_ZWECK` 1-3 (work, business or
    education pairs, deferred, not rejected outright).** This would need primary facility ids to
    be accepted by the facilities coverage check the way the #201 `escort_linked` anchors already
    are. ADR-0112's committed table measures only the `W_ZWECK` 1/2 (work/business) share of this
    group, at **4.2 %** of raw pairs; code 3 (education) sits inside a separate, not-broken-out
    `3, 9, 99 = 2.0 %` row, so a combined work-and-education share is NOT measurable from that
    table. The figure carried forward here is therefore the 4.2 % work/business share only (see
    this record's own Assumptions section 3). Deferred; these legs are excluded and counted under
    `purpose_not_secondary` (they map to `other` in phase 1's purpose rule regardless).
  - **Joint household solving inside chainsolvers (rejected).** Chainsolvers exposes no API to
    solve two related persons' chains as one coupled problem; the two-pass composition with a
    resolved anchor is the mechanism the existing `activity_anchors` machinery already supports.
- **Consequences:**
  - **The locations of the anchored child activities change**, and by construction so does the
    child's chain-neighbouring leg distance (it is no longer drawn from the child's own distance
    layer; it inherits the adult's placement).
  - **Runtime: two `cs.solve()` invocations per run** instead of one. The shared candidate set,
    distributions, deciders and the RDA fallback index are built exactly once
    (`_build_shared_solve_state`) and reused by both passes; pass 2 is expected to be small (only
    linked children). Wall-clock impact was measured on the Kreis-03101 smoke rather than
    assumed: pass 1 (2,208 problems, 2,207 persons, 5,408 plan rows) `cs.solve` 19.0 s; pass 2
    (6 problems, 6 persons, 12 plan rows, 20 linked children) `cs.solve` 19.1 s -- see the run
    manifest below. Pass 2 solved 6 problems in the same wall time as pass 1's 2,208, so on this
    smoke the second pass cost a full pass's FIXED overhead (worker-pool spin-up plus the fallback
    pool rebuild) rather than a cost proportional to its problem count -- it doubled the measured
    `cs.solve` time for 6 problems. That fixed per-pass cost, not the number of linked children, is
    what to watch at 100 % scale, where it has not been measured (its SHARE of a much longer pass 1
    is the open question).
  - **Both passes share ONE `RandomState`** (built once in `_build_shared_solve_state`, threaded
    through both `_solve_problem_set` calls). An ON/OFF comparison is therefore a DIFFERENT
    Monte-Carlo realisation, not attributable to the anchoring mechanism alone -- pass 1 draws from
    the same stream the single OFF-path solve would have started from, but pass 2's draws happen
    after pass 1's, on a different (smaller) problem set, so the two paths cannot be expected to
    place identical unlinked persons identically even where the anchoring itself does not apply.
    This is a limitation of the smoke comparison, not a defect: reproducing either path requires the
    same flag state, not just the same seed.
  - **The link and anchor-resolution rates are logged every run** (`[passive_joint_links]` marker):
    the link rate with its three-way exclusion split (adult not in the synthetic household / adult
    leg missing / purpose not secondary) and the anchor-resolution rate, both escalating to WARNING
    at pathological values (nothing linked; 50 % or more of the links unresolved) per CLAUDE.md
    fallback transparency.
  - **An unrelated, intended behaviour change surfaced during the `_solve_problem_set` extraction**:
    on the zero-bounded-legs early-return path, `execute()` now continues after the solve returns
    and still appends the #201 `linked_location_rows` and prints the success-rate line, which the
    pre-refactor inline code skipped by returning the stage output directly from inside that branch
    (silently dropping any escort-linked rows on that path). This path is unreachable on a real run
    (bounded legs always exist), so it changes no observed output; it is recorded here because it is
    a genuine, if inert, behaviour difference from before the refactor.
  - **Draw-rate logging moved after the solve.** `_log_subtype_draw_rates` and the SrV
    draw-rate/summary-CSV writer now run ONCE, after both passes complete, over the SUMMED subtype
    statistics and CONCATENATED desired-distance samples (`_sum_subtype_stats`,
    `_concat_desired_by_category`), instead of once per solve as in the pre-refactor single-pass
    code. They are reporting only (no placement, selection or RNG draw depends on them), so the only
    externally visible difference is ORDER: a solve that crashes now loses these log lines and the
    SrV summary CSV write, where the pre-refactor code would have already written them for a
    completed single pass before any downstream failure.
  - **A smoke, not a validation.** No observed reference exists for "child and adult end up at the
    same shop" -- this is a mechanism check (does the anchoring work, at what rate, at what cost),
    not a behavioural validation against real-world joint-activity data.
- **Assumptions (explicit):**
  1. **Same-destination reading of the pair.** A code-13 leg paired with a same-household adult leg
     within `escort_passive_pair_max_gap_minutes` is read as the SAME trip, ending at the SAME
     place -- carried forward from ADR-0112's assumption 1, supported there by 91.9 % same-minute
     departures and 88.4 % identical `wegkm_imp` among raw MiD pairs (ad-hoc measurement on the raw
     file, not reproduced by committed code); MiD carries no companion identifier that could prove
     it directly.
  2. **Plan-source identity of the pair.** `(source_H_ID, source_P_ID)` uniquely identifies the
     paired adult within one synthetic household, i.e. a synthetic household's members that share
     `source_H_ID` are copies of one MiD donor household (`expand.expand_to_persons` +
     member-completion semantics). The link rate this run measures is exactly the rate at which
     that identity survives synthesis (member completion, the diary-plan match, and the day-absence
     model can all break it). The Kreis-03101 smoke measured it: **36/74 paired passive legs
     linked (48.6 %)** -- n = 74 paired legs on a 1 %-sampled single Kreis, a smoke-scale count,
     not a population rate (see Evidence and the run manifest). That measurement predates the
     `origin/main` merge that brought issue #386 (fine child age bands applied to all three donor
     matchers, ADR-0118), which changes the diary-plan match named above as one of the breaks on
     this identity; the 48.6 % figure was not re-measured after that merge and should not be read
     as the rate the branch's current HEAD produces.
  3. **The excluded primary-target share.** Children paired with an adult travelling for work,
     business or education (adult `W_ZWECK` 1-3) are excluded from anchoring here
     (`purpose_not_secondary`) and keep phase 1's `other` purpose assignment. ADR-0112 measured this
     group at **4.2 %** of raw pairs (adult `W_ZWECK` 1/2 share of the <= 15-minute pairs) -- an
     ASSUMPTION carried from that record, not re-measured by this package's own link-rate
     instrumentation, which reports the same legs under the coarser `purpose_not_secondary` reason
     (also covering the adult's trip-home and Bringen/Holen exclusions).
- **Evidence:** issue **#385** (phase 2 of #372 / ADR-0112); design spec
  `docs/superpowers/specs/2026-09-11-passive-escort-joint-location-design.md`; related **ADR-0072 /
  ADR-0073** (#201, the inverse active-side household link), **ADR-0104** (the chainsolver reads the
  reporting-day trips), **ADR-0112** (phase 1, passive escort purpose, the 4.2 % work/business
  primary-target share -- adult `W_ZWECK` 1/2 -- and the 94.15 % pairing rate this record's link
  rate is measured against). Committed
  reference table `eqasim-data/data/braunschweig/mid/mid2023_escort_w_zweck_split.csv` (script
  `scripts/derive_escort_w_zweck_split.py`, data record `mid2023_reference_tables`): **6,384/6,781 =
  94.15 %** of raw passive legs pair with a same-household adult leg within 15 minutes -- a RAW-MiD
  donor-side fact, not a synthetic-population rate, quoted here only as the upper bound the
  synthesis-side link rate is compared against. Code:
  `braunschweig/synthesis/locations/passive_joint_links.py` (`build_passive_joint_links`,
  `resolve_joint_anchors`), `braunschweig/synthesis/locations/secondary_chainsolvers/__init__.py`
  (`_build_shared_solve_state`, `_solve_problem_set`, `_compose_two_pass`, `execute`),
  `braunschweig/synthesis/locations/secondary_chainsolvers/escort.py`
  (`rewrite_anchored_activities`), `synthesis/population/spatial/secondary/problems.py`
  (`ANCHORED_PURPOSES`, `_anchor_coordinates`), `configs/base_bs.yml` (the escort block's
  `escort_passive_joint_location` line). Tests: `tests/test_passive_joint_links.py`, the
  problem-splitter anchored-boundary tests (`tests/test_escort_links.py`) and the stage-level
  two-pass-assembly tests (`tests/test_passive_joint_two_pass.py`). No automated test covers the
  `_solve_problem_set` extraction itself: the OFF-path frame equality below is a one-off manual
  comparison of the two stage pickles, as the feature record states
  (`evidence.off_path_byte_identical.note`). Run manifest
  `docs/runs/i385-passive-joint-location-smoke-03101-2026-09-11.yml` (smoke on Kreis 03101,
  completed 2026-09-11): measured link rate 36/74 paired passive legs linked (48.6 %),
  anchor-resolution rate 36/36 (100.0 %), OFF-path frame-equality result FRAME-EQUAL against the
  pre-refactor baseline, and the ON-run two-pass numbers (2,208 + 6 problems, `cs.solve` 19.0 s +
  19.1 s) -- see the manifest for the full breakdown.
  Feature record `docs/registry/features/escort_passive_joint_location.yml`; feature doc section
  `docs/features/escort-purpose.md#joint-location-adr-0119`.
