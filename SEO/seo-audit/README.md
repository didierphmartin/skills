# seo-audit

> Whole-site SEO audit — orchestrates the other SEO skills into one SEO Health Score and action plan.

## What this skill does

`seo-audit` is the single entry point for a full-site SEO audit. It is an **orchestrator skill**: it has no analysis code of its own. Instead it directs the LLM to run the other SEO skills in turn, then aggregates their report files into one consolidated audit — an SEO Health Score (0-100) and a prioritized action plan.

It exists because the individual SEO skills each cover one slice (one page, the sitemap, images, hreflang). Users who ask for "a full SEO audit" want all of them at once, scored together. `seo-audit` is that wrapper.

## How it works

There is no skill-to-skill plumbing. Orchestration happens at the LLM level:

1. The LLM has `seo-audit/SKILL.md` in context (dragged into the prompt, attached to a workflow node, or auto-routed from the prompt).
2. Following that SKILL.md, the LLM calls `run_skill_script` once per sub-skill.
3. Each sub-skill runs its own `scripts/audit.py` and writes a report file to `~/Documents/synergyAI/outputs/`.
4. The LLM reads those files and synthesizes the consolidated audit.

Because it relies on `run_skill_script`, every sub-skill must be **enabled** so it appears in the skill catalog. A disabled sub-skill is silently skipped and excluded from the score.

## What it orchestrates

| Sub-skill | Contributes |
|---|---|
| `ai-search-audit` | Technical, content quality, schema, GEO, on-page, asset hygiene, external signals — the bulk of the audit |
| `sitemap-audit` | XML sitemap presence, structure, quality gates |
| `image-audit` | Image SEO — alt text, lazy-loading, file weight |
| `hreflang-audit` | International SEO — run only for multilingual sites |

## SEO Health Score

Each sub-skill reports a 0-100 score; the consolidated score is their weighted average:

| Sub-skill | Monolingual | Multilingual |
|---|---|---|
| ai-search-audit | 60% | 50% |
| sitemap-audit | 20% | 20% |
| image-audit | 20% | 15% |
| hreflang-audit | — | 15% |

If a sub-skill fails or is skipped, its row is dropped and the remaining weights are re-normalized to 100%. Bands: 90-100 excellent · 75-89 good · 60-74 needs work · below 60 poor.

## Output

`seo-audit` writes no file of its own — the consolidated audit is presented in the chat response. The durable artifacts are the per-skill report files under `~/Documents/synergyAI/outputs/` (e.g. `<stem>-audit.md`, `batch-summary.md`), which the consolidated report links to.

## Limitations

- **Orchestrator only** — no own analysis; every finding traces to a sub-skill, and seo-audit inherits each sub-skill's limitations (static-HTML analysis, heuristic scoring, no JS rendering).
- **Sequential** — sub-skills run back-to-back; a wide crawl can take a few minutes. Parallel fan-out would require a workflow, not a skill.
- **No consolidated file in v1** — the merged audit lives in the chat response; a future `scripts/` step could emit a single `FULL-AUDIT-REPORT.md`.
- **No Core Web Vitals, GSC/GA4, or backlink data** — those need APIs/credentials not available in this environment.

## Related skills

- **`ai-search-audit`** — the per-page / crawl workhorse; run it directly for a single page or focused check.
- **`sitemap-audit`**, **`image-audit`**, **`hreflang-audit`** — the other audit inputs; each runs standalone.
- **`seo-plan`** — turns the audit's findings into a phased strategic roadmap.
- **`seo-content-brief`**, **`topic-cluster`** — act on the content gaps the audit surfaces.
