from braunschweig.popsim import stage


def test_stage_defines_kreis_control_keys_default_on():
    assert stage.KEY_INCOME_KC == "braunschweig.population.popsim.income_kreis_control"
    assert stage.KEY_INCOME_KC_METHOD == "braunschweig.population.popsim.income_draw_method"
    assert stage.KEY_INCOME_KC_HHSIZE == "braunschweig.population.popsim.income_kreis_control_hhsize_correct"


def test_configure_registers_kreis_control_defaults():
    seen = {}

    class FakeContext:
        def config(self, key, default=None):
            seen[key] = default
            return default
        def stage(self, *a, **k):
            return None

    stage.configure(FakeContext())
    assert seen[stage.KEY_INCOME_KC] is True
    assert seen[stage.KEY_INCOME_KC_METHOD] == "combined"
    assert seen[stage.KEY_INCOME_KC_HHSIZE] is True


def test_configure_registers_pareto_defaults():
    seen = {}

    class FakeContext:
        def config(self, key, default=None):
            seen[key] = default
            return default
        def stage(self, *a, **k):
            return None

    stage.configure(FakeContext())
    assert seen[stage.KEY_INCOME_KC_PARETO] is True
    assert seen[stage.KEY_INCOME_KC_PARETO_ALPHA] == 3.0
