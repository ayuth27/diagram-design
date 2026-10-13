#!/usr/bin/env python3
"""Resolve which brand's CI applies to the current request/project.

Reads the on-disk brand kit library (never bundled with the skill, never
committed to this repo): `$DIAGRAM_DESIGN_HOME/brands/<slug>/brand.json`, or
`~/.diagram-design/brands/<slug>/brand.json` when that variable is unset.

Resolution order (first match wins — see references/brand-ci.md):

1. **Request** — an alias mentioned in `--request`, with longer aliases
   covering shorter ones over the same text span.
2. **cwd** — the most specific `paths` entry containing `--cwd` (default: the
   real current directory).
3. **Project marker** — a `.diagram-design` file above `--cwd` whose body is
   exactly `profile: <slug>`, matched against a brand's `profile` field.
4. Otherwise: not resolved. Never fall back to another brand's CI.

Usage:
    python3 scripts/brand_resolve.py --request "draw the acme architecture"
    python3 scripts/brand_resolve.py --cwd /abs/project/dir
    python3 scripts/brand_resolve.py --list
    python3 scripts/brand_resolve.py --request "acme" --json

Exit codes: 0 resolved, 2 not resolved, 3 ambiguous, 4 resolved but the
brand's profile file is missing from the library.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MARKER_RE = re.compile(r"^profile:[ \t]*([a-z0-9][a-z0-9-]{0,63})[ \t]*$")
MARKER_FILENAME = ".diagram-design"


def home_dir() -> Path:
    override = os.environ.get("DIAGRAM_DESIGN_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".diagram-design"


def is_ascii(text: str) -> bool:
    try:
        text.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def safe_filename(value: Any) -> str | None:
    """Return *value* if it is a bare filename (no '/' and no '..'), else None."""
    if not isinstance(value, str) or not value:
        return None
    if "/" in value or "\\" in value or ".." in value:
        return None
    return value


def load_brand(brands_dir: Path, slug_dir_name: str, warnings: list[str]) -> dict[str, Any] | None:
    brand_dir = brands_dir / slug_dir_name
    brand_json = brand_dir / "brand.json"
    if not brand_json.is_file():
        return None
    try:
        payload = json.loads(brand_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"skipping {brand_json}: {exc}")
        return None
    if not isinstance(payload, dict):
        warnings.append(f"skipping {brand_json}: not a JSON object")
        return None

    slug = payload.get("slug")
    name = payload.get("name")
    profile = payload.get("profile")
    aliases = payload.get("aliases")
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        warnings.append(f"skipping {brand_json}: missing/invalid 'slug'")
        return None
    if slug != slug_dir_name:
        warnings.append(f"skipping {brand_json}: slug {slug!r} does not match folder name {slug_dir_name!r}")
        return None
    if not isinstance(name, str) or not name:
        warnings.append(f"skipping {brand_json}: missing/invalid 'name'")
        return None
    if not isinstance(profile, str) or not profile:
        warnings.append(f"skipping {brand_json}: missing/invalid 'profile'")
        return None
    if not isinstance(aliases, list) or not all(isinstance(a, str) and a for a in aliases):
        warnings.append(f"skipping {brand_json}: missing/invalid 'aliases'")
        return None

    paths = payload.get("paths", [])
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        warnings.append(f"ignoring invalid 'paths' in {brand_json}")
        paths = []

    logo_raw = payload.get("logo")
    logo: dict[str, Any] = {}
    if isinstance(logo_raw, dict):
        light = safe_filename(logo_raw.get("light"))
        dark = safe_filename(logo_raw.get("dark"))
        if "light" in logo_raw and light is None:
            warnings.append(f"ignoring invalid logo.light in {brand_json}")
        if "dark" in logo_raw and dark is None:
            warnings.append(f"ignoring invalid logo.dark in {brand_json}")
        logo = {
            "light": light,
            "dark": dark,
            "required": bool(logo_raw.get("required", False)),
            "min_height_px": logo_raw.get("min_height_px"),
            "clear_space": logo_raw.get("clear_space"),
            "placement": logo_raw.get("placement"),
            "rules": logo_raw.get("rules") if isinstance(logo_raw.get("rules"), list) else [],
        }
    elif logo_raw is not None:
        warnings.append(f"ignoring invalid 'logo' in {brand_json}")

    icons_dir = payload.get("icons_dir")
    if icons_dir is not None and not isinstance(icons_dir, str):
        icons_dir = None

    extra_colors = payload.get("extra_colors", [])
    if not isinstance(extra_colors, list) or not all(isinstance(c, str) for c in extra_colors):
        extra_colors = []

    fonts_note = payload.get("fonts_note")
    if fonts_note is not None and not isinstance(fonts_note, str):
        fonts_note = None

    sources = payload.get("sources", [])
    if not isinstance(sources, list):
        sources = []

    return {
        "slug": slug,
        "name": name,
        "profile": profile,
        "aliases": aliases,
        "paths": paths,
        "logo": logo,
        "icons_dir": icons_dir,
        "extra_colors": extra_colors,
        "fonts_note": fonts_note,
        "sources": sources,
        "dir": brand_dir,
    }


def load_all_brands(home: Path) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    brands_dir = home / "brands"
    if not brands_dir.is_dir():
        return [], warnings
    brands = []
    for entry in sorted(brands_dir.iterdir()):
        if not entry.is_dir():
            continue
        brand = load_brand(brands_dir, entry.name, warnings)
        if brand is not None:
            brands.append(brand)
    return brands, warnings


def find_alias_matches(request: str, brands: list[dict[str, Any]]) -> list[tuple[int, int, dict[str, Any], str]]:
    """Return (start, end, brand, alias) for every alias match in *request*."""
    matches: list[tuple[int, int, dict[str, Any], str]] = []
    lowered = request
    for brand in brands:
        for alias in brand["aliases"]:
            if is_ascii(alias):
                pattern = re.compile(
                    r"(?<![a-z0-9])" + re.escape(alias.lower()) + r"(?![a-z0-9])",
                    re.IGNORECASE,
                )
                for match in pattern.finditer(lowered):
                    matches.append((match.start(), match.end(), brand, alias))
            else:
                start = 0
                while True:
                    idx = lowered.find(alias, start)
                    if idx < 0:
                        break
                    matches.append((idx, idx + len(alias), brand, alias))
                    start = idx + 1
    return matches


def resolve_from_request(request: str, brands: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Return (slug_if_unique, candidate_brands). Longer spans cover shorter overlapping ones."""
    matches = find_alias_matches(request, brands)
    if not matches:
        return None, []
    matches.sort(key=lambda m: (m[1] - m[0]), reverse=True)
    kept_spans: list[tuple[int, int]] = []
    kept_brands: dict[str, dict[str, Any]] = {}
    for start, end, brand, _alias in matches:
        if any(start < kept_end and kept_start < end for kept_start, kept_end in kept_spans):
            continue
        kept_spans.append((start, end))
        kept_brands[brand["slug"]] = brand
    candidates = list(kept_brands.values())
    if len(candidates) == 1:
        return candidates[0]["slug"], candidates
    return None, candidates


