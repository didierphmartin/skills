---
name: seo-plan
description: "Generate a strategic SEO plan for a new or existing site, structured by business type. Use for: 'SEO plan', 'SEO strategy', 'content strategy for X', 'SEO roadmap', 'plan SEO for new SaaS', 'site architecture plan', 'keyword strategy', 'content calendar plan'. Pipeline: detect business type from URL signals (SaaS = /pricing, /features, /docs; Local Service = phone, address, geo; E-commerce = /products, /cart; Publisher = /blog, author pages; Agency = /case-studies, /portfolio) OR accept --type override; load matching industry template (saas / local-service / ecommerce / publisher / agency / generic); scaffold a 4-phase implementation roadmap (Foundation weeks 1-4, Expansion 5-12, Scale 13-24, Authority months 7-12); emit PLAN.md with industry template inserted + per-phase task scaffolding + relevant quality gates. Mostly LLM-driven planning with deterministic industry detection and scaffolding. Pairs with sitemap-audit, topic-cluster, seo-content-brief."
dependencies: [beautifulsoup4]
fetches_urls: false
---

# seo-plan

> Strategic SEO planning by business type. Scaffolds a 4-phase implementation roadmap.

The deterministic core: detect the business type from URL/content signals (or accept a `--type` override) and scaffold the plan structure. The strategic content (specific recommendations, keyword strategy, etc.) is filled in by the LLM using the loaded industry template as guidance.

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks for an SEO plan / strategy / roadmap, your first action is `run_skill_script`. The script auto-detects industry from the URL (if provided) or uses `--type`.
>
> ## ⛔ NO FABRICATION
>
> The script writes a PLAN.md with the detected business type and the loaded industry template. The plan SKELETON is from the script. The plan CONTENT (specific recommendations, keyword targets, competitors) is filled in by you (the LLM) during the chat — but only based on real signals from the user's site or what the user tells you. Don't invent competitors, keyword volumes, or domain authority numbers.

## Invocation — exact argv shapes

```json
// Auto-detect industry from URL
{ "dir_name": "seo-plan", "script": "scripts/audit.py", "argv": ["https://example.com"] }

// Force a specific business type
{ "argv": ["https://example.com", "--type", "saas"] }

// Plan for a new project (no existing site)
{ "argv": ["--type", "saas", "--new-project"] }

// List the 6 supported business types
{ "argv": ["--list-types"] }
```

CLI for reference:

```bash
# Auto-detect from URL
python scripts/audit.py https://example.com

# Force type
python scripts/audit.py https://example.com --type local-service

# New project — no URL, just industry
python scripts/audit.py --type ecommerce --new-project

# Show available industries
python scripts/audit.py --list-types
```

## Business types supported

| Type | Signals |
|---|---|
| `saas` | `/pricing`, `/features`, `/integrations`, `/docs`, "free trial", "sign up" |
| `local-service` | Phone number, address, service area, "serving [city]", Google Maps embed |
| `ecommerce` | `/products`, `/collections`, `/cart`, "add to cart", Product schema |
| `publisher` | `/blog`, `/articles`, `/topics`, Article schema, author pages, publication dates |
| `agency` | `/case-studies`, `/portfolio`, `/industries`, "our work", client logos |
| `generic` | Fallback when none of the above signals are strong enough |

## What the plan contains

`~/Documents/synergyAI/outputs/PLAN.md` (or `/outputs/...` in Pyodide):

- **Detected business type** + confidence + signals that matched
- **Industry template** — full content of `references/<type>.md` inserted as a section
- **Site context** (when URL is provided) — title, meta description, existing schema types, OG type
- **4-phase implementation roadmap** with task scaffolding:
  - **Phase 1: Foundation** (weeks 1-4) — technical setup, core pages, schema, analytics
  - **Phase 2: Expansion** (weeks 5-12) — primary-page content, blog launch, internal linking
  - **Phase 3: Scale** (weeks 13-24) — advanced content, link building, GEO optimization
  - **Phase 4: Authority** (months 7-12) — thought leadership, PR, advanced schema
- **Industry-specific quality gates** (e.g. local-service: 30+ location pages warning, 50+ hard stop)
- **Cross-skill action items** — which other skills to run next (sitemap-audit, topic-cluster, etc.)

## What this skill does NOT do

- **Doesn't generate specific keywords** — use `topic-cluster` for that
- **Doesn't write content briefs** — use `seo-content-brief` for that
- **Doesn't audit existing pages** — use `ai-search-audit` for that
- **Doesn't measure domain authority** — needs paid APIs (Moz/Ahrefs) blocked in your env

This skill is the strategic-framework layer. The other skills are tactical.

## Pyodide notes

- `fetches_urls: false` — script does its own page fetch when URL is provided
- Output detects Pyodide and routes to `/outputs/`

## Reference files

Each industry template is a detailed markdown (~150 lines) describing typical URL patterns, content priorities, schema needs, internal linking structure, and growth tactics for that business type. Lifted from claude-seo's `seo-plan/assets/`.

| File | Type |
|---|---|
| `references/saas.md` | SaaS / B2B software |
| `references/local-service.md` | Local services (plumbers, dentists, contractors) |
| `references/ecommerce.md` | Online stores |
| `references/publisher.md` | Blogs / news / publications |
| `references/agency.md` | Marketing / consulting / professional services agencies |
| `references/generic.md` | Catch-all when business type doesn't fit cleanly |

## Limitations

- **Industry detection is heuristic.** Catches the obvious cases; hybrid businesses (e.g. SaaS + local-service) may need `--type` override.
- **Phase durations are typical estimates.** A 4-week Foundation phase assumes one part-time SEO; faster or slower depending on team size.
- **Strategy content requires LLM reasoning.** The script provides structure; the LLM provides specifics. Don't expect the script to invent keyword targets or competitor analyses.
- **Doesn't replace a real audit.** Run `ai-search-audit` + `sitemap-audit` separately to ground the plan in current-state findings.

## Related skills

- **`sitemap-audit`** — design the site architecture the plan recommends
- **`topic-cluster`** — turn the plan's content strategy into concrete keyword clusters
- **`seo-content-brief`** — turn each cluster post into a writable brief
- **`ai-search-audit`** — verify the plan was executed correctly on each page
