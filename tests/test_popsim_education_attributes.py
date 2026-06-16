import pandas as pd

from braunschweig.popsim.attributes import map_schulabschluss, map_beruflabschluss


def test_schulabschluss_three_class():
    persons = pd.DataFrame({"bildung1": [1, 3, 4, 5, 9]})
    out = map_schulabschluss(persons.copy())
    # provisional codebook mapping: 1/2 low, 3 mid, 4 high, 5 low(ohne), 9 -> NaN
    assert list(out["schulabschluss"].fillna("NA")) == ["low", "mid", "high", "low", "NA"]


def test_beruflabschluss_excludes_structural():
    persons = pd.DataFrame({"bildung2": [1, 4, 5, 9, 206, 402]})
    out = map_beruflabschluss(persons.copy())
    # 1 vocational, 4 tertiary, 5 none, 9 -> NaN(impute), 206/402 -> NaN(15+ exclude)
    assert list(out["beruflabschluss"].fillna("NA")) == ["vocational", "tertiary", "none", "NA", "NA", "NA"]
