# synergyAI Skills

A collection of folder-backed **skills** — self-contained capability bundles an AI assistant invokes by name when a skill's description matches the task. Each skill is a `SKILL.md` (frontmatter + instructions) plus optional `scripts/`, `references/`, and — for skills with deeper docs — a `README.md`.

## Skills

- **`GEO`** _(collection)_ — sub-skills: geo-audit geo-brand-mentions geo-citability geo-compare geo-content geo-crawlers geo-llmstxt geo-platform-optimizer geo-proposal geo-prospect geo-report-pdf geo-report geo-schema geo-update 
- **`SEO`** _(collection)_ — sub-skills: ai-search-audit geo-technical hreflang-audit image-audit seo-audit seo-competitor-pages seo-content-brief seo-plan seo-programmatic seo-sxo serp-shape sitemap-audit topic-cluster 
- **[`copy-editing`](copy-editing/SKILL.md)** — When the user wants to edit, review, or improve existing marketing copy. Also use when the user mentions 'edit this copy,' 'review my copy,' 'copy fee
- **[`copywriting`](copywriting/SKILL.md)** — When the user wants to write, rewrite, or improve marketing copy for any page — including homepage, landing pages, pricing pages, feature pages, about
- **[`docx`](docx/SKILL.md)** — Create or edit Microsoft Word (.docx) documents. Use this skill whenever the user wants to GENERATE a new Word document (report, memo, letter, templat
- **[`frontend-design`](frontend-design/SKILL.md)** — Create distinctive, production-grade frontend interfaces with high design quality. Use this skill when the user asks to build web components, pages, a
- **[`html-to-markdown`](html-to-markdown/SKILL.md)** — Use this skill whenever the user wants to convert, transform, simplify, strip, distill, or reduce an HTML document (file, fragment, or saved page) int
- **[`html`](html/SKILL.md)** — Generate or edit HTML documents (.html). USE scripts/create.py to write a NEW HTML document (styled report, landing page, document built to mirror an 
- **[`hyperframes`](hyperframes/SKILL.md)** — Create video compositions, animations, title cards, overlays, captions, voiceovers, audio-reactive visuals, and scene transitions in HyperFrames HTML.
- **[`linkedin-carousel`](linkedin-carousel/SKILL.md)** — Generate professional LinkedIn carousel posts as PDF documents from text content. Use this skill whenever the user wants to create a LinkedIn carousel
- **[`master-claude-for-legal`](master-claude-for-legal/SKILL.md)** · [📖 docs](master-claude-for-legal/README.md) — Use this skill whenever the user is doing legal work or planning to in SynergyAI — contracts, NDAs, redlines, privilege questions, attorney-client con
- **[`medium-format`](medium-format/SKILL.md)** — Use this skill whenever the user wants to convert, transform, prepare, or adapt an HTML and/or CSS file for Medium.com — including phrases like 'forma
- **[`newspaper-layout`](newspaper-layout/SKILL.md)** — Broadsheet newspaper HTML layout with CSS design system, typography rules, editorial patterns, and multi-column grid.
- **[`pptx`](pptx/SKILL.md)** — Create or edit PowerPoint (.pptx) files. Use this skill whenever the user wants to GENERATE a new presentation (pitch deck, slides, training, etc.) or
- **[`schema-generate`](schema-generate/SKILL.md)** · [📖 docs](schema-generate/README.md) — Generate ready-to-paste JSON-LD structured-data markup for a web page. Use for: 'generate schema', 'add JSON-LD to my page', 'schema markup for [Artic
- **[`skill-creator`](skill-creator/SKILL.md)** — Create new skills, modify and improve existing skills, and measure skill performance. Use when users want to create a skill from scratch, edit, or opt
- **[`test-slow-update-warning`](test-slow-update-warning/SKILL.md)** — A test skill demonstrating missing SLOW_UPDATE markers.
- **[`workflow-compile`](workflow-compile/SKILL.md)** · [📖 docs](workflow-compile/README.md) — Author a multi-agent workflow in a compact JSON form and compile it into the full Workflow DSL JSON the engine loads. USE scripts/compile.py when the 
- **[`xlsx`](xlsx/SKILL.md)** — Create or edit Excel (.xlsx, .xlsm) files. Use this skill whenever the user wants to GENERATE a new spreadsheet (report, model, table, etc.) or modify

## Documentation

Each skill is documented inline in its `SKILL.md`. Skills with richer behavior also ship a `README.md` — see **[`workflow-compile`](workflow-compile/README.md)** for a worked example documenting the **simple language**, the **graphical DSL**, and the **compilation** between them.

## Layout
```
<skill>/
├── SKILL.md      # frontmatter (name, description, when-to-use) + instructions
├── README.md     # optional — deeper documentation
├── scripts/      # optional executable scripts (sandboxed)
└── references/   # optional schemas / examples / supporting docs
```
