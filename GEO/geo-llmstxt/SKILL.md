---
name: geo-llmstxt
description: "Analyze or generate /llms.txt — the emerging AI-discovery file standard (Jeremy Howard, Sept 2024). Use for: 'check my llms.txt', 'validate llms.txt', 'generate llms.txt', 'create llms.txt for my site', 'llms-full.txt audit', 'llms.txt missing', 'how to write llms.txt', 'AI discovery file', 'help AI find my key pages'. Pipeline: fetch /llms.txt + /llms-full.txt + homepage + sitemap.xml -> if /llms.txt exists, parse its Markdown (H1 title, blockquote description, H2 sections, page entries) and validate against the spec (URL absoluteness, description length, entry count per section, Key Facts and Contact sections); if absent, gather generation context (site title from <title>/og:site_name, meta description, homepage H1, sitemap URL count, top sitemap pages with their <title> meta). The LLM scores Completeness/Accuracy/Usefulness (0-100 each, weighted 40/35/25) for analysis mode or writes a fresh llms.txt for generation mode."
dependencies: [beautifulsoup4]
fetches_urls: false
---

# geo-llmstxt

The `/llms.txt` standard (Jeremy Howard, September 2024) gives AI systems a
machine- and human-readable summary of a site's most important pages —
analogous to `robots.txt` but for "what's *useful*" instead of "what's blocked".
As of early 2026, fewer than 5% of sites have one — early adoption is a
differentiator.

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks to validate, audit, or generate an `llms.txt`, your
> **first action** is `run_skill_script` with `dir_name: geo-llmstxt` and
> `scripts/llmstxt_signals.py`. The script fetches `/llms.txt`,
> `/llms-full.txt`, the homepage, and `/sitemap.xml` in one go, parses the
> Markdown spec, validates structure, and supplies generation context.
>
> If the tool is genuinely missing, say so **after** attempting the call.
>
> ## ⛔ NO FABRICATION
>
> The script prints the **parsed llms.txt + validation + generation context to
> stdout** — `run_skill_script` returns it to you.
>
> - The Title, description, H2 sections, page-entry list, URL validity flags,
>   line count, sitemap URL count, homepage signals: ALL come from the script.
>   Never invent any of them.
> - **Analyze mode:** if the script reports `/llms.txt` not found, say so and
>   switch to generation mode — do not invent a fake "analysis" of a file
>   that doesn't exist.
> - **Generate mode:** use the script's homepage signals + sitemap URLs as
>   the source of truth for the pages you list. The site-name and description
>   come from the homepage signals (`<title>` / `og:site_name` / meta
>   description), not from your training memory. Confirm with the user before
>   filling in Key Facts (founding year, headquarters, customer counts, etc.)
>   that are not on the homepage.

## Invocation — exact argv shapes

> **Do NOT add `read_outputs` to this call.** The script prints its full
> analysis to stdout — that stdout is your input. The `*-ANALYSIS`/`*-SCORE`
> deliverable is what *you* author afterward, so it does not exist at run time;
> listing it in `read_outputs` returns an empty result, which looks like a
> failed run and triggers needless re-runs. Send only `dir_name`/`script`/`argv`.

```json
{ "dir_name": "geo-llmstxt", "script": "scripts/llmstxt_signals.py", "argv": ["https://example.com"] }
```

Any URL on the site works — the script derives the site root and fetches the
discovery files from there. (`--url` exists as a backstop alias.)

## The /llms.txt spec (LLM reference for scoring + generation)

A valid file looks like:

```markdown
# Site Name

> One-sentence description of the site/business. Under 200 characters.

## Docs

- [Page Title](https://example.com/page): 10-30 word description of what's there.
- [Another Page](https://example.com/another): Specific facts available here.

## Optional

- [Less Critical Page](https://example.com/optional): Description.

## Key Facts
- Founded in [year] by [founder(s)]
- Headquarters: [City, Country]
- [X] customers in [Y] countries
- Industry: [classification]

## Contact
- Website: https://example.com
- Email: hello@example.com
```

**Required elements:**

| Element | Format | Notes |
|---|---|---|
| Title | `# Site Name` (single H1, first line) | Must match the official business name |
| Description | `> One-sentence …` (blockquote right after title) | < 200 chars, factual, no marketing fluff |
| At least one H2 section | `## Section Name` | Common: Docs / Optional / API / Products / Services / Resources / Key Facts / Contact |
| Page entries | `- [Title](absolute-url): description` | Absolute URLs only; 10-30 words per description |

