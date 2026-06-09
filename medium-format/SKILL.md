---
name: medium-format
description: "Use this skill whenever the user wants to convert, transform, prepare, or adapt an HTML and/or CSS file for Medium.com — including phrases like 'format for Medium', 'import to Medium', 'paste into Medium', 'Medium-ready HTML', 'publish on Medium', 'convert my blog post to Medium', or 'make this work on Medium'. Triggers on any request that involves taking existing web content (a self-contained HTML file, an HTML file with linked CSS, or an HTML fragment) and producing output suitable either for Medium's URL-based 'Import a story' tool or for direct paste into the Medium editor / the legacy `/v1/posts` API. Also triggers when the user asks to backdate a Medium import, to clean up HTML for Medium, or to strip CSS that Medium will ignore. Do NOT use for: WordPress imports, Substack, Ghost, Notion, or any non-Medium target."
dependencies: [beautifulsoup4, cssutils]
---

# medium-format

Convert HTML+CSS into a format Medium.com will accept, either as a paste-able body or as a full HTML page ready for Medium's URL import tool.

## When this skill applies

Medium accepts a restricted subset of HTML and ignores virtually all CSS. So whenever the user wants to take a styled HTML article — their own blog post, an exported page, a self-contained HTML+CSS file — and get it onto Medium, the raw source almost always needs cleaning. This skill does that cleaning deterministically via a Python script.

The two paths to Medium and how this skill maps to them:

| User wants to … | Output mode |
|---|---|
| Paste cleaned HTML directly into the Medium editor, or POST it via `/v1/posts` with `contentFormat: "html"` | `--mode body` |
| Host the page somewhere public and use Medium's "Import a story" URL tool (`https://medium.com/p/import`) — necessary if they want to **backdate** the post or set a canonical URL automatically | `--mode page` |

When in doubt, ask. If the user mentions backdating, canonical URLs, or "import tool", they want `page` mode. If they mention "paste" or "API", they want `body` mode.

## How to use the skill

The transformation is handled by `scripts/transform.py`. Read it once if you need to understand or extend behavior, but for normal use just invoke it:

```bash
python scripts/transform.py INPUT.html -o OUTPUT.html [options]
```

### Common invocations

**Body mode — clean HTML to paste into the editor:**
```bash
python scripts/transform.py post.html -o post.medium.html \
  --mode body \
  --base-url https://myblog.com \
  --main-selector "article"
```

**Page mode — full page with Open Graph metadata for the URL import tool:**
```bash
python scripts/transform.py post.html -o post.medium.html \
  --mode page \
  --title "The State of Async Python in 2025" \
  --description "A practical look at async Python in 2025." \
  --canonical "https://myblog.com/async-python-2025" \
  --published "2025-05-01T10:00:00Z" \
  --author "Jane Doe" \
  --base-url https://myblog.com \
  --main-selector "article"
```

### Flag reference

| Flag | Purpose |
|---|---|
| `--mode {body,page}` | `body` = inner HTML only; `page` = full HTML page with OG metadata. Default: `body`. |
| `--title` | Article title (page mode). Falls back to first `<h1>` then `<title>`. |
| `--description` | Sets `og:description` and `<meta name="description">`. Page mode only. |
| `--canonical` | Sets `<link rel="canonical">` and `og:url`. Page mode only. |
| `--published` | ISO 8601 timestamp for `article:published_time`. **Use this to backdate the imported post** — Medium's import tool reads this metadata and the post will appear with that date. |
| `--author` | Sets `meta name="author"` and `article:author`. |
| `--base-url` | Origin URL used to resolve relative paths in `<a href>` and `<img src>`. Strongly recommended whenever the source HTML has relative links — Medium downloads images by URL at import time, so relative paths will fail silently. |
| `--main-selector` | CSS selector for the article container (e.g. `article`, `.post-content`, `#main`). If omitted, the entire `<body>` is processed. When set, the script also pulls in any `<header>` or `<h1>` that's a sibling/ancestor of the selected element — many sites put the article title in a `<header>` outside the article body, and we don't want to silently strip it. |

### Working with the user's input

1. **If they've uploaded a single HTML file**: pass it directly. If it has a `<link rel="stylesheet" href="...">`, the script reads the linked CSS from disk relative to the HTML file. Make sure the CSS file is alongside it.
2. **If they've uploaded an HTML fragment** (no `<html>`/`<body>`): also fine — the script handles it.
3. **If they've uploaded multiple files**: ask which file is the article. Place the linked CSS files in the same directory as the HTML file before running.
4. **If they paste HTML into the chat**: save it to a temp file first, then run the script on it.

### After running the script

