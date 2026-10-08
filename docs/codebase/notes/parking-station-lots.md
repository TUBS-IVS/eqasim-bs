# The station car parks of Braunschweig Hbf and Wolfsburg

Mechanism of spec Amendment G of the parking cost zones v2 design (issue #436, Task 4g): how the DB BahnPark station car parks
enter the release. The decision and its rejected alternatives live in ADR-0140 (decision 11) and the design spec; counts, hashes
and licences are in the data records `parking_zones_2026`, `parking_tariffs_2026`, `parking_garages_2026` and
`parking_coverage_register_2026`. This note says where the code is and which rules a maintainer must keep.

## What it does

The owner decided on 2026-10-08 that the station car parks are public (BahnCard and Pcard only give a discount), which revokes
rulings R-4b-4 and R-4b-9 for exactly four car parks:

- **Braunschweig Hauptbahnhof P1 Nord, P2 Sued and P3 West are single paid-site zones** (`bs_hbf_p1_nord`, `bs_hbf_p2_sued`,
  `bs_hbf_p3_west`): the area within 50 m of the OSM outline of the lot (ASSUMPTION C-a), as the other D3 sites. The step
  `station_lots.py` is the LAST step of the zone chain: it appends the zones, their three tariff rows and their QA rows to the
  FINISHED release and refuses to change any other zone (P1 is cut against the BgA zone Willy-Brandt-Platz, which borders it).
- **The Wolfsburg Parkdeck Hauptbahnhof P1 is a garage** (`wob_hauptbahnhof`): one entry of `GARAGE_SPECS`, its rules read by id
  (`WOB_HBF_P1_R01` rate, `WOB_HBF_P1_R02` day tariff as the cap, `WOB_HAUPTBAHNHOF_MONTHLY_1`).

## Where things are

| Concern | Place |
| --- | --- |
| Reading and verifying both inputs, the evidence, zones, tariff rows, QA rows, the append | `scripts/curation/parking_zones_2026/station_lots.py` |
| The shared cut of a rule-based zone and the precedence list | `scripts/curation/parking_zones_2026/assemble_parking_zones.py` (`cut_rule_zone`, `cut_against_neighbours`, `PRECEDENCE_REGIONAL`, `STATION_ZONE_IDS`) |
| The deck, its monthly products, the two directory candidates, the three used station products | `scripts/curation/parking_zones_2026/regional_garage_specs.py` (`GARAGE_SPECS`, `MONTHLY_PRODUCTS`, `DIRECTORY_DECISIONS`, `BS_RECORDED_SPECS`) |
| The QA vocabulary (`station_zone`) | `braunschweig/parking/garage_qa.py` (`CANDIDATE_REASONS`) |
| Tests | `tests/test_parking_station_lots.py` (a synthetic package and the committed release), `tests/test_parking_garage_options.py` (the deck) |

## Rules maintainers must keep

- **No amount is typed.** The specification names the LABEL of the line to read in the fee table of each Contipark page; the
  step reads its amount and stops when the line is missing, duplicated or contradicted. The same amounts must stand in the DB
  BahnPark sheet and the monthly amount must be the cheapest publicly purchasable monthly product of the package's rules.
- **Both inputs are hash-pinned** (`PACKAGE_SHA256` through `bs_monthly_products`, `OSM_SHA256`); every member read is checked
  against the package's `manifest.sha256`; no script of the package is ever executed.
- **The step is additive.** It never re-cuts or re-simplifies another zone (a second run of `apply_precedence` on a finished
  release changes committed zones, so it is not used); a station zone that would overlap a committed zone stops the step and
  the whole zone chain must be re-run with the lots.
- **The commuter product is a used product of its zone**: the QA row (`monthly_bs_hbf_*`) names the zone in `zone_ids`, and the
  validator compares `commuter_day_eur` with the amount over 21 working days; a station product is never a garage product and
  never a value of ASSUMPTION P13.
- **The size of the zones is a limitation**: they are 4.7, 4.6 and 9.2 times the area of the lots (P1, P2, P3); the OSM fee=no street-side parking west of the station lies 86 to 390 m outside the zones; the zones take in the station forecourt (P1) and private Siemens lots (P2, mostly free through the employer draw); the destination content is small (building potentials: P1 12 buildings, P2 4, P3 5); a bare outline without the 50 m area would leave the zones inert.
- **Not modelled and named**: the Kiss&Ride and evening tariffs of P1, the BahnCard and Pcard discounts, the weekly and machine
  monthly tickets, the reserved-space products and the stated maximum durations; free street parking west of the station
  stays unzoned.
- **The step writes the header lines of the tariff table** (`station_header`: assumptions G-a and G-b and the bites lists), and it refuses a zone file that is not named `parking_zones_2026.geojson` (the GeoJSON `name` member follows the file name).
- **The fee window of the station rows is an assumption** (ruling R-4g-2): it is read from the opening hours of an operator page, so `fee_window_source` is `assumption` and the note names ASSUMPTION F1.
- **Do not edit the data files by hand**; regenerate them (commands in the data records and the download checklist) and update
  the pinned hashes of the records.
