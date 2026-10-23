# ADR-0164 · 2026-10-23 · Far primary locations go by rank to the longest commuters

- **Status:** accepted (implemented; measured offline on the cached inputs of the 1 % rerun of the #465 fixes, run
  manifest `docs/runs/i465-primary-assignment-measure-1pct-2026-10-23.yml`)
- **Numbering:** 0164 checked free across all local and remote branches on 2026-10-23 (0153-0163 are taken by the
  #465 wave).
- **Issue:** #465 (person 343377 walked to a portal gate; root cause of the far work location). **Related:**
  ADR-0163 (the portal stage's soft-mode check, which handles the consequence), ADR-0141 (portal stays).
- **Owner decision (2026-10-23):** "korrigiere die Zuordnung der Arbeits-/Ausbildungsorte an die Personen
  (Rang-Matching statt gierig) ... mit Test, ADR und Messung der Fehlzuordnungen vorher/nachher".

## Context

- The primary-location step works in two parts. `synthesis.population.spatial.primary.candidates` draws, per
  origin zone, how many persons commute to which destination zone and location (the gravity OD calibrated to the
  BA Pendleratlas; it knows the zone, not the person). `synthesis/population/spatial/primary/locations.py` then
  hands these locations to the zone's persons: upstream eqasim walks the persons in input order and gives each
  the free location whose distance from the person's home is closest to the donor's commute distance
  (`define_distance_ordering`).
- That greedy order has no look-ahead: far locations that nobody early in the order wanted are left to whoever
  comes last. Person 343377 (Salzgitter, donor walked 0.38 km to work) received a work location in Kreis Pinneberg,
  168 km away; the portal stage turned it into an outside stay and the walk of 82.5 km followed.
- Measured on the cached inputs of the 1 % rerun with the upstream greedy (the measurement reproduces the cached
  assignment exactly): of 3,920 workers, 91 with a donor commute of at most 5 km got a location beyond 45 km, and
  only 152 of the 186 workers with a donor commute beyond 45 km got one.

## Decision

1. **Far class by rank.** Per origin zone, the sampled locations farther than
   `primary_location_far_class_min_distance_m` (default 30000, straight line from the mean home of the zone's
   persons) form the far class. The same number of persons with the LONGEST donor commute distances form the far
   class of persons; the two are paired by rank (shortest wanted distance with the nearest far location).
2. **Everything else unchanged.** The remaining persons take the remaining locations with the upstream greedy on
   their exact home distances, in input order. The sampled flows (which zone sends how many persons to which
   location) are not touched; the change is a permutation within an origin zone.
3. **Missing donor distance.** On the new path a person without a donor distance takes the zone's median donor
   distance (ASSUMPTION; upstream gave such a person an arbitrary remaining location). The stage logs the count.
4. **Observable.** Per purpose the stage logs the persons, those without a donor distance, the median and 90th
   percentile of |assigned - donor| and the persons whose donor commute is below a tenth of the far-class distance
   but whose location lies beyond it.
5. **Flag.** `primary_location_far_class_rank` (default true); false restores the upstream greedy byte-identically.
   Applies to work and, where the base education path is used, to education (in production education comes from
   the education gravity stage and is not affected).

ASSUMPTION (30 km): beyond this distance the spread of the homes inside one zone is small against the distances,
so the mean home may stand for every home in the rank decision. Below it the exact home distances matter and the
greedy keeps them. 30 km is a modelling choice, not a derived value; only this value was measured.

## Measured effect (1 % rerun, work, 3,920 workers; manifest above)

| metric | upstream greedy | far-class rank |
|---|---|---|
| donor <= 5 km, assigned > 45 km | 91 | 0 |
| donor > 45 km and assigned > 45 km (of 186) | 152 | 177 |
| donor <= 2 km, assigned > 10 km | 126 | 102 |
| abs. deviation median / p90 / mean (km) | 1.82 / 27.18 / 16.13 | 2.32 / 26.36 / 15.23 |
| Pearson donor vs assigned | 0.403 | 0.717 |
| person 343377 | 167.9 km | 1.6 km |

The median deviation rises by 0.5 km: the near class loses the far locations its last persons used to absorb and
must place those persons among near locations that fit them less well. Short donor commutes that end beyond 10 km
remain (102), because the near class keeps the upstream greedy.

## Rejected options

- **Pure rank matching over the whole zone** (persons by donor distance, locations by distance from the mean home):
  for short commutes the mean home is not the person's home, so a 1 km commuter at the edge of a large zone gets a
  location 1 km from the zone centre, several km from home. An exploratory, uncommitted comparison steered this
  choice; its figures are not evidence here.
- **Optimal assignment (minimum total |assigned - donor|) per zone:** quadratic memory and cubic time in the zone
  size; the largest zone (Braunschweig) holds about 86,000 workers at 100 %. Blocking it by rank reintroduces the
  mean-home error of pure rank matching for short commutes.
- **Re-sorting the greedy (ascending or descending donor distance):** keeps the leftover problem at the other end
  of the order.
- **Changing the gravity OD:** the flows follow the observed commuting statistics; the defect is the assignment
  of the drawn locations to persons, not the flows.

## Consequences

- Output change by design (default on): the work locations are re-permuted within each origin zone; the commute
  distance distribution, the portal stays and every downstream stage change. The flows per origin zone and
  destination location are unchanged (guarded by the measurement).
- LIMITATION: measured at 1 % only, from one cache; no 100 % population was available. Not a validation: the
  donor commute distance is a survey value of another person, not an observed distance of the synthetic person.
