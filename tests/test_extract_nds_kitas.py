from scripts.extract_nds_kitas import parse_kita_rows, filter_zgb_units


def test_parse_kita_rows_extracts_code_name_plaetze():
    rows = [
        ["101     Braunschweig,Stadt", "211", "-", "57", "56", "98", "13112", "2772"],
        ["151401  Boldecker Land, SG", "13", "-", "7", "2", "4", "656", "160"],
        ["", ""],
    ]
    out = parse_kita_rows(rows)
    assert {"lsn_code": "101", "name": "Braunschweig,Stadt",
            "einrichtungen": 211.0, "plaetze": 13112.0} in out
    boldeck = [r for r in out if r["lsn_code"] == "151401"][0]
    assert boldeck["plaetze"] == 656.0


def test_filter_zgb_units_keeps_kreisfrei_3digit_and_landkreis_6digit():
    rows = [
        {"lsn_code": "101", "name": "BS", "einrichtungen": 1.0, "plaetze": 13112.0},
        {"lsn_code": "151", "name": "Gifhorn (Kreis total)", "einrichtungen": 1.0, "plaetze": 9453.0},
        {"lsn_code": "151009", "name": "Gifhorn Stadt", "einrichtungen": 1.0, "plaetze": 2240.0},
        {"lsn_code": "241001", "name": "Hannover (out)", "einrichtungen": 1.0, "plaetze": 99.0},
    ]
    out = filter_zgb_units(rows)
    codes = {r["lsn_code"] for r in out}
    assert codes == {"101", "151009"}
