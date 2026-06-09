---
name: geo-schema
description: "Audit a page's Schema.org structured data — detect JSON-LD / Microdata / RDFa, validate required + recommended properties per @type, flag deprecated types, analyze sameAs coverage across 14 entity platforms, check speakable / knowsAbout for AI signals. Use for: 'schema audit', 'validate JSON-LD', 'check structured data', 'is my schema complete', 'schema.org audit', 'sameAs check', 'AI entity recognition', 'structured data score', 'why isn't my schema working'. Pipeline: fetch page -> extract all JSON-LD blocks (flatten @graph) -> detect Microdata / RDFa presence -> validate each entity against type-specific required + recommended properties -> classify all sameAs URLs across 14 platforms (Wikipedia, Wikidata, LinkedIn, YouTube, Twitter/X, Facebook, GitHub, Crunchbase, etc.) -> flag deprecated @types -> check speakable + knowsAbout presence -> emit structured audit. The LLM applies the 100-point rubric and recommends missing schemas (then calls schema-generate to produce fresh markup if the user wants it)."
dependencies: [beautifulsoup4]
fetches_urls: false
---

# geo-schema

Audit a page's Schema.org structured data — the primary machine-readable signal
that tells AI systems WHAT an entity is, what it does, and how it connects to
other entities. A complete entity graph (especially the `sameAs` array)
dramatically increases citation probability across all AI search platforms.

This is the **validator**. To produce fresh JSON-LD for missing schemas, call
the **`schema-generate`** skill after the audit.

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks for a schema / structured-data audit, your **first
> action** is `run_skill_script` with `dir_name: geo-schema` and
> `scripts/schema_audit.py`. The script fetches the page, parses every
> JSON-LD block, detects Microdata / RDFa, classifies sameAs URLs, and
> validates against the spec.
>
> If the tool is genuinely missing, say so **after** attempting the call.
>
> ## ⛔ NO FABRICATION
>
> The script prints the **parsed schema + validation results to stdout** —
> `run_skill_script` returns it to you.
>
> - Every `@type`, property presence/absence, sameAs URL, format
>   (JSON-LD / Microdata / RDFa) flag, validation issue, and platform
>   classification comes from the script. Never invent a schema you didn't
>   see, never claim a property is "valid" if the script didn't report it.
> - The **scoring is your judgment** but each score must be grounded in the
>   script's signals.
> - If the script reports no structured data at all, recommend running
>   **`schema-generate`** to produce a starting set — do not invent a
>   "current schema" that isn't there.

## Invocation — exact argv shapes

> **Do NOT add `read_outputs` to this call.** The script prints its full
> extract to stdout — that stdout is your input. The `*-ANALYSIS`/`*-SCORE`
> deliverable is what *you* author afterward, so it does not exist at run time;
> listing it in `read_outputs` returns an empty result, which looks like a
> failed run and triggers needless re-runs. Send only `dir_name`/`script`/`argv`.

```json
{ "dir_name": "geo-schema", "script": "scripts/schema_audit.py", "argv": ["https://example.com/blog/my-post"] }
```

The URL is positional. (`--url` exists as a backstop alias.)

## What the script extracts

**Format detection.** JSON-LD block count, total entities parsed (flattens
`@graph`), and presence flags for Microdata (`itemscope`/`itemtype`) and RDFa
(`typeof`/`property`). JSON-LD is the GEO-preferred format — the LLM
recommends migrating Microdata/RDFa to JSON-LD if those are the only formats
present.

**Per-entity validation.** For every entity the script knows, it reports which
required properties are missing and which recommended properties are missing.
Known types and their property maps:

| @type | Required | Recommended |
|---|---|---|
| `Organization` | `name`, `url` | `logo`, `sameAs`, `description`, `foundingDate`, `founder`, `address`, `contactPoint`, `knowsAbout` |
| `LocalBusiness` | `name`, `url`, `address`, `telephone` | `openingHoursSpecification`, `geo`, `priceRange`, `aggregateRating` |
| `Article` / `NewsArticle` / `BlogPosting` | `headline`, `datePublished`, `author` | `dateModified`, `image`, `publisher`, `speakable` |
| `Person` | `name` | `url`, `sameAs`, `jobTitle`, `worksFor`, `knowsAbout`, `alumniOf`, `award` |
| `Product` | `name`, `image`, `offers` | `description`, `brand`, `sku`, `aggregateRating`, `review` |
| `WebSite` | `name`, `url` | `potentialAction` (SearchAction) |
| `FAQPage` | `mainEntity` | — |
| `SoftwareApplication` | `name`, `applicationCategory` | `operatingSystem`, `offers`, `aggregateRating`, `featureList` |
| `BreadcrumbList` | `itemListElement` | — |

