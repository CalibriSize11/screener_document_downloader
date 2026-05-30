"""
investigate_quarterly.py — Inspect how Screener exposes quarterly PDFs.

Opens a Screener quarter source URL with Playwright, logs every network
request, redirect, and PDF response, then saves the rendered HTML.

Run:
    python investigate_quarterly.py
    python investigate_quarterly.py --url "https://www.screener.in/company/source/quarter/1285734/9/2024/"
"""

import argparse
import json
import time
from pathlib import Path
from urllib.parse import urlparse

# Test URLs from failed downloads
TEST_URLS = [
    "https://www.screener.in/company/source/quarter/1285734/9/2024/",
    "https://www.screener.in/company/source/quarter/1285734/12/2024/",
    "https://www.screener.in/company/source/quarter/1285734/6/2025/",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def investigate(url: str):
    from playwright.sync_api import sync_playwright

    print(f"\n{'='*72}")
    print(f"  INVESTIGATING: {url}")
    print(f"{'='*72}\n")

    # ── Collected data ─────────────────────────────────────────────────
    all_requests  = []
    all_responses = []
    redirects     = []
    pdf_hits      = []

    def on_request(req):
        entry = {
            "type":     req.resource_type,
            "method":   req.method,
            "url":      req.url,
        }
        all_requests.append(entry)

        if req.is_navigation_request() and req.url != url:
            redirects.append({"from": url, "to": req.url})

        if "pdf" in req.url.lower():
            pdf_hits.append({"source": "request", "url": req.url})
            print(f"  🔗 PDF REQUEST: {req.url}")

    def on_response(resp):
        ct  = resp.headers.get("content-type", "")
        loc = resp.headers.get("location", "")
        entry = {
            "status":       resp.status,
            "url":          resp.url,
            "content_type": ct,
            "location":     loc,
        }
        all_responses.append(entry)

        if resp.status in (301, 302, 303, 307, 308):
            redirects.append({
                "status": resp.status,
                "from":   resp.url,
                "to":     loc,
            })
            print(f"  ↪  REDIRECT {resp.status}: {resp.url[:65]}")
            print(f"            → {loc[:65]}")

        if "pdf" in ct.lower() or "pdf" in resp.url.lower():
            pdf_hits.append({"source": "response", "url": resp.url, "ct": ct})
            print(f"  📄 PDF RESPONSE: {resp.url}")
            print(f"       content-type: {ct}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page    = context.new_page()
        page.set_extra_http_headers(HEADERS)

        page.on("request",  on_request)
        page.on("response", on_response)

        print(f"Navigating to URL...")
        try:
            page.goto(url, wait_until="networkidle", timeout=25_000)
        except Exception as e:
            print(f"  WARNING: goto raised: {e}")

        page.wait_for_timeout(3000)
        final_url = page.url
        print(f"\nFinal URL after load: {final_url}")

        # ── Rendered HTML ──────────────────────────────────────────────
        html       = page.content()
        html_path  = Path("quarterly_page.html")
        html_path.write_text(html, encoding="utf-8")
        print(f"\nRendered HTML saved to: {html_path} ({len(html):,} chars)")

        # ── Page title ─────────────────────────────────────────────────
        print(f"Page title: {page.title()!r}")

        # ── All links ──────────────────────────────────────────────────
        print(f"\n{'─'*60}")
        print("ALL LINKS ON PAGE")
        print(f"{'─'*60}")
        links = page.locator("a[href]").all()
        print(f"Total <a href> count: {len(links)}")
        for a in links[:50]:
            try:
                href = a.get_attribute("href") or ""
                text = a.inner_text().strip()[:50]
                print(f"  [{text:<30}] → {href[:70]}")
            except Exception:
                pass

        # ── Iframes ────────────────────────────────────────────────────
        print(f"\n{'─'*60}")
        print("IFRAMES")
        print(f"{'─'*60}")
        iframes = page.locator("iframe").all()
        print(f"Total <iframe> count: {len(iframes)}")
        for iframe in iframes:
            try:
                src = iframe.get_attribute("src") or "(no src)"
                print(f"  src: {src}")
            except Exception:
                pass

        # ── Embeds ─────────────────────────────────────────────────────
        print(f"\n{'─'*60}")
        print("EMBED / OBJECT ELEMENTS")
        print(f"{'─'*60}")
        for tag in ["embed", "object"]:
            els = page.locator(tag).all()
            print(f"<{tag}> count: {len(els)}")
            for el in els:
                try:
                    src  = el.get_attribute("src")  or ""
                    data = el.get_attribute("data") or ""
                    typ  = el.get_attribute("type") or ""
                    print(f"  src={src!r}  data={data!r}  type={typ!r}")
                except Exception:
                    pass

        # ── Page text preview ──────────────────────────────────────────
        print(f"\n{'─'*60}")
        print("PAGE TEXT (first 600 chars)")
        print(f"{'─'*60}")
        try:
            body_text = page.locator("body").inner_text()
            print(body_text[:600])
        except Exception as e:
            print(f"  (error reading body text: {e})")

        browser.close()

    # ── Network summary ────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("NETWORK SUMMARY")
    print(f"{'─'*60}")

    from collections import Counter
    type_counts = Counter(r["type"] for r in all_requests)
    print(f"\nRequest types:")
    for rt, n in sorted(type_counts.items()):
        print(f"  {rt:<25}: {n}")

    print(f"\nAll XHR/fetch requests ({sum(1 for r in all_requests if r['type'] in ('xhr','fetch'))}):")
    for r in all_requests:
        if r["type"] in ("xhr", "fetch"):
            print(f"  [{r['method']}] {r['url']}")

    print(f"\nAll document navigations:")
    for r in all_requests:
        if r["type"] == "document":
            print(f"  {r['url']}")

    print(f"\nRedirects captured: {len(redirects)}")
    for rd in redirects:
        if "status" in rd:
            print(f"  {rd['status']}: {rd['from'][:60]} → {rd['to'][:60]}")
        else:
            print(f"  nav: {rd['from'][:60]} → {rd['to'][:60]}")

    print(f"\nPDF hits: {len(pdf_hits)}")
    for h in pdf_hits:
        print(f"  [{h['source']}] {h['url']}")

    # ── Save full log ──────────────────────────────────────────────────
    log = {
        "url":          url,
        "final_url":    final_url,
        "redirects":    redirects,
        "pdf_hits":     pdf_hits,
        "xhr_fetch":    [r for r in all_requests if r["type"] in ("xhr","fetch")],
        "documents":    [r for r in all_requests if r["type"] == "document"],
        "all_responses": all_responses[:100],
    }
    log_path = Path("quarterly_network_log.json")
    log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")
    print(f"\nFull network log saved to: {log_path}")

    # ── Verdict ────────────────────────────────────────────────────────
    print(f"\n{'='*72}")
    print("  VERDICT")
    print(f"{'='*72}")

    if pdf_hits:
        print(f"  ✓ PDF URL(s) found directly:")
        for h in pdf_hits:
            print(f"    {h['url']}")
    elif redirects:
        print(f"  → Page redirects detected — check quarterly_network_log.json")
        for rd in redirects:
            dest = rd.get("to", "")
            print(f"    Redirect to: {dest}")
    elif final_url != url:
        print(f"  → Page navigated away:")
        print(f"    From: {url}")
        print(f"    To:   {final_url}")
    else:
        print(f"  ? No PDF found, no redirects. Page may need login or uses JS download.")
        print(f"    → Open quarterly_page.html to inspect the rendered page.")
        print(f"    → Check quarterly_network_log.json for XHR calls.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", "-u", help="Specific URL to investigate")
    parser.add_argument("--all", "-a", action="store_true",
                        help="Investigate all three test URLs")
    args = parser.parse_args()

    if args.all:
        for url in TEST_URLS:
            investigate(url)
    elif args.url:
        investigate(args.url)
    else:
        # Default: investigate the first test URL
        print("Investigating first test URL. Use --all for all three.")
        investigate(TEST_URLS[0])
        print(f"\nOther URLs to try:")
        for u in TEST_URLS[1:]:
            print(f"  python investigate_quarterly.py --url \"{u}\"")


if __name__ == "__main__":
    main()
