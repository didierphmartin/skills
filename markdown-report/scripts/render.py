#!/usr/bin/env python3
"""Render a markdown document to conversation-style HTML.

Deterministic layout: the CSS below mirrors the platform's chat markdown
rendering (light theme, readable measure, bordered tables, styled code).
The model never authors HTML for this skill — it only supplies markdown.

Usage:
    python render.py -i /scratch/report.md -o /outputs/report.html [--title "..."]
"""
import argparse
import html as _html
import sys
from pathlib import Path

import markdown

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2.5rem 1.25rem; background: #f8fafc; color: #1f2937;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  font-size: 16px; line-height: 1.65;
}
main { max-width: 880px; margin: 0 auto; background: #ffffff; border: 1px solid #e5e7eb;
       border-radius: 12px; padding: 2.5rem 3rem; box-shadow: 0 1px 3px rgba(0,0,0,.06); }
h1, h2, h3, h4 { color: #111827; line-height: 1.3; margin: 1.6em 0 .6em; }
h1 { font-size: 1.9rem; margin-top: 0; }
h2 { font-size: 1.45rem; border-bottom: 1px solid #e5e7eb; padding-bottom: .35rem; }
h3 { font-size: 1.15rem; }
p { margin: .8em 0; }
a { color: #2563eb; text-decoration: none; }
a:hover { text-decoration: underline; }
table { border-collapse: collapse; width: 100%; margin: 1.2em 0; font-size: .95em; display: block; overflow-x: auto; }
th, td { border: 1px solid #e5e7eb; padding: .55em .9em; text-align: left; }
thead th { background: #f3f4f6; font-weight: 600; }
tbody tr:nth-child(even) { background: #fafafa; }
code { background: #f3f4f6; border-radius: 5px; padding: .15em .4em; font-size: .9em;
       font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
pre { background: #f6f8fa; border: 1px solid #e5e7eb; border-radius: 8px; padding: 1em 1.2em; overflow-x: auto; }
pre code { background: none; padding: 0; }
blockquote { margin: 1em 0; padding: .3em 1.1em; border-left: 4px solid #d1d5db; color: #4b5563; background: #f9fafb; }
ul, ol { padding-left: 1.6em; }
li { margin: .3em 0; }
hr { border: none; border-top: 1px solid #e5e7eb; margin: 2em 0; }
strong { color: #111827; }
@media print { body { background: #fff; padding: 0; } main { border: none; box-shadow: none; padding: 0; } }
"""

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(description="Render markdown to conversation-style HTML.")
    ap.add_argument("-i", "--input", required=True, help="Path to the input .md file.")
    ap.add_argument("-o", "--output", required=True, help="Path to write the .html file.")
    ap.add_argument("--title", default="", help="Page title (defaults to the first heading).")
    args = ap.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"error: input file not found: {in_path}", file=sys.stderr)
        sys.exit(2)
    text = in_path.read_text(encoding="utf-8")
    if not text.strip():
        print("error: input markdown is empty", file=sys.stderr)
        sys.exit(2)

    title = args.title.strip()
    if not title:
        for line in text.splitlines():
            if line.startswith("#"):
                title = line.lstrip("#").strip()
                break
    title = title or "Report"

    body = markdown.markdown(
        text,
        extensions=["extra", "tables", "fenced_code", "sane_lists", "toc"],
        output_format="html5",
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(TEMPLATE.format(title=_html.escape(title), css=CSS, body=body), encoding="utf-8")
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
