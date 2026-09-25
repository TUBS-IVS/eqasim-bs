# VRB tariff zone polygons: open items for the data providers

Status: **not sent** (2026-09-24). This note collects what still has to be asked or
reported to the two providers before the polygon layer can be committed or the
locality list can be trusted blindly. The dataset facts (fields, counts, hashes,
cross-check numbers) live in the Data Registry record
`docs/registry/data/vrb_tariff_zone_polygons.yml`; this file only holds the
outreach items and their state.

## 1. Regionalverband Grossraum Braunschweig (RGB): licence and maintenance

- Dataset: ArcGIS REST service `Verkehr/Tarifzonen`, layer 0 (46 polygons, EPSG:25832),
  `https://webgis.regionalverband-braunschweig.de/server/rest/services/Verkehr/Tarifzonen/MapServer/0`.
- Question 1: Under which licence may the layer be used and redistributed? The service and
  the portal item carry no licence text; the geodata linked from "Daten & Services" are
  CC BY 4.0. Until confirmed, the file stays local-only (`eqasim-data/`, gitignored) and is
  recorded as `license: unknown`, `redistribution: restricted`.
- Question 2: Is the layer maintained (last portal modification 2023-12)? VRB still publishes
  48 zones in 2026 and our 2026-09-24 cross-check against the VRB locality list found no
  boundary change, but a confirmed update cadence would let us pin a vintage per scenario year.
- Question 3: Are zones 55 (Haemelerwald Bahnhof) and 56 (Dedenhausen Bahnhof), which lie in
  Region Hannover, available as geometries, or are they to be treated as single-station zones
  (our current assumption, VRB terms 2026 section 9.2)?

## 2. VRB GmbH: five locality-to-zone entries on the website look wrong

Source checked: the Start/Ziel locality selectors on
`https://www.vrb-online.de/de/tickets/tarifzonen-preisstufen` (726 entries, captured
2026-09-15). Each entry below names a zone that neither the official RGB polygon layer nor the
GTFS stops of that locality support. The `stops` column counts the GTFS stop points of the
locality in our 2026-09-24 spatial cross-check.

| Locality (Gemeinde) | Zone on the website | Zone of the polygon / stops | Stops | Remark |
|---|---|---|---|---|
| Buendheim (Bad Harzburg) | 92 | 90 | 24 | zone 92 exists neither in the price-stage matrix nor in the layer |
| Frellstedt (Helmstedt) | 33 | 31 | 15 | website zone about 5 km away |
| Wolsdorf (Helmstedt) | 50 | 31 | 10 | website zone about 42 km away |
| Wobeck (Schoeppenstedt) | 13 | 33 | 4 | website zone about 53 km away |
| Wunderbuettel (Wittingen) | 53 | 13 | 3 | website zone about 48 km away |

Consequence for us: every name-based zone assignment built from that list (the HTML-scrape
mode of `scripts/build_vrb_stations_json.py`, `vrb/stations.json`, and the locality-seed
inventory of the regional fare branch) inherits these five errors; the polygon spatial join
does not. The distances are taken from the discovery note of 2026-09-24 and were not
re-measured here.

## 3. What happens when the answers arrive

- Licence confirmed as CC BY 4.0 or similar: set `licensing` in the registry record, add the
  attribution string, and decide whether to commit the GeoJSON (3.4 MB) or keep the download step.
- Licence refused or unclear: keep local-only; the pipeline still runs, the README download
  step remains the acquisition path.
- VRB corrects the list: re-capture the locality page, rerun the cross-check, update the
  registry record's cross-check paragraph; nothing else depends on the list.
