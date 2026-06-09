"""
Edit an existing .docx — find/replace operations applied at the run
level, but with a merge-runs pre-pass so search strings that Word has
split across multiple <w:r> elements still match.

Usage:
    python edit.py -i input.docx -o output.docx --ops ops.json
"""
import argparse
import json
import re
import sys
from pathlib import Path

from docx import Document


def merge_consecutive_runs(paragraph):
    """
    Word commonly splits text into many runs even when they share
    formatting (e.g. autocorrect, paste). For find/replace to behave
    intuitively we collapse consecutive runs that have identical
    formatting into one. Runs with different formatting are left
    alone — splitting a search across distinct styling is a real
    semantic boundary the user probably didn't mean to cross.
    """
    if len(paragraph.runs) < 2:
        return

    def fmt_key(r):
        # A small "formatting fingerprint" — only fields we commonly care
        # about for find/replace. Style on the run, font size, bold,
        # italic, underline, color. None vs explicit value matters.
        f = r.font
        return (
            r.style.name if r.style else None,
            r.bold,
            r.italic,
            r.underline,
            f.size,
            getattr(f, "name", None),
            getattr(f.color, "rgb", None) if f.color is not None else None,
        )

    runs = list(paragraph.runs)
    i = 0
    while i < len(runs) - 1:
        cur, nxt = runs[i], runs[i + 1]
        if fmt_key(cur) == fmt_key(nxt):
            cur.text = (cur.text or "") + (nxt.text or "")
            nxt._r.getparent().remove(nxt._r)
            runs.pop(i + 1)
        else:
            i += 1


def apply_find_replace_to_paragraph(paragraph, find, replace, case_sensitive):
    merge_consecutive_runs(paragraph)
    for run in paragraph.runs:
        text = run.text or ""
        if not text:
            continue
        if case_sensitive:
            if find in text:
                run.text = text.replace(find, replace)
        else:
            # Case-insensitive replace via regex but escape `find` so
            # special chars in the LLM-emitted string don't blow up.
            run.text = re.sub(re.escape(find), replace, text, flags=re.IGNORECASE)


def apply_find_replace(doc, find, replace, case_sensitive=False):
    # Body paragraphs.
    for p in doc.paragraphs:
        apply_find_replace_to_paragraph(p, find, replace, case_sensitive)
    # Table cells.
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    apply_find_replace_to_paragraph(p, find, replace, case_sensitive)
    # Headers + footers in every section.
    for section in doc.sections:
        for p in section.header.paragraphs:
            apply_find_replace_to_paragraph(p, find, replace, case_sensitive)
        for p in section.footer.paragraphs:
            apply_find_replace_to_paragraph(p, find, replace, case_sensitive)


def main():
    parser = argparse.ArgumentParser(description="Edit an existing .docx")
    parser.add_argument("-i", "--input", required=True, help="Input .docx path")
    parser.add_argument("-o", "--output", required=True, help="Output .docx path")
    parser.add_argument("--ops", required=True, help="JSON ops file (see SKILL.md)")
    args = parser.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    ops_path = Path(args.ops)
    for p, label in [(in_path, "input"), (ops_path, "ops")]:
        if not p.exists():
            print(f"{label.capitalize()} file not found: {p}", file=sys.stderr)
            sys.exit(2)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ops = json.loads(ops_path.read_text(encoding="utf-8"))
    doc = Document(in_path)

    fr_ops = ops.get("find_replace", [])
    for op in fr_ops:
        if not isinstance(op, dict):
            continue
        find = op.get("find")
        replace = op.get("replace", "")
        if not find:
            continue
        case_sensitive = bool(op.get("case_sensitive", False))
        apply_find_replace(doc, find, replace, case_sensitive)

    doc.save(out_path)
    print(f"Applied {len(fr_ops)} find/replace op(s); wrote {out_path.stat().st_size} bytes to {out_path}")


if __name__ == "__main__":
    main()
