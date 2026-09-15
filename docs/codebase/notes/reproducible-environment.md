# Reproducible runtime environments

The Linux runtime is a captured, audited production-server snapshot, not a new
solver result. `environments/server-linux-64.lock` lists the 312 installed
conda artifacts as exact URLs, MD5 checksums, SHA-256 checksums, versions, and
builds. `environments/requirements-server-linux-64.txt` records the 11
effective pip installations and their exact PyPI artifact URLs and SHA-256
hashes. It is the canonical environment for WSL2 and the Linux production
server.

The captured server ran Python 3.10.10 on `linux-64`. The committed export
deliberately omits its machine paths, package file lists, and other local
metadata. Its source was a read-only production environment snapshot collected
on 2026-09-15.

## Install the Linux server snapshot

Install conda artifacts first, then reproduce the server's effective pip layer:

```bash
micromamba create --prefix /path/to/eqasim-server --file environments/server-linux-64.lock
/path/to/eqasim-server/bin/python -m pip install --no-deps -r environments/requirements-server-linux-64.txt
```

The `--no-deps` flag is required: the conda lock already provides the complete
base graph and the requirements file captures only packages that the production
server installed through pip. For an offline WSL host, prefetch or mirror the
artifact URLs in the two files. The `chainsolvers` entry is the exact VCS commit
`d8d8ae7de5bf2504d44b8422abcfe827ed7d054b`; stage that checkout or an equivalent
archive before the pip step.

The pip layer intentionally overrides some conda-installed packages. In
particular, the server has conda `pytest=7.2.2` but its effective runtime is pip
`pytest=9.1.1`, pulled in with the pinned `chainsolvers` installation. This is
documented and replayed rather than hidden. The other captured pip packages are
`py-spy=0.4.2`, `plotly=6.8.0`, `PyYAML=6.0.3`, `narwhals=2.22.0`,
`frozendict=2.4.7`, `pyzmq=27.1.0`, `bhepop2=2.0.0`, `synpp=1.6.2`, and
`Pygments=2.20.0`.

## Windows development and CI

`environment.yml` remains the editable portable source for Windows. Its
`pytest=9.1.1` pin matches the production server's effective version.
`environments/conda-lock.yml` and `environments/conda-win-64.lock` are the
Windows-only conda-lock 4.0.2 resolution; install the unified lock with:

```powershell
conda-lock install --conda (Get-Command micromamba).Source --name eqasim environments/conda-lock.yml
```

The explicit Windows file is for auditing and cache seeding. Use the unified
lock for installation so its pip section is also applied.

## Refresh policy

Do not regenerate the Linux snapshot from `environment.yml`: it represents a
known production runtime. Refresh it only from a new read-only server snapshot,
then record both the conda artifacts and the effective pip metadata again.

Regenerate the Windows lock after changing `environment.yml` with the recorded
tool version:

```bash
uvx --from conda-lock==4.0.2 conda-lock lock --micromamba --file environment.yml --platform win-64 --kind lock --lockfile environments/conda-lock.yml
uvx --from conda-lock==4.0.2 conda-lock render --platform win-64 --kind explicit --filename-template 'environments/conda-{platform}.lock' environments/conda-lock.yml
```
