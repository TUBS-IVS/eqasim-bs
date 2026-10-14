# The large surface lots of the garage dataset

Mechanism of spec Amendment I of the parking cost zones v2 design (issue #436, Task 4j): how the research package of the large
public surface lots enters `parking_garages_2026`. The decisions, the results of the run and the rejected alternatives live in
ADR-0140 (decision 13); the counts, rules and licences of the dataset in the data record `parking_garages_2026`; this note says
where the code is and which rules a maintainer must keep. The garage options around it are in
[parking-garage-options.md](parking-garage-options.md); the superseded reading of the 24 Wolfsburg points is in
[parking-wolfsburg-lots.md](parking-wolfsburg-lots.md).

## What it does

`scripts/curation/parking_zones_2026/large_lots.py` reads the owner's package `Grossparkplaetze_ZGB8_2026-10-12.zip` (an
optional input of `regional_garages.py`: `--large-lots-zip`, needs `--zones` and `--tariffs`; it supersedes
`--wolfsburg-lots-zip` for the rows, so passing both stops the step before anything is read). Every one of its 152 records is
either an option (one dataset row with `facility_kind` surface_lot and the key `lot_<facility key>`) or ONE QA candidate row with
its reason:

| Step | Rule | Place |
| --- | --- | --- |
| Policy (ASSUMPTION L50) | open, a single surface lot, no alias, not unconfirmed, access class public, public with a customer discount or mixed public and customers (an owner decision on documented evidence is the only other way), capacity for the threshold at least 50 | `large_lots.decide` |
| Shortlist cross-check | the package's own flags `public_candidate_30/50/100` must equal the policy; the load stops otherwise | `large_lots.check_package_flags` |
| Tariff by role | the owner decision per site names the roles of the general-use components; the existing encoder reads the values | `regional_garage_specs.LARGE_LOT_SPECS`, `large_lots.convert_rules`, `regional_garages._large_lot_encoding` |
| Street product of a zone (I1, decision A) | rate per started unit, fee window and cap of the zone's table row, consistency check against the documented component | `large_lots.zone_product_rules`, `large_lots.zone_check_sentence` |
| Median product (P14) | paid lots with a tariff of their own, same zone first, else municipality, ordered by the price of 60 min | `large_lots.median_schedule`, `regional_garages._impute_p14` |
| Free Wolfsburg lots on a per-site source (I5a) | free only on an archived page that holds the quotation | `large_lots.load_free_evidence`, `regional_garage_specs.WOB_FREE_LOT_EVIDENCE` |
| Rows, QA rows, counts and warnings | `build_large_lots`, `_large_lot_row`, `_qa_candidate`, `_large_lot_counts` | `scripts/curation/parking_zones_2026/regional_garages.py` |
| QA reasons, P14, five package hashes, zone-product check | `CANDIDATE_REASONS`, `garages.ASSUMPTIONS`, `MAXIMUM_PACKAGE_HASHES`, `zone_product_lot_check` | `braunschweig/parking/garage_qa.py`, `garages.py` |
| Tests | the synthetic package, the policy, the conversions, P14, the evidence gate, the rows and the primary path on the real package; the committed release at its pricing edges | `tests/test_parking_large_lots.py` |

## Rules maintainers must keep

- **Read the package as its README says.** The capacity for the threshold is `capacity_selected_for_threshold`, never
  `capacity_reported` alone; a missing capacity is unknown and never 0; a site's capacity counts once; `facility_key` is the key of
  the tariff components; components are evidence and no alternatives; `null` is unknown. Only a REPORTED capacity fills the dataset
  column `capacity_reported`; an OSM value or an area estimate is named in the notes with its origin.
- **No silent fallback.** The step logs primary against fallback: options by tariff source (own components, zone street product,
  free, imputed under P14), the QA rows by reason, the number of rows that carry a restriction the columns do not (a maximum stay
  or a weekend window), a warning above 25 % imputed rows and one warning per tariff that the grammar cannot express (that site is
  the QA row `lot_tariff_not_expressible`, never approximated). The tests pin zero such rows at 30, 50 and 100 on the real package;
  a changed package that creates one fails them, which is the signal to extend the grammar in a task of its own.
- **A mistake of an owner decision stops the whole step** (a component the facility does not hold, two forms for one site, an
  option without a decision); only a refusal of the encoder is a QA row. An option at a threshold needs an entry in
  `LARGE_LOT_SPECS`, also for the sensitivities (the entries for 30 are there).
- **The zone tariff is never changed by a lot.** A zone-product lot carries the table row of the zone that contains its point
  (the step stops when the point lies elsewhere); the validator keeps the dataset row equal to the table row (`parking_tariffs_2026.csv`
  named in the evidence column), so a regenerated table without a regenerated dataset fails. A maximum stay or long-stay product
  of the zone is not carried (the notes name it).
- **P14 never invents a price.** The median is a donor's complete schedule; an even number of donors needs identical schedules in the
  middle, else the step stops; a free lot is no donor; fewer than two donors on both levels is the QA row `lot_tariff_unknown`.
- **A free Wolfsburg lot needs its page.** The three sites of `WOB_FREE_LOT_EVIDENCE` are rows only when `wob_free_lots_evidence/MANIFEST.md`
  lists an archived page with its SHA-256 and the file holds the quotation; otherwise they are QA rows with what was searched.
  ASSUMPTION P12 is withdrawn: no default makes a lot free.
- **Restrictions are named, not modelled.** A maximum stay, a Saturday or Sunday window, a customer discount, a vehicle-group
  exemption and a purchase-bound free period are listed per row in the notes ("Not modelled", "Not used") and never change a price.
- **Sensitivities never reach the release.** `--lot-threshold 30` or `100` is refused when `--out` or `--qa-out` carries the release
  file name; the sensitivity datasets are the inputs of the calibration (Task 4k), written elsewhere.
- **Regenerate, never edit.** The dataset is a local restricted file (never `git add` it), the QA table is committed; the command and
  the pinned hashes are in the data record.

## Limitations

- Option weights ignore capacity: a 50-space lot pulls as much as a 1,300-space garage at the same distance (spec E3).
- 29 of the 55 lots at 50 spaces are free by an explicit source statement; a free option within 1,000 m lowers the expected cost of
  a stay in a paid zone.
- The calibrated decay lengths belong to the 49-row option set of decisions 7 and 12 until Task 4k recalibrates them (spec I6).
- The three Wolfsburg sites with the owner's free evidence (Allersee Suedufer, Freibad Fallersleben, VW-Bad) are QA rows because no
  page is archived; the archived copy of the VfB Fallersleben page needs the owner's confirmation of the download.
