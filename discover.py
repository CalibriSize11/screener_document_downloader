"""
discover.py — Document discovery only. No downloading.

Sources:
  1. Screener #documents section → Annual Reports, Credit Reports, Concall PPTs
  2. BSE Announcements API      → Quarterly Result filings (last 7 years)

Run:
  python discover.py
  python discover.py --url https://www.screener.in/company/ACE/consolidated/
  python discover.py --company ACE
"""

import argparse
import re
import sys
import tempfile
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

# ── Constants ──────────────────────────────────────────────────────────────────
SCREENER_BASE      = "https://www.screener.in"
SCREENER_SEARCH    = "https://www.screener.in/api/company/search/"
BSE_ATTACH_HIS     = "https://www.bseindia.com/xml-data/corpfiling/AttachHis/{}"
BSE_ATTACH_LIVE    = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/{}"
YEARS_BACK         = 2   # Set to 2 for validation; increase to 7 once confirmed working
HEADERS            = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# BSE SUBCATNAME values that indicate an actual financial result filing
RESULT_SUBCATS = {
    "financial results",
    "quarterly results",
    "half yearly results",
    "annual results",
    "standalone financial results",
    "consolidated financial results",
}

# Keywords in NEWSSUB that also indicate result filings
RESULT_SUBJ_KEYWORDS = [
    "financial results",
    "quarterly results",
    "unaudited results",
    "audited results",
    "results for the quarter",
    "results for quarter",
    "q1", "q2", "q3", "q4",
    "half year",
    "half-year",
]

# Subjects that indicate a notice/intimation (not the result PDF itself)
NOTICE_SUBJ_KEYWORDS = [
    "board meeting intimation",
    "intimation of board",
    "prior intimation",
    "notice of board",
    "outcome of board",
    "outcome of the board",
    "schedule.*board",
    "board.*scheduled",
]


# ── Helpers ────────────────────────────────────────────────────────────────────
def separator(title="", width=62):
    if title:
        print(f"\n{'─'*4} {title} {'─'*(width - len(title) - 6)}")
    else:
        print("─" * width)


def build_bse_url(attachment_name: str, date_str: str) -> str:
    """
    Construct full BSE PDF URL from ATTACHMENTNAME and NEWS_DT.
    Recent filings (< 60 days) → AttachLive, older → AttachHis.
    """
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        if dt > datetime.now() - timedelta(days=60):
            return BSE_ATTACH_LIVE.format(attachment_name)
    except Exception:
        pass
    return BSE_ATTACH_HIS.format(attachment_name)


def is_result_filing(newssub: str, subcat: str) -> bool:
    """Return True if announcement is an actual result filing, not a notice."""
    text = newssub.lower()

    # Reject notices
    for kw in NOTICE_SUBJ_KEYWORDS:
        if re.search(kw, text):
            return False

    # Accept by subcategory
    if subcat.lower() in RESULT_SUBCATS:
        return True

    # Accept by subject keywords
    for kw in RESULT_SUBJ_KEYWORDS:
        if kw in text:
            return True

    return False


# ── Company resolution ─────────────────────────────────────────────────────────
def resolve_company(raw: str) -> tuple[str, str, str]:
    """
    Returns (company_name, screener_url, bse_code).
    Accepts company name or direct Screener URL.
    """
    if "screener.in/company/" in raw:
        url = raw if raw.startswith("http") else "https://" + raw
        parts = url.rstrip("/").split("/")
        name = parts[-1] if parts[-1] not in ("", "consolidated", "standalone") else parts[-2]
        return name.upper(), url, ""

    # Search
    resp = requests.get(SCREENER_SEARCH, params={"q": raw, "v": "3"},
                        headers=HEADERS, timeout=15)
    resp.raise_for_status()
    results = resp.json()

    if not results:
        print(f"No companies found for '{raw}'")
        sys.exit(1)

    if len(results) == 1:
        r = results[0]
        return r["name"], SCREENER_BASE + r["url"], ""

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
                return r["name"], SCREENER_BASE + r["url"], ""
        except ValueError:
            pass


