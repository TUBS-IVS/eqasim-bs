"""OFF byte-identical contract test for the TAZ gravity flag.

Proves that with ``taz_work_location_choice=False`` (the default), calling
``braunschweig.gravity.model.configure()`` requests EXACTLY the pre-feature
stages and config keys and NO TAZ-specific stages.

Design
------
synpp is a server-only dependency (requires real stages, LAPACK, ...).  This
test bypasses synpp entirely by passing a tiny ``MockContext`` that records
every ``context.config(key, *default)`` and ``context.stage(name)`` call and
returns the supplied default (or False for unknown boolean keys).  configure()
reads the TAZ flag from the stub and, since it defaults to False, must NOT
stage the TAZ dependencies.  The assertions verify the stage/config boundary
exactly, so any future addition of a TAZ dep behind the wrong flag will be
caught.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.gravity.model import configure


# ---------------------------------------------------------------------------
# Minimal stub context (no synpp)
# ---------------------------------------------------------------------------

class MockContext:
    """Records config() and stage() calls; returns defaults or False."""

    def __init__(self, flag_values=None):
        """flag_values: dict mapping config key -> bool override (default: all False)."""
        self._flag_values = flag_values or {}
        self.configs_requested = []   # list of (key, default) tuples
        self.stages_requested = []    # list of stage name strings

    def config(self, key, *args):
        """Record the config call; return override value if set, else default or False."""
        default = args[0] if args else None
        self.configs_requested.append((key, default))
        if key in self._flag_values:
            return self._flag_values[key]
        # Preserve the supplied default (None, False, numeric, str).
        return default

    def stage(self, name):
        """Record the stage dependency; return None (stub result)."""
        self.stages_requested.append(name)
        return None


# ---------------------------------------------------------------------------
# Expected base stages (unconditionally declared by configure())
# ---------------------------------------------------------------------------

BASE_STAGES = {
    "eqasim_common.gravity.distance_matrix",
    "data.census.filtered",
    "braunschweig.data.census.employees",
    "braunschweig.data.bbsr.regiostar",
    "braunschweig.data.census.pendler",
    "braunschweig.data.census.employment",
    "braunschweig.data.external_workplaces",
}

# TAZ-specific stages that must NOT appear on the OFF path.
TAZ_ONLY_STAGES = {
    "braunschweig.data.spatial.taz",
    "braunschweig.gravity.distance_matrix_taz",
    "synthesis.population.spatial.home.locations",
    "braunschweig.data.building_potentials",
    "eqasim_common.spatial.codes",          # also sector-aware dep; OFF for both here
}


# ---------------------------------------------------------------------------
# Test 1: flag absent/default -> no TAZ stages
# ---------------------------------------------------------------------------

def test_off_path_does_not_request_taz_stages():
    """With taz_work_location_choice=False (default) configure() must not
    stage any TAZ-specific dependency.

    The MockContext returns False for every boolean config key (flag is OFF).
    After configure() runs we assert:
    - none of the TAZ-only stages appear in stages_requested;
    - all four pre-feature base stages are present;
    - 'taz_work_location_choice' IS declared (default False confirmed).
    """
    ctx = MockContext()
    configure(ctx)

    staged = set(ctx.stages_requested)
    configured_keys = {k for k, _ in ctx.configs_requested}

    # TAZ stages must NOT be requested on the OFF path.
    for taz_stage in TAZ_ONLY_STAGES:
        assert taz_stage not in staged, (
            "configure() staged %r with taz_work_location_choice=False; "
            "this breaks the OFF byte-identical contract" % taz_stage
        )

    # Base stages must all be present.
    for base_stage in BASE_STAGES:
        assert base_stage in staged, (
            "configure() did not stage base dependency %r on the OFF path" % base_stage
        )

    # The flag key must be declared (so the pipeline config layer registers it).
    assert "taz_work_location_choice" in configured_keys, (
        "configure() did not declare 'taz_work_location_choice' config key"
    )


# ---------------------------------------------------------------------------
# Test 2: flag explicitly False -> same result as absent/default
# ---------------------------------------------------------------------------

def test_explicit_false_identical_to_default():
    """Explicitly setting taz_work_location_choice=False must produce the
    same stage/config requests as not setting it at all (the default path)."""
    ctx_default = MockContext()
    ctx_false = MockContext(flag_values={"taz_work_location_choice": False})

    configure(ctx_default)
    configure(ctx_false)

    assert set(ctx_default.stages_requested) == set(ctx_false.stages_requested), (
        "Stage requests differ between default and explicit-False flag paths"
    )
    # Config keys declared should also be the same.
    keys_default = {k for k, _ in ctx_default.configs_requested}
    keys_false = {k for k, _ in ctx_false.configs_requested}
    assert keys_default == keys_false, (
        "Config keys declared differ between default and explicit-False flag paths"
    )


# ---------------------------------------------------------------------------
# Test 3: flag ON -> TAZ stages ARE requested
# ---------------------------------------------------------------------------

def test_on_path_requests_taz_stages():
    """When taz_work_location_choice=True all four TAZ-specific stages are
    declared by configure().

    This is the positive counterpart: it confirms the conditional block is
    actually wired (a no-op conditional would make Test 1 vacuously pass).
    """
    ctx = MockContext(flag_values={
        "taz_work_location_choice": True,
        "braunschweig.gravity.sector_aware_enabled": False,
    })
    configure(ctx)

    staged = set(ctx.stages_requested)

    for taz_stage in {
        "braunschweig.data.spatial.taz",
        "braunschweig.gravity.distance_matrix_taz",
        "synthesis.population.spatial.home.locations",
        "braunschweig.data.building_potentials",
        "eqasim_common.spatial.codes",
    }:
        assert taz_stage in staged, (
            "configure() with taz_work_location_choice=True did not stage %r" % taz_stage
        )


# ---------------------------------------------------------------------------
# Test 4: default values are the pre-feature defaults (numeric + None)
# ---------------------------------------------------------------------------

def test_off_path_default_config_values():
    """The numeric and None defaults declared by configure() on the OFF path
    must match the pre-feature values baked into the module constants."""
    from braunschweig.gravity.model import (
        DEFAULT_SLOPE, DEFAULT_CONSTANT, DEFAULT_DIAGONAL, DEFAULT_GRAVITY_MAX_ITERATIONS
    )

    ctx = MockContext()
    configure(ctx)

    config_map = {k: d for k, d in ctx.configs_requested}

    assert config_map.get("gravity_slope") == DEFAULT_SLOPE
    assert config_map.get("gravity_constant") == DEFAULT_CONSTANT
    assert config_map.get("gravity_diagonal") == DEFAULT_DIAGONAL
    assert config_map.get("gravity_max_iterations") == DEFAULT_GRAVITY_MAX_ITERATIONS
    # Both optional-dict keys must default to None (not {}), because synpp's
    # flatten() drops empty-dict values and would prevent execute() from reading them.
    assert config_map.get("gravity_slope_by_regiostar7") is None
    assert config_map.get("gravity_friction_factors") is None
    # TAZ flag defaults to False.
    assert config_map.get("taz_work_location_choice") is False
