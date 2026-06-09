---
name: geo-report-pdf
description: "Generate a professional, client-ready PDF from completed GEO audit data using ReportLab — cover page with score gauge, category bar charts, AI-platform readiness visuals, color-coded crawler-access table, severity-coded findings, and a prioritized action plan. Use for: 'GEO PDF', 'PDF report', 'client PDF', 'export the audit as a PDF', 'professional PDF report', 'turn the audit into a PDF'. You assemble the audit results into one JSON object and run scripts/generate_pdf_report.py over it; the script renders the PDF to /outputs/."
dependencies: [reportlab]
fetches_urls: false
---

# geo-report-pdf

Turn a completed GEO audit into a polished, client-ready **PDF**. The script
does all the rendering — your only job is to **assemble the audit data into one
JSON object** and hand it to the script in a single `run_skill_script` call.

> ## ✅ HOW TO RUN IT — one `run_skill_script` call (Pyodide, not a shell)
>
> This is a SynergyAI/Pyodide skill. There is **no shell, no `pip install`, no
> `python3`, and no `~/.claude/` path** — ignore any such phrasing. Run it the
> same way as every other skill:
>
> 1. **Build the audit JSON** (schema below) from the data you already have in
>    this conversation — the `GEO-CLIENT-REPORT.md` and the per-dimension
>    extracts produced by the audit. Assemble it from context; do not try to
>    read files from a shell.
> 2. **Pass that JSON via `input_files`**, and name the output with an
>    **absolute `/outputs/` path** so the PDF persists to the user's disk.
>
> ```json
> {
>   "dir_name": "GEO/geo-report-pdf",
>   "script": "scripts/generate_pdf_report.py",
>   "input_files": { "/scratch/geo-audit-data.json": "<the audit JSON, as a string>" },
>   "argv": ["/scratch/geo-audit-data.json", "/outputs/GEO-REPORT-<brand>.pdf"],
>   "read_outputs": ["/outputs/GEO-REPORT-<brand>.pdf"]
> }
> ```
>
> - `argv[0]` = the JSON path you just wrote with `input_files`.
> - `argv[1]` = the **absolute** output path under `/outputs/` (e.g.
>   `/outputs/GEO-REPORT-didierphmartin.pdf`). A bare filename would write into
>   the skill folder and never reach the user's disk — always use `/outputs/…`.
> - **`read_outputs` = the PDF path.** This is the ONE GEO skill where you DO
>   set `read_outputs`: the deliverable is a **binary file** (not stdout), and
>   reading it back confirms the PDF was written and reports its size. The tool
>   result will show it as `[binary file, N bytes]` — that is **success**;
>   report the path and size to the user. Do NOT re-run the skill on seeing the
>   binary descriptor.
> - **`reportlab` installs automatically** (declared in `dependencies` above).
>   You do not install anything.

## Step 1 — assemble the audit JSON

Collect every score, finding, and recommendation from the completed audit into
this exact schema (omit a key only if you genuinely lack the data):

```json
{
    "url": "https://example.com",
    "brand_name": "Example Company",
    "date": "2026-02-18",
    "geo_score": 65,
    "scores": {
        "ai_citability": 62,
        "brand_authority": 78,
        "content_eeat": 74,
        "technical": 72,
        "schema": 45,
        "platform_optimization": 59
    },
    "platforms": {
        "Google AI Overviews": 68,
        "ChatGPT": 62,
        "Perplexity": 55,
        "Gemini": 60,
        "Bing Copilot": 50
    },
    "executive_summary": "A 4-6 sentence summary of the audit findings...",
    "findings": [
        {
            "severity": "critical",
            "title": "Finding Title",
            "description": "Description of the finding and its impact."
        }
    ],
    "quick_wins": [
        "Action item 1",
        "Action item 2"
    ],
    "medium_term": [
        "Action item 1",
        "Action item 2"
    ],
    "strategic": [
        "Action item 1",
        "Action item 2"
    ],
    "crawler_access": {
        "GPTBot": {"platform": "ChatGPT", "status": "Allowed", "recommendation": "Keep allowed"},
        "ClaudeBot": {"platform": "Claude", "status": "Blocked", "recommendation": "Unblock for visibility"}
    }
}
```

Where to source each field from a completed audit:

- **geo_score / scores** — the composite + per-dimension scores from
  `GEO-CLIENT-REPORT.md` (or the geo-report output).
- **platforms** — the AI-platform readiness scores (Google AIO, ChatGPT,
  Perplexity, Gemini, Bing Copilot) from the platform-optimizer dimension.
- **crawler_access** — the crawler status table from `geo-crawlers`
  (Allowed/Blocked per GPTBot, ClaudeBot, etc.).
- **findings / quick_wins / medium_term / strategic / executive_summary** —
  your synthesized analysis from the audit.

## Step 2 — run the skill

Make the single `run_skill_script` call shown in the callout above. The script
produces a professional PDF with:

- **Cover Page** — brand, URL, date, overall GEO score with a visual gauge
- **Executive Summary** — key findings and top recommendations
- **Score Breakdown** — table + bar chart of all 6 scoring categories
- **AI Platform Readiness** — horizontal bar chart per platform
- **AI Crawler Access** — color-coded table (green = allowed, red = blocked)
- **Key Findings** — severity-coded list (critical/high/medium/low)
- **Prioritized Action Plan** — quick wins, medium-term, strategic
- **Appendix** — methodology, data sources, glossary

## Step 3 — report back

Tell the user the PDF's `/outputs/` path and its size (from the
`[binary file, N bytes]` result). On the user's disk it lands under
`~/Documents/synergyAI/outputs/`.

## If there is no audit data yet

If this conversation has no completed audit, run the `geo-audit` skill first
(or ask the user to run it), then come back and generate the PDF.

## Notes

- US Letter (8.5"×11"). Palette: navy `#1a1a2e`, blue `#0f3460`, coral
  `#e94560`, green `#00b894`. Each page has a header line, page number,
  "Confidential" watermark, and generation date.
- Score gauges use traffic-light colors: green (80+), blue (60–79),
  yellow (40–59), red (below 40).
- The PDF is binary — it is written to `/outputs/…` and synced to the user's
  disk by the runner; never embed its bytes in your reply.
