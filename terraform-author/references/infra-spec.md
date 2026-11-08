# JSON infrastructure spec

One cloud-neutral description of an application's infrastructure. `scripts/create.py` checks it against
`catalog.md` and writes the Terraform project for the target cloud. Every problem is reported at once.

## Top level

| Key | Required | What |
|---|---|---|
| `name` | yes | lowercase slug, 3–24 letters, digits and single hyphens (`customer-portal`); the folder is `<name>-<cloud>` |
| `title` | yes | the application, in the user's words and language |
| `environment` | no | 2–10 lowercase letters/digits (`prod`, `dev`); default `prod`; part of every resource name |
| `target` | no | `{"cloud": "aws" or "azure", "region": "<the cloud's region name>"}`; missing → `NEED_TARGET` |
| `tags` | no | `{"name": "value"}` put on every resource (names: letters, digits, `_`, `.`, `-`) |
| `network` | no | `{"cidr": "10.0.0.0/16"}` (default): a private IPv4 range from /16 to /22 |
| `components` | yes | the list below |
| `access` | no | who may use what (below); nothing is reachable without an edge |
| `placeholders` | when used | `{"<name>": "<what the user must provide>"}` — every placeholder used, nothing else |
| `left_out` | no | out-of-v1 names from catalog.md (`"custom_domain"`, `"vm"`, …) the description asked for |

## Components

Each is `{"type": "<type>", "name": "<name>", …options}`; options and defaults per type are in catalog.md.
Names are lowercase letters, digits and single `_` (1–20, unique; not `blob`, `deployer` or `apps_env`); the
environment variables an edge injects use the name in upper case (`uploads` → `UPLOADS_BUCKET`), and no two edges of
one app may set the same variable.

- `container_app` — `image` is required: the image the user gave (`"ghcr.io/acme/portal:2.1"`) or
  `{"placeholder": "container_image"}`. `env` values are text or placeholders, never secrets (use a `secret` and an
  edge). On AWS all apps of one spec share `public_https`.
- `postgres` — `size` small/medium/large; on Azure `high_availability: true` needs `medium` or `large`.
- `object_storage` — `versioning` (default true). On Azure two storage names must differ in their first 8
  letters and digits.
- `secret` — no options, no value: the user types it at apply time (`TF_VAR_<name>`). A secret cannot be named
  `<postgres name>_password` or `<postgres name>_admin_password` (the database's generated password uses them).

## Access

`{"from": "<container_app>", "to": "<component>", "level": "<level>"}` — `level` is `read` or `readwrite` for
`object_storage` (required), `read` for a `secret` (default), `connect` for `postgres` (default). One edge per pair;
apps never point at apps (they talk over HTTPS). What each edge grants and which environment variables it adds:
catalog.md → Access. Never set those variable names in `env`.

## Placeholders

`{"placeholder": "<name>"}` wherever a value only the user knows is needed (an image, a URL in `env`). Each becomes
a Terraform variable without a default that the user fills in `terraform.tfvars`. Names: lowercase letters,
digits, `_`; not the name of a secret.

## Out of v1

A VM, MySQL, a custom domain (`portal.example.com`), a private-only secret store, several regions, Kubernetes,
functions, caches, queues, a CDN: never components or options. Add the name to `left_out`; the report prints the
sentence to tell the user (catalog.md → Out of v1).

## Complete example

The customer portal (a public app, a database, file storage, a payment API key, a custom domain left out):

```json
{
  "name": "customer-portal",
  "title": "Customer portal",
  "environment": "prod",
  "target": {"cloud": "aws", "region": "eu-west-3"},
  "tags": {"project": "customer-portal", "env": "prod"},
  "network": {"cidr": "10.0.0.0/16"},
  "components": [
    {"type": "container_app", "name": "web", "image": {"placeholder": "container_image"}, "port": 8080,
     "cpu": 0.5, "memory_gb": 1, "min_instances": 2, "max_instances": 4, "public_https": true,
     "env": {"APP_MODE": "production"}},
    {"type": "postgres", "name": "db", "version": "17", "size": "small", "storage_gb": 32, "backup_days": 7},
    {"type": "object_storage", "name": "uploads", "versioning": true},
    {"type": "secret", "name": "payment_api_key"}
  ],
  "access": [
    {"from": "web", "to": "uploads", "level": "readwrite"},
    {"from": "web", "to": "payment_api_key", "level": "read"},
    {"from": "web", "to": "db"}
  ],
  "placeholders": {"container_image": "The application's container image (registry/name:tag)."},
  "left_out": ["custom_domain"]
}
```
