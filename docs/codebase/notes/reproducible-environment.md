# Reproducible Linux environment

`environment.yml` is the editable dependency specification for the `eqasim`
conda environment. `environments/conda-lock.yml` is its committed, unified
`linux-64` and `win-64` lock: every conda artifact has a checksum and every pip
artifact has an exact URL and hash where the upstream format provides one. Its
Linux resolution is the canonical runtime definition for WSL2 and the Linux
production server; the Windows resolution keeps CI and local development on an
equally exact dependency set.

`environments/conda-linux-64.lock` and `environments/conda-win-64.lock` are
rendered explicit views of the same lock. They are useful for auditing or
seeding a package cache. Install through `conda-lock install`, rather than
`conda create --file`, because the unified installer also applies the pip
section.

The pip layer must not replace a direct scientific conda package. When updating
the lock, inspect the `manager: pip` records in `conda-lock.yml`: direct pins
such as `numpy`, `pandas`, `scipy`, `geopandas`, `scikit-learn`, and `pyarrow`
must remain conda-managed. If a VCS or PyPI dependency makes that impossible,
make the conflict explicit in `environment.yml` and resolve it before accepting
the new lock.

## Install

Install the unified lock with a Linux conda-compatible executable (for example
micromamba):

```bash
conda-lock install --conda "$(command -v micromamba)" --name eqasim environments/conda-lock.yml
```

This install includes `synpp`, `bhepop2`, and the pinned `chainsolvers` VCS
source. The lock records the latter as
`git+https://github.com/TUBS-IVS/chainsolvers.git@d8d8ae7de5bf2504d44b8422abcfe827ed7d054b`.
On an offline WSL host, prefetch or mirror the URLs recorded in the unified lock
(including that VCS commit) before running the install command.

## Update and verify

Regenerate both platform lock views only after changing `environment.yml`,
using the recorded lock tool version:

```bash
uvx --from conda-lock==4.0.2 conda-lock lock --micromamba --file environment.yml --platform linux-64 --platform win-64 --kind lock --lockfile environments/conda-lock.yml
uvx --from conda-lock==4.0.2 conda-lock render --platform linux-64 --platform win-64 --kind explicit --filename-template 'environments/conda-{platform}.lock' environments/conda-lock.yml
uvx --from conda-lock==4.0.2 conda-lock lock --file environment.yml --platform linux-64 --platform win-64 --lockfile environments/conda-lock.yml --check-input-hash
```

With conda-lock 4.0.2, the final command reports that the spec hash is already
locked and exits successfully when the lock still matches the source
specification.

## Pytest compatibility pin

The pinned `chainsolvers` commit declares `pytest>=8.4.2`. Its previous
`pytest=7.2.2` source constraint was therefore incompatible with a complete
pip installation. `environment.yml` pins the lowest compatible version,
`pytest=8.4.2`, and repeats it in the pip section: conda-lock resolves conda
and pip requirement graphs independently, so the repeated pip constraint
prevents the VCS dependency from silently selecting a newer pytest release.
