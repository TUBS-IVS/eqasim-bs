# ADR-0163 · 2026-10-23 · A far walk or bicycle portal stay without a road mode becomes pt

- **Status:** accepted (implemented; effect not yet measured in a run)
- **Numbering:** 0163 checked free across all local and remote branches on 2026-10-23 (0153-0162 are taken by the
  sibling branches of #465).
- **Issue:** #465 (person 343377 of the 1 % rerun of the #465 fixes walks 82.5 km to an outside activity).
  **Amends:** ADR-0141 (the portal stay's fixed mode), in interplay with ADR-0145 (outside-tour modes).
- **Owner decision (2026-10-23):** no long walks; no new issue (reference #465).

## Context

Evidence from the 1 % rerun on felix (`~/wt-i487-rerun/cache_rerun`, portal stage `c5a07be6...`, outside-tour-modes
stage `da0565f5...`, synthesis population `7d6576a7...`; read-only scripts in `~/scratch_i465_portalwalk`):

- Person 343377 (age 23, licence, `carAvailability none`, `carPassengerAvailability none`, `bicycleAvailability none`):
  the donor day is home -> work by walk, reported 0.49 km (`euclidean_distance` 377 m). The work location assigned by
  the primary-location step lies beyond the 45 km portal threshold, so the classification (home -> ASSIGNED location,
  ADR-0141) makes the leg a portal stay; the portal stage keeps the donor's outbound mode `walk`, because walk is
  "always available" and never checked against a distance. The stay gets a ROAD gate (`portal_gate_0026`, 63 km from
  home); the prepared population routes two walks of 82.5 km and 18:20 h each.
- In that run 15 of 582 portal stays are walk (13 work, 2 education; donor distances 0.1-3.0 km) and 36 are bicycle
  (28 work, 1 education, 7 leisure). All of them come from the same mismatch: the donor's mode belongs to the donor's
  distance, the assigned or drawn destination is beyond 45 km.
- The outside-tour-modes stage (ADR-0145) re-modes 97 portal tours, among them most of these, by a SrV-weighted draw
  among the plausible modes of the person, restricted to the gate class of the stay (pt -> rail exit stop, every other
  mode -> road gate). It leaves 2 portal tours unchanged with `no_candidate_in_gate_class`, and exactly 2 soft-mode
  portal stays remain in the final trips: person 343377 (walk) and person 64849 (bicycle, no car, no lift; home to
  gate 63.5 km). Both persons can use no road-class motorised mode, and pt is outside the locked road class.

So the defect starts where the portal stage fixes the gate class from an implausible soft mode: no later stage can
move the stay to the only plausible mode left (pt).

## Decision

1. **Plausibility bound of the soft modes, from the committed SrV table.** A walk / bicycle stay is beyond its
   distance when the straight-line distance from its origin (home or the assigned primary location) to its
   destination (assigned location or drawn external point) exceeds `braunschweig.portal.walk_max_distance_m`
   (default 5000) or `braunschweig.portal.bicycle_max_distance_m` (default 30000). The defaults are the lower edge of
   the first band of `eqasim-data/data/braunschweig/srv/srv2023_mode_by_distance_band.csv` (group `all`) in which the
   mode's share falls below the `min_share` 0.01 that ADR-0145 uses for implausible: walk 0.68 % in 5-10 km, bicycle
   0.30 % in 30-45 km. Derived from a committed table, not a reference value of the model's output.
2. **No road-class motorised mode -> pt in the portal stage.** When such a stay's person can use neither car (car
   availability and licence) nor car_passenger, the stay becomes `pt`, counted as a mode substitution with reason
   `soft_mode_beyond_distance_no_road_mode`. It then takes the timetable-aware pt gate or the no-connection chain of
   ADR-0152/ADR-0156 (shift, earlier return, lift as the last resort), like every other pt stay.
3. **Otherwise left to outside_tour_modes.** A far soft-mode stay whose person can use car or car_passenger keeps its
   road gate; ADR-0145 then re-modes it with its SrV-weighted draw within the road class. Counted as
   `n_soft_mode_stays_left_to_outside_tour_modes`; `n_soft_mode_stays_beyond_distance` counts both cases. A soft-mode
   availability substitution (bicycle without a bicycle) keeps its existing reason and order.

## Rejected options

- **Constrain the primary-location assignment by the donor's distance:** the work/education locations follow the
  commuting flows; binding them to a donor's 0.5 km walk would bias the flows and is a larger design change of the
  location step. The location is plausible, the donor mode is not.
- **Substitute every far soft-mode stay in the portal stage (car -> car_passenger -> pt):** robust without
  ADR-0145, but replaces its SrV-weighted draw by a deterministic car-first order for about 50 stays per 1 % run.
- **Let outside_tour_modes switch the gate class:** the gate and its facility are fixed by the portal stage
  (anchors, facilities, chain solver); a later class switch would need a second gate choice downstream.

## Consequences

- Output change by design: in the evidence run 2 soft-mode stays become pt (then gate_found, shifted or a lift); the
  other far soft-mode stays are unchanged here and still re-moded by ADR-0145.
- LIMITATION: with `braunschweig.outside_tour_modes.enabled: false` the far soft-mode stays of persons with a road mode
  stay walk / bicycle; the portal report counts them (`n_soft_mode_stays_left_to_outside_tour_modes`).
- **Status:** implemented and unit/stage tested; not yet re-measured in a run. A smoke would not be a validation.
