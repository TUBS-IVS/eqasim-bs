"""Municipal parking packages in the parking zone curation (parking cost zones v2, spec Amendment C, issue #436).

The owner's packages are gitignored, so the curation step is pinned on synthetic packages written the way the owner
supplied them (a zip per municipality holding a GeoPackage in EPSG:25832): C1, the Braunschweig fee zones 1a and 1b
replace the geometry of bs_zone_ia and bs_zone_ib, the v1 precedence still cuts the BgA car parks out and the
reconstructed southern section is flagged; C2, three Wolfsburg tariff zones replace wob_innenstadt, each the area
within 50 m (ASSUMPTION C-a) of its street sections, split by the nearer section where two 50 m areas overlap (ties to
the higher tariff); C4, Goslar is a cross-check only. The release must pass ``load_zone_polygons`` and the municipal
QA table ``municipal_zone_qa.validate_municipal_qa``. The tariff evidence of the Wolfsburg rows comes from the layer
attributes, every value either uniform (or the dominant regime of assumption S1) or left empty.
"""
from __future__ import annotations

import hashlib
import importlib.util
import math
import random
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely import affinity
from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union

from braunschweig.parking import municipal_zone_qa as mq
from braunschweig.parking import zones as pz

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = REPO_ROOT / "scripts" / "curation" / "parking_zones_2026"
METRIC_CRS = "EPSG:25832"
X0, Y0 = 604_000.0, 5_789_000.0
BS, WOB, GS = "03101000", "03103000", "03153017"
#: The Wolfsburg and Goslar layouts lie far from Braunschweig, so no 50 m area or precedence cut reaches another town.
WOB_X, GS_X = 20_000.0, 40_000.0
#: A release polygon read back from the file differs from its metric geometry by the WGS84 rounding (7 decimals, about
#: 1 cm): a few m2 on 1 km2.
FILE_ROUNDING = 1e-5
#: The assembly simplifies the buffered municipal zones by 0.5 m, which cuts up to 0.5 % off a round 50 m buffer cap.
SIMPLIFIED_BUFFER = 1e-2


def _box(x0, y0, x1, y1, dx=0.0):
    return box(X0 + dx + x0, Y0 + y0, X0 + dx + x1, Y0 + y1)


def _point(x, y, dx=0.0):
    return Point(X0 + dx + x, Y0 + y)


