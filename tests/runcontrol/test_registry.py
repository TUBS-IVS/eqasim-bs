from braunschweig.runcontrol import registry


def test_every_flag_is_fully_described():
    assert len(registry.FLAGS) >= 25
    for f in registry.FLAGS:
        assert f.key and f.group and f.type in ("bool", "int", "float", "str", "choice")
        assert f.description, f"flag {f.key} lacks a description"
        if f.type == "choice":
            assert f.choices, f"choice flag {f.key} lacks choices"


def test_keys_unique_and_known_examples_present():
    keys = [f.key for f in registry.FLAGS]
    assert len(keys) == len(set(keys))
    for expected in ("sampling_rate", "random_seed", "matsim_last_iteration",
                     "freight_enabled", "cordon_enabled", "fleet_model_enabled",
                     "braunschweig.population.method",
                     "braunschweig.population.popsim.control_tiers"):
        assert expected in keys


def test_numeric_ranges_declared():
    by = registry.by_key()
    s = by["sampling_rate"]
    assert s.type == "float" and s.min == 0.0 and s.max == 1.0 and s.unit == "fraction"
    assert by["matsim_last_iteration"].min == 0
