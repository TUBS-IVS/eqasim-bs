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
