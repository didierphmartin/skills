---
name: image-audit
description: "Audit images on a web page for SEO, performance, and accessibility. Use for: 'image SEO', 'audit images', 'are my images optimized', 'check alt text', 'image format check' (WebP/AVIF/JPEG), 'are images lazy-loaded', 'CLS images', 'fetchpriority audit', 'responsive image check', 'image file size audit', 'verifier les images'. Checks: alt-text coverage and quality, format (recommends WebP/AVIF over JPEG/PNG), <picture> + responsive srcset/sizes, width/height attributes for CLS prevention, 5-stack lazy-loader detection (native/perfmatters/ewww/js-generic/none — avoids false 'not lazy-loaded' flags on WordPress sites with optimizers that strip the native attribute), fetchpriority=high on LCP candidate, decoding=async on non-LCP, filename quality (flags IMG_1234/DSC_0001/hex strings), CDN usage. Optional file-size HEAD probe with tiered thresholds (thumbnail <50KB, content <100KB, hero <200KB). Skippable via --skip-sizes for speed. Output: scored Markdown report with per-image issue table and prioritized fixes."
dependencies: [beautifulsoup4]
# fetches_urls: false because the size-probe pass HEAD-fetches every image URL
# concurrently via the proxy. The bridge's single-URL prefetch is wasted; the
# script does its own fetches.
fetches_urls: false
---

# image-audit

Image SEO and performance audit. The image-side counterpart to `ai-search-audit` — that one checks the page's overall structure including a basic alt-coverage stat; this one drills into every `<img>` element with format, lazy-loading, CLS, and file-size detail.

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks anything image-SEO related and gives a URL, your first action is `run_skill_script` with `dir_name: image-audit` and `scripts/audit.py`. Localhost URLs are fully supported. Don't refuse with access concerns.
>
> ## ⛔ NO FABRICATION
>
> Every finding ("3 images missing alt text", "2 hero images over 700KB") must come from the actual report file. Never produce per-image findings from inference. If the script errors, surface the error and stop.

## Invocation — exact argv shapes

The URL is **positional**, not a flag.

```json
// Full audit (slowest — includes file-size HEAD probes)
{ "dir_name": "image-audit", "script": "scripts/audit.py", "argv": ["https://example.com"] }

// Skip the file-size HEAD probes (faster — checks only what's in the HTML)
{ "argv": ["https://example.com", "--skip-sizes"] }

// Tune concurrency of size probes (default 8)
{ "argv": ["https://example.com", "--concurrency", "16"] }
```

**Do NOT pass the URL with a flag name.** No `--input`, `--site`, `--page`. URL goes first in `argv`. (The `--url` alias exists as a backstop.)

CLI for reference:

```bash
# Full audit (default)
python scripts/audit.py https://example.com

# Skip file-size HEAD probes
python scripts/audit.py https://example.com --skip-sizes

# Custom output
python scripts/audit.py https://example.com -o my-image-report.md
```

## What it checks

### 1. Alt text
- **Presence** — flag every `<img>` without an `alt` attribute (decorative images should explicitly use `alt=""`)
- **Length** — flag empty alt on non-decorative images; flag alt text >125 chars as too verbose
- **Quality** — flag `alt="image.jpg"`, `alt="picture"`, `alt="DSC_0001"`, or alt equal to the filename
- **Keyword stuffing** — flag alt text where the same word repeats 3+ times

### 2. Format
- Detect format from URL extension and MIME hint
- Recommend WebP (~97% browser support) or AVIF (~92%) over JPEG/PNG
- Check for `<picture>` element with format fallback chain (`<source type="image/avif">` → `<source type="image/webp">` → `<img src="...jpg">`)
- Note: JPEG XL is feature-complete in Chrome (Nov 2025) but not yet stable; not yet recommended

### 3. Responsive images
- Check `srcset` presence with multiple widths
- Check `sizes` attribute matches the layout
- Flag images with absolute pixel dimensions that exceed common breakpoints

