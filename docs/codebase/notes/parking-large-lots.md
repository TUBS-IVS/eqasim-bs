# The large surface lots of the garage dataset

Mechanism of spec Amendment I of the parking cost zones v2 design (issue #436, Task 4j and its fix round): how the research
package of the large public surface lots enters `parking_garages_2026`. The decisions, the consequences and the rejected
alternatives live in ADR-0140 (decision 13); every number of the dataset (counts, shares, hashes, the lots that differ from their
zone) and the rules and licences of its sources live in the data record `parking_garages_2026`, which is the only place that
states them; this note says where the code is and which rules a maintainer must keep. The garage options around it are in
[parking-garage-options.md](parking-garage-options.md); the history of the superseded Wolfsburg city-layer package (reader removed) is in
[parking-wolfsburg-lots.md](parking-wolfsburg-lots.md).

## What it does

`scripts/curation/parking_zones_2026/large_lots.py` reads the owner's package `Grossparkplaetze_ZGB8_2026-10-12.zip` (an
optional input of `regional_garages.py`: `--large-lots-zip`, needs `--zones` and `--tariffs`; it superseded the earlier
Wolfsburg car-park package, whose option `--wolfsburg-lots-zip` was removed in Task 4l). Every record of the package is
either an option (one dataset row with `facility_kind` surface_lot and the key `lot_<facility key>`) or ONE QA candidate row with
its reason. The steps, the rule of each and the code that carries it:

- **Policy (ASSUMPTION L50).** Open, a single surface lot, no alias, not unconfirmed, access class public, public with a customer
  discount or mixed public and customers (an owner decision on documented evidence is the only other way), capacity for the
  threshold at least the threshold (`--lot-threshold`, default 50). Code: `large_lots.decide`.
- **Shortlist cross-check.** The package's own flags `public_candidate_30/50/100` must equal the policy; the load stops
  otherwise. Code: `large_lots.check_package_flags`.
- **Tariff by role.** The owner decision per site names the roles of the general-use components; the existing encoder reads the
  values. Code: `regional_garage_specs.LARGE_LOT_SPECS`, `large_lots.convert_rules`, `regional_garages._large_lot_encoding`.
- **Street product of a zone (I1, owner decision A, rulings R-4j-6 and R-4j-7).** The lot carries the WHOLE table row of its
  zone: rate per started unit, fee window, cap, the maximum stay with its long-stay product as the bands of its schedule and, at
  a decision-A lot, the commuter product as its monthly product; the documented component of the package is compared with the
  row (rate, billing unit, fee window). Code: `large_lots.zone_product_rules`, `large_lots.zone_check_sentence`,
  `regional_garages._attach_zone_monthly` with `regional_garage_specs.LARGE_LOT_ZONE_MONTHLY`.
- **Pass over every lot inside a zone polygon (R-4j-7).** The street equivalent of the row against the row of each zone polygon
  that contains its point, report-only. Code: `regional_garages._zone_consistency_pass`, `garage_qa.lot_street_equivalent`,
  `garage_qa.zone_consistency_check`, printed by `scripts/validate_parking_zones.py`.
- **Median product (P14).** Paid lots with a tariff of their own, same zone first (two donors at least), else municipality,
  ordered by the price of 60 min. Code: `large_lots.median_schedule`, `regional_garages._impute_p14`.
- **Free Wolfsburg lots on a per-site source (I5a).** Free only on an archived page that holds the quotation. Code:
  `large_lots.load_free_evidence`, `regional_garage_specs.WOB_FREE_LOT_EVIDENCE`.
- **Provenance columns with a counted fallback.** The source of a tariff is the first page that states a URL and a date and is no
  web-service layer; the position source is the OSM object, else a coordinate source of the package, else (counted) the tariff
  source; the capacity origin is reported, OSM tag, area estimate or derived. Code: `large_lots.source_of`,
  `regional_garages._position_source`, `large_lots.capacity_origin`.
- **Rows, QA rows, counts and warnings.** Code: `build_large_lots`, `_large_lot_row`, `_qa_candidate`, `_large_lot_counts` in
  `scripts/curation/parking_zones_2026/regional_garages.py`.
- **QA reasons, P14, five package hashes, zone checks.** Code: `CANDIDATE_REASONS`, `garages.ASSUMPTIONS`,
  `garages.MAXIMUM_PACKAGE_HASHES`, `zone_product_lot_check` in `braunschweig/parking/garage_qa.py` and `garages.py`.
- **Tests.** `tests/test_parking_large_lots.py`: the synthetic package, the policy, the conversions, P14, the evidence gate, the
  rows, the primary path on the real package, the exactness sweep of the zone products, the pass inside the zone polygons and the
  committed release at its pricing edges.

## Rules maintainers must keep

