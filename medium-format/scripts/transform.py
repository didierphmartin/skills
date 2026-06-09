#!/usr/bin/env python3
"""
transform.py — Convert an HTML+CSS file into a Medium-compatible format.

Medium supports a restricted set of HTML tags and largely ignores CSS, so this
script:
  1. Resolves all CSS (linked, <style>, and inline style="...") into per-element
     effective styles.
  2. Maps style cues (font-weight, font-style, font-family) to Medium's semantic
     tags (<strong>, <em>, <code>).
  3. Unwraps containers (<div>, <span>, <section>, ...) that Medium ignores.
  4. Drops elements Medium discards entirely (<script>, <style>, <nav>, ...).
  5. Strips attributes Medium will not honor (class, id, style, data-*, etc.).
  6. Optionally wraps the cleaned body in a full HTML page containing the
     Open Graph metadata that Medium's "Import a story" tool requires.

Two output modes:
  --mode body     -> body content only, ready to paste into the Medium editor
                    or post via the legacy /v1/posts API (contentFormat=html).
  --mode page     -> full standalone HTML page with og:type/og:title/og:description
                    (and optionally article:published_time). Host this anywhere
                    publicly reachable, then paste the URL into
                    https://medium.com/p/import.

Usage:
    python transform.py input.html [-o output.html] [--mode body|page]
                        [--title "..."] [--description "..."]
                        [--canonical https://...] [--published 2024-05-01]
                        [--base-url https://myblog.com]
                        [--main-selector "article"]
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from html import escape
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

# Silence cssutils' chatty warnings about unsupported CSS3 properties.
import cssutils
cssutils.log.setLevel(logging.CRITICAL)


# ---------------------------------------------------------------------------
# Tag classification
# ---------------------------------------------------------------------------

# Tags Medium renders directly. Anything outside this set gets unwrapped or
# dropped depending on the category below.
ALLOWED_TAGS: set[str] = {
    "h1", "h2", "h3", "h4",
    "p", "br", "hr",
    "blockquote",
    "pre", "code",
    "ul", "ol", "li",
    "a", "img",
    "strong", "b", "em", "i", "u",
    "figure", "figcaption",
    "iframe",  # Medium converts iframes to embeds via Embedly when the URL is supported
}

# Containers/structural tags whose children we keep but the wrapper is removed.
# header is here (not in DROP_TAGS) because article-internal headers contain
# the title + dek/byline, which we want to keep.
UNWRAP_TAGS: set[str] = {
    "div", "span", "section", "article", "main",
    "header",
    "details", "summary",
    "small", "mark", "abbr", "cite", "q", "time", "kbd", "samp", "var",
}

# Tags removed entirely along with their contents. nav/footer/aside go here
# (not in UNWRAP_TAGS) because their contents are virtually never article
# content — they're site navigation, copyright lines, related-post lists, etc.
DROP_TAGS: set[str] = {
    "script", "style", "noscript", "template",
    "nav", "footer", "aside",
    "form", "input", "button", "select", "textarea", "label", "fieldset", "legend",
    "video", "audio", "source", "track", "canvas",
    "svg", "math",
    "object", "embed", "param", "applet",
    "meta", "link", "base",
}

# h5/h6 do not exist in Medium's editor → demote to h4.
HEADING_DEMOTE: dict[str, str] = {"h5": "h4", "h6": "h4"}

# Attributes we keep, organized per-tag. Everything else is stripped.
KEEP_ATTRS: dict[str, set[str]] = {
    "a": {"href", "title", "name"},
    "img": {"src", "alt", "title"},
    "iframe": {"src", "width", "height", "frameborder", "allowfullscreen"},
    "blockquote": {"class"},  # Medium uses class="big" / class="small" on blockquote
    "pre": {"data-language"},
    "code": {"class"},  # language-foo / lang-foo hint for syntax highlighting
}


# ---------------------------------------------------------------------------
# CSS resolution
# ---------------------------------------------------------------------------

class StyleResolver:
    """Computes effective CSS declarations for each element in the soup.

    Only handles selectors that BeautifulSoup's CSS engine (soupsieve) can match.
    Cascade is approximated by source order — sufficient for the bold/italic/
    monospace cues we actually use.
    """

    def __init__(self, soup: BeautifulSoup, css_sources: Iterable[str]):
        self.soup = soup
        self._element_styles: dict[int, dict[str, str]] = {}
        for source in css_sources:
            self._apply_stylesheet(source)
        self._merge_inline_styles()

    def _apply_stylesheet(self, css_text: str) -> None:
        try:
            sheet = cssutils.parseString(css_text)
        except Exception:
            return
        for rule in sheet:
            if rule.type != rule.STYLE_RULE:
                continue
            decls = self._declarations(rule.style)
            if not decls:
                continue
            for selector in rule.selectorList:
                try:
                    matched = self.soup.select(selector.selectorText)
                except Exception:
                    continue
                for el in matched:
                    self._element_styles.setdefault(id(el), {}).update(decls)

    def _merge_inline_styles(self) -> None:
        for el in self.soup.find_all(True):
            inline = el.get("style")
            if not inline:
                continue
            decls = self._declarations_from_text(inline)
            if decls:
                self._element_styles.setdefault(id(el), {}).update(decls)

    @staticmethod
    def _declarations(style) -> dict[str, str]:
        out: dict[str, str] = {}
        for prop in style:
            out[prop.name.lower()] = prop.value.strip()
        return out

    @staticmethod
    def _declarations_from_text(text: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for piece in text.split(";"):
            if ":" not in piece:
                continue
            name, _, value = piece.partition(":")
            name = name.strip().lower()
            value = value.strip()
            if name and value:
                out[name] = value
        return out

    def styles_for(self, el: Tag) -> dict[str, str]:
        """Effective styles for this element, walking up the ancestor chain
        so children inherit font-weight / font-style / font-family from parents."""
        chain: list[Tag] = []
        cur: Tag | None = el
        while cur is not None and isinstance(cur, Tag):
            chain.append(cur)
            cur = cur.parent
        merged: dict[str, str] = {}
        for ancestor in reversed(chain):
            merged.update(self._element_styles.get(id(ancestor), {}))
        return merged


def _is_bold(styles: dict[str, str]) -> bool:
    weight = styles.get("font-weight", "").lower()
    if weight in {"bold", "bolder"}:
        return True
    m = re.match(r"\d+", weight)
    if m and int(m.group(0)) >= 600:
        return True
    return False


def _is_italic(styles: dict[str, str]) -> bool:
    return styles.get("font-style", "").lower() in {"italic", "oblique"}


def _is_monospace(styles: dict[str, str]) -> bool:
    family = styles.get("font-family", "").lower()
    if not family:
        return False
    return any(token in family for token in (
        "monospace", "courier", "consolas", "menlo", "monaco",
        "ubuntu mono", "fira code", "source code", "roboto mono", "jetbrains mono",
    ))


def _font_size_is_small(value: str) -> bool:
    """Heuristic: True for font-size values that are visually 'small'
    (≤ 14px, ≤ 0.9em/0.9rem, or keyword 'small'/'x-small'). Used by the label
    detector to spot CSS-styled labels that don't have a giveaway class name."""
    if not value:
        return False
    v = value.strip().lower()
    if v in {"small", "x-small", "xx-small"}:
        return True
    m = re.match(r"([\d.]+)\s*([a-z%]*)", v)
    if not m:
        return False
    try:
        num = float(m.group(1))
    except ValueError:
        return False
    unit = m.group(2)
    if unit in {"px", "pt"}:
        return num <= 14
    if unit in {"em", "rem"}:
        return num <= 0.9
    if unit == "%":
        return num <= 90
    return False


