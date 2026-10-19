# The Wolfsburg car-park package of 2026-10-07 (historical, reader removed)

> **Superseded and removed (spec Amendment I, Task 4j; reader deleted in Task 4l, owner decision 2026-10-14, issue #436).**
> The owner package `Wolfsburg_Parkplaetze_Pruefung_2026-10-07.zip` (SHA-256
> `650508f87c80ee2b84063def301ff67b5ecb8f2653cef2ab4202fb398c818df0`, 24 car parks of the city layer `wob_parkplaetze`) is no input of
> the current dataset and no code reads it: the module `wolfsburg_lots.py`, the option `--wolfsburg-lots-zip`, the owner decisions
> of spec E14 (`LOT_SPECS`, `LOT_RULE_IDS`, `LOT_RELEASED`) and the tests of the reader are gone. Every Wolfsburg surface lot of
> `parking_garages_2026` now comes from the large-lot research package, see [parking-large-lots.md](parking-large-lots.md).

Where the history lives (this stub keeps no facts of its own):

- **Why and how the package was read, and why it was replaced:** ADR-0140 decision 8 (garage tariff forms and evidence rules,
  Amendments E10 to E14, the classes a to e of the 24 points, ASSUMPTION P12) and decision 13 (large surface lots, Amendment I, with
  the withdrawal of P12 under I5 and the rejected alternatives).
- **The package as a dataset input in history** (pinned SHA-256, what was read, the 13 rows it contributed before Task 4j and the
  hashes of those datasets): the data record `docs/registry/data/parking_garages_2026.yml`.
- **The code as it was:** the git history of `scripts/curation/parking_zones_2026/wolfsburg_lots.py` before the Task 4l commit.
- **What stays in the code on purpose:** the file name and SHA-256 of the package as the constants
  `SUPERSEDED_WOLFSBURG_LOTS_FILE` and `SUPERSEDED_WOLFSBURG_LOTS_SHA256` of `regional_garages.py`, because the attribution and the
  column glossary written into the dataset name the package as part of its history (the dataset must stay byte-identical), and the
  withdrawn ASSUMPTION P12 in `braunschweig/parking/garages.py` (`WITHDRAWN_ASSUMPTIONS`), which keeps the register entry that the
  Java fixture pins.
