"""Tests for the day-absence synpp stage (issue #370, Task 3).

Driven through the same stub-context pattern as ``tests/test_commute_day_stages.py``
(``_ConfigureRecorder`` with synpp's two-argument ``config(name, default)``, ``_StubContext`` with
the real SINGLE-argument ``config(name)`` that refuses anything ``configure`` did not declare), so a
stage reading an undeclared key or stage fails here rather than first on the run server.

The ON path reads the COMMITTED SrV reference tables under ``eqasim-data/data/braunschweig/srv/``
(the same tables ``braunschweig.synthesis.day_absence.absence`` is already tested against in
``tests/test_day_absence.py``); the OFF path must read no file at all, which is asserted by pointing
``data_path`` at an empty ``tmp_path``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.synthesis.day_absence import absence as D
from braunschweig.synthesis.day_absence import absence_stage as S

REPO = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
DATA_PATH = __import__("os").path.join(REPO, "eqasim-data", "data")


class _ConfigureRecorder:
    """Records what ``configure`` declares, with synpp's two-argument config() signature."""

    def __init__(self): self.stages, self.config_keys = [], {}
    def stage(self, name, **_kwargs): self.stages.append(name)
    def config(self, name, default=None):
        self.config_keys[name] = default; return default


class _StubContext:
    """synpp's ExecuteContext contract: ``stage(name)``, SINGLE-arg ``config(name)``.

    Both accessors fail loudly on anything ``configure`` did not declare, so a stage that reads an
    undeclared key or stage cannot pass the way a permissive stub would.
    """

    def __init__(self, declared, stages=None, config=None):
        self._declared, self._stages, self._config = declared, stages or {}, config or {}
    def stage(self, name):
        assert name in self._declared.stages, f"stage {name!r} not declared"
        return self._stages[name]
    def config(self, name):
        assert name in self._declared.config_keys, f"config {name!r} not declared"
        return self._config[name]


def _context(stages, config):
    recorder = _ConfigureRecorder(); S.configure(recorder)
    return _StubContext(recorder, stages=stages, config=config)


def _enriched():
    return pd.DataFrame({"person_id": range(6), "household_id": [1, 2, 2, 3, 3, 3], "age": [70, 30, 32, 40, 38, 8]})


def _config(**overrides):
    base = {"random_seed": 1234, "data_path": DATA_PATH, S.KEY_ENABLED: True,
            S.KEY_HOUSEHOLD_STAGE: True, S.KEY_MAX_BAND_DEVIATION_PP: 1.0,
            S.KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE: S.DEFAULT_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE}
    base.update(overrides); return base


def test_configure_declares_exactly_the_documented_inputs():
    recorder = _ConfigureRecorder(); S.configure(recorder)
    assert recorder.stages == ["synthesis.population.enriched"]
    assert set(recorder.config_keys) == {"random_seed", "data_path", S.KEY_ENABLED, S.KEY_HOUSEHOLD_STAGE,
                                         S.KEY_MAX_BAND_DEVIATION_PP,
                                         S.KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE}


def test_configure_declares_the_individual_stage_min_household_size_default():
    recorder = _ConfigureRecorder(); S.configure(recorder)
    assert recorder.config_keys[S.KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE] == 2
    assert S.DEFAULT_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE == 2


def test_on_path_returns_one_row_per_person_with_the_documented_columns():
    out = S.execute(_context({"synthesis.population.enriched": _enriched()}, _config()))
    absence = out["absence"]
    assert list(absence.columns) == list(D.ABSENCE_COLUMNS) and len(absence) == 6
    assert set(absence["day_absence_state"]) <= set(D.STATES)
    assert out["diagnostics"]["enabled"] is True and "by_band" in out["diagnostics"]


def test_off_path_marks_everyone_present_without_reading_any_file(tmp_path):
    out = S.execute(_context({"synthesis.population.enriched": _enriched()},
                             _config(**{S.KEY_ENABLED: False, "data_path": str(tmp_path)})))
    assert (out["absence"]["day_absence_state"] == D.STATE_PRESENT).all()
    assert (out["absence"]["reason"] == D.REASON_DISABLED).all()
    assert out["diagnostics"] == {"enabled": False}


def test_validate_token_is_an_md5_over_the_pure_module():
    token = S.validate(None)
    assert isinstance(token, str) and len(token) == 32


