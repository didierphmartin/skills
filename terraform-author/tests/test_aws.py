import unittest
from helpers import CATALOG, create, fixture, spec_of


def aws_files(raw: dict, region: str | None = None) -> dict:
    s = spec_of(raw, 'aws', region)
    assert not s.errors, s.errors
    return create.AwsProject(s).files()


class AwsMappingTest(unittest.TestCase):
    """Each component becomes the resources of the lab's validated scratch/aws-full/main.tf (cited per test)."""

    @classmethod
    def setUpClass(cls):
        cls.cp = aws_files(fixture('customer-portal.json'))
        cls.shop = aws_files(fixture('all-options.json'))
        cls.tools = aws_files(fixture('internal-tools.json'), 'eu-central-1')
        cls.archive = aws_files(fixture('storage-only.json'), 'eu-north-1')

    def test_versions_and_provider(self):   # research §1 pinning; aws-full provider block
        self.assertIn('required_version = ">= 1.11"', self.cp['versions.tf'])
        self.assertIn('    aws = {\n      source  = "hashicorp/aws"\n      version = "~> 6.0"\n    }', self.cp['versions.tf'])
        self.assertNotIn('random', self.cp['versions.tf'])
        self.assertIn('provider "aws" {\n  region = "eu-west-3"\n  default_tags {\n'
                      '    tags = { project = "customer-portal", env = "prod", managed_by = "terraform" }', self.cp['providers.tf'])

    def test_network(self):   # aws-full network section; separate SG rule resources (no inline rules → no Cycle)
        n = self.cp['network.tf']
        for needle in ('resource "aws_vpc" "main"', 'cidr_block           = "10.0.0.0/16"',
                       'cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)',
                       'cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index + 2)',
                       'resource "aws_nat_gateway" "main"', 'nat_gateway_id = aws_nat_gateway.main.id',
                       'resource "aws_vpc_security_group_egress_rule" "web_all"', 'resource "aws_security_group" "db"'):
            self.assertIn(needle, n)
        self.assertNotIn('ingress {', n)
        self.assertNotIn('egress {', n)
        self.assertIn('cidrsubnet(aws_vpc.main.cidr_block, 2, count.index)', self.shop['network.tf'])   # /22 → /24s

    def test_object_storage(self):   # aws-full object storage section
        s = self.cp['storage.tf']
        self.assertIn('bucket_prefix = "customer-portal-prod-uploads-"', s)
        for needle in ('block_public_acls       = true', 'restrict_public_buckets = true', '"BucketOwnerEnforced"',
                       'sse_algorithm = "aws:kms"', 'bucket_key_enabled = true', 'status = "Enabled"'):
            self.assertIn(needle, s)
        self.assertNotIn('aws_s3_bucket_versioning', self.tools['storage.tf'])   # versioning: false

    def test_postgres(self):   # aws-full database section
        d = self.cp['database.tf']
        for needle in ('engine                      = "postgres"', 'engine_version              = "17"',
                       'instance_class              = "db.t4g.small"', 'allocated_storage           = 32',
                       'storage_encrypted           = true', 'manage_master_user_password = true',
                       'publicly_accessible         = false', 'multi_az                    = false',
                       'backup_retention_period     = 7', 'deletion_protection         = true',
                       'final_snapshot_identifier   = "customer-portal-prod-db-final"'):
            self.assertIn(needle, d)
        o = self.shop['database.tf']
        self.assertIn('instance_class              = "db.r7g.xlarge"', o)
        self.assertIn('multi_az                    = true', o)
        self.assertIn('backup_retention_period     = 35', o)

    def test_secret_is_write_only(self):   # aws-full secret section; write-only needs TF >= 1.11
        self.assertIn('secret_string_wo         = var.payment_api_key\n  secret_string_wo_version = 1', self.cp['secrets.tf'])
        v = self.cp['variables.tf']
        self.assertIn('variable "payment_api_key" {', v)
        self.assertIn('  sensitive   = true\n  ephemeral   = true\n}', v)
        self.assertIn('variable "container_image" {\n  description = "The application\'s container image (registry/name:tag)."\n'
                      '  type        = string\n}', v)

    def test_public_app_runs_in_public_subnets(self):   # research §2.4 Express networking trap
        a = self.cp['app.tf']
        self.assertIn('resource "aws_ecs_express_gateway_service" "web"', a)
        self.assertIn('subnets         = aws_subnet.public[*].id', a)
        self.assertIn('cpu                     = "512"\n  memory                  = "1024"', a)
        self.assertIn('scaling_target {\n    min_task_count = 2\n    max_task_count = 4\n  }', a)
        self.assertIn('image          = var.container_image', a)
        self.assertIn('AmazonECSInfrastructureRoleforExpressGatewayServices', a)

    def test_internal_apps_run_in_private_subnets(self):
        a = self.tools['app.tf']
        self.assertEqual(a.count('subnets         = aws_subnet.private[*].id'), 2)
        self.assertIn('image          = "ghcr.io/example/admin-ui:1.4.2"', a)

    def test_edges_become_least_privilege_grants(self):   # aws-full identity section
        x = self.cp['access.tf']
        self.assertIn('actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]\n'
                      '    resources = ["${aws_s3_bucket.uploads.arn}/*"]', x)
        self.assertIn('actions   = ["secretsmanager:GetSecretValue"]\n'
                      '    resources = [aws_secretsmanager_secret.payment_api_key.arn, aws_db_instance.db.master_user_secret[0].secret_arn]', x)
        self.assertIn('resource "aws_vpc_security_group_ingress_rule" "db_from_web"', x)
        self.assertIn('referenced_security_group_id = aws_security_group.web.id', x)
        self.assertIn('actions   = ["s3:GetObject"]', self.shop['access.tf'])   # storefront: read only

    def test_env_and_secrets_reach_the_container(self):
        a = self.cp['app.tf']
        self.assertIn('name  = "UPLOADS_BUCKET"\n      value = aws_s3_bucket.uploads.bucket', a)
        self.assertIn('name  = "DB_HOST"\n      value = aws_db_instance.db.address', a)
        self.assertIn('name       = "PAYMENT_API_KEY"\n      value_from = aws_secretsmanager_secret.payment_api_key.arn', a)
        self.assertIn('value_from = "${aws_db_instance.db.master_user_secret[0].secret_arn}:password::"', a)
        self.assertIn('  depends_on = [aws_iam_role_policy.web_execution, aws_iam_role_policy_attachment.web_execution, '
                      'aws_iam_role_policy_attachment.infrastructure]', a)

    def test_only_the_files_needed(self):
        self.assertEqual(list(self.archive), ['versions.tf', 'providers.tf', 'variables.tf', 'network.tf', 'storage.tf',
                                              'secrets.tf', 'outputs.tf', 'terraform.tfvars.example', 'README.md',
                                              '.gitignore', 'spec.json'])   # no app: no app.tf, no access.tf

    def test_readme_and_report_material(self):
        r = self.cp['README.md']
        self.assertIn('# Customer portal — Terraform for AWS (eu-west-3)', r)
        self.assertIn("- `export TF_VAR_payment_api_key='…'`", r)
        self.assertIn('cd ~/Documents/synergyAI/outputs/terraform/customer-portal-aws', r)
        self.assertIn('The apps run in public subnets', r)
        self.assertIn(CATALOG['out_of_v1']['custom_domain'], r)
        self.assertIn('- `web`: `UPLOADS_BUCKET`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `PAYMENT_API_KEY`, `DB_USER`, `DB_PASSWORD`', r)
        self.assertEqual(self.cp['terraform.tfvars.example'].splitlines()[-1], 'container_image = ""')

    def test_multiline_text_stays_valid(self):   # descriptions land in comments: one line each
        raw = fixture('customer-portal.json')
        k = next(iter(raw['placeholders']))
        raw['placeholders'][k] = 'first line\nsecond line\n\nthird'
        f = aws_files(raw)
        self.assertIn(f'# first line second line third\n{k} = ""', f['terraform.tfvars.example'])
        self.assertIn(f'- `{k}` — first line second line third', f['README.md'])
        self.assertIn('"first line\\nsecond line\\n\\nthird"', f['variables.tf'])

    def test_rds_identifier_prefix_fits(self):   # identifier_prefix + 26 random characters must fit in 63
        import re
        raw = {'name': 'a' * 24, 'title': 'T', 'environment': 'e' * 10, 'target': {'cloud': 'aws', 'region': 'eu-west-3'},
               'components': [{'type': 'postgres', 'name': 'd' * 19}]}
        f = aws_files(raw)
        m = re.search(r'identifier_prefix\s+= "([^"]*)"', f['database.tf'])
        self.assertTrue(m)
        self.assertLessEqual(len(m.group(1)), 37)
        self.assertTrue(m.group(1).endswith('-'))
        self.assertNotIn('--', m.group(1))


if __name__ == '__main__':
    unittest.main()
