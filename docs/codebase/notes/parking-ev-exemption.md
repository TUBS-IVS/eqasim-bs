# The EV exemption of the parking cost zones

Mechanism of spec A5 of the parking cost zones v3 design (issue #436, Part A, tasks A3, A4, A6 and A7). The decision, its
assumptions (E1, E2) and the rejected alternatives live in ADR-0142 (decision 5); this note says where the code is and which
rules a maintainer must keep. The zone-based pricing around it is in [parking-cost-zones.md](parking-cost-zones.md), the zone
values (the data of Part B) in the data record `parking_tariffs_2026`.

## What it does

Some towns let an electric car park free on the street up to a limit. The model reads that as three facts that travel
separately and meet in the pricing of one stay:

1. **The zone states the rule.** The tariff table has the optional columns `ev_free_minutes` (minutes, empty = no exemption)
   and `ev_rule_valid_until` (ISO date, empty = open-ended, only together with `ev_free_minutes`), defined in
   `braunschweig/parking/zones.py` (`EV_COLUMNS`, validated per row; they exist on street_paid rows only, a campus zone and a
   resident zone refuse them) and read into `cost.ZoneTariff.ev_free_minutes` by `tariff_export.tariff_row_to_zone`. The
   committed table has no value yet: Part B fills them from the 2026-10-14 packages and does not redefine the columns.
2. **The export applies the validity date.** `tariff_export` writes `ev_free_minutes` into the model (schema 5, every zone
   entry, null = none) only while the rule is in force on the tariff snapshot date (`parking_tariff_snapshot_date`); an
   expired rule is exported as no exemption with an info line that names the zone and the expiry (the switch is never
   silent). The pricing reference therefore never sees a validity date.
3. **The person carries the flag.** `braunschweig.parking.attach.attach_ev_exemption` adds the bool column
   `parking_ev_exempt` to the persons (`PARKING_EV_EXEMPT_COLUMN`), which the plans writer writes as the person attribute
   `parkingEvExempt` (only where true; the Java reader `ParkingAttributes.evExempt` treats absent as false and refuses any
   other type). `braunschweig.matsim.scenario.population` calls it after the in-commuter merge when
   `parking_ev_exemption_enabled` is true (default true, declared only with `parking_zones_enabled`; false writes no column and
   the plans are byte-identical to those without the feature).
4. **The pricing applies the rule.** `cost._ev_exempt_street` (Python reference) and the same step of
   `org.eqasim.braunschweig.parking.ParkingCostCalculator` (Java): after the early rules (home, `parkingFree`, resident, outside
   the fee window) and the campus branch, before the street product, an exempt person whose chargeable duration (street fee
   window, seconds rounded up to minutes) is at most `ev_free_minutes` AND at most the zone's `max_stay_min` pays 0 with the
   outcome `EV_EXEMPT`. A longer stay pays the full schedule: the exemption is no deduction. `EV_EXEMPT` is a FREE outcome in
   `scripts/parking/compare_parking_targets.py` and is counted in the street-free counter and in the report column
   `ev_exempt_count` (outcome report v4).

## Who is exempt (ASSUMPTION E1)

A person is exempt iff at least one vehicle it owns with `mode == "car"` has a powertrain in `attach.ELECTRIC_POWERTRAINS`
(`bev`, `phev`; compared case and whitespace insensitive). Facts a maintainer must keep:

- The powertrain is a per-VEHICLE column of the vehicles frame (the second element of `synthesis.vehicles.vehicles`, after the
  in-commuter merge; written as the vehicle attribute `powertrain`). The vehicle TYPES frame has no such column. Vehicles are
  attributed to their `owner_id`, never to the other members of the household.
- `ELECTRIC_POWERTRAINS` is mirrored in `attach.py` and pinned equal to `fleet_sampling_de.ELECTRIC_POWERTRAINS` by a test
  (`tests/test_parking_attach.py`); importing the fleet module would put its import closure into two synpp stage tokens.
- The placeholder routing car (`type_id` `default_car`, no powertrain) is never exempt: a person without a car of the
  household fleet owns only that car. A fleet car without a powertrain, or a vehicles frame without the column (the legacy
  fleet of `fleet_model_enabled: false`), cannot be classified, exempts nobody and is warned about.
- **Several cars.** The plans tie no leg to a vehicle, so the rule is "any owned car is a BEV/PHEV" and over-assigns the
  exemption to the owner of a mixed fleet. Such owners exist: the household fleet
  (`braunschweig.synthesis.vehicles.cars.household`) gives the cars beyond the number of licensed adults round-robin to the
  licensed adults and all cars of a household without a licensed adult to its oldest adult. The log counts them.
- **The log lines are the fallback transparency.** `[parking] EV exemption:` gives the headline rate per car owner;
  `[parking] EV exemption basis` states the share of fleet-car owners (the headline counts the owners of the placeholder car
  only as car owners, so it understates the rate among fleet-car owners), the rate of every category, the several-car and mixed
  owners and the powertrains. A warning is raised for fleet cars that cannot be classified, a missing column, cars of unknown
  owners and a fleet without any BEV/PHEV.

## ASSUMPTION E2: the exemption is clipped to the maximum stay

Where a zone's `ev_free_minutes` exceeds its `max_stay_min` (the 2026-10-14 package gives Salzgitter 240 min against a
150-min maximum and does not say how they relate), the exemption applies within the maximum only: a stay above the maximum is
not exempt even where it is within `ev_free_minutes`. The validator logs every such zone (`zones.validate_tariffs`); the rule
is in `_ev_exempt_street` and in its Java counterpart and is pinned by the golden cases of the family X.

## Where things are

| Concern | Place |
| --- | --- |
| Zone columns, validation | `braunschweig/parking/zones.py` (`EV_COLUMNS`) |
| Validity date, model entry, register E1 and E2 | `braunschweig/parking/tariff_export.py` (`_ev_rule_in_force`, `build_tariff_model`) |
| Rule | `braunschweig/parking/cost.py` (`_ev_exempt_street`, outcome `EV_EXEMPT`); Java `ParkingCostCalculator` |
| Person flag, log lines | `braunschweig/parking/attach.py` (`attach_ev_exemption`), `braunschweig/matsim/scenario/population.py` |
| Golden contract | `braunschweig/parking/golden_cases.py` (family X), `tests/fixtures/parking/parking_golden_cases.json` |
| Report | Java `ParkingOutcomeReportListener` (`ev_exempt_count`), `scripts/parking/compare_parking_targets.py` (`ev_exempt_share_<group>`) |
| Config | `parking_ev_exemption_enabled` in `configs/base_bs.yml` and the two popsim fixture configurations |

## Known limitations

- Every BEV/PHEV counts as an E-plate holder (E1, spec A5): no share, no sensitivity beyond the flag; the synthetic fleet
  has no plates.
- The mixed-fleet over-assignment above, and that the owner of a placeholder car is never exempt.
- Goslar's exemption holds at marked machines only (not modelled); the charging-only regime of Wolfenbuettel and the unverified
  rules of Wolfsburg, Bad Harzburg, Braunlage, Helmstedt and Gifhorn are not modelled (spec A5; Part B states the values).
- A rule with a validity date before the snapshot date is exported as no exemption: a run with an older snapshot date prices
  differently from one with a newer one.
- An exempt stay is free as a whole: the street option costs 0, so E4 applies and the garage and lot options carry no weight
  (the stay is counted in the street-free counter, ADR-0142 decision 5, until the report's `ev_exempt_count`). A stay above
  `ev_free_minutes` prices over the street and the options like everybody's.
