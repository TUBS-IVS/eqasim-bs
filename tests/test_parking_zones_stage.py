"""The synpp stage ``braunschweig.parking.zones_stage``: one validated parking cost zone release (issue #249).

Covered: ``configure`` declares ``data_path`` and the six release paths (defaults of design spec 5.5, of the resident
districts of spec Amendment C3 and of the garages of Amendment E1, all relative to ``data_path``); ``execute`` loads
the Task 2 fixture release (fixture zones, tariffs, resident districts and garages plus a synthetic coverage register
and a synthetic SrV-shaped shares table written here, all of which pin behaviour, not truth) and returns the release
whose ``sources`` satisfy the tariff-model contract of the preparation stage; a tariff workplace class without an SrV
class row, a tariff row the Java tariff model would reject, a coverage register that contradicts the tariffs and a
district layer that contradicts the register or itself raise; ``validate()`` changes with every input byte and every
hashed module but not with line endings; the committed release loads through the stage (the primary path on real
data). The context double follows ``tests/test_parking_prepare_wiring.py``: after ``configure`` it is strict like
synpp's ExecuteContext and ValidateContext.
"""
from __future__ import annotations

import inspect
import logging
import shutil
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon, box

from braunschweig.parking import attach, tariff_export, zones_stage
from braunschweig.parking import garages as pg
from braunschweig.parking import zones as pz
from tests.restricted_parking_data import require_restricted_parking_files

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "parking"
COMMITTED_DATA = REPO / "eqasim-data" / "data"
STAGE_LOGGER = "braunschweig.parking.zones_stage"

FIXTURE_ZONE_IDS = ["fx_bs_ia", "fx_bs_ib", "fx_sz", "fx_wob", "fx_pe", "fx_res_a", "fx_campus", "fx_frac",
                    "fx_bs_ib_v2", "fx_bs_ia_v2", "fx_wob_v2", "fx_campus_v2", "fx_garage_window_v2",
                    "fx_capped_street_v2", "fx_campus_tie_v2", "fx_res_garage_v2", "fx_bga_v2", "fx_res_nopermit_v2"]
DEFAULT_PATHS = {
    "parking_zones_path": "braunschweig/parking/parking_zones_2026.geojson",
    "parking_tariffs_path": "braunschweig/parking/parking_tariffs_2026.csv",
    "parking_coverage_register_path": "braunschweig/parking/parking_coverage_register_2026.csv",
    "parking_workplace_shares_path": "braunschweig/srv/srv2023_commute_parking_by_workplace_class.csv",
    "parking_resident_districts_path": "braunschweig/parking/parking_resident_districts_2026.geojson",
    "parking_garages_path": "braunschweig/parking/parking_garages_2026.geojson",
}
#: Deliberately NOT the defaults, so the tests prove that the configured paths are the ones read.
FIXTURE_PATHS = {
    "parking_zones_path": "parking/zones_fixture.geojson",
    "parking_tariffs_path": "parking/tariffs_fixture.csv",
    "parking_coverage_register_path": "parking/coverage_register_fixture.csv",
    "parking_workplace_shares_path": "srv/workplace_shares_fixture.csv",
    "parking_resident_districts_path": "parking/districts_fixture.geojson",
    "parking_garages_path": "parking/garages_fixture.geojson",
}
SOURCE_IDS = ["parking_zones_2026", "parking_tariffs_2026", "parking_coverage_register_2026",
              "srv2023_commute_parking_by_workplace_class", "parking_resident_districts_2026", "parking_garages_2026"]
FIXTURE_DISTRICT_IDS = ["fx_district_a", "fx_district_b", "fx_district_sz_a"]
FIXTURE_GARAGE_COUNT = 19
#: The loader of the garage dataset warns when most priced garages rest on an assumption (P4, P5 and the others); the
#: committed dataset does (34 of 35), so these two warnings are expected at every load and are no defect of the release.
GARAGE_ASSUMPTION_RATE_WARNING = "rest on"
PATH_KEYS = list(FIXTURE_PATHS)

