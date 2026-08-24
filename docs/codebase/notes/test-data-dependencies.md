# Tests and local-only data: declare the dependency

Most inputs of this project are gitignored. A fresh clone -- and **every git
worktree** -- carries only the committed, allowlisted tables, so any test that reads
a local-only input behaves differently there than on a data-carrying machine.

## The rule

**A test whose result depends on a local-only input must declare that dependency**,
with a `skipif` naming the missing file, so its absence produces an honest SKIP:

```python
_needs_hsn_tsn_lookup = pytest.mark.skipif(
    not (DATA / "braunschweig" / "kba" / "hsn_tsn_lookup.csv").exists(),
    reason="local-only raw data absent: braunschweig/kba/hsn_tsn_lookup.csv",
)
```

Without the declaration the test lies in one of two directions:

- **False RED** -- it fails wherever the data is absent, which reads like a code
  regression. `test_fleet_sampling_de.py::test_age_income_off_unchanged` and
  `test_run_fleet_stage.py::test_default_car_rows_identifiable_and_non_default_rows_use_canonical_vocab`
  did exactly this until 2026-08-24; diagnosing them cost a full baseline
  investigation of `origin/main` before the single-file cause was isolated.
- **Vacuous GREEN** -- it passes because the code under test silently fell back, so
  the green says nothing about the method the test claims to cover. This is the
  failure class the fallback-transparency rule in `CLAUDE.md` exists for.

**A frozen golden inherits the same dependency.** A comparison artifact generated on
a data-carrying machine only reproduces there. Regenerate a golden ONLY with the
data present -- regenerating it without would freeze the fallback distribution as
the reference, i.e. pin a fallback as truth.

## Checking it

`scripts/audit_test_data_dependencies.py` is both halves of the check in one file.
Run it from the repository root **in a tree without the local-only data** (a git
worktree is exactly that):

```
python -m pytest tests/ -p scripts.audit_test_data_dependencies -q
python scripts/audit_test_data_dependencies.py test_data_probe_audit.txt
```

The plugin observes `os.path.exists`, `pathlib.Path.exists` and `open` and records,
per test, every probe of an `eqasim-data` path that is not there; the classifier
sorts the result into false reds (exit code 1), candidates to read, probes that are
absent by design, and correctly declared dependencies. It only observes -- no
behaviour changes.

Its limits are real: a probe is seen only through those three entry points, and a
recorded probe proves that a test ASKED for an absent input, never by itself that
its assertion rests on it. The candidate class is a reading list, not a verdict.

## Audit of 2026-08-24

4332 tests recorded in a worktree carrying only committed data; 58 probed an absent
input.

- **False reds: 0** (after the two fixes above). The whole suite is honest on a
  fresh clone: 4242 passed, 90 skipped, none failed.
- **Candidates: 39, all read, none vacuous with respect to its own claim.** Three
  benign patterns cover them: an *incidental probe* when `FleetSampler` is
  constructed (it consults the HSN/TSN lookup once for the optional feasibility
  mask, so tests about determinism, flag byte-identity or committed tables probe it
  without claiming anything about it); *deliberate negative and fallback tests*
  (`TestFleetSamplerFallbackNoEuroCSV`, `test_model_fuel_is_none_when_csv_absent`,
  the `missing_csv_raises_with_context` pair pointing at `_does_not_exist`); and
  *documented tolerant optional inputs* (the VG250 archive, resolved with
  `strict=False` for the dashboard panel and a logged warning, while the
  analysis/validation path raises).
- Only two local-only inputs actually surface in the suite:
  `braunschweig/kba/hsn_tsn_lookup.csv` and the VG250 archive.

**One honest residual, not fixable by a guard.** OFF-arm flag tests whose ON arm is
data-guarded (`test_hsn_tsn_attributes_off_*`) are strictly weaker where the data is
absent: their assertion ("the engine columns are NOT there") is satisfied trivially
when the ON path could not have run anyway. Guarding them too would remove coverage
that is valid everywhere, so they stay as they are -- the pair is only meaningful on
a data-carrying machine, and that is where the ON arm runs.
