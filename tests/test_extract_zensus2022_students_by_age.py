"""Extraction of the Zensus 2022 pupils and students without paid work, age 20 to 29 (#140).

The census database delivers table 2000S-2001 as JSON-stat style files, one per region level
(Kreis, Land, Germany) plus a structure file with the labels. Each data file holds two datasets:
the detailed activity status (``ERWST4``) and the coarse one (``ERWST3``, whose total is all
persons), both crossed with the ten-year age groups and the region. A withheld cell has the
value 0 and the status flag ``/``. These tests build small files of that shape in ``tmp_path``.
"""
from __future__ import annotations

import itertools
import json

import pandas as pd
import pytest

from scripts import extract_zensus2022_students_by_age as ex

AGE_CODES = ["%TOTAL%", "ALT000B009", "ALT010B019", "ALT020B029", "ALT030B039"]
LABELS = {"03101": "Braunschweig, Stadt", "03151": "Gifhorn", "03": "Niedersachsen", "DG": "Deutschland"}


def _dataset(region_variable, regions, status_variable, status_codes, cell, withheld=()):
    """One dataset; ``cell(region, status, age)`` gives the count, ``withheld`` is a set of keys."""
    values, flags = [], []
    for status in status_codes:
        for age in AGE_CODES:
            for region in regions:
                if (region, status, age) in withheld:
                    values.append(0.0)
                    flags.append("/")
                else:
                    values.append(float(cell(region, status, age)))
                    flags.append("e")
    index = lambda codes: {"category": {"index": {code: i for i, code in enumerate(codes)}}}
    return {
        "id": ["STAG", "statistic", "content", status_variable, "ALTGR2", region_variable],
        "size": [1, 1, 1, len(status_codes), len(AGE_CODES), len(regions)],
        "dimension": {
            "STAG": index(["2022-05-15"]), "statistic": index(["2000S"]),
            "content": index(["PRS004$ID0004"]), status_variable: index(status_codes),
            "ALTGR2": index(AGE_CODES), region_variable: index(regions),
        },
        "value": values,
        "status": flags,
    }


def _cell(region, status, age):
    # Hand-set counts: other age groups 7 x scale; age 20-29: students 300 x scale, all persons
    # 2000 x scale, any other status 1000 x scale (scale 1 / 2 / 10 / 100 by region).
    scale = {"03101": 1, "03151": 2, "03": 10, "DG": 100}[region]
    if age != "ALT020B029":
        return 7 * scale
    return (300 if status == "ERW-AKTIV-X" else 1000) * scale if status != "%TOTAL%" else 2000 * scale


