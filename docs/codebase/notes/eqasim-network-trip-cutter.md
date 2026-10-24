# eqasim's population cutter keeps a car trip a car trip when its access or egress walk crosses the border

`braunschweig.matsim.simulation.prepare` cuts the prepared scenario to the cordon extent
(`_cut_to_cordon` in `braunschweig/matsim/simulation/prepare.py`, which runs
`org.eqasim.core.scenario.cutter.RunScenarioCutter`). For every trip of a network mode (car,
car_passenger, ...) the population cutter, `org.eqasim.core.scenario.cutter.population.trips.NetworkTripProcessor`,
replaces the parts outside the extent by `outside` activities and `outside` legs and gives every
inside piece one leg, which the router later routes again. The crossing points come from
`DefaultNetworkTripCrossingPointFinder`, which checks every leg of the routed trip: the access walk,
the car leg and the egress walk.

## The defect, fixed in the fork only (eqasim-bs#465)

Upstream eqasim-java has it (the code is unchanged since the fork point, eqasim-java @ 3f7da9b). The
inside piece took the mode of the leg it crossed the border on (`NetworkTripCrossingPoint.legMode`).
When a home lies just outside the extent and its access walk to the first car link crosses the
border, the incoming crossing lies on the walk, so the whole inside piece, the car leg included,
became one `walk` leg; the same on the way back with the egress walk. The router then teleported a
walk across the inside part.

Evidence, 1 % rerun on felix (`~/wt-i487-rerun`, prepare cache
`braunschweig.matsim.simulation.prepare__b4c75863700204be34f47ab4ffba9947`, compared with the
synthesis population `braunschweig.matsim.scenario.population__7d6576a723c2726c47aacde2d01fc277`):

| Person | Synthesis | Prepared (cut) |
|---|---|---|
| 1125988 (in-commuter; the access walk crosses the border 106 m from home) | home -> work by car, work -> home by car | `outside` -> work -> `outside`, both legs `walk`, 107.5 km and 23:53 h each; stuck at the day end |
| 1125983, 1125994 (in-commuters, same home location) | home -> work by car | first inside piece `walk`, 1.25 km between two border points |

These are the 4 legs of 3 persons among the 37 walk legs of 1 km and more that touch an `outside`
activity in that population. Of the other 33, 31 belong to persons with synthesised PT trips; the two
spot-checked persons that also drive (425518, 963065) walk on a former PT trip, not a car trip (a
separate mechanism, the PT router's walk fallback, see [plan-check](plan-check.md); the rest was not
checked trip by trip). 2 belong to person 343377, whose synthesised portal stay is already
reached on foot (82.5 km each way) before the cut (portal trips, not the cutter). Walk pieces shorter than 1 km cannot be told apart
from genuine access walks in the output, so 4 is a lower bound; the counter below gives the exact
number on the next cut.

## The rule since the fix

An inside piece runs from the trip start or an incoming crossing to an outgoing crossing or the trip
end. `NetworkTripCrossingPoint.tripLegIndex` records on which leg of the trip each crossing lies.

- If both ends lie on the same access or egress leg, the piece is the short walk between the border
  and an activity and keeps that leg's mode (the old behaviour, which is right here).
- Otherwise the piece contains the main network leg and keeps the trip's routing mode, so the
  router routes it by car again.

`NetworkTripCrossingStatistics` counts both kinds and, among the routing-mode pieces, those whose
crossing leg is an access or egress leg (the ones the old code turned into walks). `RunScenarioCutter`
logs the counts once after the population cut, as a line starting with `[cutter] inside pieces of
border-crossing network trips`. A rising "cross the border on an access/egress leg" count is
expected wherever homes lie close to the extent border; it is not a fallback.

Pinned by `TestNetworkTripProcessor` in the core tests of eqasim-java-bs (TUBS-IVS/eqasim-java-bs#48,
branch `fix/i465-cutter-car-trip-mode`). The rule holds only with a jar built from a fork commit that
contains that change; the effect of the fix has not been measured in a run yet.

## Rules for maintainers

- Never label a cut piece with the mode of the leg it crosses on without checking whether the piece
  contains the main leg; `TransitTripProcessor` already uses the routing mode for its pieces.
- After any change to the cutter, compare the cut population with the uncut one for long walks next
  to `outside` activities (the `n_walk_legs_over` count of the plan check, `frozen` class).
