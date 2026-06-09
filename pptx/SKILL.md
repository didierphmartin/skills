---
name: pptx
description: "Create or edit PowerPoint (.pptx) files. Use this skill whenever the user wants to GENERATE a new presentation (pitch deck, slides, training, etc.) or modify text in an existing one (find/replace). Pyodide-native — no shell, no LibreOffice, no Node.js. READING / EXTRACTION is NOT this skill's job: when the user attaches a .pptx, the platform-level attachment converter has already turned it into Markdown that's in your context. Cannot generate slide thumbnails or PDF exports; for those, the user needs PowerPoint/LibreOffice."
license: MIT
dependencies: python-pptx
metadata:
  version: 2.0.0
  origin: synergyAI custom (writer-only — reads handled by platform attachment converter)
allowed-tools: run_skill_script
---

# PPTX skill (Pyodide-native, writer-only)

## CRITICAL — runtime contract (read first, no exceptions)

**You MUST call `run_skill_script` for every user request that involves CREATING or EDITING a .pptx file.** This skill is non-functional without a tool call. Specifically:

- **Never claim a deck was created or edited unless you have actually called `run_skill_script` and received a tool_result with `exit_code: 0`.** Hallucinating "I've created the deck, you can find it at ..." when you haven't called the tool wastes the user's time and leaves them looking for a file that doesn't exist.
- **Don't paraphrase the script's output as if you ran it.** If you didn't call the tool, you didn't run it.
- **One user intent → one or more script calls.** "Create a deck" → call `create.py`. "Find and replace in this deck" → call `edit.py`.
- **Do NOT try to "extract" a .pptx with this skill.** When the user attaches a deck, the frontend attachment converter has already converted it to Markdown and it's already in your message context. Read it directly. There is no `extract.py` here.
- **Don't ask clarifying questions you can answer yourself.** "Create a Q3 pitch deck" → make reasonable choices (5-7 slides, title + section + bullet layouts, default theme) and produce the file. Iterate after.
- **The output path you pass via argv MUST appear in `read_outputs`** so the file is surfaced back. Forgetting this means the user sees no result even though the file is on disk.

If you cannot satisfy a request via these scripts (PDF export, slide thumbnails, complex SmartArt), say so plainly — don't fake it.

---

Write PowerPoint presentations in pure Python via `python-pptx`. Two scripts cover the workflow:

| Goal | Script | Inputs | Outputs |
|------|--------|--------|---------|
| Create a new .pptx from a structured spec | `scripts/create.py` | `.json` spec | `.pptx` |
| Edit an existing .pptx (find/replace) | `scripts/edit.py` | `.pptx` + ops | `.pptx` |

## When to use which script

- User asks for a new deck (pitch, training, status report, etc.) → **`create.py`** with a JSON spec describing each slide.
- User attaches a deck and wants targeted text changes → **`edit.py`** with find/replace ops. The original file is at `/scratch/<filename>.pptx`.
- User attaches a deck and asks for ANALYSIS or SUMMARY → don't call any script. The attachment is already in your context as Markdown; reason over that and respond in chat.

## What this skill CANNOT do

Be honest with the user about these — don't promise and fail:

- **Generate slide thumbnails or preview images** (needs LibreOffice/PowerPoint to render).
- **Export to PDF** (needs LibreOffice/PowerPoint).
- **From-scratch decks with rich theme/branding** beyond python-pptx's default Office layouts. For brand-specific output, the user should provide a `.pptx` template; pass `--template` to `create.py` and we'll use its layouts/master.
- **Charts, SmartArt, math equations** — python-pptx has limited support; complex shapes may render as plain placeholders.
- **Slide transitions and animations** — not exposed by python-pptx.
- **Read .pptx files** — that's the platform converter's job, not yours.

## scripts/create.py — build a new .pptx from a spec

Takes a JSON spec describing each slide and produces a `.pptx`. Two modes:
- **Default mode** uses python-pptx's built-in default layouts (Office "Default Theme").
- **Template mode** opens a user-supplied .pptx template and reuses its layouts/master — best for brand-consistent output.

```
python scripts/create.py -i /scratch/<spec>.json -o /outputs/<name>.pptx
                          [--template /scratch/<template>.pptx]
```

### Spec format

