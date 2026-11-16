"""Tests for the switch that drops PopulationSim's ``write_data_dictionary`` step.

Covers ``braunschweig.popsim.folders.drop_data_dictionary_step`` /
``resolve_batch_settings_yaml`` and the stage wiring of
``braunschweig.population.popsim.write_data_dictionary``.
"""

from __future__ import annotations

import logging

import pytest

from braunschweig.popsim import folders
from braunschweig.popsim.stage import config_keys

STEP_LINE = "    - write_data_dictionary\n"

SETTINGS = """\
# PopulationSim settings (test fixture)
geographies: [WELT, STAAT, KREIS, ZENSUS1km, ZENSUS100m]
seed_geography: STAAT
max_expansion_factor: 100
models:
    - input_pre_processor
    - setup_data_structures
    - initial_seed_balancing
    - meta_control_factoring
    - final_seed_balancing
    - integerize_final_seed_weights
    - sub_balancing.geography=ZENSUS1km
    - sub_balancing.geography=ZENSUS100m
    - expand_households
    - write_data_dictionary
    - summarize
    - write_tables
    - write_synthetic_population
output_tables:
  action: include
  tables: [summary_ZENSUS100m, expanded_household_ids]
"""


def test_drop_removes_only_the_step_line():
    assert folders.drop_data_dictionary_step(SETTINGS) == SETTINGS.replace(STEP_LINE, "")


def test_drop_preserves_crlf_line_endings_and_trailing_comment():
    commented = "    - write_data_dictionary  # diagnostics only\n"
    text = SETTINGS.replace(STEP_LINE, commented).replace("\n", "\r\n")
    result = folders.drop_data_dictionary_step(text)
    assert result == text.replace(commented.replace("\n", "\r\n"), "")


@pytest.mark.parametrize("text, message", [
    (SETTINGS.replace(STEP_LINE, ""), "exactly once"),
    (SETTINGS.replace("    - summarize\n", STEP_LINE + "    - summarize\n"), "exactly once"),
    ("geographies: [STAAT]\nmodels: [setup_data_structures, write_data_dictionary]\n",
     "block-list line"),
    # Listed once in models (flow style) and once as a block item of another key: the
    # textual cut would hit the wrong list, so the parse check must refuse it.
    ("models: [setup_data_structures, write_data_dictionary]\n"
     "other_list:\n  - write_data_dictionary\n", "changed more than"),
], ids=["missing", "twice", "flow-list", "line-outside-models"])
def test_drop_refuses_settings_it_cannot_cut_cleanly(text, message):
    with pytest.raises(ValueError, match=message):
        folders.drop_data_dictionary_step(text)


class _ExecuteContext:
    """synpp execute() context double: ``config(key)`` takes ONE argument, like synpp's."""

    def __init__(self, values):
        self._values = values

    def config(self, key):
        return self._values[key]


@pytest.mark.parametrize("switch, expected", [
    (True, SETTINGS),
    (False, SETTINGS.replace(STEP_LINE, "")),
], ids=["on-byte-identical", "off-drops-step"])
def test_stage_writes_batch_settings_per_switch_and_logs(tmp_path, caplog, switch, expected):
    from braunschweig.popsim import stage

    settings_path = tmp_path / "settings.yaml"
    settings_path.write_bytes(SETTINGS.encode("utf-8"))
    context = _ExecuteContext({config_keys.KEY_WRITE_DATA_DICTIONARY: switch})
    with caplog.at_level(logging.INFO, logger=folders.__name__):
        assert stage._batch_settings_yaml(context, settings_path) == expected
    assert "write_data_dictionary" in caplog.text


class _RecordingContext:
    """Minimal synpp configure() context that records declared config keys."""

    def __init__(self):
        self.declared = {}

    def config(self, key, default=None, **kwargs):
        self.declared[key] = (default, kwargs)
        return default

    def stage(self, *args, **kwargs):
        return None


def test_stage_declares_switch_volatile_with_default_true():
    # Volatile because the switch changes no synthetic household or person (identity
    # proof in the run manifest the key's comment names): flipping it must not throw
    # away the expensive popsim stage cache.
    from braunschweig.popsim import stage

    context = _RecordingContext()
    stage.configure(context)
    default, kwargs = context.declared[config_keys.KEY_WRITE_DATA_DICTIONARY]
    assert default is True
    assert kwargs.get("volatile") is True
