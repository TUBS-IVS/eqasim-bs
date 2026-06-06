import numpy as np
from braunschweig.synthesis.locations.education_gravity_model import (
    balance_doubly_constrained, assign_by_capacity_gravity, assign_by_radius,
    assign_by_decay, FALLBACK_WARN_SHARE,
)


def test_furness_hits_both_margins():
    friction = np.array([[1.0, 0.5],
                         [0.5, 1.0],
                         [1.0, 1.0]])
    production = np.array([1.0, 1.0, 1.0])
    attraction = np.array([2.0, 1.0])
    T = balance_doubly_constrained(production, attraction, friction,
                                   max_iterations=200, tolerance=1e-9)
    assert np.allclose(T.sum(axis=1), production, atol=1e-6)
    assert np.allclose(T.sum(axis=0), attraction, atol=1e-6)


def test_capacity_gravity_fills_big_school_not_tiny_one():
    # two schools at the SAME location; capacities 1000 vs 10 -> ~99% to big.
    rng = np.random.RandomState(0)
    pupils = np.zeros((500, 2))
    schools = np.array([[0.0, 0.0], [0.0, 0.0]])
    capacity = np.array([1000.0, 10.0])
    choice, fallback = assign_by_capacity_gravity(
        pupils, schools, capacity, slope=-0.2, max_radius_km=50.0,
        max_iterations=200, tolerance=1e-9, rng=rng)
    assert np.mean(choice == 0) > 0.95
    assert fallback.sum() == 0


def test_capacity_constraint_keeps_tiny_close_school_from_overfilling():
    # Core feature: a tiny-capacity school sits ON TOP of 1000 pupils; a big
    # school is 10 km away. Proximity alone would dump everyone into the tiny
    # school; the doubly-constrained balancing must hold its intake near its
    # scaled capacity target (50), NOT ~1000. The "no 2-vs-10000" guarantee.
    rng = np.random.RandomState(7)
    pupils = np.zeros((1000, 2))
    schools = np.array([[0.0, 0.0], [10_000.0, 0.0]])
    capacity = np.array([50.0, 950.0])
    choice, _ = assign_by_capacity_gravity(
        pupils, schools, capacity, slope=-0.2, max_radius_km=60.0,
        max_iterations=500, tolerance=1e-9, rng=rng)
    n_tiny = int(np.sum(choice == 0))
    assert 20 <= n_tiny <= 110


def test_capacity_gravity_prefers_closer_when_capacity_allows():
    # Distance preference shows up with HETEROGENEOUS pupil locations: half the
    # pupils sit at school A, half at school B; equal capacities matching the
    # co-located counts. The model must route (almost) everyone to their nearest
    # (co-located) school -- distance preference within capacity feasibility.
    rng = np.random.RandomState(1)
    near_a = np.zeros((200, 2))
    near_b = np.tile(np.array([30_000.0, 0.0]), (200, 1))
    pupils = np.vstack([near_a, near_b])
    schools = np.array([[0.0, 0.0], [30_000.0, 0.0]])
    capacity = np.array([200.0, 200.0])
    choice, _ = assign_by_capacity_gravity(
        pupils, schools, capacity, slope=-0.2, max_radius_km=60.0,
        max_iterations=500, tolerance=1e-9, rng=rng)
    assert np.mean(choice[:200] == 0) > 0.9
    assert np.mean(choice[200:] == 1) > 0.9


def test_capacity_gravity_radius_fallback_to_nearest():
    rng = np.random.RandomState(2)
    pupils = np.zeros((10, 2))
    schools = np.array([[40_000.0, 0.0]])
    capacity = np.array([100.0])
    choice, fallback = assign_by_capacity_gravity(
        pupils, schools, capacity, slope=-0.2, max_radius_km=10.0,
        max_iterations=50, tolerance=1e-6, rng=rng)
    assert (choice == 0).all()
    assert fallback.all()


def test_assign_by_radius_weighted_and_fallback():
    rng = np.random.RandomState(3)
    pupils = np.zeros((200, 2))
    schools = np.array([[0.0, 0.0], [0.0, 0.0]])
    weight = np.array([9.0, 1.0])
    choice = assign_by_radius(pupils, schools, weight, radius_m=2000.0, rng=rng)
    assert np.mean(choice == 0) > 0.8
    assert set(np.unique(choice)) <= {0, 1}