```json
{
  "metadata": {"title": "...", "author": "...", "subject": "...", "keywords": "..."},
  "slide_size": "16:9",                 // "16:9" | "4:3" | {"width_in":13.33, "height_in":7.5}
  "slides": [
    {"layout": "title",
     "title": "Q3 Update",
     "subtitle": "Strong quarter, ahead of plan"},
    {"layout": "title_and_content",
     "title": "Highlights",
     "bullets": [
       "Revenue up 23% YoY",
       {"text": "Customer NPS reached 67", "level": 0},
       {"text": "Up from 58 last quarter", "level": 1}
     ]},
    {"layout": "section",
     "title": "Operations"},
    {"layout": "two_content",
     "title": "Wins vs. Risks",
     "left": ["3 enterprise deals", "Hired 12"],
     "right": ["Churn ticked up", "AWS cost +18%"]},
    {"layout": "title_only",
     "title": "Numbers don't lie."},
    {"layout": "blank"},
    {"layout": "title_and_content",
     "title": "Sales by region",
     "table": {
       "header": ["Region", "Q2", "Q3", "Δ"],
       "rows": [
         ["NA", "12,450", "15,300", "+22.9%"],
         ["EU", "8,720", "10,540", "+20.9%"],
         ["APAC", "6,890", "8,690", "+26.1%"]
       ]
     }},
    {"layout": "title_and_content",
     "title": "Architecture",
     "image": {"path": "/scratch/diagram.png", "width_in": 9, "left_in": 0.5, "top_in": 1.5}},
    {"layout": "title_and_content",
     "title": "Speaker notes example",
     "bullets": ["Visible bullet"],
     "notes": "Spoken-only context for the presenter."}
  ]
}
```

### Layouts supported

`title` (0), `title_and_content` (1), `section` (2), `two_content` (3), `comparison` (4), `title_only` (5), `blank` (6), `content_with_caption` (7), `picture_with_caption` (8). Any layout can also be referenced by its 0-based index if a template uses non-standard layout names.

### Per-slide fields

- `title`, `subtitle` — strings
- `bullets` — list of strings, OR list of `{text, level, bold, italic, underline, color_rgb, font_size_pt}`
- `left`, `right` — bullet lists for two_content / comparison layouts
- `table` — `{header: [...], rows: [[...], ...]}`
- `image` — `{path, width_in?, height_in?, left_in?, top_in?}` (positions default to centered)
- `notes` — speaker notes string
- `background_rgb` — solid background color hex (e.g. `"1F2937"`)

## scripts/edit.py — modify an existing .pptx

Open an existing deck, apply find/replace operations across all slide text frames, table cells, and speaker notes. Run-aware: handles cases where PowerPoint splits the search string across multiple `<a:r>` runs.

```
python scripts/edit.py -i /scratch/<input>.pptx -o /outputs/<output>.pptx \
       --ops /scratch/<ops>.json
```

### Ops file format

```json
{
  "find_replace": [
    {"find": "{{name}}", "replace": "Alice", "case_sensitive": true},
    {"find": "OLD COMPANY", "replace": "NewCo"}
  ]
}
```

## Output paths

Every script writes to absolute paths under `/outputs/`. ALWAYS include the output path in `read_outputs` when calling `run_skill_script` — that's how the file is persisted to the user's filesystem AND echoed back to you.

## Examples

**Create a 5-slide deck:**
```json
{
  "script": "scripts/create.py",
  "argv": ["-i", "/scratch/spec.json", "-o", "/outputs/q3-deck.pptx"],
  "input_files": {
    "/scratch/spec.json": "{\"slides\":[{\"layout\":\"title\",\"title\":\"Q3 Update\"},{\"layout\":\"title_and_content\",\"title\":\"Highlights\",\"bullets\":[\"Revenue up 23%\",\"NPS 67\"]}]}"
  },
  "read_outputs": ["/outputs/q3-deck.pptx"]
}
```

**Replace placeholders in a template deck** (the user's template is at `/scratch/<filename>.pptx` from the attachment pre-write):
```json
{
  "script": "scripts/edit.py",
  "argv": ["-i", "/scratch/template.pptx", "-o", "/outputs/filled.pptx", "--ops", "/scratch/ops.json"],
  "input_files": {
    "/scratch/ops.json": "{\"find_replace\":[{\"find\":\"{{client}}\",\"replace\":\"Acme Corp\"},{\"find\":\"{{date}}\",\"replace\":\"2026-05-08\"}]}"
  },
  "read_outputs": ["/outputs/filled.pptx"]
}
```
