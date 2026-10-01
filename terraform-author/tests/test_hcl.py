import unittest
from helpers import create

Hcl, Raw = create.Hcl, create.Raw


class HclTest(unittest.TestCase):
    """The writer's output is what `terraform fmt` writes (the lab gate runs fmt -check on whole projects)."""

    def test_values(self):
        v = Hcl.value
        self.assertEqual(v('a"b\\c ${x} %{y}'), '"a\\"b\\\\c $${x} %%{y}"')   # literal text, never interpolated
        self.assertEqual(v(Raw('aws_vpc.main.id')), 'aws_vpc.main.id')
        self.assertEqual((v(True), v(8080), v(0.5), v(1.0), v(16777216)), ('true', '8080', '0.5', '1', '16777216'))
        self.assertEqual(v(['a', Raw('b.c')]), '["a", b.c]')
        self.assertEqual(v({'Name': 'x', 'cost-center': 'y'}), '{ Name = "x", "cost-center" = "y" }')

    def test_alignment_runs_end_at_blocks_and_comments(self):
        b = Hcl('resource', 'aws_db_instance', 'db').set('engine', 'postgres').set('storage_encrypted', True)
        b.block('timeouts').set('create', '60m')
        b.note('after a block').set('tags', {}).set('depends_on', [Raw('x.y')])
        self.assertEqual(create.hcl_file([b]),
                         'resource "aws_db_instance" "db" {\n'
                         '  engine            = "postgres"\n'
                         '  storage_encrypted = true\n'
                         '  timeouts {\n'
                         '    create = "60m"\n'
                         '  }\n'
                         '  # after a block\n'
                         '  tags       = {}\n'
                         '  depends_on = [x.y]\n'
                         '}\n')

    def test_empty_block_object_attribute_and_file_layout(self):
        t = Hcl('terraform').set('required_version', '>= 1.11')
        t.block('required_providers').object('aws').set('source', 'hashicorp/aws').set('version', '~> 6.0')
        p = Hcl('provider', 'azurerm', comment='why')
        p.block('features')
        self.assertEqual(create.hcl_file([t, p]),
                         'terraform {\n  required_version = ">= 1.11"\n  required_providers {\n    aws = {\n'
                         '      source  = "hashicorp/aws"\n      version = "~> 6.0"\n    }\n  }\n}\n\n'
                         '# why\nprovider "azurerm" {\n  features {}\n}\n')

    def test_prefix(self):
        self.assertEqual(create.prefix('customer-portal-prod-web_api-task', 38), 'customer-portal-prod-web-api-task-')
        self.assertEqual(create.prefix('a-very-long-project-name-prod-uploads', 37), 'a-very-long-project-name-prod-upload-')
        self.assertLessEqual(len(create.prefix('x' * 60, 38)), 38)
        self.assertNotIn('--', create.prefix('abc-' + 'd' * 40, 38))


if __name__ == '__main__':
    unittest.main()
