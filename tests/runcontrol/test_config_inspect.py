from braunschweig.runcontrol.collectors import config_inspect

TEMPLATE = """
run:
  - synthesis.output
  - matsim.output
config:
  sampling_rate: 0.25
  random_seed: 1234
  matsim_last_iteration: 9
  freight_enabled: true
  some_exotic_flag: 42
"""


def test_inspect_groups_curated_flags_and_counts_uncurated():
    res = config_inspect.inspect(TEMPLATE)
    assert res.run_list == ["synthesis.output", "matsim.output"]
    general = {f["key"]: f for f in res.groups["General"]}
    assert general["sampling_rate"]["value"] == 0.25
    assert general["sampling_rate"]["unit"] == "fraction"
    assert res.uncurated_count == 1                      # some_exotic_flag -- visible, not editable


def test_inspect_marks_flags_absent_from_template():
    res = config_inspect.inspect(TEMPLATE)
    ext = {f["key"]: f for g in res.groups.values() for f in g}
    assert ext["cordon_enabled"]["value"] is None and ext["cordon_enabled"]["in_template"] is False


def test_diff_reports_only_real_changes():
    d = config_inspect.diff(TEMPLATE, {"matsim_last_iteration": 199, "sampling_rate": 0.25})
    assert d == [{"key": "matsim_last_iteration", "old": 9, "new": 199}]


def test_diff_rejects_uncurated_keys():
    import pytest
    with pytest.raises(ValueError, match="not in the curated registry"):
        config_inspect.diff(TEMPLATE, {"some_exotic_flag": 7})
