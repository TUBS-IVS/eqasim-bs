"""Tests for the tracked PopulationSim integerizer settings and the per-batch seed.

Covers ``braunschweig.popsim.folders`` (``apply_populationsim_overrides``,
``populationsim_overrides_from_config``, ``batch_integerizer_seed``,
``with_integerizer_seed``, ``resolve_batch_settings_yaml``), the ``integerizer_seed_base``
keyword of ``braunschweig.popsim.mid.run_popsim_mid``, the stage wiring and the batch
signature (issue #645, task E3). The settings keys are those of the group's PopulationSim
fork (version 0.10.0+bs.1); every one defaults to upstream behaviour in the fork.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import pandas as pd
import pytest
import yaml

from braunschweig.popsim import batch, folders, mid, stage
from braunschweig.popsim.stage import config_keys
from tests.test_popsim_data_dictionary_switch import SETTINGS, STEP_LINE
from tests.test_popsim_mid import _orchestration_inputs
from tests.test_popsim_work_dir_guard import _sig as _base_signature

NEUTRAL = {
    "KEEP_FRACTIONAL_SAMPLE_WEIGHTS": False,
    "INTEGERIZER_DRAW": "off",
    "INTEGERIZER_TIMEOUT": 7200,
    "INTEGERIZER_PATH_RECORD": True,
}


def _overrides(**changes):
    values = dict(NEUTRAL)
    values.update(changes)
    return values


def test_overrides_appended_and_parse(caplog):
    with caplog.at_level(logging.INFO, logger=folders.__name__):
        result = folders.apply_populationsim_overrides(SETTINGS, NEUTRAL)
    # Appended after every byte of the source text: a comment line, then one KEY: value
    # line per override.
    assert result.startswith(SETTINGS)
    tail = result[len(SETTINGS):]
    assert tail.count("\n") == len(NEUTRAL) + 1
    assert tail.splitlines()[0].startswith("#")
    expected = dict(yaml.safe_load(SETTINGS))
    expected.update(NEUTRAL)
    assert yaml.safe_load(result) == expected
    assert "KEEP_FRACTIONAL_SAMPLE_WEIGHTS" in caplog.text
    assert "INTEGERIZER_TIMEOUT" in caplog.text


def test_draw_value_is_written_quoted_so_off_stays_a_string():
    # YAML 1.1 reads a bare ``off`` as boolean False; the tool needs the string.
    result = folders.apply_populationsim_overrides(SETTINGS, NEUTRAL)
    assert "INTEGERIZER_DRAW: 'off'" in result
    assert yaml.safe_load(result)["INTEGERIZER_DRAW"] == "off"
    gumbel = folders.apply_populationsim_overrides(SETTINGS, _overrides(INTEGERIZER_DRAW="gumbel"))
    assert yaml.safe_load(gumbel)["INTEGERIZER_DRAW"] == "gumbel"


def test_override_without_trailing_newline_and_with_crlf():
    no_newline = folders.apply_populationsim_overrides("a: 1", NEUTRAL)
    parsed = yaml.safe_load(no_newline)
    assert parsed["a"] == 1
    assert parsed["INTEGERIZER_TIMEOUT"] == 7200
    crlf = folders.apply_populationsim_overrides(SETTINGS.replace("\n", "\r\n"), NEUTRAL)
    assert crlf.count("\n") == crlf.count("\r\n")


@pytest.mark.parametrize("raw, key", [
    ("INTEGERIZER_TIMEOUT: 60\n", "INTEGERIZER_TIMEOUT"),
    ("INTEGIZER_TIMEOUT: 60\n", "INTEGIZER_TIMEOUT"),
    ("INTEGERIZER_DRAW: pareto\n", "INTEGERIZER_DRAW"),
    ("INTEGERIZER_DRAW: off\n", "INTEGERIZER_DRAW"),  # bare off is boolean False, not 'off'
    ("KEEP_FRACTIONAL_SAMPLE_WEIGHTS: true\n", "KEEP_FRACTIONAL_SAMPLE_WEIGHTS"),
    ("INTEGERIZER_PATH_RECORD: false\n", "INTEGERIZER_PATH_RECORD"),
], ids=["timeout", "timeout-alias", "draw", "draw-bare-off", "keep", "path-record"])
def test_override_conflicting_raw_value_raises(raw, key):
    with pytest.raises(ValueError, match=key):
        folders.apply_populationsim_overrides(SETTINGS + raw, NEUTRAL)


def test_override_equal_raw_value_is_accepted_without_a_duplicate_key():
    raw = SETTINGS + "INTEGIZER_TIMEOUT: 7200\nINTEGERIZER_PATH_RECORD: true\n"
    result = folders.apply_populationsim_overrides(raw, NEUTRAL)
    assert result.count("INTEGERIZER_PATH_RECORD") == 1
    parsed = yaml.safe_load(result)
    assert parsed["INTEGIZER_TIMEOUT"] == 7200
    assert parsed["INTEGERIZER_TIMEOUT"] == 7200


@pytest.mark.parametrize("changes, key", [
    ({"INTEGERIZER_TIMEOUT": 0}, "INTEGERIZER_TIMEOUT"),
    ({"INTEGERIZER_TIMEOUT": -5}, "INTEGERIZER_TIMEOUT"),
    ({"INTEGERIZER_TIMEOUT": 1.5}, "INTEGERIZER_TIMEOUT"),
    ({"INTEGERIZER_TIMEOUT": True}, "INTEGERIZER_TIMEOUT"),
    ({"INTEGERIZER_DRAW": "yes"}, "INTEGERIZER_DRAW"),
    ({"INTEGERIZER_DRAW": False}, "INTEGERIZER_DRAW"),
    ({"KEEP_FRACTIONAL_SAMPLE_WEIGHTS": "true"}, "KEEP_FRACTIONAL_SAMPLE_WEIGHTS"),
    ({"INTEGERIZER_PATH_RECORD": 1}, "INTEGERIZER_PATH_RECORD"),
    ({"SOME_OTHER_KEY": 1}, "SOME_OTHER_KEY"),
])
def test_override_type_validation(changes, key):
    with pytest.raises(ValueError, match=key):
        folders.apply_populationsim_overrides(SETTINGS, _overrides(**changes))


def test_overrides_from_config_validates_and_orders():
    assert folders.populationsim_overrides_from_config(
        keep_fractional_sample_weights=False, integerizer_draw="off",
        integerizer_timeout_s=7200) == NEUTRAL
    with pytest.raises(ValueError, match="INTEGERIZER_DRAW"):
        folders.populationsim_overrides_from_config(
            keep_fractional_sample_weights=False, integerizer_draw="yes",
            integerizer_timeout_s=7200)


def test_batch_seed_is_stable_distinct_and_32bit():
    names = [f"batch_{i:03d}" for i in range(60)]
    seeds = [folders.batch_integerizer_seed(20260301, name) for name in names]
    assert len(set(seeds)) == 60
    assert all(0 <= seed < 2**32 for seed in seeds)
    assert seeds == [folders.batch_integerizer_seed(20260301, name) for name in names]
    other = [folders.batch_integerizer_seed(20260302, name) for name in names]
    assert not set(seeds) & set(other)


def test_batch_seed_pinned_derivation():
    # Pins the derivation (sha256 of the documented string, first four bytes, little
    # endian); a change would silently move every batch's integerizer noise.
    digest = hashlib.sha256(b"populationsim-integerizer|7|batch_000").digest()
    assert folders.batch_integerizer_seed(7, "batch_000") == int.from_bytes(digest[:4], "little")


def test_with_integerizer_seed_appends_and_refuses_an_existing_key():
    result = folders.with_integerizer_seed(SETTINGS, 123)
    assert result.startswith(SETTINGS)
    assert yaml.safe_load(result)["INTEGERIZER_RANDOM_SEED"] == 123
    with pytest.raises(ValueError, match="INTEGERIZER_RANDOM_SEED"):
        folders.with_integerizer_seed(result, 456)
    for bad in (-1, 2**32, 1.5, True):
        with pytest.raises(ValueError, match="INTEGERIZER_RANDOM_SEED"):
            folders.with_integerizer_seed(SETTINGS, bad)


def test_drop_data_dictionary_still_applied():
    result = folders.resolve_batch_settings_yaml(SETTINGS, False, NEUTRAL)
    assert STEP_LINE not in result
    parsed = yaml.safe_load(result)
    assert "write_data_dictionary" not in parsed["models"]
    assert parsed["INTEGERIZER_TIMEOUT"] == 7200
    kept = folders.resolve_batch_settings_yaml(SETTINGS, True, NEUTRAL)
    assert kept == folders.apply_populationsim_overrides(SETTINGS, NEUTRAL)
    # Without overrides the text is exactly what it was before this task.
    assert folders.resolve_batch_settings_yaml(SETTINGS, True) == SETTINGS


# ---------------------------------------------------------------------------
# run_popsim_mid: one seed per batch folder
# ---------------------------------------------------------------------------

def _fake_run_one(folder):
    xwalk = pd.read_csv(Path(folder) / "data" / "geo_cross_walk.csv", dtype=str)
    out_dir = Path(folder) / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    xwalk.assign(H_ID=1).to_csv(out_dir / "final_expanded_household_ids.csv", index=False)
    return batch.BatchResult(str(folder), "succeeded", "ok", 0.0)


def _run(work_dir, seed_base):
    cells, base_cols, controls_df, seed_hh, seed_p = _orchestration_inputs()
    mid.run_popsim_mid(
        cells, base_cols, controls_df, seed_hh, seed_p,
        work_dir=work_dir, settings_yaml=SETTINGS, logging_yaml="version: 1\n",
        max_cells=2, run_one=_fake_run_one, num_workers=1,
        integerizer_seed_base=seed_base,
    )
    return sorted(Path(work_dir).glob("batch_*/configs/settings.yaml"))


def test_each_batch_folder_gets_its_own_seed(tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        files = _run(tmp_path, 4711)
    assert len(files) >= 2
    seeds = []
    for path in files:
        seeds.append(yaml.safe_load(path.read_text(encoding="utf-8"))["INTEGERIZER_RANDOM_SEED"])
        assert seeds[-1] == folders.batch_integerizer_seed(4711, path.parent.parent.name)
    assert len(set(seeds)) == len(seeds)
    # The files differ in the seed line only.
    lines = [path.read_text(encoding="utf-8").splitlines() for path in files]
    for other in lines[1:]:
        assert len(lines[0]) == len(other)
        diff = [(a, b) for a, b in zip(lines[0], other) if a != b]
        assert len(diff) == 1 and diff[0][0].startswith("INTEGERIZER_RANDOM_SEED")
    # One INFO line proves the seeds are distinct (fallback transparency).
    assert f"{len(seeds)} distinct" in caplog.text


def test_no_seed_line_when_draw_off(tmp_path):
    files = _run(tmp_path, None)
    assert files
    for path in files:
        assert "INTEGERIZER_RANDOM_SEED" not in path.read_text(encoding="utf-8")


def test_draw_on_without_seed_base_raises_before_any_folder_is_written(tmp_path):
    # A drawing integerizer without per-batch seeds would give every batch the same noise.
    cells, base_cols, controls_df, seed_hh, seed_p = _orchestration_inputs()
    drawing = folders.apply_populationsim_overrides(SETTINGS, _overrides(INTEGERIZER_DRAW="pareto"))
    with pytest.raises(ValueError, match="INTEGERIZER_DRAW") as error:
        mid.run_popsim_mid(
            cells, base_cols, controls_df, seed_hh, seed_p,
            work_dir=tmp_path, settings_yaml=drawing, logging_yaml="version: 1\n",
            max_cells=2, run_one=_fake_run_one, num_workers=1, integerizer_seed_base=None)
    assert "pareto" in str(error.value)
    assert not list(tmp_path.glob("batch_*"))


def test_seed_clash_between_batches_raises_naming_folders_and_seed(tmp_path, monkeypatch):
    # A 32-bit clash is a rare accident; forced here. Two batches with one seed would draw
    # the same integerizer noise, so the run stops and names who collides on which seed.
    monkeypatch.setattr(folders, "batch_integerizer_seed", lambda base, name: 12345)
    with pytest.raises(ValueError) as error:
        _run(tmp_path, 4711)
    message = str(error.value)
    assert "12345" in message
    assert "batch_000" in message and "batch_001" in message


def test_seed_base_is_stable_across_runs(tmp_path):
    first = {p.parent.parent.name: p.read_text(encoding="utf-8") for p in _run(tmp_path / "a", 99)}
    second = {p.parent.parent.name: p.read_text(encoding="utf-8") for p in _run(tmp_path / "b", 99)}
    assert first == second


# ---------------------------------------------------------------------------
# batch signature
# ---------------------------------------------------------------------------

def test_signature_changes_with_each_override_and_seed_base():
    base = _base_signature(settings_overrides=NEUTRAL, integerizer_seed_base=None)
    assert base == _base_signature(settings_overrides=NEUTRAL, integerizer_seed_base=None)
    seen = {base}
    for changes in (
        {"KEEP_FRACTIONAL_SAMPLE_WEIGHTS": True},
        {"INTEGERIZER_DRAW": "pareto"},
        {"INTEGERIZER_DRAW": "gumbel"},
        {"INTEGERIZER_TIMEOUT": 3600},
        {"INTEGERIZER_PATH_RECORD": False},
    ):
        signature = _base_signature(
            settings_overrides=_overrides(**changes), integerizer_seed_base=None)
        assert signature not in seen, changes
        seen.add(signature)
    for seed_base in (1, 2):
        signature = _base_signature(settings_overrides=NEUTRAL, integerizer_seed_base=seed_base)
        assert signature not in seen, seed_base
        seen.add(signature)


# ---------------------------------------------------------------------------
# stage wiring
# ---------------------------------------------------------------------------

class _ExecuteContext:
    """synpp execute() context double: ``config(key)`` takes ONE argument, like synpp's."""

    def __init__(self, values):
        self._values = values

    def config(self, key):
        return self._values[key]


