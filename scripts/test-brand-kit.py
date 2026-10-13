#!/usr/bin/env python3
"""Adversarial tests for the brand kit (brand_resolve.py / brand_check.py).

Builds a throwaway `$DIAGRAM_DESIGN_HOME` with fictitious brands (`acme`,
`acme-labs`, `globex` — never a real client) and exercises resolution order,
ambiguity, malformed input, and the color/font/logo checks.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESOLVE_SCRIPT = ROOT / "skills/diagram-design/scripts/brand_resolve.py"
CHECK_SCRIPT = ROOT / "skills/diagram-design/scripts/brand_check.py"
STYLE_GUIDE = ROOT / "skills/diagram-design/references/style-guide.md"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


resolve_mod = load_module(RESOLVE_SCRIPT, "diagram_design_brand_resolve")
check_mod = load_module(CHECK_SCRIPT, "diagram_design_brand_check")


def base_style_guide() -> str:
    """The pristine, committed style guide — never the (possibly dirty) working copy."""
    result = subprocess.run(
        ["git", "show", "HEAD:skills/diagram-design/references/style-guide.md"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout
    # Fallback for a checkout with no git history: use the working copy as-is.
    return STYLE_GUIDE.read_text(encoding="utf-8")


def fake_profile(slug: str, name: str, light_accent: str, dark_accent: str) -> str:
    """A fictitious profile derived from the shipped default, accent swapped."""
    body = base_style_guide()
    body = body.replace(
        "| `accent` | Focal / 1–2 max per diagram | `#bf4520` (burnt-tangerine) | `#f08a59` |",
        f"| `accent` | Focal / 1–2 max per diagram | `{light_accent}` | `{dark_accent}` |",
    )
    header = (
        "<!-- diagram-design-profile\n"
        f"name: {name}\n"
        f"slug: {slug}\n"
        "source-url: none\n"
        "created: 2026-01-01\n"
        "updated: 2026-01-01\n"
        "notes: fictitious test fixture\n"
        "-->\n"
    )
    return header + body


class Kit:
    """A throwaway $DIAGRAM_DESIGN_HOME."""

    def __init__(self, tmp: Path) -> None:
        self.home = tmp / "home"
        self.projects = tmp / "projects"
        self.home.mkdir()
        self.projects.mkdir()
        (self.home / "profiles").mkdir()
        (self.home / "brands").mkdir()

    def write_profile(self, slug: str, name: str, light_accent: str, dark_accent: str) -> None:
        (self.home / "profiles" / f"{slug}.md").write_text(
            fake_profile(slug, name, light_accent, dark_accent), encoding="utf-8"
        )

    def write_brand(self, slug: str, payload: dict) -> Path:
        brand_dir = self.home / "brands" / slug
        brand_dir.mkdir(parents=True, exist_ok=True)
        (brand_dir / "brand.json").write_text(json.dumps(payload), encoding="utf-8")
        return brand_dir

    def project_dir(self, name: str) -> Path:
        path = self.projects / name
        path.mkdir(parents=True, exist_ok=True)
        return path


def run_resolve(home: Path, argv: list[str]) -> tuple[int, str, str]:
    import contextlib
    import io

    old_home = os.environ.get("DIAGRAM_DESIGN_HOME")
    os.environ["DIAGRAM_DESIGN_HOME"] = str(home)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = resolve_mod.main(argv)
    finally:
        if old_home is None:
            os.environ.pop("DIAGRAM_DESIGN_HOME", None)
        else:
            os.environ["DIAGRAM_DESIGN_HOME"] = old_home
    return code, out.getvalue(), err.getvalue()


def run_check(home: Path, argv: list[str]) -> tuple[int, str, str]:
    import contextlib
    import io

    old_home = os.environ.get("DIAGRAM_DESIGN_HOME")
    os.environ["DIAGRAM_DESIGN_HOME"] = str(home)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check_mod.main(argv)
    finally:
        if old_home is None:
            os.environ.pop("DIAGRAM_DESIGN_HOME", None)
        else:
            os.environ["DIAGRAM_DESIGN_HOME"] = old_home
    return code, out.getvalue(), err.getvalue()


SVG_HEAD = (
    '<svg viewBox="0 0 400 200" xmlns="http://www.w3.org/2000/svg" role="img" '
    'aria-labelledby="t-title t-desc"><title id="t-title">T</title>'
    '<desc id="t-desc">T.</desc>'
)


def document(body: str) -> str:
    return f"<!DOCTYPE html><html><body>{SVG_HEAD}{body}</svg></body></html>"


def main() -> int:
    failures: list[str] = []

    def expect(label: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"OK: {label}")
        else:
            failures.append(f"{label}: {detail}")

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        kit = Kit(tmp)

        acme_dir = kit.project_dir("acme-site")
        labs_dir = kit.project_dir("acme-labs-site")
        marker_dir = kit.project_dir("globex-via-marker")
        nested_marker_dir = marker_dir / "nested" / "deeper"
        nested_marker_dir.mkdir(parents=True)
        bad_marker_dir = kit.project_dir("bad-marker")
        unresolved_dir = kit.project_dir("nobody")

        kit.write_profile("acme", "Acme Corp", "#aa0000", "#cc3333")
        kit.write_profile("acme-labs", "Acme Labs", "#0000aa", "#3333cc")
        kit.write_profile("globex", "Globex Corporation", "#00aa00", "#33cc33")

        kit.write_brand(
            "acme",
            {
                "slug": "acme",
                "name": "Acme Corp",
                "profile": "acme",
                "aliases": ["acme"],
                "paths": [str(acme_dir)],
                "logo": {"light": "logo-light.svg", "dark": "logo-dark.svg", "required": False},
            },
        )
        kit.write_brand(
            "acme-labs",
            {
                "slug": "acme-labs",
                "name": "Acme Labs",
                "profile": "acme-labs",
                "aliases": ["acme labs"],
                "paths": [str(labs_dir)],
                "logo": {"required": True},
            },
        )
        kit.write_brand(
            "globex",
            {
                "slug": "globex",
                "name": "Globex Corporation",
                "profile": "globex",
                "aliases": ["globex", "โกลเบ็กซ์"],
                "paths": [],
            },
        )
        # A real logo asset for acme, so logo-without-asset has a negative case too.
        (kit.home / "brands" / "acme" / "logo-light.svg").write_text("<svg/>", encoding="utf-8")
        (kit.home / "brands" / "acme" / "logo-dark.svg").write_text("<svg/>", encoding="utf-8")

        # --- resolution: request -------------------------------------------------
        code, out, _ = run_resolve(kit.home, ["--request", "draw the globex org chart", "--json"])
        expect("resolve from request picks globex", code == 0 and json.loads(out)["slug"] == "globex", out)

        # --- resolution: longer alias covers shorter one over the same span ------
        code, out, _ = run_resolve(kit.home, ["--request", "redo the acme labs rollout diagram", "--json"])
        expect(
            "longer alias 'acme labs' wins over 'acme'",
            code == 0 and json.loads(out)["slug"] == "acme-labs",
            out,
        )

        # --- resolution: two distinct brands in one request => ambiguous ---------
        code, out, err = run_resolve(kit.home, ["--request", "compare acme and globex architectures"])
        expect("two brands in one request is ambiguous (exit 3)", code == 3, f"code={code} err={err}")
        expect("ambiguous message names both brands", "acme" in err and "globex" in err, err)

        # --- resolution: no match anywhere => not resolved ------------------------
        code, out, err = run_resolve(
            kit.home, ["--request", "draw a generic flowchart", "--cwd", str(unresolved_dir)]
        )
        expect("no brand resolved anywhere (exit 2)", code == 2, f"code={code} err={err}")

        # --- resolution: cwd ------------------------------------------------------
        code, out, _ = run_resolve(kit.home, ["--cwd", str(acme_dir), "--json"])
        expect("resolve from cwd picks acme", code == 0 and json.loads(out)["slug"] == "acme", out)
        expect("resolve from cwd reports method=cwd", json.loads(out)["method"] == "cwd", out)

        # --- resolution: project marker -------------------------------------------
        (marker_dir / ".diagram-design").write_text("profile: globex\n", encoding="utf-8")
        code, out, _ = run_resolve(kit.home, ["--cwd", str(nested_marker_dir), "--json"])
        payload = json.loads(out)
        expect(
            "resolve from marker (walking up) picks globex",
            code == 0 and payload["slug"] == "globex" and payload["method"] == "marker",
            out,
        )

        # --- resolution: malformed marker is ignored, not matched ----------------
        (bad_marker_dir / ".diagram-design").write_text(
            "profile: globex\nextra line that breaks the grammar\n", encoding="utf-8"
        )
        code, out, err = run_resolve(kit.home, ["--cwd", str(bad_marker_dir)])
        expect("malformed marker is ignored (falls through to not-resolved)", code == 2, f"code={code} err={err}")

        # --- library: corrupted brand.json is skipped, not fatal ------------------
        broken_dir = kit.home / "brands" / "broken"
        broken_dir.mkdir()
        (broken_dir / "brand.json").write_text("{not valid json", encoding="utf-8")
        code, out, err = run_resolve(kit.home, ["--list", "--json"])
        slugs = {b["slug"] for b in json.loads(out)["brands"]}
        expect(
            "corrupted brand.json skipped, others still listed",
            code == 0 and slugs == {"acme", "acme-labs", "globex"},
            f"slugs={slugs} err={err}",
        )
        expect("corrupted brand.json warns on stderr", "broken" in err, err)

        # --- library: logo filename with '..' is rejected -------------------------
        evil_dir = kit.home / "brands" / "evil"
        evil_dir.mkdir()
        (evil_dir / "brand.json").write_text(
            json.dumps(
                {
                    "slug": "evil",
                    "name": "Evil Corp",
                    "profile": "acme",
                    "aliases": ["evil"],
                    "logo": {"light": "../../etc/passwd"},
                }
            ),
            encoding="utf-8",
        )
        code, out, err = run_resolve(kit.home, ["--request", "evil diagram", "--json"])
        payload = json.loads(out)
        expect(
            "logo path traversal rejected (no logo_files entry)",
            code == 0 and payload["slug"] == "evil" and payload["logo_files"] == {},
            out,
        )
        # Clean up the adversarial brand so it doesn't interfere with later checks.
        import shutil

        shutil.rmtree(evil_dir)
        shutil.rmtree(broken_dir)

        # ------------------------------------------------------------------
        # brand_check.py
        # ------------------------------------------------------------------

        def check(label: str, slug: str, html: str, expect_types: list[str]) -> None:
            with tempfile.TemporaryDirectory() as scratch:
                candidate = Path(scratch) / "candidate.html"
                candidate.write_text(html, encoding="utf-8")
                code, out, err = run_check(kit.home, [str(candidate), "--brand", slug, "--json"])
            payload = json.loads(out) if out.strip() else {}
            found_types = sorted(f["type"] for f in payload.get("findings", []))
            if found_types != sorted(expect_types):
                failures.append(f"{label}: expected {expect_types}, got {found_types} (err={err})")
            else:
                print(f"OK: {label}")

        clean = document(
            '<style>.n{fill:#aa0000;font-family:\'Instrument Serif\',serif;}</style>'
            '<rect x="10" y="10" width="100" height="40" fill="#aa0000"/>'
        )
        check("clean acme file has zero findings", "acme", clean, [])

        off_color = document('<rect x="10" y="10" width="100" height="40" fill="#123456"/>')
        check("unrecognized color flagged off-brand-color", "acme", off_color, ["off-brand-color"])

        other_brand_color = document('<rect x="10" y="10" width="100" height="40" fill="#00aa00"/>')
        check(
            "globex accent used in acme file flagged other-brand-color",
            "acme",
            other_brand_color,
            ["other-brand-color"],
        )

        off_font = document(
            '<text font-family="Comic Sans MS" x="10" y="20">hi</text>'
        )
        check("unrecognized font flagged off-brand-font", "acme", off_font, ["off-brand-font"])

        logo_excluded = document(
            '<g data-brand-logo="acme"><rect fill="#00aa00" width="10" height="10"/></g>'
        )
        check(
            "color inside data-brand-logo subtree is not scanned",
            "acme",
            logo_excluded,
            [],
        )

        wrong_logo = document('<g data-brand-logo="globex"><rect fill="#00aa00"/></g>')
        check("foreign data-brand-logo flagged logo-wrong-brand", "acme", wrong_logo, ["logo-wrong-brand"])

        no_asset = document('<g data-brand-logo="acme-labs"></g>')
        check(
            "data-brand-logo with no asset on disk flagged logo-without-asset",
            "acme-labs",
            no_asset,
            ["logo-without-asset"],
        )

        missing_logo = document('<rect x="10" y="10" width="100" height="40" fill="#0000aa"/>')
        check(
            "required logo absent flagged logo-missing",
            "acme-labs",
            missing_logo,
            ["logo-missing"],
        )

        with tempfile.TemporaryDirectory() as scratch:
            candidate = Path(scratch) / "candidate.html"
            candidate.write_text(clean, encoding="utf-8")
            code, out, err = run_check(kit.home, [str(candidate), "--brand", "acme"])
        expect("clean file: exit 0", code == 0, f"code={code} out={out} err={err}")

        with tempfile.TemporaryDirectory() as scratch:
            candidate = Path(scratch) / "candidate.html"
            candidate.write_text(off_color, encoding="utf-8")
            code, out, err = run_check(kit.home, [str(candidate), "--brand", "acme"])
        expect("findings: exit 1", code == 1, f"code={code} out={out} err={err}")

        code, out, err = run_check(kit.home, ["/no/such/file.html", "--brand", "acme"])
        expect("missing file: exit 2", code == 2, f"code={code} out={out} err={err}")

        code, out, err = run_check(kit.home, [str(acme_dir), "--brand", "no-such-brand"])
        # acme_dir is a directory, not a file, and the brand doesn't exist either way.
        expect("unknown brand: exit 2", code == 2, f"code={code} out={out} err={err}")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("All brand kit tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
