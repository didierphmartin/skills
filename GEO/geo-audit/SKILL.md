---
name: geo-audit
description: "Orchestrate a full GEO+SEO audit by chaining all the GEO sub-skills (crawlers, technical, content, citability, platform, schema, llms.txt) against one site, then consolidating into a client report. Use for: 'full GEO audit', 'complete audit', 'audit my whole site', 'GEO audit for [url]', 'soup-to-nuts GEO assessment', 'run all GEO checks', 'one-shot audit', 'GEO health check', 'comprehensive AI search audit'. Pipeline: detect business type from the homepage -> run geo-crawlers (domain) -> run geo-technical / geo-content / geo-citability / geo-platform-optimizer / geo-schema (each on the homepage; for publishers also a sample blog post) -> run geo-llmstxt (domain) -> run geo-report to consolidate. The skill is the LLM-driven sequence; the sub-skills do the actual measurement work. No script of its own — every step delegates."
dependencies: []
fetches_urls: false
max_tool_rounds: 12
---

# geo-audit

The **orchestrator**. One command — one full GEO audit, chained across every
sub-skill, ending in a consolidated client report. Use this when the user
wants the whole picture for a site, not a single dimension.

This skill has **no script of its own**. It is a sequence of
`run_skill_script` calls to the other geo-* skills, in a specific order, on
a specific input set, with synthesis at the end.

> ## 🪙 TOOL-CALL BUDGET — EMIT SUB-SKILLS IN PARALLEL
>
> SynergyAI gives this skill a budget of 12 client-tool rounds per chat turn
> (set via `max_tool_rounds` in the frontmatter above; the default for other
> skills is 6). Even so, **prefer parallel batching** — it is faster and
> cheaper. A round can carry many parallel `run_skill_script` calls, and this
> skill's sub-skills are **independent** (each fetches the same URL and runs
> its own analysis; none depends on another's output). Emit them all in **one
> assistant response with multiple parallel tool_use blocks**:
>
> **Round 1 (one assistant response, 6 parallel tool_use blocks):**
> `geo-crawlers`, `geo-llmstxt`, `geo-technical`, `geo-content`,
> `geo-schema`, `geo-platform-optimizer` — all with `argv: ["<URL>"]`.
>
> **Round 2:** `geo-citability` on the homepage (and on one inner page if
> the previous round's outputs revealed a publisher — emit both citability
> calls in parallel in round 2).
>
> **Round 3:** `geo-report` with `argv: []` — consolidates everything into
> `GEO-CLIENT-REPORT.md`.
>
> Total: 3 rounds. Comfortably inside the 12-round budget, leaving plenty of
> room for a follow-up like `geo-proposal` if the user wants a sales
> document — and headroom if your provider serializes some calls instead of
> batching them.
>
> If your provider serializes tool calls instead of batching, fall back to
> the sequential playbook below — but **prefer the parallel pattern**.
>
> ## ⛔ DO NOT SET `read_outputs` ON THE SUB-SKILL CALLS
>
> Every sub-skill script **prints its full extract/report to stdout** — that
> stdout IS your scoring input; you do not need to read any file back. Each
> `run_skill_script` call must contain **only** `dir_name`, `script`, and
> `argv`. **Never add `read_outputs`.**
>
> In particular, do NOT put a `*-ANALYSIS.md`, `*-SCORE.md`, or `*-REPORT.md`
> path in `read_outputs`: those are deliverables **you author afterward**, so
> they do not exist at script-run time. Listing a not-yet-created file in
> `read_outputs` makes the tool result come back empty, which looks like a
> failed run and makes you **re-run the same sub-skills in a loop** until the
> round budget stops the audit. (The script also writes a `*-EXTRACT.md` for
> `geo-report` to gather later — you do not read it here.)
>
> ✅ Correct: `{ "dir_name": "geo-content", "script": "scripts/content_signals.py", "argv": ["https://example.com"] }`
> ❌ Wrong:   adding `"read_outputs": ["/outputs/GEO-CONTENT-ANALYSIS.md", ...]`
>
> ## ✅ REQUIRED FIRST STEP — RUN THE SUB-SKILLS IN SEQUENCE (FALLBACK)
>
> When the user asks for a full GEO audit / complete audit / soup-to-nuts
> audit for a URL, your job is to chain the sub-skill `run_skill_script`
> calls in the order below. Do NOT try to do the work yourself — every
> measurement comes from the sub-skill that owns it.
>
> If a sub-skill is missing from your tool list, list which ones are
> missing and run the rest; produce a partial report flagging the gaps.

## The audit playbook

For a target URL like `https://example.com`:

### Phase 1 — Domain-level signals (run these first; they don't depend on body content)

1. **`geo-crawlers`** with `argv: ["https://example.com"]` — fetches
   robots.txt + AI-discovery files; produces AI Visibility Score and 14-crawler
   access table.
2. **`geo-llmstxt`** with `argv: ["https://example.com"]` — fetches /llms.txt
   and /llms-full.txt; analyze mode if present, generation-context mode
   otherwise.

### Phase 2 — Homepage signals

3. **`geo-technical`** with `argv: ["https://example.com"]` — SSR verdict,
   canonical, viewport, hreflang, JSON-LD types, URL structure.
4. **`geo-content`** with `argv: ["https://example.com"]` — E-E-A-T signals
   (author, dates, trust links, generic-phrasing tells, heading hierarchy).
5. **`geo-schema`** with `argv: ["https://example.com"]` — JSON-LD validation,
   sameAs platform coverage, deprecated types.
6. **`geo-platform-optimizer`** with `argv: ["https://example.com"]` —
   platform-specific signals for AIO / ChatGPT / Perplexity / Gemini / Copilot.

### Phase 3 — Citability sample

