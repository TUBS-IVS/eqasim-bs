"""Output-hash exclusions of the two Maven jar stages (issue #474).

Both stages exclude Maven's incremental-build state, which lists absolute build paths, so a
rebuild in another working directory reproduces the output hash. The real double builds are
gated (``tests/jar_reproducibility.py``); this guard runs always, because a too broad glob
is the dangerous failure: an excluded jar would let a changed jar pass as unchanged, and
early cutoff would spare consumers whose input really changed.
"""
import fnmatch

import pytest

from braunschweig.freight import extraction_tool
from matsim.runtime import eqasim


def _excluded(module, relative):
    return any(fnmatch.fnmatchcase(relative, pattern) for pattern in module._OUTPUT_HASH_EXCLUDE)


@pytest.mark.parametrize("module, module_root, jar", [
    (extraction_tool, "freight-tool", extraction_tool.JAR_RELATIVE % "2026.0-2026w12"),
    (eqasim, "eqasim-java/braunschweig", "eqasim-java/braunschweig/target/braunschweig-2.3.1.jar"),
    (eqasim, "eqasim-java/core", "eqasim-java/core/target/core-2.3.1.jar"),
])
def test_maven_status_is_excluded_but_no_build_product_is(module, module_root, jar):
    status = module_root + "/target/maven-status/maven-compiler-plugin/compile/default-compile/"
    assert _excluded(module, status + "inputFiles.lst")
    assert _excluded(module, status + "createdFiles.lst")
    assert not _excluded(module, jar)
    assert not _excluded(module, module_root + "/target/classes/org/eqasim/A.class")
    assert not _excluded(module, module_root + "/pom.xml")
