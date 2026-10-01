# Catalog — what a spec can describe, and what it becomes

`scripts/create.py` checks every spec against `catalog.json`; this page says the same in words. Providers pinned in
every project: Terraform `>= 1.11`, `hashicorp/aws ~> 6.0`,
`hashicorp/azurerm ~> 5.0`, `hashicorp/random ~> 3.7` (Azure only).

## Components

### `container_app`
An application packaged as a container image, served over HTTPS.

| Option | Values | Default |
|---|---|---|
| `image` | the image the user gave (`registry/name:tag`), else `{"placeholder": "<name>"}` | — (required) |
| `port` | 1–65535, the port the container listens on | `8080` |
| `cpu` | 0.25, 0.5, 1, 2, 4 (vCPU) | `0.5` |
| `memory_gb` | always 2 × cpu (0.25/0.5, 0.5/1, 1/2, 2/4, 4/8: the sizes both clouds accept) | 2 × cpu |
| `min_instances` | 1–100 | `1` |
| `max_instances` | 1–100, ≥ min_instances | `3` |
| `public_https` | true = reachable from the internet; false = only from inside the network | `false` |
| `env` | `{"NAME": "text" or {"placeholder": "<name>"}}`, UPPER_CASE names | `{}` |

AWS: ECS Express Mode service (HTTPS load balancer and certificate managed by ECS; it checks `GET /`), task,
execution and infrastructure roles, 30-day log group. **On AWS every container app of one project has the same
`public_https`** (the first Express service of a network fixes whether its load balancer faces the internet), and a
public app's tasks run in public subnets (a security group admits nothing directly). Azure: one Container Apps
environment (Consumption profile, in the VNet, logs to Log Analytics), one container app with HTTPS ingress and its
own managed identity per app. When no app is public the environment is internal (HTTPS inside the network, with a
private DNS zone for its domain linked to the VNet); when one is, the others are reachable only from the apps.
Images: on AWS a public image or one in the same account's ECR (pulled with the execution role); on Azure public
images only in this version (a private registry or Azure Container Registry is for a later version).

### `postgres`
A managed PostgreSQL server with one database named like the component.

| Option | Values | Default |
|---|---|---|
| `version` | "16", "17" | `17` |
| `size` | small, medium, large (table below) | `small` |
| `storage_gb` | 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384 | `32` |
| `backup_days` | 7–35 | `7` |
| `high_availability` | a standby in another zone | `false` |

| Size | AWS instance class | Azure SKU |
|---|---|---|
| `small` | `db.t4g.small` | `B_Standard_B1ms` |
| `medium` | `db.m7g.large` | `GP_Standard_D2ds_v5` |
| `large` | `db.r7g.xlarge` | `MO_Standard_E4ds_v5` |

`high_availability` with `small` is refused on Azure (Burstable has no high availability): use `medium`.
AWS: RDS (encrypted gp3, private, deletion protection, final snapshot; the admin password is managed by RDS in
Secrets Manager). Azure: flexible server in its own delegated subnet with a private DNS zone, public access off;
the admin password is generated at apply time, written only to the server and the key vault.

### `object_storage`
A private bucket / blob container for files.

| Option | Values | Default |
|---|---|---|
| `versioning` | keep every version of every object | `true` |

AWS: S3 bucket, all four public-access blocks, owner-enforced, KMS encryption. Azure: storage account (no shared
keys, TLS 1.2, public network access disabled, 7-day soft delete) with one private container and a private endpoint.

### `secret`
A value the application reads (API key, token). No options: its value is typed at apply time
(`TF_VAR_<name>`), never written in the spec, the project or the state. AWS: Secrets Manager. Azure: one key vault
for all secrets (role-based access, purge protection, public network access — see out of v1).

## Access

An access edge `{"from": "<container_app>", "to": "<component>", "level": …}` grants one app one right:

| To | Levels | AWS | Azure | Environment variables the app gets |
|---|---|---|---|---|
| `object_storage` | `read`, `readwrite` (required) | IAM statements on the bucket | `Storage Blob Data Reader` / `Contributor` on the account | AWS `<NAME>_BUCKET`; Azure `<NAME>_BLOB_ENDPOINT`, `<NAME>_CONTAINER` |
| `secret` | `read` (default) | execution role reads it at start | `Key Vault Secrets User` on that secret | `<NAME>` |
| `postgres` | `connect` (default) | security group rule app → db:5432, admin credentials from the RDS secret (read at task start; the task role may re-read it: the password rotates every 7 days) | NSG rule apps subnet → db:5432, password from the key vault | `<NAME>_HOST`, `<NAME>_PORT`, `<NAME>_NAME`, `<NAME>_USER`, `<NAME>_PASSWORD`; AWS also `<NAME>_SECRET_ARN` |

`<NAME>` is the target component's name in upper case. On Azure every app also gets `AZURE_CLIENT_ID` (its identity).
Do not set these names in `env`, and do not give two components of one app's edges names that set the same
variable (a secret `db_host` and a database `db` both set `DB_HOST`): the script refuses both. Apps connect to a
database as its admin user in this version.

## Regions

