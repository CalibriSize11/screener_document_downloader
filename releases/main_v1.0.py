"""
main.py — Screener document downloader (v2 — type-safe pipeline).

Every document carries an explicit document_type from discovery through download.
Filenames are generated from document_type, never inferred from quarter labels.

Run:
    python main.py --company ACE --base "C:/Users/Ram/Research"
    python main.py --url https://www.screener.in/company/ACE/consolidated/ --base "C:/Users/Ram/Research"
"""

import argparse
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

try:
    import pypdf
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False

# ── Document types ─────────────────────────────────────────────────────────────
ANNUAL_REPORT         = "annual_report"
CREDIT_REPORT         = "credit_report"
QUARTERLY_RESULT      = "quarterly_result"
INVESTOR_PRESENTATION = "investor_presentation"
TRANSCRIPT            = "transcript"

TYPE_TO_FOLDER = {
    ANNUAL_REPORT:         "Annual Reports",
    CREDIT_REPORT:         "Credit Reports",
    QUARTERLY_RESULT:      "Quarterly Results",
    INVESTOR_PRESENTATION: "Investor Presentations",
    TRANSCRIPT:            "Concall Transcripts",
}


def make_filename(doc_type: str, label: str) -> str:
    """
    Generate filename purely from doc_type and label.
    Never infer doc_type from label.

    Quarterly results use 'FY26 Q1 Results.pdf' format so Windows Explorer
    groups all quarters of a financial year together when sorted by name.
    Label for quarterly is 'Q1FY26' — we reformat to 'FY26 Q1' here.
    """
    if doc_type == ANNUAL_REPORT:
        return f"{label} Annual Report.pdf"
    if doc_type == CREDIT_REPORT:
        return f"{label} Credit Rating.pdf"
    if doc_type == QUARTERLY_RESULT:
        # Reformat 'Q1FY26' → 'FY26 Q1' for Windows Explorer grouping
        m = re.match(r'^(Q[1-4])FY(\d{2})$', label, re.IGNORECASE)
        if m:
            return f"FY{m.group(2)} {m.group(1)} Results.pdf"
        return f"{label} Results.pdf"   # fallback for unusual labels
    if doc_type == INVESTOR_PRESENTATION:
        return f"{label} Investor Presentation.pdf"
    if doc_type == TRANSCRIPT:
        return f"{label} Transcript.pdf"
    return f"{label}.pdf"


def sanitize_filename(name: str) -> str:
    """
    Remove characters that are invalid in Windows filenames.
    Invalid: \\ / : * ? " < > |
    Falls back to 'Unknown' if the result is empty.
    """
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, "")
    name = re.sub(r'[\x00-\x1f]', "", name)   # control chars
    name = re.sub(r'\s+', " ", name).strip()
    stem = name.replace(".pdf", "").strip()
    if not stem:
        return "Unknown.pdf"
    return name if name.endswith(".pdf") else name


# ── Document dataclass ─────────────────────────────────────────────────────────
@dataclass
class Document:
    document_type: str    # one of the constants above — never changes after creation
    label:         str    # e.g. "Q4FY26", "FY26", "2025-04"
    url:           str    # source URL
    date_display:  str    # human-readable, for terminal output
    filename:      str = field(init=False)
    source:        str = ""   # "screener_annual" | "screener_credit" | "screener_concall" | "bse"
    subject:       str = ""   # BSE NEWSSUB, for debug

    def __post_init__(self):
        self.filename = sanitize_filename(make_filename(self.document_type, self.label))

    @property
    def dedup_key(self) -> tuple:
        """Deduplication key: one file per (label, type)."""
        return (self.label, self.document_type)


# ── Constants ──────────────────────────────────────────────────────────────────
SCREENER_BASE   = "https://www.screener.in"
SCREENER_SEARCH = "https://www.screener.in/api/company/search/"
YEARS_BACK      = 7
CUTOFF_YEAR     = datetime.now().year - YEARS_BACK
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

MONTH_NAME = {
    "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
    "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12,
    "january":1,"february":2,"march":3,"april":4,"june":6,
    "july":7,"august":8,"september":9,"october":10,
    "november":11,"december":12,
}

# Month of announcement → (Indian quarter, FY-end year offset)
# Used for BSE announcement dates and Screener concall date labels.
# Example: company announces in Aug 2025 → Q1FY26 results (April-June)
MONTH_TO_QUARTER = {
    1:("Q3",0), 2:("Q3",0), 3:("Q3",0),
    4:("Q4",0), 5:("Q4",0), 6:("Q4",0),
    7:("Q1",1), 8:("Q1",1), 9:("Q1",1),
    10:("Q2",1),11:("Q2",1),12:("Q2",1),
}

# Period-END month → (Indian quarter, FY-end year offset)
# Used for Screener quarterly table COLUMN HEADERS which show the quarter's last month.
# Indian FY runs Apr-Mar. Quarter periods:
#   Q1: Apr-Jun  → column header says "Jun YYYY"  → FY ends Mar YYYY+1 → FYyy = (YYYY+1)[-2:]
#   Q2: Jul-Sep  → column header says "Sep YYYY"  → FY ends Mar YYYY+1 → FYyy = (YYYY+1)[-2:]
#   Q3: Oct-Dec  → column header says "Dec YYYY"  → FY ends Mar YYYY+1 → FYyy = (YYYY+1)[-2:]
#   Q4: Jan-Mar  → column header says "Mar YYYY"  → FY ends Mar YYYY   → FYyy = YYYY[-2:]
PERIOD_END_TO_QUARTER = {
    6:  ("Q1", 1),   # Jun 2024 → Q1FY25
    9:  ("Q2", 1),   # Sep 2024 → Q2FY25
    12: ("Q3", 1),   # Dec 2024 → Q3FY25  ← was broken with MONTH_TO_QUARTER
    3:  ("Q4", 0),   # Mar 2025 → Q4FY25
}

# BSE subcategories that indicate actual financial results

