# Deploy a generated project

The project is in `~/Documents/synergyAI/outputs/terraform/<name>-<cloud>/` (the README there repeats this for
that project). SynergyAI never runs Terraform: you do, in a terminal.

## Prerequisites (once)

- Terraform **1.11 or later** (`terraform version`): the projects use write-only arguments, which keep secret
  values out of the state.
- AWS: credentials for the target account — `aws configure`, or `aws sso login --profile <p>` then
  `export AWS_PROFILE=<p>`. The account needs rights to create VPC, ECS, RDS, S3, IAM, Secrets Manager and
  CloudWatch Logs resources.
- Azure: `az login`, then `export ARM_SUBSCRIPTION_ID=$(az account show --query id -o tsv)`. Your account needs
  Owner (or Contributor + User Access Administrator) on the subscription: the project creates role assignments.

## Fill the placeholders

```sh
cd ~/Documents/synergyAI/outputs/terraform/<name>-<cloud>
cp terraform.tfvars.example terraform.tfvars
```

Set every value in `terraform.tfvars` (for a container image: `registry/name:tag`, pushed where the cloud can pull
it). `terraform.tfvars` is ignored by git.

## Secrets

Each secret is a variable read at apply time and never stored. Export it in the same terminal, just before plan
and apply:

```sh
export TF_VAR_payment_api_key='…'
```

## Plan and apply

```sh
terraform init
terraform validate
terraform plan -out tfplan
terraform apply tfplan
```

`terraform output` then shows the app addresses, database endpoints and storage names. Commit
`.terraform.lock.hcl` with the project. State is local (`terraform.tfstate`, ignored by git): keep it safe, or move
it to a remote backend (S3 / Azure Storage) before a team works on it.

If an apply fails part-way, read the "If an apply fails" section of the project's README (Azure projects with a database) before applying again: it says which two version numbers to raise together.

## Change it

Ask SynergyAI to change the description and run the skill again: the generated files are replaced (your
`terraform.tfvars`, lock file and state stay), then run `terraform plan` to see the difference.

## Remove it

`terraform destroy`. Databases (and, on Azure, storage accounts) are protected first: on AWS set `deletion_protection = false` in `database.tf` and
apply (a final snapshot is kept); on Azure remove the `lifecycle { prevent_destroy = true }` blocks of the databases and storage accounts. An Azure key
vault keeps its name reserved for 7 days after deletion (purge protection).
