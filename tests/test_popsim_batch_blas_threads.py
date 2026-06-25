"""PopulationSim batch subprocesses must pin BLAS / OpenMP to a single thread.

Each PopulationSim batch runs as its own process and uses numpy with OpenBLAS,
which by default spawns one thread per CPU core. Running many batches in parallel
(num_workers up to cpu_count) therefore oversubscribes the machine by orders of
magnitude (e.g. 16 batches x 64 OpenBLAS threads = 1024 threads on 64 cores). That
oversubscription is not just slow: on the 64-core run server it crashed numpy with
segfaults in libc (12 batches lost in one 25% run, verified via dmesg). Pinning the
BLAS/OpenMP thread count to 1 per subprocess removes the oversubscription so the
batch-level parallelism is governed solely by num_workers.
"""
from braunschweig.popsim import batch


class _Result:
    returncode = 0
    stdout = ""
    stderr = ""


def test_run_one_pins_blas_and_openmp_threads_to_one(tmp_path):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _Result()

    run_one = batch.make_populationsim_run_one(
        subprocess_run=fake_run, cwd=str(tmp_path)
    )
    # batch_x has no completion marker -> the (fake) subprocess is spawned.
    run_one(str(tmp_path / "batch_x"))

    env = captured["env"]
    assert env["OPENBLAS_NUM_THREADS"] == "1"
    assert env["OMP_NUM_THREADS"] == "1"
    assert env["MKL_NUM_THREADS"] == "1"
    assert env["NUMEXPR_NUM_THREADS"] == "1"


def test_run_one_env_inherits_the_rest_of_the_environment(tmp_path):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _Result()

    run_one = batch.make_populationsim_run_one(
        subprocess_run=fake_run, cwd=str(tmp_path)
    )
    run_one(str(tmp_path / "batch_x"))
    # The thread caps are added on top of the inherited environment, not instead of
    # it (uv / PopulationSim still need PATH, HOME, ... to run).
    assert "PATH" in captured["env"]