# ══════════════════════════════════════════════════════════════════════
# QUARTER / FY LABEL HELPERS
# ══════════════════════════════════════════════════════════════════════

def quarter_from_text(text: str):
    """Extract (Qx, YY) from text like 'Q4 FY26' or 'Q4FY2026'. Returns None if not found."""
    m = re.search(r'\b(Q[1-4])\s*[-–]?\s*FY\s*(20)?(\d{2})\b', text, re.IGNORECASE)
    if m:
        return m.group(1).upper(), m.group(3)
    return None


def label_from_month(date_label: str) -> str:
    """Convert 'May 2026' → 'Q4FY26'. Falls back to 'May2026' if parsing fails."""
    parts = date_label.lower().split()
    month_num = year = None
    for p in parts:
        if p in MONTH_NAME:
            month_num = MONTH_NAME[p]
        try:
            y = int(p)
            if 2000 <= y <= 2099:
                year = y
        except ValueError:
            pass
    if month_num and year:
        q, offset = MONTH_TO_QUARTER.get(month_num, ("Q?", 0))
        fy = str(year + offset)[-2:]
        return f"{q}FY{fy}"
    return date_label.replace(" ", "")


def label_from_bse_date(news_dt: str) -> str:
    """
    Convert a BSE NEWS_DT string ('2025-08-14T...') to a quarter label.
    Uses announcement-month mapping (MONTH_TO_QUARTER), not period-end mapping.
    Example: '2025-08-14' → 'Q1FY26' (Aug announcement = Q1 Apr-Jun results)
    Falls back to 'UnknownQ' if parsing fails.
    """
    try:
        dt = datetime.strptime(news_dt[:10], "%Y-%m-%d")
        return label_from_month(dt.strftime("%b %Y"))
    except Exception:
        return "UnknownQ"


def label_from_period_end(date_label: str) -> str:
    """
    Convert Screener quarterly table column header to quarter label.
    Column headers are period-END months: 'Jun 2024' → 'Q1FY25'.

    Different from label_from_month() which handles announcement months.
    Only recognises Jun/Sep/Dec/Mar (the actual Indian quarter-end months).
    Falls back to label_from_month() for anything else.
    """
    parts     = date_label.lower().split()
    month_num = year = None
    for p in parts:
        if p in MONTH_NAME:
            month_num = MONTH_NAME[p]
        try:
            y = int(p)
            if 2000 <= y <= 2099:
                year = y
        except ValueError:
            pass

    if month_num and year and month_num in PERIOD_END_TO_QUARTER:
        q, offset = PERIOD_END_TO_QUARTER[month_num]
        fy = str(year + offset)[-2:]
        return f"{q}FY{fy}"

    # Not a standard quarter-end month (e.g. unusual Screener entry)
    # Fall back to announcement-month logic rather than returning garbage
    return label_from_month(date_label)
    """Convert '2025-08-14' → 'Q1FY26'."""
    try:
        dt = datetime.strptime(news_dt[:10], "%Y-%m-%d")
        return label_from_month(dt.strftime("%b %Y"))
    except Exception:
        return "UnknownQ"


def fy_from_title(title: str) -> str:
    """'Financial Year 2025' → 'FY25'."""
    m = re.search(r'\b(20\d{2})\b', title)
    return f"FY{m.group(1)[-2:]}" if m else "FY??"


def credit_label_from_text(text: str, title: str = "", url: str = "") -> str:
    """
    Extract 'YYYY-MM' from credit rating date text.
    Returns a safe fallback if parsing fails — never returns '?' characters.
    Logs context when parsing fails so the issue can be investigated.
    """
    yr_m  = re.search(r'\b(20\d{2})\b', text)
    mon_m = re.search(
        r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\b',
        text, re.IGNORECASE
    )

    if not yr_m or not mon_m:
        # Log so the user knows why the date couldn't be parsed
        fallback = f"Unknown-{datetime.now().strftime('%Y%m%d')}"
        print(f"    [Could not parse rating date — using '{fallback}']")
        if title:
            print(f"      Title: {title}")
        if url:
            print(f"      URL  : {url}")
        return fallback

    year    = yr_m.group(1)
    mon_num = str(MONTH_NAME.get(mon_m.group(1)[:3].lower(), "01")).zfill(2)
    return f"{year}-{mon_num}"


# ══════════════════════════════════════════════════════════════════════
# COMPANY RESOLUTION
# ══════════════════════════════════════════════════════════════════════

def resolve_company(raw: str) -> tuple:
    if "screener.in/company/" in raw:
        url   = raw if raw.startswith("http") else "https://" + raw
        parts = url.rstrip("/").split("/")
        name  = next(
            (p for p in reversed(parts) if p and p not in ("consolidated","standalone")),
            "COMPANY"
        )
        return name.upper(), url

    print(f"\nSearching Screener for '{raw}'...")
    try:
        resp = requests.get(SCREENER_SEARCH, params={"q": raw, "v": "3"},
                            headers=HEADERS, timeout=15)
        resp.raise_for_status()
        results = resp.json()
    except Exception as e:
        print(f"Search failed: {e}")
        sys.exit(1)

    if not results:
        print(f"No companies found for '{raw}'.")
        sys.exit(1)

    if len(results) == 1:
        r = results[0]
        print(f"Found: {r['name']}")
        return r["name"], SCREENER_BASE + r["url"]

    print("\nMultiple companies found:\n")
    for i, r in enumerate(results[:10], 1):
        print(f"  {i}. {r['name']}  ({r['url']})")
    while True:
        ch = input("\nChoose number (0 to cancel): ").strip()
        if ch == "0":
            sys.exit(0)
        try:
            idx = int(ch) - 1
            if 0 <= idx < len(results):
                r = results[idx]
                return r["name"], SCREENER_BASE + r["url"]
        except ValueError:
            pass


# ══════════════════════════════════════════════════════════════════════
# SCREENER DISCOVERY
# ══════════════════════════════════════════════════════════════════════

