#!/usr/bin/env python3
"""Render markdown/plain-text report content into a styled PDF (fpdf2)."""
import argparse
import sys
from pathlib import Path

from fpdf import FPDF


def _cell(pdf: FPDF, h: float, txt: str, markdown: bool = False) -> None:
    """Reset x to left margin then emit multi_cell (avoids fpdf2 cursor drift)."""
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, h, txt, markdown=markdown)


def render_pdf(text: str, title: str | None) -> FPDF:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(18, 18, 18)
    pdf.add_page()

    if title:
        pdf.set_font("Helvetica", "B", 20)
        _cell(pdf, 10, title)
        pdf.ln(2)

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            pdf.ln(3)
            continue
        if line.startswith("### "):
            pdf.set_font("Helvetica", "B", 12); _cell(pdf, 7, line[4:])
        elif line.startswith("## "):
            pdf.set_font("Helvetica", "B", 14); pdf.ln(1); _cell(pdf, 8, line[3:])
        elif line.startswith("# "):
            pdf.set_font("Helvetica", "B", 17); pdf.ln(1); _cell(pdf, 9, line[2:])
        elif line.lstrip().startswith(("- ", "* ")):
            pdf.set_font("Helvetica", "", 11)
            # U+00B7 middle-dot is latin-1 safe; fpdf2 core fonts use latin-1
            _cell(pdf, 6, "  \xb7 " + line.lstrip()[2:], markdown=True)
        else:
            pdf.set_font("Helvetica", "", 11)
            _cell(pdf, 6, line, markdown=True)
    return pdf


def main() -> int:
    p = argparse.ArgumentParser(description="Render report text/markdown into a PDF.")
    p.add_argument("-i", "--input", required=True, help="path to markdown/text file")
    p.add_argument("-o", "--output", default="/outputs/report.pdf", help="output PDF path")
    p.add_argument("-t", "--title", default=None, help="optional report title")
    args = p.parse_args()

    try:
        text = Path(args.input).read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 2

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    render_pdf(text, args.title).output(str(out))
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
