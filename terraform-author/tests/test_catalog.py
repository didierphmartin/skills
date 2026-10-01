import json, re, unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
CATALOG = json.loads((SKILL / 'references' / 'catalog.json').read_text(encoding='utf-8'))


class CatalogTest(unittest.TestCase):
    """catalog.json is what create.py checks against; the numbers come from the research and the lab."""

    def test_versions_are_the_lab_ones(self):   # research §1; lab plugin cache
        self.assertEqual(CATALOG['terraform'], '>= 1.11')                      # write-only arguments
        p = CATALOG['providers']
        self.assertEqual((p['aws']['version'], p['aws']['lab']), ('~> 6.0', '6.67.0'))
        self.assertEqual((p['azurerm']['version'], p['azurerm']['lab']), ('~> 5.0', '5.7.0'))
        self.assertEqual((p['random']['version'], p['random']['lab']), ('~> 3.7', '3.9.1'))   # ephemeral random_password

    def test_regions(self):
        aws, azure = CATALOG['clouds']['aws']['regions'], CATALOG['clouds']['azure']['regions']
        self.assertEqual(len(aws), 32)
        self.assertIn('eu-west-3', aws)
        self.assertNotIn('of-east-1', aws)                     # sovereign partitions left out
        self.assertEqual(len(azure), 49)
        self.assertTrue(azure['francecentral'])                # has availability zones
        self.assertFalse(azure['westus'])
        self.assertNotIn('francesouth', azure)                 # restricted access
        for a, z in CATALOG['region_pairs']:
            self.assertIn(a, aws)
            self.assertIn(z, azure)
        self.assertEqual(set(aws), {a for a, _ in CATALOG['region_pairs']})       # every region has a nearest one
        self.assertEqual(set(azure), {z for _, z in CATALOG['region_pairs']})

    def test_sizes_from_the_spec(self):   # spec: size table
        s = CATALOG['sizes']
        self.assertEqual([s[k]['aws'] for k in ('small', 'medium', 'large')], ['db.t4g.small', 'db.m7g.large', 'db.r7g.xlarge'])
        self.assertEqual([s[k]['azure'] for k in ('small', 'medium', 'large')],
                         ['B_Standard_B1ms', 'GP_Standard_D2ds_v5', 'MO_Standard_E4ds_v5'])
        self.assertFalse(s['small']['azure_ha'])               # Burstable has no HA

    def test_components_and_out_of_v1(self):
        c = CATALOG['components']
        self.assertEqual(list(c), ['container_app', 'postgres', 'object_storage', 'secret'])
        self.assertEqual(c['container_app']['options']['cpu']['values'], [0.25, 0.5, 1, 2, 4])
        self.assertEqual(c['postgres']['options']['backup_days'], {'default': 7, 'min': 7, 'max': 35})
        self.assertEqual(c['object_storage']['levels'], ['read', 'readwrite'])
        for k in ('vm', 'mysql', 'custom_domain', 'private_key_vault', 'multi_region'):
            self.assertIn(k, CATALOG['out_of_v1'])

    def test_roles(self):
        r = CATALOG['roles']
        self.assertEqual(r['azure']['object_storage.readwrite'], 'Storage Blob Data Contributor')
        self.assertEqual(r['azure']['secret.read'], 'Key Vault Secrets User')
        self.assertEqual(r['aws']['object_storage.read']['objects'], ['s3:GetObject'])


class CatalogDocTest(unittest.TestCase):
    """catalog.md (read by the model) says what catalog.json (read by the script) allows."""

    @classmethod
    def setUpClass(cls):
        cls.md = (SKILL / 'references' / 'catalog.md').read_text(encoding='utf-8')

    def test_every_region_is_listed(self):
        for r in CATALOG['clouds']['aws']['regions']:
            self.assertIn(f'`{r}`', self.md, r)
        for r in CATALOG['clouds']['azure']['regions']:
            self.assertIn(f'`{r}`', self.md, r)

    def test_region_pairs(self):
        for a, z in CATALOG['region_pairs']:
            self.assertRegex(self.md, rf'\| `{re.escape(a)}` \| `{re.escape(z)}` \|')

    def test_options_and_defaults(self):
        for ctype, spec in CATALOG['components'].items():
            self.assertIn(f'### `{ctype}`', self.md)
            for opt, o in spec['options'].items():
                self.assertIn(f'`{opt}`', self.md, f'{ctype}.{opt}')
                if 'default' in o and not isinstance(o['default'], (dict, str)):
                    d = 'true' if o['default'] is True else 'false' if o['default'] is False else str(o['default'])
                    self.assertRegex(self.md, rf'`{opt}` \| [^|]*\| `{re.escape(d)}`', f'{ctype}.{opt} default')

    def test_sizes_and_out_of_v1(self):
        for name, s in CATALOG['sizes'].items():
            self.assertRegex(self.md, rf'\| `{name}` \| `{s["aws"]}` \| `{s["azure"]}` \|')
        for k, msg in CATALOG['out_of_v1'].items():
            self.assertIn(f'| `{k}` | {msg} |', self.md)


if __name__ == '__main__':
    unittest.main()