def _siblings_html(heading_tag) -> BeautifulSoup:
    siblings = []
    for sib in heading_tag.find_next_siblings():
        if sib.name in ["h2","h3","h4"]:
            break
        siblings.append(str(sib))
    return BeautifulSoup("".join(siblings), "lxml")


def discover_annual_reports(soup: BeautifulSoup) -> list[Document]:
    docs = []
    for h in soup.find_all(["h2","h3"]):
        if "annual report" not in h.get_text().lower():
            continue
        for li in _siblings_html(h).find_all("li"):
            a = li.find("a", href=True)
            if not a:
                continue
            title  = li.get_text(" ", strip=True)
            yr     = re.search(r'\b(20\d{2})\b', title)
            if yr and int(yr.group(1)) <= CUTOFF_YEAR:
                continue
            label  = fy_from_title(title)
            docs.append(Document(
                document_type = ANNUAL_REPORT,
                label         = label,
                url           = a["href"],
                date_display  = title,
                source        = "screener_annual",
            ))
        break
    return docs


def discover_credit_reports(soup: BeautifulSoup) -> list[Document]:
    docs = []
    for h in soup.find_all(["h2","h3"]):
        if "credit" not in h.get_text().lower() and "rating" not in h.get_text().lower():
            continue
        for li in _siblings_html(h).find_all("li"):
            a = li.find("a", href=True)
            if not a:
                continue
            text  = li.get_text(" ", strip=True)
            url   = a["href"]
            yr    = re.search(r'\b(20\d{2})\b', text)
            if yr and int(yr.group(1)) <= CUTOFF_YEAR:
                continue
            # Pass text + url so failures are logged with context
            label = credit_label_from_text(text, title=text, url=url)
            docs.append(Document(
                document_type = CREDIT_REPORT,
                label         = label,
                url           = url,
                date_display  = text,
                source        = "screener_credit",
            ))
        break
    return docs


