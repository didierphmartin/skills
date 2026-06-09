---
name: report-pdf
description: "Render synthesized report content (markdown or plain text) into a styled PDF. USE scripts/build.py when a workflow's terminal agent must deliver a PDF, or when the user asks for 'a PDF report'. Input: the report text/markdown. Output: a .pdf written to /outputs/. Do NOT use to author a workflow (that's workflow-compile) or to edit Office files (docx/pptx/xlsx skills)."
license: MIT
metadata:
  version: 0.1.0
  origin: synergyAI custom
dependencies: [fpdf2]
allowed-tools: run_skill_script
---

# report-pdf

Turn a synthesized report (markdown or plain text) into a clean, styled PDF.

## Runtime contract
- Call `run_skill_script` with `scripts/build.py`; only the written `.pdf` in `/outputs/` is the deliverable.
- `argv`: `["-i", "/scratch/report.md", "-o", "/outputs/<name>.pdf"]` (or `--title "..."`).
- `read_outputs`: `["/outputs/<name>.pdf"]`.

## What it renders
Markdown headings (`#`, `##`, `###`) become titled sections; `-`/`*` lines become bullets; blank
lines separate paragraphs. Inline `**bold**`/`*italic*` are honored. Everything else is body text.
