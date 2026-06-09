# skill-creator — Roadmap & Notes

Companion to `SKILL.md` (current implementation) and **`PAPER-COMPARISON.md`** (where we stand against the SkillOpt paper). This file tracks **known issues**, **lessons learned**, **deferred work**, and a **roadmap** for the optimization loops (`run_loop.py`, `run_body_loop.py`) and the SkillOpt panel UI in the gpt frontend (`assets/js/skillopt-panel.js`).

When a roadmap item ships, also update `PAPER-COMPARISON.md` if it closes a gap with the paper.

Last updated: 2026-05-31

---

## 1. Known Issues

### 1.1 Target-drift in the body analyst (HIGH PRIORITY)

**Symptom.** `improve_body.py` proposes 2 edits per iteration. Iteration 1 lands; iterations 2–3 produce edits whose `target` strings no longer match the (now-modified) body, and `apply_patch_to_body` silently skips them. Net effect: "3 iterations accepted" with only 1–2 real edits.

**Evidence.** geo-citability DeepSeek run on 2026-05-28 — `body-opt-results/body-loop_geo-citability_2026-05-28_075920/results.json`. Diff at end: 2 edits. Patches in history: ~6 proposed.

**Fix sketch.**
- `body_analyzer.md`: add explicit "Quote `target` strings VERBATIM from the body shown above, never from memory."
- `improve_body.py`: when skip count > 0, expose a `skipped_targets` summary so the loop can log it / retry.
- `run_body_loop.py`: include applied/skipped counts per iteration in verbose stderr AND in `results.json`.

### 1.2 Body eval was noisy without `runs_per_query`

**Status:** PARTIALLY FIXED. `run_body_eval.py` now supports `runs_per_query` with majority vote; `run_body_loop.py` defaults it to 3.

**Remaining.**
- 3× call cost. A "calibrate" mode that runs baseline N times to estimate per-case variance before any optimization would help users decide if 3 is enough.
- Soft-score gating as an option: use `avg_soft_score` deltas (continuous) instead of `overall_passed` deltas (binary) — much smoother signal.

### 1.3 No way to cancel a running optimization

**Symptom.** Stop button in SkillOpt panel is informational. `runSkillScript` queues serially; JS can't kill a Python script in flight.

**Fix sketch.** Have `run_body_loop.py` poll for `<results-dir>/STOP` between iterations. UI writes it when Stop is clicked. Loop exits cleanly at next iteration boundary with whatever was accepted so far.

### 1.4 Description-side failures aren't body-fixable

**Symptom.** Body analyst correctly identifies that some failures are description-side (wrong skill triggered) but the gate still rejects iterations that try those edits. The right answer is to run `run_loop.py` (description) first, then `run_body_loop.py` — but nothing guides the user toward that.

**Fix sketch.** A "Optimize description + body" sequential mode in the panel: runs `run_loop.py`, applies its `best_description`, then runs `run_body_loop.py` from the cleaner baseline.

---

## 2. Lessons Learned

### 2.1 From the geo-citability case (2026-05-27 to 2026-05-28)

- **Eval calibration matters more than the gate.** Useful baseline range: 5–6 out of 10. 0/10 → eval broken or wrong model. 9–10/10 → cases too easy.
- **Don't blame the analyst for description-side failures.** Cases like "Italian restaurant" (should-NOT-trigger) and "SEO audit" (wrong skill) need `run_loop.py`, not `run_body_loop.py`.
- **Cross-provider evals are misleading.** DeepSeek baselined 0/10 vs Claude's 5–6/10 on the same skill. Use the production model as the agent-under-test. A cheap provider is fine for the analyst + judge **only**.
- **Tie-tolerant gate (`>=`) is the right default.** SkillOpt §3.5's monotonic-non-decrease rule. Strict `>` throws away every neutral move and stalls progress under eval noise.

### 2.2 Backend / runtime gotchas (memory cross-refs)

- **Model IDs differ from Claude Code's env header.** Backend wants Anthropic-API IDs with date suffix (`claude-sonnet-4-5-20250929`), NOT marketing names (`claude-sonnet-4-6`). See `~/.claude/.../memory/reference_backend_model_ids.md`.
- **Provider-error humanizer collapses real errors to "unknown".** When debugging /api/v1/agent failures, check the backend PHP error log directly. Long-term fix: surface `$e->getMessage()` in debug builds.
- **FSA mounts don't auto-refresh.** Files added on host after mount are invisible to pyodide until the directory handle is re-acquired (`await window.localFs.requestRootAccess('synergyAi')`).
- **Pyodide-vfs `/tmp` is in-memory.** Don't use it for `--results-dir`. Use `/skill/<dir>/...` (mounted, persistent on host disk).

---

## 3. Roadmap

Ranked by leverage. Each item has a rough scope estimate.

### Tier 1 — Quality-affecting fixes

1. **Target-drift fix** (§1.1) — ~30 LOC. Highest impact: makes "3 iterations accepted" actually 3 real edits.
2. **Real cancel button** (§1.3) — ~50 LOC across script + panel.
3. **Different models per role** — agent / analyst / judge as 3 separate dropdowns. Lets you eval against Claude (production fidelity) while spending cheap-provider $$ on the analyst + judge.

### Tier 2 — UX completeness

4. **Full diff view of accepted edits** — side-by-side `SKILL.md.preopt.bak` vs current SKILL.md in Results tab, syntax-highlighted.
5. **Eval-set editor inline** — open/edit `eval_set.json` with regex-compile linting.
6. **Crash recovery / resume** — detect partial results dir on next open; offer "Resume from iteration N".
7. **Score-trajectory chart** — Chart.js line chart on Monitor + Results.

### Tier 3 — Cost & safety

8. **Pre-flight cost estimate** — show "~N API calls; ~$X" before launching.
9. **Eval-set noise sampling** — "Calibrate" button runs baseline N times, reports per-case variance.
10. **Per-skill cost cap** — refuse to launch above a configurable limit.

### Tier 4 — Workflow / breadth

11. **Description + body sequential mode** (§1.4) — one-click two-phase optimization.
12. **Compare two past runs** — pick two from Results, diff scores + accepted edits.
13. **A/B validate with production model** — after a cheap-provider optimization, run the eval once on Claude with both `.preopt.bak` and current SKILL.md to confirm the changes also help (or don't break) the prod model.
14. **Batch optimize a folder** — sequentially optimize every skill in `GEO/` etc.
15. **Export run as Markdown report** — for sharing / archiving.

---

## 4. Deferred — Phase 2C (Microsoft SkillOpt §3.7)

Two pieces from the SkillOpt paper that we explicitly deferred until we had real Phase 2B data:

- **Optimizer-side meta-skill** — a persistent prompt that accumulates lessons across runs and is fed back to the analyst on every iteration. Lives at `agents/optimizer_meta.md` + persistent `<skill>/optimizer_memory.md` per skill.
- **Comparator-driven gate** — a separate LLM that judges "which of two candidate bodies produces better outputs", replacing pass-count delta with pairwise preference.

We now have one real-data point (geo-citability with Claude + DeepSeek). Pick this back up when:
- The current Tier 1 fixes ship AND
- We have at least one skill where optimization plateaus on the current pipeline AND
- We have eval data showing the plateau isn't a bug.

New flags: `--use-meta-skill`, `--use-comparator-gate`.

---

## 5. Maintenance

Update this file when:
- A roadmap item ships → move it to a "Done" log section (add when first item ships).
- A new known issue is observed in a real run.
- A lesson contradicts something here — correct in place; don't just append.
