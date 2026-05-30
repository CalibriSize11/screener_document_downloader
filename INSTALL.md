# Screener Document Downloader — Installation Guide

## Requirements

- **Python 3.10 or later** (the code uses `str | None` union type syntax)
- Internet connection
- A Screener.in company URL or company name

## Install

**Step 1 — Install Python packages**

```
pip install -r requirements.txt
```

**Step 2 — Install Playwright browser** (one-time, downloads ~130 MB)

```
playwright install chromium
```

This is required for the Concalls section, which is JavaScript-rendered
and not visible to plain HTTP requests.

## Run

```
python main.py
```

The script will prompt you for:
1. Company name or Screener URL
2. Output folder path

Or pass both as arguments to skip the prompts:

```
python main.py --company "Infosys"
python main.py --url "https://www.screener.in/company/INFY/consolidated/"
python main.py --company "TCS" --base "/path/to/output/folder"
```

## What gets downloaded

For each company, folders are created inside your chosen base folder:

```
CompanyName/
├── Annual Reports/          ← FY25 Annual Report.pdf
├── Quarterly Results/       ← FY26 Q1 Results.pdf
├── Investor Presentations/  ← FY26 Q1 Aug 2025 Investor Presentation.pdf
├── Concall Transcripts/     ← Q1FY26 Transcript.pdf
└── Credit Reports/          ← 2025-04 Credit Rating.pdf
```

Quarterly results are fetched from BSE using the confirmed
`category=Result, subcategory=Financial Results` filter.
All other documents are sourced from Screener's Documents section.

## Notes

- Re-running the script skips files that already exist.
- Credit reports may be saved as `.pdf`, `.html`, or `.bin` depending
  on what the rating agency URL returns. Skip detection works correctly
  for all three extensions.
- Investor presentations are hash-deduplicated after download — identical
  files from different URLs are saved only once.
- PDF validation runs after every download. Files that are not valid PDFs
  (e.g. HTML error pages) are rejected and logged.

## Troubleshooting

**`playwright install chromium` fails**
Run `playwright install --with-deps chromium` to install system dependencies.

**`ModuleNotFoundError`**
Make sure you run `pip install -r requirements.txt` in the same Python
environment you use to run `python main.py`. If using a virtual environment,
activate it first.

**BSE rate limiting / timeouts**
The BSE API is rate-limited. If timeouts occur, the script retries up to
3 times with a 2-second delay. Partial results are preserved.