7. **`geo-citability`** on the homepage AND, if the site is a publisher /
   blog (detected from Phase 2 signals — see business-type table below) or
   has a clearly visible "Read more" / blog link, on **one representative
   inner page**. This is the only sub-skill that benefits from being run
   per-page rather than per-domain.

### Phase 4 — Consolidate

8. **`geo-report`** with `argv: []` — gathers all the audit outputs you
   just wrote into outputs/ and returns the inventory + score rollup for
   you to synthesize the 12-section client report.

If the user is in a sales context (the request mentioned "proposal" /
"prospect" / "pitch"), follow with:

9. **`geo-proposal`** with `argv: ["<domain>"]` — produces the sales
   proposal from the same audit pool.

## Business-type detection (before Phase 3)

From geo-content + geo-technical + geo-platform-optimizer outputs, classify
the homepage:

| Type | Signals (any 2+ from the script extracts) |
|---|---|
| **SaaS** | path includes `/pricing` or `/features`; JSON-LD `SoftwareApplication`; CTAs like "Sign up" / "Free trial" in nav text; `og:type=website`; subdomain `app.*` mentioned in links |
| **Local Business** | `LocalBusiness` JSON-LD; address / phone in trust-link scan; `/contact` link; map-related domains in outbound links |
| **E-commerce** | `Product` / `Offer` JSON-LD; `/products` or `/shop` in nav; price patterns ($, €) detected in body |
| **Publisher** | `Article` / `NewsArticle` / `BlogPosting` JSON-LD; `/blog` or `/news` in nav; multiple author bylines detected |
| **Agency / Services** | `/case-studies` / `/portfolio` / `/work` in nav; `/team` page; client-logo blocks (heuristic: many small images in a row) |
| **Hybrid** | Multiple of the above — classify by the strongest signal in JSON-LD `@type` |

The business type informs **Phase 3** (publisher → audit one blog post in
addition to homepage) and feeds the geo-report Section 1 ("operating in
[INDUSTRY]") and Section 11 (competitor framing).

## Composite GEO Score — produced by geo-report

`geo-report` computes the composite from the sub-skill outputs using the
weighting baked into its SKILL.md:

```
GEO Score = (Platform × 0.25) + (Content × 0.25) + (Technical × 0.20)
          + (Schema × 0.15) + (Brand × 0.15)
```

You do not compute this yourself — `geo-report` does it. Your job here is
to ensure every sub-skill ran so geo-report has full data.

## Issue severity (used in the consolidated report)

`geo-report`'s template already places issues by priority. For reference,
here is the severity ladder the sub-skills surface findings against:

| Severity | Examples |
|---|---|
| **Critical** — fix immediately | All AI crawlers blocked in robots.txt; SPA with no SSR; domain-wide noindex; no structured data at all; brand not in any sameAs platform |
| **High** — fix within 1 week | GPTBot / ClaudeBot / PerplexityBot blocked; no llms.txt; missing Organization or LocalBusiness schema; no author attribution on content pages |
| **Medium** — fix within 1 month | Partial AI-crawler blocking; llms.txt malformed; ≤5 sameAs platforms; missing FAQ schema on FAQ-style content; thin author bios |
| **Low** — optimize when possible | Schema validation warnings; alt-text coverage <60%; freshness gaps on non-critical pages; missing Open Graph tags |

## Quality gates for the orchestration

- **One URL per audit run.** Do not crawl multiple sites in one orchestrator
  pass — pick one. Sitewide multi-page analysis is out of scope; this
  pipeline audits a representative page set (homepage + optional one inner
  page).
- **Fail-soft.** If a sub-skill errors, surface the error in the consolidated
  report and continue. Do not abort the whole audit on one failure.
- **Reuse, don't re-run.** If the user has run sub-skills recently and the
  outputs/ folder already contains fresh audit files for this domain, call
  **`geo-report`** directly and skip the sub-skill runs. Tell the user what
  you skipped and why ("found a `GEO-CRAWLER-ACCESS.md` from N minutes ago
  for this domain — reusing").
- **Respect off-page caveats.** geo-technical's CWV / Page-Speed categories
  need PageSpeed Insights; geo-platform-optimizer's entity-existence checks
  need external verification. The consolidated report should mark these
  `N/A — requires external tool` (per each sub-skill's SKILL.md), not
  guessed.

## Suggested follow-up commands (offer to the user after the audit)

- **`geo-compare <baseline-file> <current-file>`** — monthly delta report
  versus a previous audit (if one exists in outputs/).
- **`geo-proposal <domain>`** — sales-pitch counterpart to the status report
  (uses the same audit pool).
- **`schema-generate`** (SynergyAI built-in) — produce JSON-LD for any
  missing schemas geo-schema flagged.

## Output

`geo-audit` itself writes nothing directly. The sub-skills write their
extracts to `~/Documents/synergyAI/outputs/`; `geo-report` writes the final
`GEO-CLIENT-REPORT.md`. Tell the user what landed where.

## Pyodide notes

`dependencies: []`, `fetches_urls: false`, no script of its own — every
fetch happens inside the sub-skills, each of which already handles the
dual Pyodide/CPython fetch path through `/gpt/backend/api/v1/fetch-url`.

## Limitations

- **One representative page per call.** This orchestrator audits the homepage
  plus optionally one inner page (for publishers). True sitewide coverage
  (every URL in the sitemap audited individually) would require a much
  longer pipeline; offer it as a manual follow-up if the user requests it.
- **The composite score is only as accurate as the sub-skills' data.** If a
  sub-skill returns partial data (e.g. CWV missing because PSI wasn't run),
  the composite is partial too — geo-report's SKILL.md handles the rescaling.
- **No CRM / billing integration.** This is a measurement + reporting
  pipeline only; output is files in `outputs/`, not external systems.
