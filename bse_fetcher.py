"""
bse_fetcher.py — Fetch quarterly result PDFs from BSE using the
                  unofficial `bse` Python library.

Install:  pip install bse

How it works:
  1. Extract BSE scrip code from the Screener page HTML (already fetched).
  2. Call BSE.announcements(scripcode, category=CATEGORY.RESULT) with pagination.
  3. Filter for actual quarterly/annual result filings (not board meeting notices).
  4. Return DocumentLink objects for each result PDF found.

BSE scrip code for ACE: 532762  (visible in BSE link on Screener page)
"""

import logging
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("screener")

# Regex to extract BSE scrip code from Screener page HTML
# e.g. "BSE: 532762" or href="...bseindia.com/.../532762/"
BSE_CODE_PATTERNS = [
    re.compile(r'BSE[:/\s]+(\d{6})', re.IGNORECASE),
    re.compile(r'bseindia\.com[^"]*?/(\d{6})[/"]'),
]

# Keywords that indicate an actual result filing (not just a board meeting notice)
RESULT_KEYWORDS = [
    "financial results",
    "quarterly results",
    "standalone results",
    "consolidated results",
    "unaudited results",
    "audited results",
    "results for the quarter",
    "results for quarter",
    "q1", "q2", "q3", "q4",
]

# Keywords that indicate it's a NOTICE of board meeting (not the result PDF itself)
NOTICE_KEYWORDS = [
    "board meeting intimation",
    "intimation of board meeting",
    "notice of board meeting",
    "schedule.*board meeting",
    "board meeting.*scheduled",
    "outcome of board meeting",      # outcome = the decision, often attached separately
    "prior intimation",
]


@dataclass
class BSEAnnouncement:
    scrip_code:   str
    headline:     str
    subject:      str
    date:         str
    pdf_url:      str
    is_quarterly: bool = False
    quarter_hint: str = ""   # e.g. "Q3 FY26" if detectable from headline


def _is_result_filing(headline: str, subject: str) -> bool:
    """
    Return True if this announcement is an actual result filing
    (not a board meeting notice or other non-result filing).
    """
    text = (headline + " " + subject).lower()

    # Reject board meeting notices
    for kw in NOTICE_KEYWORDS:
        if re.search(kw, text):
            return False

    # Accept if any result keyword found
    for kw in RESULT_KEYWORDS:
        if kw in text:
            return True

    return False


def _extract_quarter_hint(headline: str, subject: str) -> str:
    """Try to extract quarter label from announcement headline/subject."""
    text = headline + " " + subject

    # Look for explicit quarter patterns
    patterns = [
        re.compile(r'(Q[1-4])\s*[-]?\s*FY\s*(\d{2,4})', re.IGNORECASE),
        re.compile(r'quarter\s+ended?\s+([\w]+\s+\d{4})', re.IGNORECASE),
        re.compile(r'(january|february|march|april|may|june|july|august|'
                   r'september|october|november|december)\s+(\d{4})', re.IGNORECASE),
    ]
    for pat in patterns:
        m = pat.search(text)
        if m:
            return m.group(0)
    return ""


def extract_bse_code_from_html(html: str) -> Optional[str]:
    """
    Extract BSE scrip code from Screener page HTML.
    Returns 6-digit code as string, or None if not found.
    """
    for pat in BSE_CODE_PATTERNS:
        m = pat.search(html)
        if m:
            code = m.group(1)
            if len(code) == 6 and code.isdigit():
                log.debug(f"Found BSE code: {code}")
                return code
    return None


