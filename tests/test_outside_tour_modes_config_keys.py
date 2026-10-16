import pytest

from braunschweig.synthesis.outside_tour_modes import config_keys as keys


def test_key_names_and_defaults():
    assert keys.KEY_ENABLED == "braunschweig.outside_tour_modes.enabled" and keys.DEFAULT_ENABLED is True
    assert keys.KEY_MIN_SHARE == "braunschweig.outside_tour_modes.min_share" and keys.DEFAULT_MIN_SHARE == 0.01
    assert keys.KEY_WARN_SHARE == "braunschweig.outside_tour_modes.warn_share" and keys.DEFAULT_WARN_SHARE == 0.5
    assert keys.SIMULATED_TRIPS_ALIAS == "synthesis.population.trips.simulated"
    assert keys.FINAL_TRIPS_STAGE == "synthesis.population.trips.final"
    assert keys.RNG_OFFSET == 400000 and keys.RNG_OFFSET not in (100000, 200000, 300000)


@pytest.mark.parametrize("min_share, warn_share, key", [
    (0.0, 0.5, keys.KEY_MIN_SHARE), (1.0, 0.5, keys.KEY_MIN_SHARE),
    (0.01, 0.0, keys.KEY_WARN_SHARE), (0.01, 1.5, keys.KEY_WARN_SHARE),
])
def test_validate_settings_names_the_offending_key(min_share, warn_share, key):
    with pytest.raises(ValueError, match=key.replace(".", r"\.")):
        keys.validate_settings(min_share, warn_share)


def test_validate_settings_accepts_the_defaults():
    keys.validate_settings(keys.DEFAULT_MIN_SHARE, keys.DEFAULT_WARN_SHARE)
