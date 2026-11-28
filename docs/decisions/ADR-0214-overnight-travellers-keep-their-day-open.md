# ADR-0214 · 2026-11-27 · Overnight travellers keep their day open instead of getting an invented trip home

- **Status:** active (branch `feature/i694-overnight-open-day-end`, default ON). Amends [ADR-0108](ADR-0108-plans-start-and-end-at-home-with-an-observed-closure-dwell.md) (closed plans) for persons who were on an overnight trip.
- **Numbering:** ADR-0214 is the next free id; checked on 2026-11-28 across all local and remote branches and the open pull requests (highest id taken: ADR-0213).
- **Issue:** #694. Owner decision of 2026-11-26/27: of three rules the owner picked A (the MiD overnight answer decides). The rule, the alternatives and the measurement bound are stated in issue #694 and below.
- **Numbers:** none are copied here. Measurement: [run manifest](../runs/i694-overnight-open-end-smoke-1pct-2026-11-27.yml). Feature record: `docs/registry/features/overnight_open_day_end.yml`.

## Context

ADR-0108 closes every synthetic day: a person whose MiD diary does not end at home gets an invented return-home trip. For persons who really spend the night away that trip is wrong: it adds an evening trip that did not happen, and for far stays it caused the car flips of #570. The committed SrV 2023 reference says that most mobile persons end their day at home (`share_mobile_last_to_home` in `srv2023_plan_structure_reference.csv`), so the closure is right for most diary gaps and should be dropped only where the survey itself says the night was spent away. MATSim and eqasim do not need a closed plan (open days run; the scoring warns that first and last activity differ), so this is a data decision, not a technical one.

## Decision

Switch `braunschweig.population.popsim.keep_overnight_open_ends` (default **true**) turns on **approach A**: the MiD answer "trip with an overnight stay on the reporting day" (`P_STREISE` 2 or 3) decides.

1. A diary that ends away from home and whose owner answered 2 or 3 keeps its open end: no return-home trip is appended, the validator does not raise `no_home_end` for it, and the decision holds in every repair pass (per-row column `keeps_open_end` carried by each chain, so a resampled or commute-day donor chain carries its own donor's decision).
2. Children under 14 (code 402, not asked) inherit the flag from a member of the same MiD household (all members share the reporting day). Codes 9 and 610 (no usable answer) close the day and are counted and logged.
3. Middle days of a trip (the diary starts away and its first leg does not go home) stay closed: the model rebases such a day at the synthetic home anyway. Only days that start at home or with an unknown start code can stay open. Start-of-day handling is unchanged.
4. The trip_class seed counts the day the plan realises: `+1` only for open-end donors that are closed.
5. The last activity of an open day has no end time; the agent stays until the simulated day ends. An open end inside the study area is located by the existing chain-solver fallback (tail problem); there is no change to `secondary_chainsolvers`.
6. ADR-0183 is unchanged: a far stay whose return is the invented closure still ends outside, flag or not.
7. OFF reproduces today's behaviour byte-identically. The ENTD source rejects ON.
8. Validation bound (a pre-registered ASSUMPTION, not a literature value): the simulated open-end share (pre-portal open ends plus far stays ended outside) must lie within SrV +/- 1 pp. A result outside the bound stops the merge and goes to the owner. The result is in the run manifest.

## Rejected options

- **B: share of open ends per arrival band and purpose from SrV, random draw.** Matches SrV in level and structure, but SrV would become a fitted target, so the independent check is lost; only 273 SrV open-end persons feed coarse cells; a random draw per donor or person breaks the agreement of seed and plan. Source of the trade-off: design section 5.
- **C: an arrival at 22 h or later keeps the day open.** Trivial, but MiD has about three times the late open ends of SrV (design section 3, probe numbers, not committed) and the rule misses daytime departures of travellers.
- **Close everything (ADR-0108 unchanged).** Keeps the evening trip that did not happen for overnight travellers.

## Consequences

- **Results change by decision** (default ON): the trip stage, its consumers and the PopulationSim seed change; the stage caches recompute once.
- **Evidence is a 1 % smoke, not a validation** ([manifest](../runs/i694-overnight-open-end-smoke-1pct-2026-11-27.yml)): the registered metric fell inside the bound, but there is no switch-off arm, no 100 % replay, one seed, and the universe differs from the SrV Kreise (the manifest lists each caveat). The effect against today's closure share is not measured; the next production run measures it.
- **Not covered:** night shifts and local overnight stays without a travel answer stay closed (SrV open ends contain many work ends); persons whose day starts away.
- **Stuck agents and plan-check findings** in the smoke belong to #678 and are reported in the manifest, not judged here.
- Analysis: `plan_structure_vs_srv` stays on the pre-portal view; its accepted-deviation note no longer says that all days are closed.
- If a later 100 % run lands outside the bound, the follow-up question is whether ADR-0183 should also require the flag (owner decision).

## Evidence

[Run manifest](../runs/i694-overnight-open-end-smoke-1pct-2026-11-27.yml); tests `tests/test_overnight_open_end.py`, `tests/test_popsim_trips_stage.py::test_keep_overnight_open_ends_off_is_byte_identical`, `tests/test_popsim_plan_validation.py`, `tests/test_trip_class_seed_closure.py`.
