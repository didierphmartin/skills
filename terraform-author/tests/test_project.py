import tempfile, unittest
from pathlib import Path
from helpers import fixture, minimal, run_cli


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.out = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def test_valid_writes_the_project_and_reports(self):
        r = run_cli(fixture('customer-portal.json'), self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        root = self.out / 'customer-portal-aws'
        self.assertEqual(sorted(p.name for p in root.iterdir()),
                         sorted(['.gitignore', 'README.md', 'access.tf', 'app.tf', 'database.tf', 'network.tf', 'outputs.tf',
                                 'providers.tf', 'secrets.tf', 'spec.json', 'storage.tf', 'terraform.tfvars.example',
                                 'variables.tf', 'versions.tf']))
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], 'VALID — "Customer portal" (customer-portal) on AWS eu-west-3')
        self.assertIn('  container_image — The application\'s container image (registry/name:tag).', lines)
        self.assertIn('secrets (typed at apply time, never stored): TF_VAR_payment_api_key', lines)
        self.assertTrue(any(l.startswith('warning: The apps run in public subnets') for l in lines))
        self.assertTrue(any(l.startswith('left out: A custom domain') for l in lines))
        self.assertTrue(lines[-1].startswith('NEXT: your reply is exactly the lines of SKILL.md rule 7'))
        self.assertIn('.terraform/\n*.tfstate\n', (root / '.gitignore').read_text())

    def test_invalid_writes_nothing(self):
        bad = minimal(components=[{'type': 'vm', 'name': 'box'}, {'type': 'secret', 'name': 'k', 'size': 1}])
        r = run_cli(bad, self.out)
        self.assertEqual(r.returncode, 1)
        self.assertIn('INVALID — 2 problem(s):', r.stderr)
        self.assertIn('NEXT: fix every problem in one revision', r.stderr)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_need_target_once_the_spec_is_valid(self):
        raw = minimal()
        del raw['target']
        r = run_cli(raw, self.out)
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.startswith('NEED_TARGET — the spec is valid'))
        self.assertIn('"AWS or Azure, and which region?"', r.stdout)
        self.assertEqual(list(self.out.iterdir()), [])
        r = run_cli(raw | {'title': ''}, self.out)                     # invalid: INVALID first, not NEED_TARGET
        self.assertIn('INVALID', r.stderr)
        self.assertNotIn('NEED_TARGET', r.stdout)
        r = run_cli(raw, self.out, '--cloud', 'aws', '--region', 'eu-central-1')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('region = "eu-central-1"', (self.out / 'notes-aws' / 'providers.tf').read_text())

    def test_rerun_replaces_generated_files_and_keeps_the_others(self):
        self.assertEqual(run_cli(fixture('customer-portal.json'), self.out).returncode, 0)
        root = self.out / 'customer-portal-aws'
        (root / 'terraform.tfvars').write_text('container_image = "x"\n')
        (root / '.terraform.lock.hcl').write_text('# lock\n')
        smaller = fixture('customer-portal.json')
        smaller['components'] = [c for c in smaller['components'] if c['type'] == 'object_storage']
        smaller['access'], smaller['placeholders'] = [], {}
        self.assertEqual(run_cli(smaller, self.out).returncode, 0)
        names = {p.name for p in root.iterdir()}
        self.assertNotIn('app.tf', names)                     # generated before, not now: removed
        self.assertNotIn('database.tf', names)
        self.assertIn('terraform.tfvars', names)              # the user's files stay
        self.assertIn('.terraform.lock.hcl', names)

    def test_env_name_set_by_an_edge_is_refused(self):
        raw = fixture('customer-portal.json')
        raw['components'][0]['env']['DB_HOST'] = 'localhost'
        r = run_cli(raw, self.out)
        self.assertEqual(r.returncode, 1)
        self.assertIn('components.web.env.DB_HOST: this name is set by an access edge; remove it from env', r.stderr)

    def test_unreadable_spec_or_argv_is_exit_2(self):
        bad = self.out / 'bad.json'
        bad.write_text('{"name": ')
        self.assertEqual(run_cli(bad, self.out).returncode, 2)
        self.assertEqual(run_cli(minimal(), self.out, '--cloud', 'gcp').returncode, 2)

    def test_errors_beat_need_target_when_both_apply(self):
        raw = minimal(title='')
        del raw['target']
        r = run_cli(raw, self.out)
        self.assertEqual(r.returncode, 1)
        self.assertIn('INVALID — 1 problem(s):', r.stderr)
        self.assertNotIn('NEED_TARGET', r.stdout)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_unwritable_out_is_one_line_exit_2(self):
        blocker = self.out / 'file'
        blocker.write_text('x')
        r = run_cli(minimal(), blocker / 'sub')
        self.assertEqual(r.returncode, 2)
        self.assertTrue(r.stderr.startswith('ERROR: cannot write '), r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1)
        self.assertNotIn('Traceback', r.stderr)

    def test_help_exits_0(self):
        r = run_cli(minimal(), self.out, '--help')
        self.assertEqual(r.returncode, 0)
        self.assertIn('usage:', r.stdout)

    def test_non_object_spec_is_invalid_not_a_crash(self):
        r = run_cli([], self.out)          # a JSON list, via a file
        self.assertEqual(r.returncode, 1)
        self.assertIn('INVALID', r.stderr)
        self.assertNotIn('Traceback', r.stderr)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_hostile_name_is_invalid_and_writes_nothing(self):
        r = run_cli(minimal(name='../evil'), self.out)
        self.assertEqual(r.returncode, 1)
        self.assertIn('INVALID', r.stderr)
        self.assertEqual(list(self.out.iterdir()), [])
        self.assertFalse((self.out.parent / 'evil-aws').exists())


if __name__ == '__main__':
    unittest.main()
