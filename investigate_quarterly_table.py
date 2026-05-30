"""
investigate_quarterly_table.py — Inspect the Raw PDF row of Screener's
quarterly results table.

For each quarter column, captures:
  - The exact href value from the Raw PDF cell
  - ALL attributes on the <a> tag (onclick, data-*, title, etc.)
  - The full redirect chain to the final URL
  - Whether the final URL is a PDF or a BSE announcements page

Run:
    python investigate_quarterly_table.py
    python investigate_quarterly_table.py --url https://www.screener.in/company/INFY/consolidated/
"""

import argparse
import json
import time
from pathlib import Path

import requests

SCREENER_URL = "https://www.screener.in/company/ACE/consolidated/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def follow_redirects(url: str) -> dict:
    """
    Follow redirect chain for a URL using requests.
    Returns dict with chain, final_url, final_content_type.
    """
    chain = []
    try:
        resp = requests.get(url, headers=HEADERS, allow_redirects=True,
                            timeout=15, stream=True)
        # Build chain from response history
        for r in resp.history:
            chain.append({
                "status": r.status_code,
                "url":    r.url,
                "location": r.headers.get("location", ""),
            })
        chain.append({
            "status": resp.status_code,
            "url":    resp.url,
            "location": "(final)",
        })
        ct = resp.headers.get("content-type", "")
        return {
            "chain":        chain,
            "final_url":    resp.url,
            "final_status": resp.status_code,
            "content_type": ct,
            "is_pdf":       "pdf" in ct.lower() or resp.url.lower().endswith(".pdf"),
        }
    except Exception as e:
        return {"chain": chain, "error": str(e), "final_url": url, "is_pdf": False}


def get_quarterly_table_html(screener_url: str) -> str:
    """Use Playwright to get the fully rendered #quarters section HTML."""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    print(f"Opening: {screener_url}")
    html = ""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page    = browser.new_page()
        page.set_extra_http_headers(HEADERS)
        page.goto(screener_url, wait_until="domcontentloaded", timeout=30_000)

        try:
            page.wait_for_selector("#quarters", timeout=10_000)
            page.evaluate("document.querySelector('#quarters').scrollIntoView()")
            page.wait_for_timeout(2000)
            html = page.inner_html("#quarters")
        except PWTimeout:
            print("  ERROR: #quarters not found.")

        browser.close()
    return html


