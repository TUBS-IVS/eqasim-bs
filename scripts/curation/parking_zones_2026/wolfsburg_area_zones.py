"""Wolfsburg area zones: the mobile-parking areas with a recorded maximum stay (parking cost zones v3, spec B2, issue #436).

Not part of the pipeline and never run by synpp: ``assemble_parking_zones.py --municipal-dir`` calls ``carve_area_zones``
after ``municipal_zones.wolfsburg_tariff_zones`` has built the three tariff zones. Input: the layer
``mobile_parking_areas`` (73 polygons, EPSG:25832) of the owner's package Wolfsburg_Parkdaten.zip, read from the zip by
``municipal_zones.load_packages`` after its SHA-256 check (never extracted into the repository).

* ``recorded_maximum_areas``: the areas with a recorded maximum stay (``max_duration_minutes``) are 15 of the 73; any
  other count stops with the list. The 58 areas without a recorded maximum are not carved out: they share the rule of
  their parent tariff zone (ASSUMPTION W-max, owner decision 2026-10-14: no recorded maximum reads as "long parking
  allowed"). The counts are logged.
* ``carve_area_zones`` (ruling R-B-12): the zone ``wob_area_<id>`` of an area is the part of its parent tariff zone (the
  50 m access area of ASSUMPTION C-a after the split between the tariff zones) that is nearer to the street section of
  that area than to any other section of the parent. It is the same generalised Voronoi split that
  ``municipal_zones.split_by_nearest_section`` applies between tariff zones (same sampling spacing, same precision grid;
  where two sections overlap, the area wins), not the raw section polygon: street sections are thin, activity points lie
  beside them and would almost never fall inside, so the maximum stay would never act. The parent loses exactly the
  area zones, so zones never overlap.
* ``area_tariff_rows``: one tariff row per area zone with the values of the parent row (rate, unit, fee window, empty
  product and EV columns), its own ``max_stay_min`` and the raw paid-hours text of the area compared with the parent
  window (a difference is named in the note, the window column is not changed: fee windows and maximum stays are separate
  columns and nothing is inferred from the other). ``write_area_rows`` inserts the rows into the committed tariff table,
  ``check_area_tariff_rows`` checks them against the layer.

``wolfsburg_area_release.py`` applies the same derivation to a FINISHED zone release (the full chain needs inputs that are
not on every machine) and writes the area rows into the committed tariff table: that is the command to run.
CRS: EPSG:25832 throughout, areas in m2, distances in m.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

import curation_common as cc
import municipal_zones as mz

#: Release zone id of an area (``source_id`` of the layer, lower-case token) and its prefix in the tariff table.
AREA_ZONE_ID_PREFIX = mz.WOLFSBURG_AREA_ZONE_ID_PREFIX
AREA_ZONE_ID = AREA_ZONE_ID_PREFIX + "{}"
#: Geometry source of an area zone (same vocabulary value as ``braunschweig.parking.zones.MUNICIPAL_AREA_GEOMETRY_SOURCE``).
AREA_GEOMETRY_SOURCE = "municipal_mobile_parking_area"
#: What the pinned layer holds (data record parking_zones_2026): any other count stops the step.
EXPECTED_AREA_COUNT, EXPECTED_RECORDED_MAXIMUM_COUNT = mz.WOLFSBURG_AREA_COUNTS
#: Columns of a Wolfsburg area row that stay empty like the parent row (no source publishes them for the street areas).
EMPTY_COLUMNS = ("free_if_stay_at_most_min", "first_period_min", "first_period_eur", "daily_cap_eur",
                 "long_stay_product_eur", "member_day_eur", "guest_day_eur", "commuter_day_eur", "resident_permits_valid",
                 "ev_free_minutes", "ev_rule_valid_until")
#: Values of a parent row that the area row copies verbatim.
COPIED_COLUMNS = ("municipality_ags", "zone_type", "workplace_class", "hourly_rate_eur", "billing_unit_min", "fee_start_h",
                  "fee_end_h", "resident_exempt", "source_url", "source_date", "valid_from", "fee_window_source")
#: Parent row id of a tariff zone key.
PARENT_ZONE_ID = mz.WOLFSBURG_ZONE_ID
#: Key of the sections of a parent that carry no recorded maximum, in the nearest-section split.
_REST_KEY = "rest"
_ID_TOKEN = re.compile(r"^[a-z0-9]+$")
#: ASSUMPTION C-b / provenance of the parent rows are in their notes; an area row names the parent instead of copying them.
_LAYER_NAME = "mobile_parking_areas"


# ---------------------------------------------------------------- the layer


def area_id(source_id) -> str:
    """The lower-case token of a layer ``source_id`` that names the zone; anything else stops (a zone id is a token)."""
    token = str(source_id).strip().lower()
    if not _ID_TOKEN.match(token):
        raise SystemExit(f"mobile_parking_areas source_id {source_id!r} is not a lower-case alphanumeric token: the zone "
                         f"id {AREA_ZONE_ID.format('<source_id>')} needs one")
    return token


def _describe(frame: gpd.GeoDataFrame) -> list:
    return [f"{area_id(row.source_id)} ({cc.ascii_transliteration(str(row.name))}, tariff zone {int(row.fee_zone)}, "
            f"{int(row.max_duration_minutes)} min)" for row in frame.itertuples()]


def recorded_maximum_areas(sections: gpd.GeoDataFrame, *, expected_areas: int = EXPECTED_AREA_COUNT,
                           expected_recorded: int = EXPECTED_RECORDED_MAXIMUM_COUNT) -> gpd.GeoDataFrame:
    """The areas of the layer with a recorded maximum stay (``max_duration_minutes`` set), counted and logged.

    The layer must hold ``expected_areas`` areas with unique ``source_id`` tokens and exactly ``expected_recorded`` of
    them a recorded maximum, else ``SystemExit`` with the list of the recorded ones. The others are not carved out
    (ASSUMPTION W-max).
    """
    ids = [area_id(value) for value in sections["source_id"]]
    duplicated = sorted({value for value in ids if ids.count(value) > 1})
    if duplicated:
        raise SystemExit(f"mobile_parking_areas source_id not unique: {duplicated}")
    recorded = sections[sections["max_duration_minutes"].notna()]
    if len(sections) != expected_areas or len(recorded) != expected_recorded:
        raise SystemExit(f"mobile_parking_areas holds {len(sections)} areas with {len(recorded)} areas with a recorded "
                         f"maximum stay, the pinned layer has {expected_areas} and {expected_recorded}; the recorded ones: "
                         + "; ".join(_describe(recorded)))
    print(f"Wolfsburg mobile-parking areas: {len(sections)} areas, {len(recorded)} with a recorded maximum carved out "
          f"as area zones, {len(sections) - len(recorded)} without (ASSUMPTION W-max: they stay in their tariff zone)")
    return recorded


# ---------------------------------------------------------------- carving


def _nonempty(geometry) -> bool:
    return geometry is not None and not geometry.is_empty and geometry.area > 0.0


def carve_area_zones(parent_zones: dict, sections: gpd.GeoDataFrame, ranking: list, *, buffer_m: float = mz.SECTION_BUFFER_M,
                     spacing_m: float = mz.SAMPLE_SPACING_M, expected_areas: int = None,
                     expected_recorded: int = None) -> dict:
    """Carve the areas with a recorded maximum stay out of their parent tariff zones (module docstring, ruling R-B-12).

    ``parent_zones``: tariff zone key -> polygon of the parent (EPSG:25832, the 50 m access area after the split between
    the tariff zones); ``sections``: the whole layer (``fee_zone`` = the tariff zone key); ``ranking``: the keys, highest
    tariff first. A key without an area with a recorded maximum is returned unchanged. ``expected_areas`` and
    ``expected_recorded`` default to the pinned layer's counts (``recorded_maximum_areas``) and are only given by the
    tests. Returns {"areas": [dict per area in tariff order, then id: area_id, zone_id, parent_key, parent_zone_id, name,
    source_id, max_stay_min, max_duration_raw, paid_hours_raw, short_stay_available, section_geometry, geometry (the
    zone), section_area_m2, parent_buffer_m], "parents": key -> remaining polygon, "samples": boundary samples}.
    Raises ``SystemExit`` when an area has no parent tariff zone, lies outside its parent, or a parent is left without
    area.
    """
    recorded = recorded_maximum_areas(
        sections, expected_areas=EXPECTED_AREA_COUNT if expected_areas is None else expected_areas,
        expected_recorded=EXPECTED_RECORDED_MAXIMUM_COUNT if expected_recorded is None else expected_recorded)
    strangers = sorted({int(key) for key in recorded["fee_zone"]} - set(parent_zones))
    if strangers:
        outside = [f"{area_id(row.source_id)} (tariff zone {int(row.fee_zone)})" for row in recorded.itertuples()
                   if int(row.fee_zone) in strangers]
        raise SystemExit(f"areas with a recorded maximum stay outside every parent tariff zone {sorted(parent_zones)}: "
                         + ", ".join(outside))
    areas, parents, samples = [], dict(parent_zones), 0
    for key in ranking:
        group = recorded[recorded["fee_zone"] == key].copy()
        if group.empty:
            continue
        group["area_id"] = [area_id(value) for value in group["source_id"]]
        group = group.sort_values("area_id")
        parent = parent_zones[key]
        rest_frame = sections[(sections["fee_zone"] == key) & sections["max_duration_minutes"].isna()]
        area_keys = list(group["area_id"])
        own, higher = {}, None
        for name, geometry in zip(area_keys, group.geometry):
            section = shapely.union_all([geometry])
            own[name] = section if higher is None else mz._on_grid(shapely.difference, section, higher)
            higher = section if higher is None else mz._on_grid(shapely.union, higher, section)
        names = list(area_keys)
        if len(rest_frame):
            rest = mz._on_grid(shapely.union_all, list(rest_frame.geometry))
            own[_REST_KEY] = mz._on_grid(shapely.difference, rest, higher)
            names.append(_REST_KEY)
        empty = [name for name in area_keys if not _nonempty(own[name])]
        if empty:
            raise SystemExit(f"tariff zone {key}: the section of area(s) {empty} is covered by the area(s) before it")
        regions, count = mz._nearest_regions(own, names, parent, buffer_m, spacing_m)
        samples += count
        every_section = mz._on_grid(shapely.union_all, list(own.values()))
        zones = {}
        for name in area_keys:
            inside = mz._on_grid(shapely.intersection, own[name], parent)
            near = mz._on_grid(shapely.intersection, regions[name], parent)
            zones[name] = mz._on_grid(shapely.union_all, [mz._on_grid(shapely.difference, near, every_section), inside])
        for row in group.itertuples():
            name = row.area_id
            if not _nonempty(zones[name]) or not zones[name].intersects(own[name]):
                raise SystemExit(f"area {name} ({cc.ascii_transliteration(str(row.name))}) lies outside the polygon of "
                                 f"its parent tariff zone {key}: nothing of it is left to carve")
            areas.append({"area_id": name, "zone_id": AREA_ZONE_ID.format(name), "parent_key": key,
                          "parent_zone_id": PARENT_ZONE_ID.format(key), "name": str(row.name),
                          "source_id": str(row.source_id), "max_stay_min": int(row.max_duration_minutes),
                          "max_duration_raw": str(row.max_duration_raw), "paid_hours_raw": str(row.paid_hours_raw),
                          "short_stay_available": bool(row.short_stay_available), "section_geometry": row.geometry,
                          "geometry": zones[name], "section_area_m2": float(row.geometry.area), "parent_buffer_m": buffer_m})
        taken = mz._on_grid(shapely.union_all, [zones[name] for name in area_keys])
        remainder = mz._on_grid(shapely.difference, parent, taken)
        if not _nonempty(remainder):
            raise SystemExit(f"tariff zone {key}: nothing left after the area zones {area_keys} are carved out")
        parents[key] = remainder
    print("Wolfsburg area zones: " + "; ".join(
        f"{area['zone_id']} {area['geometry'].area:.0f} m2 of tariff zone {area['parent_key']} (maximum "
        f"{area['max_stay_min']} min)" for area in areas) + "; parents after the carving: " + "; ".join(
        f"{PARENT_ZONE_ID.format(key)} {parent_zones[key].area:.0f} -> {parents[key].area:.0f} m2"
        for key in ranking if key in parents) + f"; {samples} boundary samples")
    return {"areas": areas, "parents": parents, "samples": samples}


def area_note(area: dict, *, simplify_m: float, clearance_m: float, opening_m: float, minimum_part_m2: float,
              sha256: str) -> str:
    """The digitising note of an area zone polygon (method, source, parameters)."""
    return (f"{mz.WOLFSBURG_PROVENANCE}. Area {area['source_id']} ({cc.ascii_transliteration(area['name'])}) of the layer "
            f"{_LAYER_NAME} (Handyparkzonen; package Wolfsburg_Parkdaten.zip, SHA-256 {sha256[:12]}...) with a recorded "
            f"maximum stay of {area['max_stay_min']} min (layer field max_duration_raw {area['max_duration_raw']}), tariff zone "
            f"{area['parent_key']}: the part of the {area['parent_buffer_m']:.0f} m access area of tariff zone "
            f"{area['parent_key']} (ASSUMPTION C-a) that is nearer to the street section of this area than to any other "
            f"section of that tariff zone (spec v3 B2, ruling R-B-12; section boundaries sampled every "
            f"{mz.SAMPLE_SPACING_M:g} m; where two sections overlap, the area wins), the street section itself "
            f"({area['section_area_m2']:.0f} m2) included; the parent polygon {area['parent_zone_id']} loses exactly this "
            f"part; simplified {simplify_m:g} m and cut with a {clearance_m:g} m clearance, opened by {opening_m:g} m, parts "
            f"below {minimum_part_m2:g} m2 dropped. QA rows {area['zone_id']}_* of parking_zones_2026_municipal_qa.csv.")


# ---------------------------------------------------------------- tariff rows


def _window_text(start, end) -> str:
    return f"{float(start):g}-{float(end):g} h"


def _paid_hours_sentence(area: dict, parent: pd.Series) -> str:
    """The raw paid-hours text of the area against the window of the parent row, named on difference."""
    text = area["paid_hours_raw"]
    parent_window = _window_text(parent["fee_start_h"], parent["fee_end_h"])
    window = mz.parse_weekday_window(text)
    if window is None:
        return (f"Paid hours of this area (attribute Kostenpflichtige Parkzeit): {text!r}, which gives no single weekday "
                f"window: DIFFERS from the window {parent_window} of {area['parent_zone_id']}; the window columns keep the "
                "parent value (fee windows are not changed here).")
    if (abs(window[0] - float(parent["fee_start_h"])) > 1e-9 or abs(window[1] - float(parent["fee_end_h"])) > 1e-9):
        return (f"Paid hours of this area (attribute Kostenpflichtige Parkzeit): {text!r}, which gives the weekday window "
                f"{_window_text(*window)}: DIFFERS from the window {parent_window} of {area['parent_zone_id']}; the window "
                "columns keep the parent value (fee windows are not changed here).")
    return (f"Paid hours of this area (attribute Kostenpflichtige Parkzeit): {text!r}, which gives the weekday window "
            f"{parent_window} and agrees with the window of {area['parent_zone_id']} (the Saturday interval is not "
            "modelled, D1).")


def area_tariff_rows(tariffs: pd.DataFrame, areas: list, *, package_sha256: str) -> pd.DataFrame:
    """One tariff row per area zone, in the column order of ``tariffs`` (all text, as the committed table spells it).

    The values are those of the parent row (``COPIED_COLUMNS``; the columns of ``EMPTY_COLUMNS`` stay empty like the
    parent's, no EV exemption is verified for Wolfsburg); ``max_stay_min`` is the area's own recorded maximum; the note
    names the parent, ASSUMPTION W-max and ASSUMPTION M3, the source and the raw paid-hours text compared with the parent
    window. A missing parent row stops.
    """
    parents = tariffs.set_index("zone_id")
    missing = sorted({area["parent_zone_id"] for area in areas} - set(parents.index))
    if missing:
        raise SystemExit(f"the tariff table lacks the parent row(s) {missing} of the Wolfsburg area zones")
    rows = []
    for area in areas:
        parent = parents.loc[area["parent_zone_id"]]
        row = {column: "" for column in tariffs.columns}
        for column in COPIED_COLUMNS:
            row[column] = parent[column]
        name = cc.ascii_transliteration(area["name"])
        row.update({
            "zone_id": area["zone_id"],
            "name": f"Wolfsburg Handyparkzone area {area['source_id']} ({name}), tariff zone {area['parent_key']}",
            "max_stay_min": str(area["max_stay_min"]),
            "notes": (
                f"{mz.WOLFSBURG_PROVENANCE}. Area zone of the area {area['source_id']} ({name}) of the layer "
                f"{_LAYER_NAME} (Handyparkzonen; owner-supplied package Wolfsburg_Parkdaten.zip of 2026-10-01, SHA-256 "
                f"{package_sha256[:12]}...), carved out of its parent tariff zone {area['parent_zone_id']} (spec v3 B2, "
                f"scripts/curation/parking_zones_2026/wolfsburg_area_zones.py): rate, billing unit, fee window and "
                f"every empty product or EV column are the values of the parent row (see its note for their source, "
                f"including ASSUMPTION C-b). The layer records a maximum stay of {area['max_stay_min']} min for this area "
                f"(layer field max_duration_raw {area['max_duration_raw']}), so it is a zone of its own instead of sharing "
                f"the parent's rule (ASSUMPTION W-max: only the areas WITHOUT a recorded maximum stay stay in the parent "
                f"and allow long parking). ASSUMPTION M3: no street option for a stay whose chargeable duration exceeds "
                f"the maximum, no long-stay product exists. "
                + _paid_hours_sentence(area, parent) + " Kurzzeitparken flag of the area: "
                + ("yes" if area["short_stay_available"] else "no") + " (its conditions are not in the layer and it is not "
                "modelled). No EV exemption is verified for Wolfsburg, so none is entered. Geometry: the part of the parent's "
                f"{area['parent_buffer_m']:.0f} m access area nearer to this street section than to any other section of "
                "the parent (ASSUMPTION C-a, ruling R-B-12); the polygon note of the zone file has the parameters."),
        })
        rows.append(row)
    return pd.DataFrame(rows, columns=list(tariffs.columns))


def _first_field(line: str) -> str:
    return line.split(",", 1)[0]


def write_area_rows(path, rows: pd.DataFrame) -> None:
    """Insert ``rows`` into the committed tariff table right after the last Wolfsburg tariff zone row (the existing
    ``wob_area_`` rows are replaced, so the call is idempotent); comments, other rows and the line layout stay as they are
    (LF line ends, minimal quoting)."""
    path = Path(path)
    lines = path.read_text(encoding="utf-8").split("\n")
    trailing = lines[-1] == ""
    if trailing:
        lines = lines[:-1]
    lines = [line for line in lines if not _first_field(line).startswith(AREA_ZONE_ID_PREFIX)]
    anchors = [index for index, line in enumerate(lines) if re.match(r"wob_tarifzone_\d+,", line)]
    if not anchors:
        raise SystemExit(f"{path}: no wob_tarifzone_<n> row to insert the area rows after")
    new_lines = []
    for record in rows.itertuples(index=False):
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="").writerow(list(record))
        new_lines.append(buffer.getvalue())
    if not "\n".join(new_lines).isascii():
        raise SystemExit("the area tariff rows must be ASCII")
    lines = lines[:anchors[-1] + 1] + new_lines + lines[anchors[-1] + 1:]
    path.write_text("\n".join(lines) + ("\n" if trailing else ""), encoding="utf-8", newline="\n")
    print(f"written {len(new_lines)} Wolfsburg area rows into {path}")


def _empty(value) -> bool:
    return bool(pd.isna(value)) or str(value).strip() in ("", "nan")


def _same(first, second) -> bool:
    """Two table cells agree: both empty, both the same number (the table is read as text or typed), or the same text."""
    if _empty(first) or _empty(second):
        return _empty(first) and _empty(second)
    try:
        return abs(float(first) - float(second)) < 1e-9
    except (TypeError, ValueError):
        return str(first).strip() == str(second).strip()


def check_area_tariff_rows(areas: list, tariffs: pd.DataFrame) -> None:
    """The ``wob_area_`` rows of ``tariffs`` are exactly the area zones of ``areas`` and carry the values of their parent
    row, their own maximum stay and nothing in the columns of ``EMPTY_COLUMNS``; else ``SystemExit`` listing every
    problem."""
    rows = tariffs.set_index("zone_id")
    expected = {area["zone_id"]: area for area in areas}
    present = {str(zone_id) for zone_id in tariffs["zone_id"] if str(zone_id).startswith(AREA_ZONE_ID_PREFIX)}
    problems = []
    if present != set(expected):
        problems.append(f"Wolfsburg area rows {sorted(present)}, expected {sorted(expected)}")
    for zone_id, area in sorted(expected.items()):
        if zone_id not in rows.index or area["parent_zone_id"] not in rows.index:
            continue
        row, parent = rows.loc[zone_id], rows.loc[area["parent_zone_id"]]
        for column in COPIED_COLUMNS:
            if not _same(row[column], parent[column]):
                problems.append(f"{zone_id}: {column} {row[column]!r} but its parent {area['parent_zone_id']} has "
                                f"{parent[column]!r}")
        if not _same(row["max_stay_min"], area["max_stay_min"]):
            problems.append(f"{zone_id}: max_stay_min {row['max_stay_min']!r} but the layer records "
                            f"{area['max_stay_min']}")
        filled = [column for column in EMPTY_COLUMNS if column in row.index and not _empty(row[column])]
        if filled:
            problems.append(f"{zone_id}: {filled} must stay empty like the parent row")
    if problems:
        raise SystemExit("Wolfsburg area tariff rows contradict the layer or their parents:\n  " + "\n  ".join(problems))
    print("Wolfsburg area tariff rows agree with the layer: " + ", ".join(sorted(expected)))


# ---------------------------------------------------------------- QA rows


def qa_rows(context: dict, release: gpd.GeoDataFrame) -> list:
    """Two QA rows per area zone for the municipal QA table (``municipal_zones._row`` layout): the release polygon against
    the street section of its area, and against the 50 m access area of its parent tariff zone. ``context`` is the
    assembly's municipal context (``context["wolfsburg_areas"]`` from ``carve_area_zones``), ``release`` the written zone
    file in EPSG:25832."""
    polygons = dict(zip(release["zone_id"].astype(str), release.geometry))
    buffers = context["wolfsburg"]["split"]["buffers"]
    rows = []
    for area in context["wolfsburg_areas"]["areas"]:
        zone_id = area["zone_id"]
        rows.append(mz._row(
            f"{zone_id}_release_vs_street_section", mz.WOB_AGS, f"release polygon {zone_id}",
            f"the street section of area {area['source_id']} (layer mobile_parking_areas)", polygons[zone_id],
            [area["section_geometry"]], release_zone_id=zone_id,
            note=(f"B2: the area zone against its own street section (recorded maximum stay {area['max_stay_min']} min): "
                  "subject_share_in_reference is the share of the zone that is the section itself, "
                  "reference_share_in_subject the share of the section the polygon covers (below 1 where the precedence "
                  "cut clearance or the simplification takes a strip)")))
        rows.append(mz._row(
            f"{zone_id}_release_vs_parent_50m_area", mz.WOB_AGS, f"release polygon {zone_id}",
            f"the area within {area['parent_buffer_m']:.0f} m of the street sections of tariff zone {area['parent_key']} "
            f"(the parent {area['parent_zone_id']} before the carving)", polygons[zone_id],
            mz._features(buffers[area["parent_key"]]), release_zone_id=zone_id,
            note=("B2: subject_share_in_reference is the share of the area zone inside the 50 m access area of its parent "
                  "tariff zone (the part nearer to this street section than to any other section of the parent, ruling "
                  "R-B-12; 1 up to the simplification and the cut clearance)")))
    return rows