#: One status row per municipality of the fixture tariffs (all zoned) plus one unaudited municipality.
REGISTER_LINES = (
    "# Synthetic coverage register for the parking fixture tariffs (tests only).",
    "ags,name,status,source,note",
    "03101000,Fixture Braunschweig,zoned,fixture,",
    "03102000,Fixture Salzgitter,zoned,fixture,",
    "03103000,Fixture Wolfsburg,zoned,fixture,",
    "03157006,Fixture Peine,zoned,fixture,",
    "03153017,Fixture Goslar,not_audited,,",
)
#: The SrV commute-parking layout with made-up shares: one class row per fixture workplace class and the pooled
#: total row, the unweighted mean of the class rows (bs_outer carries that mean itself).
SHARES_LINES = (
    "# Synthetic SrV-shaped free-parking shares for the parking fixture tariffs (tests only).",
    "workplace_class,level,n_unweighted,n_eff,share_employer_lot,share_street,share_garage_large_lot,"
    "share_other,share_paid_total,share_free_total",
    "bs_zentrum,class,200,100.0,0.5,0.3,0.2,0.0,0.25,0.75",
    "bs_innenbereich,class,200,100.0,0.6,0.3,0.1,0.0,0.1,0.9",
    "bs_outer,class,200,100.0,0.68,0.22,0.1,0.0,0.096,0.904",
    "03102,class,200,100.0,0.8,0.2,0.0,0.0,0.05,0.95",
    "03103,class,200,100.0,0.8,0.1,0.1,0.0,0.05,0.95",
    "03157,class,200,100.0,0.7,0.2,0.1,0.0,0.03,0.97",
    "total,total,1200,600.0,0.68,0.22,0.1,0.0,0.096,0.904",
)


class _Context:
    """synpp context double: records what configure() declares, then serves only that.

    Before ``executing`` is set it behaves like synpp's ConfigurationContext (an option resolves to the
    given value, else to its declared default, else it is unavailable); afterwards like its ExecuteContext
    and ValidateContext: ``config`` takes the option alone and only for a declared option.
    """

    def __init__(self, values):
        self.values = dict(values)
        self.declared_config = {}
        self.declared_stages = []
        self.executing = False

    def config(self, key, *default, **options):
        if self.executing:
            if default or options:
                raise TypeError(f"ExecuteContext.config() takes the option alone; got more for {key!r}")
            if key not in self.declared_config:
                raise KeyError(f"Config option {key} is not requested")
            return self.declared_config[key]
        if key in self.values:
            self.declared_config[key] = self.values[key]
        elif default:
            self.declared_config[key] = default[0]
        else:
            raise KeyError(f"Config option is not available: {key}")
        return self.declared_config[key]

    def stage(self, name, **options):
        if self.executing:
            raise KeyError(f"Stage {name} is not requested")
        self.declared_stages.append(name)


def _write_lines(path: Path, lines) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


@pytest.fixture
def fixture_data(tmp_path):
    """A data_path holding the fixture release under FIXTURE_PATHS."""
    data = tmp_path / "data"
    for key in PATH_KEYS:
        (data / FIXTURE_PATHS[key]).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / "parking_zones_fixture.geojson", data / FIXTURE_PATHS["parking_zones_path"])
    shutil.copyfile(FIXTURES / "parking_tariffs_fixture.csv", data / FIXTURE_PATHS["parking_tariffs_path"])
    shutil.copyfile(FIXTURES / "parking_resident_districts_fixture.geojson",
                    data / FIXTURE_PATHS["parking_resident_districts_path"])
    shutil.copyfile(FIXTURES / "parking_garages_fixture.geojson", data / FIXTURE_PATHS["parking_garages_path"])
    _write_lines(data / FIXTURE_PATHS["parking_coverage_register_path"], REGISTER_LINES)
    _write_lines(data / FIXTURE_PATHS["parking_workplace_shares_path"], SHARES_LINES)
    return data


def _context(data_path, paths=None):
    context = _Context({"data_path": str(data_path), **(FIXTURE_PATHS if paths is None else paths)})
    zones_stage.configure(context)
    context.executing = True
    return context


def _release(data_path):
    return zones_stage.execute(_context(data_path))


# --------------------------------------------------------------------------- configure


def test_configure_declares_data_path_and_the_six_release_paths():
    context = _Context({"data_path": "/srv/eqasim-data/data"})
    zones_stage.configure(context)
    assert context.declared_config == {"data_path": "/srv/eqasim-data/data", **DEFAULT_PATHS}
    assert context.declared_stages == []


@pytest.mark.parametrize("value", ["/data/parking/zones.geojson", "C:/data/parking/zones.geojson",
                                   "braunschweig\\parking\\zones.geojson", ""],
                         ids=["posix_absolute", "windows_drive", "backslashes", "empty"])
