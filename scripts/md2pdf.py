"""Export a markdown document to PDF (A4) without pandoc or LaTeX.

    uv run --with markdown --with weasyprint python scripts/md2pdf.py docs/architecture.md docs/architecture.pdf
"""

import sys
from pathlib import Path

import markdown
from weasyprint import HTML

CSS = """
@page { size: A4; margin: 15mm 15mm 15mm 15mm;
        @bottom-right { content: counter(page) " / " counter(pages); font-size: 8pt; color: #777; } }
body { font-family: 'DejaVu Sans', 'Liberation Sans', sans-serif; font-size: 10pt; line-height: 1.38; color: #1d1d1f; }
h1 { font-size: 16pt; margin: 0 0 6pt; } h2 { font-size: 11.5pt; margin: 11pt 0 4pt; border-bottom: 1px solid #ddd; }
p, li { margin: 3pt 0; } ul { margin: 2pt 0 4pt 14pt; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0 6pt; font-size: 8.4pt; page-break-inside: avoid; }
th, td { border: 1px solid #ccc; padding: 2pt 4pt; vertical-align: top; text-align: left; } th { background: #f3f0f7; }
pre { font-family: 'Fira Code', 'DejaVu Sans Mono', 'Droid Sans Mono', monospace; font-size: 7.2pt; line-height: 1.15; background: #faf9fb;
      border: 1px solid #eee; padding: 4pt; white-space: pre; page-break-inside: avoid; }
code { font-family: 'Fira Code', 'DejaVu Sans Mono', monospace; font-size: 8pt; }
"""

src, out = Path(sys.argv[1]), Path(sys.argv[2])
body = markdown.markdown(src.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"])
doc = HTML(string=f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}</body></html>").render()
doc.write_pdf(out)
print(f"{out}: {len(doc.pages)} pages")
