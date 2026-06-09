# image-audit

> Image SEO and performance audit for a web page.

## What this skill does

Extracts every `<img>` on a page and audits across 8 dimensions:

1. **Alt text** — presence, length, quality heuristics, keyword stuffing detection, filename-as-alt detection
2. **Format** — JPEG/PNG/GIF flagged when not paired with `<picture>` + AVIF/WebP fallback chain
3. **Responsive** — `srcset` and `sizes` attribute presence
4. **Lazy loading** — 5-stack detector (native, perfmatters, ewww, js-generic, none) — lifted verbatim from claude-seo's `parse_html.py`
5. **LCP/CLS** — LCP candidate detection (first image ≥800px wide, else largest declared width); enforces no lazy-loading + `fetchpriority="high"` on LCP; `decoding="async"` on non-LCP; width/height attributes for CLS prevention
6. **Filename quality** — flags generic camera names (`IMG_NNNN`, `DSC_NNNN`), hex strings (UUID/hash), pure-numeric timestamps, generic placeholders (`image`/`photo`)
7. **CDN usage** — known CDN hostname detection (CloudFront, Cloudflare, Imgix, Cloudinary, Fastly, Akamai, ImageKit, BunnyCDN, etc.)
8. **File size** *(optional)* — tiered HEAD-probe with thresholds by image category

## Output

`~/Documents/synergyAI/outputs/IMAGE-AUDIT.md` (or `/outputs/IMAGE-AUDIT.md` in Pyodide), containing:
- **Score** (0-100)
- **Summary** table — total images, alt coverage, dimension coverage, lazy %, modern format count, size probed count
- **Strengths** list
- **Findings** grouped by category, with severity, what, why, fix
- **Per-image detail** table — every image with format, dims, lazy method, size, alt status, LCP indicator (★)

## CLI reference

```bash
python scripts/audit.py [INPUT] [OPTIONS]
```

| Flag | Default | Purpose |
|---|---|---|
| `INPUT` (positional) | — | URL to audit. Required unless `--url`. |
| `--url URL` | — | Alias for the positional argument |
| `--skip-sizes` | off | Skip per-image HEAD probes (HTML-only audit — much faster) |
| `--concurrency N` | 8 | Concurrent HEAD-probe count |
| `-o`, `--output PATH` | `~/Documents/synergyAI/outputs/IMAGE-AUDIT.md` | Output file/dir |
| `--stdout` | off | Print to stdout |

## File-size tiers

Image category is inferred from declared width:

| Category | Width band | Target | Warning | Critical |
|---|---|---|---|---|
| Thumbnail | <200px | <50KB | >100KB | >200KB |
| Content | 200-799px | <100KB | >200KB | >500KB |
| Hero / banner | ≥800px | <200KB | >300KB | >700KB |

Critical-tier images become recommended findings; warning-tier become nice-to-have.

## 5-stack lazy-load detection

Order: specific WordPress stacks first (so reports attribute correctly), then generic data-* patterns, then native HTML attribute.

| `lazy_method` | Detection signal |
|---|---|
| `native` | `loading="lazy"` HTML attribute |
| `perfmatters` | `data-perfmatters-src` / `data-perfmatters-srcset` OR class `perfmatters-lazy` |
| `ewww` | `data-ewww-src` / `data-eio` OR class `lazyload-eio` |
| `js-generic` | `data-src` / `data-lazy-src` / `data-original` / `data-srcset` OR class `lazyload`/`lazyloaded`/`lazy` |
| `none` | None of the above — image is not lazy-loaded |

This 5-stack approach avoids false "not lazy-loaded" findings on WordPress sites running optimizers that strip the native attribute and substitute their own data-* attributes.

## LCP detection heuristic

1. Walk all images in document order
2. Mark the **first** image with declared width ≥ 800px as the LCP candidate
3. If no image meets that threshold, mark the overall largest-width image
4. The candidate gets a ★ in the per-image table

Without rendering, this is the best static-HTML proxy for "above the fold." Findings against the LCP candidate:
- **Critical**: LCP is lazy-loaded (any of the 5 methods)
- **Recommended**: LCP missing `fetchpriority="high"`

## Known CDN hostnames

| Pattern | Label |
|---|---|
| `cloudfront.net` | AWS CloudFront |
| `cloudflare.com` | Cloudflare |
| `imgix.net` | Imgix |
| `cloudinary.com` | Cloudinary |
| `fastly.net` | Fastly |
| `akamaized.net` / `akamaihd.net` | Akamai |
| `imagekit.io` | ImageKit |
| `bunnycdn.com` / `b-cdn.net` | BunnyCDN |
| `kxcdn.com` | KeyCDN |
| `jsdelivr.net` | jsDelivr |
| `googleusercontent.com` | Google CDN |
| `wp.com` / `wordpress.com` | WordPress.com CDN (Jetpack/Photon) |
| `shopify.com` | Shopify CDN |
| `squarespace-cdn.com` | Squarespace CDN |
| `vercel.app` | Vercel CDN |
| `netlify.app` | Netlify CDN |

Other external hosts are correctly flagged as "external" but not labeled with a vendor name.

## Bad-filename patterns

Regex matches against the filename stem (without extension):

| Pattern | Reason |
|---|---|
| `^IMG[-_]?\d{3,}` | generic camera filename |
| `^DSC[-_]?\d{3,}` | generic camera filename |
| `^DSCN?\d{3,}` | generic Nikon filename |
| `^P\d{7,}` | Olympus-style camera filename |
| `^[0-9a-f]{16,}$` | hex string (UUID/hash) |
| `^\d{10,}$` | pure-numeric (likely timestamp) |
| `^(image\|picture\|photo\|untitled)\d*$` | generic placeholder name |

## Pyodide notes

- `fetches_urls: false` — page HTML fetch + per-image HEAD probes all happen via the proxy
- Output detects Pyodide and routes to `/outputs/`
- HEAD probes parallelized via `asyncio.gather` with semaphore-bounded concurrency

## What it does NOT do

- **Format conversion** (cwebp/exiftool/ffmpeg) — Pyodide can't shell out. Would require a PHP backend endpoint.
- **Visual content quality** (blurry, distorted, miscropped) — requires rendering + ML.
- **EXIF/IPTC/XMP audit** — separate skill if needed.

## Limitations

- **Format detection from URL extension only by default.** A `.jpg` URL serving WebP is undercounted. When size probes run, the Content-Type header corrects this.
- **LCP candidate is heuristic.** Without rendering, "above the fold" is a guess.
- **JavaScript-rendered images missed.** Same caveat as `ai-search-audit` and `sitemap-audit`.
- **CDN list non-exhaustive.** Private CDNs (`assets.<yourdomain>.com`) flagged as "external" but unlabeled.

## Related skills

- **`ai-search-audit`** — covers alt-coverage in the asset-hygiene pass; this skill drills deeper
- **`sitemap-audit`** — site-architecture counterpart