@pytest.fixture(scope="module")
def assembly():
    """The assembly script as a module (it imports curation_common and municipal_zones from its own directory)."""
    sys.path.insert(0, str(CURATION_DIR))
    try:
        spec = importlib.util.spec_from_file_location("assemble_parking_zones_municipal_under_test",
                                                      CURATION_DIR / "assemble_parking_zones.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(CURATION_DIR))
    return module


# --------------------------------------------------------------------------- synthetic packages

BS_FEE_ATTRIBUTES = {"zone_type": "fee", "source_date": "2025-11-26; southern continuation 2024-04-30",
                     "digitized_on": "2026-10-01", "estimated_accuracy_m": 25, "registration_rmse_m": 4.53}
#: The two strips of tariff zones 1 and 2 lie 60 m apart, so their 50 m areas overlap in a 40 m band split at y = 40.
STRIP_1, STRIP_2 = _box(0, 0, 300, 10, WOB_X), _box(0, 70, 300, 80, WOB_X)
#: Overlapping sections of tariff zones 1 and 2: the overlap and the points above it go to the higher tariff.
TIE_1, TIE_2 = _box(1000, 500, 1100, 510, WOB_X), _box(1050, 500, 1150, 510, WOB_X)
#: A lone section of tariff zone 3: nothing competes with its 50 m area.
LONE_3 = _box(5000, 0, 5100, 10, WOB_X)


def _wob_section(geometry, fee_zone, name, *, window="07:00-18:00, Sa 07:00-14:00", maximum=None, short=False):
    price = {1: "2 \N{EURO SIGN} pro Stunde", 2: "1,20 \N{EURO SIGN} pro Stunde", 3: "1 \N{EURO SIGN} pro Stunde"}[fee_zone]
    rate = {1: 2.0, 2: 1.2, 3: 1.0}[fee_zone]
    return {"name": name, "max_duration_raw": "NULL" if maximum is None else f"{maximum // 60}h{maximum % 60:02d}m",
            "owner": "Stadt Wolfsburg", "paid_hours_raw": window, "fee_zone_raw": str(fee_zone), "price_raw": price,
            "short_stay_raw": "Ja" if short else "Nein", "source_id": name, "source_layer": "digitalisierung_handyparkzonen_a",
            "source_url": "https://geoviewer.stadt.wolfsburg.de/default/ows/projects/gpt/parken",
            "retrieved_at_utc": "2026-10-01T06:27:59.395836+00:00", "hourly_rate_eur": rate,
            "max_duration_minutes": float("nan") if maximum is None else float(maximum), "fee_zone": fee_zone,
            "short_stay_available": short, "source_metadata_date": "2024-12", "computed_area_m2": geometry.area,
            "geometry": MultiPolygon([geometry])}


def _write_package(directory: Path, name: str, layers: dict) -> None:
    """``<directory>/<name>.zip`` holding ``<name>/<name>.gpkg`` with ``layers`` (layer name -> GeoDataFrame)."""
    work = directory / f"{name}_work"
    work.mkdir()
    gpkg = work / f"{name}.gpkg"
    for layer, frame in layers.items():
        frame.to_file(gpkg, layer=layer, driver="GPKG")
    with zipfile.ZipFile(directory / f"{name}.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(gpkg, f"{name}/{name}.gpkg")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def packages(tmp_path_factory):
    """The three packages the step reads, written into one directory, with their SHA-256."""
    directory = tmp_path_factory.mktemp("municipal_2026-10-01")
    one_a = unary_union([_box(0, 0, 1000, 1000), _box(200, -300, 800, 0)])
    one_b = MultiPolygon([_box(1000, 0, 1300, 600), _box(1000, 700, 1300, 1000)])
    fee = gpd.GeoDataFrame([dict(BS_FEE_ATTRIBUTES, zone_id="1a", name="Parkgebuehrenzone 1a"),
                            dict(BS_FEE_ATTRIBUTES, zone_id="1b", name="Parkgebuehrenzone 1b")],
                           geometry=[MultiPolygon([one_a]), one_b], crs=METRIC_CRS)
    reconstructed = gpd.GeoDataFrame({"zone_id": ["1a"], "reason": ["southern continuation from the 2024 annex"]},
                                     geometry=[_box(200, -300, 800, 0)], crs=METRIC_CRS)
    _write_package(directory, "Braunschweig_Parkzonen", {"parking_fee_zones": fee, "reconstructed_section": reconstructed})
    sections = [_wob_section(STRIP_1, 1, "Strip 1"), _wob_section(STRIP_2, 2, "Strip 2"),
                _wob_section(TIE_1, 1, "Tie 1"), _wob_section(TIE_2, 2, "Tie 2"),
                _wob_section(LONE_3, 3, "Lone 3", window="09:00-18:00", maximum=120, short=True)]
    _write_package(directory, "Wolfsburg_Parkdaten",
                   {"mobile_parking_areas": gpd.GeoDataFrame(sections, geometry="geometry", crs=METRIC_CRS)})
    edit = "2018-11-22T15:42:08.753000+00:00"
    resident = gpd.GeoDataFrame(
        {"Parkbereiche_Kennzeichen": ["A", "B", "C", None],
         "Parkbereiche_Beschreibung": ["Parkbereich A", "Parkbereich B", "Parkbereich C", "Parkbereich C - Oberstadt"],
         "source_data_edit_date": [edit] * 4},
        geometry=[_box(0, 0, 600, 1000, GS_X), _box(900, 0, 1500, 500, GS_X), _box(2000, 0, 2100, 100, GS_X),
                  _box(2000, 200, 2100, 300, GS_X)], crs=METRIC_CRS)
    facilities = gpd.GeoDataFrame(
        {"Bezeichnung": ["Parkplatz P", "Parkplatz F", "Parkhaus U"], "Hinweis": ["gebuehrenpflichtig", "gebuehrenfrei",
                                                                                 "keine Bewohnerparkregelung"],
         "fee_status": ["paid", "free", "unknown"], "source_data_edit_date": [edit] * 3},
        geometry=[_box(100, 100, 150, 150, GS_X), _box(3000, 0, 3050, 50, GS_X), _box(950, 950, 1050, 1050, GS_X)],
        crs=METRIC_CRS)
    _write_package(directory, "Goslar_Parkdaten",
                   {"resident_parking_zones": resident, "parking_facility_areas": facilities})
    expected = {name: _sha256(directory / f"{name}.zip")
                for name in ("Braunschweig_Parkzonen", "Wolfsburg_Parkdaten", "Goslar_Parkdaten")}
    return directory, expected


def _v1_zones(assembly) -> list:
    """The v1 records the step meets: zones Ia/Ib traced from the overview map (Ia without its southern part), a BgA
    car park inside 1a (cut out by the v1 precedence), the OSM-derived wob_innenstadt and the Goslar approximation."""
    return [assembly.zone_record("bs_zone_ia", BS, _box(0, 100, 1000, 1000), "ordinance_map", "https://v1", "v1 Ia"),
            assembly.zone_record("bs_zone_ib", BS, _box(1000, 0, 1300, 1000), "ordinance_map", "https://v1", "v1 Ib"),
            assembly.zone_record("bs_bga_markthalle", BS, _box(100, 500, 150, 550), "ordinance_map", "https://v1",
                                 "v1 BgA"),
            assembly.zone_record("wob_innenstadt", WOB, _box(-100, -100, 400, 200, WOB_X), "osm_fee_tags",
                                 "https://v1", "v1 Wolfsburg"),
            assembly.zone_record("gs_altstadt_zone1", GS, _box(0, 0, 1000, 1000, GS_X), "centre_approximation",
                                 "https://v1", "v1 Goslar")]


@pytest.fixture(scope="module")
def release(assembly, packages, tmp_path_factory):
    """The municipal step on the synthetic packages, through the v1 precedence, written and loaded as the release."""
    directory, expected = packages
    inputs = assembly.mz.load_packages(directory, expected_sha256=expected)
    zones, context = assembly.apply_municipal_packages(_v1_zones(assembly), inputs)
    zones, trims = assembly.apply_precedence(zones)
    assembly.finish_municipal_zones(zones, context)
    out = tmp_path_factory.mktemp("release")
    assembly.write_zone_file(assembly.zone_frame(zones), out / "zones.geojson", municipal=True)
    loaded = pz.load_zone_polygons(out / "zones.geojson", max_repairs=0)  # valid as written, no polygon repaired
    rows = assembly.mz.qa_rows(context, loaded)
    assembly.mz.write_qa_table(out / "municipal_qa.csv", rows)
    return {"zones": loaded.set_index("zone_id"), "trims": dict(trims), "qa_path": out / "municipal_qa.csv",
            "zone_path": out / "zones.geojson", "loaded": loaded}


# --------------------------------------------------------------------------- C1: Braunschweig fee zones 1a and 1b


def test_braunschweig_fee_zones_replace_ia_and_ib_with_the_v1_cut_outs_and_the_reconstruction_flag(release):
    zones = release["zones"]
    ia, ib = zones.loc["bs_zone_ia"], zones.loc["bs_zone_ib"]
    # 1a (1,000,000 m2 plus the 180,000 m2 southern section) minus the BgA car park cut out by the v1 precedence
    assert ia.geometry.area == pytest.approx(1_180_000.0 - 2_500.0, rel=FILE_ROUNDING)
    assert release["trims"]["bs_zone_ia"] == pytest.approx(2_500.0, abs=0.5)
    assert not ia.geometry.contains(_point(125, 525)) and zones.loc["bs_bga_markthalle"].geometry.contains(_point(125, 525))
    assert ia.geometry.contains(_point(500, -150))  # the southern part the v1 tracing lacked
    assert ib.geometry.area == pytest.approx(270_000.0, rel=FILE_ROUNDING) and ib.geometry.geom_type == "MultiPolygon"
    for zone in (ia, ib):
        assert zone["geometry_source"] == "ordinance_map"
        assert (zone["source_date"], zone["digitised_on"]) == (pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-01"))
        assert zone["digitising_note"].startswith(
            "Stadt Braunschweig, published fee zone map (2025-11-26) and Amtsblatt 2024-04-30 map annex; digitised "
            "(owner-supplied package 2026-10-01); working accuracy 25 m; base map Open GeoData dl-de/by-2-0")
        assert zone["digitising_note"].isascii()
    # the flag: the part of the polygon whose boundary rests on the reconstruction from the 2024 annex
    assert ia["reconstructed_section_m2"] == pytest.approx(180_000.0, abs=1.0)
    assert ib["reconstructed_section_m2"] == 0.0
    assert pd.isna(zones.loc["bs_bga_markthalle", "reconstructed_section_m2"])


# --------------------------------------------------------------------------- C2: Wolfsburg tariff zones


def test_wolfsburg_tariff_zones_replace_wob_innenstadt_split_by_the_nearer_section(release):
    zones = release["zones"]
    assert "wob_innenstadt" not in zones.index
    one, two, three = (zones.loc[f"wob_tarifzone_{key}"] for key in (1, 2, 3))
    for zone in (one, two, three):
        assert zone["geometry_source"] == pz.MUNICIPAL_SECTIONS_GEOMETRY_SOURCE
        assert zone["section_buffer_m"] == 50.0
        assert zone["digitising_note"].startswith(
            "Stadt Wolfsburg, Geoviewer Themenkarte Parken (Stand 12/2024); open reuse licence not verified; used by "
            "owner decision 2026-10-01.")
    # The two strips are mirror images about y = 40, the bisector of the 40 m band where their 50 m areas overlap.
    strips = unary_union([STRIP_1.buffer(50.0), STRIP_2.buffer(50.0)])
    one_strip = one.geometry.intersection(strips.buffer(1.0))
    two_strip = two.geometry.intersection(strips.buffer(1.0))
    assert one_strip.area == pytest.approx(two_strip.area, rel=2e-3)
    assert one_strip.area + two_strip.area == pytest.approx(strips.area, rel=SIMPLIFIED_BUFFER)
    assert one_strip.intersection(two_strip).area < 1.0
    assert one.geometry.contains(_point(150, 38.5, WOB_X)) and two.geometry.contains(_point(150, 41.5, WOB_X))
    assert one_strip.intersection(_box(0, 41.5, 300, 60, WOB_X)).area < 1.0
    assert two_strip.intersection(_box(0, 20, 300, 38.5, WOB_X)).area < 1.0
    assert one_strip.difference(STRIP_1.buffer(50.5)).area < 1.0  # never beyond 50 m of its own sections
    # Ties go to the higher tariff: the overlap of the two sections and the points above it equidistant from both.
    assert one.geometry.contains(_point(1075, 505, WOB_X)) and one.geometry.contains(_point(1075, 530, WOB_X))
    assert two.geometry.contains(_point(1125, 505, WOB_X)) and two.geometry.contains(_point(1125, 530, WOB_X))
    assert not two.geometry.contains(_point(1075, 505, WOB_X))
    # Nothing competes with the lone zone-3 section: its zone is its 50 m area.
    assert three.geometry.area == pytest.approx(100 * 10 + 2 * 110 * 50 + math.pi * 50 ** 2, rel=SIMPLIFIED_BUFFER)
    assert three.geometry.symmetric_difference(LONE_3.buffer(50.0)).area < 0.01 * LONE_3.buffer(50.0).area
    assert release["zone_path"].read_text(encoding="utf-8").isascii()


def _irregular_layout(seed: int) -> dict:
    """Two tariff zones of rotated float-coordinate sections: each zone-2 section touches a zone-1 section with 1e-7 m
    of noise, overlaps it by a few metres, or lies apart (the probe of the task review, which grew the real zone-2
    sections 3 m into zone 1)."""
    rng = random.Random(1000 + seed)
    x0, y0 = X0 + WOB_X + rng.random(), Y0 + 9_000.0 + rng.random()
    zone1, zone2 = [], []
    for _ in range(3):
        x, y = x0 + rng.uniform(0, 120), y0 + rng.uniform(0, 120)
        shape = affinity.rotate(box(x, y, x + rng.uniform(20, 120), y + rng.uniform(4, 12)), rng.uniform(0, 180),
                                origin=(x, y))
        zone1.append(shape)
        kind = rng.choice(["touch", "overlap", "apart"])
        if kind == "touch":
            zone2.append(affinity.translate(shape, 0.0, shape.bounds[3] - shape.bounds[1] + rng.uniform(-1e-7, 1e-7)))
        elif kind == "overlap":
            zone2.append(affinity.translate(shape.buffer(rng.uniform(1, 5)), rng.uniform(-3, 3), rng.uniform(-3, 3)))
        else:
            xx, yy = x0 + rng.uniform(0, 120), y0 + rng.uniform(0, 120)
            zone2.append(affinity.rotate(box(xx, yy, xx + 60.123456789, yy + 7.987654321), rng.uniform(0, 180),
                                         origin=(xx, yy)))
    return {1: unary_union(zone1), 2: unary_union(zone2)}


@pytest.mark.parametrize("seed", [5, 4, 45], ids=["cells_lost_their_sample", "topology_exception", "merged_samples"])
def test_nearest_section_split_holds_on_irregular_float_sections(assembly, seed):
    # The three layouts on which the first implementation aborted (a Voronoi cell lost its sample to a near-coincident
    # one, a GEOS topology error, two samples in one cell). The split must still partition the 50 m areas, give the
    # overlap of two sections to the higher tariff and every point clearly nearer to one zone's sections to that zone.
    sections = _irregular_layout(seed)
    split = assembly.mz.split_by_nearest_section(sections, [1, 2])
    one, two = split["zones"][1], split["zones"][2]
    union = unary_union([split["buffers"][1], split["buffers"][2]])
    assert one.intersection(two).area < 1e-3
    assert unary_union([one, two]).area == pytest.approx(union.area, abs=1e-2)
    assert two.intersection(sections[1].intersection(sections[2])).area < 1e-3
    minx, miny, maxx, maxy = split["contested_area"].bounds
    checked = 0
    for x in range(int(minx), int(maxx), 3):
        for y in range(int(miny), int(maxy), 3):
            point = Point(x + 0.5, y + 0.5)
            first, second = point.distance(sections[1]), point.distance(sections[2])
            if not split["contested_area"].contains(point) or abs(first - second) < 1.0:
                continue  # within the sampling error of the split line, or a tie handled above
            assert (one if first < second else two).contains(point), (seed, point.wkt, first, second)
            checked += 1
    assert checked > 100


def test_opening_removes_parts_narrower_than_twice_the_radius_and_never_grows(assembly):
    # The cut clearance left slivers and zero-width spikes in the first wob_tarifzone_2 that the 1e-7 degree rounding
    # of the file turned into a self-intersection; the opening removes every part narrower than twice its radius,
    # keeps wider ones and never grows the polygon into a neighbour.
    square = _box(0, 0, 100, 100, WOB_X)
    spike = Polygon([(X0 + WOB_X + 50, Y0 + 100), (X0 + WOB_X + 50.004, Y0 + 100), (X0 + WOB_X + 50.7, Y0 + 103.66)])
    thin_island = _box(0, 120, 30, 120.04, WOB_X)  # 4 cm wide
    kept_island = _box(0, 140, 30, 140.25, WOB_X)  # 25 cm wide
    zone = unary_union([square, spike, thin_island, kept_island])
    opened = assembly.open_thin_features(zone, assembly.MUNICIPAL_OPENING_M)
    assert opened.difference(zone).area == 0.0
    assert opened.area == pytest.approx(square.area + kept_island.area, abs=1e-6)
    assert not opened.intersects(thin_island) and opened.contains(kept_island.centroid)
    assert opened.intersection(spike).area < 1e-9  # the zero-width spike is gone, the square below it stays


def test_release_licence_names_the_municipal_sources(release):
    import json

    document = json.loads(release["zone_path"].read_text(encoding="utf-8"))
    assert "ODbL" in document["license"] and "OpenStreetMap contributors" in document["attribution"]
    assert "every polygon is derived from OpenStreetMap" not in document["license"]
    for wording in ("Stadt Wolfsburg, Geoviewer Themenkarte Parken (Stand 12/2024); open reuse licence not verified; "
                    "used by owner decision 2026-10-01",
                    "Stadt Braunschweig, published fee zone map (2025-11-26) and Amtsblatt 2024-04-30 map annex; "
                    "digitised (owner-supplied package 2026-10-01); working accuracy 25 m; base map Open GeoData "
                    "dl-de/by-2-0"):
        assert wording in document["license"]
    assert "Stadt Braunschweig - Open GeoData" in document["attribution"]


# --------------------------------------------------------------------------- C4 and the QA table


def test_qa_table_reports_the_overlaps_and_the_goslar_cross_check(release):
    qa = mq.load_municipal_qa(release["qa_path"]).set_index("row_id")
    mq.validate_municipal_qa(qa.reset_index(), release["loaded"])
    assert release["qa_path"].read_text(encoding="utf-8").isascii()

    def numbers(row_id):
        row = qa.loc[row_id]
        return tuple(float(row[column]) for column in ("subject_area_m2", "reference_area_m2", "overlap_area_m2"))

    # package 1a contains the v1 tracing of Ia (both before the precedence cuts) and adds the southern section
    assert numbers("bs_zone_ia_package_vs_v1") == pytest.approx((1_180_000.0, 900_000.0, 900_000.0), abs=0.1)
    assert float(qa.loc["bs_zone_ia_package_vs_v1", "reference_share_in_subject"]) == pytest.approx(1.0)
    assert numbers("bs_zone_ib_package_vs_v1") == pytest.approx((270_000.0, 300_000.0, 270_000.0), abs=0.1)
    assert numbers("bs_zone_ia_reconstructed_section_vs_v1") == pytest.approx((180_000.0, 900_000.0, 0.0), abs=0.1)
    assert qa.loc["bs_zone_ia_release_vs_package", "release_zone_id"] == "bs_zone_ia"
    # Goslar: the fee polygon stays; the resident areas cover 65 % of it, one paid and no free facility area lies in it
    goslar = qa[qa["municipality_ags"] == GS]
    assert set(goslar["release_zone_id"]) == {"gs_altstadt_zone1"}
    assert numbers("gs_altstadt_zone1_vs_resident_areas") == pytest.approx((1_000_000.0, 920_000.0, 650_000.0),
                                                                           rel=FILE_ROUNDING)
    assert float(qa.loc["gs_altstadt_zone1_vs_resident_areas", "subject_share_in_reference"]) == pytest.approx(
        0.65, abs=1e-6)
    assert (qa.loc["gs_altstadt_zone1_vs_resident_areas", "reference_features"],
            qa.loc["gs_altstadt_zone1_vs_resident_areas", "reference_features_overlapping"]) == ("4", "2")
    assert numbers("gs_altstadt_zone1_vs_paid_facility_areas")[2] == pytest.approx(2_500.0, abs=0.5)
    assert numbers("gs_altstadt_zone1_vs_free_facility_areas")[2] == 0.0
    assert numbers("gs_altstadt_zone1_vs_unknown_facility_areas")[2] == pytest.approx(2_500.0, abs=0.5)
    assert "Stadt Goslar, ArcGIS service Bewohnerparken (last edit 2018-11-22)" in qa.loc[
        "gs_altstadt_zone1_vs_resident_areas", "note"]
    assert release["zones"].loc["gs_altstadt_zone1"].geometry.area == pytest.approx(1_000_000.0, rel=FILE_ROUNDING)
    # Wolfsburg: each tariff zone against v1 and the share of its 50 m area it keeps after the split
    assert qa.loc["wob_tarifzone_1_release_vs_v1", "release_zone_id"] == "wob_tarifzone_1"
    kept = float(qa.loc["wob_tarifzone_3_release_vs_50m_area", "reference_share_in_subject"])
    assert kept == pytest.approx(1.0, abs=SIMPLIFIED_BUFFER)
    # The header defines every column.
    header = [line for line in release["qa_path"].read_text(encoding="utf-8").splitlines() if line.startswith("#")]
    for column in mq.MUNICIPAL_QA_COLUMNS:
        assert any(line.startswith(f"# {column}: ") for line in header), column


def test_every_polygon_of_the_municipal_and_regional_sources_needs_rows_in_the_municipal_qa_table():
    # spec Amendments C and D: the buffered street sections, a polygon with a reconstruction flag, the campus unions of
    # camera detection zones and the single-site buffers are compared with their references in this table, so a
    # release cannot hold one of them without rows
    zones = pd.DataFrame({
        "zone_id": ["a_sections", "b_flag", "c_campus", "d_site", "e_plain", "f_other_campus"],
        "geometry_source": [pz.MUNICIPAL_SECTIONS_GEOMETRY_SOURCE, "ordinance_map", pz.CAMPUS_DETECTION_ZONES_GEOMETRY_SOURCE,
                            pz.SINGLE_SITE_BUFFERED_GEOMETRY_SOURCE, "centre_approximation",
                            "centre_approximation"],
        "reconstructed_section_m2": [None, 1000.0, None, None, None, None]})
    assert mq.municipal_zone_ids(zones) == ["a_sections", "b_flag", "c_campus", "d_site"]


def _stale(qa: pd.DataFrame) -> pd.DataFrame:
    """The row wob_tarifzone_2_release_vs_v1 as an older release would have left it: a 1 % larger subject, shares
    consistent with the recorded areas, so only the comparison with the polygon can tell."""
    qa = qa.copy()
    row = qa["row_id"] == "wob_tarifzone_2_release_vs_v1"
    subject = float(qa.loc[row, "subject_area_m2"].iloc[0]) * 1.01
    qa.loc[row, "subject_area_m2"] = f"{subject:.1f}"
    qa.loc[row, "subject_share_in_reference"] = f"{float(qa.loc[row, 'overlap_area_m2'].iloc[0]) / subject:.6f}"
    return qa


@pytest.mark.parametrize("change, message", [
    (_stale, "has .* m2 \\(stale table"),
    (lambda qa: qa[~qa["release_zone_id"].isin(["wob_tarifzone_3"])], "wob_tarifzone_3"),
    (lambda qa: qa.assign(overlap_area_m2=qa["overlap_area_m2"].where(qa["row_id"] != "bs_zone_ib_package_vs_v1",
                                                                       "999999.0")),
     "overlap_area_m2"),
], ids=["stale_release_area", "municipal_polygon_without_a_row", "overlap_above_an_area"])
def test_municipal_qa_table_is_cross_checked_against_the_polygons(release, change, message):
    qa = mq.load_municipal_qa(release["qa_path"])
    with pytest.raises(ValueError, match=message):
        mq.validate_municipal_qa(change(qa), release["loaded"])


# --------------------------------------------------------------------------- inputs the step refuses


def _rewrite_package(source: Path, target: Path, name: str, layer: str, edit) -> None:
    """Copy the package ``name`` from ``source`` to ``target`` with ``edit`` applied to ``layer``."""
    path = f"/vsizip/{(source / f'{name}.zip').as_posix()}/{name}/{name}.gpkg"
    layers = {}
    import pyogrio

    for other, _ in pyogrio.list_layers(path):
        frame = gpd.read_file(path, layer=other)
        layers[other] = edit(frame) if other == layer else frame
    _write_package(target, name, layers)


@pytest.mark.parametrize("name, layer, edit, message", [
    (None, None, None, "SHA-256"),
    ("Braunschweig_Parkzonen", "parking_fee_zones", lambda frame: frame[frame["zone_id"] == "1a"], "1a and 1b"),
    ("Braunschweig_Parkzonen", "parking_fee_zones", lambda frame: frame.assign(estimated_accuracy_m=30),
     "working accuracy"),
    ("Wolfsburg_Parkdaten", "mobile_parking_areas", lambda frame: frame.assign(source_metadata_date="2025-03"),
     "Stand"),
    ("Wolfsburg_Parkdaten", "mobile_parking_areas", lambda frame: frame.to_crs("EPSG:4326"), "EPSG:25832"),
], ids=["sha256_mismatch", "fee_zone_missing", "accuracy_not_the_recorded_one", "layer_date_not_the_recorded_one",
        "layer_not_metric"])
def test_step_refuses_packages_it_cannot_trust(assembly, packages, tmp_path, name, layer, edit, message):
    directory, expected = packages
    if name is None:
        with pytest.raises(SystemExit, match=message):
            assembly.mz.load_packages(directory, expected_sha256=dict(expected, Wolfsburg_Parkdaten="0" * 64))
        return
    import shutil

    for other in expected:
        if other != name:
            shutil.copy(directory / f"{other}.zip", tmp_path / f"{other}.zip")
    _rewrite_package(directory, tmp_path, name, layer, edit)
    hashes = dict(expected, **{name: _sha256(tmp_path / f"{name}.zip")})
    with pytest.raises(SystemExit, match=message):
        assembly.mz.load_packages(tmp_path, expected_sha256=hashes)


# --------------------------------------------------------------------------- tariff evidence of the Wolfsburg rows


def test_wolfsburg_tariff_evidence_is_uniform_or_dominant_or_left_empty(assembly):
    mz = assembly.mz
    rows = [_wob_section(_box(0, 0, 100, 10, WOB_X), 1, "a", maximum=30),
            _wob_section(_box(0, 100, 100, 110, WOB_X), 1, "b"),
            _wob_section(_box(0, 200, 100, 210, WOB_X), 1, "c"),
            _wob_section(_box(0, 300, 50, 310, WOB_X), 1, "d", window="00:00-24:00, Sa 00:00-14:00"),
            _wob_section(_box(0, 400, 100, 410, WOB_X), 2, "e"),
            _wob_section(_box(0, 500, 100, 510, WOB_X), 3, "f", window="09:00-18:00", maximum=120, short=True),
            _wob_section(_box(0, 600, 100, 610, WOB_X), 3, "g", window="09:00-18:00, Sa 09:00-14:00", maximum=120,
                         short=True)]
    sections = gpd.GeoDataFrame(rows, geometry="geometry", crs=METRIC_CRS)
    evidence = mz.tariff_zone_evidence(sections)
    one, three = evidence[1], evidence[3]
    assert one["hourly_rate_eur"] == 2.0 and three["hourly_rate_eur"] == 1.0
    # the dominant weekday window (S1): 3 of 4 sections, 3,000 of 3,500 m2; the first interval without a day is the
    # weekday window, the Saturday interval is not modelled (D1)
    assert (one["fee_start_h"], one["fee_end_h"]) == (7.0, 18.0)
    assert (one["window_sections"], one["sections"]) == (3, 4)
    assert one["window_area_share"] == pytest.approx(3000.0 / 3500.0)
    # a maximum stay is taken only when every section of the zone states the same one
    assert one["max_stay_min"] is None and one["max_stay_stated"] == {30: 1} and one["max_stay_unknown"] == 3
    assert three["max_stay_min"] == 120 and (three["fee_start_h"], three["fee_end_h"]) == (9.0, 18.0)
    assert three["short_stay_sections"] == 2 and one["short_stay_sections"] == 0
    assert mz.parse_weekday_window("07:00-18:00, Sa 07:00-14:00") == (7.0, 18.0)
    assert mz.parse_weekday_window("Mo-Fr 08:30-17:00") == (8.5, 17.0)
    assert mz.parse_weekday_window("nach Beschilderung") is None
    # a tariff zone with two rates, or a rate that contradicts its own price text, is no tariff zone of the layer
    with pytest.raises(SystemExit, match="hourly"):
        mz.tariff_zone_evidence(sections.assign(hourly_rate_eur=[2.0, 2.0, 2.0, 1.5, 1.2, 1.0, 1.0]))
    with pytest.raises(SystemExit, match="price_raw"):
        mz.tariff_zone_evidence(sections.assign(price_raw=["3 \N{EURO SIGN} pro Stunde"]
                                                + list(sections["price_raw"][1:])))
    # the committed tariff rows must carry the layer's values, the billing unit of ASSUMPTION C-b and nothing in the
    # columns no source fills
    empty = pd.array([pd.NA] * 3, dtype="Int64")
    tariffs = pd.DataFrame({"zone_id": ["wob_tarifzone_1", "wob_tarifzone_2", "wob_tarifzone_3"],
                            "municipality_ags": [WOB] * 3, "hourly_rate_eur": [2.0, 1.2, 1.0],
                            "fee_start_h": [7.0, 7.0, 9.0], "fee_end_h": [18.0, 18.0, 18.0],
                            "max_stay_min": pd.array([pd.NA, pd.NA, 120], dtype="Int64"),
                            "billing_unit_min": pd.array([30, 30, 30], dtype="Int64"),
                            "free_if_stay_at_most_min": empty, "first_period_min": empty,
                            "first_period_eur": [float("nan")] * 3, "daily_cap_eur": [float("nan")] * 3,
                            "long_stay_product_eur": [float("nan")] * 3})
    mz.check_tariff_rows(evidence, tariffs)
    for change, message in (({"fee_start_h": [6.0, 7.0, 9.0]}, "fee_start_h"),
                            ({"billing_unit_min": pd.array([60, 30, 30], dtype="Int64")}, "ASSUMPTION C-b"),
                            ({"daily_cap_eur": [6.0, float("nan"), float("nan")]}, "must stay empty")):
        with pytest.raises(SystemExit, match=message):
            mz.check_tariff_rows(evidence, tariffs.assign(**change))
