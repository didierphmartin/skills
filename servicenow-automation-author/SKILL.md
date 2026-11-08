---
name: servicenow-automation-author
description: "Create a SynergyAI AUTOMATION: something people order in the ServiceNow catalog and SynergyAI fulfils. From one prompt it makes both halves in sync — the ServiceNow catalog item + flow that hands a task to SynergyAI, and the SynergyAI playbook + workflow that do the work and close the task or hand it back. USE THIS whenever the user asks to 'let employees order X in ServiceNow and have SynergyAI do it', 'automate this ServiceNow request with SynergyAI', 'create/regenerate the <key> automation'. You write ONE automation spec; scripts/create.py derives the flow spec, the playbook and the workflow from it, and you save them. Do NOT use for: a ServiceNow flow that SynergyAI does not fulfil (servicenow-flow-author), a playbook alone (playbook-author), a workflow alone (workflow-compile)."
license: MIT
requires-skills: [playbook-author, servicenow-flow-author, workflow-compile]
metadata:
  version: 0.1.0
  origin: synergyAI custom
allowed-tools: run_skill_script, save_playbook_agent, save_workflow, save_automation, install_automation
context-references: references/automation-spec.md
---

# servicenow-automation-author

## CRITICAL — runtime contract (read first)

1. **You write one automation spec** (`references/automation-spec.md`) — never the flow spec, the playbook draft or
   the workflow, and never TypeScript. The key, the form variable names and the version are copied by the script;
   you never retype them between parts. Never paste the spec or the generated files in chat.
2. **The version comes from `save_automation`** (step 1), never from you, and the spec has no `version` field. Every
   run of `create.py` in this turn uses that same `--version N`.
3. **Run the script, then fix everything at once.** On `INVALID` (exit 1) every problem is listed with the part it
   comes from: `[automation]` the spec itself, `[flow]` servicenow-flow-author, `[playbook]` playbook-author,
   `[workflow]` workflow-compile. Fix all of them in one revision of the spec and run again. **At most two
   revisions**; then tell the user what remains. Exit 2: the spec or the argv could not be read — resend the exact
   call shape (step 2). Nothing is written unless every part is VALID.
4. **The playbook runs unattended**: no `#Trigger Form`, `#Request Approval` or `#Prompt for Handoff`, and every
   action must bind to an MCP tool. The data a person gives goes in `form.variables` (the playbook reads them from
   the job's `answers`); an approval goes in `servicenow.before`.
5. **Company code.** `NEED_VENDOR` (exit 1) means the ServiceNow app's company code is unknown: ask the user once
   (in ServiceNow Studio, the prefix shown when creating an app, e.g. x_1234567), then run the same call with
   `"--vendor", "<code>"` added.
6. **Never invent instance data.** Groups, catalogs, categories and any record are placeholders
   (`servicenow.placeholders`); give a value only when the user gave it.
7. **Your reply IS these lines, in this order, in the language of the user's latest message (never one inferred from memory or profile) — nothing else:**
   ```
   ✅ <title> — automation <key> v<N>: flow added to <scope> (<M> flow(s) in the app).
   📁 ~/Documents/synergyAI/outputs/servicenow/<scope>/
   ✏️ Fill before installing: <comma-separated placeholder names> (details in FLOWS.md).
   ▶️ In a terminal, in that folder: sh build-install.sh (the first time, add the instance URL — see README.md).
   🤖 Playbook <key> and workflow "<title>" saved (Start: ServiceNow: get new job, tag <key>, version <N>).
   ⚠️ <one sentence: what was left out or approximated>
   ```
   Line 3 only when placeholders are left to fill. Line 4 is the auto-install result instead when
   `install_automation` installed it (dev/test). Line 6 only when something was left out or approximated.

## Steps (one turn)

1. `save_automation {key, title, spec}` — `key` = the spec's `name`. It creates the record (version 1) or, for an
   existing key, bumps the version. Keep what it returns: `version` (N), the automation id, and `playbook_id` /
   `workflow_id` when the automation already existed.
2. `run_skill_script` with EXACTLY this shape (the spec travels as ONE JSON string):
   ```json
   {
     "script": "scripts/create.py",
     "argv": ["-i", "/scratch/<key>.json", "--version", "<N>"],
     "input_files": { "/scratch/<key>.json": "{ \"name\": \"<key>\", \"title\": \"…\", … }" }
   }
   ```
   Add `"--vendor", "<code>"` only after the user gave the company code. On `INVALID`, rule 3 (same `--version`).
   On `VALID` the report gives one line per part (`flow:`, `playbook:`, `workflow:`, `automation:`), then
   `NEXT: save_playbook_agent then save_workflow then save_automation`, then the playbook text after
   `----- playbook -----`.
3. `save_playbook_agent {name: <title>, description: <the Trigger line>, playbook: <the text after ----- playbook -----, verbatim>, pkey: <key>, automation_id}`
   — add `playbook_id` when step 1 returned one: that playbook is updated in place. The `pkey` is ALWAYS the
   automation key (the workflow finds its playbook by it): a playbook that already holds it is updated in place
   when no automation or this one manages it. On `automation_key_taken` ("key taken by automation <title>"),
   another automation owns the key: stop, pick a NEW automation key, and regenerate everything with it from step 1
   (`save_automation`, `create.py`, `save_playbook_agent`, `save_workflow`). Never save the playbook under another
   `pkey` alone.
4. `save_workflow {path: "/outputs/<key>.workflow.json", automation_id}` — add `workflow_id` when step 1 returned
   one: that workflow is updated in place (an active one is stopped and started again). It also arms the Start
   option (`start_option.armed`), so ▶ can start the workflow; when it is not armed, tell the user to tick the
   Start option on the workflow's Start node before ▶.
5. `save_automation {key, title, spec, playbook_id, workflow_id}` with the ids from steps 3 and 4 and the final spec
   (as last run, after any revision) — the record now links every part.
6. Install: pick a server from `save_automation`'s `servers` whose `environment` is `dev` or `test`, and call
   `install_automation {automation_id, server_id, scope}` (the scope from the `flow:` line); no such server, or a
   refusal, means a manual install. When it answers with `placeholders` (the app still has `PLACEHOLDER:<name>`
   values), nothing was installed: ask the user for those values, put them in the spec
   (`servicenow.placeholders: {"<name>": {"value": "…"}}`), re-run `create.py` with the same `--version`, save the
   playbook and workflow again (steps 3–5), then call `install_automation` again. Then reply with rule 7's lines.

## Regenerating

The same key again regenerates the automation: `save_automation` bumps the version, and steps 3–4 overwrite the
managed playbook and workflow in place. Until the new flow is installed, new orders carry the old version and are
handed back as out of sync — say so when the install is manual.
