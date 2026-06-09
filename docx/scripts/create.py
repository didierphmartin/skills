"""
Create a new .docx from a JSON spec using python-docx (pure-Python,
Pyodide-friendly).

Spec shape is documented in SKILL.md. Supports headings, paragraphs with
mixed-formatting runs, lists (ordered/unordered), tables (with optional
header row), inline images, hyperlinks, page breaks, horizontal rules,
TOC field, headers/footers with optional page numbers, page setup
(size/orientation/margins), and document metadata.

Usage:
    python create.py -i spec.json -o output.docx
"""
import argparse
import json
import sys
from pathlib import Path

from docx import Document
from docx.shared import Inches, Pt, RGBColor, Emu
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT, WD_BREAK
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement


# ---------- helpers ----------

PAGE_SIZES_IN = {
    "Letter":  (8.5, 11.0),
    "Legal":   (8.5, 14.0),
    "A4":      (8.27, 11.69),
    "A5":      (5.83, 8.27),
    "Tabloid": (11.0, 17.0),
}

ALIGNMENT_MAP = {
    "left": WD_PARAGRAPH_ALIGNMENT.LEFT,
    "center": WD_PARAGRAPH_ALIGNMENT.CENTER,
    "right": WD_PARAGRAPH_ALIGNMENT.RIGHT,
    "justify": WD_PARAGRAPH_ALIGNMENT.JUSTIFY,
}


def apply_run_formatting(run, fmt):
    """Apply a per-run formatting dict to a python-docx Run object."""
    if fmt.get("bold"):       run.bold = True
    if fmt.get("italic"):     run.italic = True
    if fmt.get("underline"):  run.underline = True
    if fmt.get("strike"):     run.font.strike = True
    if fmt.get("superscript"): run.font.superscript = True
    if fmt.get("subscript"):   run.font.subscript = True
    if fmt.get("font_name"):  run.font.name = fmt["font_name"]
    if fmt.get("font_size_pt"):
        try:
            run.font.size = Pt(float(fmt["font_size_pt"]))
        except (TypeError, ValueError):
            pass
    if fmt.get("color_rgb"):
        try:
            run.font.color.rgb = RGBColor.from_string(str(fmt["color_rgb"]).lstrip("#"))
        except (ValueError, TypeError):
            pass


def add_runs_to_paragraph(paragraph, runs):
    """Append a list of {text, ...formatting} runs to a paragraph."""
    for r in runs:
        if not isinstance(r, dict):
            continue
        run = paragraph.add_run(r.get("text", ""))
        apply_run_formatting(run, r)


def add_paragraph_text_or_runs(doc, element, style=None):
    """Common path: an element may carry either `text` or `runs`."""
    p = doc.add_paragraph(style=style)
    align = ALIGNMENT_MAP.get(element.get("alignment", "left"))
    if align is not None:
        p.alignment = align
    if "runs" in element and isinstance(element["runs"], list):
        add_runs_to_paragraph(p, element["runs"])
    elif "text" in element:
        p.add_run(str(element["text"]))
    return p


def add_hyperlink(paragraph, url, text):
    """Add a clickable hyperlink to a paragraph (python-docx has no helper)."""
    part = paragraph.part
    r_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)

    new_run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    rPr.append(color)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    rPr.append(underline)
    new_run.append(rPr)

    t = OxmlElement("w:t")
    t.text = text
    t.set(qn("xml:space"), "preserve")
    new_run.append(t)

    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)


def add_horizontal_rule(paragraph):
    """Insert a horizontal rule (bottom border on an empty paragraph)."""
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "auto")
    pBdr.append(bottom)
    pPr.append(pBdr)


def add_toc_field(doc, title=None):
    """Insert a TOC field. Word/LibreOffice will populate it on first open."""
    if title:
        doc.add_paragraph(title, style="Heading 1")
    paragraph = doc.add_paragraph()
    run = paragraph.add_run()

    fldChar1 = OxmlElement("w:fldChar")
    fldChar1.set(qn("w:fldCharType"), "begin")

    instrText = OxmlElement("w:instrText")
    instrText.set(qn("xml:space"), "preserve")
    instrText.text = 'TOC \\o "1-3" \\h \\z \\u'

    fldChar2 = OxmlElement("w:fldChar")
    fldChar2.set(qn("w:fldCharType"), "separate")

    fldChar3 = OxmlElement("w:t")
    fldChar3.text = "Right-click → Update Field to populate the table of contents."

    fldChar4 = OxmlElement("w:fldChar")
    fldChar4.set(qn("w:fldCharType"), "end")

    run._r.append(fldChar1)
    run._r.append(instrText)
    run._r.append(fldChar2)
    run._r.append(fldChar3)
    run._r.append(fldChar4)


def add_page_number(paragraph):
    """Insert PAGE field into a paragraph (used in headers/footers)."""
    run = paragraph.add_run()
    fldChar1 = OxmlElement("w:fldChar")
    fldChar1.set(qn("w:fldCharType"), "begin")
    instrText = OxmlElement("w:instrText")
    instrText.set(qn("xml:space"), "preserve")
    instrText.text = "PAGE"
    fldChar2 = OxmlElement("w:fldChar")
    fldChar2.set(qn("w:fldCharType"), "end")
    run._r.append(fldChar1)
    run._r.append(instrText)
    run._r.append(fldChar2)


# ---------- main rendering ----------

