# skill-creator vs. SkillOpt Paper — Where We Stand

A side-by-side map of **what the SkillOpt paper proposes** vs. **what our implementation does**, so we can see at a glance how close we are to the published algorithm and where the gaps are.

- **Paper**: *SKILLOPT: Executive Strategy for Self-Evolving Agent Skills* (Microsoft, May 2026). arXiv: 2605.23904v2.
- **Reference code**: `~/Documents/SkillOpt/` (Microsoft's reference impl). Authors' own docs at `~/Documents/SkillOpt/docs/guide/` mirror the paper's §3 method exactly — those are the source for this comparison.
- **Our impl**: `skills/skill-creator/scripts/` (`run_loop.py`, `run_body_loop.py`, `improve_body.py`, `improve_description.py`, `run_body_eval.py`, `run_eval.py`, `patch.py`) + the gpt-frontend SkillOpt panel at `gpt/frontend/assets/js/skillopt-panel.js`.

Last updated: 2026-05-31. Roadmap cross-references point to items in `ROADMAP.md` §3.

Status legend: ✅ implemented · ⚠️ partial / plumbing only · ❌ not implemented.

---

## 1. Per-step (the inner loop)

The 6-stage cycle that runs once per "step" inside an epoch.

| # | SkillOpt stage | Our equivalent | Status | Notes |
|---|---|---|---|---|
| 1 | **Rollout** — target executes tasks using current skill | `_capture_transcript` in `run_body_eval.py` (POSTs to `/api/v1/chat`) | ✅ | Single rollout per case unless `runs_per_query > 1`. |
| 2a | **Reflect — shallow** (per-trajectory analysis) | n/a | ❌ | We always reflect over all failures together. |
| 2b | **Reflect — deep** (cross-reference failures for systemic issues) | `improve_body.py` + `agents/body_analyzer.md` | ✅ | Implicitly: all failure patterns passed in one prompt. |
| 2c | `analyst_workers` (parallel reflection workers; paper default = 16) | n/a | ❌ | One analyst per iteration. |
| 2d | `max_analyst_rounds` (multi-round analyst reflection; paper default = 3) | n/a | ❌ | Analyst gets one shot per iteration. **Related to ROADMAP §1.1 target-drift.** |
| 2e | `minibatch_size` (subset of trajectories per reflect step) | n/a | ❌ | Full eval set every time. |
| 2f | `failure_only` (reflect only on failures) | always-on | ✅ | Hardcoded for body analyst; the paper makes it configurable. |
| 3 | **Aggregate** — semantic merge of similar edits | n/a | ❌ | No deduplication; an analyst proposing two near-duplicate edits gets both clipped against `lr_budget`. |
| 4 | **Select** — rank edits by relevance, cap by `learning_rate` | `apply_patch_to_body(..., lr_budget=...)` | ✅ | Same concept. Our `lr_budget` ≡ paper's `learning_rate`. |
| 4b | `lr_scheduler` — cosine / linear / constant / autonomous | n/a (effectively constant) | ❌ | Paper finds **cosine > constant**. **Roadmap candidate.** |
| 5 | **Update** — apply selected edits to skill doc | `apply_patch` (4 atomic ops: append, insert_after, replace, delete) | ✅ | Identical primitive set to the paper. |
| 6 | **Gate** — validation accept/reject | strict tie-tolerant gate (`improvement >= 0 AND no per-case regression`) in `run_body_loop.py` | ✅ | Implements §3.5 monotonic-non-decrease. |

---

## 2. Per-epoch (boundary mechanisms)

These run once at the boundary between epochs. **This is where the paper's biggest cross-run gains come from.**

| SkillOpt mechanism | Our equivalent | Status |
|---|---|---|
| **Epoch concept** — outer loop of `num_epochs × steps_per_epoch` | n/a — we have flat iterations | ❌ |
| **Slow Update** — at each epoch boundary, roll out both previous-epoch and current skill on the same samples; categorize items as improved / regressed / persistent-fail / stable-success; generate high-level guidance and inject into skill | `<!-- SLOW_UPDATE_START / END -->` markers reserved in body; protected by `apply_patch_to_body`; **nothing writes to them** | ⚠️ Plumbing reserved; writer is **Phase 2C**. |
| **Meta Skill** — persistent strategy memory across epochs, fed back to analyst on every reflect | n/a | ❌ Deferred (Phase 2C). |

---

## 3. Splits & evaluation

| SkillOpt | Ours | Status |
|---|---|---|
| Train / Selection / Test split (paper default ratio: 2:1:7) | Description loop: 60/40 train/test split. **Body loop has no split** — full eval set used for both proposing and gating. | ⚠️ Body loop conflates validation + test → overfitting risk once the loop starts producing real cumulative edits. |
| Task evaluator (single scalar loss) | Hybrid: regex `must_contain` / `must_not_contain` hard-gate + LLM judge soft score (1–5) | ✅ (richer than paper) |
| `eval_test` — final test eval after training | implicit in returned `best_score`; no separate test-eval pass | ⚠️ |

---

## 4. Models

The paper distinguishes three model roles. We collapse them to one.

| SkillOpt role | Ours | Status |
|---|---|---|
| `optimizer` — reflects on failures, proposes edits | single `--model` for everything | ❌ **ROADMAP Tier 1 #3.** |
| `target` — executes the skill (agent under test) | single `--model` for everything | ❌ Same. |
| Task evaluator | single `--model` (same as judge) | ❌ Same. |

Concrete value of splitting these out: in the geo-citability case we wanted to **eval against Claude (production fidelity)** while running the analyst + judge on cheap providers (DeepSeek) to save quota. Single-model forces an all-or-nothing pick that loses one or the other.

---

## 5. Hyperparameters — one-to-one map

| SkillOpt | Ours | Match |
|---|---|---|
| `learning_rate` (max edits per step) | `--lr-budget` | ✅ |
| `min_learning_rate` (floor for decay schedulers) | n/a | ❌ |
| `lr_scheduler` (cosine / linear / constant / autonomous) | n/a (constant) | ❌ |
| `num_epochs` | n/a (we have flat iterations) | ❌ |
| `batch_size` (tasks per step; paper default 40) | full eval set every step | ❌ — see §6 on why this is fine for our scale. |
| `accumulation` (gradient accumulation) | n/a | ❌ — same reason. |
| `minibatch_size` (reflect minibatch) | n/a | ❌ — same reason. |
| `analyst_workers` (parallel reflection; paper default 16) | n/a | ❌ |
| `max_analyst_rounds` (paper default 3) | n/a | ❌ |
| `failure_only` | always-on | ✅ (hardcoded) |
| `use_slow_update` | n/a (markers exist, no writer) | ⚠️ |
| `slow_update_samples` (paper default 20) | n/a | ❌ |
| `use_meta_skill` | n/a | ❌ |
| `use_gate` | always-on | ✅ |
| `split_ratio` | description loop only | ⚠️ |

---

## 6. Things we have that the paper doesn't

| Ours | Why we added it |
|---|---|
| `runs_per_query` (run each eval case N times, majority-vote) | Eval noise was tripping the gate in real runs (geo-citability flicker observed 2026-05-28). Paper doesn't address LLM judge variance. **Concrete need from our setting.** |
| Hybrid hard + soft scoring (regex must_contain + LLM judge) | Paper uses a single task evaluator. Splitting lets cheap regex catch deterministic invariants (e.g. "URL appears as positional argv") and LLM catch qualitative ones (e.g. "plan references all 5 rubric categories"). |
| `SKILL.md.preopt.bak` automatic backup | Operational nicety. Paper's training runs assume git/checkpoint infra. |
| Per-skill UI panel (`skillopt-panel.js`) in gpt frontend | Paper ships a Gradio webui that drives global YAML configs across a training corpus. Ours is per-skill, fits the way skills are managed in the gpt frontend. |

---

## 7. Summary — what we have, what we're missing

### Inner loop: faithful

We implement the rollout → reflect → select → update → gate cycle with the same **4-atom edit primitive set** and the same **`learning_rate` cap** semantics. Our gate is a clean §3.5 monotonic-non-decrease. One useful extension (`runs_per_query` for denoising) that the paper doesn't have.

### Missing — high impact (in priority order)

1. **Multi-round reflection** (`max_analyst_rounds`) — would directly address the target-drift bug we see today (analyst proposes edits that don't match the live body; today they're silently dropped). See ROADMAP §1.1.
2. **Epoch-boundary mechanisms — slow update + meta skill** — paper attributes most cross-run gains to these. Phase 2C in our roadmap.
3. **Different models per role** (`optimizer` vs `target` vs evaluator) — ROADMAP Tier 1 #3.
4. **`lr_scheduler`** — paper finds cosine > constant by a meaningful margin. Small change.
5. **Selection/test split for body loop** — we currently evaluate on the same set we optimize against. Risk grows once iterations actually produce cumulative edits.

### Missing — low impact for our setting

`batch_size`, `minibatch_size`, `accumulation`, `analyst_workers` — these matter for **training-corpus-scale** runs (paper uses 40 tasks/step × many steps × many epochs against benchmarks like SearchQA, DocVQA, ALFWorld). For our setting (per-skill optimization with ~10 eval cases), single-batch full-set is fine. Skipping these is a deliberate scope choice, not a bug.

---

## 8. How to keep this honest

When the paper or our impl changes:
- **A roadmap item ships that closes a gap** → flip the row from ❌/⚠️ → ✅, add a "shipped 2026-MM-DD" note.
- **The paper publishes a new version with new mechanisms** → check the version tag (`2605.23904v?`) at the top of this doc, scan the changes, add rows.
- **We deliberately diverge** (add something the paper doesn't have, or skip something it does) → record the *why* in §6 or §7. Future us needs to know whether each gap is "haven't gotten to it" or "decided against it."

Maintenance bar: update this doc whenever a Tier 1/2/Phase-2C roadmap item ships. The roadmap and this comparison should not drift apart.
