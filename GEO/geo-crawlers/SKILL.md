---
name: geo-crawlers
description: "Audit which AI crawlers can reach a website and how much of it AI search engines can see. Use for: 'can ChatGPT crawl my site', 'is GPTBot blocked', 'AI crawler access', 'check robots.txt for AI bots', 'are AI crawlers blocked', 'GPTBot/ClaudeBot/PerplexityBot/Google-Extended access', 'llms.txt check', 'Content-Signal directive', 'why isn't my site showing up in AI search'. Pipeline: fetch robots.txt + homepage + /llms.txt + /ai.txt + /.well-known/ai-plugin.json -> parse robots.txt user-agent groups -> resolve effective Allowed/Blocked/Not-Mentioned status for 14 known AI crawlers across 3 tiers -> detect blanket blocks, crawl-delay, sitemap directives -> parse the IETF Content-Signal directive -> scan the homepage for noai/noimageai/noindex meta tags -> compute a 0-100 AI Visibility Score. Output: GEO-CRAWLER-ACCESS.md report + JSON. Deterministic: the script does all fetching, parsing, and scoring; the LLM explains the findings and tailors the recommended robots.txt."
dependencies: [beautifulsoup4]
fetches_urls: false
---

# geo-crawlers

Audit a site's accessibility to **AI crawlers** — the bots AI companies use to
discover, index, and cite web content. If AI crawlers are blocked, the site
cannot appear in AI-generated answers regardless of content quality. Crawler
access is the foundational technical requirement for GEO (Generative Engine
Optimization).

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks about AI crawler access, robots.txt for AI bots, or why a
> site is missing from AI search, your **first action** is `run_skill_script`
> with `dir_name: geo-crawlers` and `scripts/crawler_check.py`. The script
> fetches and parses everything. Do not hand-write a crawler report from memory.
>
> If the tool is truly missing from your tool list, surface that **after**
> attempting the call — never refuse pre-emptively.
>
> ## ⛔ NO FABRICATION — PRESENT THE SCRIPT'S REPORT
>
> The script prints a **complete Markdown report to stdout** — `run_skill_script`
> returns it to you. **Present that report.** Do not rebuild it from memory.
>
> - This skill audits **exactly 14 named AI crawlers** (listed below). There is
>   no other. Never add a crawler the script did not return (there is no
>   "YouBot" here), never move a crawler to a different tier, never change a
>   status, score, or Content-Signal value.
> - Every crawler status, tier, score, and Content-Signal value in your answer
>   must be copied from the script's output.
> - If the script reports robots.txt was not found, say so — that means crawlers
>   are allowed by default, not blocked.
> - You may add narrative interpretation (why a publisher blocks AI crawlers,
>   what to do next) — but every fact comes from the script.

## Invocation — exact argv shapes

> **Do NOT add `read_outputs` to this call.** The script prints its full
> report to stdout — that stdout is your input. The `*-ANALYSIS`/`*-SCORE`
> deliverable is what *you* author afterward, so it does not exist at run time;
> listing it in `read_outputs` returns an empty result, which looks like a
> failed run and triggers needless re-runs. Send only `dir_name`/`script`/`argv`.

The site URL is **positional**. Any page URL works — the script derives the site
root and fetches `<root>/robots.txt` itself.

```json
{ "dir_name": "geo-crawlers", "script": "scripts/crawler_check.py", "argv": ["https://example.com"] }
```

```json
// A deep page URL is fine — the root is derived
{ "argv": ["https://example.com/blog/some-post"] }
```

Do **not** pass the URL with a flag. It goes first in `argv`. (`--url` exists as
a backstop alias.)

CLI for reference:

```bash
python scripts/crawler_check.py https://example.com
```

## What the script does

1. Derives the site root and fetches `robots.txt`, the homepage, `/llms.txt`,
   `/ai.txt`, and `/.well-known/ai-plugin.json`.
2. Parses robots.txt into User-agent groups and resolves the **effective status**
   of each of the 14 known AI crawlers: `Allowed`, `Blocked`, or `Not Mentioned`
   (inherits the `User-agent: *` group).
3. Detects a **blanket block** (`User-agent: *` + `Disallow: /`), `Crawl-delay`,
   and `Sitemap:` directives.
4. Parses the IETF **`Content-Signal:`** directive (draft `draft-romm-aipref-contentsignals`).
5. Scans the homepage for `noai`, `noimageai`, `noindex`, and bot-specific
   `<meta>` tags.
6. Computes the **AI Visibility Score (0-100)** and writes the report.

## AI crawler reference (3 tiers)

**Tier 1 — critical for AI search visibility (recommend ALLOW).** Power the AI
search products where users actively look for answers.