def _values(**changes):
    values = {
        config_keys.KEY_KEEP_FRACTIONAL_SAMPLE_WEIGHTS: False,
        config_keys.KEY_INTEGERIZER_DRAW: "off",
        config_keys.KEY_INTEGERIZER_TIMEOUT_S: 7200,
        "random_seed": 4711,
    }
    values.update(changes)
    return values


def test_stage_reads_overrides_and_seed_base_only_when_draw_is_on():
    overrides, seed_base = stage._read_integerizer_settings(_ExecuteContext(_values()))
    assert overrides == NEUTRAL
    assert seed_base is None
    overrides, seed_base = stage._read_integerizer_settings(_ExecuteContext(_values(**{
        config_keys.KEY_INTEGERIZER_DRAW: "gumbel",
        config_keys.KEY_KEEP_FRACTIONAL_SAMPLE_WEIGHTS: True})))
    assert overrides["INTEGERIZER_DRAW"] == "gumbel"
    assert overrides["KEEP_FRACTIONAL_SAMPLE_WEIGHTS"] is True
    assert seed_base == 4711


def test_stage_rejects_an_unknown_draw():
    with pytest.raises(ValueError, match="INTEGERIZER_DRAW"):
        stage._read_integerizer_settings(_ExecuteContext(_values(**{
            config_keys.KEY_INTEGERIZER_DRAW: "yes"})))


