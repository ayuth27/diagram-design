#!/usr/bin/env python3
"""Check a generated diagram file against one brand's CI.

Flags colors and fonts that are not in the target brand's profile
(`~/.diagram-design/profiles/<slug>.md`, or `$DIAGRAM_DESIGN_HOME/profiles/`),
colors that belong to a *different* brand's accent, and logo markup
(`data-brand-logo`) that is missing, wrong, or has no matching asset on disk.

Content inside any element carrying `data-brand-logo` is excluded from the
color/font scan — a logo image has its own palette.

Usage:
    python3 scripts/brand_check.py diagram.html --brand acme
    python3 scripts/brand_check.py diagram.html --brand acme --json

Exit codes: 0 no findings, 1 one or more findings, 2 misuse (bad args, brand
or profile not found, file not found).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
RGB_RE = re.compile(
    r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*[\d.]+\s*)?\)",
    re.IGNORECASE,
)
KEYWORD_COLOR_RE = re.compile(
    r"(?:fill|stroke|color|background(?:-color)?|border-color|stop-color)\s*:\s*"
    r"(none|transparent|currentcolor)\b",
    re.IGNORECASE,
)
FONT_FAMILY_DECL_RE = re.compile(r"font-family\s*:\s*([^;\"']+)", re.IGNORECASE)
CUSTOM_FONT_PROP_RE = re.compile(r"(--font[\w-]*)\s*:\s*([^;\"']+)", re.IGNORECASE)

COLOR_KEYWORDS = {"none", "transparent", "currentcolor"}
GENERIC_FONTS = {
    "serif",
    "sans-serif",
    "monospace",
    "system-ui",
    "ui-monospace",
    "ui-sans-serif",
    "ui-serif",
}


def home_dir() -> Path:
    override = os.environ.get("DIAGRAM_DESIGN_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".diagram-design"


def normalize_hex(raw: str) -> str:
    value = raw.lstrip("#").lower()
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return value


def normalize_rgb(r: str, g: str, b: str) -> str:
    return "".join(f"{int(ch):02x}" for ch in (r, g, b))


def extract_colors(text: str) -> list[str]:
    """Return normalized color keys (6-hex or a keyword) found in *text*."""
    found: list[str] = []
    for match in HEX_RE.finditer(text):
        found.append(normalize_hex(match.group(0)))
    for match in RGB_RE.finditer(text):
        found.append(normalize_rgb(*match.groups()))
    for match in KEYWORD_COLOR_RE.finditer(text):
        found.append(match.group(1).lower())
    return found


def clean_family_name(raw: str) -> str:
    text = re.sub(r"\([^)]*\)", "", raw)
    text = text.replace("*", "")
    text = re.sub(r"(?i)\bitalic\b", "", text)
    text = re.sub(r"\b\d+\b", "", text)
    return text.strip(" \t\"'")


def split_font_families(value: str) -> list[str]:
    return [clean_family_name(part) for part in value.split(",") if clean_family_name(part)]


def extract_fonts(text: str) -> list[str]:
    found: list[str] = []
    for match in FONT_FAMILY_DECL_RE.finditer(text):
        found.extend(split_font_families(match.group(1)))
    for match in CUSTOM_FONT_PROP_RE.finditer(text):
        found.extend(split_font_families(match.group(2)))
    return found


# ---------------------------------------------------------------------------
# Markdown profile parsing
# ---------------------------------------------------------------------------

def extract_markdown_table(markdown: str, heading: str) -> list[list[str]]:
    """Return table rows (including the header row, excluding the separator)."""
    idx = markdown.find(heading)
    if idx < 0:
        return []
    rows: list[list[str]] = []
    started = False
    for line in markdown[idx:].splitlines()[1:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            if started:
                break
            continue
        started = True
        if re.fullmatch(r"\|?[\s:|-]+\|?", stripped):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        rows.append(cells)
    return rows


def column_index(header: list[str], needle: str) -> int | None:
    needle = needle.casefold()
    for index, cell in enumerate(header):
        if needle in cell.casefold():
            return index
    return None


def parse_semantic_roles(markdown: str) -> tuple[list[str], dict[str, list[str]]]:
    """Return (allowed_color_keys, {role: [light_key, dark_key]}) incl. series palette."""
    allowed: list[str] = []
    role_colors: dict[str, list[str]] = {}
    for heading in ("### Semantic roles", "### Series palette"):
        rows = extract_markdown_table(markdown, heading)
        if not rows:
            continue
        header, data_rows = rows[0], rows[1:]
        light_idx = column_index(header, "light")
        dark_idx = column_index(header, "dark")
        role_idx = 0
        for row in data_rows:
            role = row[role_idx].strip("` ") if row else ""
            keys: list[str] = []
            for idx in (light_idx, dark_idx):
                if idx is None or idx >= len(row):
                    continue
                cell_keys = extract_colors(row[idx])
                keys.extend(cell_keys)
                allowed.extend(cell_keys)
            if role:
                role_colors.setdefault(role, []).extend(keys)
    return allowed, role_colors


def parse_typography(markdown: str) -> list[str]:
    rows = extract_markdown_table(markdown, "## Typography")
    if not rows:
        return []
    header, data_rows = rows[0], rows[1:]
    family_idx = None
    for index, cell in enumerate(header):
        if cell.strip().casefold() == "family":
            family_idx = index
            break
    if family_idx is None:
        return []
    families: list[str] = []
    for row in data_rows:
        if family_idx >= len(row):
            continue
        families.extend(split_font_families(row[family_idx]))
    return families


def parse_brand_notes_fonts(markdown: str) -> list[str]:
    headings = list(re.finditer(r"^(#{2,3})\s+(.*)$", markdown, re.MULTILINE))
    families: list[str] = []
    for index, heading in enumerate(headings):
        if "brand notes" not in heading.group(2).casefold():
            continue
        start = heading.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        body = markdown[start:end]
        families.extend(match.group(1) for match in re.finditer(r"'([^']+)'", body))
    return families


def parse_profile(markdown: str, extra_colors: list[str]) -> tuple[set[str], dict[str, list[str]], set[str]]:
    allowed_colors_list, role_colors = parse_semantic_roles(markdown)
    allowed_colors = set(allowed_colors_list)
    for raw in extra_colors:
        if HEX_RE.fullmatch(raw) or HEX_RE.fullmatch("#" + raw.lstrip("#")):
            allowed_colors.add(normalize_hex(raw))
    allowed_colors.add("ffffff")
    allowed_colors.add("000000")
    allowed_colors.update(COLOR_KEYWORDS)

    allowed_fonts = {name.casefold() for name in parse_typography(markdown)}
    allowed_fonts.update(name.casefold() for name in parse_brand_notes_fonts(markdown))
    allowed_fonts.update(GENERIC_FONTS)

    return allowed_colors, role_colors, allowed_fonts


def accent_colors(role_colors: dict[str, list[str]]) -> set[str]:
    return set(role_colors.get("accent", []))


# ---------------------------------------------------------------------------
# Brand library loading (minimal — brand_resolve.py owns full validation)
# ---------------------------------------------------------------------------

def load_brand(home: Path, slug: str) -> dict[str, Any] | None:
    brand_json = home / "brands" / slug / "brand.json"
    if not brand_json.is_file():
        return None
    try:
        payload = json.loads(brand_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("slug") != slug:
        return None
    if not isinstance(payload.get("profile"), str):
        return None
    logo = payload.get("logo") if isinstance(payload.get("logo"), dict) else {}
    extra_colors = payload.get("extra_colors", [])
    if not isinstance(extra_colors, list):
        extra_colors = []
    return {
        "slug": slug,
        "profile": payload["profile"],
        "logo": logo,
        "extra_colors": [c for c in extra_colors if isinstance(c, str)],
    }


def load_all_brand_slugs(home: Path) -> list[str]:
    brands_dir = home / "brands"
    if not brands_dir.is_dir():
        return []
    return sorted(p.name for p in brands_dir.iterdir() if p.is_dir())


def brand_has_logo_asset(home: Path, brand: dict[str, Any]) -> bool:
    logo = brand.get("logo") or {}
    for key in ("light", "dark"):
        name = logo.get(key)
        if isinstance(name, str) and name and "/" not in name and ".." not in name:
            if (home / "brands" / brand["slug"] / name).is_file():
                return True
    return False


# ---------------------------------------------------------------------------
# HTML scanning
# ---------------------------------------------------------------------------

class BrandScanner(HTMLParser):
    """Collect colors/fonts outside data-brand-logo subtrees, and all
    data-brand-logo attribute values regardless of nesting."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.colors: list[str] = []
        self.fonts: list[str] = []
        self.logo_values: list[str] = []
        self._stack: list[tuple[str, bool]] = []  # (tag, excluded)
        self._in_style = False
        self._style_excluded = False

    def _current_excluded(self) -> bool:
        return self._stack[-1][1] if self._stack else False

    def _record_attrs(self, attrs: list[tuple[str, str | None]], excluded: bool) -> None:
        for key, value in attrs:
            key = (key or "").casefold()
            value = value or ""
            if key == "data-brand-logo":
                self.logo_values.append(value)
            if excluded:
                continue
            self.colors.extend(extract_colors(value))
            if key == "style":
                self.fonts.extend(extract_fonts(value))
            elif key == "font-family":
                self.fonts.extend(split_font_families(value))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        parent_excluded = self._current_excluded()
        has_logo_attr = any((key or "").casefold() == "data-brand-logo" for key, _ in attrs)
        excluded = parent_excluded or has_logo_attr
        self._record_attrs(attrs, excluded)
        self._stack.append((tag, excluded))
        if tag.casefold() == "style":
            self._in_style = True
            self._style_excluded = excluded

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        parent_excluded = self._current_excluded()
        has_logo_attr = any((key or "").casefold() == "data-brand-logo" for key, _ in attrs)
        excluded = parent_excluded or has_logo_attr
        self._record_attrs(attrs, excluded)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "style":
            self._in_style = False
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if self._in_style and not self._style_excluded:
            self.colors.extend(extract_colors(data))
            self.fonts.extend(extract_fonts(data))