| Crawler | Operator | Why it matters |
|---|---|---|
| GPTBot | OpenAI | Powers ChatGPT browsing/search (300M+ weekly users) |
| OAI-SearchBot | OpenAI | ChatGPT search results; no training use |
| ChatGPT-User | OpenAI | User-initiated "visit this URL" requests |
| ClaudeBot | Anthropic | Claude web search, citations, analysis |
| PerplexityBot | Perplexity | Perplexity search; best AI referral traffic (always cites) |

**Tier 2 — broader AI ecosystem (recommend ALLOW).**

| Crawler | Operator | Why it matters |
|---|---|---|
| Google-Extended | Google | Gemini features / AI Overviews — does NOT affect Search rank |
| GoogleOther | Google | Google AI research and experimental crawls |
| Applebot-Extended | Apple | Apple Intelligence (2B+ devices) |
| Amazonbot | Amazon | Alexa answers and Amazon AI features |
| FacebookBot | Meta | Meta AI across Facebook/Instagram/WhatsApp |

**Tier 3 — training-only (ALLOW or BLOCK by strategy).** Blocking these does not
affect AI *search* visibility.

| Crawler | Operator | Note |
|---|---|---|
| CCBot | Common Crawl | Training dataset used by many AI firms |
| anthropic-ai | Anthropic | Claude training (separate from ClaudeBot) |
| Bytespider | ByteDance | Aggressive crawler — block for most Western sites |
| cohere-ai | Cohere | Cohere model training |

## Recommendation matrix

| Tier | Default recommendation | Reason |
|---|---|---|
| Tier 1 | **ALLOW all** | Direct AI search visibility; blocking removes you from AI answers |
| Tier 2 | **ALLOW all** | Broad reach; Google-Extended has no Search-rank downside |
| Tier 3 | **Context-dependent** | Training data only — allow for reach, block to control training use. Block `Bytespider` for most Western sites. |

## Maximum AI visibility — robots.txt

Recommend this configuration to a user whose Tier 1/2 crawlers are blocked or
not mentioned:

```
# AI crawlers — ALLOWED for AI search visibility
User-agent: GPTBot
User-agent: OAI-SearchBot
User-agent: ChatGPT-User
User-agent: ClaudeBot
User-agent: PerplexityBot
User-agent: Google-Extended
User-agent: GoogleOther
User-agent: Applebot-Extended
User-agent: Amazonbot
User-agent: FacebookBot
Allow: /

# AI crawlers — BLOCKED (aggressive / low value)
User-agent: Bytespider
Disallow: /

# Declare AI-usage preferences explicitly (IETF draft)
Content-Signal: ai-train=yes, search=yes, ai-retrieval=yes
```

Tailor it: if the user objects to training-data use, set `ai-train=no` and add
`CCBot` / `anthropic-ai` to the blocked group — note this does not affect AI
*search* visibility.

## Content-Signal directive

`Content-Signal:` in robots.txt declares AI-usage preferences. Valid keys:
`ai-train`, `search`, `ai-personalization`, `ai-retrieval`; valid values:
`yes` / `no`. The script parses it and flags unknown keys or invalid values.
If absent, recommend adding one — see <https://contentsignals.org/>.

## Scoring

| Component | Weight | How it scores |
|---|---|---|
| Tier 1 crawlers accessible | 50% | proportional — 10 pts per accessible Tier 1 crawler |
| Tier 2 crawlers accessible | 25% | proportional — 5 pts per accessible Tier 2 crawler |
| No blanket block | 15% | full points if no `User-agent: * Disallow: /` and no `noai` meta tag |
| AI discovery files | 10% | 5 pts for `/llms.txt` present, 5 pts for a `Sitemap:` directive |

"Accessible" = effective status `Allowed` or `Not Mentioned`.

## Output

The script prints the **complete report to stdout** — that is the deliverable
you present to the user. It also writes two files:

| File | Content |
|---|---|
| `GEO-CRAWLER-ACCESS.md` | The same full report (score, per-crawler table, Content-Signal, AI files, recommendations) |
| `GEO-CRAWLER-ACCESS.json` | Structured data for downstream skills |

Files land in `~/Documents/synergyAI/outputs/` (`/outputs/` in Pyodide). The
runner may inject `-o /outputs/<stem>-audit.md`; the script writes its report
there and the JSON beside it.

## Pyodide notes

The script routes every fetch through `/gpt/backend/api/v1/fetch-url` when
running under Pyodide, and uses `urllib` directly under native CPython — so it
works identically in the app and from a terminal. `fetches_urls: false` because
the script fetches on its own; the runner does not pre-fetch.

## Limitations

- **robots.txt only for crawler status.** Page-level `X-Robots-Tag` HTTP headers
  are not inspected; mention this if the user needs a per-page audit.
- **JavaScript rendering not assessed.** AI crawlers vary in JS support; if the
  site is an SPA without SSR, flag it separately.
- **Meta-tag scan is homepage-only.** Other pages may set their own `noindex`.
