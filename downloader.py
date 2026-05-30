"""
downloader.py — Download PDFs from any URL, following all redirects.

Handles direct PDFs, BSE/NSE links, company IR pages, and multi-hop redirects.
"""

import logging
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests

from config import DOWNLOAD_TIMEOUT, HEADERS, MAX_REDIRECT_HOPS

log = logging.getLogger("screener")


# ── Known non-PDF landing pages that need link extraction ─────────────────────
# Some domains serve an HTML wrapper instead of a direct PDF
HTML_WRAPPER_DOMAINS = [
    "bseindia.com",
    "nseindia.com",
    "nsearchives.nseindia.com",
    "corporates.bseindia.com",
]


def _is_html_wrapper(url: str) -> bool:
    domain = urlparse(url).netloc.lower()
    return any(d in domain for d in HTML_WRAPPER_DOMAINS)


def _extract_pdf_from_html(html: str, base_url: str) -> Optional[str]:
    """Try to find a PDF link in an HTML wrapper page."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf") or "pdf" in href.lower():
            return urljoin(base_url, href)
    # Also look for <iframe> or <embed> with PDF src
    for tag in soup.find_all(["iframe", "embed", "object"], src=True):
        src = tag.get("src", "")
        if src.lower().endswith(".pdf") or "pdf" in src.lower():
            return urljoin(base_url, src)
    return None


def _download_stream(url: str, dest: Path, session: requests.Session) -> bool:
    """Stream-download a URL to dest. Returns True on success."""
    try:
        with session.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT, allow_redirects=True) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")

            # If we land on an HTML page (BSE/NSE wrapper), try to find the real PDF
            if "html" in content_type:
                html = resp.text
                pdf_url = _extract_pdf_from_html(html, url)
                if pdf_url:
                    log.debug(f"  → Found PDF inside HTML wrapper: {pdf_url}")
                    return _download_stream(pdf_url, dest, session)
                else:
                    log.warning(f"  → Got HTML but couldn't find PDF link at: {url}")
                    return False

            # Confirm it's a PDF or binary
            if "pdf" not in content_type.lower() and "octet" not in content_type.lower():
                log.warning(f"  → Unexpected content-type '{content_type}' — attempting anyway")

            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)

        # Sanity check: PDF magic bytes
        if dest.stat().st_size < 1024:
            log.warning(f"  → Downloaded file too small ({dest.stat().st_size} bytes) — may be corrupt")

        return True

    except requests.exceptions.Timeout:
        log.error(f"  → Timeout downloading: {url}")
        return False
    except requests.exceptions.HTTPError as e:
        log.error(f"  → HTTP error {e.response.status_code}: {url}")
        return False
    except Exception as e:
        log.error(f"  → Download error: {e}")
        return False


def _resolve_google_drive(url: str) -> Optional[str]:
    """Convert a Google Drive share URL to a direct download URL."""
    import re
    m = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    if m:
        file_id = m.group(1)
        return f"https://drive.google.com/uc?export=download&id={file_id}"
    return None


def _resolve_url(url: str) -> str:
    """Resolve known URL patterns to direct download links."""
    if "drive.google.com" in url:
        direct = _resolve_google_drive(url)
        return direct or url
    return url


# ── Main download function ─────────────────────────────────────────────────────
def download_pdf(
    url:      str,
    dest:     Path,
    session:  Optional[requests.Session] = None,
    retries:  int = 2,
) -> bool:
    """
    Download a PDF from url to dest.
    Follows redirects, handles HTML wrappers, and retries on failure.
    Returns True on success.
    """
    if session is None:
        session = requests.Session()
        session.headers.update(HEADERS)

    url = _resolve_url(url)

    for attempt in range(1, retries + 2):
        success = _download_stream(url, dest, session)
        if success:
            return True
        if attempt <= retries:
            wait = 2 ** attempt
            log.debug(f"  → Retrying in {wait}s (attempt {attempt}/{retries})...")
            time.sleep(wait)

    return False


# ── File existence check ───────────────────────────────────────────────────────
def already_exists(folder: Path, stem: str) -> Optional[Path]:
    """
    Return the existing path if a file with this stem already exists
    in the folder, else None.
    """
    for ext in [".pdf", ".PDF"]:
        candidate = folder / (stem + ext)
        if candidate.exists():
            return candidate
    return None
