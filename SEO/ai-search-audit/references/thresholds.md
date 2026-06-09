# Thresholds and rationale

Every numeric threshold used by the audit, the severity it triggers, and why that number.

## Severity scoring

| Severity | Pts deducted | Stacks to |
|---|---|---|
| Critical | −10 | Unbounded (each issue costs 10) |
| Recommended | −3 | Unbounded (each issue costs 3) |
| Nice-to-have | −1 | Unbounded (each issue costs 1) |

Score floor: 0. Grade scale: A ≥90, B ≥80, C ≥70, D ≥60, F <60.

## 1. Cognitive structure

### H1 uniqueness
- **0 H1s** → critical. Without an H1, AI engines fall back to `<title>` or guess; topic detection breaks.
- **>1 H1** → recommended. Multiple H1s confuse topic ranking; common in old WordPress themes that put the site name in an H1.

### Heading hierarchy
- **Any skipped level** (e.g. H2 → H4) → recommended. Skipped levels break the outline tree LLMs use to weight section importance.

### Section length (text per H2/H3)
Computed as the words between one heading and the next.
- **0 words** (empty section, just a heading) → recommended. Looks like broken structure.
- **1–39 words** (thin) → recommended. AI overviews extract 50–150 word snippets; <40 rarely gets cited.
- **40–400 words** → ideal range, no finding.
- **>400 words** (bloated) → nice-to-have. Forces aggressive summarization that loses precise claims.

Rationale for 40–400: snippet extractors typically pull 50–150 words verbatim; sections need enough content to fill that, but not so much that the model has to rewrite. The sweet spot is two to four short paragraphs per H2.

### Lead sentence thesis
Heuristic: the first 25 words of each section's first paragraph should contain at least one content word (>3 chars) from the heading.
- **>50% of sections fail** (and ≥3 sections exist) → recommended.

Why this heuristic: AI engines weight the first sentence heavily when picking snippets. A section titled "Pricing models" whose first sentence is "When we built this in 2022..." buries the answer. The check fires when a majority of sections have this problem; a few weak leads is normal.

### List / table density
Computed as `(list_count + table_count) / section_count`.
- **<15% with ≥4 sections** → recommended. AI overviews quote lists/tables ~3× more than equivalent prose because they extract verbatim.
- **≥30%** → strength.

The 15% floor avoids penalizing short pages that legitimately don't need lists; the 30% ceiling rewards pages that systematically convert enumerable claims to structure.

