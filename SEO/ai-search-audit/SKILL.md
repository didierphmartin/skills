---
name: ai-search-audit
description: "Audit an HTML page (file or URL) for SEO and AI-search citation readiness. Use for any general SEO request — covers most of what people mean by 'SEO audit'. Triggers: 'SEO audit', 'audit this site/page', 'technical SEO', 'is my page LLM-friendly', 'GEO audit', 'audit for ChatGPT/Gemini/Perplexity', 'check JSON-LD', 'E-E-A-T audit', 'readability', 'AI crawlers blocked' (GPTBot/ClaudeBot), 'mobile viewport', 'JS rendering', 'llms.txt', 'security headers', 'content freshness'. Seven passes: cognitive structure; metadata (JSON-LD with deprecated-type + required-property validation, OG, Twitter); content quality (Flesch, author byline, freshness, link ratios); GEO (passage citability, definition patterns, answer-first headings); technical (URL structure, mobile viewport, JS rendering detection, IndexNow); asset hygiene; external signals (AI-crawler robots.txt, /llms.txt, security headers). Output: scored Markdown report. Complements page-speed (Lighthouse) and accessibility (axe/WAVE) audits."
dependencies: [beautifulsoup4]
fetches_urls: true
---

# ai-search-audit

Score how well an HTML page is set up to be cited by AI search engines and LLM overviews — Gemini AI Overviews, ChatGPT Search, Perplexity, Bing Copilot, and similar systems that select and quote pages in their answers.

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks for an audit, your **first action** is to call `run_skill_script` with this skill's `dir_name` (`ai-search-audit`) and `scripts/audit.py`. Do this regardless of whether you think the tool might fail — the runner will report any failure back to you, and the no-fabrication rules below apply to **after** the tool call, never before.
>
> Do NOT refuse with phrases like "the tool isn't available in this session" or "I cannot run the script." If the tool is truly missing from your tool list, surface that **after** attempting the call (your runtime will reject it with a clear error). Preemptive refusal looks the same to the user as a hallucinated audit — both deny them the report they asked for.
>
> ## 🌐 YOU DO NOT NEED NETWORK ACCESS — THE BRIDGE FETCHES THE URL FOR YOU
>
> **You (the LLM) do NOT fetch URLs. The runner does.** When you put a URL in `argv` (e.g. `argv: ["http://localhost/cv/"]`), the runner's bridge intercepts it server-side, downloads the HTML through the user's own backend (`/gpt/backend/api/v1/fetch-url`), writes it to `/tmp/prefetched_<slug>.html`, replaces the URL in argv with that path, and appends `--source <original-url>`. The script then reads the local file — no in-Python network call is ever attempted.
>
> Consequences for you:
>
> - **`localhost`, `127.0.0.1`, internal/intranet URLs all work.** The fetch happens on the user's machine where those URLs resolve. Do not refuse "I can't access localhost." You aren't accessing it; the bridge is, and it can.
> - **CORS doesn't apply.** The bridge is server-side, not browser-side.
> - **Authentication-required URLs may fail** at the bridge — that's the one case where you legitimately can't proceed, and you'll get a clear `[bridge]` error in the tool_result. Surface that after the fact, do not refuse before trying.
>
> Refusing because of perceived URL access limits is a known failure mode and is **explicitly forbidden** by this skill. Just call the tool.
>
> ## ⛔ AFTER THE TOOL CALL — NO FABRICATION
>
> **If the tool call failed and you cannot READ an actual report file written by `scripts/audit.py` to `~/Documents/synergyAI/outputs/`, you have NO audit result to share.** This rule overrides everything else in this skill — but only applies **after** you have already attempted the tool call.
>
> When the script errors, gets blocked by CORS in Pyodide, fails to write a file, or does not print `Wrote /path/...-audit.md` to stderr, your **one and only valid response** is to surface the exact error to the user and ask how they want to proceed.
>
> **Forbidden in that situation — fabricating any of the following is a hallucination, not a "best-effort report":**
> - A numeric score, percentage, or grade (e.g. `Score : 89/100`, `Grade A`, `54/100`).
> - A "what works well" list, a strengths list, a comparison table between previous and new audits.
> - Phrases like *"Excellent progrès !"*, *"Nouveau score :"*, *"+35 points depuis la dernière analyse"*, *"✅ Résolu"*, *"Voici les résultats"*, or any equivalent that implies an audit was performed.
> - A report path under `/home/pyodide/...` — that path is inside the browser WASM sandbox, the user cannot read it, and citing it is a tell-tale sign that no real audit ran. The real path lives under `~/Documents/synergyAI/outputs/` on the host filesystem.
> - Narrating fictitious "retry" attempts ("L'audit a échoué… Je relance…") and then producing a polished score anyway. If you narrated failures, finish on the failure, not on a fabricated success.
>
> **Required for every audit:**
> 1. Invoke `scripts/audit.py` (or `audit.audit_html_to_markdown(html, source=url)` in Pyodide with pre-fetched HTML).
> 2. Locate the literal line `Wrote /path/...-audit.md` in the script's stderr — or capture the returned Markdown string in Pyodide.
> 3. Quote that path back in your response so the user can `cat` the file.
> 4. READ the file. Every score, grade, finding title, and "strengths" item in your response must appear verbatim in that file.
> 5. If step 1, 2, 3, or 4 fails, send an error message, not an audit report. Hallucinated audit results actively damage the user's content decisions and erode trust in the tool.

