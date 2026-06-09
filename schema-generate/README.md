# schema-generate

> Generate ready-to-paste JSON-LD structured-data markup for a web page.

## What this skill does

Companion to `ai-search-audit`:
- `ai-search-audit` **detects + validates + flags**
- `schema-generate` **produces** the markup the audit reports as missing

Three-step pipeline:

1. **Fetch the page** (skipped in `--blank` mode)
2. **Detect type(s)** from URL pattern, existing JSON-LD `@type`, OG type, heading content, word count
3. **Auto-fill** template fields from page content; mark unknown fields as `[PLACEHOLDER]`

22 templates supported. Refuses to emit deprecated types. Refuses to emit restricted types but with clear context messages.

## Output

| File | Content |
|---|---|
| `schema-recommended.json` | The generated JSON-LD (one object by default, or a JSON array when `--all`) |
| `SCHEMA-REPORT.md` | Type-candidate scoring table, page-context summary, the emitted JSON-LD with auto-filled-fields list, warnings about restricted types |

Both land in `~/Documents/synergyAI/outputs/` (or `/outputs/` in Pyodide).

## CLI reference

```bash
python scripts/audit.py [INPUT] [OPTIONS]
```

### Modes

| Flag | Purpose |
|---|---|
| `INPUT` (positional) | URL to generate schema for. Required unless `--list` or `--blank`. |
| `--url URL` | Alias for the positional argument |
| `--type TYPE` | Force a specific `@type` (overrides auto-detection) |
| `--all` | Emit all applicable types (score ≥ 10 from the detector) |
| `--list` | Show available templates and exit (no fetch) |
| `--blank` | Output a placeholder-only template (no fetch). Requires `--type`. |
| `-o`, `--output PATH` | Default: `~/Documents/synergyAI/outputs/` |

## Type detection

`detect_types()` scores every template against the page context:

| Signal | Points | When |
|---|---|---|
| URL pattern match | +30 | URL contains one of the type's `url_patterns` |
| `og:type` match | +20 | OG type matches the template's `og_types` |
| Heading cue match | +10 each | H1 or title contains a cue phrase |
| Word count ≥ threshold | +5 | When the template declares a `min_word_count` |
| Already declared on page | +25 | Existing JSON-LD on the page already uses this `@type` |
| Always-recommended | +5 | BreadcrumbList |

Candidates are sorted high → low. By default, the script picks the top candidate. With `--all`, it picks every candidate scoring ≥ 10. With `--type X`, it forces X regardless of score.

## Auto-fill mapping

Template `[PLACEHOLDER]` values get replaced when the page provides the data:

| Page source | JSON-LD field |
|---|---|
| `<title>` | `headline`, `name` |
| `<meta name="description">` | `description` |
| `<link rel="canonical">` | `url`, `@id`, `mainEntityOfPage` |
| `<meta name="author">` or JSON-LD `Person`/`author` | `author.name` |
| `<meta property="article:published_time">` or JSON-LD `datePublished` | `datePublished` |
| `<meta property="article:modified_time">` or JSON-LD `dateModified` | `dateModified` |
| `<meta property="og:image">` | `image`, `thumbnailUrl` |
| First content `<img>` (fallback if no og:image) | `image` |
| `<html lang>` | `inLanguage` |
| `<meta property="og:site_name">` | `publisher.name` |

Unknown fields keep the `[PLACEHOLDER]` marker — the user fills them by hand. The report lists how many placeholders remain per emitted block.

## Templates

### Common templates (11)

Authored new for this skill. Stored in `templates/templates_common.json`.

| `@type` | Use case |
|---|---|
| Article | Editorial / long-form |
| BlogPosting | Blog posts (`/blog/` structure) |
| NewsArticle | News (adds dateline + section to Article) |
| Organization | Company homepage (Knowledge Panel eligibility) |
| LocalBusiness | Brick-and-mortar (address + geo) |
| Person | Author / profile (E-E-A-T signals) |
| WebSite | Site-wide schema with sitelinks search box |
| WebPage | Generic fallback |
| BreadcrumbList | Hierarchical navigation |
| ContactPage | `/contact` pages |
| Event | Conference, webinar, concert, workshop |

### Specialized templates (11)

Lifted from claude-seo's `schema/templates.json`. Stored in `templates/templates_specialized.json`.

| `@type` | Use case |
|---|---|
| VideoObject | Video pages with thumbnails, duration |
| BroadcastEvent | Live streaming (LIVE badge) |
| Clip | Key moments within a video |
| SeekToAction | Seek functionality in video rich results |
| SoftwareSourceCode | Open-source / code repository pages |
| ProductGroup | E-commerce variants grouped by size/color |
| ProfilePage | Author/creator profile pages (E-E-A-T) |
| Certification | Energy Star, safety, organic certifications |
| OfferShippingDetails | Shipping/delivery for e-commerce |
| Product (Full E-commerce) | Pricing, availability, reviews, brand |
| ItemList | Hub/pillar pages listing related content |

## Refused vs warned

### REFUSED (returns error, no schema emitted)

These types have lost Google rich-result support. Emitting them adds noise without benefit.

| `@type` | Retired |
|---|---|
| HowTo | Sept 2023 |
| SpecialAnnouncement | July 2025 |
| CourseInfo, EstimatedSalary, LearningVideo | June 2025 |
| ClaimReview, VehicleListing | June 2025 |
| PracticeProblem | late 2025 |

### RESTRICTED (no template; surfaces restriction reason)

| `@type` | Restriction |
|---|---|
| FAQPage | Google rich results gov/healthcare only since Aug 2023. AI engines still extract Q&A from properly-structured HTML even without FAQPage schema. |
| QAPage | Community-Q&A only |

These types deliberately don't have templates in this skill. The intent: avoid encouraging the common "stuff every page with FAQPage for SERP" anti-pattern.

## Python API

```python
import audit, asyncio

# Load templates
templates = audit.load_templates()  # {type_name: entry}

# Just-fetch-and-context (no generation)
import argparse
args = argparse.Namespace(input="https://example.com", url_alias=None,
                           type=None, all=False, list=False, blank=False,
                           output=None, source=None)
asyncio.run(audit.amain(args))

# Direct template autofill (e.g. you already have HTML)
ctx = audit.extract_context(html, url="https://example.com")
filled, fields = audit.autofill(templates["Article"]["template"], ctx)
```

## Pyodide notes

- `fetches_urls: false` — page fetch happens via the proxy
- Output detects Pyodide and routes to `/outputs/`
- Templates load from `<skill>/templates/*.json` regardless of runtime (skill folder is mounted)

## Limitations

- **Auto-detection is heuristic.** `/blog/some-post` with no Article-shaped content might still be classified as Article — use `--type` to override.
- **`name` field can be too long** when the page's `<title>` includes branding. The auto-filled value reflects the page literally; trim by hand if Person.name etc. should be shorter.
- **JavaScript-rendered content invisible.** Detector reads raw HTML. SPAs without SSR default to WebPage.
- **Doesn't validate the output.** Run `ai-search-audit` on your page after pasting the generated JSON-LD to confirm required properties are filled.

## Related skills

- **`ai-search-audit`** — detect + validate + flag. Run before AND after pasting generated schema.
- **`sitemap-audit`** — when adding new pages, generate schema then submit via sitemap
