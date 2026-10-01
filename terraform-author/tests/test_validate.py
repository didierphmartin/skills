import unittest
from helpers import errors_of, fixture, minimal, spec_of


def app(name='web', **over):
    a = {'type': 'container_app', 'name': name, 'image': {'placeholder': 'image'}}
    a.update(over)
    return a


def with_app(*comps, access=(), **over):
    """A spec with apps/components, the image placeholder declared."""
    return minimal(components=list(comps), access=list(access), placeholders={'image': 'The image.'}, **over)


class ValidateTest(unittest.TestCase):
    def test_fixtures_are_valid(self):
        for f in ('customer-portal.json', 'customer-portal-fr.json', 'internal-tools.json', 'all-options.json',
                  'storage-only.json', 'azure-mixed-apps.json'):
            self.assertEqual(errors_of(fixture(f)), [], f)

    def test_defaults_are_filled(self):
        s = spec_of(with_app(app(), {'type': 'postgres', 'name': 'pg'}))
        a, pg = s.components['web'], s.components['pg']
        self.assertEqual((a['port'], a['cpu'], a['memory_gb'], a['min_instances'], a['max_instances'], a['public_https']),
                         (8080, 0.5, 1.0, 1, 3, False))
        self.assertEqual((pg['version'], pg['size'], pg['storage_gb'], pg['backup_days'], pg['high_availability']),
                         ('17', 'small', 32, 7, False))
        self.assertEqual((s.environment, s.cidr), ('prod', '10.0.0.0/16'))

    def test_all_errors_at_once(self):
        bad = with_app(app(cpu=3), {'type': 'postgres', 'name': 'pg', 'size': 'huge'},
                       access=[{'from': 'web', 'to': 'nowhere'}], name='Bad Name')
        errs = errors_of(bad)
        self.assertEqual(len(errs), 4, errs)
        self.assertTrue(any(e.startswith('name:') for e in errs))
        self.assertIn('components.web.cpu: must be one of 0.25, 0.5, 1, 2, 4', errs)
        self.assertIn('components.pg.size: must be one of small, medium, large', errs)
        self.assertIn('access[0]: to "nowhere" is not a component name', errs)

    def test_unknown_component_names_the_v1_types(self):
        errs = errors_of(minimal(components=[{'type': 'lambda', 'name': 'f'}]))
        self.assertEqual(len(errs), 1)
        self.assertIn('unknown type "lambda"; v1 types: container_app, postgres, object_storage, secret', errs[0])

    def test_out_of_v1_component_gives_the_message_for_the_user(self):
        errs = errors_of(minimal(components=[{'type': 'vm', 'name': 'box'}, {'type': 'object_storage', 'name': 'f'}]))
        self.assertEqual(errs, ['components[0]: type "vm" is out of v1 — remove it, add "vm" to "left_out" and tell '
                                'the user: Virtual machines are not in this version: describe the workload as a '
                                'container app (an image), or set up the VM by hand.'])

    def test_custom_domain_is_out_of_v1(self):
        errs = errors_of(with_app(app(domain='portal.example.com')))
        self.assertEqual(len(errs), 1)
        self.assertTrue(errs[0].startswith('components.web.domain: out of v1 — remove it, add "custom_domain" to "left_out"'))
        self.assertEqual(errors_of(minimal(left_out=['custom_domain', 'mysql'])), [])
        self.assertIn('left_out: "ftp" is not an out-of-v1 name', errors_of(minimal(left_out=['ftp']))[0])

    def test_bad_region(self):
        errs = errors_of(minimal(target={'cloud': 'aws', 'region': 'westeurope'}))
        self.assertEqual(errs, ['target.region: "westeurope" is not a aws region; see references/catalog.md → Regions'])
        self.assertEqual(errors_of(minimal(target={'cloud': 'azure', 'region': 'westeurope'})), [])
        self.assertIn('"gcp" is not a v1 target', errors_of(minimal(target={'cloud': 'gcp', 'region': 'x'}))[0])

    def test_edge_to_a_missing_or_wrong_component(self):
        comps = [app(), {'type': 'secret', 'name': 'key'}, app('api')]
        self.assertIn('to "kye" is not a component name', errors_of(with_app(*comps, access=[{'from': 'web', 'to': 'kye'}]))[0])
        self.assertIn('from "key" must be the name of a container_app',
                      errors_of(with_app(*comps, access=[{'from': 'key', 'to': 'web'}]))[0])
        self.assertIn('apps reach each other over their HTTPS address',
                      errors_of(with_app(*comps, access=[{'from': 'web', 'to': 'api'}]))[0])
        self.assertIn('level for secret must be one of: read',
                      errors_of(with_app(*comps, access=[{'from': 'web', 'to': 'key', 'level': 'readwrite'}]))[0])

    def test_storage_edge_needs_a_level_and_no_duplicates(self):
        comps = [app(), {'type': 'object_storage', 'name': 'files'}]
        self.assertIn('level for object_storage must be one of: read, readwrite',
                      errors_of(with_app(*comps, access=[{'from': 'web', 'to': 'files'}]))[0])
        twice = [{'from': 'web', 'to': 'files', 'level': 'read'}, {'from': 'web', 'to': 'files', 'level': 'readwrite'}]
        self.assertEqual(errors_of(with_app(*comps, access=twice)), ['access[1]: web → files is listed twice'])

    def test_placeholders_declared_and_used(self):
        undeclared = minimal(components=[app()])
        self.assertEqual(errors_of(undeclared),
                         ['placeholders.image: used but not declared: add "<name>": "<what the user must provide>"'])
        unused = with_app(app(image='nginx:1.27'))
        self.assertEqual(errors_of(unused), ['placeholders.image: declared but never used; remove it'])
        no_image = minimal(components=[{'type': 'container_app', 'name': 'web'}])
        self.assertEqual(errors_of(no_image), ['components.web.image: required: the image the user gave, or '
                                               '{"placeholder": "<name>"} — never an invented image'])

    def test_env_placeholders_count_as_used(self):
        s = with_app(app(env={'API_URL': {'placeholder': 'api_url'}}))
        s['placeholders']['api_url'] = 'The partner API URL.'
        self.assertEqual(errors_of(s), [])
        self.assertIn('UPPER_CASE', errors_of(with_app(app(env={'api_url': 'x'})))[0])

    def test_memory_must_be_twice_cpu(self):
        self.assertEqual(errors_of(with_app(app(cpu=1, memory_gb=3))),
                         ['components.web.memory_gb: must be 2 × cpu (2 for cpu 1): the sizes both clouds accept are '
                          '0.25/0.5, 0.5/1, 1/2, 2/4, 4/8 (cpu/memory_gb)'])
        self.assertEqual(errors_of(with_app(app(cpu=0.25, memory_gb=0.5))), [])

    def test_names(self):
        self.assertIn('name used twice', errors_of(minimal(components=[{'type': 'secret', 'name': 'k'}, {'type': 'secret', 'name': 'k'}]))[0])
        self.assertIn('reserved', errors_of(minimal(components=[{'type': 'object_storage', 'name': 'blob'}]))[0])
        self.assertIn('lowercase slug', errors_of(minimal(components=[{'type': 'object_storage', 'name': 'My-Files'}]))[0])
        clash = minimal(components=[{'type': 'postgres', 'name': 'db'}, {'type': 'secret', 'name': 'db_password'}])
        self.assertIn('taken by the generated password of the database "db"', errors_of(clash)[0])

    def test_terraform_reserved_variable_names(self):
        raw = minimal(components=[{'type': 'secret', 'name': 'version'}])
        self.assertEqual(errors_of(raw), ['components.version: Terraform reserves this name for itself; rename the secret'])

    def test_network_range(self):
        self.assertIn('private IPv4 range from /16 to /22', errors_of(minimal(network={'cidr': '8.8.0.0/16'}))[0])
        self.assertIn('private IPv4 range from /16 to /22', errors_of(minimal(network={'cidr': '10.0.0.0/24'}))[0])
        self.assertEqual(errors_of(minimal(network={'cidr': '192.168.4.0/22'})), [])


