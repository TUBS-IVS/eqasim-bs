set -eu
W=$HOME/c5c_check_20261104
RAW=/home/felix/eqasim-bs/eqasim-data/data/braunschweig/popsim/mid2023_raw
rm -rf "$W"
mkdir -p "$W/eqasim-bs"
tar -xzf "$HOME/c5c_check_20261104.tar.gz" -C "$W/eqasim-bs"
mkdir -p "$W/eqasim-bs/eqasim-data/data/braunschweig/popsim"
ln -s "$RAW" "$W/eqasim-bs/eqasim-data/data/braunschweig/popsim/mid2023_raw"
cd "$W/eqasim-bs"
echo "code under test: archive of the C5c branch head"
export PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export EQASIM_MID_SMOKE_DIR="$RAW"
nice -n 10 "$HOME/miniforge3/envs/eqasim/bin/python" scripts/run_tests.py \
  tests/test_mid_donor.py tests/test_incommuter_donor_consistency.py tests/test_popsim_mid.py \
  -q -rs -o log_cli=true -o log_cli_level=INFO \
  -k "real_mid or execute_on_real or nonbinary or invalid_household" > "$W/run.out" 2>&1 || echo "runner exit code: $?"
grep -E "mid_donor\] sex|load_completed_donor|passed|failed|SKIPPED|ERROR|Error|evidence" "$W/run.out" | cut -c1-420
