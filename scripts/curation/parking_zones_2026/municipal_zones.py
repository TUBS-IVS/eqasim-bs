"""Municipal parking packages of 2026-10-01 in the zone curation (parking cost zones v2, spec Amendment C, issue #436).

Not part of the pipeline and never run by synpp: ``assemble_parking_zones.py --municipal-dir`` calls this step. Inputs:
the owner-supplied zips, copied unchanged into the gitignored
``eqasim-data/data/braunschweig/parking/raw_sources/municipal_2026-10-01/``; ``load_packages`` checks each against the
SHA-256 pinned in ``PACKAGE_SHA256`` (also in the data record parking_zones_2026) before any layer is read and reads
the GeoPackage layers straight from the zips (GDAL ``/vsizip/``), so no extracted copy exists that could drift. Every
layer must be EPSG:25832 with valid geometries, and the attributes the provenance wording states (controller ruling
R-C1: ``BRAUNSCHWEIG_PROVENANCE``, ``WOLFSBURG_PROVENANCE``, ``GOSLAR_PROVENANCE``) are checked against the layers.

* C1, ``braunschweig_fee_zones``: zones 1a and 1b of Braunschweig_Parkzonen.gpkg (layer parking_fee_zones) become the
  geometry of bs_zone_ia and bs_zone_ib (geometry_source ordinance_map; the assembly applies the v1 precedence, so the
  BgA car parks stay cut out); the layer reconstructed_section gives the flag ``reconstructed_section_m2``.
* C2, ``wolfsburg_tariff_zones``: the street sections of Wolfsburg_Parkdaten.gpkg (layer mobile_parking_areas, the
  city's Handyparkzonen) give one zone per tariff zone, the area within ``SECTION_BUFFER_M`` of its sections
  (ASSUMPTION C-a: the access walk from the destination to a paid street section); where the areas of two tariff zones
  overlap, the nearer section decides (``split_by_nearest_section``; ties to the higher hourly rate).
  ``tariff_zone_evidence`` derives the tariff values from the section attributes and ``check_tariff_rows`` checks the
  hand-written tariff rows against them.
* C4, ``qa_rows``: Goslar_Parkdaten.gpkg (layers resident_parking_zones, parking_facility_areas) is compared with the
  fee polygon gs_altstadt_zone1, which stays as it is (a cross-check only).

``qa_rows`` and ``write_qa_table`` write the committed QA table (layout ``braunschweig.parking.municipal_zone_qa``).
CRS: EPSG:25832 throughout, areas in m2, distances in m.
"""
from __future__ import annotations

import hashlib
import math
import re
import textwrap
from collections import Counter
from pathlib import Path
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.ops import unary_union

import curation_common as cc

MUNICIPAL_DIGITISED_ON = "2026-10-01"
#: The owner's packages this step reads (file ``<name>.zip``), with their SHA-256.
PACKAGE_SHA256 = {
    "Braunschweig_Parkzonen": "b47c463296995b035afc9894fe58dc57abdaffa14fa9709b2de1bfd144cd5abe",
    "Wolfsburg_Parkdaten": "73407d5b23ed2517547a0e648b6a91357772f4d9af1e4d1bc3b3efda4a3cad71",
    "Goslar_Parkdaten": "aa1bc980ae605cbe5fc2f533a2a991b4ba32c0913dc9b27090cf9d67ddb5b9d9",
}
#: The GeoPackage layers read per package (``<name>/<name>.gpkg`` inside the zip).
PACKAGE_LAYERS = {
    "Braunschweig_Parkzonen": ("parking_fee_zones", "reconstructed_section"),
    "Wolfsburg_Parkdaten": ("mobile_parking_areas",),
    "Goslar_Parkdaten": ("resident_parking_zones", "parking_facility_areas"),
}

# ---------------------------------------------------------------- provenance (controller ruling R-C1, verbatim)
BRAUNSCHWEIG_PROVENANCE = ("Stadt Braunschweig, published fee zone map (2025-11-26) and Amtsblatt 2024-04-30 map "
                           "annex; digitised (owner-supplied package 2026-10-01); working accuracy 25 m; base map Open "
                           "GeoData dl-de/by-2-0")
WOLFSBURG_PROVENANCE = ("Stadt Wolfsburg, Geoviewer Themenkarte Parken (Stand 12/2024); open reuse licence not "
                        "verified; used by owner decision 2026-10-01")
GOSLAR_PROVENANCE = "Stadt Goslar, ArcGIS service Bewohnerparken (last edit 2018-11-22)"
#: What the wording states, checked against the layer attributes before any geometry is used.
BRAUNSCHWEIG_WORKING_ACCURACY_M = 25.0
BRAUNSCHWEIG_MAP_DATES = ("2025-11-26", "2024-04-30")
BRAUNSCHWEIG_DIGITISED_ON = "2026-10-01"
WOLFSBURG_LAYER_STAND = "2024-12"
GOSLAR_LAST_EDIT = "2018-11-22"
#: The fee zone map the package digitised (its Metadaten.json, source fee_map); the source_url of bs_zone_ia/ib.
BRAUNSCHWEIG_FEE_MAP_URL = ("https://www.braunschweig.de/leben/stadtplan_verkehr/parken-in-braunschweig/"
                            "ausweitung-parkgebuehrenpflicht/20251126_Karte-Parkzone-Innenstadt-a-b-2025-01.jpg")

