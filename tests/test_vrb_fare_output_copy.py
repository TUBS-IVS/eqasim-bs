"""matsim.output exports the VRB fare inputs next to <prefix>config.xml under their prefixed names."""
import json

import pytest

from matsim import output


def _prepared(tmp_path, prefix):
    prepare_dir = tmp_path / "prepare"
    prepare_dir.mkdir(exist_ok=True)
    names = [f"{prefix}vrb_fare_model_2026-06-20.json", f"{prefix}vrb_line_scopes.csv", f"{prefix}vrb_fare_inputs_report.json"]
    for name in names[:2]:
        (prepare_dir / name).write_text(name, encoding="utf-8")
    (prepare_dir / names[2]).write_text(json.dumps({"fare_input_files": names}), encoding="utf-8")
    (prepare_dir / "vrb_tariff_zones.shp").write_text("working file", encoding="utf-8")
    return prepare_dir, names


def test_output_copies_exactly_the_listed_fare_inputs_of_each_prefix(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    prepare_dir, first = _prepared(tmp_path, "a_")
    output.copy_vrb_fare_inputs(prepare_dir, out, "a_")
    _, second = _prepared(tmp_path, "b_")
    output.copy_vrb_fare_inputs(prepare_dir, out, "b_")
    # Two scenario prefixes in one output_path keep their own fare inputs; working files stay behind.
    assert sorted(path.name for path in out.iterdir()) == sorted(first + second)


def test_output_fails_when_a_listed_fare_input_is_missing(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    prepare_dir, names = _prepared(tmp_path, "a_")
    (prepare_dir / names[0]).unlink()
    with pytest.raises(FileNotFoundError):
        output.copy_vrb_fare_inputs(prepare_dir, out, "a_")