The cloud's own region names. AWS: `af-south-1`, `ap-east-1`, `ap-northeast-1`, `ap-northeast-2`, `ap-northeast-3`, `ap-south-1`, `ap-south-2`, `ap-southeast-1`, `ap-southeast-2`, `ap-southeast-3`, `ap-southeast-4`, `ap-southeast-5`, `ap-southeast-7`, `ca-central-1`, `ca-west-1`, `eu-central-1`, `eu-central-2`, `eu-north-1`, `eu-south-1`, `eu-south-2`, `eu-west-1`, `eu-west-2`, `eu-west-3`, `il-central-1`, `me-central-1`, `me-south-1`, `mx-central-1`, `sa-east-1`, `us-east-1`, `us-east-2`, `us-west-1`, `us-west-2`.

Azure, with availability zones: `australiaeast`, `austriaeast`, `belgiumcentral`, `brazilsouth`, `canadacentral`, `centralindia`, `centralus`, `chilecentral`, `denmarkeast`, `eastasia`, `eastus`, `eastus2`, `francecentral`, `germanywestcentral`, `indiasouthcentral`, `indonesiacentral`, `israelcentral`, `italynorth`, `japaneast`, `japanwest`, `koreacentral`, `malaysiawest`, `mexicocentral`, `newzealandnorth`, `northcentralus`, `northeurope`, `norwayeast`, `polandcentral`, `qatarcentral`, `southafricanorth`, `southcentralus`, `southeastasia`, `spaincentral`, `swedencentral`, `switzerlandnorth`, `uaenorth`, `uksouth`, `westeurope`, `westus2`, `westus3`. Without availability zones (storage is LRS, a standby stays in the same
zone): `australiacentral`, `australiasoutheast`, `canadaeast`, `koreasouth`, `southindia`, `ukwest`, `westcentralus`, `westindia`, `westus`.

"The same for the other cloud" without a region uses the nearest region: AWS → Azure takes the first row with
the AWS region, Azure → AWS the first row with the Azure region.

| AWS | Azure |
|---|---|
| `us-east-1` | `eastus` |
| `us-east-2` | `centralus` |
| `us-west-1` | `westus` |
| `us-west-2` | `westus2` |
| `ca-central-1` | `canadacentral` |
| `mx-central-1` | `mexicocentral` |
| `sa-east-1` | `brazilsouth` |
| `eu-west-1` | `northeurope` |
| `eu-west-2` | `uksouth` |
| `eu-west-3` | `francecentral` |
| `eu-central-1` | `germanywestcentral` |
| `eu-central-2` | `switzerlandnorth` |
| `eu-north-1` | `swedencentral` |
| `eu-south-1` | `italynorth` |
| `eu-south-2` | `spaincentral` |
| `il-central-1` | `israelcentral` |
| `me-central-1` | `uaenorth` |
| `af-south-1` | `southafricanorth` |
| `ap-east-1` | `eastasia` |
| `ap-south-1` | `centralindia` |
| `ap-south-2` | `indiasouthcentral` |
| `ap-southeast-1` | `southeastasia` |
| `ap-southeast-2` | `australiaeast` |
| `ap-southeast-3` | `indonesiacentral` |
| `ap-southeast-4` | `australiasoutheast` |
| `ap-southeast-5` | `malaysiawest` |
| `ap-northeast-1` | `japaneast` |
| `ap-northeast-2` | `koreacentral` |
| `ap-northeast-3` | `japanwest` |
| `ca-west-1` | `canadacentral` |
| `me-south-1` | `qatarcentral` |
| `ap-southeast-7` | `southeastasia` |
| `us-east-1` | `eastus2` |
| `us-west-1` | `westus3` |
| `us-east-2` | `northcentralus` |
| `us-east-2` | `southcentralus` |
| `us-west-2` | `westcentralus` |
| `ca-central-1` | `canadaeast` |
| `eu-west-2` | `ukwest` |
| `eu-central-1` | `westeurope` |
| `eu-central-1` | `polandcentral` |
| `eu-central-1` | `austriaeast` |
| `eu-west-3` | `belgiumcentral` |
| `eu-north-1` | `norwayeast` |
| `eu-north-1` | `denmarkeast` |
| `ap-northeast-2` | `koreasouth` |
| `ap-south-2` | `southindia` |
| `ap-south-1` | `westindia` |
| `ap-southeast-2` | `australiacentral` |
| `ap-southeast-2` | `newzealandnorth` |
| `sa-east-1` | `chilecentral` |

## Out of v1

Never in `components`: put the name in `"left_out"` and tell the user the sentence (the script prints it too).

| Name | Tell the user |
|---|---|
| `vm` | Virtual machines are not in this version: describe the workload as a container app (an image), or set up the VM by hand. |
| `mysql` | MySQL is not in this version: use postgres, or add MySQL by hand after generation. |
| `custom_domain` | A custom domain with its own certificate is not in this version (it needs a DNS zone you own): the app gets the cloud's generated HTTPS address; add the domain and its certificate by hand (ECS Express listener / Container Apps custom domain). |
| `private_key_vault` | A private-only secret store is not in this version: Terraform writes secret values through the store's API, so it stays reachable over the internet, protected by role-based access. |
| `multi_region` | Several regions are not in this version: generate one project per region. |
| `kubernetes` | Kubernetes clusters are not in this version: use a container app. |
| `serverless_function` | Serverless functions are not in this version: package the code as a container app. |
| `cache` | Caches (Redis and similar) are not in this version. |
| `queue` | Message queues are not in this version. |
| `cdn` | A CDN in front of the app is not in this version. |
