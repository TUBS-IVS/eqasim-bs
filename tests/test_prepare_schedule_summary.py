import gzip

from braunschweig.matsim.simulation import prepare
from tests.test_transit_next_day import SCHEDULE


def test_schedule_summary_counts_departures_per_hour(tmp_path):
    path = tmp_path / "s.xml.gz"
    path.write_bytes(gzip.compress(SCHEDULE))
    summary = prepare._schedule_summary(str(path))
    assert summary["departures_per_hour"] == {5: 1, 7: 1, 24: 1}
    assert summary["max_departure_h"] == 24.5


def test_schedule_summary_of_a_schedule_without_departures(tmp_path):
    path = tmp_path / "empty.xml.gz"
    path.write_bytes(gzip.compress(b"<transitSchedule><transitStops/></transitSchedule>"))
    summary = prepare._schedule_summary(str(path))
    assert summary["departures_per_hour"] == {} and summary["max_departure_h"] == 0.0


def test_final_schedule_departures_are_logged_with_vrb_fares_off(tmp_path, monkeypatch, capsys):
    (tmp_path / "bs_transit_schedule.xml.gz").write_bytes(gzip.compress(SCHEDULE))

    class _Context:
        def path(self):
            return str(tmp_path)

        def config(self, key):
            return {"output_prefix": "bs_", prepare.VRB_FARES_KEY: False, "cordon_enabled": False,
                    "freight_enabled": False, prepare.PARKING_KEY: False,
                    prepare.plan_check.KEY_ENABLED: False}[key]

    monkeypatch.setattr(prepare.delegate, "execute", lambda context: "config.xml")
    monkeypatch.setattr(prepare.eqasim, "run", lambda *args, **kwargs: None)
    context = _Context()
    context.stage = lambda name: ({"next_day": {"next_day_hours": 6.0}} if name == prepare.GTFS_STAGE
                                  else type("Z", (), {"to_file": lambda self, path: None})())
    assert prepare.execute(context) == "config.xml"
    out = capsys.readouterr().out
    assert "[schedule] final schedule departures per hour {5: 1, 7: 1, 24: 1}, max departure 24.5 h" in out


class _SummaryContext:
    def __init__(self, tmp_path, gtfs_result):
        self._root, self._gtfs_result = tmp_path, gtfs_result

    def path(self):
        return str(self._root)

    def config(self, key):
        return {"output_prefix": "bs_"}[key]

    def stage(self, name):
        assert name == prepare.GTFS_STAGE
        return self._gtfs_result


def _summarise(tmp_path, gtfs_result, caplog):
    (tmp_path / "bs_transit_schedule.xml.gz").write_bytes(gzip.compress(SCHEDULE))  # latest departure 24.5 h
    with caplog.at_level("WARNING", logger=prepare.log.name):
        prepare._summarise_final_schedule(_SummaryContext(tmp_path, gtfs_result))
    return [r for r in caplog.records if r.levelname == "WARNING" and "[schedule]" in r.getMessage()]


def test_summary_warns_when_the_extension_was_on_but_the_schedule_ends_by_24h(tmp_path, caplog):
    schedule_ends_by_24 = SCHEDULE.replace(b"24:30:00", b"23:30:00")
    (tmp_path / "bs_transit_schedule.xml.gz").write_bytes(gzip.compress(schedule_ends_by_24))
    with caplog.at_level("WARNING", logger=prepare.log.name):
        prepare._summarise_final_schedule(_SummaryContext(tmp_path, {"next_day": {"next_day_hours": 6.0}}))
    assert [r for r in caplog.records if r.levelname == "WARNING" and "[schedule]" in r.getMessage()]


def test_summary_does_not_warn_when_the_extension_reaches_the_next_day_or_is_off(tmp_path, caplog):
    assert not _summarise(tmp_path, {"next_day": {"next_day_hours": 6.0}}, caplog)  # 24.5 h > 24 h
    schedule_ends_by_24 = SCHEDULE.replace(b"24:30:00", b"23:30:00")
    (tmp_path / "bs_transit_schedule.xml.gz").write_bytes(gzip.compress(schedule_ends_by_24))
    prepare._summarise_final_schedule(_SummaryContext(tmp_path, {"next_day": {"next_day_hours": 0.0}}))
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