def test_capacity_gravity_accepts_per_pupil_slope_vector():
    # Two groups of pupils at the same point; school A near (0), B far (8 km),
    # equal capacity. Group 0 has a steep slope (strong distance aversion) ->
    # prefers near A; group 1 has a near-flat slope -> more indifferent. The
    # steep group must pick A more often than the flat group.
    rng = np.random.RandomState(0)
    n = 300
    pupils = np.zeros((2 * n, 2))
    schools = np.array([[0.0, 0.0], [8000.0, 0.0]])
    capacity = np.array([1000.0, 1000.0])
    slope = np.concatenate([np.full(n, -0.8), np.full(n, -0.02)])
    choice, _ = assign_by_capacity_gravity(
        pupils, schools, capacity, slope=slope, max_radius_km=60.0,
        max_iterations=300, tolerance=1e-9, rng=rng)
    steep_near = np.mean(choice[:n] == 0)
    flat_near = np.mean(choice[n:] == 0)
    assert steep_near > flat_near


def test_capacity_gravity_scalar_slope_still_works():
    rng = np.random.RandomState(1)
    pupils = np.zeros((50, 2))
    schools = np.array([[0.0, 0.0], [5000.0, 0.0]])
    capacity = np.array([500.0, 500.0])
    choice, _ = assign_by_capacity_gravity(
        pupils, schools, capacity, slope=-0.2, max_radius_km=60.0,
        max_iterations=200, tolerance=1e-9, rng=rng)
    assert set(np.unique(choice)) <= {0, 1}


def test_assign_by_decay_weights_by_attraction_and_distance():
    # Two universities: A near (0 km, small), B far (40 km, large). With a
    # moderate slope most pupils pick the near one despite B's larger weight,
    # but a non-trivial tail reaches B (singly-constrained: no capacity forcing).
    rng = np.random.RandomState(0)
    pupils = np.zeros((500, 2))
    unis = np.array([[0.0, 0.0], [40_000.0, 0.0]])
    weight = np.array([5000.0, 25000.0])
    choice = assign_by_decay(pupils, unis, weight, slope=-0.08,
                             max_radius_km=150.0, rng=rng)
    near = np.mean(choice == 0)
    assert 0.5 < near < 1.0           # near preferred, but some reach the far big one
    assert set(np.unique(choice)) <= {0, 1}


def test_assign_by_decay_nearest_fallback_outside_radius():
    rng = np.random.RandomState(1)
    pupils = np.zeros((5, 2))
    unis = np.array([[200_000.0, 0.0]])     # 200 km away, radius 150 km
    weight = np.array([10000.0])
    choice = assign_by_decay(pupils, unis, weight, slope=-0.08,
                             max_radius_km=150.0, rng=rng)
    assert (choice == 0).all()              # nearest fallback when none in radius


# ---------------------------------------------------------------------------
# Fallback observability (CLAUDE.md "Fallback transparency"): every fallback
# must be observable as a PRIMARY-vs-FALLBACK rate and the PRIMARY path must be
# provably taken on representative input.
# ---------------------------------------------------------------------------

def test_capacity_gravity_primary_path_taken_no_fallback(capsys):
    # Representative case: every pupil HAS at least one school within the radius
    # -> the doubly-constrained PRIMARY path is fully taken, the nearest-school
    # fallback count is exactly 0, and the log reports 100% primary.
    rng = np.random.RandomState(0)
    pupils = np.zeros((1000, 2))
    schools = np.array([[1000.0, 0.0], [2000.0, 0.0]])   # both ~1-2 km away
    capacity = np.array([500.0, 500.0])
    choice, fallback = assign_by_capacity_gravity(
        pupils, schools, capacity, slope=-0.2, max_radius_km=15.0,
        max_iterations=200, tolerance=1e-9, rng=rng, label="education:grundschule")
    assert fallback.sum() == 0                     # PRIMARY path fully taken
    assert set(np.unique(choice)) <= {0, 1}
    out = capsys.readouterr().out
    assert "[education:grundschule]" in out
    assert "primary 1000/1000 (100.0%)" in out
    assert "nearest-school fallback 0 (0.0%)" in out
    assert "WARNING" not in out                    # no warning when fallback=0


