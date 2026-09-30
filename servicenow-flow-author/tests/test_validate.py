import copy, unittest
from helpers import catalog_min, errors_of, fixture, minimal


def one(errors, *needles):
    hits = [e for e in errors if all(n in e for n in needles)]
    assert hits, f'no error containing {needles} in {errors}'
    return hits[0]


def ask(sid='ask_badge', variables=None, **over):
    a = {'short_description': 'Choose the badge colour', 'assignment_group': {'placeholder': 'security_group'},
         'variables': variables or [{'name': 'colour', 'label': 'Colour', 'type': 'single_line_text', 'mandatory': True}]}
    a.update(over)
    return {'id': sid, 'label': 'Ask security', 'ask': a}


def with_ask(*steps, **over) -> dict:
    s = catalog_min(**over)
    s['placeholders']['security_group'] = {'kind': 'group', 'description': 'Security desk.'}
    s['steps'] = list(steps) + s['steps']
    return s


class ValidateTest(unittest.TestCase):
    def test_reference_examples_are_valid(self):
        for name in ('onboarding-en.json', 'onboarding-fr.json', 'all-constructs.json', 'p1-escalation.json'):
            self.assertEqual(errors_of(fixture(name)), [], name)
        self.assertEqual(errors_of(minimal()), [])
        self.assertEqual(errors_of(catalog_min()), [])
        self.assertEqual(errors_of(with_ask(ask())), [])

    def test_unknown_action_lists_the_core_actions(self):
        s = minimal(steps=[{'id': 'x', 'label': 'x', 'action': 'sendTeamsMessage', 'inputs': {}}])
        one(errors_of(s), 'action "sendTeamsMessage" is not a documented core action', 'askForApproval')

    def test_script_is_rejected_with_the_alternative(self):
        s = minimal(steps=[{'id': 'x', 'label': 'x', 'action': 'script', 'inputs': {}}])
        one(errors_of(s), 'never writes code')

    def test_go_back_to_names_do_until(self):   # Review Focus 2
        s = minimal(steps=minimal()['steps'] + [{'id': 'again', 'label': 'Retour', 'logic': 'go_back_to'}])
        one(errors_of(s), 'logic "go_back_to" is not available', 'do_until')

    def test_make_a_decision_names_else_if(self):   # Review Focus 2
        s = minimal(steps=[{'id': 'd', 'label': 'd', 'logic': 'make_a_decision'}])
        one(errors_of(s), 'else_if')

    def test_forward_pill(self):
        s = minimal(steps=[{'id': 'early', 'label': 'e', 'action': 'log', 'inputs': {'log_message': [{'pill': 'late.Count'}]}},
                           {'id': 'late', 'label': 'l', 'action': 'lookUpRecords', 'inputs': {'table': 'incident'}}])
        one(errors_of(s), 'step "late" is not available here')

    def test_pill_out_of_a_container(self):
        s = fixture('all-constructs.json')
        s['steps'][-1]['inputs']['record'] = {'pill': 'first_new.Record'}
        one(errors_of(s), 'first_new', 'wfa-flow-logic-api.md:260')

    def test_unknown_output(self):
        s = minimal(steps=[{'id': 'a', 'label': 'a', 'action': 'lookUpRecords', 'inputs': {'table': 'incident'}},
                           {'id': 'b', 'label': 'b', 'action': 'log', 'inputs': {'log_message': [{'pill': 'a.count'}]}}])
        one(errors_of(s), 'step "a" (lookUpRecords) has outputs: Records, Count, Table')

    def test_unknown_trigger_table_is_one_error(self):   # Review Focus 3
        s = fixture('p1-escalation.json')
        s['trigger']['table'] = 'u_hr_case'
        errs = errors_of(s)
        self.assertEqual(len(errs), 1, errs)
        one(errs, 'trigger table "u_hr_case" is not in the SDK 4.13.3 table index')

    def test_unknown_field_points_to_a_form_variable(self):   # Review Focus 3
        s = minimal(steps=[{'id': 'l', 'label': 'l', 'action': 'log', 'inputs': {'log_message': [{'pill': 'trigger.current.u_badge'}]}}])
        one(errors_of(s), 'table incident has no field "u_badge"', 'a variable of the "form" or of an ask step')

    def test_new_fields_is_not_a_spec_key(self):   # decision 6: no invented columns
        s = minimal(new_fields=[{'table': 'sys_user', 'name': 'start_date', 'type': 'date', 'label': 'Start date'}])
        one(errors_of(s), 'unknown key "new_fields"', 'a variable of the "form"')

    def test_unknown_field_in_a_condition(self):   # Review Focus 3
        s = minimal(trigger={'type': 'record_created', 'table': 'incident', 'condition': 'u_region=emea'})
        one(errors_of(s), 'condition field "u_region" is not on table incident')

    def test_unknown_instance_record_must_be_a_placeholder(self):   # Review Focus 3
        s = fixture('onboarding-en.json')
        s['steps'][1]['steps'][0]['inputs']['notification'] = {'placeholder': 'hr_notice'}
        errs = errors_of(s)
        one(errs, 'placeholder "hr_notice" is not declared')
        one(errs, 'placeholders.hr_rejection_notification', 'never used')

    def test_task_must_link_to_the_trigger_record(self):
        s = fixture('p1-escalation.json')
        del s['steps'][2]['steps'][0]['inputs']['field_values']['parent']
        one(errors_of(s), 'war_room_task', 'not linked to the incident record', 'parent')

    def test_parallel_cannot_nest(self):
        leaf = lambda i: [{'id': f'l{i}', 'label': 'l', 'action': 'log', 'inputs': {'log_message': 'x'}}]
        inner = {'id': 'inner', 'label': 'i', 'logic': 'parallel', 'branches': [leaf(1), leaf(2)]}
        s = minimal(steps=[{'id': 'outer', 'label': 'o', 'logic': 'parallel', 'branches': [[inner], leaf(3)]}])
        one(errors_of(s), 'cannot contain another parallel block')

    def test_pills_forbidden_in_email_body(self):
        s = fixture('onboarding-en.json')
        s['steps'][5]['inputs']['ah_body'] = ['Hi ', {'pill': 'form.new_hire.name'}]
        one(errors_of(s), 'ah_body', 'static text only')

    def test_text_with_pills_only_where_it_interpolates(self):
        s = fixture('onboarding-en.json')
        s['steps'][2]['branches'][0][0]['inputs']['ah_short_description'] = ['For ', {'pill': 'form.new_hire.name'}]
        one(errors_of(s), 'ah_short_description', 'not interpolated')

    def test_required_input_and_unknown_input(self):
        s = minimal(steps=[{'id': 'm', 'label': 'm', 'action': 'sendEmail', 'inputs': {'ah_to': 'a@b.c', 'to': 'x'}}])
        errs = errors_of(s)
        one(errs, 'sendEmail needs input "ah_subject"')
        one(errs, 'sendEmail has no input "to"')

    def test_duplicate_ids(self):
        s = minimal(steps=minimal()['steps'] * 2)
        one(errors_of(s), 'id is used twice')

    def test_step_id_cannot_take_a_generated_record_key(self):   # <flow>__item would be defined twice
        for sid in ('item', 'hide_asked', 'show_asked'):
            step = dict(catalog_min()['steps'][0], id=sid)
            one(errors_of(catalog_min(steps=[step])), f'id "{sid}"', 'reserved word')

    def test_table_given_as_a_pill_or_placeholder_is_invalid_not_a_crash(self):
        for action, key, extra in (('createRecord', 'table_name', {'values': {'short_description': 'x'}}),
                                   ('lookUpRecords', 'table', {'conditions': 'active=true'})):
            for value in ({'placeholder': 'some_table'}, {'pill': 'trigger.current.sys_class_name'}, ['incident']):
                s = minimal(steps=[{'id': 'x', 'label': 'x', 'action': action, 'inputs': dict(extra, **{key: value})}])
                one(errors_of(s), f'inputs.{key}', 'a table name is literal text')

    def test_vendor_independent_limits(self):
        s = fixture('onboarding-en.json')
        s['steps'][0]['inputs']['approval_reason'] = 'x' * 161
        one(errors_of(s), 'at most 160 characters')

    def test_untyped_action_table_is_a_note_not_an_error(self):   # Review Focus 3
        from helpers import VOCAB, create
        s = fixture('p1-escalation.json')
        errs, ir = create.check_spec(s, VOCAB)
        self.assertEqual(errs, [])
        one(ir['notes'], 'table "incident_task" is not typed by SDK 4.13.3')


