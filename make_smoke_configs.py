"""Generate smoke configs that run only synthesis.output (population deliverable).

Local-only helper for the three-case smoke validation; not part of the pipeline.
"""
import re
import pathlib

BASES = {
    "config_smoke_simple_ipf.yml": "configs/fixtures/config_local_braunschweig.yml",
    "config_smoke_popsim_mid.yml": "configs/fixtures/config_popsim_mid_braunschweig.yml",
    "config_smoke_popsim_open.yml": "configs/fixtures/config_popsim_open_braunschweig.yml",
}
BLOCK_RE = re.compile(r"^run:\n(?:[ \t]*-[^\n]*\n)+", re.MULTILINE)
WORKDIRS = {
    "working_directory: eqasim-data/cache_bs\n": "working_directory: eqasim-data/cache_smoke_ipf\n",
    "working_directory: eqasim-data/cache_bs_popsim_mid\n": "working_directory: eqasim-data/cache_smoke_popsim_mid\n",
    "working_directory: eqasim-data/cache_bs_popsim_open\n": "working_directory: eqasim-data/cache_smoke_popsim_open\n",
}

for out, base in BASES.items():
    text = pathlib.Path(base).read_text(encoding="utf-8")
    new = BLOCK_RE.sub("run:\n  - synthesis.output\n", text, count=1)
    assert "synthesis.output" in new
    for old, repl in WORKDIRS.items():
        new = new.replace(old, repl)
    pathlib.Path(out).write_text(new, encoding="utf-8")
    wd = re.search(r"working_directory:.*", new).group(0)
    print(f"{out}: {wd}")
