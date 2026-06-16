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


def test_timeout_zero_disables_timeout(tmp_path):
    """batch_timeout_s: 0 -> no timeout (subprocess.run gets timeout=None)."""
    captured = {}

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kw):
        captured.update(kw)
        return _R()

    run_one = batch.make_populationsim_run_one(
        timeout_s=0, subprocess_run=fake_run, cwd=str(tmp_path)
    )
    run_one(str(tmp_path / "batch_x"))  # not completed -> spawns the (fake) subprocess
    assert "timeout" in captured and captured["timeout"] is None


def test_positive_timeout_is_passed_through(tmp_path):
    captured = {}

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kw):
        captured.update(kw)
        return _R()

    run_one = batch.make_populationsim_run_one(
        timeout_s=7200, subprocess_run=fake_run, cwd=str(tmp_path)
    )
    run_one(str(tmp_path / "batch_y"))
    assert captured["timeout"] == 7200
