"""
utils.py — Folder creation, metadata management, logging helpers.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from config import FOLDER_NAMES

console = Console()


# ── Logging setup ──────────────────────────────────────────────────────────────
def setup_logging(verbose: bool = False) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
    )
    return logging.getLogger("screener")


# ── Folder structure ───────────────────────────────────────────────────────────
def create_company_folders(base_path: str, company_name: str) -> dict[str, Path]:
    """
    Creates the folder structure for a company. Returns a dict of
    { doc_type_key: Path } for each subfolder.
    """
    sanitized = sanitize_name(company_name)
    company_dir = Path(base_path) / sanitized
    company_dir.mkdir(parents=True, exist_ok=True)

    folders: dict[str, Path] = {}
    for key, display_name in FOLDER_NAMES.items():
        folder = company_dir / display_name
        folder.mkdir(exist_ok=True)
        folders[key] = folder

    return folders


def sanitize_name(name: str) -> str:
    """Remove characters that are invalid in folder/file names."""
    invalid = r'\/:*?"<>|'
    for ch in invalid:
        name = name.replace(ch, "")
    return name.strip()


# ── Metadata ───────────────────────────────────────────────────────────────────
def load_metadata(company_dir: Path) -> dict:
    meta_path = company_dir / "metadata.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "company":               "",
        "screener_url":          "",
        "last_updated":          "",
        "annual_reports":        0,
        "quarterly_results":     0,
        "investor_presentations": 0,
        "concalls":              0,
        "failed_downloads":      [],
    }


def save_metadata(company_dir: Path, meta: dict) -> None:
    meta["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    meta_path = company_dir / "metadata.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")


def update_metadata_counts(meta: dict, folders: dict[str, Path]) -> dict:
    """Recount files in each folder and update metadata."""
    for key, folder in folders.items():
        if folder.exists():
            meta[key] = len(list(folder.glob("*.pdf")))
    return meta


# ── Summary table ──────────────────────────────────────────────────────────────
def print_summary(
    company: str,
    downloaded: int,
    skipped: int,
    failed: list[str],
) -> None:
    console.print()
    table = Table(title=f"[bold]Download Summary — {company}[/bold]", show_header=False)
    table.add_column("Label", style="dim")
    table.add_column("Value", style="bold")

    table.add_row("✅ Downloaded", str(downloaded))
    table.add_row("⏭  Skipped (already exist)", str(skipped))
    table.add_row("❌ Failed", str(len(failed)))

    console.print(table)

    if failed:
        console.print("\n[bold red]Failed files:[/bold red]")
        for f in failed:
            console.print(f"  • {f}")


# ── Ask user for base folder ───────────────────────────────────────────────────
def ask_base_folder() -> str:
    console.print("\n[bold]Where should the company folders be created?[/bold]")
    console.print("[dim]Example: /Users/Ram/Documents/Research  or  C:\\Users\\Ram\\Research[/dim]")
    while True:
        path = input("Base folder path: ").strip()
        if not path:
            console.print("[red]Path cannot be empty.[/red]")
            continue
        p = Path(path)
        if not p.exists():
            try:
                p.mkdir(parents=True)
                console.print(f"[green]Created folder: {p}[/green]")
            except Exception as e:
                console.print(f"[red]Cannot create folder: {e}[/red]")
                continue
        return str(p)
