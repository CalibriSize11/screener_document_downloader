"""
extract_concalls.py — Extract Transcript and PPT links from Screener's
                       Concalls section using confirmed HTML structure.

Confirmed structure from documents_section.html:
  - Each row:  <li class="flex flex-gap-8 flex-wrap-420">
  - Date:      <div class="... nowrap" style="width: 74px">May 2026</div>
  - Transcript: <a class="concall-link" title="Raw Transcript" href="...">Transcript</a>
                OR <div class="concall-link">Transcript</div>  ← no file, skip
  - PPT:        <a href="..." class="concall-link">PPT</a>
                OR <div class="concall-link">PPT</div>         ← no file, skip
  - Skip:      AI Summary, REC (audio/video)

Run:
    python extract_concalls.py
    python extract_concalls.py --url https://www.screener.in/company/INFY/consolidated/
"""

import argparse
import re
import sys
from pathlib import Path

SCREENER_BASE = "https://www.screener.in"
DEFAULT_URL   = "https://www.screener.in/company/ACE/consolidated/"
YEARS_BACK    = 7


def get_rendered_concalls_html(url: str) -> str:
    """
    Use Playwright to fetch the fully rendered #documents section HTML.
    Returns the inner HTML string of the concalls <div>.
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page    = browser.new_page()
        page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/124.0.0.0 Safari/537.36"
        })

        print(f"  Opening: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        # Wait for #documents to appear
        try:
            page.wait_for_selector("#documents", timeout=15_000)
        except PWTimeout:
            print("  ERROR: #documents did not appear.")
            browser.close()
            return ""

        # Scroll to #documents to trigger lazy rendering
        page.evaluate("document.querySelector('#documents').scrollIntoView()")
        page.wait_for_timeout(2000)

        # Wait specifically for the concalls list items
        try:
            page.wait_for_selector(
                "div.documents.concalls li.flex",
                timeout=10_000
            )
        except PWTimeout:
            print("  WARNING: Concalls <li> items did not appear within 10s.")

        # Get the concalls div HTML
        try:
            html = page.inner_html("div.documents.concalls")
        except Exception:
            # Fallback: get full #documents HTML
            html = page.inner_html("#documents")

        browser.close()
        return html


def parse_concalls_html(html: str, years_back: int = YEARS_BACK) -> list[dict]:
    """
    Parse the concalls section HTML using the confirmed structure.
    Returns list of dicts: {date, transcript_url, ppt_url}

    Only includes rows where at least one of transcript_url or ppt_url
    is present (i.e. skips rows where both are <div> placeholders).
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    cutoff_year = 2026 - years_back  # e.g. 2026 - 7 = 2019

    records = []

    # Each concall row is <li class="flex flex-gap-8 flex-wrap-420">
    rows = soup.find_all("li", class_=lambda c: c and "flex" in c and "flex-gap-8" in c)

    print(f"  Found {len(rows)} concall <li> rows")

    for row in rows:
        # ── Date ──────────────────────────────────────────────────────────────
        # <div class="... nowrap" style="width: 74px">May 2026</div>
        date_div = row.find("div", style=lambda s: s and "width: 74px" in s)
        if not date_div:
            # Fallback: find div with class containing "nowrap"
            date_div = row.find("div", class_=lambda c: c and "nowrap" in c)
        if not date_div:
            continue

        date_label = date_div.get_text(strip=True)  # e.g. "May 2026"

        # Filter by year
        yr_match = re.search(r'\b(20\d{2})\b', date_label)
        if yr_match and int(yr_match.group(1)) <= cutoff_year:
            continue

        # ── Links — only <a> tags are real links, <div> = no file ─────────────
        # All real links inside this row have class="concall-link" AND are <a> tags
        all_links = row.find_all("a", class_="concall-link", href=True)

        transcript_url = None
        ppt_url        = None

        for a in all_links:
            href      = a["href"]
            link_text = a.get_text(strip=True).lower()
            title     = (a.get("title") or "").lower()

            # Skip audio/video recordings
            if any(x in href.lower() for x in
                   [".mp3", ".mp4", "youtube.com", "drive.google.com",
                    "ccreservations.com"]):
                continue

            if link_text == "transcript" or "transcript" in title:
                transcript_url = href

            elif link_text == "ppt":
                ppt_url = href

        # Only record rows with at least one real link
        if transcript_url or ppt_url:
            records.append({
                "date":           date_label,
                "transcript_url": transcript_url or "",
                "ppt_url":        ppt_url or "",
            })

    return records


def extract_concalls(url: str, years_back: int = YEARS_BACK) -> list[dict]:
    """Full pipeline: render page → parse → return records."""
    print("\nFetching rendered concalls section via Playwright...")
    html = get_rendered_concalls_html(url)

    if not html:
        print("  ERROR: No HTML returned.")
        return []

    print(f"  HTML length: {len(html):,} chars")
    ppt_count        = html.lower().count(">ppt<")
    transcript_count = html.lower().count(">transcript<")
    print(f"  '>PPT<' occurrences in HTML        : {ppt_count}")
    print(f"  '>Transcript<' occurrences in HTML : {transcript_count}")

    print("\nParsing concall rows...")
    records = parse_concalls_html(html, years_back=years_back)
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url",        "-u", default=DEFAULT_URL)
    parser.add_argument("--years-back", "-y", type=int, default=YEARS_BACK)
    args = parser.parse_args()

    records = extract_concalls(args.url, years_back=args.years_back)

    # ── Print results ──────────────────────────────────────────────────────────
    print(f"\n{'='*62}")
    print(f"  CONCALL RECORDS EXTRACTED (last {args.years_back} years)")
    print(f"{'='*62}\n")

    for r in records:
        t = r["transcript_url"] or "— no file —"
        p = r["ppt_url"]        or "— no file —"
        print(f"  {r['date']}")
        print(f"    Transcript : {t}")
        print(f"    PPT        : {p}")

    # ── Summary ────────────────────────────────────────────────────────────────
    total       = len(records)
    has_t       = sum(1 for r in records if r["transcript_url"])
    has_ppt     = sum(1 for r in records if r["ppt_url"])
    has_both    = sum(1 for r in records if r["transcript_url"] and r["ppt_url"])

    print(f"\n{'─'*62}")
    print(f"  Total rows with at least one link : {total}")
    print(f"  Rows with Transcript URL          : {has_t}")
    print(f"  Rows with PPT URL                 : {has_ppt}")
    print(f"  Rows with both                    : {has_both}")
    print(f"{'─'*62}\n")

    if has_ppt < 39:
        print(f"  WARNING: Expected 39 PPT links, got {has_ppt}.")
        print("  Check year filter — try --years-back 10 to see all.")


if __name__ == "__main__":
    main()