### 4. Lazy loading (5-stack detection)

The 5-stack detector handles real-world WordPress optimizer stacks that strip the native `loading="lazy"` and substitute their own attributes — naive detection would otherwise produce false "not lazy-loaded" findings on sites that are heavily lazy-loaded.

| Detected value | Signal |
|---|---|
| `native` | `loading="lazy"` HTML attribute |
| `perfmatters` | `data-perfmatters-src` / `-srcset` OR class `perfmatters-lazy` |
| `ewww` | `data-ewww-src` / `data-eio` OR class `lazyload-eio` |
| `js-generic` | `data-src` / `data-lazy-src` / `data-original` / `data-srcset` OR class `lazyload`/`lazyloaded`/`lazy` |
| `none` | None of the above — image is not lazy-loaded |

Lifted from claude-seo's `scripts/parse_html.py:_detect_lazy_method` verbatim.

### 5. LCP + CLS hints
- **`fetchpriority="high"`** — recommended on the LCP candidate (first content image, or the largest above-the-fold). Flag when absent on the candidate.
- **`decoding="async"`** — recommended on non-LCP images
- **`width` / `height` attributes** — flag missing dimensions (causes CLS unless `aspect-ratio` CSS is set)
- **Lazy-loading on the LCP image** is critical: it directly harms LCP scores

### 6. Filename SEO
- Flag generic-camera filenames: `IMG_\d{4,}`, `DSC_\d{4,}`, `IMG-\d+`, hex strings (32-char MD5-like), pure timestamps
- Recommend descriptive hyphenated names: `blue-running-shoes.webp` not `IMG_1234.jpg`

### 7. CDN detection
- Compare image hostname to the page hostname
- Common CDN patterns: `*.cloudfront.net`, `*.cloudflare.com`, `*.imgix.net`, `*.cloudinary.com`, `*.fastly.net`, `*.akamaized.net`, `*.imagekit.io`, `*.bunnycdn.com`
- Note when site uses or doesn't use a CDN for image delivery

### 8. File size (optional via `--skip-sizes`)

Tiered thresholds by image category. Category inferred from dimensions or position (hero vs content vs thumbnail).

| Category | Target | Warning | Critical |
|---|---|---|---|
| Thumbnail | <50KB | >100KB | >200KB |
| Content | <100KB | >200KB | >500KB |
| Hero / banner | <200KB | >300KB | >700KB |

Requires one HEAD request per image. Concurrent, default 8 at a time. Pass `--skip-sizes` for fast iteration when you only need HTML-side findings.

## What it does NOT do

- **Format conversion** (cwebp/exiftool/ffmpeg pipelines) — Pyodide can't shell out. If you want to actually convert images to WebP/AVIF on the server side, that's a separate PHP backend endpoint.
- **Visual content quality** (blurry, distorted, miscropped images) — requires rendering and ML inference.
- **EXIF/IPTC/XMP audit** — covered separately by a metadata skill if you need it.

## Pyodide notes

Routes all fetches (page HTML + per-image HEAD probes) through `/gpt/backend/api/v1/fetch-url`. Default output is `/outputs/` in Pyodide (host-mounted folder) and `~/Documents/synergyAI/outputs/` in CPython.

## Limitations to surface to the user

- **Format detection from URL only.** A `.jpg` URL serving WebP via Content-Type would be undercounted. The MIME hint from HEAD response (when `--skip-sizes` is off) corrects this.
- **LCP candidate is heuristic.** Without rendering, we can't tell "above the fold." First content image with the largest declared dimensions is the best static-HTML proxy.
- **JavaScript-rendered images missed.** The script reads raw HTML. Same caveat as `ai-search-audit` and `sitemap-audit`.
- **CDN list is non-exhaustive.** Private CDNs (`assets.<yourdomain>.com`) are correctly flagged as "external host" but won't be labeled as a known CDN.
