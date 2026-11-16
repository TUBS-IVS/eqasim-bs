#!/usr/bin/env bash
# usage: run_diag_v2.sh <workdir> <outname> [extra args]; tool env = upstream master venv (reads numpy-1 and numpy-2 stores)
W=$1; O=$2; shift 2
cd ~/i138_ladder/code_b1e4a5a
export EQASIM_BS_COMMIT=b1e4a5a53a08cc253528224c2061b4b4361d8588
/usr/bin/time -v ~/i138_ladder/populationsim_master/.venv/bin/python -m braunschweig.analysis.popsim_batch_diagnostics.run --work-dir $W --out-dir ~/i138_ladder/$O --mid-households ~/eqasim-bs/eqasim-data/data/braunschweig/popsim/mid2023_raw/MiD2023_Haushalte.csv "$@" > ~/i138_ladder/$O.log 2>&1; echo exit $? >> ~/i138_ladder/$O.log
