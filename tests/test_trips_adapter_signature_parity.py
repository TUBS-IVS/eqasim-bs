"""Guard the trips source-adapter parameter contract (night-run regression, #241).

``braunschweig.popsim.trips_stage.execute`` reads the trip-building config keys and
passes them as keywords to ``source.build_trips(...)``. That call crosses FOUR
signatures: the implementation (:func:`trips_stage.run`), the protocol
(``sources.base.PopsimSource.build_trips``) and the two adapters
(``MidSource`` / ``EntdSource``). Issue #241 added
``explicit_round_trip_purposes`` to the implementation and to the call site but not
to the three adapter-layer signatures, so the first real 100 % run died with
``MidSource.build_trips() got an unexpected keyword argument
'explicit_round_trip_purposes'`` -- after the whole population had been balanced.
Nothing caught it earlier because the control smokes stop before the trips stage.

These tests pin the parity so the next parameter cannot stop one layer short.
"""
import inspect

from braunschweig.popsim import trips_stage
from braunschweig.popsim.sources.base import PopsimSource
from braunschweig.popsim.sources.entd import EntdSource
from braunschweig.popsim.sources.mid import MidSource


def _keyword_only_names(func) -> set:
    return {
        name for name, p in inspect.signature(func).parameters.items()
        if p.kind is inspect.Parameter.KEYWORD_ONLY
    }


def _implementation_keywords() -> set:
    """The keyword-only trip-building parameters the adapters must all accept."""
    return _keyword_only_names(trips_stage.run)


def test_implementation_exposes_the_expected_trip_building_keywords():
    """Anchor the parity tests: if a keyword is renamed here, they must be updated."""
    assert _implementation_keywords() == {
        "random_seed", "escort_purpose", "escort_passive_education",
        "explicit_round_trip_purposes",
        # Added 2026-09-06 (plan-structure fix, issues #366 / #367): trips_stage.execute
        # reads and passes these four, so all four layers must carry them.
        # closure_dwell_min_obs joined in the same wave (final-review minor M1: the
        # empirical model's cell threshold became a config key).
        "exclude_rbw_legs", "drop_leading_arrive_home_leg", "closure_dwell_model",
        "closure_dwell_min_obs",
        # Added 2026-09-09 (purpose correctness, issue #373 task 2): W_ZWECK 10 ("anderer
        # Zweck") -> leisure, following MiD's own hwzweck1 fold (ADR-0111). Controller
        # ruling C-R3: the passive-escort keywords (escort_passive_from_adult,
        # passive_pair_max_gap_minutes) come in Task 4 -- adding only this one keeps the
        # suite green at every commit instead of red until Task 4 lands.
        "w_zweck_10_as_leisure",
    }


def test_protocol_accepts_every_implementation_keyword():
    missing = _implementation_keywords() - _keyword_only_names(PopsimSource.build_trips)
    assert missing == set(), (
        "sources.base.PopsimSource.build_trips does not declare "
        f"{sorted(missing)}; the protocol must carry every keyword "
        "trips_stage.execute passes to source.build_trips.")


def test_mid_adapter_accepts_every_implementation_keyword():
    missing = _implementation_keywords() - _keyword_only_names(MidSource.build_trips)
    assert missing == set(), (
        f"MidSource.build_trips does not accept {sorted(missing)}; a popsim_mid run "
        "would raise TypeError in the trips stage after the full balancing.")


def test_entd_adapter_accepts_every_implementation_keyword():
    missing = _implementation_keywords() - _keyword_only_names(EntdSource.build_trips)
    assert missing == set(), (
        f"EntdSource.build_trips does not accept {sorted(missing)}; trips_stage.execute "
        "passes the same keywords for both sources, so popsim_open would raise TypeError.")


def test_mid_adapter_forwards_the_round_trip_flag_to_the_implementation(monkeypatch):
    """Accepting the keyword is not enough -- it must reach the implementation."""
    seen = {}

    def _fake_run(persons, mid_wege, **kwargs):
        seen.update(kwargs)
        return "sentinel"

    monkeypatch.setattr(trips_stage, "run", _fake_run)
    result = MidSource().build_trips(
        persons=None, donor_trips=None, random_seed=42,
        explicit_round_trip_purposes=False,
    )
    assert result == "sentinel"
    assert seen["explicit_round_trip_purposes"] is False
    assert seen["random_seed"] == 42
