# Professional PDF - Reference Material

**Parent:** [SKILL.md](./SKILL.md)

---

## Configuration Placeholders

Replace these placeholders with values from `~/.claude/config/pdf-branding.json`:

| Placeholder | Config Key | Default |
|-------------|-----------|---------|
| `{{COMPANY_NAME}}` | company_name | "Your Company" |
| `{{ACCENT_COLOUR}}` | colours.accent | #ea580c |
| `{{TEXT_COLOUR}}` | colours.text | #1a1a1a |
| `{{BG_COLOUR}}` | colours.background | #EAEAEA |
| `{{BORDER_COLOUR}}` | colours.border | #d1d5db |
| `{{FOOTER_TEXT}}` | footer | "{{COMPANY_NAME}}" |

---

## Complete CSS Template (Hardened — passes UI Audit)

This template is the empirical-bug-fix-proven version. Any deviation that
re-introduces `display: inline-block` on headings, removes
`page-break-inside: avoid` from tables, or replaces `@page` rules with a
standalone `.footer` div WILL re-introduce known failure classes. Don't.

```css
/* ─── PAGE CHROME via @page rules (NOT a standalone .footer div) ───── */
@page {
  size: A4;
  margin: 2cm 2cm 2.2cm 2cm;
  /* FULL-BLEED page background (failure class 6). Root-element (html/body)
     background does NOT reliably propagate into the @page margin area in
     WeasyPrint — it paints only the content box, leaving white gutters
     around an inset grey panel. Setting background on @page itself paints
     the whole sheet edge-to-edge. NEVER rely on body background alone for
     a non-white page colour. */
  background: {{BG_COLOUR}};
  @top-center {
    content: "DOCUMENT_TITLE_PLACEHOLDER · YEAR_PLACEHOLDER";
    font-family: 'JetBrains Mono', 'SF Mono', monospace;
    font-size: 7pt;
    letter-spacing: 0.15em;
    color: #999;
    text-transform: uppercase;
  }
  @bottom-left {
    content: counter(page);
    font-family: 'JetBrains Mono', 'SF Mono', monospace;
    font-size: 8pt;
    color: #666;
  }
  @bottom-right {
    content: "{{COMPANY_NAME}} · YEAR_PLACEHOLDER";
    font-family: 'JetBrains Mono', 'SF Mono', monospace;
    font-size: 7pt;
    letter-spacing: 0.1em;
    color: #999;
  }
}

/* Cover page is the first @page — full bleed, no chrome */
@page :first {
  margin: 0;
  @top-center { content: none; }
  @bottom-left { content: none; }
  @bottom-right { content: none; }
}

/* WeasyPrint does NOT support @page :last selector — do not use it.
   The brand text on every page (via @bottom-right above) is the canonical
   pattern. If you want a different-looking last page, use a body-level
   element on the last content section, NOT @page :last. */

* { box-sizing: border-box; }

html, body {
  background-color: {{BG_COLOUR}};
  min-height: 100%;
  margin: 0;
  padding: 0;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 11pt;
  line-height: 1.6;
  color: {{TEXT_COLOUR}};
  max-width: 100%;
}

/* Headings: display: block is MANDATORY (NOT inline-block — that silently
   breaks page-break-after:avoid. Full-width underline is also more Swiss). */
h1, h2, h3, h4 {
  display: block;
  page-break-after: avoid;
  break-after: avoid-page;
  page-break-inside: avoid;
}
h2 + *, h3 + * {
  page-break-before: avoid;
  break-before: avoid-page;
}

h1 {
  font-size: 28pt;
  font-weight: 800;
  letter-spacing: -0.03em;
  line-height: 1.1;
  margin: 0 0 0.5em 0;
  padding-bottom: 0.3em;
  border-bottom: 3px solid {{ACCENT_COLOUR}};
  color: #000;
}

h2 {
  font-size: 16pt;
  font-weight: 700;
  letter-spacing: -0.02em;
  margin: 2em 0 0.5em 0;
  padding-bottom: 0.3em;
  border-bottom: 1px solid {{BORDER_COLOUR}};
  color: #000;
}

h3 {
  font-size: 12pt;
  font-weight: 700;
  margin: 1.5em 0 0.5em 0;
  color: {{ACCENT_COLOUR}};
}

h4 {
  font-family: "SF Mono", "Consolas", monospace;
  font-size: 10pt;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin: 1em 0 0.5em 0;
  color: #666;
}

p {
  margin: 0 0 1em 0;
  orphans: 3;
  widows: 3;
}
strong { font-weight: 700; color: #000; }
em { font-style: italic; color: #666; }

hr {
  border: none;
  border-top: 1px solid {{BORDER_COLOUR}};
  margin: 2em 0;
}

code {
  font-family: "SF Mono", "Consolas", "Monaco", monospace;
  font-size: 9pt;
  background-color: #f3f4f6;
  padding: 0.15em 0.4em;
  border-radius: 3px;
  color: {{ACCENT_COLOUR}};
}

pre {
  font-family: "SF Mono", "Consolas", "Monaco", monospace;
  font-size: 9pt;
  background-color: {{TEXT_COLOUR}};
  color: #f3f4f6;
  padding: 1em 1.2em;
  border-radius: 0;
  border-left: 3px solid {{ACCENT_COLOUR}};
  overflow-x: auto;
  margin: 1em 0;
  line-height: 1.5;
  page-break-inside: avoid;
}

pre code {
  background: none;
  padding: 0;
  color: #f3f4f6;
}

/* Tables: row-level page-break rules — table-level alone is not enough */
table, tr, thead, tbody {
  page-break-inside: avoid;
  break-inside: avoid;
}

table {
  width: 100%;
  border-collapse: collapse;
  margin: 1em 0;
  font-size: 10pt;
}

th {
  font-family: "SF Mono", "Consolas", monospace;
  font-size: 8pt;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  text-align: left;
  padding: 0.8em 1em;
  background-color: {{TEXT_COLOUR}};
  color: #fff;
  border: none;
}

td {
  padding: 0.8em 1em;
  border-bottom: 1px solid {{BORDER_COLOUR}};
  vertical-align: top;
  background-color: #fff;
}

tr:last-child td {
  border-bottom: 2px solid {{TEXT_COLOUR}};
}

ul, ol {
  margin: 0.5em 0 1em 0;
  padding-left: 1.5em;
}

li { margin: 0.3em 0; }

blockquote {
  margin: 1em 0;
  padding: 0.5em 1em;
  border-left: 3px solid {{ACCENT_COLOUR}};
  background-color: #fff;
  font-style: italic;
  color: #666;
  page-break-inside: avoid;
}

a {
  color: {{ACCENT_COLOUR}};
  text-decoration: none;
}

a:hover { text-decoration: underline; }

.title-block { margin-bottom: 2em; }

.subtitle {
  font-family: "SF Mono", "Consolas", monospace;
  font-size: 9pt;
  text-transform: uppercase;
  letter-spacing: 0.15em;
  color: #666;
  margin-top: 0.5em;
}

.meta {
  font-family: "SF Mono", "Consolas", monospace;
  font-size: 9pt;
  color: #666;
  margin-bottom: 2em;
}

.page-break { page-break-after: always; }
.no-break { page-break-inside: avoid; }
```

