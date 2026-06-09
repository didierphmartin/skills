# GEO Skills Bundle

> 💡 **You're seeing this because it's the first time you opened this folder.**
> To bring this document back later, click the 📖 icon next to the folder name
> in the sidebar (highlighted below):
>
> ![Where to find the README icon in the sidebar](readme-icon-hint.png)
>
> The auto-open only happens once per folder — after that, the 📖 button is
> your shortcut.

---

**Generative Engine Optimization (GEO)** is the discipline of making a page
discoverable, citable, and accurately summarized by AI answer engines —
Google AI Overviews, ChatGPT, Perplexity, Gemini, Bing Copilot — as opposed
to classic keyword-ranking in the blue-link results.

GEO is **not** a refinement of SEO. The two share some on-page foundations
(structured content, schema, fast SSR-rendered HTML) but diverge sharply on
which crawlers must succeed (AI bots don't execute JavaScript), which signals
matter (direct-answer paragraphs, FAQ sections, entity links to Wikipedia /
Wikidata), and how citation works (only **~11 %** of domains are cited by
both ChatGPT and Google AI Overviews for the same query).

These skills are **composable** — each produces structured output the next one
can consume, so a full GEO program can be assembled in any order. The
suggested workflow at the bottom is one such chain; pick individual skills if
you only need a focused diagnostic.

---

## 🎯 Orchestrator

| Skill | What it does |
|---|---|
| **geo-audit** | The single-call front door. Detects business type, fans out to every GEO sub-skill (crawlers / technical / content / citability / platform / schema / llmstxt), then consolidates into one client report. No script of its own — pure LLM-driven sequence. |

## 🔍 On-page Audits

| Skill | What it does |
|---|---|
| **geo-content** | Scores E-E-A-T (Experience, Expertise, Authoritativeness, Trustworthiness) and content quality for AI citability. |
| **geo-citability** | Scores how likely AI engines (ChatGPT, Claude, Perplexity, Gemini) are to cite or quote a page — and emits specific rewrite suggestions to lift the score. |
| **geo-platform-optimizer** | Per-platform scoring (Google AI Overviews, ChatGPT, Perplexity, Gemini, Bing Copilot). Same page, five separate rubrics, prioritized action plan. |
| **geo-schema** | Schema.org structured-data audit — JSON-LD / Microdata / RDFa detection, type-specific validation, deprecated @types, `sameAs` coverage across 14 entity platforms, `speakable` + `knowsAbout` for AI signals. |

## 🌐 Off-page & Discoverability

| Skill | What it does |
|---|---|
| **geo-crawlers** | Which AI crawlers (GPTBot, ClaudeBot, PerplexityBot, …) can reach the site, and how much of it they can see. |
| **geo-llmstxt** | Analyze or generate `/llms.txt` — the AI-discovery file standard (Jeremy Howard, Sept 2024). Validates an existing file or writes a fresh one. |
| **geo-brand-mentions** | Brand-authority audit across the platforms AI systems use for entity recognition: YouTube, Reddit, Wikipedia, LinkedIn, Quora, Stack Overflow, GitHub, News. |

## 📊 Reporting & Tracking

| Skill | What it does |
|---|---|
| **geo-report** | Aggregates every GEO audit under `~/Documents/synergyAI/outputs/` into a single client-ready GEO Readiness Report. |
| **geo-compare** | Baseline-vs-current delta tracking. Diffs two audit reports and produces a "here's your progress" report for monthly client updates. |

## 💼 Agency Workflow

| Skill | What it does |
|---|---|
| **geo-proposal** | Generates a client-ready GEO service proposal from existing audit data — pricing tiers, ROI projection, engagement timeline, terms. |
| **geo-prospect** | CRM-lite for the sales pipeline: Lead → Qualified → Proposal → Won / Lost, tied to audit + proposal artefacts. |

## 🔗 Cross-bundle dependency

The `geo-audit` orchestrator also chains **`geo-technical`** which currently
lives in the **SEO** bundle (`skills/SEO/geo-technical`). It hasn't been moved
here yet — be aware if you move things around that the orchestrator's
implicit path-resolution still finds it.

---

## 🔗 Suggested Workflow

The orchestrator-led full audit:

> **geo-crawlers → geo-technical → geo-content → geo-citability →
> geo-platform-optimizer → geo-schema → geo-llmstxt → geo-brand-mentions →
> geo-report**

→ [Open the GEO Full-Audit workflow](workflow://geo-full-audit)

For the agency lifecycle: chain **geo-audit → geo-report → geo-proposal →
geo-prospect** to take a single URL all the way from "first contact" to
"signed engagement" with a paper trail.

> Replace the workflow link target above with the actual URL your
> `workflow-editor` exposes once a workflow is saved that uses these skills.

---

*This README lives at `skills/GEO/README.md`. Edit it freely — the sidebar's
📖 button picks up changes on the next page reload.*
