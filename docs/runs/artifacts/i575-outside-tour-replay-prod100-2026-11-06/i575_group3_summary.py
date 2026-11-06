import pandas as pd

f = pd.read_csv("/home/felix/scratch_i575/group3_cause/group3_frozen_cause.csv.gz")
fr = f[f["frozen"].eq(True)].copy()


def cause(s):
    parts = [p for p in str(s).split(";") if p]
    exits = {p.split(">")[0] for p in parts if p.endswith(">outside")}
    if not parts:
        return "no_outside_in_tour"
    if exits == {"pt"}:
        return "pt_trip_cut"
    if "pt" in exits:
        return "pt_and_" + "_".join(sorted(exits - {"pt"})) + "_cut"
    return "_".join(sorted(exits)) + "_cut"


fr["cause"] = fr["tour_outside"].map(cause)
print("frozen legs by cause:", fr["cause"].value_counts().to_dict())
print("frozen legs, uncut plan has outside:", fr["uncut_outside_in_plan"].value_counts().to_dict())
print("frozen legs, prepared mode:", fr["prepared_mode"].value_counts().to_dict())
print("not frozen, prepared mode:", f[f["frozen"].eq(False)]["prepared_mode"].value_counts().to_dict())
print("distance km frozen p50/p90:", fr["distance_km"].quantile([0.5, 0.9]).round(1).tolist())
print(fr.sample(5, random_state=1)[["identifier", "distance_km", "prepared_mode", "tour_outside"]].to_string())
