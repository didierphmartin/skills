# Console-style playbook syntax (what the app's parser accepts)

A playbook is plain text with these sections, each starting on its own line:

```
[optional preamble lines — kept, shown to the LLM before the playbook]

Title: <short name>

Domain: <optional — the desk/team the playbook serves>

Trigger: <one sentence: what request starts it>

Instructions:
1. <step — prose; tool calls are #Action references>
- <sub-step bullet (starts with "- ")>
2. <step>
3. …

Tools used: <Server A>; <Server B>

Actions used: #Action One; #Action Two; …
```

Rules the parser and analyzer enforce:
- Section keyword + colon at the start of the line. A list marker or heading before it is tolerated ("1. Instructions:", "## Title:"), but write them plainly.
- Steps are numbered `1.`, `2.` …; sub-steps are `- ` bullets under their step. Branches read best as bullets: `- PTO → #Request Approval from manager.`
- Every `#Action` written in the prose MUST appear under **Actions used**, and every listed action must appear in the prose. Unlisted actions are silently unbound at run time.
- `#Action` names: capitalised words naming the function, e.g. `#Lookup Users`, `#Archive Channel`, `#Get PTO Balance`. Lower-case `#channel-name` is a Slack channel, not an action.
- **Never write a `Custom` prefix** (`#Custom Slack Set Status` is rejected by the script). In Console, "custom" is a tier of actions a customer built on an API, not part of an action's name; every function in our catalog is registered, so write its name: `#Set Status`. Imported Console playbooks may carry a `(Custom)` suffix — tolerated, never generated.
- Lists are `;`-separated.

Native verbs (no MCP needed — handled by the runtime): `#Send Direct Message`, `#Send Channel Message`, `#Send Email`, `#Leave Internal Note`, `#Resolve Request`, `#Escalate Request`, `#Request Approval` (approval gate), `#Prompt for Handoff` (hands to a human), `#Trigger Form` (asks the requester for fields).

**Asking a person for information — prefer a form.** When a step needs anything from a
human, `#Trigger Form` is the default and a free-text question is the exception:

- **Two or more items → always `#Trigger Form`.** Name every field in the prose, and put
  the allowed answers in parentheses when the answer is a choice. The runtime builds the
  form from what you name, so a field you do not name does not appear.
- **Ask only for what is missing.** Name the full set the step could need, then scope the
  ask to the gap — `#Trigger Form asking ONLY for the missing ones among: start date, end
  date, leave type (PTO / sick / bereavement / personal), work email`. A flat "ask for A,
  B and C" makes the playbook re-ask for facts the request already supplied, which reads
  as if nobody listened. Pair it with a parse instruction: *"Parse what the request already
  provides. If anything is still missing, #Trigger Form …"*
- **One item → a form is still usually better**, because it labels the field and validates
  that it was filled. Reach for an open question only when you genuinely want prose — a
  reason, a description, a free explanation.
- **Never ask an open question hoping for several answers.** "Ask the requester for their
  leave details" makes the person guess what you need and usually costs two or three more
  exchanges; the runtime also has a plain wait-for-a-reply tool it will fall back to on its
  own when the prose reads that way, which is how playbooks end up with a bare text box
  instead of a form.
- Mark a field `sensitive` in the prose (`... and their employee ID (sensitive)`) when the
  answer should be masked as it is typed and redacted in the transcript.

Binding rule for everything else (auto-binding): the action name is matched to a registered MCP function by words. Tool words count once, all tool words present earns a bonus, and a server name in the action counts double; the best score must be ≥ 4 and unique. So name the action after the function, the way Console names integration actions (`#Activate Okta User`, `#List Google Calendar Events`):
`#Submit Time Off` → `workday.submit_time_off`, `#Set Status` → `slack.set_status`, `#Block Time` → `google_calendar.block_time`.
Add the server name only when two servers expose the same function and the script reports the action as ambiguous: `#Lookup Workday Users` vs `#Lookup Okta Users`.
Anything that does not bind falls to the handoff policy at run time (a human takes over) — allowed, but say so.

Good habits (from the four examples): end with `#Leave Internal Note` then `#Resolve Request`; put human decisions behind `#Request Approval`; collect facts with `#Trigger Form` rather than an open question; use `#Prompt for Handoff` when the request is outside the playbook; keep steps short and imperative.
