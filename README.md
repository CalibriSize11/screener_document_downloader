# Screener Document Downloader

Downloads company documents from Screener.in and BSE and organizes them into structured folders.

## Features

* Annual Reports
* Quarterly Results
* Investor Presentations
* Concall Transcripts
* Credit Rating Reports
* Automatic folder organization
* Duplicate detection for investor presentations
* Works across Indian listed companies available on Screener

## Output Structure

Example:

ACE/

├── Annual Reports/

├── Quarterly Results/

├── Investor Presentations/

├── Concall Transcripts/

└── Credit Reports/

Files are automatically renamed into readable formats.

Examples:

* FY25 Annual Report.pdf
* FY26 Q3 Results.pdf
* FY26 Q2 Aug 2025 Investor Presentation.pdf
* Q2FY26 Transcript.pdf

## Requirements

* Python 3.10+
* Windows, Linux, or macOS
* Internet connection

## Installation

Clone the repository:

```bash
git clone https://github.com/CalibriSize11/screener_document_downloader.git
cd screener_document_downloader
```

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it:

Windows:

```bash
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Install Playwright browser:

```bash
playwright install chromium
```

## Usage

Run:

```bash
python main.py
```

or provide a Screener URL directly:

```bash
python main.py --url https://www.screener.in/company/ACE/consolidated/
```

The program will download available documents and organize them automatically.

## Data Sources

* Screener.in
* BSE India

## Known Limitations

* Some credit rating agencies return HTML pages instead of direct PDFs.
* Availability of documents depends on what is publicly disclosed by the company.
* Certain historical filings may be unavailable or removed by the source website.

## Disclaimer

This project is for educational and research purposes only.

All documents remain the property of their respective issuers, exchanges, and information providers.
