---
name: seo-audit
description: "Orchestrate a complete, whole-site SEO audit: run every other SEO skill in turn, then aggregate their results into one SEO Health Score (0-100) and a prioritized action plan. Use for: 'full SEO audit', 'complete SEO audit', 'audit my whole site', 'audit my website', 'website health check', 'full SEO check', 'comprehensive SEO analysis', 'run all the SEO checks', 'how healthy is my site'. Runs ai-search-audit (technical, content, schema, GEO, on-page), sitemap-audit (site structure), image-audit (image SEO), and — for multilingual sites — hreflang-audit, each via run_skill_script; reads every report file they write to ~/Documents/synergyAI/outputs/; combines them with weighted scoring into critical issues and quick wins. The single entry point for a full-site audit. For one page or a focused check, run the individual skills (ai-search-audit, etc.) directly."
fetches_urls: false
---

# seo-audit

> Whole-site SEO audit. Orchestrates every other SEO skill, then aggregates one SEO Health Score (0-100) and a prioritized action plan.

`seo-audit` is an **orchestrator skill** — it has no script of its own. It directs you (the LLM) to run the other SEO skills in turn and combine their findings. Mechanically: you call `run_skill_script` once per sub-skill; each sub-skill runs its own `scripts/audit.py` and writes its own report file; you read those files and synthesize the consolidated audit. There is no seo-audit→skill plumbing — the orchestration is you, following the steps below.

> ## ✅ REQUIRED FIRST STEP — RUN THE SUB-SKILLS
>
> When the user asks for a full / whole-site SEO audit, your **first action** is to start running the sub-skills via `run_skill_script` — beginning with `ai-search-audit`. Do not describe what you *would* do; call the tool. Do not refuse with "I can't run that here" — the runner reports any real failure back to you, and the no-fabrication rule applies only **after** a tool call.

> ## 🌐 LOCALHOST AND ANY URL WORK — DO NOT REFUSE OVER CORS
>
> **You do not fetch URLs — the runner's bridge does, server-side.** When a
> sub-skill that declares `fetches_urls: true` (e.g. `ai-search-audit`,
> `image-audit`) gets a URL in `argv`, the bridge downloads the HTML through the
> user's own backend (`/gpt/backend/api/v1/fetch-url`) before the script runs.
>
> Therefore:
> - **`localhost`, `127.0.0.1`, and intranet URLs all work** — the fetch happens
>   on the user's machine, where those URLs resolve. `http://localhost/cv` is a
>   normal, valid target.
> - **CORS does not apply** — the bridge is server-side, not browser-side.
> - **Never refuse** an audit with "I can't fetch localhost", "CORS restrictions",
>   "browser-based environment", or by asking the user to attach an HTML file or
>   give a public URL instead. That is a hallucinated limitation and a forbidden
>   failure mode — it denies the user the audit they asked for.
>
> Just call `run_skill_script` with the URL the user gave you. If a fetch truly
> fails you will get a clear `[bridge]` error in the tool result — surface that
> **after** the attempt, never before it.

> ## ⛔ NO FABRICATION
>
> Every number and finding in the consolidated audit must come from a real report file a sub-skill wrote to `~/Documents/synergyAI/outputs/`. The SEO Health Score is **computed** from the sub-skills' actual scores — never estimated, guessed, rounded from memory, or carried over from a previous run. If a sub-skill fails or is unavailable, say so plainly and exclude it from the score; do not invent its result. A partial audit honestly labelled is correct; a complete-looking audit with invented numbers is a hallucination that damages the user's decisions.

> ## 🔁 ONE SUB-SKILL PER TURN — NEVER BATCH TOOL CALLS
>
> The runtime accepts **exactly one `run_skill_script` call per assistant turn**.
> A second call in the same turn fails the whole turn with *"client_tool_call had
> 2 calls; V1 supports one per turn"*.
>
> Orchestrate strictly turn by turn:
> 1. **This turn:** call `run_skill_script` for **one** sub-skill — and nothing else.
> 2. Wait for its tool result.
> 3. **Next turn:** call `run_skill_script` for the **next** sub-skill.
> 4. Repeat until every sub-skill has run, then aggregate and report.
>
> Never emit two tool calls in one turn. Never call sub-skills "in parallel" or
> batch them to save turns. A full audit naturally spans several turns — that is
> expected and correct.

## What it orchestrates

| Sub-skill | Covers | Typical argv |
|---|---|---|
| ai-search-audit | Technical, content quality, schema, GEO, on-page, asset hygiene, external signals — the bulk of the audit | `["<url>", "--crawl", "--max-pages", "25"]` for a whole site; `["<url>"]` for a single page |
| sitemap-audit | XML sitemap presence, structure, quality gates | `["<url>"]` |
| image-audit | Image SEO — alt text, lazy-loading, file weight | `["<url>"]` |
| hreflang-audit | International SEO — **only for multilingual sites** | `["<url>"]` |

