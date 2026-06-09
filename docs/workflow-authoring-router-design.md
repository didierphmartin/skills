# Workflow‑authoring router/planner — design sketch

**Problem.** Turning an implicit request (*"research metals, cryptos, stocks, finance and give me a
PDF"*) into a **correct parallel workflow** requires two reasoning inferences:

1. **Recognize** the request is workflow‑suitable (route).
2. **Author** the correct fan‑out/fan‑in graph (generate).

Both scale with model strength. If the **chat model** makes them, reliability is hostage to whatever
provider the user happens to be chatting with — a weak model (e.g. DeepSeek) misses #1 (never triggers
`workflow-compile`) or botches #2 (linear chain). Prompting can't fix a reasoning gap.

**Goal.** Make authoring reliable across *all* chat models, and *without* the user typing "workflow",
by **moving the inference off the chat model** onto something capable.

---

## Design: a planner pass in front of normal chat

```
user message
     │
     ▼
┌──────────────────────────┐   not a workflow
│  ROUTER  (cheap/fixed)   │──────────────▶ normal chat (any provider)
│  is this a multi-source  │
│  / parallel / pipeline   │   looks like a workflow
│  task?                   │──────────────┐
└──────────────────────────┘              ▼
                            ┌───────────────────────────────────┐
                            │  PLANNER  (capable model, gated)  │
                            │  authors the .simple.json via      │
                            │  workflow-compile (fan-out edges)  │
                            └───────────────────────────────────┘
                                          │
                                          ▼  compile.py → DSL
                                  "Open in editor" / run
```

### 1. Router — recognize (cheap, model‑independent)
Sits at the **top of the chat pipeline** (gpt backend `ChatController`), before normal handling.
Decides *workflow vs. plain chat*. Implementation options, increasingly robust:

- **Heuristic pre‑filter** (zero‑LLM): flag messages that combine **multiple topics/sources** with a
  **merge/produce verb** — patterns like *"… and … (and …)"*, *"in parallel"*, *"compare/combine/
  synthesize/summarize/report/PDF"*, *"from several sources"*, *"monitor N feeds"*. Fast, free, but
  coarse.
- **Tiny classifier LLM** (one cheap call on a *capable* model) that returns
  `{is_workflow: bool, confidence}` — the heuristic gates *whether* to spend this call.
- **Hybrid (recommended):** heuristic narrows candidates → one classifier call confirms. The chat
  model is never the decider, so a DeepSeek‑driven chat still gets routed correctly.

### 2. Planner — author (capable model, gated)
If routed in, the **authoring runs on a capable provider** — *not* the chat model. Gate via a
per‑provider flag (e.g. `system_llm_settings.can_author_workflows`): pick the best available author
(Claude / GPT / Gemini). The planner invokes `workflow-compile` to emit the `.simple.json`
(fan‑out edges for parallel sources), then `compile.py` validates → DSL.

### 3. Execution — any provider
The authored agent **nodes** can run on any provider (DeepSeek/Kimi/etc.). Recognizing + designing the
graph is the hard part and is now off the cheap models; running one agent's instructions is easy.

---

## Why this works
The chat/execution model is no longer the one *inferring* "build a workflow." A **fixed router** (and
a **capable‑model planner**) make the call, so the behavior is consistent regardless of the chat
provider, and the user never needs the word "workflow."

## Tradeoffs / guards
- **False positives** (auto‑building a workflow when the user just wanted an answer): guard with a
  **confirm step** — *"This looks like a multi‑step task — build it as a workflow?"* — or a confidence
  threshold below which it stays plain chat.
- **Latency/cost:** the heuristic pre‑filter keeps the extra classifier call off most messages.
- **Author availability:** if no `can_author_workflows` provider is configured, fall back to the chat
  model (best effort) and surface a note.

## Minimal v1
1. Heuristic router in `ChatController` (regex/keyword patterns above).
2. One capable‑model planner pass behind a confirm, invoking `workflow-compile`.
3. The strengthened `workflow-compile` description (done) handles the *model‑based* triggering for
   capable chat models even before this router exists.

This is a **gpt‑backend** architecture change (the router/planner live in the chat pipeline); the
skill side is already in place.
