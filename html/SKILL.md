---
name: html
description: "Generate or edit HTML documents (.html). USE scripts/create.py to write a NEW HTML document (styled report, landing page, document built to mirror an attached template, fresh page from prompt content). USE scripts/edit.py for SURGICAL EDITS to an attached HTML — fix references, replace a specific section, update placeholders, swap a header — instead of regenerating the whole document (which is slower and tends to drift on untouched parts). Verbs that map to edit.py: fix, replace, update, correct, swap, change. Verbs that map to create.py: generate, create, make, build a new. Both write to /outputs/. HTML attachments are already in your message context as text — read them directly to find selectors. Office attachments (DOCX/PPTX/XLSX) are not in context here; for those, use the matching format skill. Do NOT use for converting HTML to Markdown (use html-to-markdown for that)."
license: MIT
dependencies: beautifulsoup4
metadata:
  version: 2.1.0
  origin: synergyAI custom
allowed-tools: run_skill_script
---

# HTML skill (Pyodide-native)

## CRITICAL — runtime contract (read first, no exceptions)

**You MUST call `run_skill_script` for every user request that involves producing a .html file.** This skill is non-functional without a tool call. Specifically:

- **Never claim a file was created unless you have actually called `run_skill_script` and received a tool_result with `exit_code: 0`.** Hallucinating "I've generated the page, you can find it at ..." when you haven't called the tool wastes the user's time.
- **Don't paraphrase the script's output as if you ran it.** If you didn't call the tool, you didn't run it.
- **Do NOT inline raw HTML into the chat as the deliverable.** Inline HTML breaks the right-pane preview and risks mid-stream truncation on long output. Always route the final HTML through `create.py` so it lands in `/outputs/`.
- **Don't ask clarifying questions you can answer yourself.** If the user says "make me an HTML page about X styled like the attached page", make reasonable choices and produce the file. They can iterate after.
- **The output path you pass via argv MUST appear in `read_outputs`** so the file is surfaced back. Forgetting this means the user sees no result even though the file is on disk.

If you cannot satisfy a request via this script (e.g. the user wants a PDF render or a screenshot), say so plainly — don't fake it.

---

## How this skill works

Two scripts cover the workflow:

| Goal | Script | Inputs | Outputs |
|------|--------|--------|---------|
| Write a new .html from scratch | `scripts/create.py` | the HTML you author | `.html` in `/outputs/` |
| Surgically edit an existing .html (find/replace + selector swap) | `scripts/edit.py` | source `.html` + ops JSON | edited `.html` in `/outputs/` |

**You compose the HTML yourself** by reading:
1. The user's prompt — what they want the new document to say / be about.
2. **Attached `.html` files** — these are pre-prepended to the user's message as text, so you can read the source HTML directly from your context (CSS, structure, prose, all of it).

Then you call `create.py` with the complete HTML — or `edit.py` if you only need to swap specific sections of an attached file.

### Attachment format matters

| Attachment | What you see in context | What to do |
|---|---|---|
| `.html` | The full HTML source as text. | Read it directly; reuse CSS / structure / content per the user's intent (see decision matrix below). |
| `.docx`, `.pptx`, `.xlsx` | **Nothing** — these are binary, sitting at `/scratch/<file>` but not readable as text in your context. The `script` enum on this skill does not include the docx/pptx/xlsx extract scripts, so you can't reach them from here. | Tell the user plainly: "I can't read the contents of `<file>` from this skill. Activate the `docx` (or `pptx`/`xlsx`) skill first, run its `extract.py` to get the content as markdown, then come back to the `html` skill with that markdown in the prompt." Don't fabricate the contents. |
| `.pdf`, images | Same as Office formats — not in your context here. | Either ask the user to convert, or decline. |

This skill's contract is strictly: **HTML attachment → new HTML**, or **prompt-only → new HTML**. Cross-format pipelines are a two-skill workflow today.

## Decide how to use the attached file

The user can attach a file with several different intents. Read the prompt to decide which:

| User says (paraphrased) | What to do |
|---|---|
| "Styled like this", "use this as a template", "in the same look", "match this design" | The attachment is a **style/structure template**. Copy its inline `<style>` blocks verbatim into your output's `<head>`. Mirror its tag hierarchy and class names. **Write entirely new text from the user's prompt** — do NOT paste the template's headings, paragraphs, or section titles into the new document. |
| "Summarize this", "extract key points", "make a clean version", "translate this" | The attachment is the **content source**. The user wants the same information re-presented. Style is your call (sensible defaults are fine unless they specify). |
| "Continue / extend / incorporate facts from this", "use these details" | The attachment is a **content reference**. Pull facts/quotes from it, mix with the user's prompt, style is your call. |
| "Build me X about Y" with a template-shaped attachment, no clear instruction | Default to **template mode** (style + structure from attachment, content from prompt). It's the most common intent. |

**The cardinal rule for template mode:** the visual feel comes from the attachment, the words come from the user's prompt. If your output's prose matches the template's prose, you got the modes confused — back up and re-read the user's request.

## When to use `create.py` vs `edit.py`

- User wants a **new** document, even if styled like an attached template → `create.py`. Author the full HTML, pass it through.
- User wants to **change specific parts** of an attached HTML (fix a section, swap a list, replace a header, correct references) → `edit.py`. Cheaper, safer, and avoids regenerating untouched content (which is where Claude tends to drift / drop content).