# ---------------------------------------------------------------------------
# Transformation
# ---------------------------------------------------------------------------

class Transformer:
    def __init__(
        self,
        html: str,
        *,
        source_dir: Path | None = None,
        base_url: str | None = None,
        main_selector: str | None = None,
    ):
        self.soup = BeautifulSoup(html, "html.parser")
        self.source_dir = source_dir
        self.base_url = base_url
        self.main_selector = main_selector
        self.warnings: list[str] = []

        self._collect_css()
        self.styles = StyleResolver(self.soup, self._css_sources)

    # -- CSS collection -----------------------------------------------------

    def _collect_css(self) -> None:
        self._css_sources: list[str] = []
        # Linked stylesheets
        for link in self.soup.find_all("link", rel=True):
            rels = [r.lower() for r in (link.get("rel") or [])]
            if "stylesheet" not in rels:
                continue
            href = link.get("href")
            if not href:
                continue
            text = self._read_external(href)
            if text:
                self._css_sources.append(text)
        # <style> blocks
        for style in self.soup.find_all("style"):
            if style.string:
                self._css_sources.append(style.string)

    def _read_external(self, href: str) -> str | None:
        if href.startswith(("http://", "https://", "//", "data:")):
            self.warnings.append(
                f"Linked stylesheet {href!r} is remote and was not fetched; "
                "inline styling cues from it may be missing."
            )
            return None
        if not self.source_dir:
            self.warnings.append(
                f"Linked stylesheet {href!r} could not be resolved (no source directory)."
            )
            return None
        path = (self.source_dir / href).resolve()
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            self.warnings.append(f"Could not read linked CSS {path}: {exc}")
            return None

    # -- Main pipeline ------------------------------------------------------

    def transform(self) -> Tag:
        # 1. Pick out the article root if requested, otherwise use <body> or root.
        root = self._pick_root()

        # 2. Strip HTML comments — they aren't text, but BeautifulSoup's NavigableString
        #    treats them as such and the cleanup pass would otherwise wrap them in <p>.
        self._strip_comments(root)

        # 3. Drop forbidden tags (must happen before we lose <style> contents
        #    we already harvested).
        for tag in root.find_all(True):
            if tag.name in DROP_TAGS:
                tag.decompose()

        # 4. Detect semantic patterns (pull-quotes, dividers, tag chips) that are
        #    encoded as styled <div>s in the source. Has to run BEFORE container
        #    unwrapping or the class hints we depend on are gone.
        self._detect_semantic_patterns(root)

        # 5. Apply per-element style cues — wrap with <strong>/<em>/<code>
        #    while structure is still intact.
        self._apply_style_cues(root)

        # 6. Normalize specific tags (headings, blockquotes, code, images, etc.).
        self._normalize_tags(root)

        # 7. Unwrap structural containers.
        self._unwrap_containers(root)

        # 8. Strip disallowed attributes.
        self._strip_attrs(root)

        # 9. Strip inline formatting tags inside code blocks — <pre><code> is plain
        #    text by Medium's convention, not styled prose.
        self._flatten_code_blocks(root)

        # 10. Final cleanup: collapse empty paragraphs, merge stray text into <p>.
        self._final_cleanup(root)

        return root

    def _strip_comments(self, root: Tag) -> None:
        for comment in root.find_all(string=lambda s: isinstance(s, Comment)):
            comment.extract()

    def _detect_semantic_patterns(self, root: Tag) -> None:
        """Recover semantic meaning from styled containers before unwrapping.

        Detection runs in two layers:
        1. Class-name matching — fast, high-precision when class names follow
           common conventions (WordPress wp-block-*, Ghost kg-*, custom *-label,
           generic byline/pullquote/tags/sep).
        2. Structural fallbacks — when class names don't match, look for
           structural signals: a <cite> inside a div likely means a pull quote
           regardless of class; a <p> whose entire text is repeated divider
           punctuation is a divider; a tag-chip list is "many short same-tag
           siblings"; a byline-like row is "short text + multiple inline
           children separated by punctuation."

        The layers are deliberately additive: pattern recognition only fires
        when both a class hint and (where it matters) structural plausibility
        agree, OR when the structure alone is unambiguous. When neither layer
        fires, the element falls through to the standard unwrap pass — which
        is always safe because no content is lost.
        """
        # Keyword sets reviewed against WordPress (wp-block-*), Ghost (kg-*),
        # Substack (.pullquote, .subtitle), Medium-exported HTML, and common
        # hand-rolled blog conventions. Substring matching means "wp-block-
        # pullquote" matches "pullquote", "entry-meta" matches "meta", etc.
        pullquote_keywords = (
            "pullquote", "pull-quote", "blockquote", "quote-card", "quote-feature",
            "epigraph", "kg-quote",
        )
        divider_keywords = ("sep", "separator", "divider", "horizontal-rule")
        tag_keywords = (
            "tags", "post-tags", "tag-list", "taglist", "tag-cloud", "tagcloud",
            "categories", "category-list", "taxonomy", "topic-list",
        )
        byline_keywords = (
            "byline", "meta", "author-meta", "post-meta", "article-meta",
            "entry-meta", "post-info", "article-info", "post-header-meta",
        )

        for el in list(root.find_all(True)):
            if el.parent is None:
                continue
            classes = " ".join(el.get("class", [])).lower() if isinstance(el.get("class"), list) else ""

            # ---- Pull quotes -----------------------------------------------
            if el.name in {"div", "aside", "figure", "section"}:
                hit_by_class = any(kw in classes for kw in pullquote_keywords)
                # Structural fallback: any container holding a <cite> alongside
                # a <p> is, with very high probability, a pull quote.
                hit_by_structure = (
                    not hit_by_class
                    and el.find("cite") is not None
                    and el.find("p") is not None
                    # Don't grab the whole article body if it happens to contain a cite somewhere.
                    and len(el.find_all(["h1", "h2", "h3", "h4"], recursive=True)) == 0
                    and len(el.get_text(strip=True)) < 600
                )
                if hit_by_class or hit_by_structure:
                    self._convert_to_blockquote(el)
                    continue

            # Existing <blockquote> with a <cite> inside: keep the attribution
            # text, since <cite> will otherwise be unwrapped to plain text and
            # lose the em-dash convention readers expect.
            if el.name == "blockquote" and el.find("cite") is not None:
                self._normalize_existing_blockquote(el)
                continue

            # ---- Section dividers ------------------------------------------
            if el.name in {"div", "p", "hr"}:
                if any(kw in classes for kw in divider_keywords):
                    el.replace_with(self.soup.new_tag("hr"))
                    continue
                text = el.get_text(strip=True)
                if text and len(text) <= 20 and self._is_divider_text(text) and not el.find(True):
                    el.replace_with(self.soup.new_tag("hr"))
                    continue

            # ---- Tag chip lists --------------------------------------------
            if el.name in {"div", "ul", "section", "footer", "nav"}:
                hit_by_class = any(kw in classes for kw in tag_keywords)
                # Structural fallback: any container whose ALL children are
                # short same-tag siblings (>=3 of them) looks like a chip list.
                children = [c for c in el.children if isinstance(c, Tag)]
                hit_by_structure = (
                    not hit_by_class
                    and len(children) >= 3
                    and all(
                        c.name in {"span", "a", "li"} and len(c.get_text(strip=True)) < 40
                        for c in children
                    )
                    # Avoid falsely matching navigation lists at the top of the doc.
                    and self._is_near_end_of_root(el, root)
                )
                if hit_by_class:
                    if children and all(
                        c.name in {"span", "a", "li"} and len(c.get_text(strip=True)) < 40
                        for c in children
                    ):
                        el.decompose()
                        self.warnings.append(
                            "Dropped a tag-chip list. Medium has its own tag system; "
                            "add tags in the editor before publishing."
                        )
                        continue
                elif hit_by_structure:
                    el.decompose()
                    self.warnings.append(
                        "Dropped a likely tag-chip list at the end of the document."
                    )
                    continue

            # ---- Bylines / metadata rows -----------------------------------
            # HTML5 <address> is the semantic element for contact/byline info
            # at the top of an article — handle it explicitly.
            if el.name == "address":
                self._collapse_to_paragraph(el)
                continue
            if el.name in {"div", "p"} and any(kw in classes for kw in byline_keywords):
                self._collapse_to_paragraph(el)
                continue

            # ---- Labeled-section captions ----------------------------------
            if el.name == "div" and self._looks_like_label(classes, self.styles.styles_for(el)):
                text = el.get_text(" ", strip=True)
                if text and len(text) <= 120 and not el.find(["h1", "h2", "h3", "h4", "p", "ul", "ol"]):
                    p = self.soup.new_tag("p")
                    strong = self.soup.new_tag("strong")
                    strong.string = text
                    p.append(strong)
                    el.replace_with(p)

    @staticmethod
    def _looks_like_label(classes: str, styles: dict[str, str]) -> bool:
        # Class-based signal — covers the common naming conventions.
        if classes:
            for token in classes.split():
                if token.endswith(("-label", "-header", "-title")):
                    return True
                if any(kw in token for kw in ("kicker", "caption", "eyebrow", "category-label", "overline")):
                    return True
        # CSS-based signal — small uppercase text with letter-spacing is the
        # universal "label" typographic convention. If the class names didn't
        # match but the styling shouts "label," accept it.
        text_transform = styles.get("text-transform", "").lower()
        letter_spacing = styles.get("letter-spacing", "")
        font_size = styles.get("font-size", "")
        if text_transform == "uppercase" and (letter_spacing or _font_size_is_small(font_size)):
            return True
        return False

    @staticmethod
    def _is_near_end_of_root(el: Tag, root: Tag) -> bool:
        """True if `el` is in the last quarter of `root`'s direct descendants.
        Used to avoid mis-classifying a top-of-page nav as a tag-chip list."""
        all_desc = [d for d in root.descendants if isinstance(d, Tag)]
        if not all_desc:
            return False
        try:
            idx = all_desc.index(el)
        except ValueError:
            return False
        return idx >= int(len(all_desc) * 0.75)

    def _normalize_existing_blockquote(self, bq: Tag) -> None:
        """Move <cite> attribution inside a blockquote into trailing text with
        a leading em-dash so it survives the cite unwrapping."""
        cite = bq.find("cite")
        if cite is None:
            return
        cite_text = cite.get_text(" ", strip=True)
        cite.decompose()
        if cite_text:
            if not cite_text.lstrip().startswith(("—", "-", "–")):
                cite_text = "— " + cite_text
            bq.append(self.soup.new_tag("br"))
            bq.append(self.soup.new_tag("br"))
            bq.append(NavigableString(cite_text))

    def _collapse_to_paragraph(self, el: Tag) -> None:
        """Take a div whose children are inline-ish (spans, strongs, text) and
        produce a single <p> joining their text with single spaces. Preserves
        <strong>/<em>/<code>/<a> so inline emphasis survives.

        Used for bylines and similar metadata rows where the source HTML uses
        many sibling <span>s for layout — without this they fragment into a
        cascade of one-word paragraphs after we unwrap the container.
        """
        # Flat walk that produces a list of (kind, payload) entries.
        # kind ∈ {"text", "strong", "em", "code", "a"}
        # payload is the text, except for "a" where it's (href, text).
        parts: list = []

        def walk(node):
            if isinstance(node, Comment):
                return
            if isinstance(node, NavigableString):
                t = str(node).strip()
                if t:
                    parts.append(("text", t))
                return
            if not isinstance(node, Tag):
                return

            if node.name in {"strong", "b"}:
                t = node.get_text(" ", strip=True)
                if t:
                    parts.append(("strong", t))
                return
            if node.name in {"em", "i"}:
                t = node.get_text(" ", strip=True)
                if t:
                    parts.append(("em", t))
                return
            if node.name == "code":
                t = node.get_text(" ", strip=True)
                if t:
                    parts.append(("code", t))
                return
            if node.name == "a":
                t = node.get_text(" ", strip=True)
                if t:
                    parts.append(("a", node.get("href", ""), t))
                return
            for c in node.children:
                walk(c)

        walk(el)

        if not parts:
            el.decompose()
            return

        p = self.soup.new_tag("p")
        for i, item in enumerate(parts):
            if i > 0:
                p.append(NavigableString(" "))
            kind = item[0]
            if kind == "text":
                p.append(NavigableString(item[1]))
            elif kind in {"strong", "em", "code"}:
                tag = self.soup.new_tag(kind)
                tag.string = item[1]
                p.append(tag)
            elif kind == "a":
                tag = self.soup.new_tag("a", href=item[1])
                tag.string = item[2]
                p.append(tag)

        el.replace_with(p)

    @staticmethod
    def _is_divider_text(text: str) -> bool:
        # Strip whitespace, then check whether what remains is just divider chars.
        compact = re.sub(r"\s+", "", text)
        if len(compact) < 2:
            return False
        divider_chars = set("·•・*-—–=~_.♦◆◇○●⋅⸱")
        return all(ch in divider_chars for ch in compact)

    def _convert_to_blockquote(self, el: Tag) -> None:
        # Collect the meaningful children: a <p> (the quote) and optionally a <cite>.
        quote_text = []
        cite_text = None
        for child in el.find_all(["p", "cite", "footer"], recursive=True):
            text = child.get_text(" ", strip=True)
            if not text:
                continue
            if child.name == "cite" or child.name == "footer":
                cite_text = text
            else:
                quote_text.append(text)

        if not quote_text and not cite_text:
            # Nothing usable — let the unwrap pass handle it normally.
            return

        bq = self.soup.new_tag("blockquote")
        bq["class"] = ["big"]
        bq.string = " ".join(quote_text) if quote_text else ""

        if cite_text:
            # Append the attribution on a new line inside the same blockquote.
            # Medium will preserve the line break.
            br = self.soup.new_tag("br")
            bq.append(br)
            bq.append(self.soup.new_tag("br"))
            # Ensure the em-dash is there if the original used one.
            if not cite_text.lstrip().startswith(("—", "-", "–")):
                cite_text = "— " + cite_text
            bq.append(NavigableString(cite_text))

        el.replace_with(bq)

    def _flatten_code_blocks(self, root: Tag) -> None:
        # Inside <pre>, strip out inline formatting tags (em/strong/i/b/u) but keep
        # their text. Code blocks are plain text — italicized comments would render
        # as italics inside the block, which Medium does not intend.
        for pre in root.find_all("pre"):
            for tag in pre.find_all(["em", "strong", "i", "b", "u"]):
                tag.unwrap()

    def _pick_root(self) -> Tag:
        if self.main_selector:
            picked = self.soup.select_one(self.main_selector)
            if picked is not None:
                return self._augment_with_external_title(picked)
            self.warnings.append(
                f"--main-selector {self.main_selector!r} matched nothing; "
                "falling back to <body>."
            )
        body = self.soup.body
        if body is not None:
            return body
        # No <body> — wrap everything in a synthetic container so we have a Tag.
        wrapper = self.soup.new_tag("div")
        for child in list(self.soup.contents):
            wrapper.append(child.extract())
        return wrapper

    def _augment_with_external_title(self, picked: Tag) -> Tag:
        """If the user's selector matched a body-only element but the article's
        title/dek live in a sibling <header> outside it (a common pattern), pull
        those in. Without this, --main-selector="article" silently strips titles
        for any page that puts <header><h1>...</h1></header> next to <article>.

        We only pull in the *closest* preceding <header> sibling (or the closest
        ancestor's preceding <header>), and only if the picked element doesn't
        already contain its own <h1>. Site-level headers full of nav links are
        cleaned up by the existing UNWRAP/DROP passes.
        """
        # If the picked element already has its own h1, assume the title is inside.
        if picked.find("h1"):
            return picked

        external_header = self._find_external_title_block(picked)
        if external_header is None:
            return picked

        # Build a synthetic wrapper so we can carry both into the pipeline.
        wrapper = self.soup.new_tag("div")
        # Move the external header in first, then the picked element.
        wrapper.append(external_header.extract())
        # picked might have been detached by extract() above if it was a sibling
        # of the header's ancestor — re-find it via the soup if needed.
        wrapper.append(picked)
        return wrapper

    def _find_external_title_block(self, picked: Tag) -> Tag | None:
        """Walk up from picked, looking at previous siblings at each level for a
        <header> or standalone <h1> that probably holds the article title."""
        cur: Tag | None = picked
        while cur is not None and isinstance(cur, Tag) and cur.name != "[document]":
            sibling = cur.previous_sibling
            while sibling is not None:
                if isinstance(sibling, Tag):
                    if sibling.name == "header" and sibling.find(["h1", "h2"]):
                        return sibling
                    if sibling.name == "h1":
                        return sibling
                sibling = sibling.previous_sibling
            cur = cur.parent
        return None

    # -- Style cues → semantic tags ----------------------------------------

    def _apply_style_cues(self, root: Tag) -> None:
        # Style cues (bold/italic/monospace) only apply to inline elements.
        # CSS commonly puts a monospace font on bylines, captions, and labels for
        # typographic flair — those are NOT code, and wrapping them in <code> is
        # the worst kind of false positive. So we restrict cues to a hand-picked
        # set of inline tags that are plausibly carrying inline formatting, and
        # require them to be inside a text-bearing block.
        inline_for_cues = {"span", "font", "small", "mark", "kbd", "samp", "var", "u"}
        text_block_ancestors = {"p", "li", "h1", "h2", "h3", "h4", "blockquote", "figcaption", "td", "th"}

        for el in list(root.find_all(True)):
            if el.parent is None:
                continue
            if el.name not in inline_for_cues:
                continue
            # Must be inside running prose to be a meaningful inline cue.
            if not self._inside(el, text_block_ancestors):
                continue

            styles = self.styles.styles_for(el)

            wrap_with: list[str] = []
            if _is_bold(styles) and not self._inside(el, {"strong", "b", "h1", "h2", "h3", "h4"}):
                wrap_with.append("strong")
            if _is_italic(styles) and not self._inside(el, {"em", "i"}):
                wrap_with.append("em")
            if _is_monospace(styles) and not self._inside(el, {"code", "pre"}):
                wrap_with.append("code")

            if not wrap_with:
                continue
            if not el.get_text(strip=True):
                continue

            # Replace the inline wrapper with nested semantic tags.
            inner = list(el.contents)
            outermost = None
            inner_target = None
            for tag_name in wrap_with:
                new_tag = self.soup.new_tag(tag_name)
                if outermost is None:
                    outermost = new_tag
                    inner_target = new_tag
                else:
                    inner_target.append(new_tag)
                    inner_target = new_tag
            for child in inner:
                inner_target.append(child.extract())
            el.replace_with(outermost)

    @staticmethod
    def _inside(el: Tag, names: set[str]) -> bool:
        cur = el.parent
        while cur is not None and isinstance(cur, Tag):
            if cur.name in names:
                return True
            cur = cur.parent
        return False

    # -- Tag-specific normalization ----------------------------------------

    def _normalize_tags(self, root: Tag) -> None:
        for el in list(root.find_all(True)):
            if el.parent is None:
                continue

            # h5/h6 → h4
            if el.name in HEADING_DEMOTE:
                el.name = HEADING_DEMOTE[el.name]

            # <font> → unwrap (handled by container pass too, but normalize early)
            if el.name == "font":
                el.unwrap()
                continue

            # Resolve relative URLs on images and links if a base URL was provided.
            if el.name == "img":
                self._normalize_img(el)
            elif el.name == "a":
                self._normalize_link(el)
            elif el.name == "iframe":
                self._normalize_iframe(el)
            elif el.name == "pre":
                self._normalize_pre(el)
            elif el.name == "blockquote":
                self._normalize_blockquote(el)
            elif el.name == "table":
                self._normalize_table(el)

    def _normalize_img(self, img: Tag) -> None:
        # Drop pure tracking pixels / 1x1 spacers.
        src = img.get("src", "").strip()
        if not src:
            img.decompose()
            return
        if self.base_url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:|^//", src):
            img["src"] = urljoin(self.base_url, src)
        elif not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:|^//", src) and not src.startswith("/"):
            self.warnings.append(
                f"<img src={src!r}> is a relative path; pass --base-url so Medium can fetch it."
            )

        # If the image is inside a parent that is just an <a> wrapping nothing else,
        # leave it; Medium handles linked images.

    def _normalize_link(self, a: Tag) -> None:
        href = a.get("href", "").strip()
        if not href:
            a.unwrap()
            return
        if self.base_url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:|^//|^#", href):
            a["href"] = urljoin(self.base_url, href)

    def _normalize_iframe(self, iframe: Tag) -> None:
        src = iframe.get("src", "").strip()
        if not src:
            iframe.decompose()

    def _normalize_pre(self, pre: Tag) -> None:
        # Medium expects code blocks as <pre><code>...</code></pre>. If the <pre>
        # has only text or non-<code> children, wrap them in a <code>.
        has_code_child = any(
            isinstance(c, Tag) and c.name == "code" for c in pre.contents
        )
        if has_code_child:
            return
        code = self.soup.new_tag("code")
        for child in list(pre.contents):
            code.append(child.extract())
        pre.append(code)

    def _normalize_blockquote(self, bq: Tag) -> None:
        # Medium understands blockquote class="big" and class="small". If the
        # original CSS hinted at a pull-quote style (large font-size), upgrade
        # to class="big".
        styles = self.styles.styles_for(bq)
        size = styles.get("font-size", "")
        if size and re.match(r"^\d", size):
            try:
                value = float(re.match(r"[\d.]+", size).group(0))
                unit = size[len(re.match(r"[\d.]+", size).group(0)):].strip()
                if (unit in {"px", "pt"} and value >= 24) or (unit == "em" and value >= 1.5):
                    bq["class"] = ["big"]
            except (AttributeError, ValueError):
                pass

    def _normalize_table(self, table: Tag) -> None:
        # Medium's editor does NOT preserve tables. Convert to a fenced-style
        # paragraph block so the content survives and the user can rebuild it.
        self.warnings.append(
            "A <table> was converted to plain text; Medium does not render HTML tables. "
            "You will need to re-create it manually using Medium's table tool."
        )
        rows: list[str] = []
        for tr in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["td", "th"])]
            if cells:
                rows.append(" | ".join(cells))
        replacement = self.soup.new_tag("pre")
        code = self.soup.new_tag("code")
        code.string = "\n".join(rows) if rows else ""
        replacement.append(code)
        table.replace_with(replacement)

    # -- Container unwrapping ----------------------------------------------

    def _unwrap_containers(self, root: Tag) -> None:
        # Repeated passes because unwrapping can expose nested containers.
        changed = True
        while changed:
            changed = False
            for el in list(root.find_all(True)):
                if el.parent is None:
                    continue
                if el.name in UNWRAP_TAGS:
                    el.unwrap()
                    changed = True
                elif el.name not in ALLOWED_TAGS and el.name not in {"figure", "figcaption"}:
                    # Anything else that slipped through (custom elements, <font>, etc.)
                    el.unwrap()
                    changed = True

    # -- Attribute stripping -----------------------------------------------

    def _strip_attrs(self, root: Tag) -> None:
        for el in root.find_all(True):
            keep = KEEP_ATTRS.get(el.name, set())
            for attr in list(el.attrs.keys()):
                if attr not in keep:
                    del el.attrs[attr]
            # Class on blockquote: only allow "big" or "small".
            if el.name == "blockquote" and "class" in el.attrs:
                cls = [c for c in el.get("class", []) if c in {"big", "small"}]
                if cls:
                    el["class"] = cls
                else:
                    del el.attrs["class"]
            # Class on code: only allow language-* / lang-* hints.
            if el.name == "code" and "class" in el.attrs:
                cls = [c for c in el.get("class", []) if c.startswith(("language-", "lang-"))]
                if cls:
                    el["class"] = cls
                else:
                    del el.attrs["class"]

    # -- Final cleanup ------------------------------------------------------

    def _final_cleanup(self, root: Tag) -> None:
        # Remove paragraphs that are empty or whitespace-only.
        for p in list(root.find_all("p")):
            if not p.get_text(strip=True) and not p.find("img"):
                p.decompose()

        # Promote stray text directly under root into <p>. Comment nodes are
        # subclasses of NavigableString — skip them; they should already have
        # been stripped earlier, but a defensive check is cheap.
        for child in list(root.contents):
            if isinstance(child, Comment):
                child.extract()
                continue
            if isinstance(child, NavigableString):
                text = str(child).strip()
                if text:
                    p = self.soup.new_tag("p")
                    p.string = text
                    child.replace_with(p)
                else:
                    child.extract()


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------

