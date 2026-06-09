---
name: html-to-markdown
description: "Use this skill whenever the user wants to convert, transform, simplify, strip, distill, or reduce an HTML document (file, fragment, or saved page) into clean Markdown — including phrases like 'turn this HTML into Markdown', 'convert .html to .md', 'simplify this page', 'strip the styling', 'extract the article as Markdown', 'give me a clean text version', 'just the content of this page', 'cognitive view of this document'. Triggers on any request to take HTML and emit raw Markdown that surfaces document structure (titles, headings, paragraphs, lists, code, links, images) without visual decoration. Useful both as a human reading aid and as a clean feed for downstream LLM/processing pipelines. Do NOT use for: HTML-to-HTML cleanup (use medium-format), HTML-to-PDF, HTML-to-DOCX, or Markdown-to-HTML conversion."
dependencies: [beautifulsoup4, markdownify]
---

# html-to-markdown

Convert any HTML document into clean Markdown. The point is to **strip the visual decoration** of the source page and surface its **cognitive structure** — titles, headings, paragraphs, lists, code, links, images — as a plain Markdown file that's easy to read, easy to skim, and easy to feed into a downstream pipeline.

## When this skill applies

Trigger any time the user wants Markdown out of HTML. Common framings:

- "Convert this HTML file to Markdown."
- "Give me a simplified version of this page."
- "Strip the styling, just the content."
- "Make it readable / extract the article."
- "Turn the saved page into a `.md` I can read."
- "Get a clean text version for [LLM / note-taking / archive]."

If the user wants HTML-out (not Markdown), use the `medium-format` skill instead. If they want PDF, DOCX, or to go the other way (Markdown → HTML), this is the wrong skill.

## How to use the skill

The transformation is handled by `scripts/transform.py`. For normal use just invoke it:

```bash
python scripts/transform.py INPUT.html [-o OUTPUT.md] [options]
```

If `-o` is omitted, the result lands at `~/Documents/synergyAI/outputs/<input-stem>.md`.

### Common invocations

**Basic — convert a self-contained HTML file:**
```bash
python scripts/transform.py page.html
# → ~/Documents/synergyAI/outputs/page.md
```

**With a specific article container** (recommended for full pages with nav/footer):
```bash
python scripts/transform.py page.html \
  --main-selector "article" \
  --base-url https://example.com
```

**Text-only (drop images entirely):**
```bash
python scripts/transform.py page.html --no-images
```

**Print to stdout instead of writing a file** (for piping):
```bash
python scripts/transform.py page.html --stdout | less
```

### Flag reference

| Flag | Purpose |
|---|---|
| `-o`, `--output PATH` | Output `.md` file. Default: `~/Documents/synergyAI/outputs/<input-stem>.md`. |
| `--base-url URL` | Origin URL used to resolve relative paths in `<a href>` and `<img src>`. Strongly recommended whenever the source HTML has relative links — without it, links/images stay relative and won't work outside the original site. |
| `--main-selector CSS` | CSS selector for the article container (e.g. `article`, `.post-content`, `#main`). If omitted, the script auto-tries `<main>`, `<article>`, `[role='main']`, `#content`, `#main`, `.post-content`, `.entry-content`, `.article-content`, then falls back to `<body>`. Pass it explicitly when auto-detection picks up navigation or sidebars. |
| `--no-images` | Drop all `<img>` tags. Use when only the text matters (note-taking, LLM input). Default: images are kept. |
| `--heading-style {atx,atx_closed,setext}` | Markdown heading flavor. Default: `atx` (`# Heading`). |
| `--bullets STR` | Bullet character(s) for unordered lists. Default: `-`. Pass `*` or `+` if you prefer. |
| `--stdout` | Print to stdout instead of writing a file. |

### Working with the user's input

1. **Single HTML file**: pass it directly. Linked CSS is ignored — Markdown doesn't need styling info.
2. **HTML fragment** (no `<html>`/`<body>`): also fine — the script handles it.
3. **Saved web page** (has nav, sidebar, footer, ads): pass `--main-selector` if you can identify the article wrapper, otherwise let auto-detection try. Check the output for unwanted nav links — if they leaked in, narrow `--main-selector`.
4. **HTML pasted in chat**: save it to a temp file first, then run the script.