class _RecordingContext:
    """Minimal synpp configure() context that records declared config keys."""

    def __init__(self):
        self.declared = {}

    def config(self, key, default=None, **kwargs):
        self.declared[key] = (default, kwargs)
        return default

    def stage(self, *args, **kwargs):
        return None


def test_stage_declares_neutral_defaults_non_volatile():
    context = _RecordingContext()
    stage.configure(context)
    # Neutral = upstream behaviour (the fork defaults), so a config without the keys
    # reproduces 0.10.0. Not volatile: each of them changes the synthetic population.
    for key, default in (
        (config_keys.KEY_KEEP_FRACTIONAL_SAMPLE_WEIGHTS, False),
        (config_keys.KEY_INTEGERIZER_DRAW, "off"),
        (config_keys.KEY_INTEGERIZER_TIMEOUT_S, 60),
    ):
        declared_default, kwargs = context.declared[key]
        assert declared_default == default
        assert not kwargs.get("volatile")


def test_base_config_states_the_tracked_values():
    config = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "configs" / "base_bs.yml")
        .read_text(encoding="utf-8"))["config"]
    assert config[config_keys.KEY_KEEP_FRACTIONAL_SAMPLE_WEIGHTS] is False
    assert config[config_keys.KEY_INTEGERIZER_DRAW] == "off"
    assert config[config_keys.KEY_INTEGERIZER_TIMEOUT_S] == 7200
