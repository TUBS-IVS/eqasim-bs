import numpy as np
import pandas as pd


def test_match_donors_assigns_within_cell():
    from synthesis.population.matched import match_donors

    target = pd.DataFrame({
        "person_id": [1, 2],
        "sex": pd.Categorical(["male", "female"]),
        "age_class": [3, 3],
    })
    donor = pd.DataFrame({
        "hts_id": [10, 11],
        "sex": pd.Categorical(["male", "female"]),
        "age_class": [3, 3],
        "weight": [1.0, 1.0],
    })

    out = match_donors(
        target, donor,
        matching_columns=["sex", "age_class"],
        minimum_observations=1,
        random_seed=0,
    )

    assert set(out.columns) >= {"person_id", "hts_id"}
    assert out.set_index("person_id").loc[1, "hts_id"] == 10  # male -> male donor
    assert out.set_index("person_id").loc[2, "hts_id"] == 11  # female -> female donor
    assert len(out) == 2


def test_match_donors_is_deterministic_for_fixed_seed():
    from synthesis.population.matched import match_donors

    rng = np.random.RandomState(42)
    target = pd.DataFrame({
        "person_id": np.arange(50),
        "sex": pd.Categorical(rng.choice(["male", "female"], size=50)),
        "age_class": rng.randint(1, 4, size=50),
    })
    donor = pd.DataFrame({
        "hts_id": np.arange(100, 120),
        "sex": pd.Categorical(rng.choice(["male", "female"], size=20)),
        "age_class": rng.randint(1, 4, size=20),
        "weight": rng.uniform(0.5, 2.0, size=20),
    })

    first = match_donors(
        target, donor,
        matching_columns=["sex", "age_class"],
        minimum_observations=1,
        random_seed=7,
    )
    second = match_donors(
        target, donor,
        matching_columns=["sex", "age_class"],
        minimum_observations=1,
        random_seed=7,
    )

    pd.testing.assert_frame_equal(
        first.sort_values("person_id").reset_index(drop=True),
        second.sort_values("person_id").reset_index(drop=True),
    )
    assert len(first) == len(target)


def test_match_donors_raises_when_no_donor_cell_exists():
    import pytest
    from synthesis.population.matched import match_donors

    # The first (highest-priority) matching key is never relaxed: a target value
    # absent from the donor pool cannot be matched and must raise loudly.
    target = pd.DataFrame({
        "person_id": [1],
        "sex": pd.Categorical(["female"]),
        "age_class": [3],
    })
    donor = pd.DataFrame({
        "hts_id": [10],
        "sex": pd.Categorical(["male"]),
        "age_class": [3],
        "weight": [1.0],
    })

    with pytest.raises(RuntimeError, match="could not be matched"):
        match_donors(
            target, donor,
            matching_columns=["sex", "age_class"],
            minimum_observations=1,
            random_seed=0,
        )
