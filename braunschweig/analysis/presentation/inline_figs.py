"""Inline figure PNGs (base64) and captions into the deck template.

Usage: python inline_figs.py <template.html> <mapping.json> <output.html>

mapping.json format:
{
  "figures":  { "__FIG_B1__": "C:/path/fig_b1.png", ... },
  "captions": { "__CAP_B1__": "text", ... },
  "drop_slides": ["__FIG_B5__"]   # optional: remove whole <section> blocks that
                                  # still contain this token (figure failed)
}
Slides are delimited by "<!-- ============" comment markers; a dropped slide is
removed from marker to the next marker.
"""
import base64
import json
import pathlib
import re
import sys

tpl_path, map_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
html = pathlib.Path(tpl_path).read_text(encoding="utf-8")
mapping = json.loads(pathlib.Path(map_path).read_text(encoding="utf-8"))

# 1. Drop failed figure slides (split on section comments, drop parts with token).
for token in mapping.get("drop_slides", []):
    parts = re.split(r"(?=<!-- ============)", html)
    kept = [p for p in parts if token not in p]
    if len(kept) == len(parts):
        print(f"WARN drop_slides token not found: {token}")
    html = "".join(kept)

# 2. Captions (plain text replace).
for token, text in mapping.get("captions", {}).items():
    if token not in html:
        print(f"WARN caption token not found: {token}")
    html = html.replace(token, text)

# 3. Figures (base64 data URIs; mime by extension).
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml", ".gif": "image/gif"}
total_kb = 0
for token, png in mapping.get("figures", {}).items():
    p = pathlib.Path(png)
    if not p.is_file():
        print(f"ERROR missing image for {token}: {png}")
        sys.exit(1)
    mime = MIME.get(p.suffix.lower(), "image/png")
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    total_kb += len(b64) // 1024
    if token not in html:
        print(f"WARN figure token not found: {token}")
    html = html.replace(token, f"data:{mime};base64," + b64)

leftover = re.findall(r"__(?:FIG|CAP)_\w+__", html)
if leftover:
    print(f"WARN leftover tokens: {sorted(set(leftover))}")

out = pathlib.Path(out_path)
out.write_text(html, encoding="utf-8")
print(f"wrote {out} ({len(html)//1024} KB total, {total_kb} KB base64 images)")