# ── Screener scraping ──────────────────────────────────────────────────────────
def scrape_screener(url: str) -> tuple[list, list, list, str]:
    """
    Scrape Screener #documents section.
    Returns (annual_reports, credit_reports, concall_ppts, bse_code).
    """
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    html = resp.text
    soup = BeautifulSoup(html, "lxml")

    # Extract BSE scrip code from page HTML
    bse_code = ""
    for pat in [
        re.compile(r'BSE[:/\s]+(\d{6})', re.IGNORECASE),
        re.compile(r'bseindia\.com[^"\']*?/(\d{6})[/"\']'),
    ]:
        m = pat.search(html)
        if m and len(m.group(1)) == 6:
            bse_code = m.group(1)
            break

    doc_section = soup.find(id="documents")
    if not doc_section:
        print("ERROR: Could not find #documents section. Screener may require login.")
        return [], [], [], bse_code

    annual_reports  = []
    credit_reports  = []
    concall_ppts    = []

    MONTH_RE = re.compile(
        r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4}\b',
        re.IGNORECASE
    )

    cutoff_year = datetime.now().year - YEARS_BACK

    for heading in doc_section.find_all(["h2", "h3"]):
        htxt = heading.get_text(strip=True).lower()

        # Collect sibling content until next heading
        siblings = []
        for sib in heading.find_next_siblings():
            if sib.name in ["h2", "h3"]:
                break
            siblings.append(sib)

        # Build a local soup from siblings
        from bs4 import BeautifulSoup as BS
        local_html = "".join(str(s) for s in siblings)
        local = BS(local_html, "lxml")

        # ── Annual Reports ─────────────────────────────────────────────────────
        if "annual report" in htxt:
            for li in local.find_all("li"):
                a = li.find("a", href=True)
                if not a:
                    continue
                href  = a["href"]
                if not href.startswith("http"):
                    href = SCREENER_BASE + href
                title = li.get_text(" ", strip=True)

                # Filter by year
                yr_match = re.search(r'\b(20\d{2})\b', title)
                if yr_match and int(yr_match.group(1)) < cutoff_year:
                    continue

                annual_reports.append({
                    "title": title,
                    "url":   href,
                })

        # ── Credit Ratings ─────────────────────────────────────────────────────
        elif "credit" in htxt or "rating" in htxt:
            for li in local.find_all("li"):
                a = li.find("a", href=True)
                if not a:
                    continue
                href  = a["href"]
                if not href.startswith("http"):
                    href = SCREENER_BASE + href
                title = li.get_text(" ", strip=True)

                # Filter by year
                yr_match = re.search(r'\b(20\d{2})\b', title)
                if yr_match and int(yr_match.group(1)) < cutoff_year:
                    continue

                credit_reports.append({
                    "title": title,
                    "url":   href,
                })

        # ── Concalls (PPTs = Investor Presentations) ───────────────────────────
        elif "concall" in htxt or "conference" in htxt:
            print(f"\n  [DEBUG] Found concalls section. Scanning items...")
            all_items = local.find_all(["li", "div"])
            print(f"  [DEBUG] Total <li>/<div> elements inside concalls: {len(all_items)}")

            for idx, item in enumerate(all_items):
                text = item.get_text(" ", strip=True)
                if not text:
                    continue

                date_m = MONTH_RE.search(text)

                # Print every item with its links for debugging
                all_links = item.find_all("a", href=True)
                if all_links:
                    print(f"\n  [DEBUG] Item {idx}: date_found={bool(date_m)} | "
                          f"text_preview='{text[:60]}'")
                    for a in all_links:
                        print(f"    link_text='{a.get_text(strip=True)}' | "
                              f"href='{a['href'][:80]}'")

                if not date_m:
                    continue
                date_label = date_m.group(0)

                # Filter by year
                yr_match = re.search(r'\b(20\d{2})\b', date_label)
                if yr_match and int(yr_match.group(1)) < cutoff_year:
                    continue

                for a in all_links:
                    link_text = a.get_text(strip=True).lower()
                    if "ppt" in link_text or "presentation" in link_text:
                        href = a["href"]
                        if not href.startswith("http"):
                            href = SCREENER_BASE + href
                        if not any(p["url"] == href for p in concall_ppts):
                            concall_ppts.append({
                                "date":  date_label,
                                "title": f"{date_label} Investor Presentation",
                                "url":   href,
                            })

    return annual_reports, credit_reports, concall_ppts, bse_code


