"""Downloads the real source documents listed in data/sources.json into data/sources/.
Run: python scripts/fetch_sources.py"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
out = ROOT / "data" / "sources"
out.mkdir(parents=True, exist_ok=True)
docs = json.loads((ROOT / "data" / "sources.json").read_text())["documents"]
ok = 0
for d in docs:
    dest = out / f"{d['id']}.pdf"
    if dest.exists() and dest.stat().st_size > 10_000:
        print(f"have   {dest.name}"); ok += 1; continue
    try:
        req = urllib.request.Request(d["url"], headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=60).read()
        if not data.startswith(b"%PDF"):
            raise ValueError("not a PDF (the site may have moved it)")
        dest.write_bytes(data); ok += 1
        print(f"saved  {dest.name}  {len(data)//1024} KB")
    except Exception as exc:
        print(f"FAILED {d['id']}: {exc}", file=sys.stderr)
print(f"{ok}/{len(docs)} documents ready in {out}")
