---
name: schema-generate
description: "Generate ready-to-paste JSON-LD structured-data markup for a web page. Use for: 'generate schema', 'add JSON-LD to my page', 'schema markup for [Article/Organization/LocalBusiness/Product/Event]', 'fix my structured data', 'create rich-results markup'. Pipeline: fetch page → detect type from existing JSON-LD, URL pattern (/blog, /products, /about, /contact), OG type, headings → auto-fill fields from page content (title, meta description, author, dates, og:image, canonical) → mark unknowns as [PLACEHOLDER] → emit valid JSON-LD ready to paste in <head>. 22 templates: 11 common (Article, BlogPosting, NewsArticle, Organization, LocalBusiness, Person, WebSite, WebPage, BreadcrumbList, ContactPage, Event) + 11 specialized (VideoObject, BroadcastEvent, Product, ProfilePage, ItemList, etc.). Refuses deprecated types (HowTo, SpecialAnnouncement, CourseInfo). Warns on restricted types (FAQPage on commercial sites). Companion to ai-search-audit."
dependencies: [beautifulsoup4]
fetches_urls: false
---

# schema-generate

Companion to `ai-search-audit`:
- `ai-search-audit` **detects** the JSON-LD on a page, **validates** required properties, **flags** deprecated/restricted types.
- `schema-generate` **produces** the JSON-LD a page is missing (or generates a new type to add).

The two compose: run the audit first to learn what's missing, then run schema-generate to produce the markup.

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks for schema/JSON-LD/structured-data generation, your first action is `run_skill_script` with `dir_name: schema-generate` and `scripts/audit.py`. Don't write JSON-LD inline from memory — call the tool so the page's actual content auto-fills.
>
> ## ⛔ NO FABRICATION
>
> The skill auto-fills fields from the actual page content. Never produce hand-crafted JSON-LD claiming auto-fill — the user wants the fields populated from THEIR page, not a generic template they could find on schema.org.

## Invocation — exact argv shapes

The URL is **positional**, not a flag.

```json
// Auto-detect type, generate matching schema (most common case)
{ "dir_name": "schema-generate", "script": "scripts/audit.py", "argv": ["https://example.com/blog/my-post"] }

// Force a specific type (when auto-detection picks the wrong one)
{ "argv": ["https://example.com/", "--type", "Organization"] }

// Emit ALL applicable types (Article + BreadcrumbList + Person, etc.)
{ "argv": ["https://example.com/blog/my-post", "--all"] }

// List available templates without fetching the page
{ "argv": ["--list"] }

// Generate a template without page content (placeholder-only)
{ "argv": ["--type", "LocalBusiness", "--blank"] }
```

**Do NOT pass the URL with a flag name.** No `--input`, `--page`, `--site`. The URL goes first in `argv`. (The `--url` alias exists as a backstop.)

CLI for reference:

```bash
# Auto-detect type from page
python scripts/audit.py https://example.com/blog/my-post

# Override the detected type
python scripts/audit.py https://example.com/ --type Organization

# Emit all applicable types
python scripts/audit.py https://example.com/blog/my-post --all

# List templates
python scripts/audit.py --list

# Blank template (no page fetch)
python scripts/audit.py --type LocalBusiness --blank
```

## Page-type detection (auto)

The auto-detector uses these signals, in priority order:

1. **Existing JSON-LD `@type`** on the page (if `--keep-existing` is set, the script complements rather than replaces)
2. **URL pattern** — `/blog/` → BlogPosting; `/article/` → Article; `/products/` → Product; `/about` → Organization; `/contact` → ContactPage; `/cv`, `/author/`, `/team/` → Person; `/event/` → Event; root `/` → WebSite + Organization
3. **`og:type` meta** — `article` → Article; `website` → WebPage; `product` → Product
4. **Heading content cues** — "Contact Us", "Our Location" → ContactPage/LocalBusiness; "About Me" → Person
5. **Body word count + structure** — long article-shaped content → Article; short navigational → WebPage

If detection is ambiguous, the report lists the top candidates and the user can pick one with `--type`.

## What it auto-fills from the page

| Page source | JSON-LD field |
|---|---|
| `<title>` | `headline`, `name` |
| `<meta name="description">` | `description` |
| `<link rel="canonical">` | `@id`, `mainEntityOfPage`, `url` |
| `<meta name="author">` or JSON-LD Person/author | `author.name` |
| `<meta property="article:published_time">` | `datePublished` |
| `<meta property="article:modified_time">` | `dateModified` |
| `<meta property="og:image">` | `image` |
| First content `<img>` if no OG image | `image` (fallback) |
| `<html lang>` | `inLanguage` |

Unknown fields get a `[PLACEHOLDER]` marker the user fills manually.

## What it REFUSES to emit

Deprecated types — Google has retired rich-result support for these. The skill refuses with an error rather than emitting stale markup.

| Type | Status |
|---|---|
| HowTo | Removed September 2023 |
| SpecialAnnouncement | Deprecated July 2025 |
| CourseInfo, EstimatedSalary, LearningVideo | Retired June 2025 |
| ClaimReview, VehicleListing | Retired June 2025 |
| PracticeProblem, Dataset (rich results) | Retired late 2025 |

## What it REFUSES with restriction context

`FAQPage` and `QAPage` — Google restricts rich-result eligibility to government/healthcare verticals (or community-Q&A for QAPage) since August 2023. The skill refuses to emit these by default and surfaces the restriction reason in the error output. AI engines (ChatGPT Search, Perplexity) still extract Q&A blocks from properly-structured HTML even without explicit FAQPage schema, so this restriction doesn't block AI citation — only Google rich-result eligibility on commercial sites.

If you have a legitimate reason to add FAQPage (gov/healthcare site), hand-author the markup. The skill is opinionated to avoid encouraging the common "stuff every page with FAQPage for SERP" anti-pattern.

## Output

| File | Content |
|---|---|
| `schema-recommended.json` | The emitted JSON-LD (one type by default; multiple if `--all`) |
| `SCHEMA-REPORT.md` | Detection results, auto-filled fields summary, instructions to paste, warnings about restricted types |

Both land in `~/Documents/synergyAI/outputs/` (or `/outputs/` in Pyodide).

## Pyodide notes

Routes the page fetch through `/gpt/backend/api/v1/fetch-url`. Default output is `/outputs/` in Pyodide and `~/Documents/synergyAI/outputs/` in CPython.

## Templates available

- **Common** (`templates/templates_common.json`, 11): Article, BlogPosting, NewsArticle, Organization, LocalBusiness, Person, WebSite, WebPage, BreadcrumbList, ContactPage, Event
- **Specialized** (`templates/templates_specialized.json`, 11): VideoObject, BroadcastEvent, Clip, SeekToAction, SoftwareSourceCode, ProductGroup, ProfilePage, Certification, OfferShippingDetails, Product, ItemList

Use `--list` to see them in the terminal.

## Limitations

- **Auto-detection is heuristic.** A `/blog/` URL with no Article-shaped content might still be classified as Article — pass `--type` to override.
- **JavaScript-rendered content invisible.** The detector reads raw HTML. SPAs without SSR show up as nearly empty and the recommended type defaults to WebPage.
- **Doesn't validate the output.** That's `ai-search-audit`'s job. Run the audit on your page after pasting the generated JSON-LD to confirm required properties are filled.
