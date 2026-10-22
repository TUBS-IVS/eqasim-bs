"""Accepted-core path of the parking zone assembly (parking cost zones v2, lever 1, issue #436; fix round 1, F1).

The real curation inputs are gitignored, so the accepted path is pinned on synthetic EPSG:25832 inputs, written the
way the curation aid writes them (``<ags>_qa_<tag>.json``, ``<ags>_core_<tag>.geojson``, ``overpass_failures.log``):
an accepted Goslar core replaces the centre approximation ``gs_altstadt_zone1``; the Braunschweig core minus the v1
zones is assigned piece by piece, WHOLE, to the annex zone it overlaps most (ruling R-T1-f, never clipped) at zero
overlap, with Q2 re-applied after the cut; a QA-only and a failed municipality get their rows. The output must pass
``load_zone_polygons`` and ``validate_zone_qa``. Inputs without the pre-registered parameters are refused (R-T1-g).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from braunschweig.parking import zone_geometry as zg
from braunschweig.parking import zones as pz

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = REPO_ROOT / "scripts" / "curation" / "parking_zones_2026"
METRIC_CRS = "EPSG:25832"
X0, Y0 = 604_000.0, 5_789_000.0
BS, GS, WOB, PE = "03101000", "03153017", "03103000", "03157006"
TAG = zg.PRE_REGISTERED_PARAMETERS.tag()
PAID = {"highway": "residential", "parking:both": "lane", "parking:both:fee": "yes"}


def _box(x0, y0, x1, y1):
    return box(X0 + x0, Y0 + y0, X0 + x1, Y0 + y1)


@pytest.fixture(scope="module")
def assembly():
    """The assembly script as a module (it imports curation_common from its own directory)."""
    sys.path.insert(0, str(CURATION_DIR))
    try:
        spec = importlib.util.spec_from_file_location("assemble_parking_zones_under_test",
                                                      CURATION_DIR / "assemble_parking_zones.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(CURATION_DIR))
    return module


@pytest.fixture(scope="module")
def accepted_qa():
    """QA numbers of an accepted core, produced by the real construction on a synthetic 1.5 km grid."""
    rows = []
    for index in range(16):
        offset = index * 100.0
        rows.append((LineString([(X0 + offset, Y0), (X0 + offset, Y0 + 1500.0)]), PAID))
        rows.append((LineString([(X0, Y0 + offset), (X0 + 1500.0, Y0 + offset)]), PAID))
    ways = gpd.GeoDataFrame({"osm_id": range(len(rows)), "tags": [tags for _, tags in rows]},
                            geometry=[geometry for geometry, _ in rows], crs=METRIC_CRS)
    lots = zg.classify_lots(gpd.GeoDataFrame({"osm_id": [], "tags": []}, geometry=[], crs=METRIC_CRS))
    qa = zg.build_zone_core(zg.classify_segments(ways), lots).qa()
    assert qa["decision"] == "accepted"
    return qa


def _write_inputs(directory: Path, ags: str, qa: dict, core_geometry, *, timestamp: str, **overrides) -> None:
    document = dict(qa, ags=ags, raw_response=f"{ags}_regulation_overpass_2026-09-30.json", osm_timestamp=timestamp,
                    bbox=[52.2, 10.5, 52.3, 10.6], segments=40, lots=3, reference=None, counterfactual=None)
    parts = list(getattr(core_geometry, "geoms", [core_geometry])) if core_geometry is not None else []
    document.update(core_area_m2=round(sum(part.area for part in parts), 1), core_parts=len(parts), **overrides)
    (directory / f"{ags}_qa_{TAG}.json").write_text(json.dumps(document), encoding="utf-8")
    core = gpd.GeoDataFrame({"ags": [ags] * len(parts), "part_id": [f"z{n:03d}" for n in range(1, len(parts) + 1)]},
                            geometry=parts, crs=METRIC_CRS)
    (directory / f"{ags}_core_{TAG}.geojson").write_text(core.to_crs("EPSG:4326").to_json(), encoding="utf-8")


def _reference(names) -> dict:
    """The reference section of a QA file (curation aid ``reference_report``) with one zone 'ii'."""
    zone = {"paid_ways": {"count": 0, "length_m": 0.0, "names": []},
            "paid_street_side_areas": {"count": 2, "area_m2": 310.0, "names": names},
            "free_side_ways": {"count": 1, "length_m": 120.0, "names": names},
            "free_side_ways_without_fee_tag": 1, "free_street_side_areas": {"count": 0, "area_m2": 0.0, "names": []},
            "eroded_filled_inside_m2": 0.0}
    return dict(zone, zones={"ii": zone}, core_share_inside_reference=0.8, reference_share_covered=0.1,
                tagging_completeness_inside=0.4, largest_outline_distance_m=900.0, paid_segments_outside=0)


def _annex_zones() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"zone": ["ia", "ib", "ii"]},
                            geometry=[_box(0, 0, 1000, 1000), _box(1000, 0, 1300, 1000), _box(0, -2000, 3000, 0)],
                            crs=METRIC_CRS).set_index("zone")


def test_accepted_cores_become_release_polygons_with_provenance(assembly, accepted_qa, tmp_path):
    rejected_qa = dict(accepted_qa, decision="rejected", decision_reason="core 0 m2 below 10000 m2")
    # Braunschweig core parts: A lies half under the v1 zone Ia (its southern half stays, inside the annex zone Ia);
    # B inside zone II; C 75 % inside zone II and 25 % outside every annex zone; D becomes 2,500 m2 once the v1 zones
    # Ia and Ib are cut out (below Q2); E outside every annex zone.
    bs_core = unary_union([_box(100, 100, 900, 800), _box(500, -700, 1500, -100), _box(2000, -300, 2400, 100),
                           _box(950, 450, 1050, 550), _box(5000, 5000, 5200, 5200)])
    gs_core = _box(20_100, -39_900, 20_900, -39_100)
    _write_inputs(tmp_path, BS, accepted_qa, bs_core, timestamp="2026-09-30T14:24:46Z",
                  reference=_reference(["Sch\u00f6ppenstedter Stra\u00dfe", "L\u00f6wenwall"]))
    _write_inputs(tmp_path, GS, accepted_qa, gs_core, timestamp="2026-09-30T14:25:48Z")
    _write_inputs(tmp_path, WOB, rejected_qa, None, timestamp="2026-09-30T14:29:51Z")
    (tmp_path / "overpass_failures.log").write_text("2026-09-30T14:35:27Z\t03157006\tHTTP 504\n", encoding="utf-8")
    erosion = assembly.load_erosion_inputs(tmp_path, municipalities=(BS, GS, WOB, PE))
    assert erosion[PE]["qa"] is None and "HTTP 504" in erosion[PE]["failure"]

    zones = [assembly.zone_record("bs_zone_ia", BS, _box(0, 500, 1000, 1000), "ordinance_map", "https://v1", "v1"),
             assembly.zone_record("bs_zone_ib", BS, _box(1000, 0, 1300, 1000), "ordinance_map", "https://v1", "v1"),
             assembly.zone_record("wob_innenstadt", WOB, _box(17_000, 20_000, 18_000, 21_000), "osm_fee_tags",
                                  "https://v1", "v1")]
    replacement = assembly.erosion_replacement("gs_altstadt_zone1", GS, "https://goslar", "the v1 hull", erosion)
    assert replacement is not None and assembly.erosion_replacement("wob_innenstadt", WOB, "u", "d", erosion) is None
    zones.append(replacement)
    zones, _ = assembly.apply_precedence(zones)
    records, pieces = assembly.assign_braunschweig_pieces(assembly.accepted_core(erosion[BS]), zones, _annex_zones(),
                                                          erosion[BS]["qa"])
    zones += records
    frame = assembly.zone_frame(zones)
    assembly.write_zone_file(frame, tmp_path / "zones.geojson")
    assembly.write_qa_table(tmp_path / "qa.csv", assembly.qa_table_rows(erosion, zones, pieces, counterfactuals=()))

    loaded = pz.load_zone_polygons(tmp_path / "zones.geojson").set_index("zone_id")
    assert set(loaded.index) == {"bs_zone_ia", "bs_zone_ib", "wob_innenstadt", "gs_altstadt_zone1", "bs_zone_ia_sued",
                                 "bs_zone_ii"}
    goslar = loaded.loc["gs_altstadt_zone1"]
    assert goslar["geometry_source"] == pz.EROSION_GEOMETRY_SOURCE and goslar["unavoidable_walk_m"] == 250.0
    assert goslar["osm_timestamp"] == "2026-09-30T14:25:48Z"
    assert goslar.geometry.area == pytest.approx(gs_core.area, rel=1e-4)
    southern_ia, zone_ii = loaded.loc["bs_zone_ia_sued"].geometry, loaded.loc["bs_zone_ii"].geometry
    # A without its part under the v1 zone Ia (less the 5 cm cut clearance along the 800 m shared edge)
    assert southern_ia.area == pytest.approx(800.0 * 400.0, rel=1e-3)
    # zero overlap after the WGS84 rounding of the file (the 800 m shared edge overlapped by 1.6 m2 without clearance)
    assert southern_ia.intersection(loaded.loc["bs_zone_ia"].geometry).area == 0.0
    # C is assigned whole to zone II, not clipped at the annex outline (its northern quarter lies outside it)
    assert zone_ii.area == pytest.approx(1000.0 * 600.0 + 400.0 * 400.0, rel=1e-4)
    assert zone_ii.contains(Point(X0 + 2200.0, Y0 + 50.0))
    by_status = {piece["status"]: piece for piece in pieces}
    # D, dropped by Q2 after the cut (2,500 m2 less the 5 cm clearance along its two cut edges)
    assert by_status["below_minimum_island"]["area_m2"] == pytest.approx(2500.0, rel=5e-3)
    assert by_status["no_annex_zone"]["area_m2"] == pytest.approx(40_000.0, rel=1e-4)  # E, reported, not zoned
    straddling = [piece for piece in pieces if piece["zone_id"] == "bs_zone_ii" and piece["share_outside"] > 0]
    assert len(straddling) == 1 and straddling[0]["share_outside"] == pytest.approx(0.25, abs=1e-3)
    assert "ODbL" in json.loads((tmp_path / "zones.geojson").read_text(encoding="utf-8"))["license"]
    assert json.loads((tmp_path / "zones.geojson").read_text(encoding="utf-8"))["license"] == assembly.LICENSE_V2

    qa = pz.load_zone_qa(tmp_path / "qa.csv").set_index("ags", drop=False)
    tariffs = pd.DataFrame({"zone_id": list(loaded.index),
                            "municipality_ags": [GS if z == "gs_altstadt_zone1" else WOB if z == "wob_innenstadt"
                                                 else BS for z in loaded.index]})
    pz.validate_zone_qa(qa.reset_index(drop=True), loaded.reset_index(), tariffs)
    assert (qa.loc[BS, "applied"], qa.loc[BS, "zone_ids"]) == ("true", "bs_zone_ia_sued;bs_zone_ii")
    assert (qa.loc[GS, "applied"], qa.loc[GS, "zone_ids"]) == ("true", "gs_altstadt_zone1")
    assert (qa.loc[WOB, "role"], qa.loc[WOB, "q4_decision"], qa.loc[WOB, "applied"]) == ("qa_only", "rejected", "false")
    assert qa.loc[PE, "q4_decision"] == "request_failed"
    assert "25.0 % outside" in qa.loc[BS, "piece_assignment"] and "no annex zone" in qa.loc[BS, "piece_assignment"]
    # the ordinance conflicts reach the table, OSM names in the ASCII transliteration of the committed files
    assert (qa.loc[BS, "reference_paid_street_side_areas"], qa.loc[BS, "reference_free_side_ways"]) == ("2", "1")
    assert "Schoeppenstedter Strasse" in qa.loc[BS, "reference_conflicts"] and "Loewenwall" in qa.loc[BS, "reference_conflicts"]
    assert (tmp_path / "qa.csv").read_text(encoding="utf-8").isascii()
    # the header defines every column of the table
    header = [line for line in (tmp_path / "qa.csv").read_text(encoding="utf-8").splitlines() if line.startswith("#")]
    for column in pz.ZONE_QA_COLUMNS:
        assert any(line.startswith(f"# {column}: ") for line in header), column


@pytest.mark.parametrize("overrides, message", [
    ({"parameters": dict(zg.PRE_REGISTERED_PARAMETERS.as_dict(), walk_m=150.0)}, "pre-registered"),
    ({"counterfactual": "all_kerbside_streets_regulated"}, "counterfactual"),
], ids=["sensitivity_arm", "counterfactual"])
def test_assembly_refuses_inputs_without_the_pre_registered_parameters(assembly, accepted_qa, tmp_path, overrides,
                                                                       message):
    _write_inputs(tmp_path, GS, accepted_qa, _box(0, 0, 200, 200), timestamp="2026-09-30T14:25:48Z", **overrides)
    with pytest.raises(SystemExit, match=message):
        assembly.load_erosion_inputs(tmp_path, municipalities=(GS,))
