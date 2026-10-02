"""Proposal PDF engine: a structured proposal.json in, a branded multi-page A4 PDF out."""

from pathlib import Path

__version__ = "0.1.0"

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT_DIR = PACKAGE_DIR.parent
THEMES_DIR = ROOT_DIR / "themes"