# ---------------------------------------------------------------- zones and parameters
BS_AGS, WOB_AGS, GS_AGS = "03101000", "03103000", "03153017"
#: Package fee zone -> release zone whose geometry it replaces (C1).
BRAUNSCHWEIG_FEE_ZONES = {"1a": "bs_zone_ia", "1b": "bs_zone_ib"}
#: The v1 polygon the Wolfsburg tariff zones replace (C2), and the Goslar polygon of the cross-check (C4).
WOLFSBURG_V1_ZONE = "wob_innenstadt"
GOSLAR_ZONE = "gs_altstadt_zone1"
WOLFSBURG_ZONE_ID = "wob_tarifzone_{}"
#: ASSUMPTION C-a (spec Amendment C2): a destination within this distance of a paid street section parks on it, m.
SECTION_BUFFER_M = 50.0
#: Spacing of the boundary samples behind the nearest-section split, m (the split line lies within half of it).
SAMPLE_SPACING_M = 1.0
#: Precision grid of the nearest-section split, m: the boundary samples are snapped to it before duplicates are
#: removed (near-coincident samples of touching or overlapping sections become one sample, keeping the higher tariff),
#: and the overlays of the split run on it, so that float noise cannot break the Voronoi diagram or the overlays.
SPLIT_GRID_M = 1e-6
#: The Goslar facility classes of the package field fee_status (from the source attribute Hinweis).
FACILITY_FEE_STATUSES = ("paid", "free", "unknown")
#: The billing unit of the Wolfsburg tariff rows: the started half hour of the city's Parkgebuehrenordnung of 2016
#: (ASSUMPTION C-b, ruling R-T1d-a); the layer states no unit.
WOLFSBURG_BILLING_UNIT_MIN = 30
#: Street-product columns of the Wolfsburg rows that no source fills (no first period, day cap, free period or
#: long-stay product is published for the sections): they must stay empty.
WOLFSBURG_EMPTY_COLUMNS = ("free_if_stay_at_most_min", "first_period_min", "first_period_eur", "daily_cap_eur",
                           "long_stay_product_eur")

_PRICE = re.compile(r"^\s*(\d+(?:,\d+)?)\s*\N{EURO SIGN}\s*pro\s+Stunde\s*$")
_WEEKDAY_WINDOW = re.compile(r"^(?:Mo-Fr\s+)?(\d{2}):(\d{2})-(\d{2}):(\d{2})$")
_SATURDAY_WINDOW = re.compile(r"^Sa\s+\d{2}:\d{2}-\d{2}:\d{2}$")


# ---------------------------------------------------------------- packages


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_layer(zip_path: Path, name: str, layer: str) -> gpd.GeoDataFrame:
    frame = gpd.read_file(f"/vsizip/{zip_path.resolve().as_posix()}/{name}/{name}.gpkg", layer=layer)
    if frame.crs is None or frame.crs.to_epsg() != 25832:
        raise SystemExit(f"{zip_path.name} layer {layer}: CRS {frame.crs} is not EPSG:25832, as the package states")
    if frame.empty or not frame.geometry.is_valid.all() or frame.geometry.is_empty.any():
        raise SystemExit(f"{zip_path.name} layer {layer}: empty, or invalid or empty geometries")
    return frame


def _single(values, what: str) -> str:
    distinct = sorted({str(value) for value in values})
    if len(distinct) != 1:
        raise SystemExit(f"{what}: {distinct} instead of one value")
    return distinct[0]


def _check_attributes(layers: dict) -> None:
    """Refuse (``SystemExit``) packages whose attributes contradict what the provenance wording and this step assume."""
    fee = layers[("Braunschweig_Parkzonen", "parking_fee_zones")]
    if sorted(fee["zone_id"].astype(str)) != sorted(BRAUNSCHWEIG_FEE_ZONES):
        raise SystemExit(f"parking_fee_zones holds {sorted(fee['zone_id'].astype(str))}; the step needs exactly the "
                         "zones 1a and 1b")
    accuracy = sorted({float(value) for value in fee["estimated_accuracy_m"]})
    if accuracy != [BRAUNSCHWEIG_WORKING_ACCURACY_M]:
        raise SystemExit(f"parking_fee_zones: estimated_accuracy_m {accuracy}; the recorded provenance states a working "
                         f"accuracy of {BRAUNSCHWEIG_WORKING_ACCURACY_M:.0f} m")
    for text in fee["source_date"].astype(str):
        if not all(date in text for date in BRAUNSCHWEIG_MAP_DATES):
            raise SystemExit(f"parking_fee_zones: source_date {text!r} does not name the map dates "
                             f"{BRAUNSCHWEIG_MAP_DATES} of the recorded provenance")
    digitised = {str(value)[:10] for value in fee["digitized_on"]}
    if digitised != {BRAUNSCHWEIG_DIGITISED_ON}:
        raise SystemExit(f"parking_fee_zones: digitized on {sorted(digitised)}, not the recorded {BRAUNSCHWEIG_DIGITISED_ON}")
    reconstructed = layers[("Braunschweig_Parkzonen", "reconstructed_section")]
    if set(reconstructed["zone_id"].astype(str)) != {"1a"}:
        raise SystemExit(f"reconstructed_section belongs to {sorted(set(reconstructed['zone_id'].astype(str)))}, not 1a")
    sections = layers[("Wolfsburg_Parkdaten", "mobile_parking_areas")]
    stand = _single(sections["source_metadata_date"], "mobile_parking_areas source_metadata_date")
    if stand != WOLFSBURG_LAYER_STAND:
        raise SystemExit(f"mobile_parking_areas: Stand {stand}, the recorded provenance states {WOLFSBURG_LAYER_STAND}")
    if [str(raw) for raw in sections["fee_zone_raw"]] != [str(int(key)) for key in sections["fee_zone"]]:
        raise SystemExit("mobile_parking_areas: fee_zone and its source field fee_zone_raw disagree")
    _single(sections["source_url"], "mobile_parking_areas source_url")
    for layer in PACKAGE_LAYERS["Goslar_Parkdaten"]:
        edits = {str(value)[:10] for value in layers[("Goslar_Parkdaten", layer)]["source_data_edit_date"]}
        if edits != {GOSLAR_LAST_EDIT}:
            raise SystemExit(f"Goslar {layer}: last edit {sorted(edits)}, the recorded provenance states "
                             f"{GOSLAR_LAST_EDIT}")