---

## HTML Template

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>DOCUMENT_TITLE</title>
  <style>
    /* INSERT CSS WITH PLACEHOLDERS REPLACED */
  </style>
</head>
<body>
  <div class="title-block">
    <h1>DOCUMENT_TITLE</h1>
    <p class="subtitle">DOCUMENT_SUBTITLE</p>
    <p class="meta"><strong>Document Type</strong> Version X.X | MONTH YEAR</p>
  </div>

  <!-- CONTENT SECTIONS -->

</body>
</html>
```

---

## Content Structure Templates

### Technical Reference

````markdown
# [Product/Feature]: [Topic]

**Technical Reference** Version X.X | Month Year

---

## Overview

[2-3 paragraphs explaining what this is and why it matters]

### Key Benefits

| BENEFIT | DESCRIPTION |
|---------|-------------|
| Benefit 1 | Description |
| Benefit 2 | Description |

---

## Components

### [Category]

#### ComponentName

[Description]

```language
// Code example
```

---

## Best Practices

1. **Practice 1** - Explanation
2. **Practice 2** - Explanation

---

**Document Title** | {{COMPANY_NAME}} | Month Year
````

### Blog Article

```markdown
# [Engaging Title]

**[Subtitle/Hook]**

---

## The Problem

[Set up the pain point]

---

## The Solution

[Introduce approach]

### Step 1: [Action]

[Explanation with code]

---

## Results

| METRIC | BEFORE | AFTER |
|--------|--------|-------|
| Metric 1 | X | Y |

---

## Key Takeaways

1. **Takeaway 1**
2. **Takeaway 2**

---

**{{COMPANY_NAME}}** | Month Year
```

---

## Generation Workflow

### Step 1: Load Config

```python
import json
from pathlib import Path

config_path = Path.home() / ".claude/config/pdf-branding.json"
if config_path.exists():
    config = json.loads(config_path.read_text())
else:
    config = {
        "company_name": "Your Company",
        "colours": {
            "accent": "#ea580c",
            "text": "#1a1a1a",
            "background": "#EAEAEA",
            "border": "#d1d5db"
        },
        "typography": "swiss",
        "logo_path": None,
        "footer": "Your Company"
    }
