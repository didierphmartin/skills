import json, re, subprocess, sys, tempfile, unittest
from pathlib import Path
from helpers import SKILL, errors_of, fixture, minimal

ALLOWED = {'name', 'description', 'license', 'allowed-tools', 'metadata', 'compatibility', 'context-references'}


class SkillMdTest(unittest.TestCase):
    """The rules of skill-creator/scripts/quick_validate.py, plus the SynergyAI context-references key."""

    @classmethod
    def setUpClass(cls):
        text = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
        m = re.match(r'^---\n(.*?)\n---\n', text, re.S)
        assert m, 'no frontmatter'
        cls.body = text[m.end():]
        cls.fm = dict(re.findall(r'^([a-z-]+): ?(.*)$', m.group(1), re.M))

    def test_keys(self):
        self.assertLessEqual(set(self.fm) - {'version', 'origin'}, ALLOWED)
        self.assertEqual(self.fm['name'], 'terraform-author')
        self.assertEqual(self.fm['allowed-tools'], 'run_skill_script')
        self.assertEqual(self.fm['license'], 'MIT')

    def test_description(self):
        d = self.fm['description'].strip('"')
        self.assertLessEqual(len(d), 1024)
        self.assertNotRegex(d, '[<>]')
        self.assertIn('Do NOT use for', d)
        self.assertIn('no cloud account, credentials or network access is needed', d)
        for word in ('AWS', 'Azure', 'Terraform', 'the same on Azure/AWS'):
            self.assertIn(word, d)

    def test_context_references_exist(self):
        refs = [r.strip() for r in self.fm['context-references'].split(',')]
        self.assertEqual(refs, ['references/infra-spec.md', 'references/catalog.md'])
        for ref in refs:
            self.assertTrue((SKILL / ref).is_file(), ref)

    def test_body_states_the_contract(self):
        for needle in ('never write HCL', 'NEED_TARGET', 'At most two revisions', '"script": "scripts/create.py"',
                       '"--cloud"', 'spec.json', '"left_out"', 'placeholder', 'No `read_outputs`'):
            self.assertIn(needle, self.body)


class ReplyTemplateTest(unittest.TestCase):
    """The reply is a fixed template (the sibling skill's GREEN run showed a line count is ignored): pin it."""

    LINES = ('✅ <title> — Terraform for <AWS|Azure> (<region>).',
             '📁 ~/Documents/synergyAI/outputs/terraform/<name>-<cloud>/',
             '✏️ Fill before plan: <comma-separated placeholder names> (terraform.tfvars — see README.md).',
             '🔑 Secrets, typed at apply: <comma-separated TF_VAR_ names>.',
             '▶️ In a terminal, in that folder: terraform init, then terraform plan -out tfplan, then terraform apply tfplan.',
             '⚠️ <one sentence: the trade-off the report warns about, and what was left out when both are reported>')

    def test_rule_7_is_the_template_in_order(self):
        skill = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
        rule = skill.split('7. **Your reply IS these lines', 1)[1].split('## Steps', 1)[0]
        block = rule.split('```\n', 1)[1].split('```', 1)[0]
        self.assertEqual([l.strip() for l in block.strip().splitlines()], list(self.LINES))
        self.assertIn('nothing else', rule)
        self.assertIn("rule 7's lines", skill)


class ReferencesTest(unittest.TestCase):
    def test_complete_example_is_the_valid_fixture(self):
        text = (SKILL / 'references' / 'infra-spec.md').read_text(encoding='utf-8')
        block = text.split('## Complete example', 1)[1].split('```json\n', 1)[1].split('\n```', 1)[0]
        self.assertEqual(json.loads(block), fixture('customer-portal.json'))
        self.assertEqual(errors_of(fixture('customer-portal.json')), [])

    def test_example_is_current(self):
        r = subprocess.run([sys.executable, str(SKILL / 'dev' / 'build_example.py'), '--check'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_model_is_not_sent_to_unreachable_files(self):
        skill = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
        spec = (SKILL / 'references' / 'infra-spec.md').read_text(encoding='utf-8')
        self.assertNotIn('example-customer-portal', spec)
        self.assertIn('For maintainers: references/example-customer-portal.md', skill)

    def test_step_5_words_are_in_the_report(self):
        skill = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as out:
            r = subprocess.run([sys.executable, str(SKILL / 'scripts' / 'create.py'), '-i',
                                str(SKILL / 'tests' / 'fixtures' / 'customer-portal.json'), '--cloud', 'azure', '--out', out],
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)
        for word in ('note:', 'warning:', 'left out:', 'NEXT:'):
            self.assertIn(word, skill)
            self.assertIn(word, r.stdout)

    def test_deploy_points_to_the_readme_recovery_section(self):
        d = (SKILL / 'references' / 'deploy.md').read_text(encoding='utf-8')
        self.assertIn('"If an apply fails"', d)
        self.assertLess(d.index('terraform output'), d.index('"If an apply fails"'))

    def test_deploy_names_the_commands(self):
        d = (SKILL / 'references' / 'deploy.md').read_text(encoding='utf-8')
        for needle in ('Terraform **1.11 or later**', 'terraform plan -out tfplan', 'terraform apply tfplan',
                       'export TF_VAR_', 'ARM_SUBSCRIPTION_ID', 'aws configure', '.terraform.lock.hcl', 'terraform destroy'):
            self.assertIn(needle, d)


class StandInExitCodeTest(unittest.TestCase):
    """tests/dev/run_skill_script.py reports, and exits with, the script's real exit code."""

    def run_call(self, work, argv, spec):
        call = Path(work) / 'call.json'
        call.write_text(json.dumps({'script': 'scripts/create.py', 'argv': argv,
                                    'input_files': {'/scratch/s.json': json.dumps(spec)}}), encoding='utf-8')
        return subprocess.run([sys.executable, str(SKILL / 'tests/dev/run_skill_script.py'), '--work', str(work), str(call)],
                              capture_output=True, text=True)

    def test_exit_codes(self):
        no_target = minimal()
        del no_target['target']
        with tempfile.TemporaryDirectory() as work:
            for argv, spec, code in ((['-i', '/scratch/s.json'], minimal(name='X'), 1),                      # INVALID
                                     (['-i', '/scratch/s.json'], no_target, 1),                              # NEED_TARGET
                                     (['-i', '/scratch/s.json', '--cloud', 'azure', '--region', 'westeurope'], no_target, 0),
                                     (['-i', '/outputs/terraform/notes-azure/spec.json', '--cloud', 'aws'], no_target, 0)):
                r = self.run_call(work, argv, spec)
                self.assertEqual(r.returncode, code, r.stdout)
                self.assertIn(f'exit code: {code}\n', r.stdout)
            self.assertTrue((Path(work) / 'outputs/terraform/notes-azure/storage.tf').is_file())
            self.assertIn('"region": "eu-central-1"', (Path(work) / 'outputs/terraform/notes-aws/spec.json').read_text())


if __name__ == '__main__':
    unittest.main()
