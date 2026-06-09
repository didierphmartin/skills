#!/usr/bin/env python3
"""
transform.py — Convert any HTML document into clean Markdown.

Goal: strip the visual decoration of an HTML document and surface its
cognitive structure (titles, headings, paragraphs, lists, code, links,
images) as plain Markdown. Useful both as a "simplified view" of a page
and as a feed for downstream LLM/processing pipelines.

Pipeline (mirrors the medium-format skill):
  1. Parse the HTML.
  2. Pick the article root (--main-selector if given, else <main>/<article>
     auto-detection, else <body>).
  3. Drop noise tags entirely (script, style, nav, footer, aside, form, …).
  4. Normalize a few tags so markdownify produces nice output (resolve
     relative URLs against --base-url, demote h5/h6 to h4, drop empty links).
  5. Render the cleaned subtree to Markdown via the `markdownify` library
     (GitHub-flavored: ATX headings, fenced code, pipe tables).
  6. Post-process: collapse runs of blank lines, trim trailing whitespace.

Usage:
    python transform.py input.html [-o output.md]
                        [--base-url https://myblog.com]
                        [--main-selector "article"]
                        [--keep-images]   (default: kept; use --no-images to drop)
                        [--heading-style {atx,setext}]   (default: atx)
                        [--bullets "-"]   (default: '-')

If --output is omitted, the result is written to
~/Documents/synergyAI/outputs/<input-stem>.md
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

try:
    from markdownify import markdownify, ATX, ATX_CLOSED, SETEXT
except ImportError:
    print(
        "ERROR: the 'markdownify' package is required.\n"
        "Install it with: pip install markdownify",
        file=sys.stderr,
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# Tag classification
# ---------------------------------------------------------------------------

# Tags removed entirely along with their contents — pure noise from a
# cognitive-structure standpoint.
DROP_TAGS: set[str] = {
    "script", "style", "noscript", "template",
    "nav", "footer", "aside",
    "form", "input", "button", "select", "textarea", "label",
    "fieldset", "legend",
    "video", "audio", "source", "track", "canvas",
    "svg", "math",
    "object", "embed", "param", "applet",
    "meta", "link", "base",
    "iframe",  # noisy for cognitive-structure view; opt back in if needed
}

# Headings deeper than h4 get demoted (most renderers blur them anyway and
# Markdown's h5/h6 read like fine print).
HEADING_DEMOTE: dict[str, str] = {"h5": "####", "h6": "####"}

# Heuristics for auto-detecting the article container when no --main-selector
# is given. Tried in order; first match wins.
AUTO_ROOT_SELECTORS: list[str] = [
    "main",
    "article",
    "[role='main']",
    "#content",
    "#main",
    ".post-content",
    ".entry-content",
    ".article-content",
]


# ---------------------------------------------------------------------------
# Default output directory
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT_DIR = Path.home() / "Documents" / "synergyAI" / "outputs"


# ---------------------------------------------------------------------------
# Transformation
# ---------------------------------------------------------------------------

class Transformer:
    def __init__(
        self,
        html: str,
        *,
        base_url: str | None = None,
        main_selector: str | None = None,
        keep_images: bool = True,
    ):
        self.soup = BeautifulSoup(html, "html.parser")
        self.base_url = base_url
        self.main_selector = main_selector
        self.keep_images = keep_images
        self.warnings: list[str] = []

    # -- Main pipeline ------------------------------------------------------

    def transform(self) -> Tag:
        # 1. Strip HTML comments — they shouldn't survive into Markdown.
        for c in self.soup.find_all(string=lambda t: isinstance(t, Comment)):
            c.extract()

        # 2. Pick the article root.
        root = self._pick_root()

        # 3. Drop noise tags (must happen on the chosen root, not the whole
        #    soup, in case the root itself was inside a <main> we picked).
        for tag in list(root.find_all(True)):
            if tag.name in DROP_TAGS:
                tag.decompose()

        # 4. Optionally drop images.
        if not self.keep_images:
            for img in root.find_all("img"):
                img.decompose()

        # 5. Normalize specific tags before handing to markdownify.
        self._normalize_tags(root)

        return root

    def _pick_root(self) -> Tag:
        if self.main_selector:
            picked = self.soup.select_one(self.main_selector)
            if picked is not None:
                return picked
            self.warnings.append(
                f"--main-selector {self.main_selector!r} matched nothing; "
                "falling back to auto-detection."
            )
        # Auto-detect
        for sel in AUTO_ROOT_SELECTORS:
            picked = self.soup.select_one(sel)
            if picked is not None and picked.get_text(strip=True):
                return picked
        # Fall back to <body>, then to a synthetic wrapper of root contents.
        if self.soup.body is not None:
            return self.soup.body
        wrapper = self.soup.new_tag("div")
        for child in list(self.soup.contents):
            wrapper.append(child.extract())
        return wrapper

    # -- Tag-specific normalization ----------------------------------------

    def _normalize_tags(self, root: Tag) -> None:
        for el in list(root.find_all(True)):
            if el.parent is None:
                continue

            # h5/h6 → h4 (Markdown supports h6 but it usually reads as noise)
            if el.name in {"h5", "h6"}:
                el.name = "h4"
                continue

            if el.name == "a":
                self._normalize_link(el)
            elif el.name == "img":
                self._normalize_img(el)
            elif el.name == "pre":
                self._normalize_pre(el)

    def _normalize_link(self, a: Tag) -> None:
        href = (a.get("href") or "").strip()
        if not href:
            # Empty/anchor-only links: keep the text, drop the wrapper.
            a.unwrap()
            return
        # Skip pure intra-page anchors — markdownify keeps them but they're
        # often noise in a cognitive view.
        if href.startswith("#"):
            return
        if self.base_url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:|^//", href):
            a["href"] = urljoin(self.base_url, href)

    def _normalize_img(self, img: Tag) -> None:
        src = (img.get("src") or "").strip()
        if not src:
            img.decompose()
            return
        if self.base_url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:|^//", src):
            img["src"] = urljoin(self.base_url, src)
        elif not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:|^//", src) and not src.startswith("/"):
            self.warnings.append(
                f"<img src={src!r}> is a relative path; "
                "pass --base-url to make it resolvable."
            )

    def _normalize_pre(self, pre: Tag) -> None:
        # Markdownify renders <pre><code>…</code></pre> as a fenced code block.
        # If the <pre> wraps raw text without an inner <code>, add one so we
        # get a fenced block (not a quoted paragraph).
        has_code_child = any(
            isinstance(c, Tag) and c.name == "code" for c in pre.contents
        )
        if has_code_child:
            return
        code = self.soup.new_tag("code")
        for child in list(pre.contents):
            code.append(child.extract())
        pre.append(code)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render_markdown(
    root: Tag,
    *,
    heading_style: str = "atx",
    bullets: str = "-",
) -> str:
    style_map = {"atx": ATX, "atx_closed": ATX_CLOSED, "setext": SETEXT}
    md = markdownify(
        str(root),
        heading_style=style_map.get(heading_style, ATX),
        bullets=bullets,
        strip=["script", "style"],         # belt and suspenders
        code_language_callback=_code_lang,
        escape_asterisks=False,
        escape_underscores=False,
    )
    return _post_process(md)


def _code_lang(el) -> str:
    """Pull a `language-foo` / `lang-foo` class off a <code> tag for the
    fenced code block hint. Returns '' when nothing useful is found."""
    cls = el.get("class") or []
    for c in cls:
        if c.startswith(("language-", "lang-")):
            return c.split("-", 1)[1]
    return ""


def _post_process(md: str) -> str:
    # Collapse runs of >2 blank lines.
    md = re.sub(r"\n{3,}", "\n\n", md)
    # Trim trailing spaces on each line.
    md = re.sub(r"[ \t]+$", "", md, flags=re.MULTILINE)
    # Strip leading/trailing whitespace.
    return md.strip() + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert any HTML document into clean Markdown."
    )
    parser.add_argument("input", type=Path, help="Input HTML file.")
    parser.add_argument(
        "-o", "--output", type=Path,
        help=f"Output .md file. Default: {DEFAULT_OUTPUT_DIR}/<input-stem>.md",
    )
    parser.add_argument(
        "--base-url", default=None,
        help="Base URL for resolving relative <a href> and <img src>.",
    )
    parser.add_argument(
        "--main-selector", default=None,
        help="CSS selector for the article container (e.g. 'article', "
             "'.post-content'). If omitted, auto-detects <main>/<article>/etc., "
             "falling back to <body>.",
    )
    parser.add_argument(
        "--no-images", action="store_true",
        help="Drop all <img> tags. Useful when only the text matters.",
    )
    parser.add_argument(
        "--heading-style", choices=("atx", "atx_closed", "setext"),
        default="atx",
        help="Markdown heading style. Default: atx (# Heading).",
    )
    parser.add_argument(
        "--bullets", default="-",
        help="Bullet character(s) for unordered lists. Default: '-'.",
    )
    parser.add_argument(
        "--stdout", action="store_true",
        help="Print to stdout instead of writing a file.",
    )
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
        return 1

    html = args.input.read_text(encoding="utf-8", errors="replace")
    transformer = Transformer(
        html,
        base_url=args.base_url,
        main_selector=args.main_selector,
        keep_images=not args.no_images,
    )
    root = transformer.transform()
    md = render_markdown(
        root,
        heading_style=args.heading_style,
        bullets=args.bullets,
    )

    if args.stdout:
        sys.stdout.write(md)
    else:
        out_path = args.output
        if out_path is None:
            DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
            out_path = DEFAULT_OUTPUT_DIR / f"{args.input.stem}.md"
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        print(f"Wrote {out_path} ({len(md)} bytes)", file=sys.stderr)

    if transformer.warnings:
        print("\nWarnings:", file=sys.stderr)
        for w in transformer.warnings:
            print(f"  - {w}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
