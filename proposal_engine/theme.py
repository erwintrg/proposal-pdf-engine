"""Themes: one folder per brand with a theme.json (who sends the proposal and how it looks).

themes/<name>/theme.json
  agency      name, contact, legal details, VAT home country, verified related systems
  colors      CSS colors, injected as CSS variables
  fonts       font-family stacks + stylesheets (@fonts/... for the shipped fonts, local files or https URLs)
  typography  heading weight and accent style
  logo        path relative to the theme folder
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, PrivateAttr, ValidationError, field_validator

from . import ROOT_DIR, THEMES_DIR
from .schema import Model, Problem, ProposalError, RelatedSystem, problems_from_validation

# Theme values end up inside a <style> block, so anything that could close a rule or a tag is refused.
CSS_UNSAFE = re.compile(r"[;{}<>\\]")


class Bank(Model):
    account_holder: str
    iban: str
    bic: str = ""


class Agency(Model):
    name: str
    short_name: str
    person: str
    person_first_name: str
    email: str
    website: str = ""
    address: list[str] = Field(default_factory=list)
    vat_id: str = ""
    country: str = Field(default="DE", description="ISO 3166-1 alpha-2 code of the agency.")
    domestic_vat_rate: float = Field(default=0.19, ge=0, lt=1)
    domestic_vat_label: str = "VAT 19%"
    governing_law: str = "German law"
    jurisdiction: str = ""
    bank: Optional[Bank] = None
    related_systems: list[RelatedSystem] = Field(
        default_factory=list,
        description="Systems the agency has really built. The drafter may only pick from this list.",
    )


class Colors(Model):
    paper: str
    ink: str
    body: str
    muted: str
    accent: str
    highlight: str
    line: str
    panel: str
    dots: str = "transparent"

    @field_validator("*")
    @classmethod
    def _css_safe(cls, value: str) -> str:
        if CSS_UNSAFE.search(value):
            raise ValueError(f"not a plain CSS color: {value!r}")
        return value


class Fonts(Model):
    serif: str
    sans: str
    mono: str
    stylesheets: list[str] = Field(
        default_factory=list,
        description="CSS files with @font-face rules: '@fonts/<family>/font.css' for the fonts shipped in "
        "this repo, a path relative to the theme folder, or an https URL such as a Google Fonts stylesheet.",
    )

    @field_validator("serif", "sans", "mono")
    @classmethod
    def _css_safe(cls, value: str) -> str:
        if CSS_UNSAFE.search(value):
            raise ValueError(f"not a plain font-family stack: {value!r}")
        return value


class Typography(Model):
    heading_weight: int = Field(default=400, ge=100, le=900)
    accent_style: Literal["italic", "normal"] = "italic"


class Theme(Model):
    name: str
    agency: Agency
    colors: Colors
    fonts: Fonts
    typography: Typography = Field(default_factory=Typography)
    logo: str = ""

    _folder: Path = PrivateAttr(default=Path("."))

    @property
    def folder(self) -> Path:
        return self._folder

    def css_variables(self) -> str:
        c, f, t = self.colors, self.fonts, self.typography
        pairs = {
            "paper": c.paper, "ink": c.ink, "body": c.body, "muted": c.muted, "accent": c.accent,
            "highlight": c.highlight, "line": c.line, "panel": c.panel, "dots": c.dots,
            "serif": f.serif, "sans": f.sans, "mono": f.mono,
            "heading-weight": str(t.heading_weight), "accent-style": t.accent_style,
        }
        return ":root {\n" + "".join(f"  --{k}: {v};\n" for k, v in pairs.items()) + "}"

    def asset_path(self, entry: str) -> Path:
        """'@fonts/...' points at the repo's fonts folder, anything else is relative to the theme."""
        if entry.startswith("@fonts/"):
            return (ROOT_DIR / "fonts" / entry.removeprefix("@fonts/")).resolve()
        return (self.folder / entry).resolve()

    def stylesheet_urls(self) -> list[str]:
        return [entry if entry.startswith("https://") else self.asset_path(entry).as_uri()
                for entry in self.fonts.stylesheets]

    def logo_url(self) -> Optional[str]:
        return (self.folder / self.logo).resolve().as_uri() if self.logo else None


def resolve_theme_path(name_or_path: str | Path) -> Path:
    """'ycat' -> themes/ycat/theme.json; a folder or a theme.json path is used as given."""
    candidate = Path(name_or_path)
    if candidate.is_file():
        return candidate
    if candidate.is_dir():
        return candidate / "theme.json"
    return THEMES_DIR / str(name_or_path) / "theme.json"


def load_theme(name_or_path: str | Path = "ycat") -> Theme:
    path = resolve_theme_path(name_or_path)
    if not path.is_file():
        available = sorted(p.name for p in THEMES_DIR.iterdir() if (p / "theme.json").is_file())
        raise ProposalError([Problem("theme", f"no theme at {path} (available: {', '.join(available)})")])
    try:
        theme = Theme.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as err:
        raise ProposalError([Problem("theme", f"{path} is not valid JSON: {err}")]) from None
    except ValidationError as err:
        raise ProposalError([Problem(f"theme.{p.path}", p.message) for p in problems_from_validation(err)]) from None
    theme._folder = path.parent.resolve()

    missing = []
    if theme.logo and not (theme.folder / theme.logo).is_file():
        missing.append(Problem("theme.logo", f"file not found: {theme.folder / theme.logo}"))
    for entry in theme.fonts.stylesheets:
        if not entry.startswith("https://") and not theme.asset_path(entry).is_file():
            missing.append(Problem("theme.fonts.stylesheets", f"file not found: {theme.asset_path(entry)}"))
    if missing:
        raise ProposalError(missing)
    return theme
