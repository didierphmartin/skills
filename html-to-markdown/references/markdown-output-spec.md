# Markdown output spec

This document describes what each HTML element becomes in the Markdown output, and the reasoning behind the choices.

## Goal

Strip HTML's visual decoration (CSS, classes, ids, styles, layout containers, navigation, ads, scripts) and emit clean Markdown that surfaces the **cognitive structure** of the document. The output should be:

- **Readable as text** — no decorative syntax cluttering the page.
- **Useful as LLM input** — token-efficient, no styling noise.
- **Round-trippable in spirit** — re-rendering the Markdown gives a document with the same semantic structure (not pixel layout).

## Tag mapping

| HTML | Markdown | Notes |
|---|---|---|
| `<h1>` … `<h4>` | `#` … `####` | ATX headings by default. Configurable via `--heading-style`. |
| `<h5>`, `<h6>` | `####` | Demoted — most renderers blur h5/h6, and a 5-level cognitive hierarchy is rare in practice. |
| `<p>` | paragraph | Surrounding blank lines added by markdownify. |
| `<br>` | `  ` (line break) | Two trailing spaces + newline. |
| `<strong>`, `<b>` | `**text**` | |
| `<em>`, `<i>` | `*text*` | |
| `<code>` (inline) | `` `code` `` | |
| `<pre><code>` | fenced block | `` ``` `` with optional `language-foo` / `lang-foo` hint pulled from the `<code>` class. |
| `<pre>` (no inner `<code>`) | fenced block | The script wraps the contents in a `<code>` first so markdownify produces a fence, not a quote. |
| `<a href="X">text</a>` | `[text](X)` | Relative `href` resolved against `--base-url` when given. Empty `href` causes the wrapper to be unwrapped (text kept). |
| `<img src="X" alt="Y">` | `![Y](X)` | Relative `src` resolved against `--base-url`. Dropped entirely with `--no-images`. |
| `<ul>`/`<ol>`/`<li>` | `-` / `1.` lists | Nested lists supported. Bullet character configurable via `--bullets`. |
| `<blockquote>` | `> text` | |
| `<hr>` | `---` | |
| `<table>` | GFM pipe table | Kept (unlike `medium-format`, which drops tables). |
| `<sup>`, `<sub>` | preserved as HTML | Markdown has no native syntax; markdownify keeps the inline tags. |
| `<del>`, `<s>` | `~~text~~` | GFM strikethrough. |

## Tags dropped entirely

These remove the element **and its contents**:

`script`, `style`, `noscript`, `template`, `nav`, `footer`, `aside`, `form`, `input`, `button`, `select`, `textarea`, `label`, `fieldset`, `legend`, `video`, `audio`, `source`, `track`, `canvas`, `svg`, `math`, `object`, `embed`, `param`, `applet`, `meta`, `link`, `base`, `iframe`.

**Rationale:** all of these are either non-content noise (script/style), navigation chrome (nav/footer/aside), interactive elements that don't translate to text (form/input/button), media that can't be expressed in Markdown text (video/canvas/svg), or document metadata that belongs in the header (meta/link).

`iframe` is dropped despite being content-bearing because it's almost always used for embeds (YouTube, Twitter widgets, ads) that don't render as Markdown. If you need iframes preserved as raw HTML inside the Markdown, edit the `DROP_TAGS` set in `transform.py`.

## What's *not* detected

The script does **not** parse CSS. Specifically:

- `style="font-weight: bold"` does **not** become `**text**`.
- `class="bold"` with a CSS rule making it bold does not become `**text**`.
- Custom callout/admonition styles (e.g. `<div class="warning">`) lose their semantic — they unwrap to plain prose.

This is a deliberate scope choice. Real-world HTML overwhelmingly uses `<strong>`/`<em>`/`<code>` tags directly; the rare cases that don't are easier to fix at the source than to detect heuristically. If you need CSS-aware detection, see the `medium-format` skill, which does that work for HTML→HTML.

## Article-root detection

When `--main-selector` is not given, the script tries these selectors in order and uses the first match with non-empty text:

1. `<main>`
2. `<article>`
3. `[role='main']`
4. `#content`
5. `#main`
6. `.post-content`
7. `.entry-content`
8. `.article-content`

If none match, it falls back to `<body>`. If there's no `<body>` either (a fragment), the entire document is wrapped in a synthetic container.

**Why these selectors:** they cover the vast majority of CMS conventions (WordPress uses `.entry-content` and `.post-content`, Ghost uses `.post-content`, MDN uses `<main>`, most modern hand-rolled sites use `<article>` or `<main>`). Anything outside this list almost always benefits from an explicit `--main-selector`.

## Post-processing

After markdownify renders the cleaned subtree:

1. **Runs of 3+ blank lines** are collapsed to 2 (preserves paragraph breaks, removes excessive whitespace).
2. **Trailing whitespace** on each line is stripped.
3. **Leading and trailing whitespace** on the whole document is stripped, then a single trailing newline is added.

This produces output that's diff-friendly and consistent regardless of how the source HTML was indented.
