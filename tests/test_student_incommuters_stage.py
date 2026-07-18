import numpy as np
import pandas as pd
import pytest
from braunschweig.synthesis import student_incommuters as si


class Ctx:
    """Minimal synpp-style context stub: config dict only (no stages needed for
    the guard tests)."""
    def __init__(self, cfg):
        self._cfg = cfg

    def config(self, key, default=si._SENTINEL):
        if key in self._cfg:
            return self._cfg[key]
        if default is si._SENTINEL:
            raise KeyError(key)
        return default


def test_disabled_when_cordon_off():
    frames = si.execute(Ctx({"cordon_enabled": False}))
    assert frames["persons"].empty


def test_skip_when_parent_off_and_flag_default():
    # education_gravity OFF + flag left at default (None) -> skip, empty frames.
    ctx = Ctx({"cordon_enabled": True, "education_gravity_enabled": False,
               "cordon_student_incommuters_enabled": None})
    frames = si.execute(ctx)
    assert frames["persons"].empty


def test_raise_when_flag_explicit_on_but_parent_off():
    ctx = Ctx({"cordon_enabled": True, "education_gravity_enabled": False,
               "cordon_student_incommuters_enabled": True})
    with pytest.raises(RuntimeError, match="education_gravity_enabled"):
        si.execute(ctx)
