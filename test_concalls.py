"""
test_concalls.py — Verify Playwright can extract Transcript and PPT links
                   from Screener's dynamically rendered Concalls section.

Run:
    python test_concalls.py
    python test_concalls.py --url https://www.screener.in/company/INFY/consolidated/
"""

import argparse
import re
import sys

SCREENER_BASE = "https://www.screener.in"
DEFAULT_URL   = "https://www.screener.in/company/ACE/consolidated/"

MONTH_RE = re.compile(
    r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{4}\b',
    re.IGNORECASE
)


def extract_concalls(url: str) -> list[dict]:
    """
    Use Playwright to render the Screener page, wait for the Concalls
    section to appear, and extract all Transcript + PPT links.

    Returns list of dicts:
      { date, link_type, link_text, href }
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    records = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page    = browser.new_page()

        page.set_extra_http_headers({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        })

        print(f"Opening: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)

        # Step 1: Wait for the #documents section to exist in DOM
        print("Waiting for #documents section...")
        try:
            page.wait_for_selector("#documents", timeout=15_000)
        except PWTimeout:
            print("ERROR: #documents section did not appear within 15s.")
            browser.close()
            return []

        # Step 2: Scroll to #documents to trigger any lazy rendering
        print("Scrolling to #documents to trigger lazy load...")
        page.evaluate("document.querySelector('#documents').scrollIntoView()")
        page.wait_for_timeout(2000)  # give JS time to render after scroll

        # Step 3: Wait for the Concalls heading specifically
        print("Waiting for Concalls heading...")
        try:
            # Screener uses <h3> headings inside #documents
            page.wait_for_selector(
                "#documents h3",
                timeout=10_000
            )
        except PWTimeout:
            print("WARNING: Could not find any h3 inside #documents after 10s.")

        # Step 4: Dump raw HTML of #documents for inspection
        doc_html = page.inner_html("#documents")

        # Step 5: Parse with BeautifulSoup
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(doc_html, "lxml")

        # Find the Concalls heading
        concall_heading = None
        for h in soup.find_all(["h2", "h3", "h4"]):
            if "concall" in h.get_text(strip=True).lower():
                concall_heading = h
                break

        if not concall_heading:
            print("\nERROR: Could not find Concalls heading inside #documents.")
            print("Headings found in #documents:")
            for h in soup.find_all(["h2", "h3", "h4"]):
                print(f"  <{h.name}>: '{h.get_text(strip=True)}'")
            browser.close()
            return []

        print(f"\nFound concalls heading: <{concall_heading.name}> "
              f"'{concall_heading.get_text(strip=True)}'")

        # Step 6: Collect all siblings after the heading until next heading
        section_els = []
        for sib in concall_heading.find_next_siblings():
            if sib.name in ["h2", "h3", "h4"]:
                break
            section_els.append(sib)

        section_html = "".join(str(e) for e in section_els)
        section_soup = BeautifulSoup(section_html, "lxml")

        # Step 7: Count raw elements for debugging
        all_lis  = section_soup.find_all("li")
        all_divs = section_soup.find_all("div")
        all_as   = section_soup.find_all("a", href=True)

        print(f"\n[DEBUG] Elements inside concalls section after Playwright render:")
        print(f"  <li> count  : {len(all_lis)}")
        print(f"  <div> count : {len(all_divs)}")
        print(f"  <a> count   : {len(all_as)}")

        # Step 8: Print ALL links found (raw dump)
        print(f"\n[DEBUG] All <a> tags in concalls section:")
        for a in all_as:
            print(f"  text='{a.get_text(strip=True):<20}' | href='{a['href'][:80]}'")

        # Step 9: Extract structured records
        print(f"\n[EXTRACTION] Matching Transcript / PPT links with date labels:")
        for container in section_soup.find_all(["li", "div"]):
            text   = container.get_text(" ", strip=True)
            date_m = MONTH_RE.search(text)
            if not date_m:
                continue
            date_label = date_m.group(0)

            for a in container.find_all("a", href=True):
                href      = a["href"]
                link_text = a.get_text(strip=True)
                lt_lower  = link_text.lower()
                title_att = (a.get("title") or "").lower()

                if not href.startswith("http"):
                    href = SCREENER_BASE + href

                # Classify link
                if "transcript" in lt_lower or "transcript" in title_att:
                    link_type = "Transcript"
                elif "ppt" in lt_lower:
                    link_type = "PPT"
                else:
                    continue

                # Skip audio/video
                if any(x in href.lower() for x in [".mp3", ".mp4", "youtube", "drive.google"]):
                    continue

                record = {
                    "date":      date_label,
                    "link_type": link_type,
                    "link_text": link_text,
                    "href":      href,
                }
                records.append(record)
                print(f"  ✓ [{link_type:<12}] {date_label:<12} → {href[:75]}")

        browser.close()

    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", "-u", default=DEFAULT_URL)
    args = parser.parse_args()

    records = extract_concalls(args.url)

    print(f"\n{'='*62}")
    print(f"  RESULT: {len(records)} concall links extracted")
    transcripts = sum(1 for r in records if r["link_type"] == "Transcript")
    ppts        = sum(1 for r in records if r["link_type"] == "PPT")
    print(f"    Transcripts : {transcripts}")
    print(f"    PPTs        : {ppts}")
    print(f"{'='*62}\n")

    if not records:
        print("DIAGNOSIS: No records found. Check the [DEBUG] output above.")
        print("Likely causes:")
        print("  1. Date label not matching MONTH_RE pattern")
        print("  2. Link text is not 'Transcript' or 'PPT'")
        print("  3. Links are inside a container that isn't <li> or <div>")
        print("  4. Page still requires more wait time after scroll")


if __name__ == "__main__":
    main()
