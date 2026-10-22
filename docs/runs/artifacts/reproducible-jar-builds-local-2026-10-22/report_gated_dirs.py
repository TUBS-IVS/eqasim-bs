"""Issue #474: print the output hashes of the two builds a gated reproducibility test left behind.

Usage: python report_gated_dirs.py <test directory> <stage module>
The test directory is pytest's tmp_path of test_execute_builds_a_byte_reproducible_jar; it holds
the working directories of tests/jar_reproducibility.py. Prints, per working directory, the
fork's output hash with the stage's _OUTPUT_HASH_EXCLUDE and without any exclusion, and the
files whose size or sha256 differ between the two builds.
"""
import hashlib
import importlib
import os
import sys

sys.path.insert(0, os.getcwd())
from braunschweig.synpp_fork.output_hash import hash_stage_output  # noqa: E402
from tests import jar_reproducibility  # noqa: E402

test_directory, module_name = sys.argv[1], sys.argv[2]
module = importlib.import_module(module_name)
exclude = module._OUTPUT_HASH_EXCLUDE
print("module", module_name, "exclude", exclude)
listings = []
for name in jar_reproducibility.WORKING_DIRECTORIES:
    working_directory = os.path.join(test_directory, name)
    with_exclude = hash_stage_output(working_directory, jar_reproducibility.STAGE_HASH, exclude)
    without = hash_stage_output(working_directory, jar_reproducibility.STAGE_HASH, ())
    print("%-40s with exclude: digest %s, %d files, %d bytes | without: digest %s, %d files, %d bytes" % (
        name, with_exclude["digest"][:16], with_exclude["n_files"], with_exclude["bytes"],
        without["digest"][:16], without["n_files"], without["bytes"]))
    cache = os.path.join(working_directory, jar_reproducibility.STAGE_HASH + ".cache")
    files = {}
    for root, _, names in os.walk(cache):
        for file_name in names:
            path = os.path.join(root, file_name)
            with open(path, "rb") as handle:
                files[os.path.relpath(path, cache).replace(os.sep, "/")] = (
                    os.path.getsize(path), hashlib.sha256(handle.read()).hexdigest()[:12])
    listings.append(files)
first, second = listings
for relative in sorted(set(first) | set(second)):
    if first.get(relative) != second.get(relative):
        print("differs:", relative, first.get(relative), second.get(relative))