### After running the script

- Always show the user the **warnings** the script printed (relative image paths, missing main selector, etc.).
- Tell the user where the output landed (default location is `~/Documents/synergyAI/outputs/`).
- If the output looks bloated with nav/footer noise, suggest re-running with a tighter `--main-selector`.

## What the transformation does

The script applies a deterministic pipeline:

1. **Parses the HTML** with BeautifulSoup (lenient parser — handles broken markup).
2. **Strips HTML comments** so they don't leak into the Markdown as stray text.
3. **Picks an article root** — `--main-selector` if given, otherwise auto-detect (`<main>` → `<article>` → `[role='main']` → `#content` → `#main` → `.post-content` → `.entry-content` → `.article-content`), falling back to `<body>`.
4. **Drops noise tags entirely** — `<script>`, `<style>`, `<nav>`, `<footer>`, `<aside>`, `<form>`, `<input>`, `<button>`, `<svg>`, `<video>`, `<canvas>`, `<iframe>`, `<meta>`, `<link>`, etc. The whole subtree under each goes away.
5. **Optionally drops images** when `--no-images` is set.
6. **Normalizes specific tags**:
   - `<h5>`/`<h6>` → `<h4>` (Markdown supports h5/h6 but they read as fine print).
   - Empty `<a href="">` wrappers are unwrapped (text kept).
   - Relative `<a href>` and `<img src>` are resolved against `--base-url`.
   - `<pre>` without an inner `<code>` gets one wrapped around its contents, so markdownify renders it as a fenced code block instead of a quoted paragraph.
7. **Renders to Markdown** via the `markdownify` library:
   - ATX headings by default (`# Title`).
   - Fenced code blocks with language hints from `class="language-foo"` / `class="lang-foo"`.
   - GitHub-flavored pipe tables (preserved, unlike medium-format which drops them).
   - Standard list/link/emphasis mappings.
8. **Post-processes**: collapses 3+ blank lines to 2, trims trailing whitespace, ensures a single trailing newline.

## What survives, what doesn't

**Preserved:**
- Headings (with depth — h1–h4 kept literally, h5/h6 demoted to h4).
- Paragraphs, line breaks.
- Bold (`**`), italic (`*`), inline code (` `` `).
- Links with text and href.
- Images with alt text and src (unless `--no-images`).
- Ordered and unordered lists, including nesting.
- Code blocks (fenced, with language hint when present).
- Blockquotes.
- Horizontal rules.
- **GFM tables** — kept as pipe tables.

**Dropped:**
- All CSS, classes, ids, styles, data-attrs.
- All scripts, styles, forms, embeds, iframes, media.
- Navigation, sidebars, footers (when picked up by `--main-selector` or auto-detection).
- HTML comments.

**Lossy by design:**
- Custom fonts, colors, alignment, spacing — the whole point is to remove decoration.
- Inline `style="font-weight: bold"` is **not** detected. If the source uses inline CSS for emphasis instead of `<strong>`/`<em>`, that emphasis is lost. (Real-world HTML almost always uses the tags. If you hit a source that doesn't, fall back to `medium-format` or hand-edit.)

## Reference

`references/markdown-output-spec.md` documents what each HTML element becomes in the output and the rationale for the dropped/preserved choices.

`examples/` contains sample HTML inputs you can use as smoke tests before delivering output to the user.

## Limitations to surface to the user

- **No CSS-based emphasis detection.** If the source uses `<span style="font-weight:bold">` instead of `<strong>`, the bold is lost. (See above.)
- **Auto-detected article root may guess wrong** for unusual page structures. If you see nav/footer in the output, re-run with `--main-selector`.
- **Relative URLs without `--base-url`** stay relative — they'll only resolve when viewed in the same origin. The script warns when this happens.
- **Image-heavy documents** still emit Markdown image syntax — but Markdown viewers fetch images at render time, so unreachable URLs (auth-walled, deleted) just won't display.
