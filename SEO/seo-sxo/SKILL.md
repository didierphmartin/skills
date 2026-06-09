---
name: seo-sxo
description: "Search Experience Optimization. Detects page-type mismatches with the SERP, scores pages from multiple personas, derives user stories. Use for: 'SXO audit', 'why isn't my page ranking', 'page type mismatch', 'SERP type analysis', 'persona scoring', 'user story for page', 'search experience audit', 'intent mismatch'. Pipeline: classify your page type (blog-post / listicle / comparison / product / service / landing-page / tool / how-to / glossary); fetch SerpAPI top-10 for the keyword; classify each SERP result with the same taxonomy; compute SERP consensus (dominant page type); detect mismatch (CRITICAL if your type differs from a >60% consensus); score your page against 4 personas (researcher / decision-maker / buyer / new-visitor) on 5 axes (clarity / depth / trust / action / scannability); derive user stories from intent signals. Output: SXO-REPORT.md with mismatch + persona scores + user stories + recommendations. Cost: 1 SerpAPI call. Pairs with ai-search-audit and serp-shape."
dependencies: [beautifulsoup4]
fetches_urls: false
---

# seo-sxo

> Search Experience Optimization — bridges SEO (what Google rewards) and UX (what users need).

The core insight: a page can score 95/100 on technical SEO and still fail to rank if it's the **wrong page type** for the keyword. SXO asks "does this page deserve to rank for this keyword based on what Google is actually rewarding in the SERP?"

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks for SXO / page-type-mismatch / persona scoring / "why isn't my page ranking", your first action is `run_skill_script`. The keyword is auto-detected from the page if not supplied.
>
> ## ⛔ NO FABRICATION
>
> Page-type classifications, persona scores, and user stories must come from the actual page content + SERP data in the report. Never invent SERP consensus or persona reactions.

## Invocation — exact argv shapes

```json
// Full SXO analysis — keyword auto-detected from page title + H1
{ "dir_name": "seo-sxo", "script": "scripts/audit.py", "argv": ["https://example.com/page"] }

// Specific keyword override
{ "argv": ["https://example.com/page", "--keyword", "best running shoes"] }

// Skip SERP fetch (personas-only)
{ "argv": ["https://example.com/page", "--skip-serp"] }
```

CLI for reference:

```bash
# Full analysis
python scripts/audit.py https://example.com/page

# Override keyword
python scripts/audit.py https://example.com/page --keyword "best running shoes"

# Skip SerpAPI (faster, persona-only)
python scripts/audit.py https://example.com/page --skip-serp

# Different locale
python scripts/audit.py https://example.com/page --gl fr --hl fr
```

## What it produces

`~/Documents/synergyAI/outputs/SXO-REPORT.md` (or `/outputs/...` in Pyodide):

- **Your page classification** — primary type + confidence + signals
- **SERP analysis table** — top-10 results, each classified by page type
- **SERP consensus** — dominant type + percentage + content-depth expectations
- **Page-type mismatch detection** — severity (CRITICAL / HIGH / MEDIUM / ALIGNED) + concrete recommendation
- **Persona scores** — 4 personas × 5 axes, with strengths + gaps per persona
- **User stories** — 4-6 "As a [persona], I want to [action] so that [outcome]" statements derived from the page's intent signals
- **Recommendations** — prioritized list of changes to address mismatches + persona gaps

## Page types (taxonomy)

Detected by signals; full rules in `references/page-type-taxonomy.md`:

| Type | Signals |
|---|---|
| `blog-post` | `/blog/`, Article schema, byline, publication date, prose-heavy |
| `listicle` | "best X", "top N", "N best/top", numbered H2s, ranking signals |
| `comparison` | "X vs Y", "X versus Y", comparison table, multi-product schema |
| `how-to` | "how to X", step-numbered headings, instructional pattern |
| `product` | Product schema, "add to cart", price, stock status |
| `service` | Service schema, "book", "contact", LocalBusiness association |
| `landing-page` | Single primary CTA, no nav, conversion-focused |
| `tool` | Calculator / interactive widget, JS-heavy with `<input>` elements |
| `glossary` | Definition pattern, short word count, term-as-H1 |
| `homepage` | Site root, navigation hub, multi-purpose |
| `category-hub` | `/category/`, `/topics/`, ItemList linking to children |

## Persona scoring (4 standard personas, 5 axes each)

Full rubric in `references/persona-scoring.md`:

| Persona | What they need | Top axes |
|---|---|---|
| **Researcher** | Comprehensive, citable, balanced | Depth, Trust |
| **Decision-maker** | Quick scan, key facts, comparisons | Scannability, Clarity |
| **Buyer** | Pricing, social proof, CTA, friction-free | Action, Trust |
| **New-visitor** | Orientation, "is this for me?", clear next step | Clarity, Action |

The 5 axes scored 0-10 per persona:
1. **Clarity** — Can the persona understand what the page offers in 5 seconds?
2. **Depth** — Does the page answer the persona's likely follow-up questions?
3. **Trust** — Author bylines, social proof, dates, sources, security signals
4. **Action** — Is the next step obvious for this persona?
5. **Scannability** — Headings, lists, tables, bold key claims, length appropriate

## User stories

After SERP + persona analysis, the script derives 4-6 user stories. Each follows the format:

`As a [persona], I want to [specific action] so that [outcome].`

Examples (for a CRM comparison page):
- *As a CRM evaluator, I want to see a feature-comparison table so that I can shortlist vendors fast.*
- *As a buyer, I want to find pricing without filling a contact form so that I can budget the purchase.*
- *As a researcher, I want to see independent review citations so that I can trust the comparison.*

User stories are the bridge between SERP-derived intent and UX-actionable changes.

## Cost awareness

| Mode | SerpAPI calls | Page fetches |
|---|---|---|
| Full (default) | 1 | 11 (your page + 10 SERP results) |
| `--skip-serp` | 0 | 1 (your page only — no mismatch detection) |

The skip-serp mode is for fast iteration during page design.

## Configuration

SerpAPI key in `<skill>/.env`:

```
SERPAPI_API_KEY=your_key_here
```

Pre-populated from `serp-shape`'s key.

## Reference files

| File | Purpose |
|---|---|
| `references/page-type-taxonomy.md` | Full classification rules for the 11+ page types |
| `references/persona-scoring.md` | Scoring rubric for the 4 personas × 5 axes |
| `references/user-story-framework.md` | User-story derivation from SERP + persona signals |

## Pyodide notes

- `fetches_urls: false` — script does its own SerpAPI + per-page fetches
- Output detects Pyodide and routes to `/outputs/`
- 11 fetches × async concurrency = ~3-5 seconds typical

## Limitations

- **Page-type classification is heuristic.** Edge cases (hybrid product/blog pages, listicles dressed as comparisons) may misclassify; the report shows confidence + signals so the LLM can sanity-check.
- **Persona scoring is rule-based.** It catches obvious gaps (no CTA for a buyer; no author for a researcher) but won't catch subtle UX issues that require human judgment.
- **One-page-at-a-time.** Doesn't do site-wide SXO audits in v1.
- **SerpAPI cost** — minimum 1 call per analysis.
- **No actual user testing.** This is a heuristic proxy for user experience; pair with real user research for high-stakes pages.

## Related skills

- **`ai-search-audit`** — page quality / structure / GEO (this skill is about FIT WITH SERP, not absolute quality)
- **`serp-shape`** — comparison to top-10 ranking pages on content shape (this skill is about page-type CLASSIFICATION mismatch)
- **`seo-content-brief`** — when SXO surfaces a mismatch, generate a brief for the right page type
