"""
Create a .pptx from a JSON spec via python-pptx (pure-Python, Pyodide-friendly).

Two modes:
  - default: uses python-pptx's built-in Office layouts.
  - template: opens a user-supplied .pptx and reuses its layouts/master.

Spec shape is documented in SKILL.md.

Usage:
    python create.py -i spec.json -o output.pptx [--template template.pptx]
"""
import argparse
import json
import sys
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE


# Map friendly layout names → indices in the default Office layout master.
# Templates may use different layout names; users can also pass a numeric
# index ("layout": 3) and it'll be looked up directly.
LAYOUT_INDEX = {
    "title": 0,
    "title_and_content": 1,
    "section": 2,
    "two_content": 3,
    "comparison": 4,
    "title_only": 5,
    "blank": 6,
    "content_with_caption": 7,
    "picture_with_caption": 8,
}

SLIDE_SIZES_IN = {
    "16:9": (13.333, 7.5),
    "4:3":  (10.0, 7.5),
    "16:10": (13.333, 8.333),
}


def resolve_layout(prs, name_or_index):
    """Look up a slide layout by friendly name, exact name, or 0-based index."""
    layouts = prs.slide_layouts
    if isinstance(name_or_index, int):
        if 0 <= name_or_index < len(layouts):
            return layouts[name_or_index]
        return layouts[6 if 6 < len(layouts) else 0]  # fall back to blank
    key = (name_or_index or "").strip().lower()
    if key in LAYOUT_INDEX:
        idx = LAYOUT_INDEX[key]
        if idx < len(layouts):
            return layouts[idx]
    # Try matching by layout name (case-insensitive substring) for templates
    # with custom layout names.
    for lay in layouts:
        if (lay.name or "").strip().lower() == key:
            return lay
    for lay in layouts:
        if key and key in (lay.name or "").lower():
            return lay
    # Last resort: first layout.
    return layouts[0]


def apply_run_format(run, fmt):
    f = fmt or {}
    if f.get("bold"):       run.font.bold = True
    if f.get("italic"):     run.font.italic = True
    if f.get("underline"):  run.font.underline = True
    if f.get("font_size_pt"):
        try:
            run.font.size = Pt(float(f["font_size_pt"]))
        except (TypeError, ValueError):
            pass
    if f.get("color_rgb"):
        try:
            run.font.color.rgb = RGBColor.from_string(str(f["color_rgb"]).lstrip("#"))
        except (ValueError, TypeError):
            pass
    if f.get("font_name"):
        run.font.name = f["font_name"]


def fill_text_frame_with_bullets(tf, items):
    """Replace a text frame's content with the supplied bullet items."""
    if not items:
        return
    tf.clear()
    first_para = tf.paragraphs[0]
    for i, item in enumerate(items):
        if isinstance(item, str):
            text, fmt, level = item, None, 0
        else:
            text = str(item.get("text", ""))
            fmt = item
            level = int(item.get("level", 0))
        para = first_para if i == 0 else tf.add_paragraph()
        para.level = max(0, min(8, level))
        run = para.add_run()
        run.text = text
        apply_run_format(run, fmt)


def set_title(slide, title_text):
    if not title_text:
        return
    if slide.shapes.title:
        slide.shapes.title.text = ""
        tf = slide.shapes.title.text_frame
        run = tf.paragraphs[0].add_run()
        run.text = str(title_text)


def get_placeholder(slide, idx):
    """Return the placeholder with idx (PowerPoint placeholder index) or None."""
    for ph in slide.placeholders:
        if ph.placeholder_format.idx == idx:
            return ph
    return None


def find_first_body_placeholder(slide):
    """Return the first non-title placeholder, or None."""
    for ph in slide.placeholders:
        if ph.placeholder_format.idx != 0:
            return ph
    return None


def add_table(slide, header, rows, left_in=0.5, top_in=1.5, width_in=9.0, height_in=0.8):
    cols = max((len(header) if header else 0), max((len(r) for r in rows), default=0))
    if cols == 0:
        return
    total_rows = (1 if header else 0) + len(rows)
    if total_rows == 0:
        return
    table_shape = slide.shapes.add_table(
        rows=total_rows, cols=cols,
        left=Inches(left_in), top=Inches(top_in),
        width=Inches(width_in), height=Inches(height_in * max(1, total_rows)),
    )
    table = table_shape.table
    row_offset = 0
    if header:
        for c, val in enumerate(header):
            cell = table.cell(0, c)
            cell.text = ""
            run = cell.text_frame.paragraphs[0].add_run()
            run.text = str(val)
            run.font.bold = True
        row_offset = 1
    for r_idx, row in enumerate(rows):
        for c_idx, val in enumerate(row):
            if c_idx >= cols:
                break
            cell = table.cell(row_offset + r_idx, c_idx)
            cell.text = str(val)


