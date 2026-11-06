# ADR-0184 · 2026-11-05 · Portal pt gate candidates are the served exit stops within an extra-detour cap

- **Status:** accepted (implemented; replayed once, see the status line at the end)
- **Issue:** #572. **Amends:** ADR-0152 decision 2 (the order of the pt gate filters). The rest of ADR-0152
  stands, including its rejection of rail-only gates: buses stay gate candidates.
- **Numbering:** ADR-0184 because ADR-0183 is taken by PR #571 (issue #570); checked across local and remote
  branches on 2026-11-05.

## Context

- ADR-0152 decision 2 cut the directional exit stops of a pt portal stay to the `gate_candidates` (default 5)
  smallest straight-line detours (origin -> stop -> external point) BEFORE the timetable service check.
- Most exit stops are village bus stops with few departures a day. For many stays none of the five nearest
  candidates is served at the stay's times although a served station lies a little further away; those stays
  then go through the no-connection chain and mostly become car or car passenger.
- Measured on the 100 % production run `prod100_20261031` (model output, not validation): the counts of exit
  stops, the share of stays with any served candidate today and the shares under the alternative rules are in the
  body of issue #572, section "Details" (https://github.com/TUBS-IVS/eqasim-bs/issues/572). The same section shows
  that the return-service proxy of ADR-0152 is not the cause.

## Decision

1. **`choose_pt_gates` filters in this order** (`braunschweig.synthesis.portal_trips.gates`): directional exit
   stops (unchanged direction rule) -> only stops whose detour is at most the smallest directional detour of the
   stay plus `braunschweig.portal.gate_max_extra_detour_m` -> only stops SERVED at the stay's times (outbound, and
   at the re-entry when the stay has a return; unchanged service check and window) -> the `gate_candidates`
   smallest detours among those -> the router check of ADR-0152 -> the smallest connected detour wins.
2. **The cap is relative to the best directional stop, served or not**, so a stay whose nearest directional
   stops are all unserved does not take a station arbitrarily far away.
3. **Default cap 10,000 m: an ASSUMPTION** chosen by the owner on 2026-11-05, not estimated from data; the
   key, unit and valid range are documented in `braunschweig/synthesis/portal_trips/config_keys.py`
   (`KEY_GATE_MAX_EXTRA_DETOUR_M`).
4. **Observability:** `choose_pt_gates` logs, besides the per-reason rates of ADR-0152, the rate of stays whose
   gate lies beyond the old top `gate_candidates` by detour and the rate of stays with no served stop within the
   cap although a directional stop beyond the cap is served (both info; no warn threshold, because no observed
   reference bounds them).

## Rejected options (owner triage 2026-11-05)

- **Weight candidates by frequency or prefer rail:** more parameters without a committed reference; rail-only
  gates were already rejected in ADR-0152.
- **Route more than `gate_candidates` candidates:** the served-first filter removes the crowding-out while
  keeping the per-stay router bound.
- **No cap:** a served stop far off the direct line would give implausible detours; the cap bounds them.
- **Change the return-service proxy:** the measurement in issue #572 shows it is not the cause.

## Consequences

- More pt portal stays keep a timetable-aware gate; fewer go through the no-connection chain, so the mode mix
  of portal stays and the gate distribution change by design. No committed observed reference exists for either.
- The service check now runs over all directional stops within the cap instead of five per stay (more timetable
  lookups). The router bound per stay is unchanged (`gate_candidates`), but the total number of router tasks grows:
  stays whose five nearest stops had fewer than five served ones now send more tasks, so the portal stage runs
  longer (replay figures in the run manifest below).
- The two new counts of the first gate choice are in the stage report (`portal_trips_report.json`,
  `n_pt_gate_beyond_top_n`, `n_pt_no_served_within_cap`), not only in the log.
- The columns `n_in_direction` and `n_served` of `PT_GATE_COLUMNS` now count stops within the cap before the
  `gate_candidates` cut (docstring of `choose_pt_gates`).

## Evidence

- Code and tests: `gates.pt_gate_candidates`, `gates.choose_pt_gates`, `tests/test_portal_trips_gates.py`
  (primary path: a served station beyond six nearer unserved stops wins; a served stop beyond the cap is not
  taken), `tests/test_portal_trips_config_keys.py`.
- **Status:** implemented and replayed once on the 100 % cache of `prod100_20261031` (portal stage only, model
  output, not a validation); figures in `docs/runs/i572-portal-replay-prod100-2026-11-05.yml`. No MATSim run is
  part of this change.
