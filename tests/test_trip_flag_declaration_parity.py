"""Cross-stage parity of the REPEATED trip-build flag declarations.

synpp requires every stage that READS a config key to declare it in its own
``configure()``, so the DECLARATION of a shared trip-build flag is necessarily
repeated across stages -- see the ``KEY_ESCORT_PASSIVE_EDUCATION`` block in
``braunschweig.popsim.stage.config_keys``, which states the resulting invariant:
all stages must see the SAME value. Nothing enforced that invariant, which is
what the #368 whole-branch review recorded as an open minor (four independent
declarations of one key, two of them bare literals).

These tests are that enforcement. They call each stage's real ``configure()``
with a recorder and compare the DECLARED DEFAULTS against the canonical
constants, so a flag whose default is flipped in one place and not the others
fails here instead of silently letting two stages build the same day by
different rules -- e.g. distance distributions estimated with the passive-escort
leg counted as education while the trip build does not count it, or a donor day
built under a different escort vocabulary than the day it replaces.

Why the declarations are not simply collapsed into one import: a stage's synpp
cache token covers only source that stage hashes. ``trips_stage``,
``completed_donor`` and ``home_office_donors_stage`` all hash
``braunschweig.popsim.stage.config_keys`` explicitly (see their
``_DEFERRED_HELPER_MODULE_NAMES``), so importing a DEFAULT_* from it is safe
there. ``braunschweig.popsim.distance_distributions`` has no such hook, so an
imported default could change without devalidating its cached output; its
declaration therefore stays a literal in its own hashed source and is pinned
here instead.
"""

from __future__ import annotations

import pytest


class _ConfigureRecorder:
    """Records what ``configure()`` declares, with synpp's two-argument ``config()``."""

    def __init__(self):
        self.stages = []
        self.config_keys = {}

    def stage(self, name, **_kwargs):
        self.stages.append(name)

    def config(self, name, default=None):
        self.config_keys[name] = default


def _declared(module):
    recorder = _ConfigureRecorder()
    module.configure(recorder)
    return recorder.config_keys


# --- The shared trip-build flags -------------------------------------------------------


def _trip_flag_stages():
    """The stages that declare the shared unprefixed trip-build flags.

    Imported inside the helper so a collection-time import error in one stage cannot
    take the whole module down.
    """
    from braunschweig.popsim import distance_distributions, trips_stage
    from braunschweig.synthesis.commute_day import home_office_donors_stage

    return {
        "trips_stage": trips_stage,
        "distance_distributions": distance_distributions,
        "home_office_donors_stage": home_office_donors_stage,
    }


def test_escort_passive_education_is_declared_with_the_canonical_default():
    """Every stage declaring ``escort_passive_education`` uses the canonical default.

    The seed's education flag, the trip build and the donor day must all count the same
    W_ZWECK codes as education; a stage left on the old default would relabel exactly the
    passive-escort leg (MiD W_ZWECK 13) differently from its siblings.
    """
    from braunschweig.popsim.stage.config_keys import (
        DEFAULT_ESCORT_PASSIVE_EDUCATION, KEY_ESCORT_PASSIVE_EDUCATION)

    declaring = {
        name: _declared(module)[KEY_ESCORT_PASSIVE_EDUCATION]
        for name, module in _trip_flag_stages().items()
    }
    # Guard the guard: an empty or shrunken set would pass vacuously.
    assert set(declaring) == {
        "trips_stage", "distance_distributions", "home_office_donors_stage"}
    for name, declared_default in declaring.items():
        assert declared_default is DEFAULT_ESCORT_PASSIVE_EDUCATION, (
            f"{name} declares {KEY_ESCORT_PASSIVE_EDUCATION} with {declared_default!r}, "
            f"not the canonical {DEFAULT_ESCORT_PASSIVE_EDUCATION!r}")


def test_the_popsim_stage_declares_the_same_escort_education_default():
    """The KREIS-control seed reads the flag too, and must see the same value.

    Declared separately from the trip-build stages above because this stage imports the
    canonical constant directly (it hashes ``config_keys``), so this asserts the import
    is actually used in the declaration rather than shadowed by a literal.
    """
    from braunschweig.popsim import stage
    from braunschweig.popsim.stage.config_keys import (
        DEFAULT_ESCORT_PASSIVE_EDUCATION, KEY_ESCORT_PASSIVE_EDUCATION)

    declared = _declared(stage)
    assert KEY_ESCORT_PASSIVE_EDUCATION in declared
    assert declared[KEY_ESCORT_PASSIVE_EDUCATION] is DEFAULT_ESCORT_PASSIVE_EDUCATION


