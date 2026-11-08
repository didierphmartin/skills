---
name: playbook-author
description: "Write a Console-style ITSM PLAYBOOK from a request and save it as a Playbook agent the user can drag into a workflow. USE THIS whenever the user asks to 'create/write/generate a playbook', 'automate <IT/HR/support process> as a playbook', 'onboarding/offboarding/access-request/leave/refund playbook', or wants a runbook that calls Okta/Slack/Workday/GitHub-style tools with human approvals. You draft the playbook as structured JSON, scripts/create.py renders the exact syntax (Title/Trigger/Instructions/Tools used/Actions used), checks that every #Action is listed and BINDS it to the available MCP tools, then you save it with save_playbook_agent. Do NOT use for: running a playbook, building a workflow graph (that is workflow-compile), or editing an existing workflow. Not for ServiceNow flows (use servicenow-flow-author)."
license: MIT
metadata:
  version: 0.1.0
  origin: synergyAI custom
allowed-tools: run_skill_script, save_playbook_agent
context-references: references/syntax.md
---

# playbook-author

## CRITICAL — runtime contract (read first)

1. **Never paste a playbook in chat as the deliverable.** The deliverable is the file written by `scripts/create.py` (exit 0) AND the agent created by `save_playbook_agent`.
2. **You write the draft as JSON**, not as prose. The script owns the syntax. Don't ask the user to write JSON.
3. **Loop until VALID**: if `create.py` exits 1, read the error list (each names the step), fix the draft, run again — fix all errors in one revision. If it exits 0 with `UNBOUND` lines, rename each one after a function the report lists as `closest:` (its tool words, plus the server name if two servers have that function) or replace them with a native verb / `#Prompt for Handoff`, and run again. **At most two revisions**; then save anyway (rule 4) and tell the user what stays unbound — an unbound action hands off to a human at run time, it does not block the playbook. Never end a turn on a report without either running the script again or saving. The report's last `NEXT:` line tells you which.
4. **Save immediately** with `save_playbook_agent` as soon as the report says VALID with 0 unbound, or after your second revision whatever is left — do NOT ask the user whether to save or adjust first (they can edit the playbook later from the Agents list). `name` = the Title, `description` = the Trigger, `playbook` = the text the script printed after `----- playbook -----` (verbatim). The agent shows up in the Agents list under **Playbooks**, ready to drag onto a workflow canvas.
5. Answer the user in 3–5 lines: title, trigger, which actions bound to which MCP tools, which are handoffs. No full text.

## Step-by-step

1. Understand the process: who asks, what must be checked, which systems act, where a human must approve or take over. Look at `references/example-playbook-1.md` … `4.md` — match their tone and structure (Okta MFA reset, New hire provisioning, Slack channel management, Time off & leave).
2. Name each integration action after the system's function, as Console does (`#Activate Okta User`, `#Archive Channel`, `#Get PTO Balance`) — the script binds it to the MCP tools and, when a name does not bind, lists the closest real functions — NEVER a `#Custom …` prefix (the script rejects it); use native verbs for messages, notes, approvals, handoffs, resolve (see `references/syntax.md`).
3. Anything the playbook needs from a person: use `#Trigger Form` and name the fields in the prose, with the choices in parentheses where the answer is a choice (`leave type (PTO / sick / bereavement / personal)`). Two or more items is always a form — never one open question hoping for several answers. Scope the ask to what is missing (`asking ONLY for the missing ones among: …`) rather than a flat list, so a request that already supplies half the facts is not asked for them again. See "Asking a person for information" in `references/syntax.md`.
4. Write the draft to `/scratch/<name>.json`:
   ```jsonc
   {
     "name": "contractor-access",                 // file slug (kebab-case)
     "title": "Contractor Access Provisioning",
     "trigger": "A manager asks for system access for a new contractor.",
     "domain": "an IT service desk",              // optional
     "steps": [
       {"text": "#Lookup Users on the requester with includeManager."},
       {"text": "Validate the request:", "sub": [
         "Contract end date present? If not, #Trigger Form asking for it.",
         "Manager approval → #Request Approval from the requester's manager."]},
       {"text": "On approval: #Create Okta User, then #Invite User To Org on GitHub."},
       {"text": "#Send Direct Message to the requester confirming what was created."},
       {"text": "#Leave Internal Note with the accounts created and the end date."},
       {"text": "#Resolve Request."}
     ],
     "tools_used": ["Okta", "GitHub"]             // servers of bound actions are merged in automatically
     // "actions_used" is derived from the prose — list it only to override
   }
   ```
5. Call `run_skill_script` with EXACTLY this shape (the draft travels as ONE file — a JSON *string* under its path — never as loose fields):
   ```json
   {
     "script": "scripts/create.py",
     "argv": ["-i", "/scratch/<name>.json", "-o", "/outputs/<name>.playbook.md"],
     "input_files": { "/scratch/<name>.json": "{ \"name\": \"<name>\", \"title\": \"…\", \"trigger\": \"…\", \"steps\": [ … ], \"tools_used\": [ … ] }" },
     "read_outputs": ["/outputs/<name>.playbook.md"]
   }
   ```
   The output path is flat under `/outputs/` (no sub-folder).
6. Read the report: `bound` / `UNBOUND` / `INVALID` lines. Revise (rule 3) or proceed.
7. Call `save_playbook_agent` (rule 4). Then reply (rule 5).

## ServiceNow jobs: `inputs` and `--unattended`
When the playbook serves a ServiceNow request, add `"inputs": [{"name", "label", "type", "mandatory"}]` to the draft: the Instructions then open with a line telling the runtime which `answers` the job carries. Add `"--unattended"` to `argv` when no human will answer: every gate verb (`#Trigger Form`, `#Request Approval`, `#Prompt for Handoff`) becomes INVALID, and so does an unbound action (it would hand off to a person). Replace each with a bound action or take the facts from `inputs`. See `references/syntax.md`.

## What the script checks (so you know what "INVALID" means)
- Title, Trigger and at least one step present.
- Every `#Action` in the prose is under Actions used and vice versa (it derives the list for you when you omit it).
- Action names start with `#` and use capitalised words.
- Each non-native action is scored against the MCP catalog; ties or low scores are reported as UNBOUND with the step number and the closest functions to rename it after.
