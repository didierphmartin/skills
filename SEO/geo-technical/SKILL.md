---
name: geo-technical
description: "Technical SEO audit with GEO-specific checks — SSR detection, canonical/hreflang validation, viewport, URL structure, and AI-crawler/IndexNow visibility. Applies an 8-category rubric (Crawlability, Indexability, Security, URL Structure, Mobile, Core Web Vitals, SSR, Page Speed) for a 0-100 score, marking categories that need external tools as requires-external-verification."
dependencies: [beautifulsoup4]
fetches_urls: false
---

# geo-technical

Audit a page's technical SEO with GEO-specific emphasis. The single biggest GEO
risk is **server-side rendering**: AI crawlers (GPTBot, ClaudeBot,
PerplexityBot, …) do NOT execute JavaScript, so a client-rendered SPA is
invisible to them no matter how good its content.

## When to use this skill

Trigger phrases (LLM routing): *"technical SEO audit"*, *"is my page SSR or
JS-rendered"*, *"check canonical/hreflang"*, *"mobile viewport check"*,
*"IndexNow"*, *"noindex check"*, *"AI crawlers and SEO"*, *"why isn't my site
indexed"*, *"page structure audit"*, *"technical readiness for AI search"*.

## Pipeline (what happens end-to-end)

1. **Fetch the page** (script-side — never imagine HTML).
2. **SSR signals from raw HTML**: main-content words, H1/H2 count, paragraphs,
   JSON-LD presence, internal-link count.
3. **Meta extraction**: title, description, canonical (with self-ref check),
   Open Graph, Twitter card, robots directives, viewport.
4. **Hreflang + x-default + pagination** validation.
5. **URL-structure checks**: clean / lowercase / hyphenated / depth /
   query-param noise.
6. **External resources**: `robots.txt` summary, sitemap status, IndexNow key
   file.
7. **LLM applies the 8-category rubric** (Crawlability, Indexability, Security,
   URL Structure, Mobile, Core Web Vitals, SSR, Page Speed) for a 0-100 score.
8. **Mark off-page items as `requires external verification`** rather than
   fabricating: PageSpeed Insights for CWV / TTFB / page weight,
   response-header inspection for security headers.

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks for a technical SEO audit / SSR check / canonical or
> hreflang check / IndexNow check, your **first action** is `run_skill_script`
> with `dir_name: geo-technical` and `scripts/technical_signals.py`. The script
> fetches the page + robots.txt + sitemap + IndexNow key file and extracts
> every measurable on-page signal.
>
> If the tool is genuinely missing from your tool list, say so **after**
> attempting the call.
>
> ## ⛔ NO FABRICATION
>
> The script prints the **technical-signals extraction to stdout** —
> `run_skill_script` returns it to you.
>
> - All facts in your audit come from the script: canonical presence and
>   target, hreflang list, viewport value, JSON-LD types, word counts, HTTPS,
>   sitemap/robots/IndexNow status, etc. Never invent any of these.
> - The 8 **category scores are your judgment**, but each score must be
>   grounded in the script's signals.
> - For categories that need external data (**Core Web Vitals**, **Page Speed
>   & Server**, response-header-dependent **Security**), state plainly that a
>   PageSpeed Insights run / header check is required, score them `N/A` with
>   a recommendation, and **do not invent values**. A fabricated "LCP 2.3s" is
>   worse than an honest "needs PSI".

## Invocation — exact argv shapes

> **Do NOT add `read_outputs` to this call.** The script prints its full
> signals to stdout — that stdout is your input. The `*-ANALYSIS`/`*-SCORE`
> deliverable is what *you* author afterward, so it does not exist at run time;
> listing it in `read_outputs` returns an empty result, which looks like a
> failed run and triggers needless re-runs. Send only `dir_name`/`script`/`argv`.

```json
{ "dir_name": "geo-technical", "script": "scripts/technical_signals.py", "argv": ["https://example.com/blog/post"] }
```

The URL is positional. (`--url` exists as a backstop alias.)

## What the script measures vs. what needs external tools