def scan_file(path: Path) -> BrandScanner:
    scanner = BrandScanner()
    scanner.feed(path.read_text(encoding="utf-8"))
    return scanner


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--brand", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not SLUG_RE.match(args.brand):
        print(f"error: invalid --brand slug {args.brand!r}", file=sys.stderr)
        return 2
    if not args.file.is_file():
        print(f"error: no such file: {args.file}", file=sys.stderr)
        return 2

    home = home_dir()
    brand = load_brand(home, args.brand)
    if brand is None:
        print(f"error: brand {args.brand!r} not found in {home / 'brands'}", file=sys.stderr)
        return 2

    profile_path = home / "profiles" / f"{brand['profile']}.md"
    if not profile_path.is_file():
        print(f"error: profile file missing: {profile_path}", file=sys.stderr)
        return 2
    profile_text = profile_path.read_text(encoding="utf-8")
    allowed_colors, role_colors, allowed_fonts = parse_profile(profile_text, brand["extra_colors"])
    own_accents = accent_colors(role_colors)

    other_accents: dict[str, set[str]] = {}
    for other_slug in load_all_brand_slugs(home):
        if other_slug == args.brand:
            continue
        other_brand = load_brand(home, other_slug)
        if other_brand is None:
            continue
        other_profile_path = home / "profiles" / f"{other_brand['profile']}.md"
        if not other_profile_path.is_file():
            continue
        try:
            other_text = other_profile_path.read_text(encoding="utf-8")
        except OSError:
            continue
        _, other_role_colors, _ = parse_profile(other_text, other_brand["extra_colors"])
        accents = accent_colors(other_role_colors) - own_accents
        if accents:
            other_accents[other_slug] = accents

    scanner = scan_file(args.file)

    findings: list[dict[str, Any]] = []

    color_counts: dict[str, int] = {}
    for key in scanner.colors:
        color_counts[key] = color_counts.get(key, 0) + 1

    for key, count in sorted(color_counts.items()):
        if key in allowed_colors:
            continue
        owner = None
        for other_slug, accents in other_accents.items():
            if key in accents:
                owner = other_slug
                break
        if owner:
            findings.append(
                {
                    "type": "other-brand-color",
                    "message": f"color #{key} matches brand '{owner}' accent, not '{args.brand}'",
                    "value": key,
                    "count": count,
                    "brand": owner,
                }
            )
        else:
            findings.append(
                {
                    "type": "off-brand-color",
                    "message": f"color #{key} is not in '{args.brand}' profile (used {count}x)",
                    "value": key,
                    "count": count,
                }
            )

    font_counts: dict[str, int] = {}
    for raw in scanner.fonts:
        font_counts[raw] = font_counts.get(raw, 0) + 1
    for raw, count in sorted(font_counts.items()):
        if raw.casefold() in allowed_fonts:
            continue
        findings.append(
            {
                "type": "off-brand-font",
                "message": f"font family '{raw}' is not in '{args.brand}' profile (used {count}x)",
                "value": raw,
                "count": count,
            }
        )

    logo_values = scanner.logo_values
    has_own_logo = args.brand in logo_values
    wrong_brands = sorted({value for value in logo_values if value != args.brand})
    logo_cfg = brand.get("logo") or {}

    if logo_cfg.get("required") and not has_own_logo:
        findings.append(
            {
                "type": "logo-missing",
                "message": f"logo.required is true for '{args.brand}' but no data-brand-logo=\"{args.brand}\" element found",
            }
        )
    for wrong in wrong_brands:
        findings.append(
            {
                "type": "logo-wrong-brand",
                "message": f"data-brand-logo=\"{wrong}\" found while checking brand '{args.brand}'",
                "value": wrong,
            }
        )
    if has_own_logo and not brand_has_logo_asset(home, brand):
        findings.append(
            {
                "type": "logo-without-asset",
                "message": f"data-brand-logo=\"{args.brand}\" found but the brand has no logo asset on disk",
            }
        )

    if args.json:
        print(json.dumps({"brand": args.brand, "findings": findings, "count": len(findings)}, indent=2))
    else:
        for finding in findings:
            print(f"{finding['type']}: {finding['message']}")
        print(f"brand_check: {args.brand} — {len(findings)} finding(s)")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