Entities of unknown `@type` are listed with their properties but skipped from
required-property validation.

**Deprecated / restricted types flagged.** Per Google's recent guidance:

| Type | Status |
|---|---|
| `HowTo` | rich results removed Sep 2023 |
| `SpecialAnnouncement` | deprecated Jul 2025 |
| `CourseInfo`, `EstimatedSalary`, `LearningVideo` | retired Jun 2025 |
| `ClaimReview`, `VehicleListing` | retired from rich results Jun 2025 |
| `PracticeProblem` | retired late 2025 |
| `FAQPage`, `QAPage` | restricted to gov/healthcare (still useful for AI extraction) |

**`sameAs` analysis.** Every sameAs URL across all entities is classified by
platform — 14 known platforms tracked:

Wikipedia · Wikidata · LinkedIn · YouTube · Twitter/X · Facebook · Instagram ·
GitHub · Crunchbase · Google Scholar · ORCID · Apple App Store · Google Play ·
BBB

The script tells you which platforms are PRESENT and which are MISSING. The
LLM recommends additions based on entity type (e.g. Wikipedia + Wikidata +
LinkedIn for any Organization; Google Scholar + ORCID for academics; GitHub +
Crunchbase for tech startups).

**AI-specific signals.** Presence of `speakable` (voice / AI-assistant
extraction hints) and `knowsAbout` (topical-expertise signal) — both strongly
GEO-relevant.

## Scoring rubric (0-100)

| Criterion | Pts | Source |
|---|---|---|
| Organization or Person schema present + complete (required + most recommended) | 15 | script |
| sameAs covers ≥5 priority platforms | 15 | script (count by platform) |
| Article schema with full author + dates | 10 | script (entity validation) |
| Business-type-specific schema present (Product / LocalBusiness / SoftwareApplication) | 10 | script |
| WebSite + SearchAction (`potentialAction`) | 5 | script |
| BreadcrumbList on inner pages | 5 | script |
| JSON-LD format (not Microdata/RDFa only) | 5 | script (format presence) |
| Server-rendered (JSON-LD in raw HTML) | 10 | script — if the script found JSON-LD at all, it's in raw HTML; an SPA injecting via JS would have shown empty |
| `speakable` on Article schemas | 5 | script |
| Valid JSON + recognized `@type` (no parse errors) | 10 | script (warnings) |
| `knowsAbout` on Organization / Person (≥3 topics) | 5 | script |
| No deprecated `@type` present | 5 | script |

**Interpretation:** ≥85 exceptional · 70-84 good · 55-69 average · 40-54 below
average · <40 needs immediate work.

## sameAs priority order (LLM uses for recommendations)

In approximate priority for GEO entity recognition:

1. Wikipedia · 2. Wikidata · 3. LinkedIn · 4. YouTube · 5. Twitter/X ·
6. Facebook · 7. Crunchbase · 8. GitHub · 9. Google Scholar · 10. ORCID ·
11. Instagram · 12. Apple App Store / Google Play · 13. BBB ·
14. Industry-vertical directories

For an Organization, the **top 3 (Wikipedia / Wikidata / LinkedIn)** are
table stakes. For a Person, add Google Scholar + ORCID (academics) or
GitHub + LinkedIn (tech).

## After the audit

If the LLM identifies missing schemas:

1. List them explicitly with the entity and what's missing.
2. Recommend calling **`schema-generate`** to produce the missing JSON-LD —
   that's its specific purpose.
3. For schemas that exist but are incomplete (missing recommended
   properties), include a concrete patch example using the script's reported
   field set.

## Output

The script prints the **schema audit to stdout** — that is your scoring input.
It also writes `GEO-SCHEMA-EXTRACT.md` and `.json` to
`~/Documents/synergyAI/outputs/` (`/outputs/` in Pyodide). You produce
`GEO-SCHEMA-REPORT.md` (scored audit + recommendations) from your scoring.

## Pyodide notes

The script routes the fetch through `/gpt/backend/api/v1/fetch-url` under
Pyodide and uses `urllib` natively. `fetches_urls: false` because the script
self-fetches.

## Limitations

- **Single page per call.** Sitewide schema coverage (every page validated)
  needs an orchestrator — pair with `geo-audit` once that ports.
- **No URL HEAD-check of sameAs links.** Verifying each platform URL returns
  200 would require many additional fetches; the script reports the URLs and
  the LLM may recommend the user spot-check.
- **No live Google Rich Results / Schema.org Validator round-trip.** This is
  a structural audit only — recommend the user run the official validators
  before deployment.
- **Cross-page schema** (e.g. Organization defined on homepage and reused on
  inner pages) is not analyzed across pages here.
