# hreflang-audit

> Validate or generate hreflang tags for multi-language / multi-region sites.

## What this skill does

Two purposes:

1. **Validate** the hreflang setup on a multi-language page — find missing return tags, invalid ISO codes, missing x-default, canonical mismatches, protocol inconsistency.
2. **Generate** a corrected hreflang set in three formats:
   - HTML `<link rel="alternate" hreflang="..." href="..." />`
   - HTTP `Link:` response header (RFC 8288)
   - XML sitemap with `xhtml:link` namespace

## Output

| Mode | File in `~/Documents/synergyAI/outputs/` (or `/outputs/` in Pyodide) |
|---|---|
| Validate (default) | `VALIDATION-REPORT.md` |
| Generate `--format html` | `hreflang-corrected.html` |
| Generate `--format http` | `hreflang-corrected.http.txt` |
| Generate `--format sitemap` | `hreflang-corrected.xml` |

The validation report contains:
- **Score** (0-100)
- **Declared hreflang set** table (lang, URL, source: html/http-header/sitemap)
- **Mesh check** table per alternate (fetched, declarations count, links back ✓/✗) — only when not `--skip-mesh`
- **Strengths** + **Findings** + **Stats**

## CLI reference

```bash
python scripts/audit.py [INPUT] [OPTIONS]
```

### Modes

| Flag | Purpose |
|---|---|
| `INPUT` (positional) | URL to validate (HTML page or sitemap.xml). Required unless `--url`. |
| `--url URL` | Alias for the positional argument |
| `--generate` | Generate a corrected hreflang set instead of validating |
| `--format {html,http,sitemap}` | Generate-mode output format (default: html) |
| `--skip-mesh` | Skip the return-tag mesh check (no fetches of alternate pages — faster) |

### Common flags

| Flag | Default | Purpose |
|---|---|---|
| `-o`, `--output PATH` | `~/Documents/synergyAI/outputs/` | Output path (file or directory) |
| `--stdout` | off | Print to stdout instead of writing a file |

## Validation checks

| Check | Severity | Detection |
|---|---|---|
| Self-referencing tag present | critical | Page's own URL is among the declared alternates |
| Return-tag mesh (A→B requires B→A) | critical | For each declared alternate, fetch it + verify return tag |
| x-default present | recommended | Set should include `hreflang="x-default"` |
| Multiple x-default tags | recommended | Should be exactly one |
| ISO 639-1 language code valid | critical | 184 codes embedded; suggestions for common mistakes (eng→en, jp→ja, chinese→zh, cn→zh) |
| ISO 3166-1 region code valid | critical | 249 codes embedded; suggestions (uk→GB, LA→`""`, EU→`""`) |
| Malformed value (not lang or lang-REGION or x-default) | critical | Regex `[a-z]{2,3}(-[a-zA-Z]{2,4})?` |
| Canonical alignment | critical | Page's canonical must equal page URL for hreflang to count |
| HTTP/HTTPS protocol consistency | recommended | All hreflang URLs should use same scheme |
| Alternate page unreachable | recommended | When mesh fetch fails or returns 4xx/5xx |

## Extraction sources

| Source | How |
|---|---|
| HTML `<link>` | `<link rel="alternate" hreflang="..." href="..." />` in `<head>` |
| HTTP `Link:` header | RFC 8288 multi-value `Link: <url>; rel="alternate"; hreflang="..."` |
| XML sitemap `xhtml:link` | `<url><xhtml:link rel="alternate" hreflang="..." href="..."/></url>` |

The script detects the source automatically — pass either a page URL or a sitemap URL.

## Mesh check details

When the input page declares N alternates (excluding x-default and the self-ref), the script:

1. Fetches each alternate URL concurrently (semaphore-capped, default 5)
2. Re-extracts its hreflang set
3. For each declared A→B, checks B's set contains a return tag to A
4. Reports missing returns as critical findings

Skip with `--skip-mesh` when you want a quick validate without the per-alternate fetches (~N HTTP calls).

## Generation behavior

`build_corrected_set` takes the input page's declarations and:
- Adds `x-default` pointing to the input URL if missing
- Sorts (x-default last, otherwise alphabetical by lang code)
- Does NOT auto-correct invalid codes (`eng-GB` stays `eng-GB` — the report flags it; the user is expected to fix it manually)

This is deliberate: auto-correction is opinionated, and `en-uk` could legitimately mean either `en-GB` (likely) or `en` followed by an internal "uk" identifier (unlikely but possible).

## Python API

```python
import audit, asyncio

# Validate
result = asyncio.run(audit.run_validate("https://example.com", skip_mesh=False))
print(result.score(), len(result.findings))

# Extract a hreflang set from raw HTML
decls = audit.extract_from_html(html, base_url="https://example.com")

# Generate (3 formats)
audit.generate_html(decls)            # → "<link rel=\"alternate\" ..."
audit.generate_http_header(decls)     # → "Link: <url>; rel=\"alternate\"; ..."
audit.generate_sitemap(decls, page_url="https://example.com")
```

## Key reference tables

### ISO 639-1 common mistakes → suggestion

| Wrong | Correct |
|---|---|
| eng | en |
| fre / fra | fr |
| ger / deu | de |
| spa | es |
| jpn | ja |
| chi / zho | zh |
| jp | ja |
| cn | zh (cn is region, not language) |
| chinese / japanese / english | zh / ja / en |

### ISO 3166-1 common mistakes → suggestion

| Wrong | Correct |
|---|---|
| UK | GB |
| EU | (use individual states) |
| LA | (Latin America — use individual countries) |
| EN | (English is a language, not a country) |
| EL | GR (Greece) |

## Reference files

| File | Purpose |
|---|---|
| `references/cultural-profiles.md` | DACH, Francophone, Hispanic, Japanese cultural-adaptation checklists |
| `references/locale-formats.md` | Number, date, currency, address, phone format tables per locale |
| `references/content-parity.md` | Content parity audit methodology (section structure, word-count ratios) |

Qualitative — LLM-consulted during deeper localization analysis. Not driven by deterministic logic.

## Pyodide notes

- `fetches_urls: false` — the mesh-check fetches happen via the proxy
- Output detects Pyodide and routes to `/outputs/`

## Limitations

- **JavaScript-rendered hreflang missed.** Static-HTML reader.
- **Mesh check is the bottleneck.** N alternates = N HTTP requests. Use `--skip-mesh` during initial fix-up.
- **Cultural profiles are qualitative.** They flag risk patterns; they don't score localization quality.

## Related skills

- **`sitemap-audit`** — when generating hreflang sitemap, this skill validates the resulting tags
- **`ai-search-audit`** — page-level audit (Twitter Card, OG, canonical, etc. — single page rather than cross-page mesh)
