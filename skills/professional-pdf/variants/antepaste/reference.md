# Professional PDF — Antepaste Variant

**Parent skill:** [SKILL.md](../../SKILL.md)
**Config:** [`config.json`](./config.json) (variant-local, never read across variants)
**Operator:** *(unset — the record names its audience in the footer, not here)*

The brand of one product: **Antepaste**, the paste-then-step-back clipboard
tool built on 2026-09-11. This variant exists for the documents that record
that product — first of all *The Antepaste Record*. It is cloned from the
canonical default (never from an operator-specific variant) and carries its
own full CSS block, so it renders without borrowing anyone else's brand.

Pass it per invocation as `--variant antepaste`; it is never set as the
default.

---

## Design Philosophy

**Swiss minimalism — deep teal accent on warm white.**

The accent is `#0F766E`, a deep teal: the colour of the step-back register
(the moment after a paste when the older entry is still reachable). It is
distinct by construction from GRIP navy `#1e40af` and CodeTonight orange
`#ea580c`, so a wrong-variant render is visible at a glance.

The name mark is text only: `↑ Antepaste`, the U+2191 arrow set in the mono
face at `h1_size`. No icon file, nothing binary to leak. A `.chord` span
sets a key chord (`⌘V` then `↑`) in the mono face with a one-pixel accent
rule beneath it, so the product's one gesture reads the same way everywhere
in the document.

Everything else follows the default: one accent per page, near-black on
warm white, no decorative borders that carry no information.

---

## CSS Template

The CSS below uses placeholders that the resolver fills in from `config.json`.
Do not edit the placeholders' names — they are the contract between resolver
and template. Every rule beyond the default's is a WeasyPrint guard for the
record's own components (cards, ledger rows, a landscape figure page, the
provenance block), not decoration.

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
    content: "{{company_name}} — the record{{footer_separator}}{{year}}{{footer_separator}}for V>> only";
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

@page landscape {
  size: A4 landscape;
  margin: 1.6cm 1.6cm 1.8cm 1.6cm;
}

body {
  margin: 0;
  font-family: {{body_font}};
  font-size: {{body_size}};
  line-height: {{body_line_height}};
  color: var(--brand-text);
  background: var(--brand-background);
}

h1, h2, h3, h4 {
  display: block;
  page-break-after: avoid;
  break-after: avoid-page;
  page-break-inside: avoid;
  font-family: {{heading_font}};
}
h2 + *, h3 + *, h4 + * {
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
  page-break-before: always;
  break-before: page;
}
h2.flow { page-break-before: auto; break-before: auto; }

h3 {
  font-size: {{h3_size}};
  font-weight: 700;
  color: var(--brand-accent);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-top: 1.4rem;
}

h4 {
  font-size: {{h4_size}};
  font-weight: 700;
  margin-top: 1rem;
  margin-bottom: 0.3rem;
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
  white-space: pre-wrap;
  word-wrap: break-word;
}

table, tr, thead, tbody {
  page-break-inside: avoid;
  break-inside: avoid;
}

table {
  border-collapse: collapse;
  width: 100%;
  margin: 1rem 0;
  font-variant-numeric: tabular-nums;
}

th, td {
  border: 1px solid var(--brand-border);
  padding: 0.45rem 0.6rem;
  text-align: left;
  font-size: 9.5pt;
  vertical-align: top;
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
  color: var(--brand-text);
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

.namemark {
  font-family: {{mono_font}};
  font-size: {{h1_size}};
  font-weight: 700;
  color: var(--brand-accent);
  letter-spacing: -0.02em;
}

.chord {
  font-family: {{mono_font}};
  font-size: {{code_size}};
  border-bottom: 1px solid var(--brand-accent);
  padding: 0 0.15em;
  white-space: nowrap;
}

.card {
  border: 1px solid var(--brand-border);
  border-left: 3px solid var(--brand-accent);
  background: var(--brand-surface);
  padding: 0.6rem 0.9rem;
  margin: 0.8rem 0;
  page-break-inside: avoid;
  break-inside: avoid;
}
.card .hd {
  font-family: {{mono_font}};
  font-size: {{code_size}};
  color: var(--brand-muted);
  margin-bottom: 0.3rem;
}
.card .catch {
  font-weight: 700;
}

.row { page-break-inside: avoid; break-inside: avoid; }

.landscape { page: landscape; page-break-before: always; break-before: page; }

figure {
  margin: 1rem 0;
  page-break-inside: avoid;
  break-inside: avoid;
}
figure svg { max-width: 100%; height: auto; }
figcaption {
  font-size: 9pt;
  color: var(--brand-muted);
  margin-top: 0.35rem;
}

.provenance {
  page-break-before: avoid;
  break-before: avoid-page;
  page-break-inside: avoid;
  break-inside: avoid;
  border-top: 2px solid var(--brand-accent);
  padding-top: 0.6rem;
  margin-top: 1.4rem;
  font-size: 9.5pt;
}

.unverified {
  border: 1px dashed var(--brand-muted);
  padding: 0.5rem 0.8rem;
  margin: 0.6rem 0;
  page-break-inside: avoid;
  break-inside: avoid;
}

.muted { color: var(--brand-muted); }
.tldr li { margin-bottom: 0.25rem; }

@media print {
  .footer { display: none; }
}
```

---

## Falsification

This variant is wrong if, over the next ten PDFs generated from it,
operators report:

- The teal accent reads as green-on-grey rather than as a distinct register
  (then shift toward `#115E59` and re-measure the WCAG AA ratio).
- The mono name mark is mistaken for code (then set it in the heading face
  with the arrow alone in mono).
- A reader confuses an Antepaste-branded record with a GRIP or CodeTonight
  deliverable (the distinction from those accents is too weak).

Track via the audit log (`state/pdf-audit-log.jsonl`, planned).

---

## Customising

Do not edit this file in place for a different product — fork it. The
official extension pattern is in the parent [SKILL.md](../../SKILL.md)
"Adding your own variant" section.
