---
name: docx
description: "Create or edit Microsoft Word (.docx) documents. Use this skill whenever the user wants to GENERATE a new Word document (report, memo, letter, template, etc.) or modify text in an existing one (find/replace). Pyodide-native — no shell, no LibreOffice, no Node.js. READING / EXTRACTION is NOT this skill's job: when the user attaches a .docx, the platform-level attachment converter has already turned it into Markdown that's in your context. Cannot do PDF conversion or accept tracked changes; for those, the user needs Word/LibreOffice."
license: MIT
dependencies: python-docx
metadata:
  version: 2.0.0
  origin: synergyAI custom (writer-only — reads handled by platform attachment converter)
allowed-tools: run_skill_script
---

# DOCX skill (Pyodide-native, writer-only)

## CRITICAL — runtime contract (read first, no exceptions)

**You MUST call `run_skill_script` for every user request that involves CREATING or EDITING a .docx file.** This skill is non-functional without a tool call. Specifically:

- **Never claim a file was created or edited unless you have actually called `run_skill_script` and received a tool_result with `exit_code: 0`.** Hallucinating "I've created the memo, you can find it at ..." when you haven't called the tool wastes the user's time and leaves them looking for a file that doesn't exist.
- **Don't paraphrase the script's output as if you ran it.** If you didn't call the tool, you didn't run it.
- **One user intent → one or more script calls.** "Create a memo" → call `create.py`. "Find and replace in this docx" → call `edit.py`.
- **Do NOT try to "extract" a .docx with this skill.** When the user attaches a Word document, the frontend attachment converter has already converted it to Markdown and it's already in your message context. Read it directly. There is no `extract.py` here.
- **Don't ask the user clarifying questions you can answer yourself.** If they say "create a memo about Q3", make reasonable choices (Letter page, 1-inch margins, sensible headings) and produce the file. They can ask for revisions after.
- **The output path you pass via argv MUST appear in `read_outputs`** so the file is surfaced back. Forgetting this means the user sees no result even though the file is on disk.

If you cannot satisfy a request via these scripts (e.g. the user wants a PDF, or wants tracked changes accepted), say so plainly — don't fake it.

---

Write Microsoft Word documents in pure Python via `python-docx`. Two scripts cover the workflow:

| Goal | Script | Inputs | Outputs |
|------|--------|--------|---------|
| Create a new .docx from a structured spec | `scripts/create.py` | `.json` spec | `.docx` |
| Edit an existing .docx (find/replace) | `scripts/edit.py` | `.docx` + ops | `.docx` |

## When to use which script

- User asks for a new Word document (report, memo, letter, etc.) → **`create.py`** with a JSON spec describing structure.
- User attaches a Word doc and wants targeted text changes → **`edit.py`** with find/replace ops. The original file is at `/scratch/<filename>.docx`.
- User attaches a Word doc and asks for ANALYSIS or SUMMARY → don't call any script. The attachment is already in your context as Markdown; reason over that and respond in chat.

## What this skill CANNOT do

Be honest with the user about these — don't promise and fail:

- **Format conversion** to PDF (`docx → pdf` needs LibreOffice).
- **Accept or reject tracked changes** (needs LibreOffice/Word).
- **Render** a preview/screenshot of the document.
- **Charts, SmartArt, math equations** — `python-docx` doesn't model these. The skill will create them as plain placeholders if they appear in a spec.
- **Auto-populate Table of Contents at generation time** — `create.py` inserts the TOC field, but the user must open the doc in Word/LibreOffice and click "Update TOC" to populate it.
- **Read .docx files** — that's the platform converter's job, not yours. The user's attachment is already markdown in your context.

## scripts/create.py — build a new .docx from a spec

Takes a JSON spec describing the document (page setup, headers/footers, ordered list of elements). Writes a fully-formatted .docx.

```
python scripts/create.py -i /scratch/<spec>.json -o /outputs/<name>.docx
```

### Spec format (all fields optional except `elements`)