class TargetRulesTest(unittest.TestCase):
    def test_missing_target_is_need_target_not_an_error(self):
        raw = minimal()
        del raw['target']
        s = spec_of(raw)
        self.assertEqual((s.errors, s.need_target), ([], True))
        s = spec_of(raw, cloud='azure')
        self.assertEqual((s.errors, s.need_target), ([], True))          # region still missing
        s = spec_of(raw, cloud='azure', region='swedencentral')
        self.assertEqual((s.errors, s.need_target, s.region), ([], False, 'swedencentral'))

    def test_other_cloud_maps_the_region(self):
        s = spec_of(fixture('customer-portal.json'), cloud='azure')
        self.assertEqual((s.cloud, s.region, s.errors), ('azure', 'francecentral', []))
        self.assertIn('nearest to eu-west-3', s.notes[0])
        s = spec_of(minimal(target={'cloud': 'azure', 'region': 'westeurope'}), cloud='aws')
        self.assertEqual(s.region, 'eu-central-1')                        # Azure → AWS: first row with westeurope
        s = spec_of(minimal(target={'cloud': 'aws', 'region': 'eu-west-3'}), cloud='aws', region=None)
        self.assertEqual((s.region, s.notes), ('eu-west-3', []))          # same cloud: the spec's region

    def test_azure_ha_on_small_is_refused(self):
        raw = minimal(components=[{'type': 'postgres', 'name': 'pg', 'high_availability': True}])
        self.assertEqual(errors_of(raw), [])                              # AWS: Multi-AZ works on db.t4g.small
        self.assertEqual(errors_of(raw, cloud='azure', region='westeurope'),
                         ['components.pg.high_availability: size "small" is Burstable on Azure, which has no high '
                          'availability; use size "medium" or set high_availability to false'])

    def test_azure_storage_names_must_differ(self):
        raw = minimal(components=[{'type': 'object_storage', 'name': 'reports_2025'}, {'type': 'object_storage', 'name': 'reports_2026'}])
        self.assertEqual(errors_of(raw), [])
        self.assertEqual(errors_of(raw, cloud='azure', region='westeurope'),
                         ['components.reports_2026: on Azure its storage account name would match the one of '
                          '"reports_2025" (first 8 letters and digits); rename one'])

    def test_aws_apps_share_public_https(self):
        raw = with_app(app(public_https=True), app('admin', public_https=False))
        self.assertIn('on AWS all container apps of one project share public_https', errors_of(raw)[0])
        self.assertEqual(errors_of(raw, cloud='azure', region='westeurope'), [])


if __name__ == '__main__':
    unittest.main()
