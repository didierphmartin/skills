"""
Edit an existing .pptx — find/replace operations applied at the run
level across slide text frames, table cells, and speaker notes. Includes
a merge-runs pre-pass so search strings PowerPoint split across
consecutive <a:r> elements still match.

Usage:
    python edit.py -i input.pptx -o output.pptx --ops ops.json
"""
import argparse
import json
import re
import sys
from pathlib import Path

from pptx import Presentation


def merge_consecutive_runs(paragraph):
    """
    PowerPoint, like Word, often splits text into many runs even when
    they share formatting. To make find/replace behave intuitively we
    collapse consecutive runs whose formatting fingerprint matches.
    """
    runs = list(paragraph.runs)
    if len(runs) < 2:
        return

    def fmt_key(r):
        f = r.font
        return (
            getattr(f, "name", None),
            f.size,
            f.bold,
            f.italic,
            f.underline,
            (f.color.rgb if f.color is not None and f.color.type is not None else None),
        )

    # Walk pairs and merge into the previous run; drop the duplicate
    # element from the underlying XML.
    i = 0
    while i < len(runs) - 1:
        cur, nxt = runs[i], runs[i + 1]
        if fmt_key(cur) == fmt_key(nxt):
            cur.text = (cur.text or "") + (nxt.text or "")
            nxt._r.getparent().remove(nxt._r)
            runs.pop(i + 1)
        else:
            i += 1


def apply_to_paragraph(paragraph, find, replace, case_sensitive):
    merge_consecutive_runs(paragraph)
    for run in paragraph.runs:
        text = run.text or ""
        if not text:
            continue
        if case_sensitive:
            if find in text:
                run.text = text.replace(find, replace)
        else:
            run.text = re.sub(re.escape(find), replace, text, flags=re.IGNORECASE)


def apply_to_text_frame(tf, find, replace, case_sensitive):
    if tf is None:
        return
    for para in tf.paragraphs:
        apply_to_paragraph(para, find, replace, case_sensitive)


def apply_to_slide(slide, find, replace, case_sensitive):
    for shape in slide.shapes:
        if shape.has_text_frame:
            apply_to_text_frame(shape.text_frame, find, replace, case_sensitive)
        if shape.has_table:
            for row in shape.table.rows:
                for cell in row.cells:
                    apply_to_text_frame(cell.text_frame, find, replace, case_sensitive)
    if slide.has_notes_slide:
        apply_to_text_frame(slide.notes_slide.notes_text_frame, find, replace, case_sensitive)


def main():
    parser = argparse.ArgumentParser(description="Edit an existing .pptx")
    parser.add_argument("-i", "--input", required=True)
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--ops", required=True)
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
    prs = Presentation(in_path)

    fr_ops = ops.get("find_replace", [])
    for op in fr_ops:
        if not isinstance(op, dict):
            continue
        find = op.get("find")
        replace = op.get("replace", "")
        if not find:
            continue
        case_sensitive = bool(op.get("case_sensitive", False))
        for slide in prs.slides:
            apply_to_slide(slide, find, replace, case_sensitive)

    prs.save(out_path)
    print(f"Applied {len(fr_ops)} find/replace op(s); wrote {out_path.stat().st_size} bytes to {out_path}")


if __name__ == "__main__":
    main()
