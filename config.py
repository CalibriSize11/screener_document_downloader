"""
config.py — Constants, mappings, and regex patterns.
"""

import re

# ── Screener URLs ──────────────────────────────────────────────────────────────
SCREENER_BASE_URL   = "https://www.screener.in"
SCREENER_SEARCH_URL = "https://www.screener.in/api/company/search/"

# ── HTTP Headers ───────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
}

# ── Folder names ───────────────────────────────────────────────────────────────
FOLDER_NAMES = {
    "annual_reports":          "Annual Reports",
    "quarterly_results":       "Quarterly Results",
    "investor_presentations":  "Investor Presentations",
    "concalls":                "Concalls",
}

# ── Keywords used to classify a document section heading ──────────────────────
SECTION_KEYWORDS = {
    "annual_reports":         ["annual report", "annual"],
    "quarterly_results":      ["quarterly result", "financial result", "result", "earnings"],
    "investor_presentations": ["investor presentation", "investor", "presentation"],
    "concalls":               ["concall", "conference call", "transcript", "earnings call"],
}

# ── Indian fiscal year: April → March ─────────────────────────────────────────
# Announcement month → (Quarter label, offset to add to year to get FY-end year)
# Example: Aug 2025 + offset 1  →  FY ends 2026  →  FY26
MONTH_TO_QUARTER = {
    7:  ("Q1", 1),   # Jul  → Q1
    8:  ("Q1", 1),   # Aug  → Q1
    9:  ("Q1", 1),   # Sep  → Q1
    10: ("Q2", 1),   # Oct  → Q2
    11: ("Q2", 1),   # Nov  → Q2
    12: ("Q3", 1),   # Dec  → Q3
    1:  ("Q3", 0),   # Jan  → Q3
    2:  ("Q3", 0),   # Feb  → Q3
    3:  ("Q4", 0),   # Mar  → Q4 (late Q3 is rare; default Q4)
    4:  ("Q4", 0),   # Apr  → Q4
    5:  ("Q4", 0),   # May  → Q4
    6:  ("Q4", 0),   # Jun  → Q4
}

QUARTER_WORD_MAP = {
    "first": "Q1", "second": "Q2", "third": "Q3", "fourth": "Q4",
}

MONTH_NAME_TO_NUM = {
    "january": 1,   "jan": 1,
    "february": 2,  "feb": 2,
    "march": 3,     "mar": 3,
    "april": 4,     "apr": 4,
    "may": 5,
    "june": 6,      "jun": 6,
    "july": 7,      "jul": 7,
    "august": 8,    "aug": 8,
    "september": 9, "sep": 9,  "sept": 9,
    "october": 10,  "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

# ── Regex patterns to extract quarter + FY from text ──────────────────────────
# Each tuple: (compiled_pattern, match_type)
QUARTER_PATTERNS = [
    # Q4 FY26 / Q4FY26 / Q4 FY 26
    (re.compile(r'\b(Q[1-4])\s*[-–]?\s*FY\s*(\d{2})\b',   re.IGNORECASE), "q_fy2"),
    # Q4 FY2026 / Q4FY2026
    (re.compile(r'\b(Q[1-4])\s*[-–]?\s*FY\s*(20\d{2})\b', re.IGNORECASE), "q_fy4"),
    # Quarter 4 FY26 / Quarter 4 FY2026
    (re.compile(r'Quarter\s+([1-4])\s*[-–]?\s*FY\s*(20?\d{2,4})', re.IGNORECASE), "qnum_fy"),
    # Fourth Quarter FY26
    (re.compile(r'(First|Second|Third|Fourth)\s+Quarter.*?FY\s*(20?\d{2,4})', re.IGNORECASE), "qword_fy"),
    # Q4 2026 / Q4 26  (no FY prefix)
    (re.compile(r'\b(Q[1-4])\s+(20\d{2})\b', re.IGNORECASE), "q_year4"),
]

# FY-year-only patterns (fallback when quarter comes from month mapping)
FY_ONLY_PATTERNS = [
    re.compile(r'FY\s*(\d{2})\b',                   re.IGNORECASE),
    re.compile(r'FY\s*(20\d{2})\b',                 re.IGNORECASE),
    re.compile(r'(20\d{2})\s*[-–]\s*(\d{2})\b'),    # 2025-26
    re.compile(r'(20\d{2})\s*[-–]\s*(20\d{2})\b'),  # 2025-2026
]

# ── Misc ───────────────────────────────────────────────────────────────────────
DOWNLOAD_TIMEOUT   = 90    # seconds per file
PDF_PAGES_TO_READ  = 3     # how many PDF pages to scan for quarter info
MAX_REDIRECT_HOPS  = 10    # safety limit for redirect chains
