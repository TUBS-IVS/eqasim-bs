import gzip
import inspect

from braunschweig.matsim.scenario import supply_gtfs, transit_next_day as t
from tests.test_transit_next_day import SCHEDULE, VEHICLES


class _Ctx:
    def __init__(self, path, hours):
        self._path, self._cfg, self.stages, self.configs = str(path), {t.KEY_NEXT_DAY_HOURS: hours}, [], {}

    def path(self, name=None):
        return self._path

    def config(self, key, default=None):
        self.configs[key] = default
        return self._cfg.get(key, default)

    def stage(self, name):
        self.stages.append(name)


def _write_gtfs(root, calendar, calendar_dates=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / "calendar.txt").write_text(calendar, encoding="utf-8")
    if calendar_dates is not None:
        (root / "calendar_dates.txt").write_text(calendar_dates, encoding="utf-8")
    return str(root)


def test_execute_renames_the_delegate_output_and_writes_the_extended_files(tmp_path, monkeypatch):
    (tmp_path / "transit_schedule.xml.gz").write_bytes(gzip.compress(SCHEDULE))
    (tmp_path / "transit_vehicles.xml.gz").write_bytes(gzip.compress(VEHICLES))
    monkeypatch.setattr(supply_gtfs.delegate, "execute",
                        lambda context: {"schedule_path": "transit_schedule.xml.gz",
                                         "vehicles_path": "transit_vehicles.xml.gz"})
    monkeypatch.setattr(supply_gtfs, "_estimated_service_day", lambda context: ("2026-06-12", 7))
    out = supply_gtfs.execute(_Ctx(tmp_path, 6.0))
    assert out["schedule_path"] == "transit_schedule.xml.gz"
    assert out["service_day_schedule_path"] == "transit_schedule_service_day.xml.gz"
    assert out["service_day_vehicles_path"] == "transit_vehicles_service_day.xml.gz"
    assert (tmp_path / "transit_schedule_service_day.xml.gz").exists()
    assert out["next_day"]["n_duplicated"] == 1
    assert out["estimated_service_day"] == ("2026-06-12", 7)
    assert "d1_d2" in gzip.decompress((tmp_path / "transit_schedule.xml.gz").read_bytes()).decode()


def test_execute_rejects_a_negative_horizon(tmp_path, monkeypatch):
    (tmp_path / "transit_schedule.xml.gz").write_bytes(gzip.compress(SCHEDULE))
    (tmp_path / "transit_vehicles.xml.gz").write_bytes(gzip.compress(VEHICLES))
    monkeypatch.setattr(supply_gtfs.delegate, "execute",
                        lambda context: {"schedule_path": "transit_schedule.xml.gz",
                                         "vehicles_path": "transit_vehicles.xml.gz"})
    monkeypatch.setattr(supply_gtfs, "_estimated_service_day", lambda context: ("2026-06-12", 7))
    import pytest
    with pytest.raises(ValueError):
        supply_gtfs.execute(_Ctx(tmp_path, -1.0))


def test_configure_declares_the_key_with_its_default(monkeypatch):
    monkeypatch.setattr(supply_gtfs.delegate, "configure", lambda context: None)
    ctx = _Ctx(".", 6.0)
    supply_gtfs.configure(ctx)
    assert ctx.configs[t.KEY_NEXT_DAY_HOURS] == t.DEFAULT_NEXT_DAY_HOURS


def test_validate_token_changes_with_the_helper_source(monkeypatch):
    first = supply_gtfs.validate(_Ctx(".", 6.0))
    assert first == supply_gtfs.validate(_Ctx(".", 6.0))
    real = inspect.getsource
    monkeypatch.setattr(inspect, "getsource",
                        lambda m: real(m) + "\nX = 1\n" if m is t else real(m))
    assert supply_gtfs.validate(_Ctx(".", 6.0)) != first
