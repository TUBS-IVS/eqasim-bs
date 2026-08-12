"""Escort activity options in the facilities writers (issue #201)."""
import contextlib
import gzip

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

import matsim.scenario.facilities as base
from braunschweig.matsim.scenario import facilities as bs_facilities


def _candidates():
    return gpd.GeoDataFrame({
        "location_id": ["sec_b_1", "sec_res_9", "sec_edu_0"],
        "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)],
        "offers_leisure": [True, False, False],
        "offers_shop": [False, False, False],
        "offers_other": [False, False, False],
        "offers_escort": [True, True, True],
        "offers_visit": [False, True, False],
        "offers_escort_residential": [False, True, False],
        "offers_escort_edu_kindergarten": [False, False, True],
        "offers_escort_edu_school": [False, False, False],
        "offers_escort_edu_university": [False, False, False],
    }, crs="EPSG:25832")


def test_secondary_fields_include_escort():
    assert "offers_escort" in base.SECONDARY_FIELDS


def test_secondary_facility_frame_visit_fold_is_conditional():
    on = bs_facilities.secondary_facility_frame(_candidates(), leisure_visit_enabled=True)
    assert bool(on.loc[on["location_id"] == "sec_res_9", "offers_leisure"].iloc[0])
    off = bs_facilities.secondary_facility_frame(_candidates(), leisure_visit_enabled=False)
    assert not bool(off.loc[off["location_id"] == "sec_res_9", "offers_leisure"].iloc[0])
    # escort offer survives the field selection in both cases
    assert bool(off.loc[off["location_id"] == "sec_edu_0", "offers_escort"].iloc[0])


class _NoOpProgress:
    """Stand-in for the tqdm-style progress bar object write_facilities updates."""

    def update(self):
        pass


class _WriteFacilitiesContext:
    """Minimal fake synpp context exposing only what write_facilities actually
    reads: ``context.config("escort_purpose")`` and ``context.progress(...)``
    as a no-op progress bar (mirrors the ``FacilitiesCtx`` stub in
    tests/test_student_incommuters_stage.py). Deliberately has no ``stage()``/
    ``path()`` methods -- write_facilities never calls them, only
    load_facility_frames and execute() do."""

    def __init__(self, escort_purpose):
        self._escort_purpose = escort_purpose

    def config(self, key, default=None):
        if key == "escort_purpose":
            return self._escort_purpose
        return default

    def progress(self, total, label):
        return contextlib.nullcontext(_NoOpProgress())


def _empty_homes():
    return gpd.GeoDataFrame({"household_id": []}, geometry=[], crs="EPSG:25832")


def _empty_primary():
    return gpd.GeoDataFrame({"location_id": [], "is_work": []}, geometry=[], crs="EPSG:25832")


def _secondary_rows():
    """Three fixed secondary facility rows covering: a plain shop, an
    escort-only location (e.g. a residential escort drop-off), and a
    mixed shop+leisure+escort location. Reselected through SECONDARY_FIELDS
    so column order matches exactly what write_facilities indexes into via
    itertuples (mirrors the reindex load_facility_frames performs)."""
    return gpd.GeoDataFrame({
        "location_id": ["sec_shop_1", "sec_escort_2", "sec_multi_3"],
        "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)],
        "offers_leisure": [False, False, True],
        "offers_shop": [True, False, True],
        "offers_other": [False, False, False],
        "offers_escort": [False, True, True],
    }, crs="EPSG:25832")[base.SECONDARY_FIELDS]


def _write_and_read(output_path, df_homes, df_primary, df_secondary, escort_purpose):
    """Invoke the REAL write_facilities (no monkeypatching) and return the
    decompressed XML text it produced."""
    context = _WriteFacilitiesContext(escort_purpose=escort_purpose)
    result = base.write_facilities(str(output_path), df_homes, df_primary, df_secondary, context)
    assert result == "facilities.xml.gz"
    with gzip.open(output_path, "rt", encoding="utf-8") as handle:
        return handle.read()


def test_write_facilities_gates_escort_activity_on_escort_purpose_flag(tmp_path):
    """End-to-end coverage of the actual behavior write_facilities implements
    (matsim/scenario/facilities.py:82-85): an offering secondary facility
    gets an <activity type="escort" /> option only when escort_purpose is ON,
    and never when it is OFF. Calls write_facilities itself -- unlike the two
    existing call sites (tests/test_student_incommuters_stage.py:383-386,
    481-484), which monkeypatch it away and so never execute its real body."""
    df_homes = _empty_homes()
    df_primary = _empty_primary()
    df_secondary = _secondary_rows()

    xml_on = _write_and_read(tmp_path / "facilities_on.xml.gz", df_homes, df_primary,
                              df_secondary, escort_purpose=True)
    xml_off = _write_and_read(tmp_path / "facilities_off.xml.gz", df_homes, df_primary,
                               df_secondary, escort_purpose=False)

    assert '<activity type="escort" />' in xml_on
    assert '<activity type="escort" />' not in xml_off
    # Control assertion: an unrelated purpose is written in BOTH cases, proving
    # the writer actually ran and emitted real facility content rather than an
    # empty/near-empty file that would make the escort assertions vacuous.
    assert '<activity type="shop" />' in xml_on
    assert '<activity type="shop" />' in xml_off
