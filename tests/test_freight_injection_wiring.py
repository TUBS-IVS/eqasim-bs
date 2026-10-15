from braunschweig.matsim.simulation import prepare


class RecordingContext:
    """Minimal synpp configure-context double recording stage/config requests."""
    def __init__(self, configs):
        self._configs = dict(configs)
        self.stages = []
    def stage(self, name, **kwargs):
        self.stages.append(name)
    def config(self, key, default=None, volatile=False):
        # Mirrors synpp ConfigurationContext.config(option, default, volatile).
        # "volatile" only marks an option as cache-neutral (it must not devalidate
        # cached stages, e.g. "processes"); it has no effect on the returned value,
        # so this recording double accepts and ignores it.
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


def test_boundary_link_defaults_are_declared_when_enabled():
    """Issue #444 / ADR-0144: the boundary-link rule is configured from Python, never by Java defaults."""
    context = RecordingContext({"freight_enabled": True})
    prepare.configure(context)
    assert context._configs["freight_boundary_link_minimum_capacity_veh_per_h"] == 1000.0
    assert context._configs["freight_boundary_link_maximum_distance_m"] == 300.0
    assert context._configs["freight_boundary_link_maximum_fallback_share"] == 0.05


def test_inject_freight_passes_the_boundary_link_options(tmp_path, monkeypatch):
    import pandas as pd

    from braunschweig.freight.trips import TRIP_COLUMNS

    trips = pd.DataFrame([["freight_transit_1", 0.0, 0.0, 1.0, 1.0, 3600.0, "transit"]], columns=list(TRIP_COLUMNS))
    configs = {"freight_sampling_rate": 1.0, "random_seed": 1, "freight_truck_pce": 3.5,
               "freight_truck_max_velocity_kmh": 80.0,
               "freight_boundary_link_minimum_capacity_veh_per_h": 1200.0,
               "freight_boundary_link_maximum_distance_m": 250.0,
               "freight_boundary_link_maximum_fallback_share": 0.02}

    class InjectContext:
        def config(self, key):
            return configs[key]

        def stage(self, name):
            assert name == "braunschweig.freight.trips"
            return trips

        def path(self):
            return str(tmp_path)

    calls = []

    def fake_run(context, class_name, arguments):
        calls.append((class_name, [str(a) for a in arguments]))
        options = dict(zip(calls[-1][1][::2], calls[-1][1][1::2]))
        (tmp_path / options["--summary-path"]).write_text("trip_type;injected_trips\ntransit;1\n", encoding="utf-8")
        (tmp_path / options["--boundary-link-summary-path"]).write_text(
            "endpoint_role;selected_link_endpoints;nearest_node_fallback_endpoints\nstart;1;0\nend;1;0\n",
            encoding="utf-8")

    monkeypatch.setattr(prepare.eqasim, "run", fake_run)
    prepare._inject_freight(InjectContext(), "config.xml")

    class_name, arguments = calls[0]
    options = dict(zip(arguments[::2], arguments[1::2]))
    assert class_name == "org.eqasim.braunschweig.scenario.RunInjectFreight"
    assert options["--boundary-link-minimum-capacity-veh-per-h"] == "1200.0"
    assert options["--boundary-link-maximum-distance-m"] == "250.0"
    assert options["--boundary-link-maximum-fallback-share"] == "0.02"
    assert options["--boundary-link-summary-path"] == "freight_boundary_link_summary.csv"


def test_inject_freight_logs_the_boundary_fallback_rate(tmp_path, monkeypatch, capsys):
    test_inject_freight_passes_the_boundary_link_options(tmp_path, monkeypatch)
    assert "[freight.injection] boundary links: selected 2 / 2, nearest-node fallback 0 (0.00 %)" \
        in capsys.readouterr().out
