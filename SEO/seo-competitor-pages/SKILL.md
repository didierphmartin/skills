---
name: seo-competitor-pages
description: "Generate SEO comparison and alternatives pages: 'X vs Y' head-to-head, 'alternatives to X' roundups, 'best [category]' lists. Triggers: 'comparison page', 'vs page', 'alternatives page', 'X vs Y', 'compare competitors', 'alternative to X', 'best tools roundup'. Pipeline: fetch your-product + competitor URL(s); extract product names, meta, existing Product schema; emit structured template — title, meta, H2/H3 outline, blank feature-comparison table (writer fills the matrix), Product JSON-LD with AggregateRating placeholder, FAQ schema scaffolding, CTA placement, fairness checklist (every claim verifiable, pricing 'as of [date]', balanced framing). Modes: VS (two products) and ALTERNATIVES (your product + N alternatives from URL list). Output: COMPARISON-PAGE.md ready for the writer. Pairs with schema-generate (validates Product schema) and ai-search-audit (run after publishing)."
dependencies: [beautifulsoup4]
fetches_urls: false
---

# seo-competitor-pages

> Generate the SKELETON for "X vs Y" and "alternatives to X" comparison pages.

This skill produces the page structure + fairness scaffolding. The actual feature claims must be written by a human who has verified each one against the competitor's product. The script will refuse to invent comparison content.

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks for a comparison page / vs page / alternatives page with at least two products, your first action is `run_skill_script`.
>
> ## ⛔ NO FABRICATION — ESPECIALLY HERE
>
> Comparison pages where you invent competitor features or pricing are a legal risk (false advertising) and an SEO penalty risk (low-quality content). The script's job is to produce the STRUCTURE + fairness scaffolding. Don't fill in competitor claims from inference. If you genuinely don't know a competitor's pricing or features, write `[VERIFY: <what to check>]` and leave it to the human to fill in.

## Invocation — exact argv shapes

```json
// VS mode — exactly two products
{ "dir_name": "seo-competitor-pages", "script": "scripts/audit.py", "argv": ["--your-url", "https://yourproduct.com/", "--vs", "https://competitor.com/"] }

// ALTERNATIVES mode — your product + a list of alternatives
{ "argv": ["--your-url", "https://yourproduct.com/", "--alternatives", "/path/to/alternatives.txt"] }

// Custom output dir
{ "argv": ["--your-url", "...", "--vs", "...", "-o", "/path/to/output"] }
```

CLI for reference:

```bash
# Head-to-head
python scripts/audit.py --your-url https://yourapp.com/ --vs https://competitor.com/

# Alternatives roundup (URL list file, one per line, # for comments)
python scripts/audit.py --your-url https://yourapp.com/ --alternatives alternatives.txt

# Best-[category] roundup (no --your-url, just the list)
python scripts/audit.py --alternatives alternatives.txt --mode roundup
```

## Modes

### VS mode (`--your-url` + `--vs`)

Head-to-head between exactly two products. Output includes:
- Title pattern: `[Your Product] vs [Competitor]: [Decisive Angle] (2026)`
- Meta description scaffolding
- H1: `[Your Product] vs [Competitor]: Which [category] is Right for You?`
- Comparison table skeleton (10-15 feature rows, three columns: Feature / Your Product / Competitor)
- Pros/cons per side
- "Best for" decision tree (when to pick which)
- Pricing comparison table with `as of [date]` requirement
- Product schema (×2) with AggregateRating placeholder
- FAQ schema scaffolding with 4-5 common questions
- CTA placement guidance

### ALTERNATIVES mode (`--your-url` + `--alternatives FILE`)

Your product against N alternatives. Output:
- Title: `[N] Best [Your Product] Alternatives in 2026`
- One section per alternative with summary, pros/cons, best-for, pricing
- Comparison table with N+1 columns
- ItemList + ListItem schema referencing each alternative
- "Why we built [Your Product]" callout (differentiation framing)

### ROUNDUP mode (`--alternatives FILE --mode roundup`)

"Best [category] tools" without anchoring to your own product. Use this for editorial content or when not promoting a specific product. Same structure as ALTERNATIVES minus the "Why we built X" section.

## Output

`~/Documents/synergyAI/outputs/COMPARISON-PAGE.md` (or `/outputs/...` in Pyodide), plus a sibling JSON-LD file `schema.json`.

The markdown contains:
- Detected metadata (product names, meta descriptions, existing schema) from each fetched URL
- Title + meta + URL slug suggestion
- Full page outline with H1/H2/H3
- Empty comparison table with rows labeled but cells empty (writer fills in)
- All `[VERIFY: ...]` markers the writer must address
- Product / FAQ / ItemList schema templates ready to paste
- Fairness checklist (15 points) the writer ticks before publishing

## Fairness requirements

Every comparison-page generator's biggest risk is unfair competitor framing. This skill enforces:

1. **Every feature claim has a source.** Each cell in the comparison table must cite the competitor's docs/pricing page.
2. **Pricing must include `as of [date]`.** Pricing pages change; out-of-date pricing in a comparison is a credibility hit.
3. **Both sides have pros AND cons.** Pages where Your Product has all pros and Competitor has all cons are obvious bias signals to both readers and crawlers.
4. **"Best for" framing > "X wins" framing.** Acknowledge that different users have different needs — improves trust and reduces legal risk.
5. **Update cadence.** Comparison pages stale fast. Include a `dateModified` ≤ 6 months on Product schema.

The output includes a 15-point fairness checklist generated per-page.

## Pyodide notes

- `fetches_urls: false` — script does its own page fetches
- Output detects Pyodide and routes to `/outputs/`

## Limitations

- **Does NOT auto-fill the feature comparison table.** That's the writer's job — anything the script invents would be a fabrication.
- **Static-HTML extraction.** Competitor pages with JS-rendered pricing or feature matrices will show empty data; the script flags this for manual verification.
- **No real-time competitor monitoring.** This is a one-shot template generator; comparison pages need quarterly refresh, which is on the user.
- **Doesn't generate legal disclaimers.** "Comparison based on publicly available information as of [date]" boilerplate is in the template but adapt for jurisdiction.

## Related skills

- **`schema-generate`** — validates the Product schema this skill emits
- **`ai-search-audit`** — run after publishing to verify the comparison page passes the audit
- **`topic-cluster`** — comparison pages often anchor a content cluster (X vs Y page → X review + Y review + Y alternative pages)
