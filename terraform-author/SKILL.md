---
name: terraform-author
description: "Turn the infrastructure of an application described in plain language (any language) into a Terraform project for AWS or Azure that passes terraform validate and is secure by default: private network, managed PostgreSQL, private object storage, secrets, containerised apps behind HTTPS, least-privilege access. USE THIS whenever the user asks to 'write/generate Terraform', 'create the infrastructure / IaC for my app on AWS or Azure', 'deploy this app to AWS/Azure with Terraform', or asks for 'the same on Azure/AWS'. You write one cloud-neutral JSON spec; scripts/create.py checks it, writes every .tf file for the chosen cloud and region, and lists the placeholders and secrets to supply. Do NOT use for: running Terraform (plan/apply) for the user, Kubernetes manifests, CloudFormation, Bicep or ARM templates, Pulumi, ServiceNow flows (servicenow-flow-author), playbooks (playbook-author)."
license: MIT
metadata:
  version: 0.1.0
  origin: synergyAI custom
allowed-tools: run_skill_script
context-references: references/infra-spec.md, references/catalog.md
---

# terraform-author

## CRITICAL — runtime contract (read first)

1. **You never write HCL, and you never paste the spec or the generated files in chat.** You write a JSON spec
   (`references/infra-spec.md`); `scripts/create.py` owns every line of Terraform. No `read_outputs`: the report is
   on stdout — never read back or paste the generated files.
2. **Run the script, then fix everything at once.** On `INVALID` (exit 1) every problem is listed; fix all of them
   in one revision and run again. **At most two revisions**; if it is still INVALID, tell the user what remains
   and what they must decide. Exit 2 means the spec or the argv could not be read: resend the exact call shape (step 4).
3. **Target.** If the user named no cloud or no region, do not guess: run anyway — `NEED_TARGET` (exit 1, printed
   only once the spec is valid) means ask the user once: *"AWS or Azure, and which region?"*, then run the same
   call with `"--cloud", "<aws|azure>", "--region", "<region>"` added. City names map to regions with
   catalog.md → Regions (Paris = `eu-west-3` / `francecentral`).
4. **"The same for Azure/AWS"** = no new spec: run with `"-i", "/outputs/terraform/<name>-<cloud>/spec.json"` and
   `"--cloud", "<the other cloud>"` (add `"--region"` only if the user named one; otherwise the nearest region is used).
5. **Never invent account data.** Container images, domain names, IP ranges of offices, certificates, existing
   networks: an image the user did not give is a `{"placeholder": …}`; the others are out of v1 (rule 6). Secret
   values are never in the spec: a `secret` component's value is typed by the user at apply time.
6. **Map, don't invent.** Something catalog.md lists as out of v1 (a VM, MySQL, a custom domain, …) is never a
   component: put its name in `"left_out"` and say so in line 6 of your reply.
7. **Your reply IS these lines, in this order, in the user's language — nothing else** (no design notes, resource
   lists or prerequisites — those are in the project README and `references/deploy.md`):
   ```
   ✅ <title> — Terraform for <AWS|Azure> (<region>).
   📁 ~/Documents/synergyAI/outputs/terraform/<name>-<cloud>/
   ✏️ Fill before plan: <comma-separated placeholder names> (terraform.tfvars — see README.md).
   🔑 Secrets, typed at apply: <comma-separated TF_VAR_ names>.
   ▶️ In a terminal, in that folder: terraform init, then terraform plan -out tfplan, then terraform apply tfplan.
   ⚠️ <one sentence: the trade-off the report warns about, or what was left out>
   ```
   Line 3 only when placeholders are listed; line 4 only when secrets are listed; line 6 only when the report has a
   `warning:` or `left out:` line.

## Steps

1. Read the description. List the components (catalog.md): each app packaged as a container is a `container_app`
   (`public_https: true` only when the user wants it reachable from the internet), a database is `postgres`, files
   are `object_storage`, each API key or token is a `secret`. The network is always generated: never a component.
2. Write the access edges: which app reads or writes which storage, reads which secret, connects to which database.
   An app gets nothing it has no edge for.
3. Options the user stated go in the spec (sizes, versions, instances, backups); leave the others out (defaults).
   Values only the user can give are placeholders, declared in `"placeholders"` with what to provide.
4. Call `run_skill_script` with EXACTLY this shape (the spec travels as ONE JSON string):
   ```json
   {
     "script": "scripts/create.py",
     "argv": ["-i", "/scratch/<name>.json"],
     "input_files": { "/scratch/<name>.json": "{ \"name\": \"<name>\", \"title\": \"…\", … }" }
   }
   ```
   Add `"--cloud", "<aws|azure>", "--region", "<region>"` to `argv` when the user gave them after the spec was
   written (or put them in the spec's `"target"`).
5. Read the report: `VALID` (folder, placeholders, secrets, `warning:`, `left out:`, `NEXT:`), `INVALID` (rule 2)
   or `NEED_TARGET` (rule 3).
6. Reply with rule 7's lines, filled in from the report.

What the script checks, and so what `INVALID` means: every rule in `references/infra-spec.md` and `catalog.md`
(types and options, regions, names, edges, placeholders, Azure high availability on `small`, AWS apps sharing
`public_https`). For maintainers: references/example-customer-portal.md shows a description, its spec and the files
for both clouds; references/deploy.md is what the user runs.
