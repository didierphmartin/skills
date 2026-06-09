---
name: hreflang-audit
description: "Validate or generate hreflang tags for multi-language/multi-region sites. Use for: 'check hreflang', 'audit i18n', 'validate language tags', 'fix hreflang errors', 'generate hreflang', 'multi-language SEO', 'check return tags', 'is my x-default correct', 'hreflang en-GB en-US fr de', 'verifier les balises hreflang'. Checks: self-referencing tag presence (missing = Google ignores the whole set), return-tag mesh (A→B requires B→A), x-default presence, ISO 639-1 language codes (catches 'eng' instead of 'en', 'jp' instead of 'ja'), ISO 3166-1 region codes (catches 'en-uk' instead of 'en-GB'), canonical alignment, HTTP/HTTPS consistency, cross-domain support. Generation: produces corrected hreflang sets in three formats — HTML <link> tags, HTTP Link: header values, or XML sitemap with xhtml:link namespace. Optional cultural-adaptation assessment (DACH/Francophone/Hispanic/Japanese profiles). Output: VALIDATION-REPORT.md with per-page status table + corrected hreflang code blocks."
dependencies: [beautifulsoup4]
# NOTE: fetches_urls is intentionally false. The return-tag mesh check fetches
# every alternate URL declared — N×M fetches via /api/v1/fetch-url. The bridge's
# single-URL prefetch is wasted overhead for this skill.
fetches_urls: false
---

# hreflang-audit

Two purposes:
1. **Validate** the hreflang setup of a multi-language/multi-region site — find missing return tags, invalid codes, mismatched canonicals.
2. **Generate** a corrected hreflang set in the user's preferred format (HTML / HTTP header / sitemap).