```

### Step 2: Replace Placeholders

```python
css = css_template.replace("{{COMPANY_NAME}}", config["company_name"])
css = css.replace("{{ACCENT_COLOUR}}", config["colours"]["accent"])
css = css.replace("{{TEXT_COLOUR}}", config["colours"]["text"])
css = css.replace("{{BG_COLOUR}}", config["colours"]["background"])
css = css.replace("{{BORDER_COLOUR}}", config["colours"]["border"])
css = css.replace("{{FOOTER_TEXT}}", config["footer"])
```

### Step 3: Generate PDF

```bash
weasyprint document.html document.pdf
```

---

## Cover Delight Variants

The default cover uses an "Eclipse" pattern — two soft accent-colour circles
partially bleeding off opposite corners. Three additional minimalist variants
are available for visual variety. All preserve full-bleed A4, dark background,
flex layout, and Swiss restraint. Pick one based on document tone.

### Variant 1: Eclipse (default)

```css
.cover::before {
  content: ''; position: absolute;
  top: -120px; right: -120px;
  width: 480px; height: 480px;
  border-radius: 50%;
  background: var(--accent);
  opacity: 0.12;
}
.cover::after {
  content: ''; position: absolute;
  bottom: -80px; left: -60px;
  width: 320px; height: 320px;
  border-radius: 50%;
  background: var(--accent);
  opacity: 0.07;
}
```

### Variant 2: Halftone (for design / visual documents)

Radial dot grid fading from one corner — Swiss design school + halftone print.

```css
.cover.delight-halftone::before {
  content: ''; position: absolute;
  top: 0; right: 0; bottom: 0; left: 0;
  background-image: radial-gradient(var(--accent) 1.5px, transparent 1.5px);
  background-size: 22px 22px;
  background-position: 0 0;
  opacity: 0.18;
  -webkit-mask-image: radial-gradient(circle at 100% 0%, black 0%, transparent 65%);
  mask-image: radial-gradient(circle at 100% 0%, black 0%, transparent 65%);
}
```

### Variant 3: Topo (for systems-thinking / architecture documents)

Sparse orange contour lines — minimal organic, like a topographic map.

```css
.cover.delight-topo::before {
  content: ''; position: absolute;
  top: 0; right: 0; bottom: 0; left: 0;
  background-image:
    radial-gradient(ellipse 800px 300px at 80% 20%, transparent 38%, var(--accent) 39%, var(--accent) 39.4%, transparent 39.5%),
    radial-gradient(ellipse 700px 260px at 80% 20%, transparent 50%, var(--accent) 51%, var(--accent) 51.4%, transparent 51.5%),
    radial-gradient(ellipse 600px 220px at 80% 20%, transparent 62%, var(--accent) 63%, var(--accent) 63.4%, transparent 63.5%);
  opacity: 0.14;
}
```

### Variant 4: Constellation (for theory / research documents)

5-7 small dots connected by hairlines — quietly delightful, mathematical.

```css
.cover.delight-constellation::before {
  content: ''; position: absolute;
  top: 0; right: 0; bottom: 0; left: 0;
  background-image:
    /* 5 dots */
    radial-gradient(circle 4px at 18% 24%, var(--accent) 100%, transparent 100%),
    radial-gradient(circle 4px at 36% 12%, var(--accent) 100%, transparent 100%),
    radial-gradient(circle 4px at 62% 28%, var(--accent) 100%, transparent 100%),
    radial-gradient(circle 4px at 78% 18%, var(--accent) 100%, transparent 100%),
    radial-gradient(circle 4px at 88% 38%, var(--accent) 100%, transparent 100%),
    /* connecting hairlines via thin gradients */
    linear-gradient(155deg, transparent 17%, var(--accent) 17.2%, transparent 17.4%),
    linear-gradient(20deg, transparent 24%, var(--accent) 24.2%, transparent 24.4%);
  opacity: 0.4;
}
```

### Picking a variant

| Document tone | Variant | Class on `.cover` |
|---------------|---------|-------------------|
| Technical / systems / engineering | Eclipse (default) | (none) |
| Design / visual / UI | Halftone | `.cover.delight-halftone` |
| Architecture / topology / mapping | Topo | `.cover.delight-topo` |
| Theory / research / philosophy | Constellation | `.cover.delight-constellation` |

When unsure, ask the operator via /hitl. The default Eclipse works for most.

---

## Default Colour Schemes

### Warm Orange (Recommended)

```json
{
  "accent": "#ea580c",
  "text": "#1a1a1a",
  "background": "#EAEAEA",
  "border": "#d1d5db"
}
```

### Professional Blue

```json
{
  "accent": "#0066CC",
  "text": "#333333",
  "background": "#F5F5F5",
  "border": "#CCCCCC"
}
```

### Minimal Grayscale

```json
{
  "accent": "#666666",
  "text": "#333333",
  "background": "#FFFFFF",
  "border": "#DDDDDD"
}
```

---

## Changelog

**v1.0** (2025-12-30) - Initial creation
- Bespoke configuration system
- Configurable company branding
- Multiple colour schemes
- Placeholder-based CSS templates