def extract_raw_pdf_row(html: str) -> list[dict]:
    """
    Parse the #quarters HTML and extract every cell from the Raw PDF row.
    For each cell, captures ALL attributes of the <a> tag, not just href.
    """
    from bs4 import BeautifulSoup

    soup  = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if not table:
        print("ERROR: No <table> found in #quarters HTML.")
        return []

    # Extract column headers
    headers = []
    thead   = table.find("thead")
    if thead:
        for cell in thead.find_all(["th", "td"]):
            headers.append(cell.get_text(strip=True))
    else:
        first_row = table.find("tr")
        if first_row:
            for cell in first_row.find_all(["th", "td"]):
                headers.append(cell.get_text(strip=True))

    print(f"\nTable headers ({len(headers)} columns):")
    for i, h in enumerate(headers):
        print(f"  [{i}] {h!r}")

    # Find Raw PDF row
    results = []
    for row in table.find_all("tr"):
        cells     = row.find_all(["td", "th"])
        if not cells:
            continue
        row_label = cells[0].get_text(strip=True)
        if "raw pdf" not in row_label.lower():
            continue

        print(f"\nFound Raw PDF row. Cells: {len(cells)}")

        for col_idx, cell in enumerate(cells):
            if col_idx == 0:
                continue  # skip label cell
            if col_idx >= len(headers):
                break

            quarter = headers[col_idx] if col_idx < len(headers) else f"col_{col_idx}"
            cell_html = str(cell)

            # Get ALL <a> tags in this cell
            a_tags = cell.find_all("a")

            if not a_tags:
                # No link — check if there's any other element
                inner = cell.get_text(strip=True)
                results.append({
                    "quarter":   quarter,
                    "col_idx":   col_idx,
                    "has_link":  False,
                    "cell_text": inner,
                    "cell_html": cell_html,
                })
                continue

            for a in a_tags:
                # Capture EVERY attribute
                attrs = dict(a.attrs)
                href  = attrs.get("href", "")

                results.append({
                    "quarter":   quarter,
                    "col_idx":   col_idx,
                    "has_link":  True,
                    "href":      href,
                    "all_attrs": attrs,
                    "link_text": a.get_text(strip=True),
                    "cell_html": cell_html,
                })

        break  # Only need the Raw PDF row

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", "-u", default=SCREENER_URL)
    parser.add_argument("--no-follow", action="store_true",
                        help="Skip redirect following (just show hrefs)")
    args = parser.parse_args()

    # ── Step 1: Get table HTML ─────────────────────────────────────────
    print("\n" + "="*72)
    print("  STEP 1: FETCH QUARTERLY TABLE")
    print("="*72)
    html = get_quarterly_table_html(args.url)
    if not html:
        print("Failed to get quarterly table HTML.")
        return

    # Save for manual inspection
    Path("quarterly_table.html").write_text(html, encoding="utf-8")
    print(f"\nRaw table HTML saved to: quarterly_table.html ({len(html):,} chars)")
    ppt_count = html.lower().count("raw pdf")
    print(f"'raw pdf' appears {ppt_count} times in table HTML")

    # ── Step 2: Extract Raw PDF row ────────────────────────────────────
    print("\n" + "="*72)
    print("  STEP 2: EXTRACT RAW PDF ROW")
    print("="*72)
    cells = extract_raw_pdf_row(html)

    if not cells:
        print("No Raw PDF row found.")
        return

    has_links    = [c for c in cells if c.get("has_link")]
    no_links     = [c for c in cells if not c.get("has_link")]
    print(f"\nCells with links   : {len(has_links)}")
    print(f"Cells without links: {len(no_links)}")

    # ── Step 3: Print all attributes per link ─────────────────────────
    print("\n" + "="*72)
    print("  STEP 3: ALL LINK ATTRIBUTES PER QUARTER")
    print("="*72)

    for cell in cells:
        q = cell["quarter"]
        if not cell.get("has_link"):
            print(f"\n  {q:<14} — no link  (text: {cell.get('cell_text','')!r})")
            continue

        href      = cell.get("href", "")
        all_attrs = cell.get("all_attrs", {})
        link_text = cell.get("link_text", "")

        print(f"\n  Quarter: {q}")
        print(f"  Link text: {link_text!r}")
        print(f"  href: {href!r}")

        # Print every non-href, non-class attribute (often contains metadata)
        for k, v in all_attrs.items():
            if k in ("href",):
                continue
            print(f"  {k}: {v!r}")

        # Print the raw cell HTML for full context
        print(f"  Cell HTML snippet: {cell['cell_html'][:300]}")

    # ── Step 4: Follow redirects for each href ─────────────────────────
    if not args.no_follow and has_links:
        print("\n" + "="*72)
        print("  STEP 4: REDIRECT CHAINS")
        print("="*72)

        redirect_results = []
        for cell in has_links:
            q    = cell["quarter"]
            href = cell.get("href", "")

            if not href:
                print(f"\n  {q:<14} — href is empty")
                continue

            # Make absolute if relative
            if href.startswith("/"):
                href = "https://www.screener.in" + href
            elif not href.startswith("http"):
                print(f"\n  {q:<14} — unusual href: {href!r}")
                continue

            print(f"\n  {q:<14} → following: {href}")
            result = follow_redirects(href)

            # Print chain
            for step in result.get("chain", []):
                status = step.get("status", "?")
                url    = step.get("url", "")
                loc    = step.get("location", "")
                if loc and loc != "(final)":
                    print(f"    {status} {url[:65]}")
                    print(f"        → {loc[:65]}")
                else:
                    marker = "📄 PDF" if result.get("is_pdf") else "🌐 HTML"
                    print(f"    {status} {marker} {url[:65]}")

            print(f"    Content-Type: {result.get('content_type','unknown')}")
            print(f"    Is PDF: {result.get('is_pdf', False)}")

            redirect_results.append({
                "quarter":    q,
                "href":       cell.get("href"),
                "all_attrs":  cell.get("all_attrs", {}),
                "chain":      result.get("chain", []),
                "final_url":  result.get("final_url"),
                "is_pdf":     result.get("is_pdf", False),
                "content_type": result.get("content_type", ""),
            })

        # Save full results
        log_path = Path("quarterly_redirect_log.json")
        log_path.write_text(
            json.dumps(redirect_results, indent=2, default=str),
            encoding="utf-8"
        )
        print(f"\nFull redirect log saved to: {log_path}")

    # ── Summary ────────────────────────────────────────────────────────
    print("\n" + "="*72)
    print("  SUMMARY TABLE")
    print("="*72)
    print(f"\n  {'Quarter':<14} {'Has Link':<10} {'href (first 60)'}")
    print(f"  {'─'*14} {'─'*10} {'─'*60}")
    for cell in cells:
        q    = cell["quarter"]
        link = "YES" if cell.get("has_link") else "no"
        href = (cell.get("href") or "")[:60]
        print(f"  {q:<14} {link:<10} {href}")


if __name__ == "__main__":
    main()