Companion to `sitemap-audit` (which can also emit a hreflang sitemap when given multi-language input) and `ai-search-audit` (which checks one page's structure). `hreflang-audit` is specifically for **cross-page i18n integrity** — verifying that the set of language/region variants forms a consistent mesh.

> ## ✅ REQUIRED FIRST STEP — ALWAYS CALL THE TOOL
>
> When the user asks anything hreflang-related with a URL, your first action is `run_skill_script` with `dir_name: hreflang-audit` and `scripts/audit.py`. Localhost URLs are fully supported through the fetch bridge. Don't refuse with access concerns.
>
> ## ⛔ NO FABRICATION
>
> If the script errors or writes no file, surface the exact error. Never produce a "looks like your hreflang has X problem" report from inference. Every finding ("page A is missing return tag from page B", "code 'eng' is invalid") must come from the actual report file at `/outputs/VALIDATION-REPORT.md`.

## Invocation — exact argv shapes

The URL is **positional**, not a flag. When calling via `run_skill_script`, the `argv` array should be:

```json
// Validate one page's hreflang set (default — fetches the page, extracts hreflang, runs mesh check on all alternates)
{ "dir_name": "hreflang-audit", "script": "scripts/audit.py", "argv": ["https://example.com"] }

// Skip the mesh check (faster — validates only the input page's declarations)
{ "argv": ["https://example.com", "--skip-mesh"] }

// Generate corrected hreflang from input
{ "argv": ["https://example.com", "--generate", "--format", "html"] }
{ "argv": ["https://example.com", "--generate", "--format", "http"] }
{ "argv": ["https://example.com", "--generate", "--format", "sitemap"] }
```

**Do NOT pass the URL with a flag name** — there is no `--input`, `--site`, `--page`, or `--target` flag. URL goes first in `argv`. (The `--url` alias exists as a backstop for tools that cannot emit positional argv, but the positional form is preferred.)

CLI for reference:

```bash
# Validate a multi-language page
python scripts/audit.py https://example.com

# Skip the return-tag mesh check (faster)
python scripts/audit.py https://example.com --skip-mesh

# Generate corrected hreflang in HTML format
python scripts/audit.py https://example.com --generate --format html

# Generate sitemap-format hreflang for multi-page sites
python scripts/audit.py https://example.com --generate --format sitemap

# Custom output path
python scripts/audit.py https://example.com -o my-report.md
```

## When to use this skill

| User says | Behavior |
|---|---|
| "check hreflang on my site" | validate |
| "fix my hreflang errors" | validate, then offer generation |
| "generate hreflang tags for these URLs" | generate (use `--format html`) |
| "I have a sitemap with hreflang — is it correct?" | validate (script auto-detects sitemap-form hreflang) |
| "audit cultural adaptation for German" | validate + load `references/cultural-profiles.md` for the DACH profile |

## What the validate pass checks

| Check | Severity | Why |
|---|---|---|
| Self-referencing tag presence | critical | Missing self-ref → Google ignores the entire hreflang set |
| Return-tag mesh (A→B requires B→A) | critical | One-way relationships are silently dropped |
| x-default present | recommended | Required for "no language match" fallback |
| ISO 639-1 language codes (`en`, `fr`, `de`, `ja`) | high | `eng`, `jp`, `chinese` are invalid; engines drop them |
| ISO 3166-1 region codes (`US`, `GB`, `FR`, `BR`) | high | `uk` (instead of `GB`), `LA` (Latin America, not a country) are invalid |
| Hreflang URL = canonical | high | hreflang on non-canonical URLs is ignored |
| HTTP/HTTPS consistency | medium | Mixed protocols in the set cause validation failures |
| Trailing-slash consistency | medium | hreflang URLs must match canonical exactly |
| Sitemap-vs-HTML hreflang duplication | low | Pick one method; sitemap preferred for >50 variants |

## What the generate pass produces

Three formats. Pick via `--format`:

### `--format html` (default)

```html
<link rel="alternate" hreflang="en-US" href="https://example.com/page" />
<link rel="alternate" hreflang="en-GB" href="https://example.co.uk/page" />
<link rel="alternate" hreflang="fr" href="https://example.com/fr/page" />
<link rel="alternate" hreflang="x-default" href="https://example.com/page" />
```

### `--format http`

```
Link: <https://example.com/page>; rel="alternate"; hreflang="en-US",
      <https://example.com/fr/page>; rel="alternate"; hreflang="fr",
      <https://example.com/page>; rel="alternate"; hreflang="x-default"
```

### `--format sitemap`

```xml
<url>
  <loc>https://example.com/page</loc>
  <xhtml:link rel="alternate" hreflang="en-US" href="https://example.com/page"/>
  <xhtml:link rel="alternate" hreflang="fr" href="https://example.com/fr/page"/>
  <xhtml:link rel="alternate" hreflang="x-default" href="https://example.com/page"/>
</url>
```

Best for sites with >50 language variants per page or cross-domain hreflang.

## Mesh fetch behavior

When the input page declares N alternates, the script fetches each alternate URL (N total fetches) and re-extracts the hreflang set declared on each. The validator then builds an adjacency matrix:

- For every declared A→B relationship, check that B's hreflang set contains a B→A return tag.
- Missing return tags become Critical findings.

Pass `--skip-mesh` to validate only the input page's local declarations without the cross-page fetches. Useful as a quick first pass or when the alternate pages are not reachable.

## Cultural adaptation (optional, qualitative)

If the user asks about *content* localization rather than just technical hreflang validation, load the relevant profile from `references/cultural-profiles.md` (DACH, Francophone, Hispanic, Japanese). These profiles describe typical pitfalls — direct vs indirect CTAs, trust signal expectations, locale-appropriate legal references, etc. Not algorithmically scored; the LLM consults them when producing recommendations.

## Pyodide / browser-Python notes

Pyodide cannot fetch cross-origin URLs directly. The script routes fetches through the host's same-origin proxy at `/gpt/backend/api/v1/fetch-url` — same pattern as `serp-shape` and `sitemap-audit`. Default output directory is `/outputs/` in Pyodide (the host-mounted folder) and `~/Documents/synergyAI/outputs/` in CPython.

## Reference files

- `references/cultural-profiles.md` — DACH, Francophone, Hispanic, Japanese cultural-adaptation checklists
- `references/locale-formats.md` — Number, date, currency, address, phone format tables per locale
- `references/content-parity.md` — Content parity audit methodology (section structure, word-count ratios)

## Limitations to surface to the user

- **JavaScript-rendered hreflang missed.** Sites that inject hreflang via JS won't be visible to a static-HTML crawler. Same caveat as `ai-search-audit`.
- **HTTP Link: header hreflang requires HEAD/GET response inspection.** The script checks both `<link rel="alternate">` in HTML and the `Link:` response header.
- **Mesh check is the bottleneck.** N alternates = N HTTP requests. Use `--skip-mesh` for fast iteration during initial fix-up.
- **Cultural profiles are qualitative.** They flag risk patterns; they don't score localization quality.
