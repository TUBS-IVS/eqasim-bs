"""External Gemeinde centroids stay candidates only where the network and timetable reach (eqasim-bs#442)."""
import logging

import geopandas as gpd
import pytest
from shapely.geometry import Point, box

from braunschweig.synthesis.locations import secondary_candidates as stage
from braunschweig.synthesis.locations.secondary_chainsolvers import candidates
from braunschweig.synthesis.portal_trips import config_keys as keys

CRS = "EPSG:25832"


def _external(points, ids=None, crs=CRS):
    ids = ids or [f"EXT{index}" for index in range(len(points))]
    return gpd.GeoDataFrame({"commune_id": ids, "ewz": [1.0] * len(points)},
                            geometry=points, crs=crs)


def test_restrict_external_to_supply_ring_keeps_points_inside_and_counts_the_rest():
    ring = box(0.0, 0.0, 100000.0, 100000.0)
    external = gpd.GeoDataFrame({"commune_id": ["EXTa", "EXTb"], "ewz": [1.0, 2.0]},
                                geometry=[Point(50000.0, 50000.0), Point(500000.0, 50000.0)], crs=CRS)
    kept, n_dropped = candidates.restrict_external_to_supply_ring(external, ring)
    assert kept["commune_id"].tolist() == ["EXTa"] and n_dropped == 1


def test_restrict_external_to_supply_ring_raises_when_nothing_is_kept():
    ring = box(0.0, 0.0, 10.0, 10.0)
    external = _external([Point(500000.0, 50000.0), Point(600000.0, 50000.0)])
    with pytest.raises(ValueError) as error:
        candidates.restrict_external_to_supply_ring(external, ring)
    message = str(error.value)
    assert "0 of 2" in message
    assert "CRS" in message


def test_restrict_external_to_supply_ring_accepts_an_empty_frame():
    ring = box(0.0, 0.0, 10.0, 10.0)
    kept, n_dropped = candidates.restrict_external_to_supply_ring(_external([]), ring)
    assert len(kept) == 0 and n_dropped == 0


def test_configure_declares_the_portal_flag_and_the_ring_inputs_when_external_candidates_are_on():
    calls, stages = {}, []

    class _Context:
        def config(self, key, default=None):
            calls[key] = default
            return {"secondary_building_potentials": True, "secondary_external_candidates": True,
                    keys.KEY_ENABLED: True}.get(key, default)

        def stage(self, name, **kwargs):
            stages.append(name)

    stage.configure(_Context())
    assert keys.KEY_ENABLED in calls and "cordon_network_source_buffer_m" in calls
    assert calls["cordon_network_source_buffer_m"] == 45000.0
    assert "data.spatial.municipalities" in stages


def test_configure_declares_neither_the_portal_flag_nor_the_ring_inputs_when_the_portal_is_off():
    calls, stages = {}, []

    class _Context:
        def config(self, key, default=None):
            calls[key] = default
            return {"secondary_building_potentials": True, "secondary_external_candidates": True,
                    keys.KEY_ENABLED: False}.get(key, default)

        def stage(self, name, **kwargs):
            stages.append(name)

    stage.configure(_Context())
    assert "cordon_network_source_buffer_m" not in calls
    assert "data.spatial.municipalities" not in stages


def test_the_deferred_helper_names_cover_the_portal_keys():
    assert "braunschweig.synthesis.portal_trips.config_keys" in stage._DEFERRED_HELPER_MODULE_NAMES
    assert "braunschweig.data.spatial.cordon" in stage._DEFERRED_HELPER_MODULE_NAMES


# --------------------------------------------------------------------------- #
# execute(): the ring is applied and observable
# --------------------------------------------------------------------------- #
class _StubContext:
    def __init__(self, config, stages):
        self._config, self._stages = dict(config), dict(stages)

    def config(self, key, default=None, volatile=False):
        if key in self._config:
            return self._config[key]
        self._config[key] = default
        return default

    def stage(self, name):
        return self._stages[name]


def _legacy_frame():
    return gpd.GeoDataFrame(
        {"location_id": ["sec_0"], "commune_id": ["03101000"], "iris_id": ["03101000"],
         "offers_shop": [True], "offers_leisure": [False], "offers_other": [True]},
        geometry=[Point(0, 0)], crs=CRS)


def _potentials_frame():
    return gpd.GeoDataFrame(
        {"building_id": [11], "potential_retail_daily": [4.0], "potential_retail_non_daily": [1.0],
         "potential_leisure": [0.0], "potential_generic": [2.0], "commune_id": ["03101000"]},
        geometry=[box(0, 0, 5, 5)], crs=CRS)


def _municipalities(crs=CRS):
    return gpd.GeoDataFrame({"commune_id": ["03101000"]}, geometry=[box(-1000, -1000, 1000, 1000)], crs=crs)


def _stage_context(portal_enabled=True, external=None, municipalities=None, buffer_m=2000.0):
    external = external if external is not None else _external(
        [Point(1500.0, 0.0), Point(90000.0, 0.0), Point(0.0, 99000.0)], ["EXTin", "EXTout1", "EXTout2"])
    return _StubContext(
        {"secondary_building_potentials": True, "secondary_external_candidates": True,
         "cordon_enabled": True, "secondary_other_smart_potential": False,
         "secondary_leisure_subtype_split": False, "leisure_visit_building_potential": False,
         keys.KEY_ENABLED: portal_enabled, "cordon_network_source_buffer_m": buffer_m},
        {"synthesis.locations.secondary": _legacy_frame(),
         "braunschweig.data.building_potentials": _potentials_frame(),
         "braunschweig.data.external_secondary_points": external,
         "data.spatial.municipalities": municipalities if municipalities is not None else _municipalities()})


def test_execute_drops_external_centroids_outside_the_ring_and_logs_the_rate(caplog):
    context = _stage_context()
    with caplog.at_level(logging.INFO):
        out = stage.execute(context)
    ids = set(out["location_id"].astype(str))
    assert "EXTin" in ids and "EXTout1" not in ids and "EXTout2" not in ids
    assert any("external centroids inside the supply ring: 1/3 kept (33.3%), 2 dropped" in record.getMessage()
               for record in caplog.records)


def test_execute_keeps_every_external_centroid_when_the_portal_is_off():
    out = stage.execute(_stage_context(portal_enabled=False))
    assert {"EXTin", "EXTout1", "EXTout2"} <= set(out["location_id"].astype(str))


def test_execute_raises_on_a_crs_mismatch_between_external_points_and_municipalities():
    external = _external([Point(1500.0, 0.0)], ["EXTin"], crs="EPSG:4326")
    with pytest.raises(ValueError, match="CRS"):
        stage.execute(_stage_context(external=external))
