"""Two-pass secondary chainsolver for passive escort joint locations (issue #385, ADR-0118):
flag declaration, prerequisites, and the pass composition."""
import pytest

from braunschweig.synthesis.locations import secondary_chainsolvers as sc
# Reuse the sibling suite's configure() stub instead of duplicating one: it already
# mirrors synpp's ConfigurationContext.config(name, default) semantics (a key's value
# is resolved once and stays fixed for the re-reads configure() does).
from tests.test_escort_chainsolvers import _ConfigureCtx


def _configure_context(**overrides):
    """A ``_ConfigureCtx`` whose config values are pinned BEFORE ``configure`` runs.

    Pre-seeding ``registered`` is how a caller-supplied value is expressed with that
    stub: ``config(key, default)`` keeps the first value seen, so a key seeded here
    wins over the default ``configure`` declares for it -- exactly what a YAML config
    setting that key does in production.
    """
    ctx = _ConfigureCtx()
    ctx.registered.update(overrides)
    return ctx


def _base_overrides(**overrides):
    """Both prerequisites of the joint-location flag satisfied, unless overridden."""
    values = {"escort_purpose": True, "escort_passive_from_adult": True}
    values.update(overrides)
    return values


def test_configure_declares_the_joint_location_flag_default_off():
    ctx = _configure_context()
    sc.configure(ctx)
    assert ctx.registered["escort_passive_joint_location"] is False


def test_configure_rejects_joint_location_without_passive_from_adult():
    ctx = _configure_context(**_base_overrides(escort_passive_joint_location=True,
                                               escort_passive_from_adult=False))
    with pytest.raises(ValueError, match="escort_passive_from_adult"):
        sc.configure(ctx)


def test_configure_rejects_joint_location_without_escort_purpose():
    ctx = _configure_context(**_base_overrides(escort_passive_joint_location=True,
                                               escort_purpose=False))
    with pytest.raises(ValueError, match="escort_purpose"):
        sc.configure(ctx)


# --- per-pass report aggregation --------------------------------------------------
# execute() reports the subtype draw rates and the fallback accounting ONCE over ALL
# passes, so the two aggregators below are what makes a two-pass run's transparency
# reporting cover the whole population instead of one pass's fragment.

def _report(subtype_stats=None, desired_by_category=None):
    return {
        "n_problems": 0, "n_unbounded": 0, "n_failed_bounded": 0, "n_plan_rows": 0,
        "subtype_stats": subtype_stats or {},
        "desired_by_category": desired_by_category or {},
    }


def test_sum_subtype_stats_adds_shared_keys_and_keeps_pass_only_keys():
    total = sc._sum_subtype_stats([
        _report({"shop_daily": 3, "shop_non_daily": 1}),
        _report({"shop_daily": 4, "leisure_local": 2}),
    ])
    assert total == {"shop_daily": 7, "shop_non_daily": 1, "leisure_local": 2}


def test_sum_subtype_stats_of_one_report_reproduces_its_counts():
    stats = {"shop_daily": 3, "distance_layer_fallback": 1}
    assert sc._sum_subtype_stats([_report(stats)]) == stats


def test_concat_desired_by_category_keeps_every_passs_distances():
    merged = sc._concat_desired_by_category([
        _report(desired_by_category={"shopping_daily": [1.0, 2.0]}),
        _report(desired_by_category={"shopping_daily": [3.0], "leisure_sport": [4.0]}),
    ])
    assert merged == {"shopping_daily": [1.0, 2.0, 3.0], "leisure_sport": [4.0]}


def test_concat_desired_by_category_does_not_mutate_a_report():
    first = _report(desired_by_category={"shopping_daily": [1.0]})
    sc._concat_desired_by_category([first, _report(desired_by_category={"shopping_daily": [2.0]})])
    assert first["desired_by_category"] == {"shopping_daily": [1.0]}
