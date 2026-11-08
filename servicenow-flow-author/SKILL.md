---
name: servicenow-flow-author
description: "Turn a procedure described in plain language (any language) into a ServiceNow FLOW that builds with the ServiceNow SDK and installs into the user's instance, where it opens in Workflow Studio. USE THIS whenever the user asks to 'create/build/generate a ServiceNow flow', 'automate this process in ServiceNow', 'make a Flow Designer / Workflow Studio flow', or pastes a procedure (onboarding, approvals, reminders, escalations) and wants it in ServiceNow. You write the flow as a JSON spec; scripts/create.py checks it against the SDK vocabulary, writes the Fluent project (one shared app; a procedure that needs data from people becomes a catalog item with an order form) and lists the placeholders to fill. Do NOT use for: running or testing flows on an instance, Console-style playbooks (that is playbook-author), SynergyAI workflow graphs (workflow-compile)."
license: MIT
metadata:
  version: 0.1.0
  origin: synergyAI custom
allowed-tools: run_skill_script
context-references: references/flow-spec.md, references/fluent-vocabulary.md
---

# servicenow-flow-author

## CRITICAL — runtime contract (read first)

1. **You never write TypeScript, and you never paste the spec or the flow in chat.** You write a JSON flow spec
   (`references/flow-spec.md`); `scripts/create.py` owns every line of Fluent. No `read_outputs`: the report is on
   stdout — never read back or paste the generated files.
2. **Run the script, then fix everything at once.** On `INVALID` (exit 1) every problem is listed with its step;
   fix all of them in one revision and run again. **At most two revisions**; if it is still INVALID, tell the user
   what remains and what they must decide. Exit 2 means the spec or the argv could not be read:
   resend the exact call shape (step 4).
3. **Company code.** `NEED_VENDOR` (exit 1, printed only once the spec is valid) means the app's company code is
   unknown: ask the user once for it (in ServiceNow Studio, the prefix shown when creating an app, e.g. x_1234567),
   then run the same call with `"--vendor", "<code>"` added. It is remembered in `/outputs/servicenow/.vendor`.
4. **Never invent instance data or fields.** An instance record (group, user, notification, schedule, catalog,
   category, event, any record input) is a pill or a declared placeholder — a typed name or sys_id is rejected.
   Data a person supplies is a catalog variable: on the order form (`"form"`) when known at the start, or in an
   **ask step** (a catalog task that person fills in mid-run) when only someone else knows it later.
5. **Map, don't invent.** A feature Fluent lacks is mapped to a supported construct (table below) or left out and
   named in your reply (line 5) — never a script or an action missing from `fluent-vocabulary.md`.
6. **Your reply IS these lines, in this order, in the language of the user's latest message (never one inferred from memory or profile) — nothing else** (no design notes, step
   lists or prerequisites — those are in the project README and `references/deploy.md`):
   ```
   ✅ <flow title> — added to x_<code>_synergy (<N> flow(s) in the app).
   📁 ~/Documents/synergyAI/outputs/servicenow/x_<code>_synergy/
   ✏️ Fill before installing: <comma-separated placeholder names> (details in FLOWS.md).
   ▶️ In a terminal, in that folder: sh build-install.sh (the first time, add the instance URL — see README.md).
   ⚠️ <one sentence: what was left out or approximated>
   ```
   Line 3 only when placeholders are left to fill; line 5 only when something was left out or approximated.

## Steps

1. Read the procedure. Does it need data from people (a start date, an equipment list)? Then it is a
   **catalog item**: trigger `service_catalog`, a `"form"` with the variables known at the start, its start condition ("when HR creates a user…") in `form.submitted_by`, catalog and category placeholders, and an
   ask step for each question a person answers later (for maintainers: references/example-onboarding.md). Otherwise
   pick a record or scheduled trigger.
2. Write the steps in order. Each has an `id` (snake_case, unique), a `label` in the language of the user's request, and an
   `action` with `inputs`, an `ask`, or a `logic` block; values and pills as in flow-spec.md → Values.
3. Tasks: in a catalog flow every task is `createCatalogTask` with `"ah_requested_item": {"pill": "trigger.request_item"}`
   and `"ah_wait": false` — never `createTask`. Exception: a task assigned to the SynergyAI group uses `"ah_wait": true`. In a record flow a `createTask` links back to the trigger record
   (`{"pill": "trigger.current.sys_id"}` in e.g. `parent`; the script names the usable fields).
4. Call `run_skill_script` with EXACTLY this shape (the spec travels as ONE JSON string):
   ```json
   {
     "script": "scripts/create.py",
     "argv": ["-i", "/scratch/<name>.json"],
     "input_files": { "/scratch/<name>.json": "{ \"name\": \"<name>\", \"title\": \"…\", … }" }
   }
   ```
   Add `"--vendor", "<code>"` to `argv` only after the user gave the company code.
5. Read the report: `VALID` (placeholders listed, `NEXT:` line), `INVALID` (rule 2) or `NEED_VENDOR` (rule 3).
   A VALID catalog flow also writes `previews/<name>.html`, which SynergyAI shows in the right pane by itself:
   never read it back or paste it.
6. Reply with rule 6's lines, filled in from the report. When the user later gives placeholder values, put them in `placeholders.<name>.value` and run
   the same spec again (same `name`): the flow is rewritten with the values and `FLOWS.md` marks them filled.

## Procedure features Fluent does not have

| The procedure says | Write |
|---|---|
| "X approves …; if not, stop" | `askForApproval`, then `if` `[{"pill": "<approval>.approval_state"}, "!=approved"]` → notify + `end`: any outcome other than approved takes the stop path |
| "N working days before/after <date>" | `wait_duration` with `relative_to` the date pill, `direction`, and a `schedule` placeholder — see flow-spec.md → Waits |
| "go back to step N", "repeat until" | `do_until` around the steps to repeat, with the exit condition in `until` |
| "decide", "depending on the answer" | `if` with one `else_if` per answer and an `else` |
| "wait until the task/record is done" | the action `waitForCondition` on that record |
| "ask X for …", "X confirms …" (data mid-run) | an ask step assigned to X; branch on `<ask>.catalog_task.state` (3 = closed complete) when the procedure says what happens otherwise |
| "pick any of: VPN, Git, Jira…" (several answers from a fixed list) | one `checkbox` variable per option, each branched with `[{"pill": "form.<option>"}, "=true"]` — `select_box`/`multiple_choice` take one answer only |
| "if the answer contains …" | `LIKE` (`[{"pill": …}, "LIKEvpn"]`) — `CONTAINS` is not an operator |
| a list of items ("laptop, monitor, phone") | a `list_collector` variable on the item table (e.g. `cmdb_model`); `lookUpRecords` with `["sys_idIN", {"pill": "form.<variable>"}]`, then `for_each` over its `Records` |
| an email that must show a variable in its body | `sendNotification` with a notification placeholder on `sc_req_item` (a `sendEmail` body takes no pills; only its `ah_subject` and `ah_to` do) |
| "raise the event …" | `fireEvent` with `event_name` a placeholder (kind `text`, the registered event) |
| call a workflow, subflow, dynamic flow, script or spoke | leave it out and say so in the reply |

What the script checks, and so what `INVALID` means: every rule in `references/flow-spec.md` and `fluent-vocabulary.md`.