def apply_metadata(doc, meta):
    cp = doc.core_properties
    if "title" in meta:    cp.title = str(meta["title"])
    if "author" in meta:   cp.author = str(meta["author"])
    if "subject" in meta:  cp.subject = str(meta["subject"])
    if "keywords" in meta: cp.keywords = str(meta["keywords"])


def apply_page_setup(doc, ps):
    section = doc.sections[0]
    size = ps.get("size", "Letter")
    if isinstance(size, dict) and "width_in" in size and "height_in" in size:
        w_in, h_in = float(size["width_in"]), float(size["height_in"])
    elif isinstance(size, str) and size in PAGE_SIZES_IN:
        w_in, h_in = PAGE_SIZES_IN[size]
    else:
        w_in, h_in = PAGE_SIZES_IN["Letter"]

    if ps.get("orientation", "portrait") == "landscape":
        w_in, h_in = h_in, w_in

    section.page_width = Inches(w_in)
    section.page_height = Inches(h_in)

    margins = ps.get("margins", {})
    if "top_in" in margins:    section.top_margin = Inches(float(margins["top_in"]))
    if "bottom_in" in margins: section.bottom_margin = Inches(float(margins["bottom_in"]))
    if "left_in" in margins:   section.left_margin = Inches(float(margins["left_in"]))
    if "right_in" in margins:  section.right_margin = Inches(float(margins["right_in"]))


def apply_header(doc, hdr):
    section = doc.sections[0]
    p = section.header.paragraphs[0]
    if hdr.get("text"):
        p.add_run(str(hdr["text"]))
    if hdr.get("page_numbers"):
        if hdr.get("text"):
            p.add_run("  ")
        add_page_number(p)


def apply_footer(doc, ftr):
    section = doc.sections[0]
    p = section.footer.paragraphs[0]
    if ftr.get("text"):
        p.add_run(str(ftr["text"]))
    if ftr.get("page_numbers"):
        if ftr.get("text"):
            p.add_run("  ")
        add_page_number(p)


def render_element(doc, el):
    t = el.get("type")
    if t == "heading":
        level = max(1, min(9, int(el.get("level", 1))))
        para = doc.add_heading(level=level)
        if "runs" in el:
            add_runs_to_paragraph(para, el["runs"])
        else:
            para.add_run(str(el.get("text", "")))
    elif t == "paragraph":
        add_paragraph_text_or_runs(doc, el)
    elif t == "list":
        ordered = bool(el.get("ordered"))
        style = "List Number" if ordered else "List Bullet"
        for item in el.get("items", []):
            if isinstance(item, dict) and isinstance(item.get("runs"), list):
                p = doc.add_paragraph(style=style)
                add_runs_to_paragraph(p, item["runs"])
            else:
                doc.add_paragraph(str(item), style=style)
    elif t == "table":
        rows = el.get("rows", [])
        header = el.get("header")
        col_count = max((len(header) if header else 0), max((len(r) for r in rows), default=0))
        if col_count == 0:
            return
        total_rows = (1 if header else 0) + len(rows)
        table = doc.add_table(rows=total_rows, cols=col_count)
        table.style = el.get("style", "Table Grid")
        row_offset = 0
        if header:
            for i, cell in enumerate(header):
                c = table.rows[0].cells[i]
                c.text = ""
                run = c.paragraphs[0].add_run(str(cell))
                run.bold = True
            row_offset = 1
        for r_idx, row in enumerate(rows):
            for c_idx, cell in enumerate(row):
                table.rows[row_offset + r_idx].cells[c_idx].text = str(cell)
    elif t == "image":
        path = el.get("path")
        if not path:
            return
        kwargs = {}
        if "width_in" in el:
            kwargs["width"] = Inches(float(el["width_in"]))
        if "height_in" in el:
            kwargs["height"] = Inches(float(el["height_in"]))
        doc.add_picture(path, **kwargs)
        if el.get("caption"):
            cap = doc.add_paragraph(str(el["caption"]))
            cap.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
            for r in cap.runs:
                r.italic = True
    elif t == "hyperlink":
        p = doc.add_paragraph()
        add_hyperlink(p, el.get("url", ""), el.get("text", el.get("url", "")))
    elif t == "horizontal_rule":
        add_horizontal_rule(doc.add_paragraph())
    elif t == "page_break":
        doc.add_page_break()
    elif t == "toc":
        add_toc_field(doc, el.get("title"))
    else:
        # Unknown element types: log to stderr but keep going so a single
        # bad spec entry doesn't kill the whole document.
        print(f"[create.py] Unknown element type {t!r}, skipping.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Create .docx from JSON spec")
    parser.add_argument("-i", "--input", required=True, help="JSON spec path")
    parser.add_argument("-o", "--output", required=True, help="Output .docx path")
    args = parser.parse_args()

    spec_path = Path(args.input)
    out_path = Path(args.output)
    if not spec_path.exists():
        print(f"Spec file not found: {spec_path}", file=sys.stderr)
        sys.exit(2)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    doc = Document()
    if "page_setup" in spec:
        apply_page_setup(doc, spec["page_setup"])
    if "metadata" in spec:
        apply_metadata(doc, spec["metadata"])
    if "header" in spec:
        apply_header(doc, spec["header"])
    if "footer" in spec:
        apply_footer(doc, spec["footer"])

    for el in spec.get("elements", []):
        if isinstance(el, dict):
            render_element(doc, el)

    doc.save(out_path)
    print(f"Wrote {out_path.stat().st_size} bytes to {out_path}")


if __name__ == "__main__":
    main()