@pytest.mark.parametrize("key", PATH_KEYS)
def test_configure_rejects_a_release_path_that_is_not_relative_posix(key, value):
    """The configured text becomes the ``path`` of the tariff model's sources (POSIX, relative to data_path),
    so a path the preparation stage would reject hours later fails here, before any stage runs."""
    with pytest.raises(ValueError, match=key):
        zones_stage.configure(_Context({"data_path": "/data", key: value}))


# --------------------------------------------------------------------------- execute


def test_execute_returns_the_validated_fixture_release(fixture_data):
    release = _release(fixture_data)
    assert set(release) == {"zones", "tariffs", "workplace_shares", "coverage_register", "districts", "garages",
                            "sources"}
    zones = release["zones"]
    assert isinstance(zones, gpd.GeoDataFrame) and zones.crs.to_epsg() == 25832
    assert sorted(zones["zone_id"]) == sorted(FIXTURE_ZONE_IDS)
    assert list(release["tariffs"]["zone_id"]) == FIXTURE_ZONE_IDS
    assert list(release["tariffs"].columns) == list(pz.TARIFF_COLUMNS)
    # The shares table is returned as read: both levels, the class names as text (leading zeros kept).
    shares = release["workplace_shares"]
    assert list(shares["level"]) == ["class"] * 6 + ["total"]
    assert list(shares["workplace_class"]) == ["bs_zentrum", "bs_innenbereich", "bs_outer", "03102", "03103", "03157",
                                               "total"]
    assert attach.free_share_by_class(shares)["03102"] == pytest.approx(0.95)
    assert list(release["coverage_register"]["ags"]) == [line.split(",")[0] for line in REGISTER_LINES[2:]]
    # The resident districts (spec Amendment C3) are a second, independent layer of the same release.
    districts = release["districts"]
    assert isinstance(districts, gpd.GeoDataFrame) and districts.crs.to_epsg() == 25832
    assert list(districts["district_id"]) == FIXTURE_DISTRICT_IDS
    # The garages (spec Amendment E1) are one more layer: the dataset as stored, in EPSG:25832, priced and unpriced rows.
    garages = release["garages"]
    assert isinstance(garages, gpd.GeoDataFrame) and garages.crs.to_epsg() == 25832
    assert len(garages) == FIXTURE_GARAGE_COUNT and garages["priced"].all()


def test_sources_name_the_six_inputs_in_a_fixed_order(fixture_data):
    sources = _release(fixture_data)["sources"]
    assert [source["source_id"] for source in sources] == SOURCE_IDS
    assert [source["path"] for source in sources] == [FIXTURE_PATHS[key] for key in PATH_KEYS]
    for source in sources:
        assert set(source) == {"source_id", "path", "sha256"}
        assert source["sha256"] == tariff_export.content_sha256(fixture_data / source["path"])


def test_sources_satisfy_the_tariff_model_contract_of_the_preparation(fixture_data):
    """braunschweig.matsim.simulation.prepare hands exactly these entries to build_tariff_model."""
    release = _release(fixture_data)
    model = tariff_export.build_tariff_model(release["tariffs"], snapshot_date="2026-09-28",
                                             sources=release["sources"], garages=release["garages"], garage_decay_m=400.0)
    assert model["sources"] == release["sources"]
    assert sorted(model["zones"]) == sorted(FIXTURE_ZONE_IDS)
    assert len(model["garages"]) == FIXTURE_GARAGE_COUNT and model["garage_decay_m"] == 400.0


def test_the_released_frames_drive_the_attach_functions(fixture_data):
    """The release feeds the real draw: every class of a drawn zone type has its share."""
    release = _release(fixture_data)
    for workplace_class, share in attach.free_share_by_class(release["workplace_shares"]).items():
        assert 0.0 <= share <= 1.0, workplace_class
    attach.check_workplace_classes(release["tariffs"], attach.free_share_by_class(release["workplace_shares"]))


def test_a_tariff_workplace_class_without_a_share_row_raises_naming_the_class(fixture_data):
    _write_lines(fixture_data / FIXTURE_PATHS["parking_workplace_shares_path"],
                 [line for line in SHARES_LINES if not line.startswith("03157,")])
    with pytest.raises(ValueError, match="03157") as error:
        _release(fixture_data)
    assert "fx_pe" in str(error.value)


