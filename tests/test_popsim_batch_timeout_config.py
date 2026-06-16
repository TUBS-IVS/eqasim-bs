"""The per-batch PopulationSim timeout must be configurable (was hardcoded 3600s)."""
from braunschweig.popsim import stage, batch


def test_batch_timeout_key_and_default():
    assert stage.KEY_BATCH_TIMEOUT == "braunschweig.population.popsim.batch_timeout_s"


def test_configure_registers_batch_timeout_default():
    seen = {}

    class FakeContext:
        def config(self, key, default=None):
            seen[key] = default
            return default

        def stage(self, *a, **k):
            return None

    stage.configure(FakeContext())
    assert seen[stage.KEY_BATCH_TIMEOUT] == batch.DEFAULT_POPSIM_TIMEOUT_S == 3600