```json
{
  "metadata": {"title": "...", "author": "...", "subject": "...", "keywords": "..."},
  "page_setup": {
    "size": "Letter",                    // Letter | A4 | Legal | {"width_in":..., "height_in":...}
    "orientation": "portrait",           // portrait | landscape
    "margins": {"top_in":1, "bottom_in":1, "left_in":1, "right_in":1}
  },
  "header": {"text": "...", "page_numbers": false},
  "footer": {"text": "...", "page_numbers": true},
  "elements": [
    {"type": "heading", "level": 1, "text": "Document Title"},
    {"type": "paragraph", "text": "Plain paragraph."},
    {"type": "paragraph", "runs": [
      {"text": "This is "},
      {"text": "bold", "bold": true},
      {"text": " and "},
      {"text": "italic", "italic": true},
      {"text": " and "},
      {"text": "underlined", "underline": true},
      {"text": "."}
    ], "alignment": "left"},
    {"type": "list", "ordered": false, "items": ["First", "Second", "Third"]},
    {"type": "list", "ordered": true, "items": [
      "Step one",
      {"runs": [{"text": "Step "}, {"text": "two", "bold": true}]}
    ]},
    {"type": "table",
     "header": ["Column A", "Column B", "Column C"],
     "rows": [
       ["1", "2", "3"],
       ["4", "5", "6"]
     ]},
    {"type": "image", "path": "/scratch/diagram.png", "width_in": 4, "caption": "Figure 1"},
    {"type": "hyperlink", "text": "Visit example", "url": "https://example.com"},
    {"type": "horizontal_rule"},
    {"type": "page_break"},
    {"type": "toc", "title": "Table of Contents"}
  ]
}
```

### Run alignment values

`alignment`: `"left"` (default), `"center"`, `"right"`, `"justify"`.

### Run formatting fields

Inside `runs`: `text` (required), `bold`, `italic`, `underline`, `strike`, `font_name`, `font_size_pt`, `color_rgb` (hex like `"FF0000"`), `superscript`, `subscript`.

## scripts/edit.py — modify an existing .docx

Open an existing document, apply find/replace operations, save the result. Replacements are run-aware (handles cases where Word splits the search string across multiple `<w:r>` elements).

```
python scripts/edit.py -i /scratch/<input>.docx -o /outputs/<output>.docx \
       --ops /scratch/<ops>.json
```

### Ops file format

```json
{
  "find_replace": [
    {"find": "OLD TEXT", "replace": "NEW TEXT"},
    {"find": "{{name}}", "replace": "Alice", "case_sensitive": true}
  ]
}
```

## Output file paths

Every script writes to absolute paths under `/outputs/` so the result is persisted to the user's filesystem. ALWAYS include the output path in `read_outputs` when calling `run_skill_script` — that's how the rendered file shows up in the user's artifact pane.

## Examples

**Create a simple memo:**
```json
{
  "script": "scripts/create.py",
  "argv": ["-i", "/scratch/memo-spec.json", "-o", "/outputs/memo.docx"],
  "input_files": {
    "/scratch/memo-spec.json": "{\"metadata\":{\"title\":\"Q3 Memo\"},\"elements\":[{\"type\":\"heading\",\"level\":1,\"text\":\"Q3 Update\"},{\"type\":\"paragraph\",\"text\":\"Numbers are up.\"}]}"
  },
  "read_outputs": ["/outputs/memo.docx"]
}
```

**Find and replace placeholders in a template** (the user's template is already at `/scratch/<filename>.docx` from the attachment pre-write):
```json
{
  "script": "scripts/edit.py",
  "argv": ["-i", "/scratch/template.docx", "-o", "/outputs/filled.docx", "--ops", "/scratch/ops.json"],
  "input_files": {
    "/scratch/ops.json": "{\"find_replace\":[{\"find\":\"{{name}}\",\"replace\":\"Alice\"},{\"find\":\"{{date}}\",\"replace\":\"2026-05-08\"}]}"
  },
  "read_outputs": ["/outputs/filled.docx"]
}
```