def test_a_tariff_row_the_java_tariff_model_rejects_raises(fixture_data):
    """A daily cap on a resident zone is rejected by the table validator AND by the per-row contract of the
    tariff model (braunschweig.parking.cost.ZoneTariff); the stage fails at load time naming the zone."""
    path = fixture_data / FIXTURE_PATHS["parking_tariffs_path"]
    lines = path.read_bytes().decode("utf-8").replace("\r\n", "\n").split("\n")
    header = next(line for line in lines if line.startswith("zone_id,")).split(",")
    cap = header.index("daily_cap_eur")
    for number, line in enumerate(lines):
        if line.startswith("fx_res_a,"):
            fields = line.split(",")
            fields[cap] = "9.00"
            lines[number] = ",".join(fields)
    path.write_bytes("\n".join(lines).encode("utf-8"))
    with pytest.raises(ValueError, match="fx_res_a"):
        pz.validate_tariffs(pz.load_tariffs(path), allow_fixture_marker=True)

    with pytest.raises(ValueError, match="fx_res_a") as error:
        _release(fixture_data)
    assert "daily_cap" in str(error.value)


def test_a_coverage_register_that_contradicts_the_tariffs_raises(fixture_data):
    _write_lines(fixture_data / FIXTURE_PATHS["parking_coverage_register_path"],
                 [line for line in REGISTER_LINES if not line.startswith("03157006,")])
    with pytest.raises(ValueError, match="03157006"):
        _release(fixture_data)


@pytest.mark.parametrize("key", ["parking_workplace_shares_path", "parking_resident_districts_path",
                                  "parking_garages_path"])
def test_a_missing_input_raises_naming_its_config_key(fixture_data, key):
    (fixture_data / FIXTURE_PATHS[key]).unlink()
    with pytest.raises(FileNotFoundError, match=key):
        _release(fixture_data)


@pytest.mark.parametrize("key, record", [("parking_zones_path", "parking_zones_2026"),
                                         ("parking_resident_districts_path", "parking_resident_districts_2026"),
                                         ("parking_garages_path", "parking_garages_2026")])
def test_a_missing_restricted_input_names_its_data_record_and_the_request_route(fixture_data, key, record):
    # the three geometry files are not distributed in the repository (issue #436): the message must say where to get them
    (fixture_data / FIXTURE_PATHS[key]).unlink()
    with pytest.raises(FileNotFoundError) as error:
        _release(fixture_data)
    message = str(error.value)
    assert key in message and f"docs/registry/data/{record}.yml" in message
    assert "not distributed in the repository" in message and "available on request" in message
    assert "TUBS-IVS/eqasim-bs" in message


def test_a_missing_committed_input_is_not_reported_as_available_on_request(fixture_data):
    (fixture_data / FIXTURE_PATHS["parking_workplace_shares_path"]).unlink()
    with pytest.raises(FileNotFoundError) as error:
        _release(fixture_data)
    assert "the committed input is described in" in str(error.value) and "on request" not in str(error.value)


def test_a_district_layer_that_overlaps_itself_raises(fixture_data):
    """Overlapping districts would put one activity in two districts; the stage refuses the release at load time
    instead of the plans writer failing on the first activity in the overlap."""
    path = fixture_data / FIXTURE_PATHS["parking_resident_districts_path"]
    frame = gpd.read_file(path).to_crs("EPSG:25832")
    frame.loc[frame["district_id"] == "fx_district_b", "geometry"] = frame.loc[
        frame["district_id"] == "fx_district_a", "geometry"].iloc[0].buffer(50.0)
    frame.to_crs("EPSG:4326").to_file(path, driver="GeoJSON")
    with pytest.raises(ValueError, match="overlap by more than"):
        _release(fixture_data)


def test_a_district_in_a_municipality_the_register_does_not_know_raises(fixture_data):
    """A district of Goslar, whose status row is missing from the register: no tariff row is involved, so the
    district check alone must refuse the release."""
    path = fixture_data / FIXTURE_PATHS["parking_resident_districts_path"]
    frame = gpd.read_file(path).to_crs("EPSG:25832")
    extra = frame.iloc[[0]].copy()
    extra["district_id"], extra["municipality_ags"] = "fx_district_gs_a", "03153017"
    extra["geometry"] = box(620000.0, 5790000.0, 620400.0, 5790400.0)
    pd.concat([frame, extra], ignore_index=True).set_crs("EPSG:25832", allow_override=True).to_crs("EPSG:4326").to_file(
        path, driver="GeoJSON")
    _release(fixture_data)  # with the Goslar row present (not_audited) the release loads, with a warning
    _write_lines(fixture_data / FIXTURE_PATHS["parking_coverage_register_path"],
                 [line for line in REGISTER_LINES if not line.startswith("03153017,")])
    with pytest.raises(ValueError, match="03153017"):
        _release(fixture_data)


