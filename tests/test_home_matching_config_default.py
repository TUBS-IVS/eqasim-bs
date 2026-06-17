"""Test that home_cell exposes the _DEFAULT_HOME_MATCHING module constant."""


def test_home_matching_defaults_to_typed():
    # the stage reads config("braunschweig.home_matching", "typed"); the default is "typed".
    from braunschweig.synthesis.locations import home_cell
    assert home_cell._DEFAULT_HOME_MATCHING == "typed"