## When this skill applies

Trigger this skill any time the user wants to know whether a web page is set up to be picked up and cited by AI-driven answer engines. Common framings:

- "Is my page LLM-friendly?" / "Will Gemini cite this?"
- "GEO audit" / "Generative engine optimization"
- "Audit my structured data."
- "Check this page for ChatGPT/Perplexity readiness."
- "How do I get cited by AI search?"
- "Score my article for AI overviews."

If the user wants traditional Google SEO (keywords, backlinks, site speed), use a Lighthouse-style tool instead — the optimization profile for AI search is genuinely different. If they want HTML-to-Markdown for reading, use the `html-to-markdown` skill.

## Hard rule: no audit without running the script

**Never produce an audit report — score, grade, findings, strengths, or "what's working well" lists — from a checklist, prior knowledge, or inference. The only valid source of audit content is the report file written by `scripts/audit.py`.**

This means:

1. **Always invoke `scripts/audit.py`.** Do not skip it because the page "looks fine" or because you've audited similar pages before.
2. **After the script runs, Read the actual output file** at `~/Documents/synergyAI/outputs/<stem>-audit.md` and quote the score line and finding titles verbatim. Do not paraphrase the score (e.g. don't write "≈90/100" when the file says 93).
3. **If the script fails** (non-zero exit, fetch error, parse error, no output file): say so plainly, surface the error, and stop. Do not produce a "best-effort" report.
4. **Do not invent the report path.** The path is whatever the script printed to stderr (`Wrote /path/to/<stem>-audit.md`). Quote it from the script's output, do not construct it from the URL.
5. **If the user pastes an audit summary from another tool** (e.g. an LLM-generated checklist), re-run `scripts/audit.py` before acting on it. Cross-reference each claim against the real report — checklist-style summaries from other LLMs frequently invent findings (e.g. claiming `og:image` is missing when it is present).

A useful self-check before sending any audit response: can you point to a specific line in a file at `~/Documents/synergyAI/outputs/` for every claim you are making? If not, run the script.

## How to use the skill

The audit is handled by `scripts/audit.py`. Three modes:

```bash
# Single — one file or one URL
python scripts/audit.py page.html
python scripts/audit.py https://example.com/article

# Crawl — start from a URL, follow same-host internal links, audit each
python scripts/audit.py https://myblog.com/ --crawl --max-pages 25

# Batch list — explicit list of URLs/files in a text file
python scripts/audit.py --batch-file urls.txt
```

All output lands in `~/Documents/synergyAI/outputs/`. Single mode writes one `<stem>-audit.md`. Batch and crawl modes write per-page reports **plus** a `batch-summary.md` with a comparison table and the most common issues across the batch.

### Common invocations

**Audit a single live page** (the typical first request):
```bash
python scripts/audit.py https://myblog.com/2024/my-article
```

**Audit a whole site** by crawling internal links:
```bash
python scripts/audit.py https://myblog.com/ --crawl --max-pages 50
```
The crawl starts at the given URL, BFS-walks same-host `<a href>` links (dropping fragments, skipping non-HTML extensions like `.pdf`/`.jpg`), audits each, and writes a comparison summary worst-page-first. Use `--max-pages` to cap and `--crawl-delay` to slow the discovery phase if the host rate-limits.

**Compare a specific list of pages** (e.g. yours vs competitors):
```bash
cat > pages.txt << EOF
# Our pages
https://myblog.com/2024/post-a
https://myblog.com/2024/post-b
# Competitor pages
https://competitor.com/2024/their-take
EOF
python scripts/audit.py --batch-file pages.txt
```
Lines starting with `#` and blank lines are ignored. Entries can mix file paths and URLs.

**Audit with explicit article container** (single mode only, when auto-detection picks up nav/footer):
```bash
python scripts/audit.py https://example.com/article --main-selector "article"
```

**Print single-page report to stdout** (for piping):
```bash
python scripts/audit.py page.html --stdout
```

### Pyodide / browser-Python environments

If the script is running under Pyodide (Python compiled to WebAssembly, in-browser), URL fetching via `urllib` is impossible (no socket access, no `ssl` module support) and `pyodide.http.open_url()` is restricted by browser CORS — most production sites won't permit it.

**Important:** `ssl` and `urllib.request` are lazy-imported (only loaded when the CPython URL-fetch path is invoked). This means `import audit` works fine in Pyodide — you can call `audit.audit_html_to_markdown(html, source)` with no network access whatsoever. **Do not try to fetch URLs from inside Python in a Pyodide environment** — fetch them outside.

**The fix is to fetch the HTML outside Python** (server-side: PHP, Node, etc.) and pass the content to the script. Two options:

**Option A — `--stdin` from the CLI:**
```bash
# PHP/Node/curl fetches, pipes HTML to the script
curl -sSL --compressed https://finmind.org \
  | python3 audit.py --stdin --source https://finmind.org
```

**Option B — call the Python API directly from Pyodide:**
```python
import audit
# `html` is the full document fetched by your PHP backend
md_report = audit.audit_html_to_markdown(html, source="https://finmind.org")
# md_report is the rendered Markdown string — display it, save it, return it.
```

The frontend pattern for this skill in your environment:
1. User asks the LLM to audit `finmind.org`.
2. Frontend's PHP layer fetches the URL (no CORS limit server-side).
3. Frontend passes the HTML to Pyodide as a Python global.
4. Pyodide calls `audit.audit_html_to_markdown(html, source=url)` and returns the report.

If the script's `_fetch_url` is called in Pyodide and CORS blocks it, the script raises a `RuntimeError` with a clear actionable message — that's the signal that the frontend needs to do server-side fetching.

**Critical behavior when running in Pyodide:**

1. **The bridge auto-fetches URL arguments.** Skills that declare `fetches_urls: true` in SKILL.md (this one does) have their URL argv entries intercepted by `pyodide-runner.js::prefetchUrlArgs`: each URL is POSTed to `/gpt/backend/api/v1/fetch-url`, the returned HTML is written to `/tmp/prefetched_<slug>.html`, the URL in argv is replaced with that path, `--source <original-url>` is appended, and `-o /outputs/<url-stem>-audit.md` is also appended (so the report lands in the host-mounted `/outputs/` folder, NOT in MEMFS at `/home/pyodide/Documents/synergyAI/outputs/` which the user cannot read). You do not need to do any of this manually — just invoke `python scripts/audit.py <URL>` normally.
2. **Where the report lands.** When the bridge has injected `-o /outputs/<stem>-audit.md`, the script's stderr will print `Wrote /outputs/<stem>-audit.md`. That Pyodide path is the host-side `~/Documents/synergyAI/outputs/<stem>-audit.md` (mounted via FSA). Both paths refer to the same file; quote the host path back to the user since that's where they can `cat` it.
3. **If the bridge errors** — fetch failed, backend returned non-2xx, auth missing — the runner returns `{ exitCode: 2, stderr: '[bridge] ...' }`. Surface that `[bridge]` message verbatim and stop. Do not retry, do not produce a score from inference.
4. **If `audit.py` itself errors after a successful fetch** — bad selector, parse failure — surface the script's stderr verbatim and stop. Same rule.
5. **Do not narrate retry attempts** (`"Je relance avec un autre mode…"`, `"Essayons avec --batch-file…"`) and then conjure a score. Every retry produces the same error. Stop after the first failure.
6. **Do not claim a report was written to `/home/pyodide/Documents/synergyAI/outputs/...`.** With the bridge's `-o /outputs/...` injection in place, the script CANNOT write to that MEMFS path anymore — if you see that path in the output, the bridge is misconfigured. The valid Pyodide path is `/outputs/...` (mounted) which maps to the host's `~/Documents/synergyAI/outputs/...`.

### Flag reference

| Flag | Purpose |
|---|---|
| `input` | Path to .html file OR a URL OR a bare domain. Required unless `--batch-file` is given. Accepts `https://example.com/page`, `example.com`, `www.example.com/article`, or `./page.html` interchangeably — bare domains auto-prepend `https://`. Live fetching uses a real browser User-Agent and decompresses gzip responses; bot-blocking sites may still 403. |
| `-o`, `--output PATH` | Output `.md` file (single mode only). Default: `~/Documents/synergyAI/outputs/<stem>-audit.md`. |
| `--main-selector CSS` | CSS selector for the article container. Auto-detects `<main>`/`<article>`/`[role='main']`/`#content`/etc. when omitted. Applied to every page in batch/crawl modes too — use only when all targets share the same selector. |
| `--stdout` | Single mode only: print the report to stdout instead of writing a file. |
| `--stdin` | Read HTML content from stdin. Required for Pyodide environments where the frontend pre-fetches the page. Pair with `--source URL` so the report shows what was audited. |
| `--source URL` | Source label for the report when reading from `--stdin`. Should be the original URL the HTML came from. |
| `--crawl` | Treat the URL input as a starting point. Follow same-host internal links and audit each. |
| `--max-pages N` | Crawl mode: cap at N pages (default: 25). Includes the start page. |
| `--crawl-delay SEC` | Crawl mode: seconds between fetches during the discovery phase (default: 0.5). |
| `--batch-file PATH` | Read URLs/file paths from a text file (one per line, `#` for comments). Mutually exclusive with `--crawl`. |
| `--summary-output PATH` | Batch/crawl mode: where to write the summary report (default: `~/Documents/synergyAI/outputs/batch-summary.md`). |
| `--workers N` | Batch/crawl mode: parallel audit workers (default: 8). |

### What the batch summary report contains

- **Origin** (which file or crawl URL produced the batch) and counts (OK / failed).
- **Average score** and **average critical findings** across the batch.
- **Pages ranked by score, worst first** — table with score, grade, critical count, recommended count, top issue, URL.
- **Most common issues** across the batch (e.g. "5× pages: No JSON-LD structured data") — this is the highest-leverage list, because fixing one issue across many pages is usually faster than fixing many issues on one page.
- **Per-page report links** so the user can drill into any page's full report.
- **Failed-to-audit list** for any pages that errored (404, timeout, parse error).

### Working with the user's input

1. **They give you a URL**: just pass it directly. Show them the score line printed to stderr first (e.g. `score: 66/100, grade D`) — it tells them at a glance whether the report has good news.
2. **They give you an HTML file**: same.
3. **They want to audit a whole site**: use `--crawl`. Default `--max-pages 25` is a sensible first pass; bump up if their site is bigger. For very large sites suggest doing a small crawl first to confirm auto-detection picks up the right article container before running a wide crawl.
4. **They give you a list of URLs to compare** (their pages, competitors, multiple drafts): use `--batch-file`. The summary's "most common issues" section is usually the most actionable output.
5. **They typed a bare domain** (`example.com` instead of `https://example.com`): just pass it directly. The script auto-prepends `https://` and audits. Don't bother asking them to add the protocol.
6. **The site 403s** (bot-blocked Cloudflare etc.): explain that the audit can't read the page. Offer to audit a saved copy instead — they can View → Save Page As in the browser.
6. **The auto-detected article root looks wrong** (lots of nav/footer findings in the cognitive section): re-run with `--main-selector` once they identify the wrapper. Note: in batch/crawl modes the same selector is applied to every page, so only use it when all targets share the structure.

### After running the script

**Single mode:**
- **Lead with the score** (printed to stderr). If it's <70, the page has work to do.
- **Surface the critical findings first** — these are blocking issues.
- **Don't read the whole report verbatim**: summarize the top 3 issues and tell the user the full report is at the output path.
- **Offer to fix** if the user wants — most fixes (JSON-LD, OG tags, meta description) are 5-line additions to `<head>`.

**Batch / crawl mode:**
- **Lead with the average score** and how many pages were audited (printed to stderr).
- **Surface the "most common issues" list from the summary** — this is the highest-leverage output. A site with "20× pages: No JSON-LD structured data" can be fixed once at the template level.
- **Point out the worst page** (top of the ranked table) as a starting place for individual remediation.
- **Mention the summary path** so the user knows where the comparison report is, plus the per-page reports if they want to drill in.

## What the audit checks

Seven categories, each with strict thresholds. Findings are graded by severity:

| Severity | Pts deducted | When to use |
|---|---|---|
| **critical** | −10 | Missing a load-bearing AI-search signal (no JSON-LD, no meta description, noindex, AI search crawlers blocked). The page is effectively invisible to that signal channel. |
| **recommended** | −3 | Sub-optimal structure that measurably reduces citation rate (weak leads, low citation density, thin sections, missing author byline, no llms.txt, deprecated schema). |
| **nice-to-have** | −1 | Minor polish (no `og:image`, missing `lang` attr, slightly long title, missing Twitter Card, stale content). |

Score starts at 100, deductions stacked. See `references/thresholds.md` for the full numeric thresholds and the rationale behind each.

### 1. Cognitive structure

Whether the page reads as a coherent argument an LLM can quote from, and whether HTML semantics expose that structure to a parser:
- H1 uniqueness (exactly 1 expected)
- Heading hierarchy (no skipped levels)
- Section length distribution (40–400 words ideal per H2/H3)
- Lead-sentence thesis check (first 25 words echo the heading)
- List & table density (≥15% of sections should have one)
- Citation density (≥10% of paragraphs link to an external source — ≥30% is ideal)
- Long-paragraph penalty (>120 words per paragraph hurts snippet extraction)
- HTML5 landmarks (`<main>` or `<article>` required; `<header>`/`<nav>`/`<footer>` recommended)
- Div-soup detection (page with zero semantic tags is critical)
- Prose wrapped in `<p>` tags (not loose text inside `<div>`/`<span>`)
- Real heading tags vs. `<div class="h2">…</div>` lookalikes

### 2. Machine-readable metadata

Whether structured-data consumers can identify what the page is:
- JSON-LD presence and `@type` (Article / FAQPage / HowTo / etc.)
- **Deprecated schema types** flagged: HowTo, SpecialAnnouncement, CourseInfo, EstimatedSalary, LearningVideo, ClaimReview, VehicleListing, PracticeProblem, Dataset
- **Restricted schema types** noted: FAQPage / QAPage (Google rich results restricted to government, healthcare, and community-Q&A verticals; still useful for AI citation)
- **Required-property validation** per `@type` (Article needs headline/author/datePublished; LocalBusiness needs name/address; etc.)
- **Microdata / RDFa fallback detection** — flagged when present without JSON-LD, or when coexisting with JSON-LD
- `<meta name='description'>` (120–160 chars ideal)
- Open Graph tags (`og:title`, `og:description`, `og:type` required; `og:image` recommended)
- Twitter Card tags (`twitter:card`, `twitter:title`, `twitter:description`, `twitter:image`)
- `<link rel='canonical'>`
- `<meta name='robots'>` (flags `noindex`)
- `<html lang>` attribute

### 3. Content quality (E-E-A-T, readability, freshness)

- **Flesch Reading Ease** score and grade band (very easy → very difficult). Not a Google ranking factor; used as an accessibility proxy. <30 flagged as too dense; >80 noted as possibly unauthoritative for technical content.
- **Author byline / E-E-A-T signal** — detected from `<meta name='author'>`, `rel='author'`, JSON-LD Person/author, or byline class names
- **Content freshness** — extracts `datePublished` / `dateModified` from JSON-LD, `article:published_time`, or `<meta name='date'>`. Flags content >18 months old without a recent `dateModified`.
- **Internal vs external link ratio** in the article body — aims for 2–5 internal links per 1000 words, plus ≥1 external citation per major claim

### 4. AI-citation signals (GEO)

Page-level signals specific to how AI Overviews / ChatGPT Search / Perplexity extract citable passages:
- **Passage-length citability scoring** — flags sections wildly outside the 134–167 word AI-citation sweet spot. Too-short sections force engines to combine multiple blocks (introduces error); too-long sections get paraphrased rather than quoted.
- **Definition-pattern detection** — first sentence of each H2/H3 section checked for "X is …", "X refers to …", "X means …" patterns. AI engines preferentially cite definition-shaped openings.
- **Answer-first heading check** — question-form headings ("How do I …", "What is …") must answer in their first 40 words. Flags buried answers.

### 5. Technical signals (URL structure, mobile viewport, JS rendering, IndexNow)

Per-page static-HTML technical checks (merged from claude-seo's `seo-technical`):
- **URL structure** — length >100 chars flagged; trailing-slash inconsistency with canonical flagged
- **Mobile viewport meta** — missing flagged as critical; `user-scalable=no` / `maximum-scale=1` flagged as recommended (accessibility); fixed-width viewport flagged as recommended
- **JS rendering detection** — empty SPA shell pattern (low body word count + empty mount selector like `#root`/`#__next` + no SSR markers) flagged as critical; low body text with no `<noscript>` fallback flagged as nice-to-have
- **IndexNow protocol** — `<meta name="indexnow">` declaration recognized as a strength when present

Core Web Vitals (lab data) and multi-page crawl-depth are intentionally NOT in this pass — they belong in a future `cwv-audit` skill (PageSpeed Insights API) and `sitemap-audit` respectively.

### 6. Asset hygiene

- `<title>` tag length (30–60 chars ideal)
- Image `alt` coverage (≥80% ideal, <50% is critical)
- Empty / `#`-only links

### 7. External signals (robots.txt, /llms.txt, security headers)

**Only runs when the source is an http(s) URL** — needs extra fetches against the site origin. Skipped silently for local files and `--stdin` without `--source URL`.

- **AI-crawler robots.txt audit** — for each known AI crawler (GPTBot, OAI-SearchBot, ChatGPT-User, ClaudeBot, anthropic-ai, Claude-Web, PerplexityBot, Perplexity-User, Google-Extended, Bytespider, CCBot, cohere-ai, etc.), flags whether the site allows or blocks it. Distinguishes **AI search crawlers** (live citation impact when blocked → CRITICAL) from **AI training crawlers** (training-corpus only when blocked → informational).
- **`/llms.txt` presence check** — emerging standard giving LLM crawlers a structured site map. Reports absence as a missed opportunity; reports structural problems (missing H1, missing description blockquote, no section headers, no `- [link](url)` entries) when present.
- **Security headers** — `Content-Security-Policy`, `Strict-Transport-Security`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`. Used as a trust signal by AI engines and crawl-quality scoring.

## Reference

`references/thresholds.md` documents every numeric threshold with the rationale and the source signal it tracks.

`examples/` contains sample audits you can show the user as a model of what good vs. bad reports look like.

## Mandatory verification checklist (run before sending any audit response)

Before sending ANY response containing a score, grade, finding, "strengths" item, or comparison table, walk through this list and answer each silently:

1. **Did I invoke `scripts/audit.py` (or `audit.audit_html_to_markdown` in Pyodide)?** If no → I have not yet acted on the user's request. My next step is to call the tool, NOT to refuse. Refusal is only valid AFTER a failed tool call.
2. **Did the script print a `Wrote /path/...-audit.md` line to stderr (or return a non-empty Markdown string in Pyodide)?** If no → I cannot answer with audit content. Surface the error.
3. **Can I quote that exact path back to the user, and does the file actually exist at that path on the user's host filesystem?** Valid forms: `/outputs/<stem>-audit.md` (the Pyodide mount) OR `~/Documents/synergyAI/outputs/<stem>-audit.md` (the host equivalent) — these refer to the same file. INVALID: `/home/pyodide/Documents/synergyAI/outputs/...` (MEMFS, host cannot see it). If no → I cannot answer with audit content.
4. **Did I open that file and verify that every score number, grade letter, finding title, and "what works well" item in my draft response appears verbatim in the file?** If no → I'm about to hallucinate. Rewrite.

If any answer is no, the response template is:

> *"I tried to audit `<url>` but the script <fetch-failed / wrote no file / errored with X>. I cannot produce a score, grade, or findings list from inference — those would be invented. Here is what we know: \<exact stderr / error message\>. Would you like me to <suggest a fix / try a different invocation / audit a local HTML file instead>?"*

**Bad patterns that mean you are hallucinating an audit:**
- Producing a polished "Voici les résultats" block after narrating multiple failures.
- Citing a report path under `/home/pyodide/...`.
- Comparing "previous score" to "new score" without having read either file.
- Calling out specific findings like "Densité de citations 2%" without quoting a line from a real `.md` file.
- Any "Excellent progrès ! 🎉" framing in a response where the script never wrote a file.

## Limitations to surface to the user

- **Heuristic, not predictive.** No audit can guarantee citation — AI-overview ranking depends on the engine's training data, query intent, and competing pages. The audit identifies *missing prerequisites*, not certainty.
- **Static analysis only.** Doesn't measure page speed, mobile rendering, or interactive behavior. Pair with Lighthouse for the perf side.
- **No backlink/authority signals.** The audit looks at the page in isolation. Domain authority, off-page citations, and brand recognition all matter for AI ranking but require external data sources.
- **JavaScript-rendered content invisible.** The script reads raw HTML. Pages that rely on client-side rendering (React/Vue SPAs without SSR) will appear nearly empty to the audit — and to most AI crawlers too. That's a signal worth taking seriously.
- **Auto-detected article root may guess wrong** for unusual page structures. Re-run with `--main-selector` when the cognitive findings reference nav/footer content.