def render_body_html(root: Tag) -> str:
    """Inner HTML of the cleaned root, suitable for paste-into-editor or API."""
    return "".join(str(c) for c in root.contents).strip()


def render_full_page(
    body_html: str,
    *,
    title: str,
    description: str = "",
    canonical: str = "",
    published: str = "",
    author: str = "",
) -> str:
    """Wrap body_html in a standalone HTML page with Medium-required metadata.

    Required for the URL-based import tool (https://medium.com/p/import) — Medium
    relies on Open Graph tags + an article body it can extract.
    """
    meta_lines = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<title>{escape(title)}</title>',
        '<meta property="og:type" content="article">',
        f'<meta property="og:title" content="{escape(title)}">',
    ]
    if description:
        meta_lines.append(f'<meta name="description" content="{escape(description)}">')
        meta_lines.append(f'<meta property="og:description" content="{escape(description)}">')
    if canonical:
        meta_lines.append(f'<link rel="canonical" href="{escape(canonical)}">')
        meta_lines.append(f'<meta property="og:url" content="{escape(canonical)}">')
    if author:
        meta_lines.append(f'<meta name="author" content="{escape(author)}">')
        meta_lines.append(f'<meta property="article:author" content="{escape(author)}">')
    if published:
        # Medium's import tool reads article:published_time to backdate the post.
        meta_lines.append(f'<meta property="article:published_time" content="{escape(published)}">')

    head = "\n  ".join(meta_lines)
    return (
        '<!DOCTYPE html>\n'
        '<html lang="en" prefix="og: http://ogp.me/ns#">\n'
        '<head>\n'
        f'  {head}\n'
        '</head>\n'
        '<body>\n'
        '<article>\n'
        f'{body_html}\n'
        '</article>\n'
        '</body>\n'
        '</html>\n'
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert HTML+CSS into a Medium-compatible format."
    )
    parser.add_argument("input", type=Path, help="Input HTML file.")
    parser.add_argument("-o", "--output", type=Path, help="Output file (default: stdout).")
    parser.add_argument(
        "--mode",
        choices=("body", "page"),
        default="body",
        help="'body' = body content only (paste/API). 'page' = full HTML with OG metadata for the URL import tool.",
    )
    parser.add_argument("--title", help="Article title (page mode). Defaults to the first <h1> or <title> in the source.")
    parser.add_argument("--description", default="", help="og:description / meta description (page mode).")
    parser.add_argument("--canonical", default="", help="Canonical URL of the original article (page mode).")
    parser.add_argument("--published", default="", help="article:published_time, ISO 8601 (page mode). Used by Medium to backdate the imported post.")
    parser.add_argument("--author", default="", help="Author name (page mode).")
    parser.add_argument("--base-url", default=None, help="Base URL for resolving relative links/images.")
    parser.add_argument(
        "--main-selector",
        default=None,
        help="CSS selector for the article container (e.g. 'article', '.post-content'). If omitted, the whole <body> is used.",
    )
    args = parser.parse_args(argv)

    html = args.input.read_text(encoding="utf-8")
    transformer = Transformer(
        html,
        source_dir=args.input.parent,
        base_url=args.base_url,
        main_selector=args.main_selector,
    )
    root = transformer.transform()
    body_html = render_body_html(root)

    if args.mode == "page":
        title = args.title or _guess_title(transformer.soup) or "Untitled"
        out = render_full_page(
            body_html,
            title=title,
            description=args.description,
            canonical=args.canonical,
            published=args.published,
            author=args.author,
        )
    else:
        out = body_html + "\n"

    if args.output:
        args.output.write_text(out, encoding="utf-8")
        print(f"Wrote {args.output} ({len(out)} bytes)", file=sys.stderr)
    else:
        sys.stdout.write(out)

    if transformer.warnings:
        print("\nWarnings:", file=sys.stderr)
        for w in transformer.warnings:
            print(f"  - {w}", file=sys.stderr)

    return 0


def _guess_title(soup: BeautifulSoup) -> str | None:
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    return None


if __name__ == "__main__":
    sys.exit(main())