def discover_concalls_playwright(url: str) -> list[Document]:
    """
    Playwright: confirmed selector 'div.documents.concalls li.flex'.
    Date: <div style='width: 74px'>May 2026</div>
    Transcript: <a class='concall-link' title='Raw Transcript'>Transcript</a>
    PPT:        <a class='concall-link'>PPT</a>
    Classification: link TEXT determines type — not filename, not URL.
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    print("  Opening browser for concalls (Playwright)...")
    html = ""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page    = browser.new_page()
        page.set_extra_http_headers(HEADERS)
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        try:
            page.wait_for_selector("#documents", timeout=15_000)
        except PWTimeout:
            browser.close()
            print("  WARNING: #documents not found.")
            return []
        page.evaluate("document.querySelector('#documents').scrollIntoView()")
        page.wait_for_timeout(2000)
        try:
            page.wait_for_selector("div.documents.concalls li.flex", timeout=10_000)
            html = page.inner_html("div.documents.concalls")
        except PWTimeout:
            print("  WARNING: Concalls list not rendered.")
        browser.close()

    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    rows = soup.find_all("li", class_=lambda c: c and "flex" in c and "flex-gap-8" in c)
    print(f"  Found {len(rows)} concall rows")

    docs = []
    for row in rows:
        date_div = row.find("div", style=lambda s: s and "width: 74px" in s)
        if not date_div:
            continue
        date_label = date_div.get_text(strip=True)
        yr = re.search(r'\b(20\d{2})\b', date_label)
        if yr and int(yr.group(1)) <= CUTOFF_YEAR:
            continue

        label = label_from_month(date_label)

        for a in row.find_all("a", class_="concall-link", href=True):
            href      = a["href"]
            link_text = a.get_text(strip=True)   # "Transcript" or "PPT"
            title_att = (a.get("title") or "")

            # Skip audio, video, webcast, replay — PDF only
            AUDIO_URL_PATTERNS = [
                ".mp3", ".mp4", ".wav", ".ogg", ".aac",
                "youtube.com", "youtu.be",
                "drive.google.com",
                "ccreservations.com",
                "choruscall.com",
                "globalmeet.com",
                "eventshare.com",
                "webcast",
                "replay",
                "audio",
                "recording",
            ]
            if any(x in href.lower() for x in AUDIO_URL_PATTERNS):
                continue

            # ── Classification by link TEXT, not URL ──────────────────────────
            if link_text == "Transcript" or "Raw Transcript" in title_att:
                doc_type = TRANSCRIPT
            elif link_text == "PPT":
                doc_type = INVESTOR_PRESENTATION
            else:
                continue   # AI Summary, REC — skip

            docs.append(Document(
                document_type = doc_type,
                label         = label,
                url           = href,
                date_display  = date_label,
                source        = "screener_concall",
            ))

    return docs


# ══════════════════════════════════════════════════════════════════════
# BSE QUARTERLY RESULTS DISCOVERY
# ══════════════════════════════════════════════════════════════════════

def extract_bse_code(html_text: str) -> str:
    for pat in [
        re.compile(r'BSE[:/\s]+(\d{6})', re.IGNORECASE),
        re.compile(r'bseindia\.com[^"\']*?/(\d{6})[/"\']'),
    ]:
        m = pat.search(html_text)
        if m and len(m.group(1)) == 6:
            return m.group(1)
    return ""


def discover_quarterly_results_bse(bse_code: str) -> list[Document]:
    """
    Fetch quarterly financial results directly from BSE using confirmed filters:
      category    = CATEGORY.RESULT  ('Result')
      subcategory = 'Financial Results'

    Confirmed working: returns only genuine financial result filings.
    No title classification, no keyword logic, no presentation contamination.
    BSE does the filtering at the API level.

    Label derivation priority:
      1. Explicit Q+FY pattern in NEWSSUB (e.g. "Q3FY26")
      2. Indian FY quarter inferred from NEWS_DT (announcement date)
    """
    if not bse_code:
        print("  No BSE code — cannot fetch quarterly results.")
        return []
    try:
        from bse import BSE
        from bse.constants import CATEGORY
    except ImportError:
        print("  'bse' not installed — run: pip install bse")
        return []

    from_date = datetime.now() - timedelta(days=365 * YEARS_BACK)
    to_date   = datetime.now()
    docs      = []

    def fetch_page_with_retry(bse, page_no, retries=3):
        for attempt in range(1, retries + 1):
            try:
                return bse.announcements(
                    scripcode    = bse_code,
                    category     = CATEGORY.RESULT,
                    subcategory  = "Financial Results",
                    from_date    = from_date,
                    to_date      = to_date,
                    page_no      = page_no,
                )
            except TimeoutError:
                if attempt < retries:
                    print(f"  BSE timeout (attempt {attempt}/{retries}), retrying in 2s...")
                    time.sleep(2)
                else:
                    print("  BSE timeout — returning results collected so far.")
                    return None
            except Exception as e:
                print(f"  BSE error: {e}")
                return None

    with tempfile.TemporaryDirectory() as tmp:
        with BSE(download_folder=tmp) as bse:
            page_no  = 1
            per_page = 20
            total    = None

            while True:
                print(f"  Fetching BSE page {page_no}...", end=" ", flush=True)
                data = fetch_page_with_retry(bse, page_no)
                if data is None:
                    break

                table  = data.get("Table",  [])
                table1 = data.get("Table1", [])

                if total is None and table1:
                    total = table1[0].get("ROWCNT", 0)
                    print(f"({total} total Financial Results filings)")
                else:
                    print(f"({len(table)} items)")

                if not table:
                    break

                for item in table:
                    category   = item.get("CATEGORYNAME",   "")
                    subcat     = item.get("SUBCATNAME",     "")
                    newssub    = item.get("NEWSSUB",        "")
                    attachment = item.get("ATTACHMENTNAME", "")
                    news_dt    = item.get("NEWS_DT",        "")

                    # Debug: print every record exactly as received
                    print(f"\n    CATEGORYNAME : {category}")
                    print(f"    SUBCATNAME   : {subcat}")
                    print(f"    NEWSSUB      : {newssub[:90]}")
                    print(f"    ATTACHMENTNAME: {attachment}")
                    print(f"    NEWS_DT      : {news_dt[:10]}")

                    # ── Fix 2: Exclude related-party disclosures ──────────────
                    # BSE sometimes files these under SUBCATNAME=Financial Results
                    RELATED_PARTY_PATTERNS = [
                        "related party transaction",
                        "related party disclosure",
                        "disclosure of related party",
                        "half-yearly disclosure",
                        "half yearly disclosure",
                    ]
                    if any(p in newssub.lower() for p in RELATED_PARTY_PATTERNS):
                        print(f"    → SKIP (related party disclosure)")
                        continue

                    if not attachment:
                        print(f"    → SKIP (no attachment)")
                        continue

                    # Build PDF URL
                    try:
                        dt     = datetime.strptime(news_dt[:10], "%Y-%m-%d")
                        folder = "AttachLive" if dt > datetime.now() - timedelta(days=60) else "AttachHis"
                    except Exception:
                        folder = "AttachHis"
                    pdf_url = f"https://www.bseindia.com/xml-data/corpfiling/{folder}/{attachment}"

                    # ── Fix 3: Quarter label derivation (priority order) ───────
                    # 1. Explicit Q+FY pattern in NEWSSUB
                    q_label = None
                    q_parsed = quarter_from_text(newssub)
                    if q_parsed:
                        q_label = f"{q_parsed[0]}FY{q_parsed[1]}"
                        print(f"    → Label: {q_label} (from explicit Q+FY in NEWSSUB)")

                    # 2. Quarter-end month in NEWSSUB (e.g. "December 2024", "March 2025")
                    if not q_label:
                        QEND_MONTHS = {
                            "june": 6, "jun": 6,
                            "september": 9, "sep": 9, "sept": 9,
                            "december": 12, "dec": 12,
                            "march": 3, "mar": 3,
                        }
                        for month_name, month_num in QEND_MONTHS.items():
                            # Look for "Month YYYY" or "Month, YYYY"
                            m = re.search(
                                rf'\b{month_name}\b[,\s]+(\d{{4}})\b',
                                newssub, re.IGNORECASE
                            )
                            if m:
                                year = int(m.group(1))
                                q, offset = PERIOD_END_TO_QUARTER.get(month_num, ("Q?", 0))
                                fy = str(year + offset)[-2:]
                                q_label = f"{q}FY{fy}"
                                print(f"    → Label: {q_label} (from quarter-end month in NEWSSUB: {m.group(0)})")
                                break

                    # 3. NEWS_DT fallback (announcement date, not quarter date)
                    if not q_label:
                        q_label = label_from_bse_date(news_dt)
                        print(f"    → Label: {q_label} (fallback from NEWS_DT {news_dt[:10]})")

                    docs.append(Document(
                        document_type = QUARTERLY_RESULT,
                        label         = q_label,
                        url           = pdf_url,
                        date_display  = news_dt[:10],
                        source        = "bse_financial_results",
                        subject       = newssub[:100],
                    ))

                if total and page_no * per_page >= total:
                    break
                page_no += 1

    print(f"\n  Total quarterly results found: {len(docs)}")
    return docs


# ══════════════════════════════════════════════════════════════════════
# DEDUPLICATION
# ══════════════════════════════════════════════════════════════════════

def deduplicate(docs: list[Document]) -> tuple[list[Document], list[str]]:
    """
    Deduplication rules:

    Investor Presentations — deduplicate by URL only.
      Two presentations with different URLs are kept even if same quarter.
      Same URL from different sources (Screener + BSE) → keep one.

    All other types — deduplicate by (label, document_type).
      One file per quarter per type.

    Global — same URL appearing twice regardless of type → keep first.

    Returns (unique_docs, warning_messages).
    """
    warnings: list[str] = []

    # ── Pass 1: Global URL dedup ───────────────────────────────────────
    # Catches Screener + BSE pointing to identical file
    seen_urls: dict[str, Document] = {}
    after_url: list[Document]      = []
    for doc in docs:
        if doc.url in seen_urls:
            warnings.append(
                f"Same URL for '{doc.filename}' and "
                f"'{seen_urls[doc.url].filename}' — keeping first"
            )
        else:
            seen_urls[doc.url] = doc
            after_url.append(doc)

    # ── Pass 2: Type-specific dedup ───────────────────────────────────
    seen_keys: dict[tuple, Document] = {}
    final: list[Document]            = []
    for doc in after_url:
        if doc.document_type == INVESTOR_PRESENTATION:
            # URL already unique from Pass 1 — keep all distinct presentations
            final.append(doc)
        else:
            key = (doc.label, doc.document_type)
            if key in seen_keys:
                warnings.append(
                    f"Duplicate {doc.document_type} for {doc.label}: "
                    f"'{doc.filename}' — keeping first, dropping this URL"
                )
            else:
                seen_keys[key] = doc
                final.append(doc)

    return final, warnings


# ══════════════════════════════════════════════════════════════════════
# DOWNLOADING — with credit rating HTML resolution
# ══════════════════════════════════════════════════════════════════════

def _is_js_heavy_page(html: str) -> bool:
    """
    Heuristic: returns True if the page is likely JS-rendered with little static content.
    India Ratings pages return just "Loading" in the body.
    """
    from bs4 import BeautifulSoup as _BS
    soup = _BS(html, "lxml")
    body = soup.get_text(strip=True)
    # Less than 500 chars of visible text = almost certainly JS-rendered
    return len(body) < 500


def _resolve_pdf_playwright(url: str) -> str | None:
    """
    Use Playwright to render a JS-heavy page and find the PDF URL.
    Used for India Ratings and other fully JS-rendered credit agency pages.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page()
            page.set_extra_http_headers(HEADERS)

            # Intercept navigation to PDF — if the page navigates to a PDF URL, capture it
            pdf_url_found = []

            def on_response(response):
                ct = response.headers.get("content-type", "")
                ru = response.url
                if "pdf" in ct.lower() or ru.lower().endswith(".pdf"):
                    pdf_url_found.append(ru)

            page.on("response", on_response)
            page.goto(url, wait_until="networkidle", timeout=20_000)
            page.wait_for_timeout(3000)

            # Check if a PDF URL was intercepted during load
            if pdf_url_found:
                browser.close()
                print(f"    [Playwright intercepted PDF URL]")
                return pdf_url_found[-1]

            # Look for PDF links in rendered HTML
            html     = page.content()
            pdf_urls = _find_pdf_links(html, url)

            # Also check for <a> with download attribute
            try:
                links = page.locator("a[href*='.pdf'], a[href*='pdf']").all()
                for link in links[:10]:
                    href = link.get_attribute("href") or ""
                    if href and href not in pdf_urls:
                        pdf_urls.append(href if href.startswith("http") else f"{url.rstrip('/')}/{href.lstrip('/')}")
            except Exception:
                pass

            browser.close()

            if pdf_urls:
                print(f"    [Playwright found {len(pdf_urls)} PDF link(s)]")
                # Create a temporary session for size check
                try:
                    s = requests.Session()
                    s.headers.update(HEADERS)
                    return _pick_largest_pdf(pdf_urls, s)
                except Exception:
                    return pdf_urls[0]

            print(f"    [Playwright: no PDF found on: {url[:65]}]")
            return None

    except Exception as e:
        print(f"    [Playwright PDF resolver error: {e}]")
        return None


