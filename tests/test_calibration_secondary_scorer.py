from braunschweig.calibration.secondary import coordinate_descent


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


def test_descent_records_history():
    def obj(w):
        return w["pot_weight"] ** 2 + w["dist_dev_weight"] ** 2
    out = coordinate_descent(
        obj, init={"pot_weight": 3.0, "dist_dev_weight": 3.0},
        grid={"pot_weight": [0.0, 1.0, 3.0], "dist_dev_weight": [0.0, 1.0, 3.0]})
    assert len(out["history"]) >= 1
    assert out["loss"] <= obj({"pot_weight": 3.0, "dist_dev_weight": 3.0})
