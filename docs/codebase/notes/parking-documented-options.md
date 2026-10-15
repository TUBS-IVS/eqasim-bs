# Parking documented options (Bad Harzburg, Seesen; locating a facility that a source documents and no package places)

Mechanism of spec v3 B7 of the parking cost zones (issue #436, owner decisions of 2026-10-15). The decision and the rejected
alternatives live in ADR-0142 (Part B, decision 14); the results of the pinned run (which option is located where, the rates,
the QA rows) live in the data record `parking_garages_2026`, never here. This note says where the code is and which rules a
maintainer must keep. The surface lots of the large-lot package are in [parking-large-lots.md](parking-large-lots.md), the
options in the pricing in [parking-garage-options.md](parking-garage-options.md).

## What it does

Some facilities are documented by a source (a tariff on a page, a free status in a brochure or a city page) and located by no
package: the three HarzVenture lots and the free car park areas of Bad Harzburg, the private garage Bahnhofstrasse of Seesen.
`scripts/curation/parking_zones_2026/documented_lots.py` finds their position in the pinned OpenStreetMap extract with ONE function,
`match_locator`, and `regional_garages.build_documented_options` turns the matches into dataset rows (free options) and QA rows
(everything else). The step is optional: without `--osm-extract` nothing is built, the QA candidate of the Seesen garage stays and
the run says so.

## Where things are

| Concern | Place |
| --- | --- |
| The documented options and their locators | `regional_garage_specs.DOCUMENTED_OPTIONS`, `DOCUMENTED_CANDIDATE_RELEASES`, `ZONE_CAR_PARK_FACILITIES` |
| Reading the extract, the matching function, the capacity rule | `documented_lots.read_osm_extract`, `osm_objects_from_layers`, `match_locator`, `locate_all`, `osm_capacity` |
| Reading the evidence in place (HarzVenture page, brochure, city page) | `documented_lots.read_harzventure_lots`, `read_brochure_free_parking`, `read_seesen_garage_evidence` |
| Rows, QA rows, rates | `regional_garages.build_documented_options`, `_documented_free_row`, `_zone_comparison`, `_load_documented_inputs` |
| Tests | `tests/test_parking_documented_lots.py` (reader, matching, capacity, evidence), `tests/test_parking_documented_options.py` (rows) |

## Rules maintainers must keep

- **One matching function.** The keys of a locator are tried in a fixed order (`MATCH_METHODS`: the named OpenStreetMap object,
  the name tag, the address tags of the parking area, the nearest parking area to an object that carries the address, the nearest
  to a named place) and the first key that finds a candidate decides; an AMBIGUOUS result is final too (the later keys are not
  tried, so a second key never breaks the tie). A nearest match needs `MATCH_DISTANCE_LIMIT_M` and a lead of
  `AMBIGUITY_MARGIN_M` over the second candidate; kerbside parking types are the street and never a candidate. Both numbers are
  ASSUMPTIONS (nothing committed fixes them); change them only with the data record and a rerun.
- **No geocoding service, no typed coordinate.** An address is located by an OpenStreetMap object that carries it. A locator that
  cannot be matched uniquely is a QA row (`lot_no_position`), never a guessed position.
- **The extract is pinned.** `read_osm_extract` checks the SHA-256 before it reads; the layers are scanned once per run and the
  rates of the methods are logged (`locate_all`), with a warning for every option that is not matched.
- **A match at a zone car park is no new option.** The zone is the tariff there; the QA row (`zone_street_product`) names the zone,
  the hourly reference and compares the page with the zone row (report only, the zone row never changes).
- **The size rule of the surface lots is unchanged**: `osm_capacity` is the capacity rule of the large-lot package (an OSM tag, else
  `floor(area / 25)` of a surface polygon) at the threshold of the run; a garage has no size rule.
- **A free option rests on its evidence page**: the file is read in place or checked against its pinned SHA-256, the quotation
  must be in the text, and the note of the row quotes it with the hash. A historical source (the brochure of 2021/22) is stated as
  historical in the note.
- **A paid HarzVenture lot that is no zone car park stops the step.** Its tariff (15 free minutes, a maximum fee, a fee time with a
  stated end and no stated start) needs an encoding that is not built; the step refuses instead of approximating.
- **The inputs belong together**: `--osm-extract`, `--owner-transcriptions-dir`, `--wob-evidence-zip`, `--zones`, `--tariffs`.
