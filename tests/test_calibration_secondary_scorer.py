from braunschweig.calibration.secondary import build_secondary_loss, coordinate_descent


def test_descent_finds_min_of_toy_objective():
    # min at pot=2.0, dist_dev=1.0
    def obj(w):
        return (w["pot_weight"] - 2.0) ** 2 + (w["dist_dev_weight"] - 1.0) ** 2
    out = coordinate_descent(
        obj, init={"pot_weight": 0.0, "dist_dev_weight": 0.0},
        grid={"pot_weight": [0.0, 1.0, 2.0, 3.0], "dist_dev_weight": [0.0, 1.0, 2.0]})
    assert abs(out["weights"]["pot_weight"] - 2.0) < 1e-9
    assert abs(out["weights"]["dist_dev_weight"] - 1.0) < 1e-9
    assert out["loss"] < 1e-9


def test_loss_sums_per_purpose_emd_and_prefers_lower():
    targets = {"shop": [0.5, 0.5], "leisure": [0.5, 0.5], "other": [0.5, 0.5]}
    # realised fn: a perfect match for weights A, a skewed one for weights B
    def realised(weights):
        if weights["pot_weight"] == 1.0:
            return {"shop": [0.5, 0.5], "leisure": [0.5, 0.5], "other": [0.5, 0.5]}
        return {"shop": [0.9, 0.1], "leisure": [0.9, 0.1], "other": [0.9, 0.1]}
    loss = build_secondary_loss(realised, targets)
    assert loss({"pot_weight": 1.0, "dist_dev_weight": 1.0, "attr_transform": "log1p"}) == 0.0
    assert loss({"pot_weight": 5.0, "dist_dev_weight": 1.0, "attr_transform": "log1p"}) > 0.0


def test_loss_includes_concentration_penalty():
    targets = {"shop": [0.5, 0.5], "leisure": [0.5, 0.5], "other": [0.5, 0.5]}

    def realised(weights):
        return {"shop": [0.5, 0.5], "leisure": [0.5, 0.5], "other": [0.5, 0.5]}

    def concentration(weights):
        return weights.get("pot_weight", 1.0)

    loss_no_conc = build_secondary_loss(realised, targets)
    loss_with_conc = build_secondary_loss(realised, targets,
                                          concentration_fn=concentration, conc_weight=1.0)
    # EMD is 0 for perfect match; concentration penalty = pot_weight
    w = {"pot_weight": 2.0, "dist_dev_weight": 1.0, "attr_transform": "linear"}
    assert loss_no_conc(w) == 0.0
    assert abs(loss_with_conc(w) - 2.0) < 1e-9


def test_loss_ignores_missing_purpose():
    """If realised omits a purpose, that purpose contributes 0 to the total."""
    targets = {"shop": [0.5, 0.5], "leisure": [0.5, 0.5]}

    def realised(weights):
        return {"shop": [0.9, 0.1]}  # no leisure key

    loss = build_secondary_loss(realised, targets)
    w = {"pot_weight": 1.0, "dist_dev_weight": 1.0, "attr_transform": "linear"}
    # only shop EMD counts; leisure missing -> 0
    result = loss(w)
    assert result > 0.0   # shop EMD > 0
    # if leisure had been included, loss would be higher
    def realised_full(weights):
        return {"shop": [0.9, 0.1], "leisure": [0.9, 0.1]}
    loss_full = build_secondary_loss(realised_full, targets)
    assert loss_full(w) > result


def test_descent_records_history():
    def obj(w):
        return w["pot_weight"] ** 2 + w["dist_dev_weight"] ** 2
    out = coordinate_descent(
        obj, init={"pot_weight": 3.0, "dist_dev_weight": 3.0},
        grid={"pot_weight": [0.0, 1.0, 3.0], "dist_dev_weight": [0.0, 1.0, 3.0]})
    assert out["weights"]["pot_weight"] == 0.0
    assert out["weights"]["dist_dev_weight"] == 0.0
    assert out["loss"] < 1e-9
    assert len(out["history"]) >= 2                       # init + >=1 improvement
    assert out["history"][-1]["loss"] == out["loss"]      # history tail consistent with return
