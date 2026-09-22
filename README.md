# synergyAI Skills

A collection of folder-backed **skills** — self-contained capability bundles an AI assistant invokes by name when a skill's description matches the task. Each skill is a `SKILL.md` (frontmatter + instructions) plus optional `scripts/`, `references/`, and — for skills with deeper docs — a `README.md`.

## Specification

These skills follow Anthropic's **[Agent Skills](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)** specification, so they work anywhere that spec is supported — Claude Code, claude.ai, the Claude API — not only in SynergyAI. A skill is a directory whose one required file is `SKILL.md`: YAML frontmatter with `name` and `description`, followed by the instructions. `name` is at most 64 characters of lowercase letters, numbers and hyphens; `description` is at most 1024 characters and must say **both what the skill does and when to use it**, because that sentence is what the model matches a request against when deciding to load it.

The design point behind the layout below is **progressive disclosure**. Only the frontmatter is loaded up front (~100 tokens per skill), so a large collection costs almost nothing until something is actually needed. The body of `SKILL.md` enters the context window only when the skill triggers; bundled `references/` files only when read; and `scripts/` never do — they run as subprocesses and only their output is seen. That is why a skill can ship extensive reference material and heavy scripts without penalty, and why deterministic work belongs in a script rather than in prose the model has to re-derive.

Full details — authoring guidance, the runtime constraints per surface, and the security considerations for skills from untrusted sources — are in the [official documentation](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview).

## Skills

- **`GEO`** _(collection)_ — sub-skills: geo-audit geo-brand-mentions geo-citability geo-compare geo-content geo-crawlers geo-llmstxt geo-platform-optimizer geo-proposal geo-prospect geo-report geo-report-pdf geo-schema geo-update 
- **`SEO`** _(collection)_ — sub-skills: ai-search-audit geo-technical hreflang-audit image-audit seo-audit seo-competitor-pages seo-content-brief seo-plan seo-programmatic seo-sxo serp-shape sitemap-audit topic-cluster 
- **[`copy-editing`](copy-editing/SKILL.md)** — When the user wants to edit, review, or improve existing marketing copy. Also use when the user mentions 'edit this copy,' 'review my copy,' 'copy fee
- **[`copywriting`](copywriting/SKILL.md)** — When the user wants to write, rewrite, or improve marketing copy for any page — including homepage, landing pages, pricing pages, feature pages, about
- **[`docx`](docx/SKILL.md)** — Create or edit Microsoft Word (.docx) documents. Use this skill whenever the user wants to GENERATE a new Word document (report, memo, letter, templat
- **[`gold-silver-price-report`](gold-silver-price-report/SKILL.md)** — Multi-agent workflow to research and compile a comprehensive report on gold and silver prices using market news and financial data
- **[`frontend-design`](frontend-design/SKILL.md)** — Create distinctive, production-grade frontend interfaces with high design quality. Use this skill when the user asks to build web components, pages, a
- **[`html-to-markdown`](html-to-markdown/SKILL.md)** — Use this skill whenever the user wants to convert, transform, simplify, strip, distill, or reduce an HTML document (file, fragment, or saved page) int
- **[`html`](html/SKILL.md)** — Generate or edit HTML documents (.html). USE scripts/create.py to write a NEW HTML document (styled report, landing page, document built to mirror an 
- **[`hyperframes`](hyperframes/SKILL.md)** — Create video compositions, animations, title cards, overlays, captions, voiceovers, audio-reactive visuals, and scene transitions in HyperFrames HTML.
- **[`linkedin-carousel`](linkedin-carousel/SKILL.md)** — Generate professional LinkedIn carousel posts as PDF documents from text content. Use this skill whenever the user wants to create a LinkedIn carousel
- **[`markdown-report`](markdown-report/SKILL.md)** — Render a markdown document as a clean, conversation-style HTML page (deterministic — the layout comes from the render script, not from model-authored
- **[`medium-format`](medium-format/SKILL.md)** — Use this skill whenever the user wants to convert, transform, prepare, or adapt an HTML and/or CSS file for Medium.com — including phrases like 'forma
- **[`newspaper-layout`](newspaper-layout/SKILL.md)** — Broadsheet newspaper HTML layout with CSS design system, typography rules, editorial patterns, and multi-column grid.
- **[`playbook-author`](playbook-author/SKILL.md)** · [📖 docs](playbook-author/README.md) — Write a Console-style ITSM PLAYBOOK from a request and save it as a Playbook agent the user can drag into a workflow. USE THIS whenever the user asks
- **[`pptx`](pptx/SKILL.md)** — Create or edit PowerPoint (.pptx) files. Use this skill whenever the user wants to GENERATE a new presentation (pitch deck, slides, training, etc.) or
- **[`report-pdf`](report-pdf/SKILL.md)** — Render synthesized report content (markdown or plain text) into a styled PDF. USE scripts/build.py when a workflow's terminal agent must deliver a PDF
- **[`schema-generate`](schema-generate/SKILL.md)** · [📖 docs](schema-generate/README.md) — Generate ready-to-paste JSON-LD structured-data markup for a web page. Use for: 'generate schema', 'add JSON-LD to my page', 'schema markup for [Artic
- **[`skill-creator`](skill-creator/SKILL.md)** — Create new skills, modify and improve existing skills, and measure skill performance. Use when users want to create a skill from scratch, edit, or opt
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