def load_packages(directory, expected_sha256: Optional[dict] = None) -> dict:
    """The verified layers of the owner's packages in ``directory`` (EPSG:25832).

    Every package of ``PACKAGE_SHA256`` (or of ``expected_sha256``, the same names, for synthetic test packages) must
    exist as ``<name>.zip`` with exactly that SHA-256, else ``SystemExit``: a changed package is never read. Returns
    {"files": name -> {"file", "sha256", "bytes"}, "bs_fee_zones", "bs_reconstructed", "wob_sections", "gs_resident",
    "gs_facilities"}.
    """
    expected = dict(PACKAGE_SHA256 if expected_sha256 is None else expected_sha256)
    if set(expected) != set(PACKAGE_SHA256):
        raise SystemExit(f"expected SHA-256 for {sorted(expected)}, the step reads {sorted(PACKAGE_SHA256)}")
    directory = Path(directory)
    files, layers = {}, {}
    for name, sha256 in expected.items():
        path = directory / f"{name}.zip"
        if not path.is_file():
            raise SystemExit(f"{path} missing: copy the owner's package {name}.zip unchanged into {directory}")
        actual = file_sha256(path)
        if actual != sha256:
            raise SystemExit(f"{path}: SHA-256 {actual} is not the recorded {sha256} (data record parking_zones_2026); "
                             "a changed package is never read")
        files[name] = {"file": path.name, "sha256": actual, "bytes": path.stat().st_size}
        for layer in PACKAGE_LAYERS[name]:
            layers[(name, layer)] = _read_layer(path, name, layer)
    _check_attributes(layers)
    print("municipal packages verified: " + ", ".join(f"{name} ({entry['bytes']} bytes, SHA-256 {entry['sha256']})"
                                                     for name, entry in files.items()))
    return {"files": files, "bs_fee_zones": layers[("Braunschweig_Parkzonen", "parking_fee_zones")],
            "bs_reconstructed": layers[("Braunschweig_Parkzonen", "reconstructed_section")],
            "wob_sections": layers[("Wolfsburg_Parkdaten", "mobile_parking_areas")],
            "gs_resident": layers[("Goslar_Parkdaten", "resident_parking_zones")],
            "gs_facilities": layers[("Goslar_Parkdaten", "parking_facility_areas")]}


# ---------------------------------------------------------------- C1: Braunschweig fee zones


def reconstructed_section(inputs: dict):
    """The union of the package layer reconstructed_section (the southern part of 1a), EPSG:25832."""
    return unary_union(list(inputs["bs_reconstructed"].geometry))


def braunschweig_fee_zones(inputs: dict) -> dict:
    """release zone id -> {"code", "geometry", "parts", "note"} for the package zones 1a and 1b (C1)."""
    fee = inputs["bs_fee_zones"].set_index(inputs["bs_fee_zones"]["zone_id"].astype(str))
    sha = inputs["files"]["Braunschweig_Parkzonen"]["sha256"]
    zones = {}
    for code, zone_id in BRAUNSCHWEIG_FEE_ZONES.items():
        row = fee.loc[code]
        geometry = row.geometry
        parts = len(getattr(geometry, "geoms", [geometry]))
        head = (f"{BRAUNSCHWEIG_PROVENANCE}. Zone {code} of the layer parking_fee_zones of Braunschweig_Parkzonen.gpkg "
                f"(package Braunschweig_Parkzonen.zip, SHA-256 {sha[:12]}...; {parts} part{'' if parts == 1 else 's'}), "
                f"digitised from the fee zone map 20251126_Karte-Parkzone-Innenstadt-a-b-2025-01 and georeferenced by "
                f"an affine fit on the Open GeoData city map 1:20,000 of March 2025 (fit RMSE "
                f"{float(row['registration_rmse_m']):.2f} m over the matched map features, a consistency measure, not "
                f"the boundary accuracy; the {BRAUNSCHWEIG_WORKING_ACCURACY_M:.0f} m are the package's working "
                f"estimate, not a confidence bound)")
        if code == "1a":
            note = (head + ": the dotted 1a outline. RECONSTRUCTED SECTION (its area in the field "
                    "reconstructed_section_m2): the southern part cut off by that map (package layer "
                    "reconstructed_section) follows the map annex of the Amtsblatt of 2024-04-30 "
                    "(amtsblatt_stadt_braunschweig_2024_5.pdf), "
                    "joined at the crop seam (the package's seam correction fades out within about 120 m); a newer "
                    "complete boundary of that section was not verified. Replaces the v1 tracing of the city's "
                    "overview map (2026-09-29), which lacked that part. The BgA car parks of the Entgeltordnung are "
                    "cut out (own zones, v1 precedence). The city describes 1a as the area inside the City-Ring plus "
                    "Grosser Hof, Werder, Wilhelmstrasse and Nimesstrasse.")
        else:
            note = (head + ": the green 1b areas, their boundaries shared with 1a. Replaces the v1 tracing of the "
                    "city's overview map (2026-09-29). The city describes 1b as the Wallring without the Theaterumfahrt "
                    "and the Steintorwall (both belong to 1a).")
        zones[zone_id] = {"code": code, "geometry": geometry, "parts": parts,
                          "note": note + f" QA rows {zone_id}_* of parking_zones_2026_municipal_qa.csv."}
    return zones


# ---------------------------------------------------------------- C2: Wolfsburg tariff zones