def _find_pdf_links(html: str, base_url: str) -> list[str]:
    """
    Extract all PDF URLs from an HTML page.
    Converts relative URLs to absolute using base_url.
    Returns deduplicated list preserving order.
    """
    soup  = BeautifulSoup(html, "lxml")
    found = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        abs_url = urljoin(base_url, href)
        if not abs_url.startswith("http"):
            continue
        # Accept: href ends with .pdf, or contains /pdf/ path segment
        if href.lower().endswith(".pdf") or re.search(r'[/=]pdf[/.]', href, re.IGNORECASE):
            found.append(abs_url)

    for tag in soup.find_all(["iframe", "embed", "object"]):
        src = (tag.get("src") or tag.get("data") or "").strip()
        if src and ".pdf" in src.lower():
            found.append(urljoin(base_url, src))

    # Deduplicate preserving order
    seen = set()
    return [u for u in found if not (u in seen or seen.add(u))]


def _pick_largest_pdf(pdf_urls: list[str], session: requests.Session) -> str:
    """
    Given a list of PDF URLs, return the one with the largest Content-Length.
    Falls back to the first URL if HEAD requests fail.
    """
    if len(pdf_urls) == 1:
        return pdf_urls[0]

    best_url  = pdf_urls[0]
    best_size = -1
    for url in pdf_urls:
        try:
            head = session.head(url, timeout=10, allow_redirects=True)
            size = int(head.headers.get("content-length", -1))
            if size > best_size:
                best_size = size
                best_url  = url
        except Exception:
            pass

    return best_url


def resolve_pdf_url(url: str, session: requests.Session) -> str | None:
    """
    Resolve any URL to a direct downloadable PDF URL.

    Works for all rating agencies and BSE HTML wrappers.
    Detects Screener login-gated quarterly URLs and returns None clearly.
    """
    try:
        head_resp = session.head(url, timeout=15, allow_redirects=True)
        ct        = head_resp.headers.get("content-type", "")
        final_url = head_resp.url   # URL after any redirects

        if "html" not in ct and ct:
            return url   # direct PDF or binary

        # HTML page — fetch it
        resp = session.get(url, timeout=20)

        # Detect Screener login gate: redirected to /login/ or /register/
        if "screener.in" in final_url and any(
            x in final_url for x in ["/login/", "/register/", "/premium/"]
        ):
            print(f"    [Screener login required — Raw PDF not available without account]")
            return None

        # Check if page content suggests a login wall
        body_lower = resp.text[:2000].lower()
        if "screener.in" in url and (
            "login" in body_lower or "sign in" in body_lower
        ):
            print(f"    [Screener login wall detected — skipping]")
            return None

        pdf_urls = _find_pdf_links(resp.text, url)

        if not pdf_urls:
            # India Ratings and other JS-heavy pages — try Playwright
            if _is_js_heavy_page(resp.text):
                print(f"    [JS-rendered page detected — trying Playwright...]")
                return _resolve_pdf_playwright(url)
            print(f"    [No PDF links found on: {url[:65]}]")
            return None

        chosen = _pick_largest_pdf(pdf_urls, session)
        if len(pdf_urls) > 1:
            print(f"    [Found {len(pdf_urls)} PDF links — picked largest]")
        return chosen

    except Exception as e:
        print(f"    [URL resolution error: {e}]")
        return None


