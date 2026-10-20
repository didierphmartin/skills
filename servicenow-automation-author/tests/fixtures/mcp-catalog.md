# Available MCP Functions

> Test fixture: the part of playbook-author's references/mcp-catalog.md that tests/fixtures/okta-reset.json uses,
> so the tests do not depend on the live, auto-generated catalog. Same format.

## Okta

Mock Okta identity server.

### Functions
- `mcp_reset_password` — Trigger a password reset for a user.
- `mcp_search_users` — Find an Okta user by email address.
- `mcp_unlock_user` — Unlock a locked Okta user account.

## ServiceNow

### Functions
- `mcp_add_note` — Add a work note to a SynergyAI task.
- `mcp_claim_job` — Claim an open SynergyAI task (Work in Progress) before acting on it.
- `mcp_complete_job` — Close a claimed SynergyAI task as complete, with close notes.
- `mcp_hand_back` — Give a SynergyAI task back to a human group, open, with the reason.
- `mcp_poll_jobs` — Return the oldest open, unclaimed SynergyAI task with its request's form answers, or job=null.