def parse_weekday_window(text) -> Optional[tuple]:
    """The weekday fee window of a 'Kostenpflichtige Parkzeit' text as decimal hours (start, end), else None.

    The text lists comma-separated intervals 'HH:MM-HH:MM'. The one interval without a day (or with 'Mo-Fr') is read
    as the weekday window: the layer names no day for it, which this step reads as Monday to Friday; an interval after
    'Sa' is the Saturday window, which the model's average weekday does not use (D1). Any other text, more than one
    weekday interval or a window outside 0 <= start < end <= 24 h gives None (the caller reports it).
    """
    weekday = []
    for part in str(text).split(","):
        part = part.strip()
        match = _WEEKDAY_WINDOW.match(part)
        if match:
            hours = [int(value) for value in match.groups()]
            weekday.append((hours[0] + hours[1] / 60.0, hours[2] + hours[3] / 60.0))
        elif not _SATURDAY_WINDOW.match(part):
            return None
    if len(weekday) != 1:
        return None
    start, end = weekday[0]
    return (start, end) if 0.0 <= start < end <= 24.0 else None


def tariff_zone_evidence(sections: gpd.GeoDataFrame) -> dict:
    """tariff zone (int) -> the tariff values the layer attributes support, and the tallies behind them.

    ``hourly_rate_eur``: the one rate of the zone (a zone with two rates, or a rate that contradicts its own price text
    price_raw, raises). ``fee_start_h``/``fee_end_h``: the weekday window of most sections (ties: more area), the
    dominant regime of assumption S1, with ``window_sections`` of ``sections`` and ``window_area_share``; ``None`` when no
    window can be parsed (``unparsed_windows`` counts them); ``minority_windows`` names the other sections. ``max_stay_min``:
    only when every section states the same maximum duration (the package: an empty value is no evidence of unlimited
    parking), else ``None``, with the stated values ``max_stay_stated`` (minutes -> sections) and ``max_stay_unknown``.
    ``short_stay_sections``: sections flagged Kurzzeitparken (recorded, not modelled). ``window_texts``: the raw texts.
    """
    evidence = {}
    for key, group in sections.groupby("fee_zone"):
        key = int(key)
        rates = sorted({round(float(value), 2) for value in group["hourly_rate_eur"]})
        if len(rates) != 1:
            raise SystemExit(f"tariff zone {key}: hourly_rate_eur {rates}; a tariff zone of the layer has one rate")
        for text in group["price_raw"]:
            match = _PRICE.match(str(text))
            if not match or not math.isclose(float(match.group(1).replace(",", ".")), rates[0], abs_tol=1e-9):
                raise SystemExit(f"tariff zone {key}: price_raw {text!r} contradicts hourly_rate_eur {rates[0]}")
        windows = [parse_weekday_window(text) for text in group["paid_hours_raw"]]
        tally = {}
        for window, geometry in zip(windows, group.geometry):
            entry = tally.setdefault(window, [0, 0.0])
            entry[0] += 1
            entry[1] += float(geometry.area)
        parsed = {window: entry for window, entry in tally.items() if window is not None}
        area = float(group.geometry.area.sum())
        dominant = max(parsed, key=lambda w: (parsed[w][0], parsed[w][1], -w[0])) if parsed else None
        stated = Counter(int(value) for value in group["max_duration_minutes"] if pd.notna(value))
        unknown = int(group["max_duration_minutes"].isna().sum())
        evidence[key] = {
            "hourly_rate_eur": rates[0], "sections": len(group), "area_m2": area,
            "fee_start_h": dominant[0] if dominant else None, "fee_end_h": dominant[1] if dominant else None,
            "window_sections": parsed[dominant][0] if dominant else 0,
            "window_area_share": parsed[dominant][1] / area if dominant and area > 0 else None,
            "unparsed_windows": tally.get(None, [0])[0],
            "window_texts": dict(Counter(str(text) for text in group["paid_hours_raw"])),
            "minority_windows": sorted(f"{cc.ascii_transliteration(str(name))} ({text})" for name, text, window
                                       in zip(group["name"], group["paid_hours_raw"], windows) if window != dominant),
            "max_stay_min": next(iter(stated)) if unknown == 0 and len(stated) == 1 else None,
            "max_stay_stated": dict(sorted(stated.items())), "max_stay_unknown": unknown,
            "short_stay_sections": int(group["short_stay_available"].astype(bool).sum()),
        }
    return evidence


def check_tariff_rows(evidence: dict, tariffs: pd.DataFrame) -> None:
    """The Wolfsburg rows of ``tariffs`` are exactly the tariff zones of ``evidence`` and carry the layer's hourly rate,
    weekday window and maximum stay (empty where the layer states none uniformly), the billing unit of ASSUMPTION C-b
    (``WOLFSBURG_BILLING_UNIT_MIN``) and nothing in the columns no source fills (``WOLFSBURG_EMPTY_COLUMNS``); else
    ``SystemExit``."""
    rows = tariffs.set_index("zone_id")
    problems = []
    expected = {WOLFSBURG_ZONE_ID.format(key): key for key in evidence}
    present = {str(zone_id) for zone_id, ags in zip(tariffs["zone_id"], tariffs["municipality_ags"])
               if str(ags) == WOB_AGS}
    if present != set(expected):
        problems.append(f"Wolfsburg tariff rows {sorted(present)}, expected the tariff zones {sorted(expected)}")
    for zone_id, key in sorted(expected.items()):
        if zone_id not in rows.index:
            continue
        row, values = rows.loc[zone_id], evidence[key]
        for field in ("hourly_rate_eur", "fee_start_h", "fee_end_h"):
            if values[field] is None or not math.isclose(float(row[field]), float(values[field]), abs_tol=1e-9):
                problems.append(f"{zone_id}: {field} {row[field]} but the layer gives {values[field]}")
        maximum = row["max_stay_min"]
        if (values["max_stay_min"] is None) != pd.isna(maximum) or (
                values["max_stay_min"] is not None and int(maximum) != values["max_stay_min"]):
            problems.append(f"{zone_id}: max_stay_min {maximum} but the layer gives {values['max_stay_min']} (only a "
                            "maximum duration every section states is taken)")
        unit = row["billing_unit_min"]
        if pd.isna(unit) or int(unit) != WOLFSBURG_BILLING_UNIT_MIN:
            problems.append(f"{zone_id}: billing_unit_min {unit} but ASSUMPTION C-b gives {WOLFSBURG_BILLING_UNIT_MIN}")
        filled = [column for column in WOLFSBURG_EMPTY_COLUMNS if pd.notna(row[column])]
        if filled:
            problems.append(f"{zone_id}: {filled} must stay empty (no source publishes them for the street sections)")
    if problems:
        raise SystemExit("Wolfsburg tariff rows contradict the layer attributes:\n  " + "\n  ".join(problems))
    print("Wolfsburg tariff rows agree with the layer attributes: " + ", ".join(sorted(expected)))


