#!/usr/bin/env python3
"""Regenerate the monochrome (e-ink) utility overrides inside src/app.css.

The `bw` / `bw-dark` themes render pure ink on paper with no grey tones, so the
grey families collapse to the two palette colours: `gray-*` via the ramp
override in app.css, and the other grey families in use (e.g. `neutral-*`) via
this script. Tailwind's *hue* utilities (sky, red, emerald, ...) would still
paint colour, so this script scans the source for the hue utilities actually in
use and rewrites the block between the `@mono:begin` / `@mono:end` markers.

    python3 scripts/generate-mono-css.py [--check]

Policy per utility family:

    text-/fill-/stroke-/border-/ring-/...  -> var(--app-fg)
        Reads as monochrome. Matching on a substring is deliberate: it also
        covers `hover:` / `focus:` / `dark:` variants, and since the forced
        value is exactly what monochrome text and borders want anyway, the
        variant state is irrelevant.

    bg-<hue>  tint (50/100, or opacity <= 35%)
        -> transparent. These are washes behind text; an opaque block would
           swallow the glyphs they label. Again substring-matched.

    bg-<hue>  solid (opacity >= 40%)
        -> var(--app-fg). These are status dots, badges and filled buttons, so
           collapsing them to transparent would erase meaning. Here the full
           token is kept, variant included, so that `hover:bg-emerald-600` stays
           a hover state instead of becoming permanent.

Utilities whose variant has no CSS equivalent (media queries, `peer-*`,
arbitrary variants) are reported and skipped rather than guessed at.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent
SRC = FRONTEND / "src"
APP_CSS = SRC / "app.css"

HUES = (
    "neutral|sky|blue|red|green|amber|yellow|orange|emerald|teal|cyan|indigo|violet|"
    "purple|fuchsia|pink|rose|lime"
)
SHADES = "50|100|200|300|400|500|600|700|800|900|950"

# family -> CSS declaration that carries the colour
FAMILIES = {
    "text": "color",
    "placeholder": "color",
    "caret": "color",
    "accent": "color",
    "decoration": "color",
    "fill": "fill",
    "stroke": "stroke",
    "border": "border-color",
    "outline": "outline-color",
    "ring": "--tw-ring-color",
    "divide": "border-color",
}

# `[hover:][focus:][group-hover:]bg-red-500/45`, scanned straight out of the
# source text so that utilities nested in template literals are found too.
FAMILY_PATTERN = "|".join([*FAMILIES, "bg"])
TOKEN = re.compile(
    rf"(?<![A-Za-z0-9-])(?P<variants>(?:[A-Za-z0-9-]+:)+)?"
    rf"(?P<full>(?P<family>{FAMILY_PATTERN})-(?P<hue>{HUES})-(?P<shade>{SHADES})"
    rf"(?P<opacity>/\d{{1,3}})?)(?![A-Za-z0-9-])"
)

# Variants that map onto a CSS state keyword.
STATE = {
    "hover": ":hover",
    "focus": ":focus",
    "focus-visible": ":focus-visible",
    "focus-within": ":focus-within",
    "active": ":active",
    "disabled": ":disabled",
    "checked": ":checked",
    "open": "[open]",
    "target": ":target",
}

TINT_SHADES = {"50", "100"}
TINT_OPACITY = 35


def util_tokens(text: str) -> set[str]:
    """Every hue utility in the file, variant prefixes included."""
    out: set[str] = set()
    for match in TOKEN.finditer(text):
        out.add(f"{match.group('variants') or ''}{match.group('full')}")
    return out


def collect() -> tuple[dict[str, set[str]], set[str], dict[str, str], dict[str, str]]:
    """Return (fg selectors by declaration, tint selectors, bg selectors, skipped)."""
    fg: dict[str, set[str]] = {}
    tint: set[str] = set()
    bg: dict[str, str] = {}
    skipped: dict[str, str] = {}

    for path in sorted([*SRC.rglob("*.svelte"), *SRC.rglob("*.ts")]):
        if "node_modules" in path.parts:
            continue
        for token in util_tokens(path.read_text(encoding="utf-8", errors="ignore")):
            match = TOKEN.match(token.lstrip(":"))
            if not match:
                continue
            family = match.group("family")
            shade = match.group("shade")
            base = match.group("full")
            opacity = int(match.group("opacity")[1:]) if match.group("opacity") else 100

            if family != "bg":
                selector = f"[class*='{base}']"
                if family == "divide":
                    selector += " > :not(:last-child)"
                fg.setdefault(FAMILIES[family], set()).add(selector)
                continue

            if shade in TINT_SHADES or opacity <= TINT_OPACITY:
                tint.add(f"[class*='{base}']")
                continue

            # Solid background: keep the variant so the state stays a state.
            raw = match.group("variants") or ""
            prefix, state, supported = "", "", True
            for variant in raw.rstrip(":").split(":") if raw else []:
                if variant == "dark":
                    # `dark:` is inert in monochrome: no .dark class is set.
                    supported = False
                    break
                if variant == "group-hover":
                    prefix += ":where(.group:hover) "
                    continue
                if variant in STATE:
                    state += STATE[variant]
                    continue
                supported = False
                break
            if supported:
                bg[f"{prefix}[class~='{token}']{state}"] = "background-color"
            else:
                skipped[token] = str(path.relative_to(SRC))

    return fg, tint, bg, skipped


def group(selectors: list[str], declaration: str, value: str, comment: str) -> str:
    body = ",\n".join(f"\t{selector}" for selector in sorted(selectors))
    return f"/* {comment} */\n.mono :where(\n{body}\n) {{\n\t{declaration}: {value};\n}}\n"


def build(fg: dict[str, set[str]], tint: set[str], bg: dict[str, str]) -> str:
    parts = [
        group(
            list(selectors),
            declaration,
            "var(--app-fg)",
            f"Coloured {declaration} reads as monochrome.",
        )
        for declaration, selectors in sorted(fg.items())
    ]
    if tint:
        parts.append(
            group(
                list(tint),
                "background-color",
                "transparent",
                "Washes behind text collapse so the glyphs they label stay legible.",
            )
        )
    if bg:
        parts.append(
            "/* Filled accents (status dots, badges, filled buttons) stay visible.\n"
            "   The whole utility is matched, so `hover:`/`focus:` stay states. */\n"
            + "\n".join(
                f".mono :where({selector}) {{\n\tbackground-color: var(--app-fg);\n}}"
                for selector in sorted(bg)
            )
            + "\n"
        )
    return "\n".join(parts)


def main() -> int:
    fg, tint, bg, skipped = collect()
    body = build(fg, tint, bg)

    css = APP_CSS.read_text(encoding="utf-8")
    begin, end = "/* @mono:begin */", "/* @mono:end */"
    if begin not in css or end not in css:
        print(f"markers {begin} / {end} missing from {APP_CSS}", file=sys.stderr)
        return 1
    head, rest = css.split(begin, 1)
    _, tail = rest.split(end, 1)
    existing = rest.split(end, 1)[0]

    if "--check" in sys.argv:
        if existing.strip() != body.strip():
            print("app.css monochrome block is stale", file=sys.stderr)
            return 1
        print("monochrome block is up to date")
        return 0

    APP_CSS.write_text(f"{head}{begin}\n{body}\n{end}{tail}", encoding="utf-8")

    print(f"fg rules: {sum(len(v) for v in fg.values())}")
    print(f"tints collapsed: {len(tint)}")
    print(f"solid accents kept: {len(bg)}")
    if skipped:
        print(f"skipped (no CSS equivalent for the variant): {len(skipped)}")
        for token, path in sorted(skipped.items()):
            print(f"    {token}  ({path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
