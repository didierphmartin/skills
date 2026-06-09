---
name: geo-compare
description: "Monthly delta / progress tracking for a single site — compare two GEO audit reports (baseline vs current) and produce a 'here's your progress' client report. Use for: 'monthly report', 'compare audits', 'progress report', 'delta report', 'how much did we improve', 'show client progress', 'monthly check-in', 'baseline vs current', 'GEO improvement report'. Pipeline: locate two audit/report files in ~/Documents/synergyAI/outputs/ (the place every geo-* skill writes its reports) -> regex-extract score markers and crawler statuses from both -> compute numeric deltas + crawler-status changes -> emit a structured delta table to stdout. The LLM writes the prose monthly progress report from the deltas, framing improvements as wins and declines as 'newly discovered opportunities'."
dependencies: []
fetches_urls: false
---

# geo-compare

The most powerful retention tool for a GEO agency: show clients **exactly**
what improved since last month. Every point gained is proof of value.

This skill compares two GEO audit reports for the same site (baseline vs.
current) and produces the monthly progress report.

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks for a monthly / progress / delta report comparing two
> audits, your **first action** is `run_skill_script` with `dir_name:
> geo-compare` and `scripts/compare_audits.py`. The script reads both report
> files, extracts every score it can match, and computes the deltas.
>
> If the tool is genuinely missing, say so **after** attempting the call.
>
> ## ⛔ NO FABRICATION
>
> The script prints the **extracted scores + deltas to stdout** —
> `run_skill_script` returns it to you.
>
> - Every "baseline X, current Y, delta Z" line you cite must come from the
>   script. Never invent a baseline number, a delta, or a status change.
> - The script reports which file is treated as baseline and which as current;
>   confirm that matches what the user expected.
> - If the script reports a score marker as "not found in baseline" or "not
>   found in current", say so — do not fill in a guess.

## Invocation — exact argv shapes

```json
// Two specific report files (filenames in the outputs/ folder, or absolute paths)
{ "dir_name": "geo-compare", "script": "scripts/compare_audits.py", "argv": ["GEO-CRAWLER-ACCESS-baseline.md", "GEO-CRAWLER-ACCESS.md"] }
```

```json
// Single argument: lists every report file in outputs/ so the user can pick
{ "argv": ["--list"] }
```

```json
// Single domain/substring: lists files matching that substring, sorted by mtime
{ "argv": ["--list", "stripe"] }
```

**Resolution order for each path argument:**
1. If it's an absolute path → read directly.
2. Else look in `~/Documents/synergyAI/outputs/` (`/outputs/` in Pyodide).

The **first** path is baseline, the **second** is current. If the user has a
naming convention with dates (e.g. `GEO-CRAWLER-ACCESS-2026-03.md`), the
script preserves that.

## What the script extracts

The script regex-scans both files for common GEO score markers and crawler
statuses. It is agnostic about which specific skill wrote the report —
geo-crawlers, geo-citability, geo-content, geo-technical, geo-platform-optimizer,
geo-llmstxt, geo-schema, or a hand-written summary. Markers it looks for:

**Numeric scores:**
- `AI Visibility Score: N/100` (geo-crawlers)
- `Citability Score: N/100` (geo-citability)
- `Content Score: N/100` (geo-content)
- `Technical Score: N/100` (geo-technical, hand-written)
- `Schema Score: N/100` / `Overall Score: N/100` / `GEO Score: N/100`
- `Tier 1 accessible: N/5`, `Tier 2 accessible: N/5`
- `Word count: N`
- `H1 count: N`
- `Total entries: N`
- `JSON-LD entities parsed: N`
- `Total unique sameAs URLs: N`
- Any generic `<Label>: N/100` or `<Label>: N/M` pattern

**Crawler statuses** (14 known AI crawlers): GPTBot, OAI-SearchBot,
ChatGPT-User, ClaudeBot, PerplexityBot, Google-Extended, GoogleOther,
Applebot-Extended, Amazonbot, FacebookBot, CCBot, anthropic-ai, Bytespider,
cohere-ai. Status extracted from per-crawler table rows or `Crawler: Status`
lines. Statuses recognized: `Allowed`, `Blocked`, `Not Mentioned`.

**Metadata:**
- Report timestamp (lines containing "Generated:" / "Fetched:")
- Domain / URL (lines starting with "**URL:**" / "**Site:**" / "**Domain:**")

## Output

The script emits, to stdout:

1. **File summary** — path, mtime, parsed timestamp, detected domain for each.
2. **Numeric deltas table** — every score that appears in BOTH files, with
   baseline / current / Δ / trend symbol.
3. **Numeric scores in only one file** — listed separately so the LLM can
   note them as "new tracking" / "no longer reported".
4. **Crawler status changes** — only crawlers whose status differs between
   the two reports; unchanged crawlers summarized as a count.
5. **Raw matched lines** for spot-checking.

## Trend interpretation

| Delta | Symbol | Meaning |
|---|---|---|
| +5 or more | ▲▲ | Strong improvement |
| +1 to +4 | ▲ | Improvement |
| 0 | ── | No change |
| -1 to -4 | ▼ | Slight decline |
| -5 or more | ▼▼ | Significant decline — discuss with client |

A decline is **not** necessarily bad — it can mean the current audit
discovered issues the baseline missed. Frame declines as "newly discovered
opportunities" in the client-facing report.

## Monthly progress report — LLM template

After the script returns deltas, produce **`GEO-MONTHLY-PROGRESS.md`** with:

```markdown
# GEO Monthly Progress Report — [Domain]
**Reporting period:** [baseline date] → [current date]

## Executive Summary
[2-3 sentences: what improved, what's the trend, what to focus on next month]

## Score Progress
[Table of every numeric score with baseline / current / Δ / trend, from the
script. Highlight the headline number — often "AI Visibility Score" or "GEO
Score".]

## AI Crawler Access Changes
[Only the crawlers whose status changed, from the script. Frame each as a win
or an opportunity.]

## This Month's Wins
[3-5 specific, tangible improvements, derived from the deltas]

## New Issues Discovered
[Score declines or crawlers that went from Allowed to Blocked — framed
constructively]

## Next Month Focus
[The 1-3 highest-leverage actions remaining, based on the still-low scores]
```

If the user wants a PDF version, call **`geo-report-pdf`** (when that skill
is available) — geo-compare produces the Markdown report; PDF generation is
a separate step.

## Output

The script prints the **deltas + matched lines to stdout** — that is your
report-writing input. It also writes `GEO-COMPARE-DELTA.json` to
`~/Documents/synergyAI/outputs/` (`/outputs/` in Pyodide) so subsequent skills
(e.g. `geo-report-pdf`) can pick up the structured deltas.

## Pyodide notes

The script does **no network I/O** — it only reads files from the outputs
folder. `fetches_urls: false` because there is nothing to fetch.
`dependencies: []` because everything is stdlib (regex + filesystem).

## Limitations

- **Heuristic regex matching.** A hand-written report that doesn't use
  standard score-marker phrasing may not be parsed; the script reports what
  it found vs. what's missing so the LLM knows the coverage.
- **Crawler statuses only match the 14 named in the geo-crawlers skill.**
  Custom crawler rows in a hand-written report would be skipped.
- **No semantic understanding of action-item status.** "✅ Done" vs.
  "🔄 In Progress" is for the LLM to track — the script doesn't extract
  action-item lists.
- **No baseline storage.** The user supplies both files explicitly; the
  skill doesn't maintain a database of "last month's audit per domain".
