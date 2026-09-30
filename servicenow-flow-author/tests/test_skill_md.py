import re, unittest
from helpers import SKILL

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
        self.assertEqual(self.fm['name'], 'servicenow-flow-author')
        self.assertEqual(self.fm['allowed-tools'], 'run_skill_script')
        self.assertEqual(self.fm['license'], 'MIT')

    def test_description(self):
        d = self.fm['description'].strip('"')
        self.assertLessEqual(len(d), 1024)
        self.assertNotRegex(d, '[<>]')
        self.assertIn('Do NOT use for', d)

    def test_context_references_exist(self):
        for ref in self.fm['context-references'].split(','):
            self.assertTrue((SKILL / ref.strip()).is_file(), ref)

    def test_body_states_the_contract(self):
        for needle in ('never write TypeScript', 'NEED_VENDOR', 'At most two revisions', '"script": "scripts/create.py"',
                       'placeholders', '"form"', 'ask step', 'createCatalogTask', 'npx now-sdk install'):
            self.assertIn(needle, self.body)


if __name__ == '__main__':
    unittest.main()


class FlowSpecDocTest(unittest.TestCase):
    def test_complete_example_is_the_valid_fixture(self):
        import json
        from helpers import errors_of, fixture
        text = (SKILL / 'references' / 'flow-spec.md').read_text(encoding='utf-8')
        block = text.split('## Complete example', 1)[1].split('```json\n', 1)[1].split('\n```', 1)[0]
        self.assertEqual(json.loads(block), fixture('p1-escalation.json'))
        self.assertEqual(errors_of(fixture('p1-escalation.json')), [])

    def test_example_onboarding_is_current(self):
        import subprocess, sys
        r = subprocess.run([sys.executable, str(SKILL / 'dev/build_example.py'), '--check'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)


class CurrentBehaviourDocTest(unittest.TestCase):
    """The model-facing texts state what create.py does now (Tasks 2–5), not what the first draft assumed."""

    def test_skill_md_and_references_match_the_script(self):
        skill = (SKILL / 'SKILL.md').read_text(encoding='utf-8')
        spec = (SKILL / 'references' / 'flow-spec.md').read_text(encoding='utf-8')
        deploy = (SKILL / 'references' / 'deploy.md').read_text(encoding='utf-8')
        for text in (skill, spec):
            self.assertIn('!=approved', text)                     # any outcome other than approved stops
            self.assertIn('event_name', text)                     # fireEvent.event_name is a placeholder
            self.assertRegex(text, r'never a typed name or sys_id|typed name or sys_id is rejected')
        self.assertIn('24h', spec)                                # a schedule "day" is 24h of schedule time
        self.assertIn('flow-spec.md → Waits', skill)
        self.assertIn('/outputs/servicenow/.vendor', skill)
        self.assertIn('~/Documents/synergyAI/outputs/servicenow/x_<code>_synergy/', skill)
        self.assertIn('No `read_outputs`', skill)
        self.assertIn('only moves the', deploy)
        self.assertIn('keys.ts` included', deploy)                # the zip carries the record keys

    def test_one_auth_command_form(self):
        auth = 'npx now-sdk auth --add https://<instance>.service-now.com --type basic'
        for f in ('SKILL.md', 'references/deploy.md', 'scripts/create.py'):
            text = (SKILL / f).read_text(encoding='utf-8')
            self.assertIn(auth, text, f)
            self.assertNotIn('auth --add <instance>', text, f)
