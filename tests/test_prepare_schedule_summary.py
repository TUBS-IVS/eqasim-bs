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
    monkeypatch.setattr(prepare, "_add_transit_zone_information", lambda context: None, raising=False)
    monkeypatch.setattr(prepare.eqasim, "run", lambda *args, **kwargs: None)
    context = _Context()
    context.stage = lambda name: type("Z", (), {"to_file": lambda self, path: None})()
    assert prepare.execute(context) == "config.xml"
    out = capsys.readouterr().out
    assert "[schedule] final schedule departures per hour {5: 1, 7: 1, 24: 1}, max departure 24.5 h" in out