def test_a_zone_polygon_that_needs_a_repair_stops_the_stage(fixture_data):
    """The release is loaded strictly (max_repairs=0, like scripts/validate_parking_zones.py): a self-intersecting ring
    would be repaired silently into a different polygon, so the stage refuses it and names the zone and the reason."""
    path = fixture_data / FIXTURE_PATHS["parking_zones_path"]
    frame = gpd.read_file(path).to_crs("EPSG:25832")
    x0, y0, x1, y1 = frame.geometry.iloc[0].bounds
    frame.loc[0, "geometry"] = Polygon([(x0, y0), (x1, y1), (x1, y0), (x0, y1)])  # a bow tie
    frame.to_crs("EPSG:4326").to_file(path, driver="GeoJSON")
    with pytest.raises(ValueError, match="1 invalid polygon\\(s\\) would need a repair, at most 0 allowed"):
        _release(fixture_data)


def test_an_invalid_garage_dataset_raises_at_load_time(fixture_data):
    """A garage the validator rejects (here a duplicated id) fails the release, naming the garage."""
    path = fixture_data / FIXTURE_PATHS["parking_garages_path"]
    text = path.read_text(encoding="utf-8").replace('"garage_id": "fx_g02_a"', '"garage_id": "fx_g01_core"')
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="fx_g01_core"):
        _release(fixture_data)


def test_a_fixture_marked_release_logs_a_warning_naming_the_zones(fixture_data, caplog):
    with caplog.at_level(logging.WARNING, logger=STAGE_LOGGER):
        _release(fixture_data)
    [warning] = [record.getMessage() for record in caplog.records
                 if record.levelno == logging.WARNING and record.name == STAGE_LOGGER]
    assert pz.FIXTURE_MARKER in warning
    assert all(zone_id in warning for zone_id in FIXTURE_ZONE_IDS + FIXTURE_DISTRICT_IDS)
    assert "fx_g01_core" in warning and "fx_g14_closed_capped" in warning and f"{FIXTURE_GARAGE_COUNT} of" in warning


def test_the_release_line_counts_the_zone_types_and_register_statuses_by_name(fixture_data, caplog):
    with caplog.at_level(logging.INFO, logger=STAGE_LOGGER):
        _release(fixture_data)
    [line] = [record.getMessage() for record in caplog.records if "zone release:" in record.getMessage()]
    assert "18 zones (campus 3, resident_zone 3, street_paid 12) in 4 municipalities" in line
    assert "coverage register 5 rows (not_audited 1, zoned 4)" in line
    assert "free shares for 6 workplace classes" in line
    assert "resident districts 3 (03101000 2, 03102000 1)" in line
    assert f"garages {FIXTURE_GARAGE_COUNT} (priced {FIXTURE_GARAGE_COUNT})" in line


# --------------------------------------------------------------------------- validate


@pytest.mark.parametrize("key", PATH_KEYS)
def test_validate_changes_when_an_input_byte_changes(fixture_data, key):
    context = _context(fixture_data)
    before = zones_stage.validate(context)
    path = fixture_data / FIXTURE_PATHS[key]
    content = bytearray(path.read_bytes())
    middle = len(content) // 2
    content[middle] = ord("x") if content[middle] != ord("x") else ord("y")
    path.write_bytes(bytes(content))
    assert zones_stage.validate(context) != before


def test_validate_ignores_line_endings(fixture_data):
    """LF-normalised hashes: a Windows checkout (CRLF) and a Linux one (LF) share one token."""
    context = _context(fixture_data)
    paths = [fixture_data / FIXTURE_PATHS[key] for key in PATH_KEYS]
    for path in paths:
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    token_lf = zones_stage.validate(context)
    for path in paths:
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert zones_stage.validate(context) == token_lf


@pytest.mark.parametrize("module_name", ["braunschweig.parking.zones", "braunschweig.parking.garages",
                                         "braunschweig.parking.attach", "braunschweig.parking.tariff_export",
                                         "braunschweig.parking.cost"])
