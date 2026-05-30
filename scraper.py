"""
scraper.py — Search Screener for a company and extract document links.

Strategy:
  1. Use requests + BeautifulSoup (fast, no JS overhead).
  2. Fall back to Playwright if the page content appears empty / JS-rendered.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config import (
    HEADERS,
    SCREENER_BASE_URL,
    SCREENER_SEARCH_URL,
    SECTION_KEYWORDS,
)

log = logging.getLogger("screener")


# ── Data structures ────────────────────────────────────────────────────────────
@dataclass
class DocumentLink:
    url:      str
    title:    str           # raw title from Screener
    doc_type: str           # one of FOLDER_NAMES keys
    date_label: str = ""    # e.g. "May 2026" from Screener's label


@dataclass
class CompanyResult:
    name:        str
    url:         str          # relative, e.g. /company/INFY/
    full_url:    str = ""

    def __post_init__(self):
        if not self.full_url:
            self.full_url = SCREENER_BASE_URL + self.url


# ── Company search ─────────────────────────────────────────────────────────────
def search_company(query: str) -> Optional[CompanyResult]:
    """
    Search Screener for a company name.  Returns the user-selected result.
    If only one result, auto-selects it.
    Returns None if nothing found.
    """
    log.info(f"Searching Screener for: [bold]{query}[/bold]")

    try:
        resp = requests.get(
            SCREENER_SEARCH_URL,
            params={"q": query, "v": "3"},
            headers=HEADERS,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        log.error(f"Search failed: {e}")
        return None

    if not data:
        log.warning("No companies found for that query.")
        return None

    results = [CompanyResult(name=r["name"], url=r["url"]) for r in data[:10]]

    if len(results) == 1:
        log.info(f"Found: [green]{results[0].name}[/green]")
        return results[0]

    # Multiple matches — ask user
    print("\nMultiple companies found. Please choose:\n")
    for i, r in enumerate(results, 1):
        print(f"  {i}. {r.name}  ({r.url})")

    while True:
        choice = input("\nEnter number (or 0 to cancel): ").strip()
        if choice == "0":
            return None
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(results):
                return results[idx]
        except ValueError:
            pass
        print("Invalid choice. Try again.")


def resolve_company_url(raw_input: str) -> Optional[CompanyResult]:
    """
    Accepts either a company name or a Screener URL.
    Returns a CompanyResult ready to scrape.
    """
    raw = raw_input.strip()

    if "screener.in/company/" in raw:
        # Direct URL
        url = raw if raw.startswith("http") else "https://" + raw
        # Extract name from URL path: /company/INFY/ → INFY
        parts = url.rstrip("/").split("/")
        name = parts[-1] if parts[-1] else parts[-2]
        return CompanyResult(name=name, url="", full_url=url)

    # Company name → search
    return search_company(raw)


# ── Page fetching ──────────────────────────────────────────────────────────────
def _fetch_with_requests(url: str) -> Optional[BeautifulSoup]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        # Heuristic: if fewer than 5 links, JS may be required
        if len(soup.find_all("a")) < 5:
            return None
        return soup
    except Exception as e:
        log.warning(f"requests fetch failed ({e}), will try Playwright")
        return None


def _fetch_with_playwright(url: str) -> Optional[BeautifulSoup]:
    try:
        from playwright.sync_api import sync_playwright
        log.info("Using Playwright to fetch page (JS rendering required)...")
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_extra_http_headers(HEADERS)
            page.goto(url, timeout=30_000)
            page.wait_for_load_state("networkidle", timeout=20_000)
            html = page.content()
            browser.close()
        return BeautifulSoup(html, "lxml")
    except Exception as e:
        log.error(f"Playwright fetch failed: {e}")
        return None


def fetch_page(url: str) -> Optional[BeautifulSoup]:
    soup = _fetch_with_requests(url)
    if soup is None:
        soup = _fetch_with_playwright(url)
    return soup


# ── Document link extraction ───────────────────────────────────────────────────
def _classify_section(heading_text: str) -> Optional[str]:
    """Return doc_type key if the heading matches a known section."""
    h = heading_text.lower().strip()
    for doc_type, keywords in SECTION_KEYWORDS.items():
        for kw in keywords:
            if kw in h:
                return doc_type
    return None


def _extract_links_from_soup(soup: BeautifulSoup) -> list[DocumentLink]:
    """
    Multi-strategy extraction:
      Strategy A — look for section headings then collect links beneath them.
      Strategy B — look for anchor tags whose text or href suggests a document type.
      Strategy C — collect all .pdf links and guess type from surrounding text.
    """
    docs: list[DocumentLink] = []
    seen_urls: set[str] = set()

    def add(url: str, title: str, doc_type: str, date_label: str = ""):
        url = url.strip()
        if not url or url in seen_urls:
            return
        if url.startswith("/"):
            url = SCREENER_BASE_URL + url
        seen_urls.add(url)
        docs.append(DocumentLink(url=url, title=title, doc_type=doc_type, date_label=date_label))

    # ── Strategy A: Section headings ─────────────────────────────────────────
    # Screener organises documents under <h2> or <h3> headings inside card divs.
    heading_tags = soup.find_all(["h2", "h3", "h4", "p", "div"], class_=True)
    # Also try plain headings
    heading_tags += soup.find_all(["h2", "h3", "h4"])

    for heading in heading_tags:
        heading_text = heading.get_text(strip=True)
        doc_type = _classify_section(heading_text)
        if not doc_type:
            continue

        # Walk siblings or parent container for links
        container = heading.find_parent(["section", "div", "li"]) or heading.parent
        if container is None:
            continue

        for a in container.find_all("a", href=True):
            href = a["href"]
            title = a.get_text(strip=True) or href.split("/")[-1]
            # Try to find a date label near the link (often in a sibling span)
            date_label = ""
            parent_li = a.find_parent("li")
            if parent_li:
                spans = parent_li.find_all(["span", "small", "div"])
                for sp in spans:
                    txt = sp.get_text(strip=True)
                    if any(m in txt for m in ["Jan","Feb","Mar","Apr","May","Jun",
                                               "Jul","Aug","Sep","Oct","Nov","Dec"]):
                        date_label = txt
                        break
            add(href, title, doc_type, date_label)

    # ── Strategy B: Anchor text classification ───────────────────────────────
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True).lower()

        if not text:
            continue

        # Look for explicit document keywords in anchor text
        for doc_type, keywords in SECTION_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                add(href, a.get_text(strip=True), doc_type)
                break

    # ── Strategy C: All PDF links (classify by surrounding context) ──────────
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if not (href.lower().endswith(".pdf") or "pdf" in href.lower()):
            continue
        if href in seen_urls:
            continue

        title = a.get_text(strip=True) or href.split("/")[-1]
        context = ""
        for parent in a.parents:
            context = parent.get_text(" ", strip=True)[:300].lower()
            if context:
                break

        doc_type = None
        for dtype, keywords in SECTION_KEYWORDS.items():
            if any(kw in context for kw in keywords):
                doc_type = dtype
                break

        if doc_type:
            add(href, title, doc_type)

    return docs


# ── Concall-specific extraction (PPT / Transcript / AI Summary) ───────────────
def _extract_concall_links(soup: BeautifulSoup) -> list[DocumentLink]:
    """
    Screener's concall section uses month labels (e.g. "May 2026") with
    buttons for Transcript, AI Summary, PPT.
    We want Transcript and PPT only (AI Summary is Screener's own content).
    """
    docs: list[DocumentLink] = []
    seen: set[str] = set()

    # Find the concalls section
    concall_section = None
    for tag in soup.find_all(["section", "div", "li"]):
        text = tag.get_text(" ", strip=True)
        if "concall" in text.lower() or "conference call" in text.lower():
            concall_section = tag
            break

    if concall_section is None:
        return docs

    # Each concall entry typically has a date label + buttons
    for row in concall_section.find_all(["li", "div", "tr"]):
        row_text = row.get_text(" ", strip=True)
        # Skip if no date-like text
        if not any(m in row_text for m in ["Jan","Feb","Mar","Apr","May","Jun",
                                            "Jul","Aug","Sep","Oct","Nov","Dec"]):
            continue
        date_label = row_text.split()[0:2]  # e.g. ["May", "2026"]
        date_str = " ".join(date_label)

        for a in row.find_all("a", href=True):
            href = a["href"]
            label = a.get_text(strip=True).lower()
            if "transcript" in label or "ppt" in label:
                url = href if href.startswith("http") else SCREENER_BASE_URL + href
                if url not in seen:
                    seen.add(url)
                    title = f"{date_str} {a.get_text(strip=True)}"
                    docs.append(DocumentLink(
                        url=url, title=title,
                        doc_type="concalls", date_label=date_str,
                    ))
    return docs


# ── Main entry point ───────────────────────────────────────────────────────────
def get_document_links(company: CompanyResult) -> list[DocumentLink]:
    """
    Scrape the Screener company page and return all document links found.
    """
    log.info(f"Fetching company page: [cyan]{company.full_url}[/cyan]")
    soup = fetch_page(company.full_url)

    if soup is None:
        log.error("Could not fetch the company page. Aborting.")
        return []

    docs = _extract_links_from_soup(soup)
    docs += _extract_concall_links(soup)

    # Deduplicate by URL
    seen: set[str] = set()
    unique_docs: list[DocumentLink] = []
    for d in docs:
        if d.url not in seen:
            seen.add(d.url)
            unique_docs.append(d)

    log.info(
        f"Found [bold]{len(unique_docs)}[/bold] document links "
        f"({_count(unique_docs, 'annual_reports')} annual, "
        f"{_count(unique_docs, 'quarterly_results')} quarterly, "
        f"{_count(unique_docs, 'investor_presentations')} presentations, "
        f"{_count(unique_docs, 'concalls')} concalls)"
    )
    return unique_docs


def _count(docs: list[DocumentLink], doc_type: str) -> int:
    return sum(1 for d in docs if d.doc_type == doc_type)
