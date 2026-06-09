# seo-plan

> Strategic SEO planning scaffold by business type — 4-phase implementation roadmap.

## What this skill does

Deterministic scaffolding + LLM-driven specifics. The script:

1. **Detects business type** from URL signals (or accepts `--type` override)
2. **Loads the matching industry template** from `references/`
3. **Scaffolds a 4-phase roadmap** with task checklists per phase
4. **Surfaces industry-specific quality gates** (e.g. 50+ location pages = doorway-page risk)
5. **Recommends cross-skill action items** — which Phase 1 / Phase 2 skill to run in which roadmap phase

The actual strategy content (keyword targets, competitor analysis, domain authority) is for the LLM to fill in during the chat using the loaded template as context.

## Output

`~/Documents/synergyAI/outputs/PLAN-<type>.md` (or `/outputs/...` in Pyodide):

- Business-type detection table — top 5 types ranked with reasons + signals
- Site context (when URL provided) — title, meta description, existing schema, OG type
- **Industry template** (full content of `references/<type>.md` inserted)
- **4-phase roadmap** with task checkboxes:
  - Phase 1 — Foundation (weeks 1-4)
  - Phase 2 — Expansion (weeks 5-12)
  - Phase 3 — Scale (weeks 13-24)
  - Phase 4 — Authority (months 7-12)
- **Quality gates** specific to the detected business type
- **Cross-skill recommendations** — which skill to run at each phase

## CLI reference

```bash
python scripts/audit.py [INPUT] [OPTIONS]
```

| Flag | Default | Purpose |
|---|---|---|
| `INPUT` (positional) | — | URL to detect business type from |
| `--url URL` | — | Alias for positional argument |
| `--type T` | auto-detect | Force a business type. Skip detection. |
| `--new-project` | off | No URL — plan from scratch. Requires `--type`. |
| `--list-types` | — | Show the 6 supported types and exit |
| `-o`, `--output PATH` | `~/Documents/synergyAI/outputs/` | Output dir |
| `--stdout` | off | Print to stdout |

## Business-type detection

Six types, scored from URL path, internal-link patterns, content cues, JSON-LD schema, and (for local-service) phone + address heuristics.

| Type | URL signals | Content cues | Schema |
|---|---|---|---|
| `saas` | `/pricing`, `/features`, `/integrations`, `/docs`, `/api`, `/login`, `/signup` | "free trial", "sign up", "request demo", "pricing plans" | `SoftwareApplication`, `WebApplication` |
| `local-service` | `/contact`, `/location`, `/service-area`, `/visit`, `/book` | "serving ", "service area", "call us", "free quote" | `LocalBusiness`, `ProfessionalService`, `Plumber`, etc. |
| `ecommerce` | `/products`, `/collections`, `/cart`, `/checkout`, `/shop` | "add to cart", "buy now", "free shipping", "in stock" | `Product`, `Offer`, `Store` |
| `publisher` | `/blog`, `/article`, `/news`, `/topics`, `/author` | "by ", "min read", "subscribe to", "newsletter" | `Article`, `BlogPosting`, `NewsArticle` |
| `agency` | `/case-studies`, `/portfolio`, `/work`, `/clients` | "our work", "case study", "trusted by" | `ProfessionalService` |
| `generic` | (catch-all when no signals dominate) | | |

Scoring weights:
- URL pattern on current path: +15
- Internal-link path matches: up to +20 (capped, +5 per pattern matched)
- Content cues: up to +15 (+5 per cue)
- JSON-LD schema match: +20
- Phone number / street address (local-service only): +5 / +10

The highest-scoring non-generic type wins. If all scores are 0, falls back to `generic`.

## Quality gates per type

| Type | Quality gates surfaced |
|---|---|
| `local-service` | 30+ location pages warning; 50+ hard stop; unique NAP per page |
| `ecommerce` | Cannibalization; faceted-nav index bloat; out-of-stock not 404 |
| `publisher` | Tag-page bloat; author E-E-A-T; dating signals |
| `saas` | Comparison-page proliferation; docs/blog separation; pricing-page modificationDate |
| `agency` | Case-study uniqueness; service-page templating; industries-page bloat |
| `generic` | Thin content; internal-linking reachability |

## Phase scaffolding

Common across types but tuned by industry template:

| Phase | Duration | Focus |
|---|---|---|
| 1 — Foundation | weeks 1-4 | Technical setup, core pages, essential schema, analytics |
| 2 — Expansion | weeks 5-12 | Primary content, blog launch, internal linking |
| 3 — Scale | weeks 13-24 | Advanced content, link building, GEO, image optimization, hreflang if multi-language |
| 4 — Authority | months 7-12 | Thought leadership, PR, advanced schema, quarterly audits |

## Reference files

Six industry templates lifted verbatim from claude-seo's `seo-plan/assets/`:

| File | Type |
|---|---|
| `references/saas.md` | SaaS / B2B software |
| `references/local-service.md` | Local services |
| `references/ecommerce.md` | Online stores |
| `references/publisher.md` | Blogs / news / publications |
| `references/agency.md` | Marketing / consulting / professional services |
| `references/generic.md` | Fallback |

Each ~150 lines. The script inserts the full text into the generated PLAN.md.

## Pyodide notes

- `fetches_urls: false` — script does its own URL fetch (single fetch for detection)
- Output detects Pyodide and routes to `/outputs/`

## Limitations

- **Industry detection is heuristic.** Single-page hybrid businesses (SaaS + local-service) may need `--type` override.
- **Phase durations are typical estimates.** Adjust for team size and actual scope.
- **Strategy content requires LLM reasoning.** The script provides structure; the LLM provides specifics. Don't expect the script to invent keyword targets or competitor analyses.
- **Doesn't replace audits.** Pair with `ai-search-audit` (page-level) and `sitemap-audit` (site-level) for ground truth on current state.

## Related skills

- **`sitemap-audit`** — design the architecture the plan recommends
- **`topic-cluster`** — turn the plan's content strategy into concrete keyword clusters
- **`seo-content-brief`** — turn each cluster post into a writable brief
- **`ai-search-audit`** — verify the plan was executed correctly on each page
