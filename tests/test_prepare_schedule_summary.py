import gzip

from braunschweig.matsim.simulation import prepare
from tests.test_transit_next_day import SCHEDULE


def test_schedule_summary_counts_departures_per_hour(tmp_path):
    path = tmp_path / "s.xml.gz"
    path.write_bytes(gzip.compress(SCHEDULE))
    summary = prepare._schedule_summary(str(path))
    assert summary["departures_per_hour"] == {5: 1, 7: 1, 24: 1}
    assert summary["max_departure_h"] == 24.5
