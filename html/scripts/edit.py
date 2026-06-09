"""
Apply find/replace and selector-based edits to an HTML document.

Two op families are supported:

  - find_replace: exact-string find/replace on the raw HTML source. Same
    shape as docx/pptx/xlsx edit.py. Best for one-off literal swaps
    (placeholders, fixed phrases).

  - replace_selector: BeautifulSoup CSS-selector replacement. Best for
    surgical structural edits — e.g. replace the entire <div class="refs">
    section with new HTML, without rebuilding the whole document. Mode
    "outer" (default) swaps the whole element; "inner" replaces just its
    contents and keeps the wrapper.

Ops apply in declared order. Later ops see the result of earlier ones.

LOUD-FAILURE CONTRACT: if you request ops but NONE of them match anything
in the document, the script exits 1 with an explicit error. This is
intentional — silent no-ops were causing the calling LLM to report
success when nothing actually changed. Re-read the input HTML to find
selectors / find strings that actually appear, then retry.

Usage:
    python edit.py -i /scratch/in.html -o /outputs/out.html --ops /scratch/ops.json
"""
import argparse
import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup


def parse_args():
    p = argparse.ArgumentParser(description="Edit an HTML document via find/replace and selector ops.")
    p.add_argument("-i", "--input", required=True, help="Path to input HTML file.")
    p.add_argument("-o", "--output", required=True, help="Path to write the edited HTML.")
    p.add_argument("--ops", required=True, help="Path to JSON ops file.")
    return p.parse_args()


def apply_find_replace(html, ops):
    out = html
    applied = 0
    for op in ops:
        find = op.get("find", "")
        replace = op.get("replace", "")
        case_sensitive = bool(op.get("case_sensitive", True))
        if not find:
            continue
        if case_sensitive:
            count = out.count(find)
            out = out.replace(find, replace)
        else:
            pattern = re.escape(find)
            new, count = re.subn(pattern, lambda _m: replace, out, flags=re.IGNORECASE)
            out = new
        if count == 0:
            print(f"warning: find_replace matched nothing for: {find[:80]!r}", file=sys.stderr)
        applied += count
    return out, applied


def apply_replace_selector(html, ops):
    if not ops:
        return html, 0
    soup = BeautifulSoup(html, "html.parser")
    applied = 0
    for op in ops:
        sel = op.get("selector", "")
        new_html = op.get("html", "")
        mode = op.get("mode", "outer")
        if not sel:
            print("warning: replace_selector op missing 'selector'; skipped", file=sys.stderr)
            continue
        try:
            elements = soup.select(sel)
        except Exception as e:
            print(f"warning: invalid selector {sel!r}: {e}", file=sys.stderr)
            continue
        if not elements:
            print(f"warning: selector matched nothing: {sel}", file=sys.stderr)
            continue
        for el in elements:
            new_soup = BeautifulSoup(new_html, "html.parser")
            new_children = list(new_soup.contents)
            if not new_children:
                el.decompose() if mode == "outer" else el.clear()
            elif mode == "outer":
                first = new_children[0]
                el.replace_with(first)
                cursor = first
                for extra in new_children[1:]:
                    cursor.insert_after(extra)
                    cursor = extra
            else:
                el.clear()
                for child in new_children:
                    el.append(child)
            applied += 1
    return str(soup), applied


def main():
    args = parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    ops_path = Path(args.ops)

    if not in_path.exists():
        print(f"error: input file not found: {in_path}", file=sys.stderr)
        sys.exit(2)
    if not ops_path.exists():
        print(f"error: ops file not found: {ops_path}", file=sys.stderr)
        sys.exit(2)

    html = in_path.read_text(encoding="utf-8")
    try:
        ops = json.loads(ops_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"error: ops file is not valid JSON: {e}", file=sys.stderr)
        sys.exit(2)

    fr_ops = ops.get("find_replace", []) or []
    sel_ops = ops.get("replace_selector", []) or []

    fr_applied = sel_applied = 0
    if fr_ops:
        html, fr_applied = apply_find_replace(html, fr_ops)
    if sel_ops:
        html, sel_applied = apply_replace_selector(html, sel_ops)

    total = fr_applied + sel_applied
    requested_ops = len(fr_ops) + len(sel_ops)

    # Loud failure when ops were requested but NOTHING changed. Without
    # this, the script silently writes the file unchanged + only warns
    # to stderr, and the calling LLM glosses over the warning and
    # reports success to the user. A non-zero exit forces the model to
    # re-read the input and retry with selectors / find strings that
    # actually exist in the document.
    if requested_ops > 0 and total == 0:
        # Dump the document's actual classes and ids so the caller (an LLM)
        # has something concrete to retry with instead of guessing again.
        # This was the failure mode in practice — model kept guessing
        # selector names that didn't exist and hitting MAX_DEPTH.
        try:
            inv_soup = BeautifulSoup(in_path.read_text(encoding="utf-8"), "html.parser")
            classes = set()
            ids = set()
            tags_with_text = []
            for el in inv_soup.find_all(True):
                cls = el.get("class")
                if cls:
                    for c in cls:
                        if c:
                            classes.add(c)
                eid = el.get("id")
                if eid:
                    ids.add(eid)
                # Capture short text-bearing elements so the caller can
                # match by visible heading/label text. Heading/label
                # elements only, capped to 60 chars, dedup'd.
                if el.name in ("h1", "h2", "h3", "h4", "h5", "h6", "summary", "legend"):
                    txt = (el.get_text() or "").strip().replace("\n", " ")
                    if txt:
                        tags_with_text.append(f"<{el.name}>: {txt[:60]}")
            classes_sorted = sorted(classes)[:40]
            ids_sorted = sorted(ids)[:40]
            headings_unique = []
            seen_h = set()
            for h in tags_with_text:
                if h in seen_h:
                    continue
                seen_h.add(h)
                headings_unique.append(h)
                if len(headings_unique) >= 25:
                    break
        except Exception as e:
            classes_sorted, ids_sorted, headings_unique = [], [], []
            print(f"(could not introspect document for selector hints: {e})", file=sys.stderr)

        msg = [
            f"error: 0 of {requested_ops} op(s) matched anything. The output would be "
            "IDENTICAL to the input — no file was written.",
            "",
            "DOCUMENT INVENTORY (use these to construct a working selector):",
            f"  classes ({len(classes_sorted)}): " + (", ".join(f".{c}" for c in classes_sorted) if classes_sorted else "(none)"),
            f"  ids ({len(ids_sorted)}): " + (", ".join(f"#{i}" for i in ids_sorted) if ids_sorted else "(none)"),
        ]
        if headings_unique:
            msg.append("  headings/labels:")
            for h in headings_unique:
                msg.append(f"    {h}")
        msg.extend([
            "",
            "RETRY: pick a selector from the inventory above (e.g. one of the listed "
            "classes or ids), or use a tag selector like 'section:has(h2)' to target "
            "by heading. For find_replace ops, make sure the find string appears "
            "verbatim in the document.",
        ])
        print("\n".join(msg), file=sys.stderr)
        sys.exit(1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    print(f"wrote {out_path} "
          f"({fr_applied} find_replace match(es), {sel_applied} selector match(es))")


if __name__ == "__main__":
    main()