@pytest.mark.parametrize("key_name, default_name", [
    ("KEY_ESCORT_PURPOSE", "DEFAULT_ESCORT_PURPOSE"),
    ("KEY_EXPLICIT_ROUND_TRIP_PURPOSES", "DEFAULT_EXPLICIT_ROUND_TRIP_PURPOSES"),
])
def test_the_other_shared_trip_flags_agree_across_their_declaring_stages(key_name, default_name):
    """``escort_purpose`` / ``explicit_round_trip_purposes``: same invariant, same pin.

    Their canonical home is ``home_office_donors_stage`` (they have no ``config_keys``
    constant), so that module's constants are the reference and every other declaring
    stage must match them.
    """
    from braunschweig.synthesis.commute_day import home_office_donors_stage as donors

    key = getattr(donors, key_name)
    canonical = getattr(donors, default_name)
    stages = _trip_flag_stages()
    declaring = {name: _declared(module) for name, module in stages.items()}
    seen = {name: declared[key] for name, declared in declaring.items() if key in declared}
    # More than one stage must declare it, or there is no parity left to pin.
    assert len(seen) >= 2, f"only {sorted(seen)} declare {key}"
    for name, declared_default in seen.items():
        assert declared_default == canonical, (
            f"{name} declares {key} with {declared_default!r}, not the canonical "
            f"{canonical!r}")


# --- completed_donor's own declared defaults ------------------------------------------


def test_completed_donor_declares_its_defaults_from_the_canonical_constants():
    """``seed_day_filter`` / ``weekend_plan_match`` are declared from ``config_keys``.

    Both were declared with bare literals (``"default"`` / ``True``) because neither key
    had a ``DEFAULT_*`` constant yet -- the residual half of the #368 review's
    bare-literal-declaration finding. ``completed_donor`` hashes ``config_keys`` in its
    ``_DEFERRED_HELPER_MODULE_NAMES``, so a changed declared default devalidates its
    cached donor exactly as an edit to its own source would; that is what makes naming
    the constant there the safe form and a literal here the unsafe one.
    """
    from braunschweig.popsim import completed_donor
    from braunschweig.popsim.stage.config_keys import (
        DEFAULT_SEED_DAY_FILTER, DEFAULT_WEEKEND_PLAN_MATCH, KEY_SEED_DAY_FILTER,
        KEY_WEEKEND_PLAN_MATCH)

    declared = _declared(completed_donor)
    assert declared[KEY_SEED_DAY_FILTER] == DEFAULT_SEED_DAY_FILTER
    assert declared[KEY_WEEKEND_PLAN_MATCH] == DEFAULT_WEEKEND_PLAN_MATCH
    # The values the pipeline has always run with; pinned so introducing the constants
    # cannot have changed behaviour.
    assert DEFAULT_SEED_DAY_FILTER == "default"
    assert DEFAULT_WEEKEND_PLAN_MATCH is True


def test_the_popsim_stage_agrees_on_the_seed_day_and_weekend_match_defaults():
    """The popsim stage declares the same two keys; both stages must see one value.

    ``completed_donor`` builds the donor under the day filter and the weekend remap, and
    the popsim stage builds the seed from that donor: a default flipped in one of the two
    declarations would let the seed be assembled under a day universe the donor was never
    built for.
    """
    from braunschweig.popsim import completed_donor, stage
    from braunschweig.popsim.stage.config_keys import (
        KEY_SEED_DAY_FILTER, KEY_WEEKEND_PLAN_MATCH)

    stage_declared = _declared(stage)
    donor_declared = _declared(completed_donor)
    for key in (KEY_SEED_DAY_FILTER, KEY_WEEKEND_PLAN_MATCH):
        assert key in stage_declared and key in donor_declared
        assert stage_declared[key] == donor_declared[key], (
            f"popsim.stage declares {key} with {stage_declared[key]!r}, completed_donor "
            f"with {donor_declared[key]!r}")