def _polygonal(geometry):
    """The polygonal part of an overlay on the precision grid: snap rounding can collapse slivers into lines or points,
    which carry no area and would make the next overlay fail as mixed-dimension input."""
    if geometry.is_empty or geometry.geom_type in ("Polygon", "MultiPolygon"):
        return geometry
    parts = [part for part in shapely.get_parts(geometry) if part.geom_type in ("Polygon", "MultiPolygon")]
    return shapely.union_all(parts, grid_size=SPLIT_GRID_M) if parts else shapely.Polygon()


def _on_grid(operation, *geometries):
    """``operation`` (shapely.intersection, difference, union or union_all) on ``SPLIT_GRID_M``, polygonal part only."""
    return _polygonal(operation(*geometries, grid_size=SPLIT_GRID_M))


def _boundary_samples(geometry, window, spacing_m: float) -> np.ndarray:
    """Coordinates every ``spacing_m`` (and at every vertex) along the boundary of ``geometry`` inside ``window``."""
    if geometry.is_empty:
        return np.empty((0, 2))
    boundary = geometry.boundary.intersection(window)
    if boundary.is_empty:
        return np.empty((0, 2))
    return shapely.get_coordinates(shapely.segmentize(boundary, spacing_m))


def _nearest_regions(own: dict, ranking: list, region, buffer_m: float, spacing_m: float) -> tuple:
    """key -> the part of the plane nearer to ``own[key]`` than to the sections of every other key, as seen from the
    points of ``region``; and the number of boundary samples.

    A generalised Voronoi split by sampling: the boundaries of the sections are sampled every ``spacing_m`` (only within
    ``buffer_m`` plus two spacings of ``region``: a point of ``region`` lies within ``buffer_m`` of its nearest section,
    so every sample that can be nearest is kept), snapped to ``SPLIT_GRID_M`` and reduced to one sample per coordinate,
    which keeps the higher-ranked key; the Voronoi cells of the samples are labelled with the key of their sample and
    united per key. A cell is the set of points nearer to its own sample than to any other, so the sample nearest to
    the cell's centroid (inside the cell: Voronoi cells are convex) is the cell's own sample; this labelling needs no
    point-in-polygon test on the cell boundary, where near-coincident samples made one fail. Outside the sections the
    distance to a section is the distance to its boundary, so the split line lies within half a spacing of the exact
    one.
    """
    window = region.buffer(buffer_m + 2.0 * spacing_m)
    coordinates, labels = [], []
    for rank, key in enumerate(ranking):
        samples = _boundary_samples(own[key], window, spacing_m)
        coordinates.append(samples)
        labels.append(np.full(len(samples), rank))
    coordinates = np.round(np.vstack(coordinates) / SPLIT_GRID_M) * SPLIT_GRID_M
    labels = np.concatenate(labels)
    order = np.lexsort((labels, coordinates[:, 1], coordinates[:, 0]))
    coordinates, labels = coordinates[order], labels[order]
    keep = np.ones(len(coordinates), dtype=bool)
    keep[1:] = np.any(coordinates[1:] != coordinates[:-1], axis=1)
    coordinates, labels = coordinates[keep], labels[keep]
    points = shapely.points(coordinates)
    cells = shapely.get_parts(shapely.voronoi_polygons(shapely.multipoints(points), extend_to=window))
    cell_index, sample_index = shapely.STRtree(points).query_nearest(shapely.centroid(cells), all_matches=False)
    if not np.array_equal(cell_index, np.arange(len(cells))):
        raise SystemExit(f"nearest-section split: {len(cells) - len(np.unique(cell_index))} of {len(cells)} Voronoi "
                         "cells found no nearest sample")
    cell_labels = labels[sample_index]
    regions = {key: _on_grid(shapely.union_all, cells[cell_labels == rank]) for rank, key in enumerate(ranking)}
    return regions, len(points)