def test_validate_token_changes_when_srv_absence_module_changes(monkeypatch):
    """Important finding 3 (final-review fix wave): the token must ALSO cover
    braunschweig.calibration.srv_absence, not absence.py alone -- the age-band edges/labels and
    the household-size-class top (what a person is drawn AGAINST) live there, so an edit there
    must devalidate the stage's cache exactly like an edit to the draw rule in absence.py."""
    token = S.validate(None)
    real_getsource = S.inspect.getsource

    def patched_getsource(module):
        if module is S.srv_absence:
            return real_getsource(module) + "\n# a changed age band edge\n"
        return real_getsource(module)

    monkeypatch.setattr(S.inspect, "getsource", patched_getsource)
    assert S.validate(None) != token


def test_band_deviation_guard_fires_when_realised_and_reference_diverge(monkeypatch, caplog):
    """Deferred minor (final-review fix wave): the >= 1,000-person band-deviation WARNING path.

    1,200 single-person households in one age band, with a monkeypatched reference whose
    household-size-1 probability is 1.0: the household stage marks EVERY one of them absent with
    CERTAINTY (a numpy ``random_sample`` draw is always < 1.0), so the band's realised rate is
    exactly 100% against a reference of 5% -- a 95 pp deviation, far above
    ``day_absence_max_band_deviation_pp``'s default 1.0 pp and well past
    ``MIN_PERSONS_FOR_BAND_GUARD`` (1,000), so this is not dismissed as sampling noise.
    """
    persons = pd.DataFrame({
        "person_id": range(1200), "household_id": range(1200), "age": [35] * 1200,
    })
    reference = D.AbsenceReference(
        p_absent_by_band={band: (0.05 if band == "30-44" else 0.0) for band in D.AGE_BAND_LABELS},
        p_all_absent_by_size={1: 1.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0},
        p_absent_person_by_size={k: float("nan") for k in range(1, 6)})
    monkeypatch.setattr(S, "load_absence_reference", lambda _srv_dir: reference)

    with caplog.at_level("WARNING", logger=S.logger.name):
        out = S.execute(_context({"synthesis.population.enriched": persons}, _config()))

    diagnostics = out["diagnostics"]
    band_cell = diagnostics["by_band"]["30-44"]
    assert band_cell["n"] == 1200
    assert band_cell["realised_rate"] == pytest.approx(1.0)
    assert diagnostics["n_band_guard_hits"] >= 1
    assert any("30-44" in message and "exceeds" in message for message in caplog.messages)


def test_off_frame_has_the_same_dtypes_and_derived_attributes_as_the_on_frame():
    persons = _enriched()
    on = S.execute(_context({"synthesis.population.enriched": persons}, _config()))["absence"]
    off = S.execute(_context({"synthesis.population.enriched": persons},
                             _config(**{S.KEY_ENABLED: False})))["absence"]
    assert on.dtypes.equals(off.dtypes)
    on_sorted = on.sort_values("person_id").reset_index(drop=True)
    off_sorted = off.sort_values("person_id").reset_index(drop=True)
    assert (on_sorted["age_band"] == off_sorted["age_band"]).all()
    assert (on_sorted["household_size_class"] == off_sorted["household_size_class"]).all()


# --- Issue #388: day_absence_individual_stage_min_household_size -------------------------------

def test_individual_stage_min_household_size_default_is_passed_through_to_the_draw():
    out = S.execute(_context({"synthesis.population.enriched": _enriched()}, _config()))
    assert out["diagnostics"]["individual_stage_min_household_size"] == 2
    assert "n_persons_ineligible_individual_stage" in out["diagnostics"]


def test_individual_stage_min_household_size_of_zero_raises_naming_the_key():
    context = _context({"synthesis.population.enriched": _enriched()},
                       _config(**{S.KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE: 0}))
    with pytest.raises(ValueError, match=S.KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE):
        S.execute(context)


def test_individual_stage_min_household_size_of_2_5_raises_naming_the_key():
    """int(2.5) == 2 would silently truncate a non-integral config value into a plausible-looking
    household size; this must raise rather than accept a rounded/truncated result."""
    context = _context({"synthesis.population.enriched": _enriched()},
                       _config(**{S.KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE: 2.5}))
    with pytest.raises(ValueError, match=S.KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE):
        S.execute(context)
