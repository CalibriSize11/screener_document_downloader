"""
renamer.py — Read PDF content to detect quarter/FY, then generate clean filename.

Priority:
  1. Extract text from first N pages of the PDF itself.
  2. Fall back to the Screener title string.
  3. Fall back to month-based inference from the date label.
  4. If none work confidently → keep original filename + flag for review.
"""

import logging
import re
from pathlib import Path
from typing import Optional

from config import (
    FY_ONLY_PATTERNS,
    MONTH_NAME_TO_NUM,
    MONTH_TO_QUARTER,
    PDF_PAGES_TO_READ,
    QUARTER_PATTERNS,
    QUARTER_WORD_MAP,
)

log = logging.getLogger("screener")


# ── PDF text extraction ────────────────────────────────────────────────────────
def extract_pdf_text(pdf_path: Path, max_pages: int = PDF_PAGES_TO_READ) -> str:
    """Try pdfplumber first, fall back to PyMuPDF."""
    text = _try_pdfplumber(pdf_path, max_pages)
    if not text.strip():
        text = _try_pymupdf(pdf_path, max_pages)
    return text


def _try_pdfplumber(pdf_path: Path, max_pages: int) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            pages = pdf.pages[:max_pages]
            return "\n".join(p.extract_text() or "" for p in pages)
    except Exception as e:
        log.debug(f"pdfplumber failed: {e}")
        return ""


def _try_pymupdf(pdf_path: Path, max_pages: int) -> str:
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(str(pdf_path))
        texts = []
        for i in range(min(max_pages, len(doc))):
            texts.append(doc[i].get_text())
        doc.close()
        return "\n".join(texts)
    except Exception as e:
        log.debug(f"PyMuPDF failed: {e}")
        return ""


# ── Quarter / FY detection ─────────────────────────────────────────────────────
def _fy_short(year_str: str) -> str:
    """Convert '2026' or '26' to 2-digit string '26'."""
    y = year_str.strip()
    return y[-2:]


def _parse_fy_range(text: str) -> Optional[str]:
    """
    Detect patterns like '2025-26' or '2025-2026' and return '26'.
    """
    m = re.search(r'(20\d{2})\s*[-–]\s*(\d{2,4})', text)
    if m:
        end = m.group(2)
        return end[-2:]
    return None


def detect_quarter_from_text(text: str) -> Optional[tuple[str, str]]:
    """
    Parse text and return (quarter, fy_short) e.g. ('Q4', '26').
    Returns None if not found with confidence.
    """
    if not text:
        return None

    for pattern, match_type in QUARTER_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue

        if match_type == "q_fy2":
            q   = m.group(1).upper()
            fy  = m.group(2)
            return q, fy

        if match_type == "q_fy4":
            q  = m.group(1).upper()
            fy = _fy_short(m.group(2))
            return q, fy

        if match_type == "qnum_fy":
            q  = f"Q{m.group(1)}"
            fy = _fy_short(m.group(2))
            return q, fy

        if match_type == "qword_fy":
            word = m.group(1).lower()
            q    = QUARTER_WORD_MAP.get(word)
            if q:
                fy = _fy_short(m.group(2))
                return q, fy

        if match_type == "q_year4":
            q  = m.group(1).upper()
            fy = _fy_short(m.group(2))
            return q, fy

    return None


def detect_fy_from_text(text: str) -> Optional[str]:
    """Return 2-digit FY year from text if found, else None."""
    # Check range pattern first (e.g. 2025-26)
    fy = _parse_fy_range(text)
    if fy:
        return fy

    for pat in FY_ONLY_PATTERNS:
        m = pat.search(text)
        if m:
            return _fy_short(m.group(1))
    return None