def resolve_from_cwd(cwd: Path, brands: list[dict[str, Any]]) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_len = -1
    for brand in brands:
        for raw_path in brand["paths"]:
            try:
                candidate = Path(raw_path).resolve()
            except OSError:
                continue
            try:
                cwd.relative_to(candidate)
            except ValueError:
                continue
            length = len(str(candidate))
            if length > best_len:
                best_len = length
                best = brand
    return best


def read_marker_slug(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if text.endswith("\n"):
        text = text[:-1]
    if "\n" in text:
        return None
    match = MARKER_RE.match(text)
    if not match:
        return None
    return match.group(1)


def resolve_from_marker(cwd: Path, brands: list[dict[str, Any]]) -> dict[str, Any] | None:
    current = cwd
    while True:
        marker = current / MARKER_FILENAME
        if marker.is_file():
            slug = read_marker_slug(marker)
            if slug:
                for brand in brands:
                    if brand["profile"] == slug:
                        return brand
                # Valid marker, no brand uses this profile slug: the marker
                # governs this project, so do not fall back silently to an
                # ancestor's marker or to another brand.
                return None
            # Malformed/hostile marker content: ignore it entirely and keep
            # climbing, exactly as references/profiles.md does.
        if current.parent == current:
            return None
        current = current.parent


def logo_paths(home: Path, brand: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    logo = brand.get("logo") or {}
    for key in ("light", "dark"):
        name = logo.get(key)
        if not name:
            continue
        candidate = home / "brands" / brand["slug"] / name
        if candidate.is_file():
            result[key] = str(candidate)
    return result


def brand_summary(home: Path, brand: dict[str, Any]) -> dict[str, Any]:
    profile_path = home / "profiles" / f"{brand['profile']}.md"
    return {
        "slug": brand["slug"],
        "name": brand["name"],
        "aliases": brand["aliases"],
        "profile": brand["profile"],
        "profile_path": str(profile_path),
        "profile_exists": profile_path.is_file(),
        "logo_files": logo_paths(home, brand),
    }


def cmd_list(home: Path, brands: list[dict[str, Any]], as_json: bool) -> int:
    if as_json:
        print(json.dumps({"brands": [brand_summary(home, b) for b in brands]}, indent=2))
        return 0
    if not brands:
        print("no brands in the library")
        return 0
    for brand in brands:
        summary = brand_summary(home, brand)
        logos = ", ".join(sorted(summary["logo_files"])) or "none"
        print(
            f"{summary['slug']} — {summary['name']} "
            f"(aliases: {', '.join(summary['aliases'])}; "
            f"profile: {'yes' if summary['profile_exists'] else 'MISSING'}; "
            f"logos: {logos})"
        )
    return 0


def emit_success(home: Path, brand: dict[str, Any], method: str, as_json: bool) -> int:
    summary = brand_summary(home, brand)
    summary["method"] = method
    summary["icons_dir"] = brand.get("icons_dir")
    summary["extra_colors"] = brand.get("extra_colors", [])
    summary["fonts_note"] = brand.get("fonts_note")
    summary["logo_rules"] = brand.get("logo") or {}

    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"brand: {summary['slug']} ({summary['name']})")
        print(f"method: {method}")
        print(f"profile: {summary['profile_path']}" + ("" if summary["profile_exists"] else " (MISSING)"))
        logos = summary["logo_files"]
        if logos:
            for key, value in sorted(logos.items()):
                print(f"logo-{key}: {value}")
        else:
            print("logo: none on disk")
        logo_rules = brand.get("logo") or {}
        if logo_rules:
            if logo_rules.get("required"):
                print("logo required: yes")
            if logo_rules.get("min_height_px") is not None:
                print(f"logo min-height-px: {logo_rules['min_height_px']}")
            if logo_rules.get("clear_space"):
                print(f"logo clear-space: {logo_rules['clear_space']}")
            if logo_rules.get("placement"):
                print(f"logo placement: {logo_rules['placement']}")
            for rule in logo_rules.get("rules") or []:
                print(f"logo rule: {rule}")
        if brand.get("icons_dir"):
            print(f"icons_dir: {brand['icons_dir']}")
        if brand.get("fonts_note"):
            print(f"fonts_note: {brand['fonts_note']}")

    if not summary["profile_exists"]:
        return 4
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", default=None)
    parser.add_argument("--cwd", default=None)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    home = home_dir()
    brands, warnings = load_all_brands(home)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if args.list:
        return cmd_list(home, brands, args.json)

    cwd = Path(args.cwd).resolve() if args.cwd else Path.cwd().resolve()

    if args.request:
        slug, candidates = resolve_from_request(args.request, brands)
        if slug is not None:
            brand = next(b for b in brands if b["slug"] == slug)
            return emit_success(home, brand, "request", args.json)
        if len(candidates) > 1:
            names = ", ".join(sorted(b["slug"] for b in candidates))
            message = f"ambiguous — ask the user: {names}"
            if args.json:
                print(json.dumps({"error": "ambiguous", "candidates": sorted(b["slug"] for b in candidates)}))
            else:
                print(message, file=sys.stderr)
            return 3

    by_cwd = resolve_from_cwd(cwd, brands)
    if by_cwd is not None:
        return emit_success(home, by_cwd, "cwd", args.json)

    by_marker = resolve_from_marker(cwd, brands)
    if by_marker is not None:
        return emit_success(home, by_marker, "marker", args.json)

    available = sorted(b["slug"] for b in brands)
    message = "no brand resolved — ask the user; never fall back to another brand's CI"
    if args.json:
        print(json.dumps({"error": "not_resolved", "available": available}))
    else:
        print(message, file=sys.stderr)
        if available:
            print(f"available brands: {', '.join(available)}", file=sys.stderr)
        else:
            print("available brands: (none in the library)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