def split_by_nearest_section(sections: dict, ranking: list, buffer_m: float = SECTION_BUFFER_M,
                             spacing_m: float = SAMPLE_SPACING_M) -> dict:
    """One zone per key: the area within ``buffer_m`` of ``sections[key]``, split by the nearer section where the
    areas of two keys overlap (spec Amendment C2).

    ``ranking`` lists the keys from the highest tariff to the lowest. Ties go to the higher tariff: a key competes with
    its sections minus every higher-ranked section (``own``), which is the same nearest rule with the ties at distance 0
    (overlapping sections, and the points equidistant from both) resolved upwards. A point that only one key's area
    covers belongs to that key; a point that two keys' areas cover (``contested``) to the key of the nearest section
    (``_nearest_regions``), the sections themselves to their key. Every overlay runs on the precision grid
    ``SPLIT_GRID_M``, so that sections touching or overlapping with float noise cannot break it. Returns {"zones",
    "buffers", "contested" (per key: the part of its area another key's area covers as well), "contested_area" (their
    union), "competitors" (per key: the keys whose area overlaps its own), "samples"}.
    """
    own, higher = {}, None
    for key in ranking:
        own[key] = sections[key] if higher is None else _on_grid(shapely.difference, sections[key], higher)
        higher = sections[key] if higher is None else _on_grid(shapely.union, higher, sections[key])
    buffers = {key: sections[key].buffer(buffer_m) for key in ranking}
    contested, competitors = {}, {}
    for key in ranking:
        competitors[key] = [other for other in ranking if other != key
                            and _on_grid(shapely.intersection, buffers[key], buffers[other]).area > 0.0]
        contested[key] = (_on_grid(shapely.intersection, buffers[key],
                                   _on_grid(shapely.union_all, [buffers[other] for other in competitors[key]]))
                          if competitors[key] else shapely.Polygon())
    contested_area = _on_grid(shapely.union_all, list(contested.values()))
    zones = {key: _on_grid(shapely.difference, buffers[key], contested[key]) for key in ranking}
    samples = 0
    if not contested_area.is_empty:
        regions, samples = _nearest_regions(own, ranking, contested_area, buffer_m, spacing_m)
        every_section = _on_grid(shapely.union_all, list(own.values()))
        for key in ranking:
            inside = _on_grid(shapely.intersection, own[key], contested_area)
            outside = _on_grid(shapely.intersection, regions[key], contested_area)
            outside = _on_grid(shapely.difference, _on_grid(shapely.intersection, outside, buffers[key]), every_section)
            zones[key] = _on_grid(shapely.union_all, [zones[key], inside, outside])
    return {"zones": zones, "buffers": buffers, "contested": contested, "contested_area": contested_area,
            "competitors": competitors, "samples": samples}


def wolfsburg_tariff_zones(inputs: dict, *, simplify_m: float, clearance_m: float, opening_m: float,
                           minimum_part_m2: float) -> dict:
    """C2: the tariff zones of the Wolfsburg street sections, ranked by hourly rate (highest first).

    ``simplify_m``, ``clearance_m``, ``opening_m`` and ``minimum_part_m2`` are the assembly's treatment of the polygons
    after the split (stated in the notes, like every parameter that shaped a polygon). Returns
    {"ranking", "evidence" (``tariff_zone_evidence``), "sections" (key -> union of its sections), "split"
    (``split_by_nearest_section``), "zone_ids", "notes", "source_url"}.
    """
    frame = inputs["wob_sections"]
    evidence = tariff_zone_evidence(frame)
    ranking = sorted(evidence, key=lambda key: (-evidence[key]["hourly_rate_eur"], key))
    sections = {key: unary_union(list(frame.loc[frame["fee_zone"] == key, "geometry"])) for key in ranking}
    split = split_by_nearest_section(sections, ranking)
    sha = inputs["files"]["Wolfsburg_Parkdaten"]["sha256"]
    source_url = _single(frame["source_url"], "mobile_parking_areas source_url")
    notes, zone_ids = {}, {}
    for key in ranking:
        zone_ids[key] = WOLFSBURG_ZONE_ID.format(key)
        values, competitors = evidence[key], split["competitors"][key]
        contest = (f"where it overlaps the area of tariff zone {', '.join(str(other) for other in competitors)}, the "
                   f"nearer street section decides (section boundaries sampled every {SAMPLE_SPACING_M:g} m; ties to "
                   "the higher tariff)" if competitors else "no other tariff zone's area overlaps it")
        notes[key] = (f"{WOLFSBURG_PROVENANCE}. Tariff zone {key} (attribute Parkzone) of the layer "
                      f"digitalisierung_handyparkzonen_a (Handyparkzonen; package Wolfsburg_Parkdaten.zip, SHA-256 "
                      f"{sha[:12]}..., retrieved 2026-10-01 from {source_url}): the area within {SECTION_BUFFER_M:.0f} "
                      f"m (ASSUMPTION C-a: the access walk from the destination to a paid street section) of its "
                      f"{values['sections']} street-section polygons; {contest}; simplified {simplify_m:g} m and cut "
                      f"in tariff order with a {clearance_m:g} m clearance, opened by {opening_m:g} m (parts narrower "
                      f"than {2.0 * opening_m:g} m removed), parts below {minimum_part_m2:g} m2 dropped. QA rows "
                      f"{zone_ids[key]}_* of parking_zones_2026_municipal_qa.csv.")
    return {"ranking": ranking, "evidence": evidence, "sections": sections, "split": split, "zone_ids": zone_ids,
            "notes": notes, "source_url": source_url}


def print_tariff_evidence(evidence: dict) -> None:
    """The curator's console view of the Wolfsburg tariff evidence (transcribed into parking_tariffs_2026.csv)."""
    for key, values in sorted(evidence.items()):
        window = ("none parsed" if values["fee_start_h"] is None else
                  f"{values['fee_start_h']:g}-{values['fee_end_h']:g} h on {values['window_sections']} of "
                  f"{values['sections']} sections ({100.0 * values['window_area_share']:.1f} % of their area)")
        print(f"Wolfsburg tariff zone {key}: {values['sections']} sections {values['area_m2']:.0f} m2, "
              f"{values['hourly_rate_eur']:.2f} EUR/h; weekday window {window}; unparsed {values['unparsed_windows']}; "
              f"texts {values['window_texts']}; other windows {values['minority_windows']}; maximum stay "
              f"{values['max_stay_min']} (stated {values['max_stay_stated']}, unknown {values['max_stay_unknown']}); "
              f"short stay on {values['short_stay_sections']} sections")


# ---------------------------------------------------------------- QA table

