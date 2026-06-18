"""Tests for braunschweig.popsim.attributes — employment definition."""
import pandas as pd
import numpy as np
import pytest

from braunschweig.popsim import attributes as A


def test_employed_includes_azubi_excludes_elternzeit_and_fsj():
    persons = pd.DataFrame({"P_TAET": [1, 5, 7, 8, 10], "alter_gr1": [3, 3, 2, 2, 2]})
    out = A.map_employed(persons, rng=np.random.RandomState(0))
    # 1 employed; 5 (Elternzeit) NOT; 7 (FSJ) NOT; 8 (Azubi) employed; 10 (Student) NOT
    assert list(out["employed"]) == [True, False, False, True, False]