### Citation density
Computed as the fraction of `<p>` tags containing at least one `<a href>` to a different host (https/http only — internal anchors and same-host links don't count).
- **<10%** → recommended. Pages with no external citations look unsourced; AI engines treat citations as authority signals.
- **≥30%** → strength.

The 10% floor is generous — even opinion pieces typically link to *something*. The 30% target reflects what well-cited journalism and technical writing look like.

### Long paragraph penalty
- **>20% of paragraphs over 120 words** → nice-to-have.

Long paragraphs force LLMs to summarize rather than quote verbatim, which loses precision. The 20% threshold lets a few long passages slip through; firing only when long paragraphs are systemic.

### HTML5 semantic landmarks
- **No `<main>` AND no `<article>`** (also accepts `role="main"`) → critical. AI engines locate primary content via these landmarks; absence forces them to guess.
- **Missing any of `<header>` / `<nav>` / `<footer>`** (nav also accepts `role="navigation"`) → recommended. Each missing landmark removes one chrome-vs-content signal.
- **All four present (main/article + header + nav + footer)** → strength.

The split severity reflects impact: `<main>`/`<article>` is what tells a parser "this is the article"; the others delimit chrome. A blog post with only `<main>` is still parseable; a page with no `<main>` at all isn't.

### Div soup
- **Body contains zero of `<article>` / `<section>` / `<header>` / `<footer>` / `<nav>` / `<main>` / `<aside>`** → critical.

If even one semantic tag is present this doesn't fire — it's a catch-all for pages built entirely from `<div>`s (often React/Vue SPAs without semantic discipline, or old WYSIWYG output).

### Prose in `<p>` tags
Computed against the article root: expected `<p>` count = `max(3, body_text_words // 200)`.
- **Body has ≥300 visible words but the article root has fewer `<p>` tags than expected** → recommended.

Snippet extractors pull primarily from `<p>` elements. Text sitting in `<div>`s or `<span>`s is harder to lift cleanly. The 300-word floor avoids firing on very short pages; the `/200` ratio assumes one paragraph per ~200 words.

### Headings styled as `<div>` / `<span>`
Heuristic: `<div>`/`<span>` whose class name matches `h1`…`h6`, `heading`, `title`, or `headline` (with separator boundaries to avoid matching `subtitle`, `heading-link`, etc.) and whose text is under 200 chars.
- **Any match** → nice-to-have.

Nice-to-have rather than recommended because the heuristic has known false positives (e.g. card titles legitimately styled with `class="title"`). Catches the obvious "designer used divs because of CSS habits" pattern without aggressive scoring.

## 2. Machine-readable metadata

### JSON-LD structured data
- **No `<script type='application/ld+json'>` block** → critical. Highest-impact missing signal.
- **Present but unparseable JSON** → recommended (silently skipped by engines).
- **Present and parsed** → strength, with the `@type` listed.

The audit doesn't (yet) validate the JSON-LD against schema.org — just checks parseability and reports the `@type`.

### Meta description
- **Missing or empty** → critical.
- **<70 chars** → recommended (too short to fill snippet slot).
- **>200 chars** → nice-to-have (truncated in SERPs around 160; truncation breaks meaning).
- **70–200 chars** → no finding; **120–160 chars** → strength.

The 120–160 sweet spot matches Google's SERP snippet length as of 2024; AI overviews use similar truncation.

### Open Graph
Required tags: `og:title`, `og:description`, `og:type`.
- **Any of the three missing** → critical.
- **All three present, no `og:image`** → nice-to-have.

OG is what AI engines (and social previews, and chat link unfurls) read to extract page identity. Missing OG = page renders as a blob in every preview surface.

### Canonical link
- **Missing or empty** → recommended.

Without canonical, duplicate URLs (with/without trailing slash, with tracking params) split authority signals.

### Robots meta
- **Contains `noindex`** → critical.

If intentional, the user knows. If not, the page is invisible to all engines.

### `<html lang>` attribute
- **Missing** → nice-to-have.

Helps AI engines route the page to the right linguistic audience.

## 3. Asset hygiene

### `<title>` tag
- **Missing or empty** → critical.
- **<20 or >80 chars** → recommended.
- **20–30 or 60–80 chars** → nice-to-have.
- **30–60 chars** → no finding.

30–60 chars matches the SERP truncation point and provides enough room for a real headline without padding.

### Image alt-text coverage
Computed as `images_with_non_empty_alt / total_images`.
- **<50%** → critical.
- **50–80%** → recommended.
- **≥80%** → strength.

Decorative images can use `alt=""` explicitly — that counts toward coverage in the audit's eyes (it's a deliberate signal that the image is decorative).

### Empty / `#`-only links
Counts `<a>` tags with empty `href` or `href="#"` that have visible text.
- **>5** → recommended.
- **1–5** → nice-to-have.

These look like broken navigation. AI crawlers may down-weight pages that appear unfinished.

## What this audit does NOT measure

Deliberately out of scope — these matter but require different tools:

- **Page speed / Core Web Vitals** — use Lighthouse.
- **Mobile rendering** — use Lighthouse / browser DevTools.
- **Backlinks / domain authority** — use Ahrefs, Semrush, Moz.
- **Brand entity recognition** — requires Knowledge Graph data.
- **JavaScript-rendered content** — the audit reads raw HTML. SPAs without SSR appear nearly empty to both this audit and most AI crawlers (which is itself the finding).
- **Schema.org validation** — the audit checks JSON-LD parseability and `@type` but doesn't validate against the full schema spec. Use https://validator.schema.org for that.
- **Internationalization** (`hreflang`, multi-language) — not currently checked.

## Tuning

If your domain has different priorities, edit `SEVERITY_WEIGHT` at the top of `audit.py` to re-weight, or comment out individual `audit.add(Finding(...))` calls. Each finding is independent and can be muted without affecting others.