#: The header text before the column definitions, wrapped by ``write_qa_table``.
QA_INTRO = (
    "Curation QA of the municipal parking data (parking cost zones v2, spec Amendment C, issue #436): one row per "
    "comparison of a subject area with a reference area, written by "
    "scripts/curation/parking_zones_2026/assemble_parking_zones.py --municipal-dir (municipal_zones.py) from the "
    "owner-supplied packages under raw_sources/municipal_2026-10-01/ (gitignored; SHA-256 in the data record "
    "parking_zones_2026). Sources (controller ruling R-C1): Braunschweig: " + BRAUNSCHWEIG_PROVENANCE + ". Wolfsburg: "
    + WOLFSBURG_PROVENANCE + ". Goslar (cross-check only, no geometry or tariff change): " + GOSLAR_PROVENANCE + ". "
    "C1: zones 1a and 1b replace the geometry of bs_zone_ia and bs_zone_ib; 'v1' names the zones the curation chain "
    "builds before this step (the tracings of the city's overview map), compared before the precedence cuts, which "
    "both include the BgA car parks. C2: the tariff zones wob_tarifzone_<n> replace wob_innenstadt; each is the area "
    f"within {SECTION_BUFFER_M:.0f} m (ASSUMPTION C-a) of the street sections of tariff zone <n>, split by the nearer "
    "section where the areas of two tariff zones overlap (ties to the higher tariff). C4: gs_altstadt_zone1 stays. "
    "Release polygons are measured in the written zone file (scripts/validate_parking_zones.py compares them, "
    "braunschweig.parking.municipal_zone_qa.validate_municipal_qa). Units m2 (EPSG:25832, 0.1 m2), shares 0 to 1 "
    "(6 decimals, from the unrounded areas), empty = undefined. Columns:"
)
#: Width of the wrapped header lines (without the leading '# ').
QA_HEADER_WIDTH = 116
QA_COLUMN_GLOSSARY = {
    "row_id": "stable identifier of the comparison (<subject>_vs_<reference>)",
    "municipality_ags": "8-digit AGS of the municipality",
    "release_zone_id": "the release polygon that is the subject (its area is checked against the zone file); empty "
                       "when the subject is a package layer",
    "subject": "the measured area in words",
    "reference": "the area it is compared with in words",
    "subject_area_m2": "area of the subject, m2",
    "reference_area_m2": "area of the reference (union of its features), m2",
    "overlap_area_m2": "area of subject and reference together, m2",
    "subject_share_in_reference": "overlap_area_m2 / subject_area_m2 (the share of the subject inside the reference)",
    "reference_share_in_subject": "overlap_area_m2 / reference_area_m2 (the share of the reference inside the subject)",
    "reference_features": "number of source features (or derived areas) that make up the reference",
    "reference_features_overlapping": "of them the features whose overlap with the subject has a positive area",
    "note": "what the comparison shows, the method and the caveats in words",
}


def _area(geometry) -> float:
    return 0.0 if geometry is None or geometry.is_empty else float(geometry.area)


def _row(row_id: str, ags: str, subject: str, reference: str, subject_geometry, features: list, *, note: str,
         release_zone_id: str = "") -> dict:
    """One QA row: ``subject_geometry`` against the union of ``features`` (the reference, empty list = empty)."""
    reference_geometry = unary_union(features) if features else None
    overlap = 0.0 if reference_geometry is None else float(subject_geometry.intersection(reference_geometry).area)
    subject_area, reference_area = _area(subject_geometry), _area(reference_geometry)
    overlapping = sum(1 for feature in features if subject_geometry.intersection(feature).area > 0.0)
    return {"row_id": row_id, "municipality_ags": ags, "release_zone_id": release_zone_id, "subject": subject,
            "reference": reference, "subject_area_m2": f"{subject_area:.1f}",
            "reference_area_m2": f"{reference_area:.1f}", "overlap_area_m2": f"{overlap:.1f}",
            "subject_share_in_reference": f"{overlap / subject_area:.6f}" if subject_area > 0 else "",
            "reference_share_in_subject": f"{overlap / reference_area:.6f}" if reference_area > 0 else "",
            "reference_features": str(len(features)), "reference_features_overlapping": str(overlapping), "note": note}


def _features(geometry) -> list:
    return [] if geometry is None or geometry.is_empty else [geometry]


