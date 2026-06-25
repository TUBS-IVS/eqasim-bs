import importlib


def test_shims_import_moved_modules():
    for legacy, moved in [
        ("scripts.calibrate_gravity_per_rs7", "braunschweig.calibration._legacy_gravity_per_rs7"),
        ("scripts.calibrate_gravity_decay", "braunschweig.calibration._legacy_gravity_decay"),
        ("scripts.calibrate_education_slopes", "braunschweig.calibration._legacy_education_slopes"),
    ]:
        m_moved = importlib.import_module(moved)
        assert hasattr(m_moved, "main")
        m_shim = importlib.import_module(legacy)
        # the shim's main IS the moved main (same object)
        assert m_shim.main is m_moved.main
