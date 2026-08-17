"""Tests for the flag-gated sector-aware destination attraction.

Model-improvement item #8: work destinations are drawn from a doubly-
constrained gravity model whose destination *attraction* vector is the
employees-at-workplace count per Gemeinde
(``braunschweig.data.census.employees`` -> ``employees``). That count is a
pure headcount/size proxy and discards the sectoral composition of a Gemeinde.

Behind the new flag ``braunschweig.gravity.sector_aware_enabled`` (default
``False``) the attraction is tilted by the per-Gemeinde establishment count
``n_betriebe`` (BA Gemeindedaten) relative to its Kreis mean, so within a Kreis
workplace attraction shifts toward establishment-dense Gemeinden (a sectoral
structure signal: service-heavy Gemeinden host many establishments per
employee, single-large-employer Gemeinden few). The tilt is normalised to mean
1 *within each Kreis*, so the Kreis-level attraction total is preserved and the
subsequent BA-Pendler IPF calibration (which constrains Kreis-pair flows) is
left consistent; only the sub-Kreis (Gemeinde) attraction is reshaped.

The OFF path must be byte-identical to the legacy headcount attraction (proven
below). Synthetic frames only (the env has broken LAPACK; no GLM is run here).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.gravity.model import apply_sector_aware_attraction  # noqa: E402


def _employees() -> pd.DataFrame:
    """Two Gemeinden in the SAME Kreis (03101...), one in another (03153...).

    Within Kreis 03101:
    - ``g_big_employer``  (031010000000): 1000 employees, only 5 Betriebe
      (one dominant large employer -> low establishment density).
    - ``g_service``       (031010010000): 1000 employees, 200 Betriebe
      (service-heavy -> high establishment density).
    Both have equal headcount, so the legacy attraction splits the Kreis 50/50.
    """
    return pd.DataFrame(
        {
            "commune_id": ["031010000000", "031010010000", "031530000000"],
            "employees": [1000.0, 1000.0, 500.0],
        }
    )


def _betriebe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "commune_id": ["031010000000", "031010010000", "031530000000"],
            "n_betriebe": [5, 200, 50],
        }
    )


def test_off_path_is_byte_identical():
    df = _employees()
    before = df["employees"].to_numpy(copy=True)
    out = apply_sector_aware_attraction(df, _betriebe(), enabled=False)
    assert np.array_equal(out["employees"].to_numpy(), before)
    pd.testing.assert_frame_equal(out, _employees())


def test_off_path_ignores_missing_betriebe():
    df = _employees()
    out = apply_sector_aware_attraction(df, None, enabled=False)
    pd.testing.assert_frame_equal(out, _employees())


def test_kreis_total_attraction_is_preserved():
    """The tilt must preserve each Kreis's summed attraction so the downstream
    BA-Pendler Kreis-pair IPF stays consistent (only sub-Kreis reshaping)."""
    df = _employees()
    out = apply_sector_aware_attraction(df, _betriebe(), enabled=True)

    def kreis_sum(frame: pd.DataFrame) -> pd.Series:
        k = frame.assign(kreis=frame["commune_id"].str[:5])
        return k.groupby("kreis")["employees"].sum().sort_index()

    pd.testing.assert_series_equal(kreis_sum(df), kreis_sum(out))


def test_on_path_shifts_within_kreis_toward_establishment_dense_gemeinde():
    """Equal-headcount Gemeinden in one Kreis must no longer split 50/50: the
    establishment-dense Gemeinde gains attraction, the large-employer one loses
    it, while their Kreis total is unchanged."""
    df = _employees()
    out = apply_sector_aware_attraction(df, _betriebe(), enabled=True)

    big = out[out["commune_id"] == "031010000000"]["employees"].iloc[0]
    service = out[out["commune_id"] == "031010010000"]["employees"].iloc[0]

    assert service > 1000.0 > big
    # Conservation within the Kreis.
    assert np.isclose(big + service, 2000.0)


def test_single_gemeinde_kreis_is_unchanged():
    """A Kreis with a single Gemeinde has nothing to redistribute -> its
    attraction is unchanged regardless of establishment count."""
    df = _employees()
    out = apply_sector_aware_attraction(df, _betriebe(), enabled=True)
    other = out[out["commune_id"] == "031530000000"]["employees"].iloc[0]
    assert np.isclose(other, 500.0)


def test_missing_betriebe_gemeinde_falls_back_to_neutral_tilt():
    """A Gemeinde absent from the establishment table must not vanish; it keeps
    a neutral (Kreis-mean) tilt so it is neither boosted nor zeroed."""
    df = _employees()
    betriebe = _betriebe().iloc[[1, 2]]  # drop 031010000000
    out = apply_sector_aware_attraction(df, betriebe, enabled=True)
    # Kreis total still preserved.
    kreis = out[out["commune_id"].str.startswith("03101")]["employees"].sum()
    assert np.isclose(kreis, 2000.0)
    # The present, establishment-dense Gemeinde still gains.
    service = out[out["commune_id"] == "031010010000"]["employees"].iloc[0]
    assert service >= 1000.0


def test_zero_betriebe_does_not_zero_out_attraction():
    """A Gemeinde with zero recorded establishments must keep a positive
    attraction (a neutral tilt), never collapse to zero."""
    df = _employees()
    betriebe = pd.DataFrame(
        {
            "commune_id": ["031010000000", "031010010000", "031530000000"],
            "n_betriebe": [0, 200, 50],
        }
    )
    out = apply_sector_aware_attraction(df, betriebe, enabled=True)
    big = out[out["commune_id"] == "031010000000"]["employees"].iloc[0]
    assert big > 0.0
    kreis = out[out["commune_id"].str.startswith("03101")]["employees"].sum()
    assert np.isclose(kreis, 2000.0)


def test_zero_employees_gemeinde_is_left_at_zero():
    """A Gemeinde with zero headcount attraction must stay at zero (no jobs to
    attract); the tilt is multiplicative."""
    df = pd.DataFrame(
        {
            "commune_id": ["031010000000", "031010010000"],
            "employees": [0.0, 1000.0],
        }
    )
    out = apply_sector_aware_attraction(df, _betriebe(), enabled=True)
    zero = out[out["commune_id"] == "031010000000"]["employees"].iloc[0]
    assert zero == 0.0


# ---------------------------------------------------------------------------
# Stage wiring: the flag must default OFF and must not request new config /
# stages on the OFF path (so the legacy pipeline is untouched).
# ---------------------------------------------------------------------------

FLAG = "braunschweig.gravity.sector_aware_enabled"


def test_configure_registers_flag_default_false():
    from braunschweig.gravity import model

    recorded: dict = {}

    class _StubContext:
        def config(self, option, default=None):
            recorded[option] = default
            return default

        def stage(self, name, config=None):
            return None

    model.configure(_StubContext())
    assert FLAG in recorded, "configure() must request the sector-aware flag"
    assert recorded[FLAG] is False, "the flag default must be False (OFF)"


def test_configure_off_does_not_request_sector_aware_inputs():
    """With the flag OFF the establishment-count inputs must NOT be required, so
    a config that omits ``data_path`` / the gemband path still configures."""
    from braunschweig.gravity import model

    requested_stages: list[str] = []
    requested_config: list[str] = []

    class _StubContext:
        def config(self, option, default=None):
            requested_config.append(option)
            if option == FLAG:
                return False  # flag OFF
            return default

        def stage(self, name, config=None):
            requested_stages.append(name)
            return None

    model.configure(_StubContext())
    # The codes stage is only needed for the establishment-count join.
    assert "eqasim_common.spatial.codes" not in requested_stages
    # ``data_path`` is only required to locate the gemband XLSX.
    assert "data_path" not in requested_config


def test_execute_gravity_base_off_path_passes_employees_unchanged():
    """A focused check that the OFF branch does not call into the sector-aware
    helper: ``apply_sector_aware_attraction(df, None, enabled=False)`` is the
    contract the stage relies on for byte-identity."""
    from braunschweig.gravity.model import apply_sector_aware_attraction

    df = _employees()
    out = apply_sector_aware_attraction(df, None, enabled=False)
    assert out is df  # same object, no copy, no mutation on the OFF path


# ---------------------------------------------------------------------------
# Call-site schema (#128 regression): the employees STAGE returns
# ``(commune_id, weight)`` -- NOT ``employees``. The tilt used to be applied
# to the raw stage output before the ``weight -> employees`` rename, so
# enabling the flag crashed the pipeline with ``KeyError: 'employees'``.
# ``build_destination_attraction`` owns that handoff and is tested here with
# the true stage schema (the primary path, per the CLAUDE.md fallback rule).
# ---------------------------------------------------------------------------


def _employees_stage_output() -> pd.DataFrame:
    """The exact schema ``braunschweig.data.census.employees`` returns."""
    return pd.DataFrame(
        {
            "commune_id": ["031010000000", "031010010000", "031530000000"],
            "weight": [1000.0, 1000.0, 500.0],
        }
    )


def test_build_destination_attraction_on_accepts_stage_schema():
    """#128 regression: the ON path must work on the raw stage output
    (``commune_id``/``weight``) without a KeyError and must tilt within-Kreis
    attraction while preserving the Kreis totals."""
    from braunschweig.gravity.model import build_destination_attraction

    out = build_destination_attraction(
        _employees_stage_output(), _betriebe(), sector_aware_enabled=True
    )

    assert list(out.columns) == ["commune_id", "employees"]
    big = out[out["commune_id"] == "031010000000"]["employees"].iloc[0]
    service = out[out["commune_id"] == "031010010000"]["employees"].iloc[0]
    assert service > 1000.0 > big  # tilt applied toward the dense Gemeinde
    assert np.isclose(big + service, 2000.0)  # Kreis total preserved


def test_build_destination_attraction_off_matches_plain_rename():
    """OFF path byte-identity: the result must equal the raw stage output with
    only the ``weight -> employees`` rename (the legacy attraction vector)."""
    from braunschweig.gravity.model import build_destination_attraction

    raw = _employees_stage_output()
    out = build_destination_attraction(raw, None, sector_aware_enabled=False)

    expected = raw.rename(columns={"weight": "employees"})[
        ["commune_id", "employees"]
    ]
    pd.testing.assert_frame_equal(out, expected)


def test_execute_applies_tilt_after_weight_rename():
    """Static guard: inside ``model.py`` the tilt must run via
    ``build_destination_attraction`` (which renames first); a direct
    ``apply_sector_aware_attraction`` call on the raw stage frame in
    ``_execute_gravity_base`` reintroduces the #128 KeyError."""
    import inspect

    from braunschweig.gravity import model

    source = inspect.getsource(model._execute_gravity_base)
    assert "build_destination_attraction" in source
    assert "apply_sector_aware_attraction" not in source