def qa_rows(context: dict, release: gpd.GeoDataFrame) -> list:
    """The rows of the municipal QA table from the step's ``context`` (``assemble_parking_zones.
    apply_municipal_packages``) and the ``release`` as loaded from the written zone file (EPSG:25832)."""
    if release.crs is None or release.crs.to_epsg() != 25832:
        raise SystemExit(f"the release must be measured in EPSG:25832, found {release.crs}")
    polygons = dict(zip(release["zone_id"].astype(str), release.geometry))
    inputs, v1, bs, wob = context["inputs"], context["v1"], context["braunschweig"], context["wolfsburg"]
    rows = []
    package = "package zone {code} (layer parking_fee_zones of Braunschweig_Parkzonen.gpkg)"
    for zone_id, values in bs.items():
        code = values["code"]
        rows.append(_row(f"{zone_id}_package_vs_v1", BS_AGS, package.format(code=code) + ", before the precedence cuts",
                         f"v1 {zone_id} (traced from the city's overview map on 2026-09-29), before the precedence cuts",
                         values["geometry"], _features(v1[zone_id]), note=(
                             f"C1: zone {code} replaces the geometry of {zone_id}; both areas include the BgA car parks "
                             "(the precedence cuts come later)")))
        if code == "1a":
            rows.append(_row(f"{zone_id}_reconstructed_section_vs_v1", BS_AGS,
                             "package layer reconstructed_section (the southern part of 1a after the Amtsblatt annex "
                             "of 2024-04-30)", f"v1 {zone_id} before the precedence cuts", reconstructed_section(inputs),
                             _features(v1[zone_id]), note=(
                                 "C1: the reconstruction is flagged in reconstructed_section_m2; the v1 tracing of the "
                                 "overview map covered only the overlap")))
    for zone_id, values in bs.items():
        rows.append(_row(f"{zone_id}_release_vs_package", BS_AGS,
                         f"release polygon {zone_id} (after the v1 precedence cuts)", package.format(code=values["code"]),
                         polygons[zone_id], _features(values["geometry"]), release_zone_id=zone_id, note=(
                             "C1: the difference is the zones cut out by the v1 precedence (the BgA car parks) and the "
                             f"{context['simplify_m']:g} m simplification")))
    v1_wob, split = v1[WOLFSBURG_V1_ZONE], wob["split"]
    for key in wob["ranking"]:
        zone_id, count = wob["zone_ids"][key], wob["evidence"][key]["sections"]
        rows.append(_row(f"{zone_id}_release_vs_v1", WOB_AGS, f"release polygon {zone_id}",
                         "v1 wob_innenstadt (OSM fee evidence in the centre window, 2026-09-29)", polygons[zone_id],
                         _features(v1_wob), release_zone_id=zone_id,
                         note="C2: the tariff zone against the v1 polygon it replaces"))
        rows.append(_row(f"{zone_id}_sections_vs_v1", WOB_AGS,
                         f"the {count} street sections of tariff zone {key} (layer mobile_parking_areas)",
                         "v1 wob_innenstadt", wob["sections"][key], _features(v1_wob), note=(
                             "C2: the street sections themselves; the release adds their 50 m area (ASSUMPTION C-a)")))
        rows.append(_row(f"{zone_id}_release_vs_50m_area", WOB_AGS, f"release polygon {zone_id}",
                         f"the area within {SECTION_BUFFER_M:.0f} m of the street sections of tariff zone {key}",
                         polygons[zone_id], _features(split["buffers"][key]), release_zone_id=zone_id, note=(
                             "C2: reference_share_in_subject is the share of its 50 m area the tariff zone keeps after "
                             f"the nearest-section split, the {context['simplify_m']:g} m simplification and the "
                             f"{100.0 * context['clearance_m']:g} cm cut clearance")))
        rows.append(_row(f"{zone_id}_release_vs_contested_area", WOB_AGS, f"release polygon {zone_id}",
                         f"the part of the 50 m area of tariff zone {key} that the 50 m area of another tariff zone "
                         "covers as well", polygons[zone_id], _features(split["contested"][key]),
                         release_zone_id=zone_id, note=(
                             "C2: the part of the contested area the nearer street sections give to this tariff zone"
                             if split["competitors"][key] else "C2: no other tariff zone's area overlaps this one")))
    goslar = polygons[GOSLAR_ZONE]
    resident = inputs["gs_resident"]
    per_area = []
    for code, description, geometry in zip(resident["Parkbereiche_Kennzeichen"], resident["Parkbereiche_Beschreibung"],
                                           resident.geometry):
        label = (str(code) if pd.notna(code) and str(code).strip() else
                 f"(no code: {cc.ascii_transliteration(str(description))})")
        per_area.append(f"{label} {goslar.intersection(geometry).area:.0f} of {geometry.area:.0f} m2")
    # spec Amendment D3 cuts the Goslar car parks at 1 EUR/h out of the polygon: it is then no longer 'unchanged'
    state = ("centre approximation, unchanged" if context.get("regional") is None else
             "centre approximation; the single-site car parks of spec Amendment D3 are cut out")
    rows.append(_row(f"{GOSLAR_ZONE}_vs_resident_areas", GS_AGS,
                     f"release polygon {GOSLAR_ZONE} ({state})",
                     "the resident parking areas of the Goslar service (layer resident_parking_zones)", goslar,
                     list(resident.geometry), release_zone_id=GOSLAR_ZONE, note=(
                         f"C4, {GOSLAR_PROVENANCE}; cross-check only, no geometry or tariff change. Overlap per area: "
                         + "; ".join(per_area))))
    facilities = inputs["gs_facilities"]
    for status in FACILITY_FEE_STATUSES:
        chosen = facilities[facilities["fee_status"] == status]
        rows.append(_row(f"{GOSLAR_ZONE}_vs_{status}_facility_areas", GS_AGS, f"release polygon {GOSLAR_ZONE}",
                         f"the parking facility areas of the Goslar service with fee_status {status} (layer "
                         "parking_facility_areas)", goslar, list(chosen.geometry), release_zone_id=GOSLAR_ZONE, note=(
                             f"C4, {GOSLAR_PROVENANCE}; cross-check only. fee_status is the package's reading of the "
                             "historical source attribute Hinweis (free = gebuehrenfrei, paid = gebuehrenpflichtig, "
                             "unknown = no fee hint)")))
    return rows


def write_qa_table(path, rows: list, intro: str = QA_INTRO) -> None:
    """The municipal QA table with its header: the intro (``QA_INTRO``, extended by the regional step) and one
    '# <column>: <definition>' line per column."""
    from braunschweig.parking import municipal_zone_qa as mq

    if set(QA_COLUMN_GLOSSARY) != set(mq.MUNICIPAL_QA_COLUMNS):
        raise SystemExit("QA_COLUMN_GLOSSARY and MUNICIPAL_QA_COLUMNS differ")
    header = [f"# {line}" for line in textwrap.wrap(intro, QA_HEADER_WIDTH, break_on_hyphens=False)]
    header += [f"# {column}: {QA_COLUMN_GLOSSARY[column]}" for column in mq.MUNICIPAL_QA_COLUMNS]
    table = pd.DataFrame(rows, columns=list(mq.MUNICIPAL_QA_COLUMNS))
    text = "\n".join(header) + "\n" + table.to_csv(index=False, lineterminator="\n")
    if not text.isascii():
        raise SystemExit("the municipal QA table must be ASCII")
    Path(path).write_text(text, encoding="utf-8", newline="\n")
    print(f"written {path} with {len(table)} rows")
