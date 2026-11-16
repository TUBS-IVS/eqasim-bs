# Download checklist — Braunschweig (ARS 031, Niedersachsen)

This file is a **signpost, not an inventory**. It used to hold full dataset tables
(sources, paths, licences, lists of committed files). Those tables duplicated
their owners and drifted from the tree (audit findings CFG-F20, CFG-F27, 2026-11), so
they were removed. A fact about a dataset lives in exactly one place:

| You need | Owner |
|---|---|
| Which files to obtain, exact target path under `eqasim-data/data/`, source, licence, restrictions, whether a file is committed or local-only | the Data Registry, [`docs/registry/data/*.yml`](../docs/registry/data/), rendered as [`docs/generated/DATA.md`](../docs/generated/DATA.md) (build it with `python -m braunschweig.documentation build`; never commit it on a branch) |
| The setup-oriented summary, the data-protection policy and the downloader scripts | README, section "Data setup" |
| An automated check of what is present, missing, optional, restricted or generated, with the source of each | `python scripts/verify_braunschweig_inputs.py` (`--matsim` adds the MATSim-only inputs; `--check-urls` probes the public sources; see its `--help`) |
| The dataset entry tags (`A1`, `B1b`, `D5`, ...) | the `name=` of each entry of `INPUTS` in `scripts/verify_braunschweig_inputs.py`; a registry record names its entry in `verification.verifier_entry` |

All data paths are relative to `eqasim-data/data/`. `eqasim-data/` is gitignored;
the only tracked exceptions are the small derived reference tables and provenance
documents allowed by `.gitignore`. Do not `git add -f` any other data file.

When you add or replace a pipeline input, update the registry record, the verifier
entry and the README "Data setup" (the duty table of
[`docs/DOCUMENTATION_GOVERNANCE.md`](../docs/DOCUMENTATION_GOVERNANCE.md)); this file needs no change.

## Preprocessing of the large raw inputs

ALKIS buildings, ATKIS landuse and the OSM Niedersachsen extract are processed once
into GeoParquet; the synpp stages read only the preprocessed files. Entries C1 to C3
of the verifier name the raw input and the output of each. The scripts and their
current arguments:

- `scripts/preprocess_alkis_landuse.py` (ALKIS buildings and ATKIS landuse)
- `scripts/preprocess_osm_pois.py` (OSM POIs)

Run `python scripts/<script> --help`; the arguments are not repeated here.

## VRB tariff-zone mapping (entry D3)

`braunschweig.data.vrb.zones` reads `vrb/stations.json`, built once by
`scripts/build_vrb_stations_json.py` from the GTFS feed and the tariff zones, either
from the official zone polygons (preferred) or from the public VRB tariff-zone page
(legacy name matching). The polygon download, its licence status and the build
arguments are in the data records `vrb_tariff_zone_polygons` and `vrb_tariff_zones`
and in `python scripts/build_vrb_stations_json.py --help`.

## Bounding box (eight ZGB Kreise)

- AGS prefixes: `03101, 03102, 03103, 03151, 03153, 03154, 03157, 03158`
- WGS84, as used by `scripts/build_vrb_stations_json.py` (`ZGB_BBOX_WGS84`):
  9.6 - 11.4 deg E, 51.4 - 52.7 deg N. Use it when pre-filtering GTFS.
