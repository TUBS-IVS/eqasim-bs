"""The shared spatial helpers must expose the same ZGB-8 map and loaders that
run_mid_validation used before extraction, so the refactor is behaviour-preserving."""
from braunschweig.analysis import spatial
from braunschweig.analysis import run_mid_validation as rmv


def test_zgb8_map_is_shared_and_unchanged():
    assert spatial.ZGB8 == {
        "03101": "SK Braunschweig", "03102": "SK Salzgitter",
        "03103": "SK Wolfsburg", "03151": "LK Gifhorn",
        "03153": "LK Goslar", "03154": "LK Helmstedt",
        "03157": "LK Peine", "03158": "LK Wolfenbüttel",
    }
    # run_mid_validation must now re-export the same object (no divergent copy).
    assert rmv.ZGB8 is spatial.ZGB8


def test_spatial_exposes_loader_callables():
    for name in ("load_kreise", "load_gemeinden", "load_regiostar", "assign_geographies"):
        assert callable(getattr(spatial, name))