def test_validate_folds_in_the_source_of_every_module_the_stage_runs(fixture_data, monkeypatch, module_name):
    context = _context(fixture_data)
    before = zones_stage.validate(context)
    real_getsource = inspect.getsource

    def getsource(target):
        source = real_getsource(target)
        return source + "\n# edited" if getattr(target, "__name__", None) == module_name else source

    monkeypatch.setattr(inspect, "getsource", getsource)
    assert zones_stage.validate(context) != before


def test_validate_raises_when_a_deferred_helper_module_cannot_be_hashed(fixture_data, monkeypatch):
    absent = "braunschweig.parking.module_absent_for_the_token_test"
    monkeypatch.setattr(zones_stage, "_DEFERRED_HELPER_MODULE_NAMES", (absent,))
    with pytest.raises(RuntimeError, match=absent):
        zones_stage.validate(_context(fixture_data))


def test_validate_raises_for_a_missing_input_naming_its_config_key(fixture_data):
    (fixture_data / FIXTURE_PATHS["parking_coverage_register_path"]).unlink()
    with pytest.raises(FileNotFoundError, match="parking_coverage_register_path"):
        zones_stage.validate(_context(fixture_data))


# --------------------------------------------------------------------------- the committed release


def test_the_committed_release_loads_through_the_stage(caplog):
    """The primary path on real data: the committed polygons, tariffs, register and SrV shares under the
    default paths form one consistent release, and none of it carries the test-set marker."""
    require_restricted_parking_files()      # the stage reads the three restricted files from the data root
    context = _context(COMMITTED_DATA, paths={})
    with caplog.at_level(logging.WARNING, logger=STAGE_LOGGER):
        release = zones_stage.execute(context)
    # The only warning is the assumption-rate warning of the garage loader (44 of 48 priced rows rest on an assumption): by
    # design at every load, no marker, no other problem. The second rate warning (P4 or P5) is silent since the Wolfsburg
    # surface lots (spec E14): 33 of 49 rows (67.3 %) are below the 75 % threshold.
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert [record.name for record in warnings] == ["braunschweig.parking.garages"]
    assert all(GARAGE_ASSUMPTION_RATE_WARNING in record.getMessage() for record in warnings)
    assert len(release["zones"]) > 0
    assert set(release["zones"]["zone_id"]) == set(release["tariffs"]["zone_id"])
    assert set(attach.free_share_by_class(release["workplace_shares"]).index) == set(pz.WORKPLACE_CLASSES)
    assert [source["path"] for source in release["sources"]] == [DEFAULT_PATHS[key] for key in PATH_KEYS]
    # Braunschweig A, B, C and Goslar A, B, C, F, G, H, J (spec Amendment C3): a second layer, not fee zones.
    assert len(release["districts"]) == 10 and set(release["districts"]["municipality_ags"]) == {"03101000", "03153017"}
    # The committed garage dataset: 49 rows (36 garages, 13 surface lots), every one priced (specs E13 and E14), in EPSG:25832.
    assert len(release["garages"]) == 49 and release["garages"]["priced"].all()
    assert len(zones_stage.validate(context)) == 64
    # The committed release exports as the schema-3 tariff model the preparation writes: with the decay 0 (the garage
    # options off) and with a decay value, both list the 49 priced rows.
    model = tariff_export.build_tariff_model(release["tariffs"], snapshot_date="2026-09-28", sources=release["sources"],
                                             garages=release["garages"])
    assert model["schema_version"] == 3 and set(model["zones"]) == set(release["tariffs"]["zone_id"])
    assert len(model["garages"]) == 49 and model["garage_decay_m"] == 0.0


def test_a_legacy_garage_dataset_without_the_imputed_column_is_refused_unless_every_row_is_a_marked_fixture(fixture_data):
    """Spec Amendment F: the stage never silently loads a production dataset that lacks monthly_imputed_eur. The fixture file lacks
    it and is accepted only because every row carries the synthetic test-set marker; one unmarked row refuses the release."""
    _release(fixture_data)   # the marked fixture release loads (and is warned about)
    path = fixture_data / FIXTURE_PATHS["parking_garages_path"]
    text = path.read_text(encoding="utf-8")
    marker = f'"geometry_method": "{pz.FIXTURE_MARKER}"'
    assert marker in text
    path.write_text(text.replace(marker, '"geometry_method": "official_feed_point"', 1), encoding="utf-8")
    with pytest.raises(pg.LegacyColumnsError, match="monthly_imputed_eur"):
        _release(fixture_data)