# ── BSE quarterly results ──────────────────────────────────────────────────────
def fetch_bse_results(bse_code: str) -> list:
    """
    Fetch quarterly result filings from BSE for the last 7 years.
    Uses real field names: NEWSID, NEWSSUB, ATTACHMENTNAME,
                           CATEGORYNAME, SUBCATNAME, NEWS_DT
    """
    if not bse_code:
        print("  No BSE code found — skipping BSE results fetch.")
        return []

    try:
        from bse import BSE
    except ImportError:
        print("  'bse' library not installed. Run: pip install bse")
        return []

    import time

    from_date = datetime.now() - timedelta(days=365 * YEARS_BACK)
    to_date   = datetime.now()

    results      = []
    seen_subcats = set()
    MAX_RETRIES  = 3
    RETRY_DELAY  = 2  # seconds

    def fetch_page_with_retry(bse, page_no):
        """Fetch one page of announcements, retrying on timeout."""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                return bse.announcements(
                    scripcode = bse_code,
                    from_date = from_date,
                    to_date   = to_date,
                    page_no   = page_no,
                )
            except TimeoutError:
                if attempt < MAX_RETRIES:
                    print(f"  WARNING: BSE request timed out (attempt {attempt}/{MAX_RETRIES}). "
                          f"Retrying in {RETRY_DELAY}s...")
                    time.sleep(RETRY_DELAY)
                else:
                    print(f"  WARNING: BSE timed out after {MAX_RETRIES} attempts on page {page_no}. "
                          f"Returning results collected so far.")
                    return None
            except Exception as e:
                print(f"  WARNING: BSE API error on page {page_no}: {e}")
                return None

    try:
        with tempfile.TemporaryDirectory() as tmp:
            with BSE(download_folder=tmp) as bse:
                page_no  = 1
                per_page = 20
                total    = None

                while True:
                    print(f"  Fetching page {page_no}...", end=" ", flush=True)
                    data = fetch_page_with_retry(bse, page_no)

                    if data is None:
                        # Timeout exhausted — stop pagination gracefully
                        break

                    table  = data.get("Table",  [])
                    table1 = data.get("Table1", [])

                    if total is None and table1:
                        total = table1[0].get("ROWCNT", 0)
                        print(f"({total} total announcements)")
                    else:
                        print(f"({len(table)} items)")

                    if not table:
                        break

                    for item in table:
                        newssub    = item.get("NEWSSUB",        "")
                        attachment = item.get("ATTACHMENTNAME", "")
                        category   = item.get("CATEGORYNAME",   "")
                        subcat     = item.get("SUBCATNAME",     "")
                        news_dt    = item.get("NEWS_DT",        "")

                        seen_subcats.add(subcat)

                        if not attachment:
                            continue

                        if not is_result_filing(newssub, subcat):
                            continue

                        pdf_url = build_bse_url(attachment, news_dt)

                        results.append({
                            "NEWS_DT":        news_dt[:10] if news_dt else "",
                            "NEWSSUB":        newssub,
                            "CATEGORYNAME":   category,
                            "SUBCATNAME":     subcat,
                            "ATTACHMENTNAME": attachment,
                            "pdf_url":        pdf_url,
                        })

                    if total and page_no * per_page >= total:
                        break
                    page_no += 1

    except Exception as e:
        print(f"\n  WARNING: Unexpected BSE error: {e}. Returning results collected so far.")

    print(f"  All SUBCATNAME values seen: {sorted(seen_subcats)}")
    return results


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Screener document discovery")
    parser.add_argument("--company", "-c", help="Company name")
    parser.add_argument("--url",     "-u", help="Direct Screener URL")
    args = parser.parse_args()

    raw = args.url or args.company
    if not raw:
        raw = input("Enter company name or Screener URL: ").strip()

    company_name, screener_url, _ = resolve_company(raw)

    print(f"\n{'═'*62}")
    print(f"  DOCUMENT DISCOVERY: {company_name}")
    print(f"  URL: {screener_url}")
    print(f"  Period: last {YEARS_BACK} years")
    print(f"{'═'*62}")

    # ── Screener ───────────────────────────────────────────────────────────────
    print("\nFetching Screener documents...")
    annual, credit, ppts, bse_code = scrape_screener(screener_url)

    print(f"  BSE Code found: {bse_code or 'NOT FOUND'}")

    separator("Annual Reports")
    for r in annual:
        print(f"  {r['title']}")
        print(f"    → {r['url']}")

    separator("Credit Reports")
    for r in credit:
        print(f"  {r['title']}")
        print(f"    → {r['url']}")

    separator("Investor Presentations (Concall PPTs)")
    for r in ppts:
        print(f"  {r['date']} — {r['url']}")

    # ── BSE ────────────────────────────────────────────────────────────────────
    separator("Quarterly Results (from BSE)")
    print(f"  Fetching BSE announcements for scrip {bse_code}...")
    bse_results = fetch_bse_results(bse_code)

    for r in bse_results:
        print(f"  {r['NEWS_DT']}  |  {r['SUBCATNAME']}")
        print(f"    NEWSSUB:    {r['NEWSSUB'][:80]}")
        print(f"    ATTACHMENT: {r['ATTACHMENTNAME']}")
        print(f"    URL:        {r['pdf_url']}")

    # ── Summary ────────────────────────────────────────────────────────────────
    separator()
    print(f"\n  SUMMARY")
    print(f"  {'Annual Reports found:':<35} {len(annual)}")
    print(f"  {'Credit Reports found:':<35} {len(credit)}")
    print(f"  {'Investor Presentations found:':<35} {len(ppts)}")
    print(f"  {'Quarterly Result filings found:':<35} {len(bse_results)}")
    separator()


if __name__ == "__main__":
    main()