class CatalogValidateTest(unittest.TestCase):
    """Decision 6: order form (catalog item variables), ask steps (catalog tasks), fulfilment on sc_task."""

    def test_catalog_trigger_needs_a_form(self):
        s = minimal(trigger={'type': 'service_catalog'})
        one(errors_of(s), 'describe the order form under "form"')

    def test_form_needs_the_catalog_trigger(self):
        s = catalog_min(trigger={'type': 'record_created', 'table': 'incident'})
        one(errors_of(s), 'set "trigger": {"type": "service_catalog"}', 'form.submitted_by')

    def test_form_lists_catalog_and_category_placeholders(self):
        s = catalog_min()
        del s['form']['category']
        s['form']['catalog'] = {'placeholder': 'category'}
        errs = errors_of(s)
        one(errs, 'form.category', 'every item needs a catalog and a category')
        one(errs, 'form.catalog', 'must be of kind "catalog"')

    def test_form_pill_must_name_a_form_variable(self):
        s = catalog_min()
        s['steps'][0]['inputs']['log_message'] = [{'pill': 'form.badge_colour'}]
        one(errors_of(s), 'the order form has no variable "badge_colour"', 'badge_holder')

    def test_asked_variable_is_read_from_its_step(self):
        s = fixture('all-constructs.json')
        s['steps'][1]['steps'][0]['inputs']['log_message'] = [{'pill': 'form.outcome'}]
        one(errors_of(s), '"outcome" is asked by step "ask_review"', '{"pill": "ask_review.outcome"}')

    def test_ask_step_reads_only_its_variables(self):
        s = fixture('all-constructs.json')
        s['steps'][1]['steps'][0]['inputs']['log_message'] = [{'pill': 'ask_review.summary'}]
        one(errors_of(s), 'step "ask_review" (ask) has outputs: resolution_notes, outcome, follow_up, catalog_task',
            'on the order form: {"pill": "form.summary"}')

    def test_answers_exist_only_after_the_ask(self):
        early = {'id': 'early', 'label': 'e', 'action': 'log', 'inputs': {'log_message': [{'pill': 'ask_badge.colour'}]}}
        one(errors_of(with_ask(early, ask())), 'step "ask_badge" is not available here')

    def test_variable_types_come_from_the_vocabulary(self):
        s = catalog_min()
        s['form']['variables'].append({'name': 'photo', 'label': 'Photo', 'type': 'rich_text_label'})
        one(errors_of(s), 'variable type "rich_text_label" is not offered', 'list_collector')

    def test_variable_needs_what_its_builder_needs(self):
        s = catalog_min()
        s['form']['variables'] += [{'name': 'doors', 'label': 'Doors', 'type': 'list_collector'},
                                   {'name': 'size', 'label': 'Size', 'type': 'select_box'},
                                   {'name': 'agree', 'label': 'I agree', 'type': 'checkbox', 'mandatory': True}]
        errs = errors_of(s)
        one(errs, 'form.variables[1].reference', 'referenced table "None"')
        one(errs, 'form.variables[2]', 'needs "choices"')
        one(errs, 'form.variables[3]', 'a checkbox variable cannot be mandatory', 'yes_no')

    def test_variable_names_are_unique_on_the_item(self):
        s = with_ask(ask(variables=[{'name': 'badge_holder', 'label': 'Holder', 'type': 'single_line_text'}]))
        one(errors_of(s), 'variable "badge_holder" is already declared at form.variables[0]')

    def test_ask_needs_someone_to_answer(self):
        a = ask()
        del a['ask']['assignment_group']
        errs = errors_of(with_ask(a))
        one(errs, 'needs "assignment_group" or "assigned_to"')
        one(errs, 'placeholders.security_group', 'never used')

    def test_ask_is_not_inside_for_each(self):
        loop = {'id': 'each', 'label': 'each', 'logic': 'for_each', 'items': {'pill': 'find.Records'}, 'item': 'rec', 'steps': [ask()]}
        find = {'id': 'find', 'label': 'find', 'action': 'lookUpRecords', 'inputs': {'table': 'sys_user'}}
        one(errors_of(with_ask(find, loop)), 'cannot be inside for_each')

    def test_ask_needs_a_catalog_flow(self):
        s = minimal(steps=[ask()] + minimal()['steps'])
        one(errors_of(s), 'an ask step is a catalog task of the request')

    def test_catalog_flow_tasks_are_catalog_tasks(self):
        s = fixture('onboarding-en.json')
        s['steps'][2]['branches'][0][0] = {'id': 'task_accounts', 'label': 'x', 'action': 'createTask',
                                           'inputs': {'task_table': 'task', 'field_values': {'short_description': 'x'}}}
        one(errors_of(s), 'use createCatalogTask', 'wfa-flow-actions-guide.md:948')

    def test_catalog_task_is_on_the_request_item_with_an_explicit_wait(self):
        s = fixture('onboarding-en.json')
        inputs = s['steps'][2]['branches'][0][0]['inputs']
        del inputs['ah_wait']
        inputs['ah_requested_item'] = {'pill': 'equipment_items.Table'}
        inputs['catalog_variables'] = ['new_hire']
        errs = errors_of(s)
        one(errs, 'needs "ah_wait": false', 'wfa-flow-actions-guide.md:1092')
        one(errs, '"ah_requested_item": {"pill": "trigger.request_item"}')
        one(errs, '"catalog_variables" is set by the script', 'use an ask step')

    def test_catalog_actions_only_in_catalog_flows(self):
        s = minimal(steps=[{'id': 't', 'label': 't', 'action': 'createCatalogTask',
                            'inputs': {'ah_requested_item': 'x', 'ah_wait': False}}])
        one(errors_of(s), 'works only in a flow started from a catalog item', 'wfa-flow-actions-guide.md:1080')

    def test_get_catalog_variables_is_written_by_the_script(self):
        s = catalog_min()
        s['steps'].append({'id': 'read', 'label': 'r', 'action': 'getCatalogVariables', 'inputs': {}})
        one(errors_of(s), 'getCatalogVariables is written by the script', 'form.<variable>')

    def test_instance_records_are_never_typed_names(self):
        def task(fv):
            return {'id': 't', 'label': 'Task', 'action': 'createCatalogTask', 'inputs': {
                'ah_requested_item': {'pill': 'trigger.request_item'}, 'ah_short_description': 'Do it',
                'ah_fields': fv, 'ah_wait': False}}
        one(errors_of(with_ask(task({'assignment_group': 'Network team'}))), 'assignment_group', 'placeholder')
        self.assertEqual(errors_of(with_ask(task({'assignment_group': {'placeholder': 'security_group'}}))), [])
        appr = {'id': 'ap', 'label': 'Approve', 'action': 'askForApproval', 'inputs': {
            'record': {'pill': 'trigger.request_item'}, 'table': 'sc_req_item',
            'approval_conditions': {'rule': 'Any', 'users': ['bob.smith']}}}
        one(errors_of(with_ask(appr)), 'users', 'placeholder')
        one(errors_of(with_ask(ask(assigned_to='bob', assignment_group=None))), 'assigned_to', 'placeholder')
        notif = {'id': 'nf', 'label': 'Notify', 'action': 'sendNotification', 'inputs': {
            'notification': '0123456789abcdef0123456789abcdef', 'record': {'pill': 'trigger.request_item'},
            'table_name': 'sc_req_item'}}
        one(errors_of(with_ask(notif)), 'notification', 'placeholder')
        notif['inputs']['notification'] = {'placeholder': 'security_group'}
        self.assertEqual(errors_of(with_ask(notif)), [])

    def test_placeholders_must_be_an_object(self):
        one(errors_of(minimal(placeholders=['a'])), 'placeholders', 'object')


if __name__ == '__main__':
    unittest.main()
