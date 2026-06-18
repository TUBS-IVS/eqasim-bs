# tests/test_preprocess_alkis_oi.py
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location("pp_alkis", pathlib.Path("scripts/preprocess_alkis_landuse.py"))
pa = importlib.util.module_from_spec(spec); spec.loader.exec_module(pa)


def test_alkis_reads_oi():
    assert "OI" in pa.ALKIS_READ_COLUMNS
    assert pa.ALKIS_READ_COLUMNS == ["AGS", "OI", "GFK"]