def download_file(url: str, dest: Path, session: requests.Session) -> bool:
    for attempt in range(1, 4):
        try:
            # For HTML wrapper pages, resolve to actual PDF URL first
            final_url = resolve_pdf_url(url, session)
            if not final_url:
                return False

            with session.get(final_url, stream=True, timeout=60,
                             allow_redirects=True) as resp:
                resp.raise_for_status()
                ct = resp.headers.get("content-type","")
                if "html" in ct:
                    print(f"    [Still HTML after resolution — skipping]")
                    return False
                with open(dest, "wb") as f:
                    for chunk in resp.iter_content(65536):
                        if chunk:
                            f.write(chunk)

            if dest.stat().st_size < 512:
                print(f"    [File too small ({dest.stat().st_size}B) — skipping]")
                dest.unlink(missing_ok=True)
                return False
            return True

        except Exception as e:
            if attempt < 3:
                time.sleep(2 ** attempt)
            else:
                print(f"    [Failed: {e}]")
    return False


# ══════════════════════════════════════════════════════════════════════
# PDF VALIDATION
# ══════════════════════════════════════════════════════════════════════

def validate_pdf(path: Path) -> tuple[bool, str]:
    """
    Verify the file at path is a real, readable PDF with at least 1 page.

    Returns (is_valid, reason).

    Catches two BSE failure modes:
      1. BSE returns an HTML error page saved with .pdf extension.
         These start with b'<' instead of b'%PDF'.
      2. BSE returns a valid PDF container but with 0 pages (rare).

    Uses pypdf if available; falls back to magic-byte check only.
    """
    try:
        # Check magic bytes — all real PDFs start with %PDF
        with open(path, "rb") as f:
            header = f.read(5)
        if header != b"%PDF-":
            # Read a bit more to show what it actually is
            with open(path, "rb") as f:
                preview = f.read(120).decode("utf-8", errors="replace")
            return False, f"Not a PDF (starts with: {preview[:80]!r})"

        # Full parse with pypdf
        if PYPDF_AVAILABLE:
            reader = pypdf.PdfReader(str(path))
            pages  = len(reader.pages)
            if pages == 0:
                return False, "PDF opened but has 0 pages"
            return True, f"{pages} page(s)"

        # pypdf not available — magic bytes passed, accept
        return True, "magic bytes OK (pypdf not installed for full check)"

    except Exception as e:
        return False, f"pypdf error: {e}"


def _unique_dest(folder: Path, filename: str) -> Path:
    """Return a non-colliding path for filename in folder."""
    p = folder / filename
    if not p.exists():
        return p
    stem = Path(filename).stem
    for i in range(2, 500):
        p = folder / f"{stem} ({i}).pdf"
        if not p.exists():
            return p
    return folder / filename


def _ppt_filename(doc) -> str:
    """
    Fix 6: Generate a meaningful filename for investor presentations.
    When multiple genuine presentations exist for the same quarter, include
    the publication date so files are distinguishable in Windows Explorer.

    Examples:
      Single PPT for quarter     → 'FY26 Q2 Investor Presentation.pdf'
      PPT with date in label     → 'FY26 Q2 2025-08-14 Investor Presentation.pdf'
    """
    base_label = doc.label         # e.g. 'Q2FY26'
    date_str   = doc.date_display  # e.g. 'Aug 2025' or '2025-08-14'

    # Reformat label if it's in QxFYyy format (same logic as make_filename)
    m = re.match(r'^(Q[1-4])FY(\d{2})$', base_label, re.IGNORECASE)
    if m:
        label_part = f"FY{m.group(2)} {m.group(1)}"
    else:
        label_part = base_label

    # Include date_display to distinguish multiple PPTs for same quarter
    # Sanitize date_str for use in filename
    safe_date = re.sub(r'[\\/:*?"<>|]', "", date_str).strip()
    if safe_date and safe_date not in label_part:
        return sanitize_filename(f"{label_part} {safe_date} Investor Presentation.pdf")
    return sanitize_filename(f"{label_part} Investor Presentation.pdf")


def finalize_presentations(
    temp_files: list,   # [(doc, temp_Path), ...]
    folder: Path,
) -> tuple[int, int]:
    """
    Hash-dedup all downloaded investor presentation temp files and
    save unique ones under their final filenames.

    Algorithm:
      1. Hash all existing files in the folder (from previous runs).
      2. For each temp file: compute hash.
         - If hash already seen → duplicate → delete temp.
         - If hash is new → save as final filename.
    Returns (saved_count, dupes_removed_count).
    """
    import hashlib

    saved     = 0
    dupes     = 0
    hash_map: dict[str, Path] = {}   # hash → final saved path

    # Seed with files already in folder (re-run safety)
    for existing in sorted(folder.glob("*.pdf")):
        if existing.name.startswith("_ppt_"):
            continue
        try:
            h = hashlib.sha256(existing.read_bytes()).hexdigest()
            hash_map[h] = existing
        except Exception:
            pass

    for doc, tmp in temp_files:
        try:
            h = hashlib.sha256(tmp.read_bytes()).hexdigest()
        except Exception:
            # Can't hash — save anyway with date suffix
            dest = _unique_dest(folder, _ppt_filename(doc))
            tmp.rename(dest)
            print(f"  ✅ Saved (no hash): {dest.name}")
            saved += 1
            continue

        if h in hash_map:
            print(f"  ♻  Hash duplicate: '{_ppt_filename(doc)}'")
            print(f"       same as:       '{hash_map[h].name}' — deleted")
            tmp.unlink(missing_ok=True)
            dupes += 1
        else:
            dest = _unique_dest(folder, _ppt_filename(doc))
            tmp.rename(dest)
            hash_map[h] = dest
            print(f"  ✅ Saved: {dest.name}")
            saved += 1

    return saved, dupes


