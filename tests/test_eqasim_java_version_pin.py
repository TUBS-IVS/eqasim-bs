"""The jar version comes from the java project's pom, not from a hand-kept constant.

`matsim.runtime.eqasim` builds the braunschweig module from source
(`eqasim_source_path`) and then looks for `braunschweig-<version>.jar` **by name**,
raising if it is absent. So the version string is not documentation: it is the
filename the build has to produce, and `braunschweig/pom.xml` is what decides it.

Keeping a copy of that number in python made the pipeline break every time the
fork cut a release. It has happened twice: `release-please` in
TUBS-IVS/eqasim-java-bs lists `braunschweig/pom.xml` among its `extra-files`, so
v2.3.0 and then v2.3.1 (2026-08-21, a pure version bump carrying no code at all)
each renamed our jar. The workflow runs on every merge to main, so the next
`fix:` PR would rename it again. Note what a release does NOT do here: the
pipeline builds from source and `validate()` keys its cache on the newest source
mtime, so a plain `git pull` already delivers a java fix and triggers the rebuild.

The version is therefore READ from the pom of the tree that is actually built.
`DEFAULT_EQASIM_VERSION` survives only as the fallback for the two paths that
have no such pom (a prebuilt jar, and the legacy upstream clone), and an explicit
`eqasim_version` config still wins over both. Issue #347.
"""
import os

import pytest

import matsim.runtime.eqasim as eqasim

POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
\t<parent>
\t\t<groupId>org.eqasim</groupId>
\t\t<artifactId>eqasim</artifactId>
\t\t<version>%s</version> <!-- x-release-please-version -->
\t\t<relativePath>../pom.xml</relativePath>
\t</parent>
\t<artifactId>braunschweig</artifactId>
\t<dependencies>
\t\t<dependency>
\t\t\t<groupId>org.eqasim</groupId>
\t\t\t<artifactId>core</artifactId>
\t\t\t<version>%s</version>
\t\t</dependency>
\t</dependencies>
</project>
"""


def _write_project(root, version):
    module = root / "braunschweig"
    module.mkdir(parents=True, exist_ok=True)
    (module / "pom.xml").write_text(POM % (version, version), encoding="utf-8")
    return str(root)


# --- reading the pom --------------------------------------------------------

def test_the_module_version_is_read_from_the_parent_block(tmp_path):
    """The <parent> version is the one that names the jar; the dependency block
    carries the same number and must not be picked up by accident."""
    root = _write_project(tmp_path, "2.3.1")
    pom = os.path.join(root, "braunschweig", "pom.xml")
    assert eqasim.module_version_from_pom(pom) == "2.3.1"


def test_a_pom_without_a_parent_version_fails_loudly(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text("<project><parent><groupId>x</groupId></parent></project>",
                   encoding="utf-8")
    with pytest.raises(RuntimeError) as excinfo:
        eqasim.module_version_from_pom(str(pom))
    assert "version" in str(excinfo.value).lower()


# --- which version wins ----------------------------------------------------

def test_the_pom_wins_over_the_python_constant(tmp_path):
    """The exact regression: constant says 2.3.0, the tree builds 2.3.1."""
    root = _write_project(tmp_path, "2.3.1")
    messages = []
    version = eqasim.resolve_source_version(root, None, log=messages.append)

    assert version == "2.3.1"
    assert any("2.3.1" in m for m in messages), messages


def test_an_explicit_config_value_still_wins(tmp_path):
    """The escape hatch stays: someone pinning eqasim_version means it."""
    root = _write_project(tmp_path, "2.3.1")
    messages = []
    version = eqasim.resolve_source_version(root, "9.9.9", log=messages.append)

    assert version == "9.9.9"
    assert any("9.9.9" in m for m in messages), messages


def test_a_missing_pom_falls_back_to_the_constant_and_says_so(tmp_path):
    """No silent fallbacks: an unreadable pom must be visible in the log."""
    messages = []
    version = eqasim.resolve_source_version(str(tmp_path), None, log=messages.append)

    assert version == eqasim.DEFAULT_EQASIM_VERSION
    assert any("WARNING" in m for m in messages), messages


def test_the_constant_documents_the_current_fork_release():
    """It is only a fallback now, but a stale fallback is still misleading."""
    assert eqasim.DEFAULT_EQASIM_VERSION == "2.3.1"


# --- the real checkout, when it is there ------------------------------------

def test_the_checked_out_fork_resolves_to_a_concrete_version():
    """End-to-end against the actual sibling project, skipped when absent."""
    import yaml
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((repo_root / "configs" / "base_bs.yml").read_text(encoding="utf-8"))
    raw = (config.get("config") or {}).get("eqasim_source_path")
    assert raw, "configs/base_bs.yml no longer sets eqasim_source_path"

    source = (repo_root / raw).resolve()
    if not (source / "braunschweig" / "pom.xml").exists():
        pytest.skip(f"java fork not checked out at {source}")

    version = eqasim.resolve_source_version(str(source), None, log=lambda m: None)
    assert version.count(".") == 2, version
