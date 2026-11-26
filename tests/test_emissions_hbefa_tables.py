"""HBEFA table inspection (#688): real tables are identified, wrong or sample tables refused.

The tables are tiny synthetic files with the column layout of the MATSim sample tables; no
real HBEFA value is involved.
"""
from __future__ import annotations

import hashlib

import pytest

from braunschweig.emissions import hbefa_tables
from braunschweig.emissions.hbefa_tables import inspect_hbefa_tables

_HEADERS = {
    "warm_average": "Case;VehCat;Year;Component;RoadCat;TrafficSit;Gradient;V_weighted;EFA_weighted",
    "cold_average": "Case;VehCat;Year;Component;RoadCat;AmbientCondPattern;EFA_weighted",
    "warm_detailed": "Case;VehCat;Component;TrafficSit;Technology;SizeClasse;EmConcept;V;EFA",
    "cold_detailed": "Case;VehCat;Component;AmbientCondPattern;Technology;SizeClasse;EmConcept;EFA",
}


def _write_tables(folder, *, bom_on="warm_average"):
    paths = {}
    for kind, header in _HEADERS.items():
        row = ";".join([f"Test{kind}[9.9]"] + ["x"] * (header.count(";")))
        text = f"{header}\n{row}\n{row}\n"
        data = text.encode("utf-8")
        if kind == bom_on:
            data = b"\xef\xbb\xbf" + data
        path = folder / f"EFA_{kind}.csv"
        path.write_bytes(data)
        paths[kind] = path
    return paths


def test_valid_tables_are_identified(tmp_path):
    paths = _write_tables(tmp_path)

    report = inspect_hbefa_tables(paths)

    assert set(report) == set(_HEADERS)
    warm = report["warm_average"]  # written with a UTF-8 byte order mark
    assert warm["data_rows"] == 2
    assert warm["case"] == "Testwarm_average[9.9]"
    assert warm["sha256"] == hashlib.sha256(paths["warm_average"].read_bytes()).hexdigest()
    assert warm["path"] == str(paths["warm_average"].resolve())


def test_missing_wrong_and_duplicate_tables_are_refused(tmp_path):
    paths = _write_tables(tmp_path)

    with pytest.raises(ValueError, match="cold_detailed"):
        inspect_hbefa_tables({k: v for k, v in paths.items() if k != "cold_detailed"})
    with pytest.raises(FileNotFoundError, match="warm_detailed"):
        inspect_hbefa_tables({**paths, "warm_detailed": tmp_path / "absent.csv"})
    # The average table handed in as the detailed one lacks the sub-segment columns.
    average_copy = tmp_path / "EFA_average_copy.csv"
    average_copy.write_bytes(paths["warm_average"].read_bytes())
    with pytest.raises(ValueError, match=r"lacks the columns \['Technology'"):
        inspect_hbefa_tables({**paths, "warm_detailed": average_copy})
    with pytest.raises(ValueError, match="same file"):
        inspect_hbefa_tables({**paths, "cold_average": paths["warm_average"]})
    header_only = tmp_path / "EFA_empty.csv"
    header_only.write_text(_HEADERS["cold_average"] + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no data rows"):
        inspect_hbefa_tables({**paths, "cold_average": header_only})


def test_matsim_sample_tables_are_refused_by_name_and_by_hash(tmp_path, monkeypatch):
    paths = _write_tables(tmp_path)

    by_name = tmp_path / "sample_41_EFA_HOT_vehcat_2020average.csv"
    by_name.write_bytes(paths["warm_average"].read_bytes())
    with pytest.raises(ValueError, match="sample table"):
        inspect_hbefa_tables({**paths, "warm_average": by_name})

    # A renamed copy is still caught by its content hash.
    renamed_digest = hashlib.sha256(paths["cold_average"].read_bytes()).hexdigest()
    monkeypatch.setattr(hbefa_tables, "SAMPLE_TABLE_SHA256", frozenset({renamed_digest}))
    with pytest.raises(ValueError, match="sample table"):
        inspect_hbefa_tables(paths)