- Always show the user the **warnings** the script printed (relative image paths, dropped tables, etc.). These are real things they need to fix.
- For `body` mode: present the output file with `present_files`, and tell the user to either copy-paste it into Medium's editor or use it as the `content` field in the `/v1/posts` API.
- For `page` mode: present the output file and tell the user the next steps:
  1. Host the file at a public URL (GitHub Pages, Netlify, S3, even a Gist served raw won't work — Medium needs a real HTML response).
  2. Go to `https://medium.com/p/import` and paste the URL.
  3. Medium will fetch, extract, and create a draft. They can edit/publish from there.

## What the transformation does

The script makes Medium-incompatible HTML compatible by reading the rules in `references/medium-html-spec.md`. In short:

1. **Resolves CSS** — linked stylesheets, `<style>` blocks, and inline `style="..."` attributes are merged into per-element effective styles.
2. **Strips HTML comments** so they don't leak into the output as stray text.
3. **Drops disallowed elements** — `<script>`, `<style>`, `<form>`, `<svg>`, `<video>`, `<canvas>`, `<nav>`, `<footer>`, `<aside>`, etc., go away entirely.
4. **Detects semantic patterns** that hide inside styled `<div>`s — pull quotes, dividers, bylines, tag-chip lists, labeled-section captions. See "How the semantic detection works" below.
5. **Converts style cues to semantic tags** before discarding CSS — `font-weight: bold` → `<strong>`, `font-style: italic` → `<em>`, monospace `font-family` → `<code>`. Restricted to genuinely inline elements inside running prose so it doesn't false-fire on bylines or captions that just happen to use a monospace font.
6. **Normalizes specific tags** — `<h5>`/`<h6>` demoted to `<h4>`, `<pre>` wrapped in `<code>` if needed, relative URLs resolved, `<table>` converted to a plain-text fallback (Medium does not support HTML tables).
7. **Unwraps containers** — `<div>`, `<span>`, `<section>`, `<article>`, `<main>`, `<header>` lose their wrapping but keep their children.
8. **Strips attributes** to a per-tag whitelist (mostly `href`/`src`/`alt`).
9. **Flattens code blocks** — strips `<em>`/`<strong>` from inside `<pre><code>` so syntax-highlighted comments don't render with italics inside code.
10. **Final cleanup** — empty paragraphs removed, stray text wrapped in `<p>`.
11. **Page mode only** — wraps the result in a minimal HTML5 page with the Open Graph metadata Medium's import tool requires.

## How the semantic detection works (and why it generalizes)

Medium's import is a one-way trip — once HTML is pasted, the user is editing what's there. So when the source has a meaningful piece of structure encoded as a styled `<div>` (a pull quote, a divider, a byline, a tag list, a section label), the script tries to recover the semantic before unwrapping. Otherwise the user ends up with prose that looks correct but reads flat.

Detection runs in two layers, additive:

**Layer 1: class-name matching.** The script recognizes common conventions used across blog platforms. WordPress (`wp-block-pullquote`, `wp-block-separator`, `entry-meta`, `tag-cloud-link`), Ghost (`kg-quote`, `kg-callout-card`, `gh-article-tags`), Substack, Hugo, and most hand-rolled blogs use names that fall into a handful of patterns. The script substring-matches against keyword sets — `pullquote`/`pull-quote`/`blockquote`/`quote-card`/`epigraph` for pull quotes, `sep`/`separator`/`divider` for dividers, and so on. Substring matching means `wp-block-pullquote` matches `pullquote`, `entry-meta` matches `meta`, `gh-article-tags` matches `tags`.

**Layer 2: structural fallbacks.** When class names don't help (custom blogs, exotic conventions, no class names at all), the script falls back to structural signals that don't depend on naming:
- Any `<div>`/`<aside>`/`<figure>`/`<section>` containing a `<cite>` is treated as a pull quote — the `<cite>` is a strong attribution signal regardless of what the wrapper is named.
- Any short element whose entire stripped text is just repeating divider punctuation (`· · ·`, `* * *`, `———`) becomes `<hr>`.
- Any container whose all children are short same-tag siblings (≥ 3 spans/anchors/li) located near the end of the document is treated as a tag-chip list.
- HTML5 `<address>` is recognized as a byline element directly.
- A `<div>` whose CSS shows `text-transform: uppercase` plus letter-spacing or small font-size is treated as a label, even with no helpful class name.

When neither layer fires, the element falls through to the standard unwrap pass, which is always safe — no content is lost, the structure just becomes generic prose. So the worst case for a document with unusual conventions is "looks like clean prose without the polish," not "broken output."

The script has been tested on hand-rolled designer HTML, simulated WordPress exports (`wp-block-*`), simulated Ghost exports (`kg-*` plus HTML5 `<address>`), and minimal vanilla HTML with no class attributes at all. All produce reasonable Medium-ready output.

## Reference

`references/medium-html-spec.md` documents Medium's accepted HTML format in detail — the supported tag set, what each tag renders as, what gets stripped, embed handling, image handling, and the requirements for the URL import path. Read it if the user asks why something was transformed a particular way, or if they want to manually adjust the output.

`examples/sample_input.html` (with its companion `style.css`) is a worked example covering most edge cases — useful as a smoke test before delivering output to the user.

## Limitations to surface to the user

The script is deterministic and fast, but Medium's renderer has a few hard limits worth flagging up front:

- **Tables don't render.** The script preserves cell content as text in a code block, but the user must rebuild the table using Medium's native table tool.
- **Images must be absolute URLs.** Medium downloads images at import/publish time. If the source had relative paths and no `--base-url` was given, the images won't appear on Medium.
- **Embeds only work via Embedly.** YouTube, Twitter/X, GitHub Gist, CodePen, etc. work; arbitrary iframes won't.
- **CSS is gone.** Custom fonts, colors, alignments, decorative styling — all replaced by Medium's design system. The script preserves *meaning* (bold, italic, code), not *appearance*.
- **Backdating only works through the URL import tool**, not through paste or the API. If the user wants to preserve an original publication date, they need `--mode page` plus hosting.