def fetch_quarterly_results(
    scrip_code:  str,
    years_back:  int = 5,
) -> list[BSEAnnouncement]:
    """
    Fetch all quarterly result announcements from BSE for a given scrip code.

    Args:
        scrip_code:  BSE 6-digit scrip code (e.g. '532762' for ACE)
        years_back:  How many years of history to fetch (default 5)

    Returns:
        List of BSEAnnouncement objects for actual result filings.
    """
    try:
        from bse import BSE
        from bse.constants import CATEGORY
    except ImportError:
        log.error(
            "The 'bse' library is not installed. "
            "Run: pip install bse"
        )
        return []

    announcements: list[BSEAnnouncement] = []

    to_date   = datetime.now()
    from_date = to_date - timedelta(days=365 * years_back)

    log.info(
        f"Fetching BSE result announcements for scrip {scrip_code} "
        f"({from_date.strftime('%d/%m/%Y')} → {to_date.strftime('%d/%m/%Y')})"
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            with BSE(download_folder=tmp_dir) as bse:
                page_no   = 1
                total     = None
                per_page  = 20  # BSE default page size

                while True:
                    data = bse.announcements(
                        page_no    = page_no,
                        scripcode  = scrip_code,
                        category   = CATEGORY.RESULT,
                        from_date  = from_date,
                        to_date    = to_date,
                    )

                    table  = data.get("Table",  [])
                    table1 = data.get("Table1", [])

                    # Total record count (from first response)
                    if total is None and table1:
                        total = table1[0].get("ROWCNT", 0)
                        log.info(f"  Total result announcements found: {total}")

                    if not table:
                        break

                    for item in table:
                        headline = item.get("HEADLINE", "")
                        subject  = item.get("SUBJECT",  "")
                        date_str = item.get("DT_TM",    "")
                        pdf_url  = _build_pdf_url(item)

                        if not pdf_url:
                            log.debug(f"  No PDF URL for: {headline[:60]}")
                            continue

                        if not _is_result_filing(headline, subject):
                            log.debug(f"  Skipping non-result: {headline[:60]}")
                            continue

                        quarter_hint = _extract_quarter_hint(headline, subject)

                        print(f"    [Quarterly Result] {headline[:70]}")
                        print(f"                       date={date_str} | quarter={quarter_hint}")
                        print(f"                       url={pdf_url}")

                        announcements.append(BSEAnnouncement(
                            scrip_code   = scrip_code,
                            headline     = headline,
                            subject      = subject,
                            date         = date_str,
                            pdf_url      = pdf_url,
                            is_quarterly = True,
                            quarter_hint = quarter_hint,
                        ))

                    # Pagination
                    if total and page_no * per_page >= total:
                        break
                    page_no += 1

        except Exception as e:
            log.error(f"BSE API error: {e}")

    log.info(f"  Found {len(announcements)} quarterly result PDFs on BSE")
    return announcements


def _build_pdf_url(item: dict) -> Optional[str]:
    """
    Build the PDF download URL from an announcement dict.
    BSE announcement dicts typically have ATTACHMENTNAME or similar fields.
    Common URL pattern:
      https://www.bseindia.com/xml-data/corpfiling/AttachLive/{ATTACHMENTNAME}
      https://www.bseindia.com/xml-data/corpfiling/AttachHis/{ATTACHMENTNAME}
    """
    # Try known field names for attachment
    attachment = (
        item.get("ATTACHMENTNAME")
        or item.get("ATTACHMENT")
        or item.get("PDF_NAME")
        or item.get("FILENAME")
        or ""
    )

    if attachment:
        # Determine Live vs His (historical) based on date
        date_str = item.get("DT_TM", "")
        try:
            dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
            cutoff = datetime.now() - timedelta(days=60)
            folder = "AttachLive" if dt > cutoff else "AttachHis"
        except Exception:
            folder = "AttachHis"

        return f"https://www.bseindia.com/xml-data/corpfiling/{folder}/{attachment}"

    # Fallback: if the item already has a direct URL field
    for field in ["URL", "LINK", "HREF", "PDFLINK"]:
        val = item.get(field, "")
        if val and val.startswith("http"):
            return val

    return None


# ── Standalone test ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("\nTesting BSE quarterly results fetch for ACE (532762)...\n")
    results = fetch_quarterly_results("532762", years_back=3)

    print(f"\n{'='*60}")
    print(f"SUMMARY: {len(results)} quarterly result PDFs found")
    print(f"{'='*60}\n")

    # Also print raw field names from first item to verify structure
    print("Run this to see raw announcement field names:")
    print("""
    from bse import BSE
    from bse.constants import CATEGORY
    import tempfile, json
    with tempfile.TemporaryDirectory() as tmp:
        with BSE(download_folder=tmp) as bse:
            data = bse.announcements(scripcode='532762', category=CATEGORY.RESULT)
            if data.get('Table'):
                print(json.dumps(data['Table'][0], indent=2))
    """)
