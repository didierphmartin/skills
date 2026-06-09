# seo-competitor-pages

> Generate the structure + fairness scaffolding for "X vs Y", "alternatives to X", and "best [category]" pages.

## What this skill does

Produces page templates with:
- Title / meta / URL slug suggestions
- Full H1/H2/H3 outline
- **Empty** comparison tables for the writer to fill from verified sources
- Product / FAQPage / ItemList JSON-LD schemas
- 15-point fairness checklist
- `[VERIFY: ...]` markers everywhere a claim must be human-validated

**The skill deliberately does NOT auto-fill competitor features or pricing.** That's where comparison pages become legal risks and SEO penalty risks. The script's job is structure + scaffolding; the writer's job is verified claims.

## Modes

| Mode | Inputs | Output |
|---|---|---|
| **VS** | `--your-url URL --vs URL` | Head-to-head between exactly two products |
| **ALTERNATIVES** | `--your-url URL --alternatives FILE` | Your product + N alternatives roundup |
| **ROUNDUP** | `--alternatives FILE --mode roundup` | "Best [category]" without anchoring to your own product |

## Output

`~/Documents/synergyAI/outputs/COMPARISON-PAGE-<slug>.md` + `COMPARISON-SCHEMA-<slug>.json`.

Markdown contains:
- Detected metadata from each fetched product
- Title + meta + slug
- Full page outline
- Empty comparison table with feature rows labeled
- Pricing table with `as of [date]` requirement
- FAQ section with 4-5 suggested questions
- CTA placement guidance
- 15-point fairness checklist

JSON contains:
- VS mode: 2 × Product + 1 × FAQPage schema
- ALTERNATIVES / ROUNDUP: 1 × ItemList with N ListItem(Product) children

## CLI reference

```bash
python scripts/audit.py [OPTIONS]
```

| Flag | Default | Purpose |
|---|---|---|
| `--your-url URL` | — | Your own product's URL. Required for VS and ALTERNATIVES modes. |
| `--vs URL` | — | Competitor URL (VS mode). |
| `--alternatives FILE` | — | Path to a URL list file (one per line, `#` comments). |
| `--mode {vs,alternatives,roundup}` | auto | Force mode. Otherwise auto-derived from inputs. |
| `-o`, `--output PATH` | `~/Documents/synergyAI/outputs/` | Output dir |
| `--stdout` | off | Print markdown to stdout |

## The fairness scaffolding

Comparison pages without fairness scaffolding are SEO and legal risk. The skill enforces 15 checklist items the writer must tick before publishing:

1. Every feature claim cites the competitor's docs/pricing page
2. Pricing includes `as of [date]` + link to live pricing
3. Both sides have pros AND cons (no one-sided framing)
4. "Best for" framing > universal "X wins"
5. Competitor names spelled correctly with trademark symbols where required
6. No claims about customer count / revenue without public citation
7. Pricing currency-tagged + tier-named exactly
8. Marked-unavailable features verified against CURRENT docs
9. Third-party review quotes include source + date
10. Same yardstick for both products (no apples-to-oranges)
11. Disclosure of your relationship to the products
12. Update schedule documented (quarterly minimum)
13. `dateModified` ≤ 6 months in JSON-LD
14. Product names linked to competitor's site on first mention
15. Comparison table balanced (your product doesn't trivially dominate every row)

## Why this matters

Comparison-page SEO is a high-value target — "X vs Y" keywords have buyer intent. But two failure modes:

| Failure | Consequence |
|---|---|
| Auto-generated "X vs Y" pages with no verified content | Google's helpful-content system targets these specifically (multiple updates 2022-2024); pages get de-indexed |
| Biased framing favoring your own product | Damages credibility with readers (savvy buyers notice); legal risk if claims are false |

Done well, "X vs Y" pages are the best-converting content type on most B2B SaaS sites — the buyer is in active comparison mode.

## Pyodide notes

- `fetches_urls: false` — script fetches each product URL itself
- Output detects Pyodide and routes to `/outputs/`

## Limitations

- **Does NOT auto-fill the feature comparison table** — by design
- **Static-HTML extraction.** JS-rendered pricing pages will show empty data; flagged with `[VERIFY:...]`
- **Heuristic product-name extraction.** Pulls `<title>`, then `Product.name` from JSON-LD, then strips trailing brand suffixes (`| Brand`, `- Brand`). Catches common cases; tag-along brand names may need manual correction.
- **No real-time competitor monitoring.** This is a one-shot template generator. Comparison pages need quarterly refresh.
- **No legal disclaimers.** "As of [date]" boilerplate is in the template but adapt for your jurisdiction.

## Related skills

- **`schema-generate`** — validates the Product schema this skill emits
- **`ai-search-audit`** — run after publishing to verify the page passes the audit
- **`topic-cluster`** — comparison pages often anchor a content cluster (the X vs Y page links to X review, Y review, Y alternative roundup)
- **`seo-content-brief`** — for the supporting cluster content