def test_capacity_gravity_fallback_counted_and_warned(capsys):
    # Force the fallback: all 10 pupils sit 40 km from the only school, radius
    # 10 km -> every pupil takes the nearest-school fallback. The fallback must
    # be COUNTED (10/10) and the line WARNING-prefixed (share > threshold).
    rng = np.random.RandomState(2)
    pupils = np.zeros((10, 2))
    schools = np.array([[40_000.0, 0.0]])
    capacity = np.array([100.0])
    choice, fallback = assign_by_capacity_gravity(
        pupils, schools, capacity, slope=-0.2, max_radius_km=10.0,
        max_iterations=50, tolerance=1e-6, rng=rng, label="education:grundschule")
    assert fallback.all()
    assert int(fallback.sum()) == 10
    out = capsys.readouterr().out
    assert "primary 0/10 (0.0%)" in out
    assert "nearest-school fallback 10 (100.0%)" in out
    assert out.lstrip().startswith("WARNING: ") or "WARNING: [education:grundschule]" in out


def test_assign_by_decay_primary_path_taken_no_fallback(capsys):
    # Representative case: every student has a campus within the radius -> the
    # in-radius weighted-decay PRIMARY path is fully taken, nearest-campus
    # fallback count is 0.
    rng = np.random.RandomState(0)
    pupils = np.zeros((500, 2))
    unis = np.array([[5_000.0, 0.0], [40_000.0, 0.0]])   # both within 150 km
    weight = np.array([5000.0, 25000.0])
    choice = assign_by_decay(pupils, unis, weight, slope=-0.08,
                             max_radius_km=150.0, rng=rng,
                             label="education:university")
    assert set(np.unique(choice)) <= {0, 1}
    out = capsys.readouterr().out
    assert "[education:university]" in out
    assert "primary 500/500 (100.0%)" in out
    assert "nearest-campus fallback 0 (0.0%)" in out
    assert "WARNING" not in out


def test_assign_by_decay_fallback_counted_and_warned(capsys):
    # Force the fallback: the only campus is 200 km away, radius 150 km -> every
    # student takes the nearest-campus fallback, counted and WARNING-prefixed.
    rng = np.random.RandomState(1)
    pupils = np.zeros((5, 2))
    unis = np.array([[200_000.0, 0.0]])
    weight = np.array([10000.0])
    choice = assign_by_decay(pupils, unis, weight, slope=-0.08,
                             max_radius_km=150.0, rng=rng,
                             label="education:university")
    assert (choice == 0).all()
    out = capsys.readouterr().out
    assert "primary 0/5 (0.0%)" in out
    assert "nearest-campus fallback 5 (100.0%)" in out
    assert "WARNING: " in out


def test_assign_by_radius_fallback_observability(capsys):
    # PRIMARY: pupils with a facility in radius draw it. FALLBACK: pupils with
    # none in radius take the nearest. Mixed input -> both counts must appear.
    rng = np.random.RandomState(3)
    # 4 pupils at origin (school in radius), 1 far away (forces nearest fallback)
    pupils = np.array([[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0],
                       [50_000.0, 0.0]])
    schools = np.array([[0.0, 0.0]])
    weight = np.array([1.0])
    assign_by_radius(pupils, schools, weight, radius_m=2000.0, rng=rng,
                     label="education:kindergarten")
    out = capsys.readouterr().out
    assert "[education:kindergarten]" in out
    assert "primary 4/5 (80.0%)" in out
    assert "nearest-facility fallback 1 (20.0%)" in out


def test_assign_by_radius_primary_path_no_fallback(capsys):
    # Every pupil has a facility in radius -> nearest-facility fallback is 0.
    rng = np.random.RandomState(3)
    pupils = np.zeros((20, 2))
    schools = np.array([[100.0, 0.0]])
    weight = np.array([1.0])
    assign_by_radius(pupils, schools, weight, radius_m=2000.0, rng=rng,
                     label="education:kindergarten")
    out = capsys.readouterr().out
    assert "primary 20/20 (100.0%)" in out
    assert "nearest-facility fallback 0 (0.0%)" in out
    assert "WARNING" not in out
