---
name: markdown-report
description: Render a markdown document as a clean, conversation-style HTML page (deterministic — the layout comes from the render script, not from model-authored HTML)
dependencies: [markdown]
fetches_urls: false
---

# markdown-report

Renders markdown into a clean, readable HTML page styled like the platform's
conversation markdown rendering: light theme, comfortable typography, bordered
tables, styled code blocks and blockquotes. The LAYOUT IS FIXED by the render
script — this skill never authors HTML.

## Process (AUTHOR-FIRST)

This is an author-first skill. The "authoring" step is minimal by design:

1. Output the material's COMPLETE markdown as your deliverable — verbatim
   content, lightly cleaned only if needed (fix broken heading levels, remove
   tool noise). Do NOT write HTML. Do NOT summarize or shorten. Do NOT add
   commentary.
2. The runtime stages your markdown and the render script converts it:

```json
{ "script": "scripts/render.py", "argv": ["-i", "/scratch/<staged>.md", "-o", "/outputs/<name>.html", "--title", "<document title>"] }
```

The script output (the .html in /outputs/) is the deliverable.
