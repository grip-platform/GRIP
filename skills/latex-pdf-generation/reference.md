# LaTeX/PDF Generation Reference

Detailed templates and troubleshooting for the latex-pdf-generation skill.

---

## Full Academic Paper Template

```latex
\documentclass[11pt,a4paper]{article}

% --- Encoding and Fonts ---
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}

% --- Mathematics ---
\usepackage{amsmath,amssymb,amsthm}
\usepackage{mathtools}

% --- Layout ---
\usepackage[margin=2.5cm]{geometry}
\usepackage{hyperref}
\usepackage{enumitem}
\usepackage{booktabs}

% --- Optional: Colour and Boxes ---
% \usepackage{xcolor}
% \usepackage{tcolorbox}

% --- Theorem Environments ---
\newtheorem{theorem}{Theorem}[section]
\newtheorem{lemma}[theorem]{Lemma}
\newtheorem{corollary}[theorem]{Corollary}
\newtheorem{proposition}[theorem]{Proposition}
\theoremstyle{definition}
\newtheorem{definition}[theorem]{Definition}
\newtheorem{remark}[theorem]{Remark}

% --- Title ---
\title{\textbf{Paper Title}\\[6pt]\large Subtitle}
\author{Author Name\thanks{Affiliation. Email: \texttt{author@example.com}}}
\date{Month Year}

\begin{document}
\maketitle

\begin{abstract}
Abstract text here.
\end{abstract}

\section{Introduction}
Content.

\section{Methods}
Content.

\section{Results}
Content.

\section{Conclusion}
Content.

\bibliographystyle{plain}
\bibliography{references}

\end{document}
```

---

## Swiss Minimalism HTML Template

Matches the professional-pdf skill branding. Uses CSS Paged Media for
weasyprint.

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Document Title</title>
<style>
  @page {
    size: A4;
    margin: 2cm 2.5cm;
    @bottom-center {
      content: counter(page);
      font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
      font-size: 9pt;
      color: #666;
    }
  }
  body {
    font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.6;
    color: #1a1a1a;
    background: #fff;
  }
  h1 {
    font-size: 28pt;
    font-weight: 800;
    border-bottom: 3px solid #ea580c;
    padding-bottom: 0.3em;
  }
  h2 { font-size: 16pt; font-weight: 700; }
  h3 { font-size: 12pt; font-weight: 700; color: #ea580c; }
  table {
    width: 100%;
    border-collapse: collapse;
    margin: 1em 0;
  }
  th, td {
    border: 1px solid #d1d5db;
    padding: 8px 12px;
    text-align: left;
  }
  th { background: #f3f4f6; font-weight: 700; }
  code {
    font-family: 'SF Mono', 'Consolas', 'Liberation Mono', monospace;
    font-size: 9pt;
    background: #f3f4f6;
    padding: 2px 4px;
    border-radius: 3px;
  }
  .footer {
    font-size: 9pt;
    color: #666;
    border-top: 1px solid #d1d5db;
    padding-top: 0.5em;
    margin-top: 2em;
  }
</style>
</head>
<body>
  <h1>Document Title</h1>
  <!-- Content here -->
  <div class="footer">Confidential</div>
</body>
</html>
```

---

## Statement of Work Template (HTML)

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Statement of Work</title>
<style>
  @page {
    size: A4;
    margin: 2cm 2.5cm;
    @bottom-right { content: "Page " counter(page) " of " counter(pages); font-size: 9pt; }
  }
  body { font-family: -apple-system, Helvetica, Arial, sans-serif; font-size: 11pt; color: #1a1a1a; }
  h1 { font-size: 22pt; border-bottom: 2px solid #ea580c; }
  table { width: 100%; border-collapse: collapse; margin: 1em 0; }
  th, td { border: 1px solid #d1d5db; padding: 8px; }
  th { background: #f9fafb; }
  .signature-line { border-bottom: 1px solid #333; width: 250px; display: inline-block; margin-top: 2em; }
</style>
</head>
<body>
  <h1>Statement of Work</h1>

  <h2>1. Scope of Services</h2>
  <p>Description of services to be delivered.</p>

  <h2>2. Deliverables</h2>
  <table>
    <tr><th>Item</th><th>Description</th><th>Due Date</th></tr>
    <tr><td>1</td><td>Deliverable description</td><td>YYYY-MM-DD</td></tr>
  </table>

  <h2>3. Payment Terms</h2>
  <p>Rate and schedule details.</p>

  <h2>4. Signatures</h2>
  <p>Client: <span class="signature-line">&nbsp;</span></p>
  <p>Provider: <span class="signature-line">&nbsp;</span></p>
</body>
</html>
```

---

## Troubleshooting Guide

### pdflatex Exits With No Obvious Error

The log file often contains the real error buried in output. Search for:

```bash
grep -E '^!|^l\.' "${source%.tex}.log" | head -20
```

### "I can't write on file" Error

LaTeX cannot write auxiliary files. Check:
1. Directory permissions: `ls -la $(dirname "$source")`
2. Existing `.aux` lock: `rm -f "${source%.tex}.aux"`
3. Disk space: `df -h .`

### Unicode Characters in pdflatex

pdflatex does not support Unicode natively. Options:
1. Switch to xelatex: `xelatex "$source"`
2. Use escape sequences: `Str\o mme` for "Stromme"
3. Add `\usepackage[utf8]{inputenc}` (handles basic Latin extensions only)

### WeasyPrint Renders Blank Pages

Usually caused by CSS errors. Debug with:

```bash
weasyprint --presentational-hints -d "$source" /dev/null 2>&1 | head -20
```

### Large Documents Taking Too Long

For documents over 100 pages:
- Use `\includeonly{chapter}` to compile a single chapter during editing
- Use `draft` mode: `\documentclass[11pt,a4paper,draft]{article}`
- Run single-pass during editing, full multi-pass for final output only

---

## Engine Feature Matrix

| Feature | pdflatex | xelatex | lualatex | weasyprint | wkhtmltopdf |
|---------|----------|---------|----------|------------|-------------|
| Speed | Fast | Moderate | Slow | Fast | Moderate |
| Unicode | Limited | Full | Full | Full | Full |
| System fonts | No | Yes | Yes | Yes | Yes |
| Math typesetting | Yes | Yes | Yes | No | No |
| CSS Paged Media | No | No | No | Yes | Partial |
| Auto-install pkgs | No | No | No | N/A | N/A |
| Bibliography | bibtex/biber | bibtex/biber | bibtex/biber | N/A | N/A |
