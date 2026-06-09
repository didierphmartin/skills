---
name: serp-shape
description: "Compare a webpage to the top-10 Google results for a keyword — SERP positioning, content gap, competitive analysis. Requires BOTH a search keyword AND a page URL; ask for the missing piece if only one is given. Triggers: 'how do I rank for X', 'compare my page to top results for X', 'SERP analysis', 'content gap', 'positioning report', 'why isn't my page ranking', 'what do competitors do for keyword X', 'comment me positionner pour X', 'analyse SERP', 'pourquoi ma page ne ranke pas'. Localhost URLs (http://localhost, 127.0.0.1, intranet hosts) are fully supported — the runner fetches them server-side. Never refuse with 'I cannot access localhost'. Output: Markdown report with the SERP's content shape (featured snippet format, People Also Ask, related searches, heading patterns, schema types), per-page top-10 detail, and prioritized recommendations comparing the user's page to the ranking aggregate. For structural page audits or AI-overview readiness, use ai-search-audit instead."
dependencies: [beautifulsoup4]
# NOTE: fetches_urls is INTENTIONALLY false. The bridge's URL pre-fetch
# replaces argv URLs with local file paths, but this script needs to
# (a) construct the SerpAPI URL at runtime (key from .env, never in argv)
# and (b) fetch each top-N page after the SerpAPI call returns. Both
# require keeping URLs as URLs through argv. The script does its own
# fetching via the /api/v1/fetch-url proxy in Pyodide mode and via
# urllib directly in CPython mode.
---

# serp-shape

Compares a webpage to the top-10 Google results for a keyword and reports
where it falls short. Pairs with `ai-search-audit`: that one checks whether
the page is well-structured *in absolute terms*; this one checks whether
the page matches *what currently ranks for this specific query*.

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks for a positioning/comparison/SERP analysis and provides
> both a keyword and a URL, your **first action** is to call `run_skill_script`
> with `dir_name: serp-shape`. Do this even if you suspect the tool may fail —
> the runner reports failures explicitly, and the no-fabrication rules apply
> **after** the call, never before.
>
> Do NOT refuse with phrases like "the tool isn't available in this session"
> or "I cannot access localhost". The runner handles URL fetching for you.
> Just call the tool.
>
> ## 🌐 YOU DO NOT NEED NETWORK ACCESS
>
> You (the LLM) do not fetch URLs. The script fetches them — directly when
> running outside the browser (Claude Desktop, CLI), or via the host's
> URL-fetch proxy when running inside Pyodide. URLs like `http://localhost/cv/`,
> intranet hosts, and any URL the user can reach all work. Just pass them in argv.
>
> ## ⛔ AFTER THE TOOL CALL — NO FABRICATION
>
> If the script fails or doesn't write a report file, your **one and only
> valid response** is to surface the exact error to the user. Never invent
> a score, "top H2s", or recommendations. The only valid source of report
> content is the Markdown file written to `/outputs/`.

## What it does

1. Calls **SerpAPI** with the user's keyword → top-10 organic results plus
   rich SERP blocks (answer_box, related_questions, related_searches,
   knowledge_graph).
2. Fetches the user's page + each of the 10 ranking pages.
3. For each page, extracts: title, meta description, H1, H2 list, word
   count, JSON-LD `@type`s, FAQ-schema presence, list/table counts,
   image count.
4. Aggregates the **SERP shape**: median word count, dominant schema type,
   % with FAQ rich snippets, common H2 patterns, featured-snippet format.
5. Compares the user's page to the aggregate; produces prioritized
   recommendations.

## Setup (one-time per install)

The skill is **autonomous** — it brings its own SerpAPI key. No
host-platform search wrapper is required. Get a free key at
https://serpapi.com (5,000 searches/month free), then:

```bash
cd ~/Documents/synergyAI/skills/serp-shape/
cp .env.example .env
# edit .env, replace placeholder with your real key
```

`.env` is gitignored. Never share it.

## How to use

### From the CLI (Claude Desktop, terminal, anywhere CPython runs)

```bash
python scripts/analyze.py \
    --keyword "AI architect Montreal" \
    --your-page http://localhost/cv/ \
    -o ~/Documents/synergyAI/outputs/ai-architect-montreal-serp-shape.md
```

### From an AI Assistant chat (the LLM constructs the call)

User types something like:
- *"Compare `http://localhost/cv/` to the top 10 Google results for 'AI architect Montreal'."*
- *"Run a SERP analysis: my page is `https://yoursite.com/pricing`, keyword 'best agentic AI platform'."*
- *"Pourquoi ma page `https://monsite.com/` ne ranke pas pour 'plateforme IA' ?"*

The LLM extracts the keyword + URL and emits `run_skill_script` with:
```
argv: [
  "--keyword", "<the keyword>",
  "--your-page", "<the URL>",
  "-o", "/outputs/<keyword-slug>-serp-shape.md"
]
```

### Flag reference

| Flag | Purpose |
|---|---|
| `--keyword TEXT` | Required. The Google query to analyze. |
| `--your-page URL` | Required. The page being optimized. |
| `--depth N` | Optional. Number of SERP results to analyze (1–20, default 10). |
| `--location TEXT` | Optional. SerpAPI location string (default "United States"). |
| `-o`, `--output PATH` | Optional. Output Markdown path. Default `~/Documents/synergyAI/outputs/<slug>-serp-shape.md`. |
| `--json-sidecar` | Optional. Also write a JSON sidecar with the raw shape data. |

## What the report contains

- **Score line** (printed to stderr): one number summarizing position vs SERP.
- **SERP intent map**: featured snippet format, content mix (videos/news/local),
  People Also Ask questions, related searches.
- **Per-page table**: top-10 with title, domain, schema, FAQ, sitelinks indicator.
- **Shape summary**: median word count, common schema, FAQ rate, etc.
- **Gap vs your page**: per-dimension delta.
- **Prioritized recommendations**: ranked by impact (schema > format > length).

## How it differs from `ai-search-audit`

| | `ai-search-audit` | `serp-shape` |
|---|---|---|
| Frame | Absolute (universal spec) | Relative (the current SERP) |
| Input | URL only | Keyword + URL |
| Output | Score 0–100 + finding list | Shape map + prioritized gaps |
| Cost | Free (no external API) | $0.015 per analysis (SerpAPI) |

A page can score 100/100 in `ai-search-audit` and still fail `serp-shape`
if its *content shape* doesn't match what Google rewards for the keyword.
Both checks are useful; they answer different questions.

## Limitations

- **Heuristic, not predictive.** Top-10 ranking changes weekly; SERP-shape
  recommendations are based on a snapshot.
- **Static analysis only.** Doesn't account for site-level signals
  (backlinks, domain authority, internal linking).
- **SerpAPI dependency.** Requires a valid API key in `.env`. The skill
  refuses to run without one.
- **JavaScript-rendered pages.** Pages that rely on client-side rendering
  appear nearly empty to the parser — same limitation as `ai-search-audit`.
