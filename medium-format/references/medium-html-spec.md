# Medium HTML Format Reference

This is the practical specification of what Medium accepts when importing or pasting HTML, distilled from Medium's import tool, the (now deprecated but still informative) `POST /v1/posts` API, and the rendered HTML Medium produces.

## Two paths into Medium

There are two ways HTML reaches Medium, and they have different requirements.

### 1. URL import — `https://medium.com/p/import`

The user pastes a URL. Medium fetches the page through a third-party extraction service and pulls out the article content. For this to work the page must:

- Be publicly reachable (no auth, no JS-only rendering).
- Include Open Graph metadata. At minimum:
  - `<meta property="og:type" content="article">`
  - `<meta property="og:title" content="...">`
  - `<meta property="og:description" content="...">`
- Have the article content in standard semantic HTML (`<article>`, headings, paragraphs).

Optional but commonly used:
- `<meta property="article:published_time" content="2024-05-01T10:00:00Z">` — Medium uses this to **backdate** the imported post, which is otherwise impossible.
- `<link rel="canonical" href="...">` — sets the canonical URL on the imported story so SEO is not penalized for duplicate content.
- `<meta property="article:author" content="...">`.

The `prefix="og: http://ogp.me/ns#"` attribute on `<html>` is recommended; some import failures have been traced to its absence.

### 2. Paste / API — `<contentFormat: "html">`

The user pastes content into Medium's editor, or POSTs to the legacy `/v1/posts` endpoint with `contentFormat: "html"`. The editor (and the API) interpret a restricted subset of HTML directly. No metadata is required — just the body content.

The `body` mode of `transform.py` produces output suitable for this path.

## Supported tags

| Tag | Renders as | Notes |
|---|---|---|
| `<h1>` | Article title | The first `<h1>` is treated as the title. Optional in body mode. |
| `<h2>` | Large subtitle | Medium's "T1" heading. |
| `<h3>` | Mid-size heading | Common section heading. |
| `<h4>` | Small heading / kicker | `<h5>`/`<h6>` do not exist — demote to `<h4>`. |
| `<p>` | Paragraph | The default block. |
| `<blockquote>` | Quote | `class="big"` for pull-quote style; `class="small"` for inline-style quote. |
| `<pre><code>` | Code block | Optional `class="language-foo"` on the inner `<code>` for syntax highlighting. |
| `<code>` (inline) | Inline code | Outside a `<pre>`, renders as inline monospace. |
| `<ul>`, `<ol>`, `<li>` | Lists | Nesting is supported but Medium's renderer flattens deep nesting. |
| `<a href="...">` | Link | Use absolute URLs. |
| `<img src="..." alt="...">` | Image | Medium downloads the image at import time and rehosts it — relative URLs will fail. |
| `<figure>` + `<figcaption>` | Captioned image | The recommended structure for images with captions. |
| `<strong>`, `<b>` | Bold | Both work. |
| `<em>`, `<i>` | Italic | Both work. |
| `<u>` | Underline | Renders, but uncommon on Medium. |
| `<hr>` | Section break | Renders as Medium's three-dot divider. |
| `<br>` | Line break | Use sparingly; Medium prefers paragraph breaks. |
| `<iframe src="...">` | Embed | Only renders if the URL is supported by Embedly (YouTube, Twitter/X, GitHub Gist, CodePen, JSFiddle, SoundCloud, etc.). Otherwise stripped. |

## What gets stripped or ignored

Medium's renderer applies its own design system, so essentially **all CSS is discarded**:

- `style="..."` attributes
- `class` attributes (except `class="big"` / `class="small"` on `<blockquote>` and `class="language-*"` on `<code>`)
- `id` attributes
- `<style>` blocks and `<link rel="stylesheet">`
- Custom fonts, colors, sizes, alignments
- `<div>`, `<span>`, `<section>`, `<article>` (children kept, wrapper unwrapped)
- `<header>`, `<footer>`, `<nav>`, `<aside>` (typically dropped wholesale during URL import)
- `<script>`, `<noscript>`, `<form>`, `<input>`, `<button>`, `<svg>`, `<canvas>`, `<video>`, `<audio>`
- `<table>` — **not supported**. Medium has its own table tool but does not import HTML tables. The transform script falls back to plain-text rows inside a code block so the data survives, and warns the user to rebuild the table manually.

## Embeds

Medium routes iframes through Embedly, which supports 700+ providers. The most common:

- YouTube (`https://www.youtube.com/watch?v=...`)
- Twitter / X (`https://twitter.com/.../status/...`)
- GitHub Gist (`https://gist.github.com/...`)
- CodePen (`https://codepen.io/.../pen/...`)
- JSFiddle, SoundCloud, Vimeo, Spotify, etc.

For paste mode, the simplest approach is to put the URL on a line by itself — the editor auto-converts it to an embed. For HTML mode, an `<iframe src="...">` works if the URL is on the supported list.

## Image handling

- Medium **downloads images by URL** at import/publish time and rehosts them on `cdn-images-1.medium.com`. After that, the original URL is no longer used.
- This means image URLs must be **absolute and publicly reachable** at the moment of import. Relative paths (`./img.png`) and `file://` URLs will silently fail.
- The transform script accepts a `--base-url` flag to resolve relative URLs against an absolute origin.

## Title handling

When importing via URL, Medium derives the title from (in order of preference):
1. `<meta property="og:title">`
2. `<title>`
3. The first `<h1>` in the body

When pasting/API: the first `<h1>` becomes the title. If you want a body that starts without a heading, omit the `<h1>` and either pass the title via the API's `title` field or set it manually after pasting.

## Summary: the conversion contract

The transform script's job is to take any well-formed HTML+CSS document and produce something Medium will render correctly. The contract:

1. **Style cues are converted to semantic tags** before CSS is dropped — `font-weight: bold` becomes `<strong>`, `font-style: italic` becomes `<em>`, monospace fonts become `<code>`.
2. **Container tags are unwrapped** so structural noise disappears but content survives.
3. **Disallowed tags are dropped wholesale** (scripts, forms, media elements).
4. **Unknown tags are unwrapped** rather than dropped — better to lose styling than lose content.
5. **Attributes are stripped down to a per-tag whitelist** so nothing Medium ignores remains in the output.
6. **For URL import**, the result is wrapped in a minimal HTML page with the Open Graph metadata Medium requires.
