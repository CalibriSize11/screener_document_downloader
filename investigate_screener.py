"""
investigate_screener.py — Find where Screener loads Concalls data from.

Launches a VISIBLE browser, intercepts all network requests,
saves the fully rendered HTML, and identifies any API calls.

Run:
    python investigate_screener.py
"""

import json
import time
from pathlib import Path

URL = "https://www.screener.in/company/ACE/consolidated/"

def main():
    from playwright.sync_api import sync_playwright

    # Capture all network requests
    api_calls   = []
    all_requests = []

    def on_request(request):
        url = request.url
        rt  = request.resource_type
        all_requests.append({"type": rt, "url": url})

        # Flag XHR and fetch calls specifically
        if rt in ("xhr", "fetch"):
            api_calls.append({
                "type":   rt,
                "method": request.method,
                "url":    url,
            })

    def on_response(response):
        # Flag JSON responses
        ct = response.headers.get("content-type", "")
        if "json" in ct and response.status == 200:
            url = response.url
            # Only flag if not a static asset
            if not any(x in url for x in [".js", ".css", "fonts", "analytics"]):
                try:
                    body = response.json()
                    # Save interesting JSON responses
                    api_calls_with_data.append({
                        "url":  url,
                        "data": body,
                    })
                except Exception:
                    pass

    api_calls_with_data = []

    with sync_playwright() as p:
        # ── Launch visible browser ─────────────────────────────────────────────
        print("Launching visible Chromium browser...")
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page    = context.new_page()

        # Attach listeners BEFORE navigation
        page.on("request",  on_request)
        page.on("response", on_response)

        # ── Navigate ───────────────────────────────────────────────────────────
        print(f"Opening: {URL}")
        page.goto(URL, wait_until="domcontentloaded", timeout=30_000)

        print("Waiting 10 seconds for full render and all API calls...")
        time.sleep(10)

        # ── Locator counts ─────────────────────────────────────────────────────
        ppt_count        = page.locator("text=PPT").count()
        transcript_count = page.locator("text=Transcript").count()
        print(f"\nLocator counts (visible in browser):")
        print(f"  text=PPT        : {ppt_count}")
        print(f"  text=Transcript : {transcript_count}")

        # ── Save rendered HTML ─────────────────────────────────────────────────
        html_path = Path("rendered_page.html")
        html_path.write_text(page.content(), encoding="utf-8")
        print(f"\nFull rendered HTML saved to: {html_path.resolve()}")
        print(f"  File size: {html_path.stat().st_size:,} bytes")

        # ── Network summary ────────────────────────────────────────────────────
        print(f"\n{'='*62}")
        print(f"  NETWORK REQUESTS SUMMARY")
        print(f"{'='*62}")

        # Count by type
        from collections import Counter
        type_counts = Counter(r["type"] for r in all_requests)
        print(f"\nAll request types:")
        for rt, count in sorted(type_counts.items()):
            print(f"  {rt:<20}: {count}")

        # Print all XHR / fetch calls
        print(f"\nXHR + Fetch calls ({len(api_calls)} total):")
        if not api_calls:
            print("  (none captured)")
        for r in api_calls:
            print(f"  [{r['type'].upper():<5}] {r['method']} {r['url']}")

        # Print JSON API responses
        print(f"\nJSON API responses ({len(api_calls_with_data)} total):")
        if not api_calls_with_data:
            print("  (none captured)")
        for r in api_calls_with_data:
            print(f"\n  URL: {r['url']}")
            # Print first 300 chars of JSON
            preview = json.dumps(r["data"])[:300]
            print(f"  DATA (preview): {preview}...")

        # Save full network log
        log_path = Path("network_log.json")
        log_path.write_text(
            json.dumps({
                "all_requests":        all_requests,
                "xhr_fetch":           api_calls,
                "json_responses":      [
                    {"url": r["url"], "data_preview": str(r["data"])[:500]}
                    for r in api_calls_with_data
                ],
            }, indent=2),
            encoding="utf-8"
        )
        print(f"\nFull network log saved to: {log_path.resolve()}")

        # ── Search rendered HTML for concall clues ────────────────────────────
        html = page.content()
        print(f"\n{'='*62}")
        print(f"  HTML SEARCH")
        print(f"{'='*62}")

        search_terms = ["PPT", "Transcript", "concall", "NEWSID",
                        "attachmentname", "AttachHis", "AttachLive"]
        for term in search_terms:
            count = html.lower().count(term.lower())
            print(f"  '{term}' appears {count} times in rendered HTML")

        # ── Look for concall section in DOM directly ───────────────────────────
        print(f"\n{'='*62}")
        print(f"  CONCALL SECTION DOM INSPECTION")
        print(f"{'='*62}")

        # Try multiple selectors
        selectors = [
            "#documents h3",
            "#concalls",
            "[id*='concall']",
            "[class*='concall']",
            "section:has(h3)",
        ]
        for sel in selectors:
            try:
                count = page.locator(sel).count()
                print(f"  '{sel}' → {count} elements found")
            except Exception as e:
                print(f"  '{sel}' → ERROR: {e}")

        # Print all h2/h3 headings inside #documents
        print(f"\n  All headings inside #documents:")
        try:
            headings = page.locator("#documents h2, #documents h3, #documents h4").all()
            for h in headings:
                print(f"    '{h.inner_text().strip()}'")
        except Exception as e:
            print(f"  ERROR reading headings: {e}")

        # Try to get inner HTML of #documents
        try:
            doc_inner = page.inner_html("#documents")
            doc_path  = Path("documents_section.html")
            doc_path.write_text(doc_inner, encoding="utf-8")
            print(f"\n  #documents inner HTML saved to: {doc_path.resolve()}")
            print(f"  Size: {doc_path.stat().st_size:,} bytes")
            # Check if PPT appears in documents section
            ppt_in_docs = doc_inner.lower().count("ppt")
            print(f"  'ppt' appears {ppt_in_docs} times in #documents HTML")
        except Exception as e:
            print(f"  ERROR getting #documents HTML: {e}")

        print("\nBrowser staying open for 5 seconds so you can inspect...")
        time.sleep(5)
        browser.close()

    print("\nDone. Files saved:")
    print("  rendered_page.html     — full page HTML after JS render")
    print("  documents_section.html — just the #documents section")
    print("  network_log.json       — all network requests captured")


if __name__ == "__main__":
    main()