When the user says "fix X in this document", "replace these references", "update the header", default to `edit.py`. When they say "make a new document about Y", default to `create.py`.

## scripts/create.py — write the HTML

```
python scripts/create.py -i /scratch/<name>.html -o /outputs/<name>.html
                         [--pretty]      # reformat with BeautifulSoup.prettify()
                         [--fragment]    # allow a body fragment without <html>/<head> (skips the wrap warning)
```

- HTML should be a complete document (`<!doctype html>` + `<html>` + `<head>` + `<body>`) unless the user explicitly asked for a fragment.
- `--pretty` runs `BeautifulSoup.prettify()` before writing — useful for human-editable output, but note it can subtly alter whitespace inside `<pre>` and `<textarea>`.
- The script validates that the input parses as HTML and warns about missing `<!doctype>`, `<html>`, `<head>`, or `<body>` (unless `--fragment`).

## Example call

The user attaches `template.html` and writes "make me a page about quarterly results, styled like the attached page."

You read the attachment from the message context, see its CSS and structure, compose new HTML using that CSS verbatim plus your own quarterly-results content with the template's class names, then:

```json
{
  "script": "scripts/create.py",
  "argv": ["-i", "/scratch/q3.html", "-o", "/outputs/q3.html"],
  "input_files": {
    "/scratch/q3.html": "<!doctype html><html><head><style>/* CSS copied from template */</style></head><body>/* Q3 content with template's class names */</body></html>"
  },
  "read_outputs": ["/outputs/q3.html"]
}
```

The right pane renders `/outputs/q3.html`; the left pane carries your narration.

## scripts/edit.py — surgically edit an existing HTML

Use this when the user wants targeted changes to an attached HTML file rather than a full regeneration. Two op families, applied in declared order:

```
python scripts/edit.py -i /scratch/<input>.html -o /outputs/<output>.html \
       --ops /scratch/<ops>.json
```

### Ops file shape

```json
{
  "find_replace": [
    {"find": "{{client}}", "replace": "Acme Corp", "case_sensitive": true},
    {"find": "old phrase", "replace": "new phrase"}
  ],
  "replace_selector": [
    {"selector": "header h1", "html": "Updated Title", "mode": "inner"},
    {"selector": "div.references", "html": "<div class=\"references\"><p>Real ref A</p><p>Real ref B</p></div>", "mode": "outer"}
  ]
}
```

- **`find_replace`** — exact string find/replace on the raw HTML source. Use for placeholders or fixed phrases the user wants swapped verbatim. `case_sensitive` defaults to true.
- **`replace_selector`** — BeautifulSoup CSS-selector replacement. Use for structural edits ("replace the references block", "update the title text"). `mode` is `"outer"` (replace the whole element, default) or `"inner"` (replace only the contents, keep the wrapper tag).

### Tips

- Prefer `replace_selector` over giant `find_replace` blocks for HTML — selectors don't break when whitespace or attribute order changes.
- For "replace section X with this new content", use `mode: "outer"` and include the wrapper tag in the replacement HTML so structure stays intact.
- For "change the title text" or "update the timestamp shown in `<time>`", use `mode: "inner"` so the wrapper stays.
- The script warns when a selector matches nothing or a `find_replace` finds zero hits — read stderr to confirm your ops actually fired.

### Failure mode the user just hit (and why edit.py exists)

When a user attached a generated HTML and asked the model to "replace hallucinated references with these real ones," `create.py` regenerated the whole document — and the model silently kept the stale references because it pulled them from the attachment context instead of the new list. With `edit.py` the model can target the references block directly via a selector, replace it with the new HTML, and leave the rest of the document untouched. That's the right tool for "fix X" requests.

## What this skill CANNOT do

Be honest with the user — don't promise and fail:

- **Render the HTML to a screenshot or PDF** (needs a headless browser, not available in Pyodide).
- **Convert HTML to Markdown** — that's the `html-to-markdown` skill's job.
- **Faithfully reproduce JavaScript-rendered content** in attachments — only static HTML in the attachment text is visible.

## Output file paths

Both `create.py` and `edit.py` write to absolute paths under `/outputs/`. ALWAYS include the output path in `read_outputs` when calling `run_skill_script` — that's how the rendered file shows up in the user's artifact pane.

## Examples

**Replace the references section in an attached document** (the case that motivated `edit.py`):

```json
{
  "script": "scripts/edit.py",
  "argv": ["-i", "/scratch/synergyai-workflow.html", "-o", "/outputs/synergyai-workflow.html", "--ops", "/scratch/ops.json"],
  "input_files": {
    "/scratch/ops.json": "{\"replace_selector\":[{\"selector\":\"div.references\",\"html\":\"<div class=\\\"references\\\"><p>Real ref A</p><p>Real ref B</p></div>\",\"mode\":\"outer\"}]}"
  },
  "read_outputs": ["/outputs/synergyai-workflow.html"]
}
```

**Update placeholders in an HTML template:**

```json
{
  "script": "scripts/edit.py",
  "argv": ["-i", "/scratch/template.html", "-o", "/outputs/filled.html", "--ops", "/scratch/ops.json"],
  "input_files": {
    "/scratch/ops.json": "{\"find_replace\":[{\"find\":\"{{client}}\",\"replace\":\"Acme Corp\"},{\"find\":\"{{date}}\",\"replace\":\"2026-05-08\"}]}"
  },
  "read_outputs": ["/outputs/filled.html"]
}
```
