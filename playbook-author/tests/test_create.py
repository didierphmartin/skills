import json, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / 'scripts' / 'create.py'
CATALOG = HERE / 'fixtures' / 'mcp-catalog.md'


def run(draft, catalog=CATALOG, extra=()):
    with tempfile.TemporaryDirectory() as td:
        i = Path(td) / 'd.json'; o = Path(td) / 'out.md'
        i.write_text(json.dumps(draft))
        r = subprocess.run([sys.executable, str(SCRIPT), '-i', str(i), '-o', str(o), '--catalog', str(catalog), *extra],
                           capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr, (o.read_text() if o.exists() else '')


def load_module():
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        import create
    finally:
        sys.path.pop(0)
    return create


def load_real_catalog():
    """The shipped catalog: Okta and Google Workspace both have add_to_group, GitHub has add_user_to_team."""
    return load_module().load_catalog(str(HERE.parent / 'references' / 'mcp-catalog.md'))


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

    def test_unbound_action_lists_closest_catalog_functions(self):
        # The catalog is not in the model's context; the report names the real
        # functions so one revision can fix the action name.
        d = json.loads(json.dumps(BASE)); d['steps'][1]['text'] = '#Update Workday Record for the requester.'
        code, out, err, _ = run(d)
        self.assertEqual(code, 0, err)
        self.assertIn('#Update Workday Record is unbound (ambiguous', out)
        self.assertIn('closest: Workday: get_pto_balance, Workday: submit_time_off', out)

    def test_unbound_action_with_no_match_suggests_handoff(self):
        d = json.loads(json.dumps(BASE)); d['steps'][1]['text'] = '#Frobnicate Widgets for the requester.'
        code, out, err, _ = run(d)
        self.assertIn('no catalog function matches — use a native verb or #Prompt for Handoff', out)

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

    def test_inputs_line_opens_the_instructions(self):
        d = json.loads(json.dumps(BASE))
        d['inputs'] = [{'name': 'start_date', 'label': 'Start date', 'type': 'date', 'mandatory': True},
                       {'name': 'reason', 'label': 'Reason', 'type': 'string', 'mandatory': False}]
        code, out, err, text = run(d)
        self.assertEqual(code, 0, err)
        self.assertIn('Instructions:\nThe request is a ServiceNow job: its "answers" carry '
                      'start_date (date, required), reason (string, optional).\n1. #Lookup Users', text)

    def test_no_inputs_no_line(self):
        code, out, err, text = run(BASE)
        self.assertNotIn('ServiceNow job', text)

    def test_unattended_rejects_gate_verbs_naming_the_step(self):
        d = json.loads(json.dumps(BASE)); d['steps'][1]['text'] = '#Trigger Form asking for the dates.'
        code, out, err, _ = run(d, extra=['--unattended'])
        self.assertEqual(code, 1)
        self.assertIn('INVALID', err)
        self.assertIn('#trigger form', err.lower())
        self.assertIn('step 2', err)
        self.assertIn('step 4', err)  # #Request Approval in BASE

    def test_without_the_flag_gates_are_fine(self):
        d = json.loads(json.dumps(BASE)); d['steps'][1]['text'] = '#Trigger Form asking for the dates.'
        code, out, err, _ = run(d)
        self.assertEqual(code, 0, err)

    def test_unattended_unbound_action_is_an_error(self):
        d = json.loads(json.dumps(BASE))
        d['steps'] = [{'text': '#Frobnicate Widgets for the requester.'}, {'text': '#Resolve Request.'}]
        code, out, err, _ = run(d, extra=['--unattended'])
        self.assertEqual(code, 1)
        self.assertIn('#Frobnicate Widgets', err)
        self.assertIn('step 1', err)
        code, out, err, _ = run(d)
        self.assertEqual(code, 0, err)

    def test_unattended_clean_playbook_passes(self):
        d = json.loads(json.dumps(BASE))
        d['steps'] = [{'text': '#Get PTO Balance for the requester.'}, {'text': '#Resolve Request.'}]
        code, out, err, _ = run(d, extra=['--unattended'])
        self.assertEqual(code, 0, err)

    def test_unattended_name_cut_by_a_lowercase_word_shows_the_full_name(self):
        # "#Add User to Okta Group" is read as "#Add User": the error says so and gives the
        # name in capitalised words, which binds. A model retried this three ways in vain.
        tools, _ = load_real_catalog()
        text = 'Title: T\n\nTrigger: x\n\nInstructions:\n1. #Add User to Okta Group for the new_team.\n'
        errs = load_module().run_unattended_check(text, tools)
        self.assertEqual(len(errs), 1, errs)
        self.assertIn('read as #Add User', errs[0])
        self.assertIn('#Add User To Okta Group', errs[0])
        self.assertIn('binds to okta.add_to_group', errs[0])
        self.assertIn('step 1', errs[0])

    def test_unattended_server_prefix_with_colon_shows_the_full_name(self):
        tools, _ = load_real_catalog()
        text = 'Title: T\n\nTrigger: x\n\nInstructions:\n1. #GitHub: Add User to Team for new_team.\n'
        errs = load_module().run_unattended_check(text, tools)
        self.assertEqual(len(errs), 1, errs)
        self.assertIn('read as #GitHub', errs[0])
        self.assertIn('#GitHub Add User To Team', errs[0])
        self.assertIn('binds to github.add_user_to_team', errs[0])

    def test_unattended_unbound_lists_the_closest_functions(self):
        # A tie (Okta and Google Workspace both have add_to_group) names both servers.
        tools, _ = load_real_catalog()
        text = 'Title: T\n\nTrigger: x\n\nInstructions:\n1. #Add To Group for the new team.\n'
        errs = load_module().run_unattended_check(text, tools)
        self.assertEqual(len(errs), 1, errs)
        self.assertIn('ambiguous', errs[0])
        self.assertIn('Okta: add_to_group', errs[0])
        self.assertIn('Google Workspace: add_to_group', errs[0])

    def test_run_unattended_check_is_importable(self):
        sys.path.insert(0, str(SCRIPT.parent))
        try:
            import create
        finally:
            sys.path.pop(0)
        tools, _ = create.load_catalog(str(CATALOG))
        text = 'Title: T\n\nTrigger: x\n\nInstructions:\n1. #Get PTO Balance.\n2. Then:\n- #Request Approval from the manager.\n'
        errs = create.run_unattended_check(text, tools)
        self.assertEqual(len(errs), 1)
        self.assertIn('step 2', errs[0])
        self.assertEqual(create.main(['-i', '/nonexistent.json', '-o', '/tmp/x.md']), 2)


if __name__ == '__main__':
    unittest.main()