def infer_quarter_from_date_label(date_label: str) -> Optional[tuple[str, str]]:
    """
    Use Screener's date label (e.g. "May 2026") + Indian FY month mapping
    to infer (quarter, fy_short).  Flagged as inferred, not confirmed.
    """
    if not date_label:
        return None

    words = date_label.lower().split()
    month_num = None
    year = None

    for w in words:
        if w in MONTH_NAME_TO_NUM:
            month_num = MONTH_NAME_TO_NUM[w]
        try:
            candidate = int(w)
            if 2000 <= candidate <= 2099:
                year = candidate
        except ValueError:
            pass

    if month_num is None or year is None:
        return None

    mapping = MONTH_TO_QUARTER.get(month_num)
    if mapping is None:
        return None

    q, offset = mapping
    fy_year   = year + offset
    return q, str(fy_year)[-2:]


# ── Filename generation ────────────────────────────────────────────────────────
def build_filename(
    doc_type:   str,
    quarter:    Optional[str],
    fy:         Optional[str],
    title:      str,
    original:   str,
    inferred:   bool = False,
) -> tuple[str, bool]:
    """
    Returns (filename_without_extension, needs_review).
    needs_review=True means the name was guessed or kept original.
    """
    if quarter and fy:
        label = f"{quarter}FY{fy}"
        if doc_type == "annual_reports":
            name = f"FY{fy} Annual Report"
        elif doc_type == "quarterly_results":
            name = label
        elif doc_type == "investor_presentations":
            name = f"{label} Investor Presentation"
        elif doc_type == "concalls":
            # Preserve PPT / Transcript distinction from title
            suffix = "Transcript" if "transcript" in title.lower() else "PPT"
            name = f"{label} Concall {suffix}"
        else:
            name = f"{label} {doc_type}"

        return name, inferred

    # Annual report: FY only
    if doc_type == "annual_reports" and fy:
        return f"FY{fy} Annual Report", inferred

    # Cannot determine — keep original
    stem = Path(original).stem
    return stem, True  # needs_review = True


# ── Master renaming function ───────────────────────────────────────────────────
def get_final_filename(
    pdf_path:   Path,
    doc_type:   str,
    title:      str,
    date_label: str,
) -> tuple[str, bool]:
    """
    Full pipeline:
      1. Read PDF text → detect quarter/FY
      2. Fallback: detect from title string
      3. Fallback: infer from date_label (month mapping)
      4. Fallback: keep original stem

    Returns (final_filename_no_ext, needs_review).
    """
    original = pdf_path.name

    # ── Step 1: PDF text ──────────────────────────────────────────────────────
    log.debug(f"Reading PDF for quarter detection: {pdf_path.name}")
    pdf_text = extract_pdf_text(pdf_path)
    result   = detect_quarter_from_text(pdf_text)
    if result:
        q, fy = result
        log.debug(f"  → Found in PDF: {q} FY{fy}")
        return build_filename(doc_type, q, fy, title, original, inferred=False)

    # ── Step 2: Screener title string ─────────────────────────────────────────
    result = detect_quarter_from_text(title)
    if result:
        q, fy = result
        log.debug(f"  → Found in title: {q} FY{fy}")
        return build_filename(doc_type, q, fy, title, original, inferred=False)

    # Annual-only: FY from PDF or title
    if doc_type == "annual_reports":
        fy = detect_fy_from_text(pdf_text) or detect_fy_from_text(title)
        if fy:
            log.debug(f"  → FY from text: FY{fy}")
            return build_filename(doc_type, None, fy, title, original, inferred=False)

    # ── Step 3: Month-based inference ─────────────────────────────────────────
    result = infer_quarter_from_date_label(date_label)
    if result:
        q, fy = result
        log.debug(f"  → Inferred from date label '{date_label}': {q} FY{fy}")
        return build_filename(doc_type, q, fy, title, original, inferred=True)

    # ── Step 4: Keep original ─────────────────────────────────────────────────
    log.debug(f"  → Could not determine quarter — keeping original name")
    stem = Path(original).stem
    return stem, True
