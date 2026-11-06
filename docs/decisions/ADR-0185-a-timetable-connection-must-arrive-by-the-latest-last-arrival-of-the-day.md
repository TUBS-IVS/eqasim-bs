# ADR-0185 · 2026-11-05 · A timetable connection must arrive by the latest last arrival of the day

- **Status:** accepted (implemented; replay on the cache of `prod100_20261031` in the run manifest
  `docs/runs/i573-check-replay-prod100-2026-11-05.yml`)
- **Numbering:** 0182-0184 are taken on other branches (checked across all local and remote refs on 2026-11-05);
  0185 claimed in the shared session notes of the #572-#576 sequence.
- **Issue:** #573. **Amends:** the connection rule of ADR-0152 (`braunschweig.data.transit.connections.connection_mask`),
  which ADR-0153 (in-commuters) and ADR-0159 (outside tours) reuse. **Relates to:** ADR-0151 (next-day departures),
  ADR-0160 (routed day-end check).
- **Owner decision (2026-11-05):** bound the waiting time or the arrival time, the choice and value to be documented
  as an ASSUMPTION; the owner delegated the choice to the session ("Steuer das von hier").

## Context

- The connection rule counted any routed pt trip as a connection. Since ADR-0151 the schedule repeats the morning
  service at +24 h, so an evening task can be routed as a wait at the stop through the night for the first next-day
  departure.
- Its arrival then lies after the latest last arrival that prepare's routed day-end check allows (ADR-0160). Prepare
  moves the whole day earlier, routes it again, and at the earlier time a walk of many hours wins. Example:
  in-commuter 1186377 of `prod100_20261031` (model output, findings B3 behind #573; the replication is in the issue).
- The three timetable checks (in-commuters, outside tours, portal pt gates) accepted such trips as served, so their
  existing chains (shift, car, lift) never saw them.

## Decision

1. **Bound the arrival, not the wait.** A pt trip that passes every other test of the rule counts as a connection
   only if its routed arrival (the last pt leg's arrival at its egress stop plus the final walk, seconds of the
   simulated day) is at or before a limit. A trip beyond it is rejected under the reason `late arrival` and goes
   through the caller's existing chain. Only-walk results are not bounded (the accepted ones are short walks).
2. **The limit is prepare's.** `connections.latest_arrival_s` returns the end of the simulated day minus
   `braunschweig.day_end_margin.last_arrival_margin_s` (the whole day when the margin is disabled): the same number
   as the in-commuter and outside-tour shift bounds and as the limit prepare passes to the routed check (ADR-0160).
   No new number is introduced; a test guards the three against drift.
3. **Switch.** `braunschweig.transit_connections.latest_arrival_check` (default true, ASSUMPTION, pinned in
   `configs/base_bs.yml`); false restores the old rule.
4. **Observability.** Each caller logs, under its own label, the tasks the bound rejected out of the otherwise
   accepted pt trips, as a rate; WARNING above `LATE_ARRIVAL_WARN_SHARE` (most otherwise good trips rejected points
   at broken times, e.g. a unit mismatch).

## Rejected options

- **A maximum waiting time** (initial plus transfer waiting): needs a new number with no committed reference, and
  would also reject daytime connections on lines with long headways, where the wait is a timing artefact of the
  planned departure, not a missing service; it changes far more trips than the defect.
- **A maximum total travel time:** the same invented number, and it does not target the day-end interaction.
- **Changing ADR-0160 or the next-day duplication of ADR-0151:** out of scope by the owner's triage; both are correct
  on their own, the defect is that the checks judged differently from prepare.

## Consequences

- The checks now judge an evening trip as prepare will: a trip they accept no longer triggers the routed day-end
  shift through its own arrival.
- Limitation (ASSUMPTION): an overnight wait that still arrives before the limit (an early-morning task) stays a
  connection; it does not move the day and is simulated as a long wait at the stop.
- The portal shift chain keeps its own bound (end of the simulated day); a shifted portal trip that arrives between
  the two limits is rejected by the rule and falls to the chain's last resort, counted in `no_connection_action`.
- Outputs change for every caller whose tasks had a late arrival; the replay manifest gives the rates.
