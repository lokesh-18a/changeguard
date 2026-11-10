"""Ingestor and Segmenter.

Ingestor: PDF -> list of pages (page number kept, so every fact can cite its page).
Segmenter: pages -> overlapping text windows; a keyword router picks the windows worth
sending to an extractor, so a 79-page document costs a handful of model calls, not 79.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import logging

from pypdf import PdfReader

logging.getLogger("pypdf").setLevel(logging.ERROR)

from ..catalog import DATA

ROUTES = {
    "depreciation tables": r"depreciation|wooden parts|fibre ?glass|rubber",
    "IDV": r"fixing idv|insured.?s declared value",
    "No Claim Bonus": r"no claim bonus|consecutive years",
    "total loss": r"constructive total loss|\bctl\b",
    "zero depreciation cover": r"zero depreciation|nil depreciation|depreciation (shield|waiver|cover)",
}


@dataclass
class Segment:
    doc: str
    page: int
    text: str
    routes: list[str]


def ingest(doc_id: str, folder: Path | None = None) -> list[str]:
    path = (folder or DATA / "sources") / f"{doc_id}.pdf"
    reader = PdfReader(str(path))
    return [p.extract_text() or "" for p in reader.pages]


HEADING = re.compile(r"^\s*(?:\d+[.)]\s+)?[A-Z][A-Za-z/&\- ]{3,55}\s*$")


def segments(doc_id: str, pages: list[str], window: int = 45, overlap: int = 10) -> list[Segment]:
    """Overlapping windows per page. Each window is prefixed with the nearest heading above
    it, so a clause cut off from its title (e.g. the conditions of 'Zero Depreciation')
    still tells the extractor which cover it belongs to."""
    from collections import Counter
    # running headers/footers repeat on many pages; they are not section headings
    seen_on = Counter(l.strip() for text in pages for l in set(text.splitlines()) if l.strip())
    running = {l for l, n in seen_on.items() if n >= 3}
    out = []
    heading = ""
    for pno, text in enumerate(pages, start=1):
        lines = [l.rstrip() for l in text.splitlines() if l.strip()]
        for start in range(0, max(len(lines), 1), window - overlap):
            block = lines[start:start + window]
            if not block:
                continue
            chunk = "\n".join(block)
            routes = [name for name, pat in ROUTES.items() if re.search(pat, (heading + "\n" + chunk), re.I)]
            if routes:
                prefix = f"[Section: {heading.strip()}]\n" if heading else ""
                out.append(Segment(doc_id, pno, prefix + chunk, routes))
            last = start + window >= len(lines)
            for l in (block if last else block[: window - overlap]):
                if HEADING.match(l) and l.strip() not in running and not l.strip().lower().startswith(("page", "uin")):
                    heading = l
            if start + window >= len(lines):
                break
    return out


def manifest() -> list[dict]:
    return json.loads((DATA / "sources.json").read_text())["documents"]


def full_text(pages: list[str]) -> str:
    return "\n".join(pages)
