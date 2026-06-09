"""
Write an HTML document to disk.

Pass-through by design: the model authors the HTML; this script validates it
parses, optionally reformats it via BeautifulSoup.prettify(), and writes it
to the output path so it lands in /outputs/ for the right-pane preview.

Usage:
    python create.py -i /scratch/in.html -o /outputs/out.html [--pretty] [--fragment]
"""
import argparse
import sys
from pathlib import Path

from bs4 import BeautifulSoup


def parse_args():
    p = argparse.ArgumentParser(description="Write an HTML document to /outputs/.")
    p.add_argument("-i", "--input", required=True, help="Path to input HTML file.")
    p.add_argument("-o", "--output", required=True, help="Path to write the HTML.")
    p.add_argument("--pretty", action="store_true",
                   help="Reformat with BeautifulSoup.prettify() before writing.")
    p.add_argument("--fragment", action="store_true",
                   help="Allow a fragment (skip the missing-doctype/html/head/body warnings).")
    return p.parse_args()


def warn(msg):
    print(f"warning: {msg}", file=sys.stderr)


def main():
    args = parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    if not in_path.exists():
        print(f"error: input file not found: {in_path}", file=sys.stderr)
        sys.exit(2)

    raw = in_path.read_text(encoding="utf-8")
    if not raw.strip():
        print("error: input HTML is empty", file=sys.stderr)
        sys.exit(2)

    soup = BeautifulSoup(raw, "html.parser")

    if not args.fragment:
        lower = raw.lstrip().lower()
        if not lower.startswith("<!doctype"):
            warn("missing <!doctype html> — browsers will fall back to quirks mode")
        if not soup.find("html"):
            warn("missing <html> root element")
        if not soup.find("head"):
            warn("missing <head> — title, meta, and inline CSS belong here")
        if not soup.find("body"):
            warn("missing <body>")

    output = soup.prettify() if args.pretty else raw

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output, encoding="utf-8")

    size = out_path.stat().st_size
    print(f"wrote {out_path} ({size} bytes)")


if __name__ == "__main__":
    main()
