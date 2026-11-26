"""Stage ``braunschweig.data.hbefa`` (#688): licensed tables come from the config, never a default.

Defects these tests catch: a run that proceeds without real tables (a default, a download), a
replaced table that leaves the cached result in place, and a refused table without a trace.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from braunschweig.data import hbefa as stage
from tests.emissions_tables import write_tables


class _Context:
    """Execute- and validate-time context: ``config(key)`` takes the key alone, as in synpp."""

    def __init__(self, tmp_path, paths):
        self.values = {stage.KEY_TABLE_PATHS[kind]: str(path) for kind, path in paths.items()}
        self.stage_dir = tmp_path / "stage"
        self.stage_dir.mkdir(exist_ok=True)

    def config(self, key):
        return self.values[key]

    def path(self):
        return str(self.stage_dir)


class _Recording:
    def __init__(self):
        self.declared = {}

    def config(self, key, *default):
        self.declared[key] = default


def test_execute_identifies_the_tables_and_returns_their_key_index(tmp_path):
    paths = write_tables(tmp_path / "tables", hgv_cold=True)
    context = _Context(tmp_path, paths)

    result = stage.execute(context)

    assert set(result["tables"]) == set(paths)
    for kind, path in paths.items():
        assert result["tables"][kind]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    # 2 categories x 2 road types x 2 traffic situations in the warm average table.
    assert len(result["keys"]["warm_average"]["lookups"]) == 8
    report = json.loads((context.stage_dir / result["report_path"]).read_text(encoding="utf-8"))
    assert report["tables"] == result["tables"]
    assert report["keys"]["warm_average"]["lookup_keys"] == 8
    assert report["keys"]["cold_detailed"]["categories"]["PASSENGER_CAR"]["vehicle_keys"] == 1


def test_the_token_follows_the_table_content(tmp_path):
    paths = write_tables(tmp_path / "tables")
    context = _Context(tmp_path, paths)

    token = stage.validate(context)

    assert token == stage.validate(context)
    assert token["files"]["warm_average"] == hashlib.sha256(paths["warm_average"].read_bytes()).hexdigest()
    assert set(token["modules"]) >= {"braunschweig.emissions.hbefa_tables", "braunschweig.emissions.table_keys"}
    with paths["cold_detailed"].open("ab") as handle:
        handle.write(b"\n")
    assert stage.validate(context)["files"]["cold_detailed"] != token["files"]["cold_detailed"]
    paths["cold_average"].unlink()
    with pytest.raises(RuntimeError, match=r"HBEFA table 'cold_average' \(hbefa_cold_average_path\) not found"):
        stage.validate(context)


def test_the_paths_have_no_default_and_an_unset_path_stops_the_stage(tmp_path):
    recording = _Recording()
    stage.configure(recording)
    # Declared with the key alone: synpp then refuses a configuration that lacks the key.
    assert recording.declared == dict.fromkeys(stage.KEY_TABLE_PATHS.values(), ())

    context = _Context(tmp_path, write_tables(tmp_path / "tables"))
    context.values["hbefa_warm_detailed_path"] = None
    for call in (stage.execute, stage.validate):
        with pytest.raises(ValueError, match=r"hbefa_warm_detailed_path.*never downloaded"):
            call(context)


def test_a_table_with_repeated_keys_is_refused_after_the_report_is_written(tmp_path):
    second_gradient = [("pass. car", "CO", "URB/Local/50/Freeflow", "+2%")]
    context = _Context(tmp_path, write_tables(tmp_path / "tables", extra_warm_average=second_gradient))

    with pytest.raises(ValueError, match="silently keeps the later row"):
        stage.execute(context)

    report = json.loads((context.stage_dir / stage.REPORT_FILE_NAME).read_text(encoding="utf-8"))
    assert report["keys"]["warm_average"]["duplicate_rows"] == 1
