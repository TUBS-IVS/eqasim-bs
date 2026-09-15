# ADR-0120 · 2026-09-14 · Preserve donor demographics and chronological in-commuter plans

- **Status:** active
- **Issue:** #397
- **Numbering:** All local branches and origin tracking refs were inspected with
  `git for-each-ref` and `git ls-tree` on 2026-09-14. ADR-0119 already exists on the
  passive-escort branch; 0120 is the next free id. The implementation starts at
  `origin/main` commit `1502e88ef529001167f4a8848892862c39bda9cb`.

## Context

`braunschweig.data.hts.mid_donor.build_mid_donor_frames` forwarded optional
`age`/`sex`, while `MidSource.load_donor` supplies `HP_ALTER`/`HP_SEX`. Consequently
the worker and student person builders received neither column and substituted
40/male and 22/male respectively. The old synthetic fixture already contained the
normalised names and concealed the production schema mismatch.

`braunschweig.data.cordon.plans.extract_activity_times` can return the same trip
as both the outbound and inbound anchor when the diary is incomplete. For a lone
08:00-09:00 outbound leg, the old imputer moved the work departure to 17:00 but
left home arrival at 09:00, producing a -28,800-second return trip. This is a
synthetic reproduction, not a production prevalence estimate.

## Decision

Two independent flags are default-ON in the canonical base config:

- `cordon_incommuter_donor_demographics`: the donor adapter calls the existing
  `braunschweig.popsim.expand.map_demographics` after employment/study mapping.
  Age is retained directly. The existing seeded same-age-band, then global,
  binary-sex imputation applies to non-binary/missing survey codes. A missing
  age-band key uses the global observed pool. Missing raw columns, invalid ages,
  an entirely absent observed binary-sex pool or unresolved mapped sex raise.
  Direct and imputed coverage is logged. This reuses the model's current sex
  representation; it does not reinterpret survey nonresponse as male.
- `cordon_incommuter_time_chronology`: after middle-departure repair, home arrival
  is repaired when it is non-finite **or earlier than that departure**. Negative
  home-departure/middle-arrival times are also repaired. The completed sequence
  must satisfy `0 <= depart_home <= arrive_middle < depart_middle <= arrive_home`.
  Next-day times remain allowed. Trips and activities use the same completed
  arrays through `assemble_incommuter_core_frames`.

Each OFF path keeps the old transformations and RNG consumption. The time helper
retains its fixed local seed, independent of the stage RNG. Donor demographic
mapping occurs after the existing employment draws and does not change those
draws. Cache validation tokens include the relevant shared mappers, plan helper
and student assembly dependency so a helper edit invalidates cached stage output.
This is scoped cache coverage, not a repository-wide helper audit.

## Assumptions and alternatives

The time repair retains the old positive-duration sampling within the same
subpopulation. Duration pools are computed sequentially and can include anchors
repaired earlier in the same call; pool draws are therefore not necessarily
fully observed durations. **ASSUMPTIONS retained from the previous implementation:**
when the relevant pool is empty, middle arrival is 08:00, middle duration is eight
hours for work or six for education, and each travel duration is one hour. These
are not empirical reference values. The logs distinguish duration-pool draws
(potentially based on repaired anchors) from direct fixed assumptions and warn
on every repair or direct fixed-assumption use.

Clamping home arrival to departure would create zero-duration return trips for
the broken diaries. Drawing a positive duration uses the established imputation
policy instead. Dropping incomplete diaries would change donor-pool composition
and demand timing beyond this repair. Changing the shared resident demographic
mapper would broaden the scientific scope, so the guard stays in the cordon
adapter. Disabling a correction is for historical reproduction: it deliberately
restores the corresponding defect as part of the legacy result.

## Evidence and consequences

`tests/test_incommuter_donor_consistency.py` covers raw schema propagation into
both person builders, single-leg diaries for both purposes, empirical return
duration sampling, invalid/missing inputs, next-day timings, stage configuration
and cache tokens. `test_legacy_off_outputs_match_frozen_pre_fix_baseline` compares
donor CSV output, subsequent RNG bytes and time-array bytes against
`tests/fixtures/incommuter_legacy_397.json`, captured before implementation from
the base commit named above. The actual-data smoke is recorded in
`docs/runs/incommuter-donor-smoke-2026-09-14.yml`.

Corrected age/sex can change demographic summaries and age-conditioned fleet
attributes. Corrected timing changes inconsistent plans. Donor selection,
in-commuter counts and the demand/mode reference inputs are unchanged. The synpp
dependency graph, required datasets, paths and setup commands are unchanged;
README setup instructions therefore need no amendment. Empirical scenario
validation and a full MATSim run are not claimed.
