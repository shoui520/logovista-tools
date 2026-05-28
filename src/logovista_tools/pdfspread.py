"""PDFSpread sidecar support for observed Mac OS X SSED packages.

The HKRKIKHY Mac renderer does not turn HONMON into decorated HTML.  Its
renderer plugin converts the HONMON page anchor to a PDFSpread SQLite lookup,
selects IDRight/IDLeft, writes the BLOB to a cache path, and returns that PDF
path to the viewer.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .ssed import is_metadata_noise_path


FULLWIDTH_DIGIT_TRANS = str.maketrans("０１２３４５６７８９", "0123456789")
HALFWIDTH_DIGIT_TRANS = str.maketrans("0123456789", "０１２３４５６７８９")
PAGE_ID_RE = re.compile(r"([0-9０-９]{8})")
PDFSPREAD_SPECIAL_RIGHT_PAGES = frozenset({17, 69, 177, 241, 295, 305, 369})


@dataclass(frozen=True)
class PdfSpreadLookup:
    """One resolved PDFSpread row."""

    page_id: str
    page_number: int
    side: str
    id_right: str
    id_left: str | None
    pdf: bytes


def normalize_pdfspread_page_id(value: str) -> str | None:
    """Return an eight-fullwidth-digit page id, or None if invalid."""

    stripped = value.strip()
    match = PAGE_ID_RE.search(stripped)
    if match is None:
        return None
    return match.group(1).translate(HALFWIDTH_DIGIT_TRANS)


def pdfspread_page_number(page_id: str) -> int | None:
    normalized = normalize_pdfspread_page_id(page_id)
    if normalized is None:
        return None
    try:
        return int(normalized.translate(FULLWIDTH_DIGIT_TRANS))
    except ValueError:
        return None


def pdfspread_lookup_side(page_id: str) -> str | None:
    """Return the PDFSpread lookup side used by the HKRKIKHY renderer plugin."""

    page_number = pdfspread_page_number(page_id)
    if page_number is None:
        return None
    if page_number in PDFSPREAD_SPECIAL_RIGHT_PAGES:
        return "Right"
    return "Right" if page_number % 2 == 0 else "Left"


def extract_pdfspread_page_id(text: str) -> str | None:
    """Extract the last page-like id from decoded HONMON text."""

    matches = PAGE_ID_RE.findall(text)
    if not matches:
        return None
    return normalize_pdfspread_page_id(matches[-1])


def sqlite_has_pdfspread(path: Path) -> bool:
    """Return true when *path* is a plain SQLite PDFSpread database."""

    if is_metadata_noise_path(path):
        return False
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.DatabaseError:
        return False
    try:
        row = con.execute(
            "select 1 from sqlite_master where type='table' and lower(name)='pdfspread' limit 1"
        ).fetchone()
        if row is None:
            return False
        columns = {
            str(info[1]).lower()
            for info in con.execute('pragma table_info("PDFSpread")')
        }
        return {"idright", "pdf"} <= columns
    except sqlite3.DatabaseError:
        return False
    finally:
        con.close()


def find_pdfspread_database(root: Path) -> Path | None:
    """Find the first package-local PDFSpread SQLite database."""

    package = root.parent if root.is_file() else root
    if not package.is_dir():
        return None
    candidates = sorted(
        path
        for path in package.iterdir()
        if path.is_file()
        and not is_metadata_noise_path(path)
        and path.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}
    )
    for candidate in candidates:
        if sqlite_has_pdfspread(candidate):
            return candidate.resolve()
    return None


def lookup_pdfspread(path: Path, page_id: str) -> PdfSpreadLookup | None:
    """Resolve one page id to its PDFSpread row and BLOB."""

    normalized = normalize_pdfspread_page_id(page_id)
    side = pdfspread_lookup_side(page_id)
    page_number = pdfspread_page_number(page_id)
    if normalized is None or side is None or page_number is None:
        return None
    id_column = "IDRight" if side == "Right" else "IDLeft"
    query = f'SELECT IDRight, IDLeft, PDF FROM PDFSpread WHERE "{id_column}" = ? LIMIT 1'
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        row = con.execute(query, (normalized,)).fetchone()
    finally:
        con.close()
    if row is None or not isinstance(row[2], bytes):
        return None
    return PdfSpreadLookup(
        page_id=normalized,
        page_number=page_number,
        side=side,
        id_right=str(row[0]),
        id_left=str(row[1]) if row[1] is not None else None,
        pdf=row[2],
    )