def add_image(slide, img):
    path = img.get("path")
    if not path:
        return
    kwargs = {}
    if "width_in" in img:  kwargs["width"]  = Inches(float(img["width_in"]))
    if "height_in" in img: kwargs["height"] = Inches(float(img["height_in"]))
    left = Inches(float(img.get("left_in", 0.5)))
    top  = Inches(float(img.get("top_in", 1.5)))
    slide.shapes.add_picture(path, left, top, **kwargs)


def set_slide_background(slide, rgb_hex):
    """Solid-fill a slide background. Hex without leading '#'."""
    try:
        rgb = RGBColor.from_string(str(rgb_hex).lstrip("#"))
    except (ValueError, TypeError):
        return
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = rgb


def set_speaker_notes(slide, notes):
    if not notes:
        return
    notes_tf = slide.notes_slide.notes_text_frame
    notes_tf.clear()
    notes_tf.paragraphs[0].add_run().text = str(notes)


def render_slide(prs, spec):
    layout = resolve_layout(prs, spec.get("layout", "title_and_content"))
    slide = prs.slides.add_slide(layout)

    # Background.
    if "background_rgb" in spec:
        set_slide_background(slide, spec["background_rgb"])

    # Title.
    set_title(slide, spec.get("title"))

    # Subtitle (placeholder idx 1 on the title layout).
    if spec.get("subtitle"):
        ph = get_placeholder(slide, 1)
        if ph and ph.has_text_frame:
            fill_text_frame_with_bullets(ph.text_frame, [spec["subtitle"]])

    # Bullets → first body placeholder if available.
    if spec.get("bullets"):
        ph = find_first_body_placeholder(slide) or get_placeholder(slide, 1)
        if ph and ph.has_text_frame:
            fill_text_frame_with_bullets(ph.text_frame, spec["bullets"])

    # Two-content / comparison: left + right.
    if spec.get("left") or spec.get("right"):
        body_phs = [ph for ph in slide.placeholders if ph.placeholder_format.idx != 0]
        if len(body_phs) >= 2:
            if spec.get("left"):
                fill_text_frame_with_bullets(body_phs[0].text_frame, spec["left"])
            if spec.get("right"):
                fill_text_frame_with_bullets(body_phs[1].text_frame, spec["right"])

    # Table.
    if spec.get("table"):
        t = spec["table"]
        add_table(slide, t.get("header"), t.get("rows", []))

    # Image.
    if spec.get("image"):
        add_image(slide, spec["image"])

    # Speaker notes.
    if spec.get("notes"):
        set_speaker_notes(slide, spec["notes"])

    return slide


def apply_metadata(prs, meta):
    cp = prs.core_properties
    if "title" in meta:    cp.title = str(meta["title"])
    if "author" in meta:   cp.author = str(meta["author"])
    if "subject" in meta:  cp.subject = str(meta["subject"])
    if "keywords" in meta: cp.keywords = str(meta["keywords"])


def apply_slide_size(prs, size):
    if isinstance(size, dict) and "width_in" in size and "height_in" in size:
        prs.slide_width  = Inches(float(size["width_in"]))
        prs.slide_height = Inches(float(size["height_in"]))
        return
    if isinstance(size, str) and size in SLIDE_SIZES_IN:
        w, h = SLIDE_SIZES_IN[size]
        prs.slide_width  = Inches(w)
        prs.slide_height = Inches(h)


def main():
    parser = argparse.ArgumentParser(description="Create .pptx from JSON spec")
    parser.add_argument("-i", "--input", required=True)
    parser.add_argument("-o", "--output", required=True)
    parser.add_argument("--template", default=None,
                        help="Optional path to a .pptx whose layouts/master should be reused.")
    args = parser.parse_args()

    spec_path = Path(args.input)
    out_path = Path(args.output)
    if not spec_path.exists():
        print(f"Spec file not found: {spec_path}", file=sys.stderr)
        sys.exit(2)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    if args.template:
        tpl_path = Path(args.template)
        if not tpl_path.exists():
            print(f"Template not found: {tpl_path}", file=sys.stderr)
            sys.exit(2)
        prs = Presentation(tpl_path)
        # Remove any pre-existing slides from the template; we'll add our own.
        sldIdLst = prs.slides._sldIdLst
        for sld in list(sldIdLst):
            sldIdLst.remove(sld)
    else:
        prs = Presentation()

    if "slide_size" in spec:
        apply_slide_size(prs, spec["slide_size"])

    if "metadata" in spec:
        apply_metadata(prs, spec["metadata"])

    for slide_spec in spec.get("slides", []):
        if isinstance(slide_spec, dict):
            render_slide(prs, slide_spec)

    prs.save(out_path)
    print(f"Wrote {out_path.stat().st_size} bytes to {out_path}")
    print(f"Created {len(prs.slides)} slide(s).")


if __name__ == "__main__":
    main()