**Recommended:** Key Facts section, Contact section. Total 10-30 entries.
Total length 30-200 lines (concise version). `/llms-full.txt` exists for
the extended 150-500-line version.

## Analysis-mode scoring (when /llms.txt exists)

Score from the script's parsed structure + validation flags.

**Completeness (0-100, 40% weight):**
- Are major site sections covered? (script reports H2 sections present + sitemap top-level paths)
- Are the most important pages from the sitemap included? (LLM compares entry URLs against sitemap)
- Key Facts section present? (script flags)
- Contact section present? (script flags)

**Accuracy (0-100, 35% weight):**
- URLs absolute and well-formed? (script flags relative or malformed URLs)
- URLs map to real sitemap pages? (LLM compares)
- Descriptions specific and factual, not marketing fluff? (LLM judgment on entry text the script returns)
- Title matches `<title>` / `og:site_name` from homepage? (script reports both)

**Usefulness (0-100, 25% weight):**
- Descriptions specific enough to differentiate pages? (LLM judgment)
- Entries ordered by importance within each section? (LLM judgment)
- Total length 30-200 lines? (script reports)
- Section names match site navigation conceptually? (LLM judgment)

**Overall** = Completeness × 0.40 + Accuracy × 0.35 + Usefulness × 0.25.

## Generation-mode procedure (when /llms.txt is absent)

The script returns the generation context: homepage `<title>`, `og:site_name`,
meta description, homepage H1, full sitemap URL list (capped to 200 for
manageability), and the first navigation links from the homepage. Use those
verbatim — they're authoritative.

Page-selection priorities (apply to the sitemap URLs):

**Always include:** Homepage, About, Pricing (if exists), top 3-5 product /
service pages, Contact, Docs landing page (if exists).

**Include if high quality:** top blog posts (by apparent importance), case
studies, key resource/guide pages, FAQ, careers.

**Skip:** thin tag/category pages, paginated index pages, login/signup, legal
boilerplate (unless directly relevant), duplicate/near-duplicate URLs.

**Description writing rules:**
- 10-30 words.
- States WHAT information is on the page, not how great the page is.
- Includes specific topics, numbers, or features when known.
- Avoids marketing language ("best", "leading", "revolutionary").
- Examples:
  - ✓ "Explains the three pricing tiers (Free / Pro / Enterprise) with feature comparison and annual/monthly costs."
  - ✗ "Our amazing pricing page!" / "Click here for details."

**Confirm with the user** any Key Facts you cannot derive from the homepage
(founding year, founder names, exact employee count, customer count, office
locations) — never invent these.

After generating, present the candidate `llms.txt` and ask the user to confirm
before saving. The site owner should review URLs and descriptions.

## /llms-full.txt — extended version

Same format as `/llms.txt` but: 30-100+ page entries, 30-100-word
descriptions (may include key facts per page), 8-15 sections, 150-500+ lines.
Both files coexist — AI systems read `llms.txt` first, then optionally pull
`llms-full.txt` for deeper analysis. Generate `llms-full.txt` only if the user
explicitly asks for the extended version.

## Output

The script prints the **parsed file + validation + generation context to
stdout** — your scoring or generation input. It also writes
`GEO-LLMSTXT-EXTRACT.md` and `.json` to `~/Documents/synergyAI/outputs/`
(`/outputs/` in Pyodide). You write `GEO-LLMSTXT-ANALYSIS.md` (scored analysis)
or the generated `llms.txt` file content.

## Pyodide notes

The script routes all fetches through `/gpt/backend/api/v1/fetch-url` under
Pyodide and uses `urllib` natively. `fetches_urls: false` because the script
self-fetches four files in parallel.

## Limitations

- **No URL-validity HEAD-check.** Verifying that every page entry's URL
  returns 200 would require N additional fetches; the script flags
  *relative* or malformed URLs but does not probe each one. Recommend the
  user spot-check listed URLs after deployment.
- **Sitemap is the only source of truth for "all pages".** If the sitemap is
  incomplete or stale, the completeness check is limited. Note that to the
  user.
- **Key Facts must be user-confirmed.** Founding year, customer counts, etc.
  rarely appear on the homepage; ask the user rather than guessing.
