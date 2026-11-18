"""Run PopulationSim unchanged and save the inputs of every simultaneous integerizer call.

Engineering tool of issue #646 (controlled rounding): the saved instances are real linear
programmes of a batch, used to compare LP solvers and to replay the integerizer offline. The
wrapper only pickles the constructor arguments of ``SimulIntegerizer`` before handing them on,
so the PopulationSim result is the one an unwrapped run gives.

Usage: python capture_simul_instances.py <instance_dir> -w <batch_folder>
       (the arguments after <instance_dir> are those of the ``populationsim`` command)
"""
import pickle
import sys
import time
from pathlib import Path


def install(instance_dir: Path) -> None:
    """Wrap ``SimulIntegerizer.__init__`` so that every call is pickled into ``instance_dir``."""
    from populationsim.integerizing import simul_integerizer

    original_init = simul_integerizer.SimulIntegerizer.__init__
    counter = {"calls": 0}

    def capturing_init(self, incidence_df, sub_weights, sub_controls_df, control_spec,
                       total_hh_control_col, trace_label=""):
        counter["calls"] += 1
        path = instance_dir / f"{counter['calls']:04d}_{trace_label}.pkl"
        started = time.perf_counter()
        with open(path, "wb") as handle:
            pickle.dump({
                "incidence_df": incidence_df,
                "sub_weights": sub_weights,
                "sub_controls_df": sub_controls_df,
                "control_spec": control_spec,
                "total_hh_control_col": total_hh_control_col,
                "trace_label": trace_label,
            }, handle, protocol=4)
        print("[capture] %s rows %d zones %d controls %d (%.2f s)" % (
            path.name, len(incidence_df.index), len(sub_weights.columns),
            len(sub_controls_df.columns), time.perf_counter() - started), flush=True)
        original_init(self, incidence_df, sub_weights, sub_controls_df, control_spec,
                      total_hh_control_col, trace_label)

    simul_integerizer.SimulIntegerizer.__init__ = capturing_init


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    instance_dir = Path(sys.argv[1])
    instance_dir.mkdir(parents=True, exist_ok=False)
    install(instance_dir)
    sys.argv = ["populationsim"] + sys.argv[2:]
    from populationsim.__main__ import main as populationsim_main

    populationsim_main()


if __name__ == "__main__":
    main()