def _download_credit_report(url: str, dest: Path, session: requests.Session) -> bool:
    """
    Fix 5: Save whatever Screener's credit report URL returns, exactly as received.
    No scraping. No agency-specific logic. No PDF-link hunting.
    Follow redirects, save the final response body.
    A failed credit report must never be silently lost.
    """
    for attempt in range(1, 4):
        try:
            with session.get(url, stream=True, timeout=60,
                             allow_redirects=True) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in resp.iter_content(65536):
                        if chunk:
                            f.write(chunk)
            if dest.stat().st_size < 100:
                print(f"    [Response too small ({dest.stat().st_size}B)]")
                dest.unlink(missing_ok=True)
                return False
            return True
        except Exception as e:
            if attempt < 3:
                time.sleep(2 ** attempt)
            else:
                print(f"    [Failed after 3 attempts: {e}]")
    return False


def _detect_extension(path: Path) -> str:
    """
    Detect the real file type from magic bytes.
    Returns '.pdf', '.html', or '.bin' (unknown).
    """
    try:
        with open(path, "rb") as f:
            header = f.read(10)
        if header.startswith(b"%PDF"):
            return ".pdf"
        if header[:5] in (b"<html", b"<!DOC", b"<HTML") or b"<html" in header.lower():
            return ".html"
    except Exception:
        pass
    return ".bin"


# ══════════════════════════════════════════════════════════════════════
# FOLDER + SKIP CHECK
# ══════════════════════════════════════════════════════════════════════