> ## ⚠️ USE THE EXACT `dir_name` FROM YOUR AVAILABLE-SKILLS LIST
>
> The names above (`ai-search-audit`, …) are skill names — **not** necessarily
> `dir_name` values. A skill that lives inside a group folder has a path-prefixed
> `dir_name`. As currently installed, these SEO skills sit in the `SEO` group
> folder, so their real `dir_name` values are **`SEO/ai-search-audit`**,
> **`SEO/sitemap-audit`**, **`SEO/image-audit`**, **`SEO/hreflang-audit`**.
>
> For each sub-skill, take the entry from your available-skills list whose
> `dir_name` equals the skill name **or ends with `/<skill name>`**, and pass that
> `dir_name` to `run_skill_script` **verbatim — prefix included**. Never strip a
> prefix; never invent a bare name (`ai-search-audit` alone will fail with
> *"Skill folder not found"*). If no entry matches, that skill is disabled — skip
> it (Process step 4).

Invoke each via `run_skill_script` with that `dir_name`, the script `scripts/audit.py`, and the argv above — e.g. with the skills installed under the `SEO` folder:
`{ "dir_name": "SEO/ai-search-audit", "script": "scripts/audit.py", "argv": ["<url>", "--crawl", "--max-pages", "25"] }`

Each sub-skill's own `SKILL.md` is the source of truth for its exact flags — consult it if an invocation is rejected.

## Process

1. **Note the site type.** Glance at the URL / homepage for an apparent business type (SaaS, e-commerce, local service, publisher, agency). Context for the report only — do not block on it.
2. **Run the sub-skills — exactly one per turn, in this order.** Each turn: make a single `run_skill_script` call, wait for its tool result, then continue on the next turn (see 🔁 above — never batch):
   1. `ai-search-audit` — `--crawl --max-pages 25` for a whole site; single mode (`["<url>"]`) for one page.
   2. `sitemap-audit`
   3. `image-audit`
   4. `hreflang-audit` — **only if** the site looks multilingual (language switcher, `/fr/`·`/de/` path segments, visible `hreflang` tags).
3. **Read every report file** each sub-skill wrote under `~/Documents/synergyAI/outputs/`. Quote scores and finding titles verbatim — see ⛔ NO FABRICATION.
4. **If a sub-skill fails or is not enabled** — note it, exclude it from the score, continue with the rest. (A sub-skill that is disabled is silently absent from the `run_skill_script` catalog; treat a "skill not found" error as "skipped, scored without it".)
5. **Compute the SEO Health Score** — see the rubric below.
6. **Present the consolidated audit** in chat — see Report structure.

## SEO Health Score

Each sub-skill reports its own 0-100 score. The consolidated SEO Health Score is their weighted average:

| Sub-skill | Weight — monolingual site | Weight — multilingual site |
|---|---|---|
| ai-search-audit | 60% | 50% |
| sitemap-audit | 20% | 20% |
| image-audit | 20% | 15% |
| hreflang-audit | — (not run) | 15% |

If a sub-skill failed or was skipped, drop its row and re-normalize the remaining weights to total 100%. Always state which sub-skills contributed to the score.

Bands: **90-100** excellent · **75-89** good · **60-74** needs work · **below 60** poor.

## Report structure

`seo-audit` writes no file of its own — the per-skill report files under `~/Documents/synergyAI/outputs/` are the durable artifacts. Present the consolidated audit in the chat response:

### Executive summary
- SEO Health Score (0-100) + band, and which sub-skills contributed
- Apparent business type
- Top 5 critical issues across all skills
- Top 5 quick wins

### Findings by area
One short paragraph per sub-skill: its score and its 2-3 most important findings, each quoting that sub-skill's report.

### Prioritized action plan
All findings, grouped by priority:
- **Critical** — blocks indexing or AI citation; fix immediately
- **High** — significantly hurts rankings; fix within a week
- **Medium** — optimization opportunity; fix within a month
- **Low** — polish; backlog

### Report files
List the path of each sub-skill's report under `~/Documents/synergyAI/outputs/` so the user can open the full detail.

## What this skill does NOT do

- **No own analysis.** Every finding traces back to a sub-skill's report.
- **No parallelism.** Sub-skills run one after another — this is an instruction-level orchestrator. For parallel fan-out, a workflow is the right tool.
- **No consolidated file.** v1 presents the merged audit in chat; the durable artifacts are the per-skill report files. (A future `scripts/` step could write a single `FULL-AUDIT-REPORT.md`.)
- **No Core Web Vitals / PageSpeed, no GSC / GA4, no backlinks.** Those need APIs or credentials not wired into this environment.
- **No PDF report.**

## Limitations

- **Sequential and can be slow.** A full audit runs several skills back-to-back; a wide crawl can take a few minutes.
- **Only as good as the sub-skills.** seo-audit inherits their limitations — static-HTML analysis, heuristic scoring, no JavaScript rendering.
- **Sub-skills must be enabled** to be reachable via `run_skill_script`. seo-audit notes any gap and scores without the missing skill.

## Related skills

- `ai-search-audit` — the per-page / crawl workhorse; run it directly for a single page.
- `sitemap-audit`, `image-audit`, `hreflang-audit` — the other audit inputs; each runs standalone for a focused check.
- `seo-plan` — turn the audit's findings into a strategic roadmap.
- `seo-content-brief`, `topic-cluster` — act on the content gaps the audit surfaces.
