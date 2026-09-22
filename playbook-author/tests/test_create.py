import json, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / 'scripts' / 'create.py'
CATALOG = HERE / 'fixtures' / 'mcp-catalog.md'


def run(draft, catalog=CATALOG):
    with tempfile.TemporaryDirectory() as td:
        i = Path(td) / 'd.json'; o = Path(td) / 'out.md'
        i.write_text(json.dumps(draft))
        r = subprocess.run([sys.executable, str(SCRIPT), '-i', str(i), '-o', str(o), '--catalog', str(catalog)],
                           capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr, (o.read_text() if o.exists() else '')


BASE = {
    'name': 'pto', 'title': 'Time Off', 'trigger': 'Requester asks for PTO.',
    'steps': [
        {'text': '#Lookup Users on the requester.'},
        {'text': '#Get PTO Balance for the requester.'},
        {'text': 'Validate:', 'sub': ['If short, #Send Direct Message with the shortfall.']},
        {'text': '#Request Approval from the manager.'},
        {'text': '#Resolve Request.'},
    ],
    'tools_used': ['Workday'],
}


class CreateTest(unittest.TestCase):
    def test_valid_playbook_renders_and_binds(self):
        code, out, err, text = run(BASE)
        self.assertEqual(code, 0, err)
        self.assertIn('Title: Time Off', text)
        self.assertIn('1. #Lookup Users on the requester.', text)
        self.assertIn('- If short, #Send Direct Message', text)
        self.assertIn('Actions used: #Lookup Users; #Get PTO Balance; #Send Direct Message; #Request Approval; #Resolve Request', text)
        self.assertIn('bound    #Get PTO Balance → workday.get_pto_balance', out)
        self.assertIn('bound    #Lookup Users → okta.lookup_users', out)
        self.assertIn('Tools used: Workday; Okta', text)  # server of a bound action merged in
        self.assertIn('VALID — 5 bound, 0 unbound', out)

    def test_unlisted_action_is_an_error_with_location(self):
        d = dict(BASE, actions_used=['#Lookup Users'])
        code, out, err, _ = run(d)
        self.assertEqual(code, 1)
        self.assertIn('#get pto balance is used in the prose but not listed', err)
        self.assertIn('step 2', err)

    def test_unbound_action_is_a_warning_not_an_error(self):
        d = json.loads(json.dumps(BASE)); d['steps'][1]['text'] = '#Frobnicate Widgets for the requester.'
        code, out, err, _ = run(d)
        self.assertEqual(code, 0, err)
        self.assertIn('UNBOUND  #Frobnicate Widgets is unbound', out)
        self.assertIn('step 2', out)

    def test_prompt_for_handoff_is_native_despite_lowercase_words(self):
        d = json.loads(json.dumps(BASE))
        d['steps'][1]['text'] = 'No connector yet: #Prompt for Handoff to the IAM team.'
        code, out, err, text = run(d)
        self.assertEqual(code, 0, err)
        self.assertIn('bound    #Prompt for Handoff → native', out)
        self.assertNotIn('#Prompt is unbound', out)
        self.assertIn('Actions used: #Lookup Users; #Prompt for Handoff;', text)

    def test_report_says_next_step_when_actions_stay_unbound(self):
        d = json.loads(json.dumps(BASE)); d['steps'][1]['text'] = '#Frobnicate Widgets for the requester.'
        code, out, err, _ = run(d)
        self.assertEqual(code, 0, err)
        self.assertIn('VALID — 4 bound, 1 unbound', out)
        self.assertIn('NEXT: rename the UNBOUND actions and run again', out)
        self.assertIn('after two revisions save anyway', out)

    def test_report_says_save_now_when_all_bound(self):
        code, out, err, _ = run(BASE)
        self.assertEqual(code, 0, err)
        self.assertIn('VALID — 5 bound, 0 unbound', out)
        self.assertIn('NEXT: call save_playbook_agent now', out)

    def test_custom_prefix_is_rejected_with_the_plain_name(self):
        # Console writes integration actions as the function name (#Archive
        # Channel, #Activate Okta User); "Custom" is not part of the name.
        d = json.loads(json.dumps(BASE)); d['steps'][1]['text'] = '#Custom Workday Get PTO Balance for the requester.'
        code, out, err, _ = run(d)
        self.assertEqual(code, 1)
        self.assertIn('#Custom Workday Get PTO Balance: drop the "Custom" prefix', err)
        self.assertIn('write it as #Get PTO Balance', err)
        self.assertIn('step 2', err)

    def test_missing_catalog_skips_binding(self):
        code, out, err, _ = run(BASE, catalog='/nonexistent.md')
        self.assertEqual(code, 0, err)
        self.assertIn('binding check skipped', out)


if __name__ == '__main__':
    unittest.main()