def create_folders(base: str, company: str) -> tuple:
    safe = re.sub(r'[\\/:*?"<>|]', "", company).strip()
    cd   = Path(base) / safe
    cd.mkdir(parents=True, exist_ok=True)
    folders = {}
    for dtype, fname in TYPE_TO_FOLDER.items():
        f = cd / fname
        f.mkdir(exist_ok=True)
        folders[dtype] = f
    return folders, cd


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", "-c")
    parser.add_argument("--url",     "-u")
    parser.add_argument("--base",    "-b")
    args = parser.parse_args()

    print("\n" + "═"*62)
    print("  Screener Document Downloader  v2")
    print("═"*62 + "\n")

    raw = args.url or args.company
    if not raw:
        print("Enter company name or Screener URL.")
        raw = input("Company / URL: ").strip()
        if not raw:
            sys.exit(1)

    base = args.base
    if not base:
        print("\nBase folder path (e.g. C:\\Users\\Ram\\Research):")
        base = input("> ").strip()
        if not base:
            sys.exit(1)

    Path(base).mkdir(parents=True, exist_ok=True)

    # ── Resolve company ────────────────────────────────────────────────
    company_name, screener_url = resolve_company(raw)
    print(f"\nCompany : {company_name}")
    print(f"URL     : {screener_url}")
    print(f"Period  : last {YEARS_BACK} years  (cutoff: {CUTOFF_YEAR})\n")

    # ── Discovery ──────────────────────────────────────────────────────
    print("─"*62)
    print("STEP 1 — DISCOVERY")
    print("─"*62)

    print("\n[Screener] Fetching static page...")
    resp = requests.get(screener_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup     = BeautifulSoup(resp.text, "lxml")
    bse_code = extract_bse_code(resp.text)
    print(f"  BSE code: {bse_code or 'NOT FOUND'}")

    annual_docs = discover_annual_reports(soup)
    print(f"  Annual Reports  : {len(annual_docs)}")

    credit_docs = discover_credit_reports(soup)
    print(f"  Credit Reports  : {len(credit_docs)}")

    print("\n[Screener] Fetching concalls (Playwright)...")
    concall_docs = discover_concalls_playwright(screener_url)
    transcripts  = [d for d in concall_docs if d.document_type == TRANSCRIPT]
    ppts         = [d for d in concall_docs if d.document_type == INVESTOR_PRESENTATION]
    print(f"  Transcripts           : {len(transcripts)}")
    print(f"  Investor Presentations: {len(ppts)}")

    print("\n[BSE] Fetching quarterly results (category=Result, subcategory=Financial Results)...")
    quarterly_docs = discover_quarterly_results_bse(bse_code)
    print(f"  Quarterly Results: {len(quarterly_docs)}")

    all_docs = annual_docs + credit_docs + transcripts + ppts + quarterly_docs

    # ── Deduplication ──────────────────────────────────────────────────
    all_docs, dedup_warnings = deduplicate(all_docs)
    if dedup_warnings:
        print(f"\n  [{len(dedup_warnings)} duplicate(s) removed]")
        for w in dedup_warnings:
            print(f"    ⚠  {w}")

    # ── Skip check ─────────────────────────────────────────────────────
    folders, company_dir = create_folders(base, company_name)
    to_download, to_skip = [], []
    for doc in all_docs:
        if doc.document_type == CREDIT_REPORT:
            # Credit reports may have been saved as .pdf, .html, or .bin
            # depending on what the URL returned. Check by stem, not exact name.
            stem   = Path(doc.filename).stem   # e.g. "2025-04 Credit Rating"
            folder = folders[CREDIT_REPORT]
            already = any(
                f.stem == stem
                for f in folder.iterdir()
                if f.is_file() and not f.name.startswith("_")
            )
            if already:
                to_skip.append(doc)
            else:
                to_download.append(doc)
        else:
            existing = folders[doc.document_type] / doc.filename
            if existing.exists():
                to_skip.append(doc)
            else:
                to_download.append(doc)

    # ── Summary table ──────────────────────────────────────────────────
    print("\n" + "─"*62)
    print("STEP 2 — SUMMARY")
    print("─"*62)

    for dtype, label in [
        (ANNUAL_REPORT,         "Annual Reports"),
        (CREDIT_REPORT,         "Credit Reports"),
        (QUARTERLY_RESULT,      "Quarterly Results"),
        (INVESTOR_PRESENTATION, "Investor Presentations"),
        (TRANSCRIPT,            "Transcripts"),
    ]:
        new   = sum(1 for d in to_download if d.document_type == dtype)
        exist = sum(1 for d in to_skip    if d.document_type == dtype)
        print(f"  {label:<28}: {new:>3} new   {exist:>3} exist")

    # ── Preview ────────────────────────────────────────────────────────
    if to_download:
        print(f"\n  Files to download ({len(to_download)}):")
        current_type = None
        for doc in to_download:
            if doc.document_type != current_type:
                current_type = doc.document_type
                print(f"\n    {TYPE_TO_FOLDER[current_type]}")
            print(f"      {doc.filename}")
            print(f"        {doc.url[:72]}")

    if to_skip:
        print(f"\n  Already exist ({len(to_skip)}) — will skip.")

    if not to_download:
        print("\nNothing to download.")
        sys.exit(0)

    print(f"\n{'─'*62}")
    confirm = input(f"Download {len(to_download)} file(s)? [y/N]: ").strip().lower()
    if confirm not in ("y","yes"):
        print("Cancelled.")
        sys.exit(0)

    # ── Download ───────────────────────────────────────────────────────
    print("\n" + "─"*62)
    print("STEP 3 — DOWNLOADING")
    print("─"*62 + "\n")

    session = requests.Session()
    session.headers.update(HEADERS)
    downloaded, failed, invalid_pdfs = 0, [], []
    ppt_temp_files = []   # [(doc, temp_path)] for investor presentations

    for i, doc in enumerate(to_download):
        folder = folders[doc.document_type]

        if doc.document_type == INVESTOR_PRESENTATION:
            # Unique temp name — hash dedup decides what to keep after loop
            tmp_dest = folder / f"_ppt_{i:04d}.pdf"

        elif doc.document_type == CREDIT_REPORT:
            # Fix 5: Credit reports — save whatever Screener links to, no scraping
            dest     = folder / doc.filename
            tmp_dest = folder / "_tmp_credit_.bin"
            tmp_dest.unlink(missing_ok=True)
            print(f"  [{TYPE_TO_FOLDER[CREDIT_REPORT]}] {doc.filename}")
            ok = _download_credit_report(doc.url, tmp_dest, session)
            if ok:
                # Determine extension from what was actually saved
                ext = _detect_extension(tmp_dest)
                final_name = doc.filename.replace(".pdf", ext)
                final_dest = folder / final_name
                tmp_dest.rename(final_dest)
                print(f"  ✅ Saved as: {final_name}\n")
                downloaded += 1
            else:
                tmp_dest.unlink(missing_ok=True)
                print(f"  ❌ Failed\n")
                failed.append(doc.filename)
            continue   # skip the generic flow below

        else:
            dest     = folder / doc.filename
            tmp_dest = folder / "_tmp_.pdf"

        tmp_dest.unlink(missing_ok=True)

        print(f"  [{TYPE_TO_FOLDER[doc.document_type]}] {doc.filename}")
        ok = download_file(doc.url, tmp_dest, session)

        if ok:
            valid, reason = validate_pdf(tmp_dest)
            if valid:
                if doc.document_type == INVESTOR_PRESENTATION:
                    ppt_temp_files.append((doc, tmp_dest))
                    print(f"  ⏳ Downloaded ({reason}) — pending hash check\n")
                else:
                    tmp_dest.rename(dest)
                    print(f"  ✅ Saved  ({reason})\n")
                    downloaded += 1
            else:
                tmp_dest.unlink(missing_ok=True)
                print(f"  ⚠️  Invalid PDF — {reason}\n")
                invalid_pdfs.append((doc.filename, reason))
        else:
            tmp_dest.unlink(missing_ok=True)
            print(f"  ❌ Failed\n")
            failed.append(doc.filename)

    # ── Investor presentations: hash dedup then save ───────────────────
    ppt_saved = 0
    hash_dupes = 0
    if ppt_temp_files:
        print(f"\n{'─'*62}")
        print(f"Hash-deduplicating {len(ppt_temp_files)} investor presentation(s)...")
        ppt_saved, hash_dupes = finalize_presentations(
            ppt_temp_files, folders[INVESTOR_PRESENTATION]
        )
        downloaded += ppt_saved
        print()

    # ── Credit reports successfully downloaded ─────────────────────────
    # Check by stem since extension may be .pdf, .html, or .bin
    credit_folder = folders[CREDIT_REPORT]
    credit_downloaded = sum(
        1 for d in to_download
        if d.document_type == CREDIT_REPORT
        and any(
            f.stem == Path(d.filename).stem
            for f in credit_folder.iterdir()
            if f.is_file() and not f.name.startswith("_")
        )
    )

    # ── Final summary ──────────────────────────────────────────────────
    print("═"*62)
    print(f"  Downloaded             : {downloaded}")
    print(f"  Skipped (exist)        : {len(to_skip)}")
    print(f"  Failed                 : {len(failed)}")
    print(f"  Invalid PDFs rejected  : {len(invalid_pdfs)}")
    print(f"  Hash duplicates removed: {hash_dupes}")
    print(f"  Credit reports saved   : {credit_downloaded}")
    if failed:
        print("\n  Failed downloads:")
        for f in failed:
            print(f"    • {f}")
    if invalid_pdfs:
        print("\n  Invalid PDFs (rejected):")
        for fname, reason in invalid_pdfs:
            print(f"    • {fname}  ({reason})")
    print(f"\n  Saved to: {company_dir}")
    print("═"*62 + "\n")


if __name__ == "__main__":
    main()