- **Read the package as its README says.** The capacity for the threshold is `capacity_selected_for_threshold`, never
  `capacity_reported` alone; a missing capacity is unknown and never 0; a site's capacity counts once; `facility_key` is the key of
  the tariff components; components are evidence and no alternatives; `null` is unknown. Only a REPORTED capacity fills the dataset
  column `capacity_reported`; an OSM value or an area estimate is named in the notes with its origin.
- **No silent fallback.** The step logs the primary method against the fallback for the tariff (own components, zone street
  product, free, imputed under P14), for the source of the tariff (a page against a web-service layer), for the source of the
  position (OSM object, coordinate source, tariff source) and for the capacity origin (an origin that the step does not know is
  counted as other and warned), the QA rows by reason, the rows that carry a restriction the columns do not (a maximum stay or a
  weekend window), a warning above 25 % imputed rows and one warning per tariff that the grammar cannot express (that site is the
  QA row `lot_tariff_not_expressible`, never approximated). The counts read a field of the row (`_facts`, the evidence column),
  never the text of the notes. The tests pin zero such rows at 30, 50 and 100 on the real package and zero fallbacks at 50; a
  changed package that creates one fails them, which is the signal to extend the grammar in a task of its own.
- **A mistake of an owner decision stops the whole step** (a component the facility does not hold, two forms for one site, an
  option without a decision); only a refusal of the encoder is a QA row. An option at a threshold needs an entry in
  `LARGE_LOT_SPECS`, also for the sensitivities (the entries for 30 are there).
- **The zone tariff is never changed by a lot.** A zone-product lot carries the table row of the zone that contains its point (the
  step stops when the point lies elsewhere) in every compared column: rate, unit, grace period, cap, fee window, maximum stay with
  its long-stay product and commuter product. The validator keeps the dataset row equal to the table row
  (`parking_tariffs_2026.csv:<zone_id>` in the evidence column), so a regenerated table without a regenerated dataset fails, and
  the sweep test pins that the lot row alone and the street path of the zone price every stay alike. A change of the zone table
  therefore needs the dataset regenerated (and its pins) in the same change.
- **The pass inside a zone polygon is report-only.** A lot with a package component of its own that differs from the zone row keeps
  the component as documented; the difference is named in its notes and QA note and printed by the validator, never an error.
  Only a zone polygon that the tariff table does not hold stops the validation.
- **P14 never invents a price.** The median is a donor's complete schedule; an even number of donors needs identical schedules in
  the middle, else the step stops; a free lot is no donor; fewer than two donors on both levels is the QA row
  `lot_tariff_unknown`.
- **A free Wolfsburg lot needs its page.** The sites of `WOB_FREE_LOT_EVIDENCE` are rows only when every page the table names for
  them is given (read in place from `Parkdaten_Restluecken_2026-10-14.zip` with `--wob-evidence-zip`, each page against the
  package's `file_manifest.json`, or listed with its SHA-256 in `wob_free_lots_evidence/MANIFEST.md`) and holds its quotation;
  otherwise they are QA rows with what was searched, and a named page that a given archive lacks stops the step. A site that the
  package gives no position stays a QA row (`lot_no_position`) whatever its pages state (Volkswagen Arena P2). The row names every
  page with its SHA-256, carries no maximum stay (a free lot does not inherit the product of a zone) and, for the Arena, the
  match-day condition that is not modelled (ASSUMPTION W-match, owner decision 2026-10-14). ASSUMPTION P12 is withdrawn: no default
  makes a lot free.
- **Restrictions are named, not modelled.** A maximum stay that the bands do not carry, a Saturday or Sunday window, a customer
  discount, a vehicle-group exemption and a purchase-bound free period are listed per row in the notes ("Not modelled", "Not
  used") and never change a price.
- **Sensitivities never reach the release.** `--lot-threshold 30` or `100` is refused when `--out` or `--qa-out` carries the release
  file name; the sensitivity datasets are the sensitivities of the calibration (`--sensitivity-garages` of
  `scripts/parking/calibrate_garage_decay.py`, header lines of its table, never a release value), written elsewhere.
- **Regenerate, never edit.** The dataset is a local restricted file (never `git add` it), the QA table is committed; the command,
  the municipality pickle of the position check and the pinned hashes are in the data record.

## Limitations

- Option weights ignore capacity: a small lot pulls as much as the largest garage at the same distance (spec E3).
- Many lots are free by an explicit source statement; a free option within 1,000 m lowers the expected cost of a stay in a paid
  zone (the data record counts them).
- The decay lengths were recalibrated on this option set (Task 4k, spec I6, ADR-0140 decision 14, data record
  `parking_garage_decay_calibration_2026`); a new option set, a lot that becomes a free option included, needs a new calibration
  (a test ties the table to the dataset pinned in the data record).
- The Wolfsburg sites with the owner's free evidence are QA rows while no page is archived; the archived copy of a page needs the
  owner's confirmation of the download.
