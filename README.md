# synergyAI Skills

A collection of folder-backed **skills** — self-contained capability bundles (a `SKILL.md` plus optional `scripts/`, `references/`, templates) that an AI assistant invokes by name when a skill's description matches the task.

## Skills

- **`copy-editing`** — When the user wants to edit, review, or improve existing marketing copy. Also use when the user mentions 'edit this copy,' 'review my copy,' 'copy fee
- **`copywriting`** — When the user wants to write, rewrite, or improve marketing copy for any page — including homepage, landing pages, pricing pages, feature pages, about
- **`docx`** — Create or edit Microsoft Word (.docx) documents. Use this skill whenever the user wants to GENERATE a new Word document (report, memo, letter, templat
- **`frontend-design`** — Create distinctive, production-grade frontend interfaces with high design quality. Use this skill when the user asks to build web components, pages, a
- **`html-to-markdown`** — Use this skill whenever the user wants to convert, transform, simplify, strip, distill, or reduce an HTML document (file, fragment, or saved page) int
- **`html`** — Generate or edit HTML documents (.html). USE scripts/create.py to write a NEW HTML document (styled report, landing page, document built to mirror an 
- **`hyperframes`** — Create video compositions, animations, title cards, overlays, captions, voiceovers, audio-reactive visuals, and scene transitions in HyperFrames HTML.
- **`linkedin-carousel`** — Generate professional LinkedIn carousel posts as PDF documents from text content. Use this skill whenever the user wants to create a LinkedIn carousel
- **`master-claude-for-legal`** — Use this skill whenever the user is doing legal work or planning to in SynergyAI — contracts, NDAs, redlines, privilege questions, attorney-client con
- **`medium-format`** — Use this skill whenever the user wants to convert, transform, prepare, or adapt an HTML and/or CSS file for Medium.com — including phrases like 'forma
- **`newspaper-layout`** — Broadsheet newspaper HTML layout with CSS design system, typography rules, editorial patterns, and multi-column grid.
- **`pptx`** — Create or edit PowerPoint (.pptx) files. Use this skill whenever the user wants to GENERATE a new presentation (pitch deck, slides, training, etc.) or
- **`schema-generate`** — Generate ready-to-paste JSON-LD structured-data markup for a web page. Use for: 'generate schema', 'add JSON-LD to my page', 'schema markup for [Artic
- **`skill-creator`** — Create new skills, modify and improve existing skills, and measure skill performance. Use when users want to create a skill from scratch, edit, or opt
- **`test-slow-update-warning`** — A test skill demonstrating missing SLOW_UPDATE markers.
- **`workflow-compile`** — Author a multi-agent workflow in a compact JSON form and compile it into the full Workflow DSL JSON the engine loads. USE scripts/compile.py when the 
- **`xlsx`** — Create or edit Excel (.xlsx, .xlsm) files. Use this skill whenever the user wants to GENERATE a new spreadsheet (report, model, table, etc.) or modify

## Layout
```
<skill>/
├── SKILL.md      # frontmatter (name, description, when-to-use) + instructions
├── scripts/      # optional executable scripts (sandboxed)
└── references/   # optional schemas / examples / supporting docs
```
