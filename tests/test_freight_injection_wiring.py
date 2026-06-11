from braunschweig.matsim.simulation import prepare


class RecordingContext:
    """Minimal synpp configure-context double recording stage/config requests."""
    def __init__(self, configs):
        self._configs = dict(configs)
        self.stages = []
    def stage(self, name, **kwargs):
        self.stages.append(name)
    def config(self, key, default=None):
        if key not in self._configs:
            self._configs[key] = default
        return self._configs[key]


def test_configure_requests_trips_when_enabled():
    context = RecordingContext({"freight_enabled": True})
    prepare.configure(context)
    assert "braunschweig.freight.trips" in context.stages


def test_configure_skips_trips_when_disabled():
    context = RecordingContext({"freight_enabled": False})
    prepare.configure(context)
    assert "braunschweig.freight.trips" not in context.stages


def test_freight_enabled_defaults_on():
    context = RecordingContext({})
    prepare.configure(context)
    assert context._configs["freight_enabled"] is True


def test_csv_header_matches_java_expected_header():
    """Pin the cross-language CSV contract.

    _inject_freight writes the sampled trips with columns TRIP_COLUMNS and
    sep=";"; the Java injector (RunInjectFreight.EXPECTED_HEADER) validates the
    header verbatim. This test fails if either side's column order drifts.
    """
    from braunschweig.freight.trips import TRIP_COLUMNS
    expected_java_header = (
        "person_id;origin_x;origin_y;destination_x;destination_y;departure_time;trip_type")
    assert ";".join(TRIP_COLUMNS) == expected_java_header
