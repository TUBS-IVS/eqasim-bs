# The Wolfsburg car parks of the garage dataset

Mechanism of spec Amendment E14 of the parking cost zones v2 design (issue #436, Task 4b3): how the 24 car parks of the
Wolfsburg city layer enter `parking_garages_2026`. The decisions and the rejected alternatives live in the design spec, the
ADR of the feature and the data record `parking_garages_2026` (counts, assumptions, licences); this note says where the code
is and which rules a maintainer must keep. The garage options around it are in
[parking-garage-options.md](parking-garage-options.md).

## What it does

`scripts/curation/parking_zones_2026/wolfsburg_lots.py` reads the owner's package `Wolfsburg_Parkplaetze_Pruefung_2026-10-07.zip`
(fourth optional input of `regional_garages.py`: `--wolfsburg-lots-zip`, needs `--zones` and `--tariffs`). Each of the 24
points gets exactly one class, decided from the fee status of the package and the position of the point, never from a name:

| Class | Meaning | Result |
| --- | --- | --- |
| a | paid municipal car park inside one zone and one strictly matched tariff area (data decide it) | QA candidate `zone_street_product` with the consistency check of its published hourly reference against the zone's street rate |
| b | operator tariff (Autostadt P2, Klinikum visitor car parks) | dataset row, `facility_kind` `surface_lot` |
| c | free for the public (Allerpark, Theaterparkplatz, Volkswagen Arena P1/P2) | dataset row at cost 0, no assumption |
| d | no fee evidence, outside every tariff area and zone (data decide it) | dataset row at cost 0 under ASSUMPTION P12 |
| e | user group only (disabled parking) or customers only (BadeLand) | QA candidate `user_group_only` or `customer_regime` |

## Where things are

| Concern | Place |
| --- | --- |
| Reading and verifying the package, classes, conversion of the components, operator quotation | `scripts/curation/parking_zones_2026/wolfsburg_lots.py` |
| The owner decisions (class per point, roles of the components, release, readings) | `scripts/curation/parking_zones_2026/regional_garage_specs.py` (`LOT_SPECS`, `LOT_RULE_IDS`, `LOT_RELEASED`) |
| Rows and QA rows | `scripts/curation/parking_zones_2026/regional_garages.py` (`decide_lots`, `build_lot`, `lot_zone_row`, `qa_rows`) |
| `facility_kind`, the free schedule, the grace period of a tiered garage, P12 | `braunschweig/parking/garages.py` (`FACILITY_KINDS`, `is_free_schedule`, `is_grace_period`, `ASSUMPTIONS`) |
| Zone consistency check, QA reason `user_group_only` | `braunschweig/parking/garage_qa.py` (`zone_reference_check`, `zone_reference_summary`) |
| Tests | `tests/test_parking_wolfsburg_lots.py` (synthetic package and the committed rows at the tariff edges) |

## Rules maintainers must keep

- **A package value enters only through an owner decision.** The package marks every record `full_cost_calculation_ready=false`;
  `LOT_RELEASED` names the owner decision per component and the statuses of the package's own field decisions it relied
  on; if the package changes a status, the release stops the step. Never type a tariff number into the specifications.
- **The data decide classes a and d.** A class the fee status does not allow, a class that the data refute and a point decided
  as a municipal free default that lies inside a published tariff area or a committed zone stop the step
  (`wolfsburg_lots.check_decision`). The same 24 points must be the points of the regional layer `wob_parkplaetze`.
- **Cost 0 is the free schedule `0- free`** (one open free band, formal fee window 0 to 24 h), never a tariff row with a
  zero rate. A free rule that states a free period or an end is the grace form, not a free car park.
- **P12 is the weakest evidence of the dataset**: no fee evidence is not evidence of none. Every P12 row cites the field
  decision of its fee status as its evidence and is counted by the validator and the loader.
- **Positions are the city's points** (`geoviewer_car_park_point`), no entrances; capacity and operator only where the package
  states them (capacity with its scope; the operator by a quotation in the package's copy of the operator's own page).
- **Do not edit the two data files by hand**; regenerate them (command in the data record) and update the pinned hashes of
  the record.
