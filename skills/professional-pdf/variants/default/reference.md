# Professional PDF — Default Variant (GRIP CANONICAL)

**Parent skill:** [SKILL.md](../../SKILL.md)
**Config:** [`config.json`](./config.json) (variant-local, never read across variants)
**Operator:** *(unset — placeholder)*

This is the **canonical default** that ships with GRIP. When no `--variant`
flag is supplied, no operator-set selector is present, and `facts/identity.md`
does not match a known organisation override, the resolver loads THIS variant.

It is deliberately neutral so any individual or organisation can use it as-is
or fork it into their own variant without inheriting another operator's brand
identity.

---

## Design Philosophy

**Swiss minimalism — navy accent on warm white.**

Restraint, hierarchy, and a single accent. No logos in the chrome (text-only
brand mark in the page footer), no decorative borders that do not carry
information, no second accent colour. Restraint **is** the look.

Palette anchor: deep navy `#1e40af` against warm white `#fafafa`. The navy
appears once per page (H1 underline + H3 + accent borders); the rest is
near-black on warm white. Body text never competes with the accent for
attention.

The colour distinct-by-construction principle: this default's accent is
intentionally different from any organisation-specific variant shipped or
added under `variants/`, so a wrong-variant render is immediately visible.

---

## CSS Template

The CSS below uses placeholders that the resolver fills in from `config.json`.
Do not edit the placeholders' names — they are the contract between resolver
and template.

```css
:root {
  --brand-accent:     {{accent}};
  --brand-text:       {{text}};
  --brand-background: {{background}};
  --brand-border:     {{border}};
  --brand-surface:    {{surface}};
  --brand-muted:      {{muted}};
}

@page {
  size: A4;
  margin: 2cm 2cm 2.2cm 2cm;
  background: var(--brand-background);

  @top-center {
    content: "{{document_title}} · {{year}}";
    font-family: {{heading_font}};
    font-size: 8pt;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--brand-muted);
  }
  @bottom-left {
    content: counter(page);
    font-family: {{heading_font}};
    font-size: 9pt;
    color: var(--brand-muted);
  }
  @bottom-right {
    content: "{{company_name}}{{footer_separator}}{{year}}";
    font-family: {{heading_font}};
    font-size: 9pt;
    color: var(--brand-muted);
  }
}

@page :first {
  margin: 0;
  @top-center { content: none; }
  @bottom-left { content: none; }
  @bottom-right { content: none; }
}

body {
  margin: 0; /* browser default 8px would inset a full-bleed cover from the sheet edge (failure class 1) */
  font-family: {{body_font}};
  font-size: {{body_size}};
  line-height: {{body_line_height}};
  color: var(--brand-text);
  background: var(--brand-background);
}

h1, h2, h3 {
  display: block;
  page-break-after: avoid;
  break-after: avoid-page;
  page-break-inside: avoid;
  font-family: {{heading_font}};
}
h2 + *, h3 + * {
  page-break-before: avoid;
  break-before: avoid-page;
}

h1 {
  font-size: {{h1_size}};
  font-weight: {{h1_weight}};
  letter-spacing: {{h1_tracking}};
  padding-bottom: 0.4rem;
  border-bottom: 4px solid var(--brand-accent);
  margin-bottom: 1.2rem;
}

h2 {
  font-size: {{h2_size}};
  font-weight: {{h2_weight}};
  margin-top: 1.8rem;
}

h3 {
  font-size: {{h3_size}};
  font-weight: 700;
  color: var(--brand-accent);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-top: 1.4rem;
}

code, pre {
  font-family: {{mono_font}};
  font-size: {{code_size}};
}

pre {
  background: var(--brand-surface);
  border: 1px solid var(--brand-border);
  border-left: 3px solid var(--brand-accent);
  padding: 0.7rem 0.9rem;
  page-break-inside: avoid;
  break-inside: avoid;
}

table, tr, thead, tbody {
  page-break-inside: avoid;
  break-inside: avoid;
}

table {
  border-collapse: collapse;
  width: 100%;
  margin: 1rem 0;
}

th, td {
  border: 1px solid var(--brand-border);
  padding: 0.5rem 0.7rem;
  text-align: left;
  font-size: {{body_size}};
}

th {
  background: var(--brand-surface);
  font-weight: 700;
}

blockquote {
  border-left: 3px solid var(--brand-accent);
  background: var(--brand-surface);
  padding: 0.6rem 0.9rem;
  margin: 1rem 0;
  page-break-inside: avoid;
  break-inside: avoid;
  color: var(--brand-muted);
}

p { orphans: 3; widows: 3; }

.cover {
  width: 210mm;
  height: 297mm;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  page-break-after: always;
  background: var(--brand-background);
  padding: 2.2cm;
  box-sizing: border-box;
}

.cover h1 {
  font-size: 42pt;
  letter-spacing: -0.035em;
  border-bottom: 6px solid var(--brand-accent);
  padding-bottom: 0.6rem;
}

.cover .meta {
  font-size: 11pt;
  color: var(--brand-muted);
  letter-spacing: 0.04em;
}

@media print {
  .footer { display: none; }
}
```

---

## Falsification

This variant is wrong if, over the next ten PDFs generated from it,
operators report:

- The navy accent is harder to read on warm white than the contrast ratio
  suggests (WCAG AA still measurable, but operator-perceived legibility is
  worse than alternative palettes).
- The placeholder `Your Organisation` text gets shipped to a real recipient
  uncustomised (UX: the resolver should refuse to render until `company_name`
  is set away from the placeholder).
- A consumer mistakes this variant for an operator-specific brand (the
  visual distinction from organisation-specific variants is too weak).

Track via the audit log (`state/pdf-audit-log.jsonl`, planned).

---

## Customising

If this default's palette or typography does not suit your work, **do not
edit this file in place** — fork it. The official extension pattern is in
the parent [SKILL.md](../../SKILL.md) "Adding your own variant" section:
copy this directory to `variants/<your-name>/`, edit, set as default with
`python3 lib/pdf_branding.py set-default <your-name>`.

Editing the default in place is fine for one operator's local tree but
breaks reproducibility for every other GRIP user — the canonical default
is meant to stay canonical.
