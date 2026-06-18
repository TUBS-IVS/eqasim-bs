import pandas as pd

from braunschweig.popsim.attributes import map_schulabschluss, map_beruflabschluss


def test_schulabschluss_three_class():
    # MiD 2023 Codeplan B1, bildung1 "Schulabschluss (zusammengefasst)":
    #   1=(noch) ohne Abschluss  2=niedrig  3=mittel  4=hoch  5=anderer Abschluss  9=k.A.
    # The 3-class control universe = completed allgemeinbildender Abschluss:
    #   2->low, 3->mid, 4->high. Code 1 ((noch) ohne; holds all <15 kids + current
    #   pupils -- there is no 402 code on bildung1), 5 (anderer; no Zensus pendant)
    #   and 9 (k.A.) -> NaN (excluded, like Zensus __1; imputed/floated downstream).
    persons = pd.DataFrame({"bildung1": [1, 2, 3, 4, 5, 9]})
    out = map_schulabschluss(persons.copy())
    assert list(out["schulabschluss"].fillna("NA")) == ["NA", "low", "mid", "high", "NA", "NA"]


def test_beruflabschluss_three_class():
    # MiD 2023 Codeplan B1, bildung2 "Berufs- oder Hochschulabschluss":
    #   1=ja Berufsabschluss  2=ja Hochschulabschluss  3=ja Berufs- UND Hochschul-
    #   abschluss  4=ja anderer  5=nein  9=k.A.  206=Proxy  402=Kind<14.
    # 1 -> vocational; 2 and 3 (both carry a Hochschulabschluss) -> tertiary;
    # 5 -> none; 4 (anderer, no Zensus pendant) + 9/206/402 (k.A./structural) -> NaN.
    persons = pd.DataFrame({"bildung2": [1, 2, 3, 4, 5, 9, 206, 402]})
    out = map_beruflabschluss(persons.copy())
    assert list(out["beruflabschluss"].fillna("NA")) == [
        "vocational", "tertiary", "tertiary", "NA", "none", "NA", "NA", "NA"
    ]