def _write_source(tmp_path, withheld=(), student_code="ERW-AKTIV-X", skip_region=None, unlabelled=None):
    kreise = [r for r in ["03101", "03151"] if r != skip_region]
    for name, variable, regions in (("GEOLK4", "GEOLK4", kreise), ("GEOBL1", "GEOBL1", ["03"]),
                                    ("GEODL1", "GEODL1", ["DG"])):
        data = {"data": [
            _dataset(variable, regions, "ERWST4", ["ERWERBSTAETIG", student_code], _cell, withheld),
            _dataset(variable, regions, "ERWST3", ["ERWERB", "%TOTAL%"], _cell, withheld),
        ]}
        (tmp_path / f"2000S-2001_ALTGR2_{name}.json").write_text(json.dumps(data), encoding="utf-8")
    labels = {code: {"label": {"de": label}} for code, label in LABELS.items()}
    labels["ERW-AKTIV-X"] = {"label": {"de": ex.STATUS_CATEGORY}}
    variables = {"GEOLK4": ["03101", "03151"], "GEOBL1": ["03"], "GEODL1": ["DG"]}
    if unlabelled:
        labels.pop(unlabelled)
    (tmp_path / "2000S-2001_structure.json").write_text(
        json.dumps({"variableValues": labels,
                    "variables": {name: {"variableValues": codes} for name, codes in variables.items()}},
                   ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_extracts_students_and_all_persons_per_region_and_level(tmp_path):
    source = _write_source(tmp_path)
    frame = ex.extract_students_by_age(source, regions=["03101", "03", "DG"])

    assert list(frame.columns) == ["region", "region_name", "region_level", "age_group",
                                   "students_nonemployed_persons", "all_persons"]
    by_region = frame.set_index("region")
    assert by_region.loc["03101", "students_nonemployed_persons"] == 300
    assert by_region.loc["03101", "all_persons"] == 2000
    assert by_region.loc["03", "students_nonemployed_persons"] == 3000
    assert by_region.loc["DG", "all_persons"] == 200000
    assert by_region.loc["03101", "region_name"] == "Braunschweig, Stadt"
    assert dict(zip(frame["region"], frame["region_level"])) == {
        "03101": "kreis", "03": "land", "DG": "germany"}
    assert set(frame["age_group"]) == {"20-29"}


def test_regions_none_returns_every_region_in_the_files(tmp_path):
    frame = ex.extract_students_by_age(_write_source(tmp_path), regions=None)
    assert set(frame["region"]) == {"03101", "03151", "03", "DG"}


@pytest.mark.parametrize("where", ["students", "all_persons"])
def test_withheld_cell_raises(tmp_path, where):
    key = ("03101", "ERW-AKTIV-X", "ALT020B029") if where == "students" \
        else ("03101", "%TOTAL%", "ALT020B029")
    source = _write_source(tmp_path, withheld={key})
    with pytest.raises(ValueError, match="03101"):
        ex.extract_students_by_age(source, regions=["03101"])


def test_withheld_cell_of_an_unrequested_region_is_ignored(tmp_path):
    source = _write_source(tmp_path, withheld={("03151", "ERW-AKTIV-X", "ALT020B029")})
    frame = ex.extract_students_by_age(source, regions=["03101"])
    assert list(frame["region"]) == ["03101"]


def test_absent_region_raises(tmp_path):
    source = _write_source(tmp_path, skip_region="03151")
    with pytest.raises(ValueError, match="03151"):
        ex.extract_students_by_age(source, regions=["03101", "03151"])


def test_renamed_status_category_raises(tmp_path):
    source = _write_source(tmp_path, student_code="ERW-RENAMED")
    with pytest.raises(ValueError, match="ERW-AKTIV-X"):
        ex.extract_students_by_age(source, regions=["03101"])


def test_students_above_all_persons_raises(tmp_path):
    # A mislabelled column would swap the two counts; the invariant students <= all must catch it.
    source = _write_source(tmp_path)
    path = source / "2000S-2001_ALTGR2_GEOLK4.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    coarse = data["data"][1]
    # raise the ERWST3 total of Braunschweig below its student count (300): set it to 100
    regions = coarse["dimension"]["GEOLK4"]["category"]["index"]
    ages = coarse["dimension"]["ALTGR2"]["category"]["index"]
    status = coarse["dimension"]["ERWST3"]["category"]["index"]
    position = (status["%TOTAL%"] * len(ages) + ages["ALT020B029"]) * len(regions) + regions["03101"]
    coarse["value"][position] = 100.0
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="03101"):
        ex.extract_students_by_age(source, regions=["03101"])


def test_region_without_a_label_raises(tmp_path):
    source = _write_source(tmp_path, unlabelled="03151")
    with pytest.raises(ValueError, match="03151"):
        ex.extract_students_by_age(source, regions=["03151"])


def _permute(dataset, order):
    """Reorder the dimensions of a dataset: id, size and the flat value/status arrays together."""
    old_size = dataset["size"]
    strides = [1] * len(old_size)
    for i in range(len(old_size) - 2, -1, -1):
        strides[i] = strides[i + 1] * old_size[i + 1]
    new_size = [old_size[i] for i in order]
    values, flags = [], []
    for coords in itertools.product(*[range(n) for n in new_size]):
        flat = sum(coords[k] * strides[order[k]] for k in range(len(order)))
        values.append(dataset["value"][flat])
        flags.append(dataset["status"][flat])
    dataset["id"] = [dataset["id"][i] for i in order]
    dataset["size"] = new_size
    dataset["value"], dataset["status"] = values, flags


def test_dimension_order_may_differ_between_files(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    regions = ["03101", "03151", "03", "DG"]
    reference = ex.extract_students_by_age(_write_source(tmp_path / "a"), regions=regions)
    source = _write_source(tmp_path / "b")
    # Region before the age group in the Land and Germany files; region first in the Kreis file.
    orders = {"GEOLK4": [5, 0, 1, 2, 3, 4], "GEOBL1": [0, 1, 2, 3, 5, 4], "GEODL1": [0, 1, 2, 3, 5, 4]}
    for suffix, order in orders.items():
        path = source / f"2000S-2001_ALTGR2_{suffix}.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        for dataset in document["data"]:
            _permute(dataset, order)
        path.write_text(json.dumps(document), encoding="utf-8")
    assert ex.extract_students_by_age(source, regions=regions).equals(reference)


def test_written_csv_carries_provenance_and_round_trips(tmp_path):
    (tmp_path / "raw").mkdir()
    source = _write_source(tmp_path / "raw")
    destination = tmp_path / "out" / "zensus2022_students_20_29_kreis.csv"

    ex.main(["--source-dir", str(source), "--out", str(destination), "--regions", "03101", "03", "DG"])

    text = destination.read_text(encoding="utf-8")
    assert text.startswith("# ")
    assert "2000S-2001" in text and "2022-05-15" in text and "sha256" in text
    frame = pd.read_csv(destination, comment="#", dtype={"region": str})
    assert frame.equals(ex.extract_students_by_age(source, regions=["03101", "03", "DG"]))


@pytest.mark.skipif(not ex.OUTPUT_PATH.exists(),
                    reason=f"local-only reference table {ex.OUTPUT_PATH} is absent; regenerate it "
                           "with scripts/extract_zensus2022_students_by_age.py")
def test_real_table_covers_the_zgb_kreise():
    frame = pd.read_csv(ex.OUTPUT_PATH, comment="#", dtype={"region": str})
    from braunschweig.calibration.srv_distance_targets import ZGB_KREISE

    assert set(ZGB_KREISE) | {"03", "DG"} <= set(frame["region"])
    wanted = frame[frame["region"].isin(set(ZGB_KREISE) | {"03", "DG"})]
    assert (wanted["students_nonemployed_persons"] > 0).all()
    assert (wanted["students_nonemployed_persons"] < wanted["all_persons"]).all()