| Category | Script measures | LLM scores from |
|---|---|---|
| 1. Crawlability (15) | robots.txt status; AI-crawler quick summary; sitemap status; noindex meta | the script |
| 2. Indexability (12) | canonical presence + self-ref; hreflang list + x-default; pagination rel=next/prev | the script |
| 3. Security (10) | HTTPS scheme | partial — full security-header audit needs response-header inspection (not returned by the Pyodide proxy). Score HTTPS from the script; mark HSTS/CSP/XFO/XCO as "needs header check". |
| 4. URL Structure (8) | clean URLs, lowercase, hyphens-vs-underscores, depth, query params | the script |
| 5. Mobile (10) | viewport meta tag value | partial — tap targets / font sizes need rendering. Score viewport; mark the rest "needs visual audit". |
| 6. Core Web Vitals (15) | — | **Needs PageSpeed Insights / CrUX field data.** Score `N/A`, recommend a PSI run. |
| 7. SSR (15) | main-content word count, H1/H2/H3 in raw HTML, paragraph count, JSON-LD presence, internal-link count — all from the raw HTML | the script (this is the GEO-critical category) |
| 8. Page Speed (15) | — | **Needs PSI / Lighthouse.** Score `N/A`, recommend a PSI run. |

## SSR detection thresholds (Category 7)

The script counts words/headings/paragraphs in the raw HTML's main content:

| Raw HTML signal | Interpretation |
|---|---|
| Main-content words ≥ 500, H2/H3 present, paragraphs ≥ 5 | Strong SSR — score 15/15 |
| 100-500 words, some headings | Partial SSR — score 8-12/15 |
| < 100 words OR no headings OR no paragraphs | Likely client-rendered — score 0-4/15 + flag as critical |

If the script flags client-rendered, recommend the right SSR framework
(Next.js / Nuxt / SvelteKit / Angular Universal / Astro / Remix) for the
detected stack, or Prerender.io as a generic prerendering option.

## Indexability checks (Category 2)

Score from script signals:

| Signal | Pts |
|---|---|
| Canonical present | 1 |
| Canonical self-referencing (or to a normalized version of the URL) | 2 |
| hreflang present (if multilingual) + x-default fallback | 2 |
| Pagination `rel=next`/`rel=prev` present where paginated | 2 |
| No `noindex` meta on a page that should be indexed | 5 |

## URL Structure (Category 4) — script flags

- Lowercase only (no mixed case) — `mixed_case` flag
- Hyphens, not underscores — `has_underscores` flag
- Path depth — `path_depth`
- Query parameters present — `has_query_params` flag (note potential
  duplicate-content risk)

## Crawlability (Category 1)

The script returns a robots.txt summary: `found`/`not found`, sitemap
directives, and a one-line "AI crawlers explicitly blocked?" check covering
GPTBot / ClaudeBot / PerplexityBot / Google-Extended. For the full
crawler-by-crawler breakdown, call **geo-crawlers** instead.

## IndexNow (Category 1 bonus)

The script checks for `/<key>.txt` under `.well-known/` AND a key reference in
the page head. If absent, recommend implementing it — ChatGPT and Bing Copilot
both index via Bing, which uses IndexNow.

## Overall scoring

| Category | Max | Score from |
|---|---|---|
| Crawlability | 15 | script |
| Indexability | 12 | script |
| Security | 10 | partial (HTTPS only from script) |
| URL Structure | 8 | script |
| Mobile | 10 | partial (viewport only from script) |
| Core Web Vitals | 15 | **N/A — needs PSI** |
| SSR | 15 | script |
| Page Speed | 15 | **N/A — needs PSI** |

If you mark a category `N/A`, present the partial score out of the categories
you *could* score (e.g. `47/60 on-page-measurable, CWV+Speed need PSI`)
rather than guessing a final number. Be explicit about what was and wasn't
measured.

**Interpretation (on-page measurable):**
- ≥85% of measured points: technically sound for GEO + traditional SEO
- 65-84%: minor issues to address
- 45-64%: significant technical debt
- <45%: major issues blocking AI / search visibility

## Output

The script prints the **technical-signals extraction to stdout** — your scoring
input. It also writes `GEO-TECHNICAL-EXTRACT.md` and `.json` to
`~/Documents/synergyAI/outputs/` (`/outputs/` in Pyodide). You produce
`GEO-TECHNICAL-AUDIT.md` (scored audit + remediation) from your scoring.

## Pyodide notes

The script routes every fetch through `/gpt/backend/api/v1/fetch-url` under
Pyodide and uses `urllib` under native CPython. `fetches_urls: false` because
the script self-fetches (multiple URLs: page + robots.txt + sitemap +
indexnow key).

## Limitations

- **Single page per call.** Multi-page crawl-depth / link-graph analysis needs
  a sitewide audit.
- **No HTTP headers in Pyodide.** HSTS, CSP, XFO, XCO, Cache-Control, CDN
  detection require server-side proxying that returns headers; recommend the
  user run a header check separately.
- **No rendered-DOM comparison.** SSR detection is HTML-only; a hybrid app
  that ships some content in HTML and hydrates the rest may score partial.
  Recommend comparing the script's `raw_text_sample` to the browser-rendered
  page if results look ambiguous.
