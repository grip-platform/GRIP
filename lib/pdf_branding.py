"""PDF branding variant resolver — cross-client isolation by construction.

Origin: 2026-05-20, task #1881. Before this shim, `/generate-pdf` read a
single global `~/.claude/config/pdf-branding.json` that held the operator's
last-configured brand. A `/configure-pdf` run for client A persisted A's
branding to that file; a subsequent `/generate-pdf` for client B silently
inherited it. Cross-client identity leak.

This shim makes the leak structurally impossible:

  - Each variant is fully self-contained at
    `skills/professional-pdf/variants/<name>/config.json` +
    `skills/professional-pdf/variants/<name>/reference.md`.
  - The global selector file (`~/.claude/config/pdf-branding.json`) holds
    EXACTLY ONE field — `default_variant: <name>`. It carries no brand
    values. There is no path by which a brand colour or wordmark from one
    variant can be read while resolving another.
  - `--variant <name>` overrides the selector; the selector is consulted
    only when `--variant` is absent.
  - If neither is set, the resolver reads `facts/identity.md` and picks
    a variant from `IDENTITY_DEFAULT_ROUTES` based on the operator's email
    domain. A CodeTonight operator gets the `codetonight` brand, every
    other operator gets the neutral `default` canonical that ships with
    GRIP.
  - Hard fallback: missing identity, missing selector, missing override →
    `CANONICAL_DEFAULT` (`default`). Never raises silently to a
    wrong-brand state.

Syscall doctrine: this is the one shim every PDF-branding caller passes
through. There is no second path to a variant. (See
`rules/syscall-doctrine.md`.)

Correct-by-construction (Task #1881 forward-only fix, 2026-05-24): every
new GRIP user/org adds their variant under `variants/<name>/`; identity
routing decides the default; client-NDA variants stay machine-local via
`.gitignore`. The resolver never assumes a directory exists, so an absent
local variant cleanly degrades to the next resolution step rather than
producing a wrong-brand render.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Tuple

CLAUDE_DIR = Path(os.environ.get("CLAUDE_DIR", str(Path.home() / ".claude")))
SELECTOR_PATH = CLAUDE_DIR / "config" / "pdf-branding.json"
VARIANTS_DIR = CLAUDE_DIR / "skills" / "professional-pdf" / "variants"
IDENTITY_PATH = CLAUDE_DIR / "facts" / "identity.md"
CANONICAL_DEFAULT = "default"

# Email-domain → variant routing for the identity-aware default. Operators
# whose `facts/identity.md` `email:` field matches one of these suffixes
# get the named variant as their default; everyone else gets
# CANONICAL_DEFAULT. Suffix-based, case-insensitive — so
# `development@codetonight.co.za` and `name@subdomain.codetonight.co.za` both
# resolve to `codetonight`. Add a new entry here when an org wants its
# operators to get its variant by default; the org's variant directory
# under `variants/` is the load-bearing artefact.
IDENTITY_DEFAULT_ROUTES: "dict[str, str]" = {
    "@codetonight.co.za": "codetonight",
}

# Fields the selector file is allowed to carry. Anything else is ignored on
# read and rejected on write — this is the cross-bleed defence.
SELECTOR_ALLOWED_KEYS = frozenset({"default_variant"})

# Email line in facts/identity.md. Tolerant of quoting and whitespace.
_EMAIL_LINE_RE = re.compile(
    r'''^\s*email\s*:\s*["']?([^"'\s]+)["']?\s*$''',
    re.MULTILINE,
)


class PdfBrandingError(Exception):
    """Raised when a variant is unknown or its config is malformed."""


def _detect_operator_default() -> str:
    """Pick a default variant from the operator's email domain.

    Reads `facts/identity.md` for an `email:` field (per-contributor,
    gitignored). If the address matches a known suffix in
    `IDENTITY_DEFAULT_ROUTES`, returns the matched variant; otherwise
    returns `CANONICAL_DEFAULT`. Returns `CANONICAL_DEFAULT` whenever the
    file is missing, unreadable, has no parseable email, or names a
    variant that does not exist on disk — the leak-safe behaviour.

    Match is case-insensitive and suffix-based. An operator does not have
    to do anything to receive the right default: a real
    `@codetonight.co.za` email lands on `codetonight`, anyone else
    lands on `default`.
    """
    try:
        text = IDENTITY_PATH.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return CANONICAL_DEFAULT
    match = _EMAIL_LINE_RE.search(text)
    if not match:
        return CANONICAL_DEFAULT
    email = match.group(1).strip().lower()
    for suffix, variant in IDENTITY_DEFAULT_ROUTES.items():
        if email.endswith(suffix.lower()):
            if (VARIANTS_DIR / variant).is_dir():
                return variant
            # Identity route names a variant the local tree doesn't have
            # (e.g. an org-specific variant on a fresh clone) — fall back
            # rather than raise; the canonical default is always present.
            return CANONICAL_DEFAULT
    return CANONICAL_DEFAULT


def _read_selector_default() -> str:
    """Return the operator's chosen default variant id, falling through to
    identity-routing and finally `CANONICAL_DEFAULT`.

    Resolution order inside this function:

      1. selector file `default_variant` (operator's persisted choice)
      2. identity-aware route via `_detect_operator_default()`
      3. `CANONICAL_DEFAULT`

    The selector file is consulted ONLY for the `default_variant` field. If
    the file is missing, unreadable, malformed, or carries a `default_variant`
    that does not resolve to an existing variant directory, this function
    falls through — never raises.
    """
    try:
        raw = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _detect_operator_default()
    if not isinstance(raw, dict):
        return _detect_operator_default()
    name = raw.get("default_variant")
    if not isinstance(name, str) or not name:
        return _detect_operator_default()
    if not (VARIANTS_DIR / name).is_dir():
        return _detect_operator_default()
    return name


def _load_variant_config(variant_dir: Path, name: str) -> dict:
    """Load + validate a variant's config.json. Never substitutes across variants."""
    config_path = variant_dir / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PdfBrandingError(
            f"Variant '{name}' has no config.json at {config_path}. "
            f"Cross-variant config substitution is forbidden — fix this variant, "
            f"do not fall through to another."
        ) from exc
    except json.JSONDecodeError as exc:
        raise PdfBrandingError(
            f"Variant '{name}' config.json is malformed: {exc}. "
            f"Refusing to fall back to another variant (leak prevention)."
        ) from exc
    if not isinstance(config, dict):
        # Valid JSON is not necessarily a config: `[1,2,3]` and `"hello"` both
        # parse, and every consumer below calls `.get`/`[...]` on the result.
        # This is the PRIMARY shape check, so no consumer reached through
        # `resolve()` has to repeat it (AGORA #4342; reproduced -- a list
        # config.json leaked `AttributeError: 'list' object has no attribute
        # 'get'`). `_brand_values` keeps its own guard deliberately: it is a
        # module-level function reachable by direct call, so it cannot rely on
        # having come through here. Defence in depth, not a contradiction
        # (AGORA #4344).
        raise PdfBrandingError(
            f"Variant '{name}' config.json must contain a JSON object, got "
            f"{type(config).__name__}. Refusing to fall back to another "
            f"variant (leak prevention)."
        )
    declared = config.get("variant_id")
    if declared != name:
        raise PdfBrandingError(
            f"Variant '{name}' config.json declares variant_id={declared!r}, "
            f"directory name is '{name}'. Cross-bleed defence: refusing to "
            f"resolve. Update variant_id in {config_path} or move the file."
        )
    return config


def resolve(variant: str | None = None) -> Tuple[dict, Path]:
    """Resolve `(config_dict, reference_md_path)` for a variant.

    Order: explicit `variant` arg → selector `default_variant` → CANONICAL_DEFAULT.
    Brand values come EXCLUSIVELY from the chosen variant's own config.json.
    Selector keys other than `default_variant` are ignored — cross-bleed is
    structurally impossible.
    """
    name = variant or _read_selector_default()
    variant_dir = VARIANTS_DIR / name
    if not variant_dir.is_dir():
        known = ", ".join(sorted(p.name for p in VARIANTS_DIR.iterdir() if p.is_dir()))
        raise PdfBrandingError(
            f"Unknown PDF variant '{name}'. Available: {known}. "
            f"Create a new variant by adding skills/professional-pdf/variants/{name}/ "
            f"with config.json and reference.md."
        )
    return _load_variant_config(variant_dir, name), variant_dir / "reference.md"


def render_stylesheet(document_title: str, year: str | int,
                      variant: str | None = None) -> str:
    """Return the variant's CSS template with its brand values substituted in.

    Exists because reading ``config.json`` and writing your own CSS from those
    values LOOKS equivalent and is not. The template carries a dozen empirical
    bug-fixes the brand values know nothing about -- page chrome in
    ``@page @bottom-*``, its suppression on ``@page :first``, a defensive
    ``.footer { display: none; }``. Hand-rolling from the config discards every
    one and re-earns the bugs (observed 2026-08-01: a milestone PDF shipped
    with a footer floating mid-page despite a page-by-page audit).

    So substitution is mechanical here, and an unresolved placeholder raises
    rather than reaching WeasyPrint as a literal ``{{muted}}``.
    """
    config, reference = resolve(variant)
    css = _extract_css_block(reference)
    values = _brand_values(config, document_title, year)
    for key, value in values.items():
        css = css.replace("{{" + key + "}}", str(value))
    _assert_every_placeholder_resolved(css, reference, values)
    return css.strip()


def _extract_css_block(reference: Path) -> str:
    """The ```css block from a variant's reference.md, or raise.

    Never degrades to empty CSS, and never borrows another variant's block:
    an unstyled PDF still looks "generated" and would ship, and a borrowed
    block would render this variant's content in someone else's brand --
    "wrong-brand-render is worse than no-render" (SKILL.md).

    A delta variant that documents its CSS as "inherited" therefore is NOT
    renderable here until it carries its own parameterised block. The
    skill-root reference.md cannot serve as the fallback: it is a concrete
    stylesheet with zero ``{{placeholders}}``, so substituting into it would
    silently emit the generic brand. Known case: ``codetonight``.
    """
    text = reference.read_text(encoding="utf-8")
    if "```css" not in text:
        raise PdfBrandingError(
            f"{reference} carries no ```css block -- cannot render a stylesheet. "
            f"The variant's CSS template is the artefact that prevents known "
            f"layout defects; refusing to fall back to hand-rolled CSS. "
            f"If this is a delta variant that documents its CSS as inherited, "
            f"give it its own ```css block (clone the default variant's and "
            f"re-brand the placeholders) -- borrowing another variant's block "
            f"would render this content in the wrong brand."
        )
    return text.split("```css", 1)[1].split("```", 1)[0]


def _brand_values(config: object, document_title: str, year: str | int) -> dict:
    """Flatten a variant config into the template's placeholder namespace.

    A config missing an expected section raises ``PdfBrandingError`` naming the
    section, not a bare ``KeyError``. Everything else in this module fails with
    that one typed error, so a caller that catches it would otherwise still be
    killed by an untyped escape from here (AGORA #4340, reproduced: a config
    without ``colours`` raised ``KeyError: 'colours'``).
    """
    try:
        return {
            **config["colours"],
            **config["scale"],
            "heading_font": config["fonts"]["heading"],
            "body_font": config["fonts"]["body"],
            "mono_font": config["fonts"]["mono"],
            "company_name": config["company_name"],
            "footer_separator": config["footer_separator"],
            "document_title": document_title,
            "year": str(year),
        }
    except (KeyError, TypeError) as exc:
        # The handler must not assume the very thing whose failure it is
        # handling. `config.get` would raise an UNCAUGHT AttributeError when
        # config is None or otherwise not a dict -- reintroducing, inside the
        # fix, the untyped escape this function exists to prevent
        # (AGORA #4341, Speaker C, reproduced for None / str / int).
        variant_id = config.get("variant_id", "?") if isinstance(config, dict) else "?"
        raise PdfBrandingError(
            f"Variant '{variant_id}' config.json is missing or "
            f"malformed at {exc}. A stylesheet needs colours, scale, fonts "
            f"(heading/body/mono), company_name and footer_separator."
        ) from exc


def _assert_every_placeholder_resolved(css: str, reference: Path, values: dict) -> None:
    """Raise if ANY ``{{...}}`` survives substitution.

    Deliberately matches any token, not just ``lowercase_underscore`` names. A
    guard narrower than the thing it guards is worse than none: ``{{BodyFont}}``
    or ``{{color2}}`` is neither substituted (no such config key) NOR reported,
    so it reaches the PDF as a literal -- exactly the silent failure this check
    exists to stop (AGORA #4336, Speaker C, quote-verified).
    """
    leftover = sorted(set(re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", css)))
    if not leftover:
        return
    raise PdfBrandingError(
        f"Unresolved placeholder(s) in {reference}: {', '.join(leftover)}. "
        f"The variant config supplies {sorted(values)}. A literal "
        f"'{{{{{leftover[0]}}}}}' in the stylesheet silently breaks that rule "
        f"in the rendered PDF."
    )


def list_variants() -> list[str]:
    """Enumerate available variant ids (directories under variants/)."""
    if not VARIANTS_DIR.is_dir():
        return []
    return sorted(p.name for p in VARIANTS_DIR.iterdir() if p.is_dir())


def set_default_variant(name: str) -> None:
    """Persist `name` as the operator's default variant in the selector file.

    Writes ONLY `{"default_variant": name}` — any prior brand fields in the
    selector file are deliberately discarded (legacy leak surface). Use this
    instead of editing the file directly.
    """
    if name not in list_variants():
        raise PdfBrandingError(
            f"Cannot set default to unknown variant '{name}'. "
            f"Known: {', '.join(list_variants())}."
        )
    SELECTOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    SELECTOR_PATH.write_text(
        json.dumps({"default_variant": name}, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PDF branding variant resolver")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="List available variants")
    p_resolve = sub.add_parser("resolve", help="Resolve a variant (or default)")
    p_resolve.add_argument("--variant", help="Explicit variant id; omit to use default")
    p_set = sub.add_parser("set-default", help="Persist default variant in selector")
    p_set.add_argument("name", help="Variant id")
    args = parser.parse_args()

    if args.cmd == "list":
        for v in list_variants():
            print(v)
    elif args.cmd == "resolve":
        cfg, ref = resolve(args.variant)
        print(json.dumps({
            "variant_id": cfg.get("variant_id"),
            "company_name": cfg.get("company_name"),
            "reference": str(ref),
        }, indent=2))
    elif args.cmd == "set-default":
        set_default_variant(args.name)
        print(f"default_variant → {args.name}")
