import unittest
from helpers import create, fixture, minimal, render

ITEM = 'newEmployeeOnboardingItem'
RITM = "`${wfa.dataPill(params.trigger.request_item, 'reference')}`"


class GenerateTest(unittest.TestCase):
    """Each construct renders the shape documented in the SDK docs / official sample (cited per test)."""

    @classmethod
    def setUpClass(cls):
        cls.onb = render(fixture('onboarding-en.json'))
        cls.all = render(fixture('all-constructs.json'))
        cls.p1 = render(fixture('p1-escalation.json'))

    def test_header_imports_and_config(self):   # flow-api.md: Flow(config, trigger, body); globals not imported
        self.assertIn("import { action, Flow, wfa, trigger } from '@servicenow/sdk/automation'\n"
                      "import { CatalogItem, DateVariable, ListCollectorVariable, MultiLineTextVariable, ReferenceVariable } "
                      "from '@servicenow/sdk/core'\n", self.onb)
        self.assertIn("export const newEmployeeOnboardingFlow = Flow(\n    {\n        $id: Now.ID['new_employee_onboarding'],\n"
                      "        name: 'New employee onboarding',\n", self.onb)
        self.assertIn("        runAs: 'system',\n    },\n", self.onb)
        self.assertNotIn('import { Now', self.onb)
        self.assertNotIn('@servicenow/sdk/core', self.p1)   # no form, no catalog item

    def test_catalog_item(self):   # catalogitem-api.md:22-199; flow: Now.ref (catalogitem-api.md:228, sample :52)
        self.assertIn(f"export const {ITEM} = CatalogItem({{\n"
                      "    $id: Now.ID['new_employee_onboarding__item'],\n"
                      "    name: 'New employee onboarding',\n", self.onb)
        # shortDescription is a one-line summary (the flow title); description says who submits the form
        self.assertIn("    name: 'New employee onboarding',\n    shortDescription: 'New employee onboarding',\n", self.onb)
        self.assertIn("    description: 'HR submits this form when it creates a new user record for an employee who hasn\\'t started yet.',\n"
                      "    flow: Now.ref('sys_hub_flow', 'new_employee_onboarding'),\n"
                      "    catalogs: ['PLACEHOLDER:service_catalog'],\n"
                      "    categories: ['PLACEHOLDER:hr_category'],\n    variables: {\n", self.onb)
        self.assertIn("        new_hire: ReferenceVariable({ question: 'New hire', referenceTable: 'sys_user', mandatory: true, "
                      "order: 100, helpText: 'The user record HR has just created.' }),\n", self.onb)
        self.assertIn("        start_date: DateVariable({ question: 'Start date', mandatory: true, order: 400 }),\n", self.onb)
        self.assertIn("        equipment: ListCollectorVariable({ question: 'Equipment (laptop, monitor, phone…)', listTable: 'cmdb_model', "
                      "mandatory: true, order: 500, helpText: 'One hardware request is created for each item.' }),\n", self.onb)
        self.assertIn("        urgency_level: SelectBoxVariable({ question: 'Urgency', choices: { low: { label: 'Low', sequence: 1 }, "
                      "high: { label: 'High', sequence: 2 } }, mandatory: true, order: 400 }),\n", self.all)
        self.assertIn("        requester: RequestedForVariable({ question: 'Requested for', order: 200 }),\n", self.all)
        # asked variables follow the form's and are never mandatory on the item (hidden + mandatory is refused by the docs)
        self.assertIn("        resolution_notes: MultiLineTextVariable({ question: 'Resolution notes', order: 1000 }),\n", self.all)

    def test_stamp_variable_props(self):   # fluent-vocabulary.json catalog.variables.*.props: defaultValue, readOnly, hidden
        from helpers import catalog_min
        s = catalog_min()
        s['form']['variables'].append({'name': 'synergyai_automation', 'label': 'SynergyAI automation',
                                       'type': 'single_line_text', 'default': "okta-reset@1", 'read_only': True, 'hidden': True})
        out = render(s)
        self.assertIn("synergyai_automation: SingleLineTextVariable({ question: 'SynergyAI automation', order: 200, "
                      "defaultValue: 'okta-reset@1', readOnly: true, hidden: true }),\n", out)
        self.assertNotIn('defaultValue', render(catalog_min()))

    def test_ui_policies_for_asked_variables(self):   # cataloguipolicy-api.md:35-41, :96-99
        self.assertIn("CatalogUiPolicy({\n    $id: Now.ID['all_constructs__hide_asked'],\n", self.all)
        self.assertIn("    catalogItem: allConstructsItem,\n    appliesOnCatalogItemView: true,\n    appliesOnRequestedItems: false,\n"
                      "    appliesOnCatalogTasks: false,\n    onLoad: true,\n    actions: [\n"
                      "        { variableName: allConstructsItem.variables.resolution_notes, visible: false },\n", self.all)
        self.assertIn("    appliesOnCatalogItemView: false,\n    appliesOnRequestedItems: false,\n    appliesOnCatalogTasks: true,\n"
                      "    onLoad: true,\n    actions: [\n"
                      "        { variableName: allConstructsItem.variables.resolution_notes, visible: true, mandatory: true },\n", self.all)
        self.assertIn("        { variableName: allConstructsItem.variables.follow_up, visible: true },\n", self.all)
        self.assertNotIn('CatalogUiPolicy', self.onb)   # nothing asked later

    def test_catalog_trigger_and_form_read(self):   # trigger-api.md:392; getCatalogVariables typings + sample :142, :158
        self.assertIn("        trigger.application.serviceCatalog,\n"
                      "        { $id: Now.ID['new_employee_onboarding__trigger'] },\n"
                      "        { run_flow_in: 'background' }\n    ),\n    (params) => {\n"
                      "        const form = wfa.action(\n            action.core.getCatalogVariables,\n"
                      "            { $id: Now.ID['new_employee_onboarding__form'] },\n"
                      f"            {{\n                requested_item: {RITM},\n"
                      f"                template_catalog_item: `${{{ITEM}}}`,\n"
                      f"                catalog_variables: [{ITEM}.variables.new_hire, {ITEM}.variables.manager, ", self.onb)
        self.assertIn("ah_to: `${wfa.dataPill(form.manager.email, 'string')}`,", self.onb)
        self.assertIn("ah_subject: `Onboarding of ${wfa.dataPill(form.new_hire.name, 'string')}: start date "
                      "${wfa.dataPill(form.start_date, 'glide_date')}`,", self.onb)

    def test_ask_is_a_blocking_task_then_its_answers(self):   # forms research §3a; createCatalogTask typings; sample :222-233
        self.assertIn("        const ask_review__task = wfa.action(\n            action.core.createCatalogTask,\n"
                      "            { $id: Now.ID['all_constructs__ask_review'], annotation: 'Review the incident' },\n"
                      f"            {{\n                ah_requested_item: {RITM},\n"
                      "                ah_short_description: 'Review the incident and record the outcome',\n"
                      "                ah_fields: TemplateValue({\n                    assignment_group: 'PLACEHOLDER:review_group',\n"
                      "                }),\n                ah_wait: true,\n"
                      "                template_catalog_item: `${allConstructsItem}`,\n"
                      "                catalog_variables: [allConstructsItem.variables.resolution_notes, allConstructsItem.variables.outcome, "
                      "allConstructsItem.variables.follow_up],\n            }\n        )\n"
                      "        const ask_review = wfa.action(\n            action.core.getCatalogVariables,\n"
                      "            { $id: Now.ID['all_constructs__ask_review__answers'], annotation: 'Review the incident' },\n", self.all)
        self.assertIn("log_message: `Outcome ${wfa.dataPill(ask_review.outcome, 'string')} for ${wfa.dataPill(form.incident.number, 'string')}`,", self.all)

    def test_branch_on_the_ask_task_state(self):   # sample :243 (catalog_task.state; research §3a (5))
        self.assertIn("condition: `${wfa.dataPill(ask_review__task.catalog_task.state, 'integer')}=3`", self.all)

    def test_fulfilment_catalog_task(self):   # wfa-flow-guide.md:274-281; wfa-flow-actions-guide.md:1092
        self.assertIn("                    action.core.createCatalogTask,\n"
                      "                    { $id: Now.ID['new_employee_onboarding__task_accounts'], annotation: 'IT service desk: email, VPN, the department\\'s apps' },\n"
                      f"                    {{\n                        ah_requested_item: {RITM},\n"
                      "                        ah_short_description: 'Set up the accounts: email, VPN and the department\\'s apps',\n"
                      "                        ah_fields: TemplateValue({\n"
                      "                            assignment_group: 'PLACEHOLDER:it_service_desk_group',\n"
                      "                        }),\n                        ah_wait: false,\n", self.onb)

    def test_record_trigger(self):   # trigger-api.md:37, wfa-flow-guide.md:97
        self.assertIn("    wfa.trigger(\n        trigger.record.created,\n"
                      "        { $id: Now.ID['p1_incident_escalation__trigger'] },\n"
                      "        { table: 'incident', condition: 'priority=1^active=true', run_flow_in: 'background' }\n    ),\n"
                      "    (params) => {\n", self.p1)

    def test_scheduled_trigger_and_unused_params(self):   # trigger-api.md:175; TS6133 on an unused params
        s = minimal(trigger={'type': 'scheduled_daily', 'time': {'hours': 9, 'minutes': 30, 'seconds': 0, 'timezone': 'UTC'}},
                    steps=[{'id': 'log_it', 'label': 'Log it', 'action': 'log', 'inputs': {'log_level': 'info', 'log_message': 'Daily'}}])
        out = render(s)
        self.assertIn("        trigger.scheduled.daily,\n", out)
        self.assertIn("        { time: Time({ hours: 9, minutes: 30, seconds: 0 }, 'UTC') }\n    ),\n    () => {\n", out)

    def test_action_with_captured_output_and_approval_rules(self):   # action-api.md:481, :516; wfa-flow-guide.md:247-265
        self.assertIn("        const ask_manager = wfa.action(\n            action.core.askForApproval,\n"
                      "            { $id: Now.ID['new_employee_onboarding__ask_manager'], annotation: 'Manager approves the onboarding and the equipment list' },\n"
                      "            {\n                record: wfa.dataPill(params.trigger.request_item, 'reference'),\n"
                      "                table: 'sc_req_item',\n                approval_conditions: wfa.approvalRules({\n"
                      "                    conditionType: 'OR',\n", self.onb)
        self.assertIn("rules: [[{ ruleType: 'Any', users: [wfa.dataPill(form.manager, 'reference')], groups: [], manual: false }]],", self.onb)

    def test_uncaptured_action(self):   # an unused const is TS6133
        self.assertIn("        wfa.action(\n            action.core.sendEmail,\n", self.onb)

    def test_if_condition_pill(self):   # wfa-flow-logic-api.md:23, sample flow-trigger-record.now.ts:34
        self.assertIn("        wfa.flowLogic.if(\n            { $id: Now.ID['new_employee_onboarding__check_rejected'], "
                      "condition: `${wfa.dataPill(ask_manager.approval_state, 'choice')}!=approved`, annotation: 'Not approved by the manager?' },\n"
                      "            () => {\n", self.onb)

    def test_else_if_and_else(self):   # wfa-flow-logic-api.md:52, :81
        self.assertIn("                wfa.flowLogic.elseIf(\n                    { $id: Now.ID['all_constructs__in_progress'], "
                      "condition: `${wfa.dataPill(inc.state, 'integer')}=2`, annotation: 'In progress' },\n", self.all)
        self.assertIn("                wfa.flowLogic.else(\n                    { $id: Now.ID['all_constructs__other_state'], annotation: 'Any other state' },\n", self.all)

    def test_end_flow(self):   # wfa-flow-logic-api.md:187
        self.assertIn("                wfa.flowLogic.endFlow({ $id: Now.ID['new_employee_onboarding__end_rejected'], annotation: 'Stop: onboarding not approved' })\n", self.onb)

    def test_parallel(self):   # wfa-flow-logic-api.md:211 — rest-argument blocks, not an array
        self.assertIn("        wfa.flowLogic.doInParallel(\n            { $id: Now.ID['new_employee_onboarding__setup'], annotation: 'Accounts and desk in parallel' },\n"
                      "            () => {\n", self.onb)
        self.assertIn("            },\n            () => {\n", self.onb)

    def test_for_each_over_a_list_collector(self):   # wfa-flow-logic-api.md:106-135, wfa-flow-guide.md:185
        # a list collector answer is a comma-separated sys_id string: look the records up, iterate Records
        self.assertIn("                table: 'cmdb_model',\n"
                      "                conditions: `sys_idIN${wfa.dataPill(form.equipment, 'string')}`,\n", self.onb)
        self.assertIn("        wfa.flowLogic.forEach(\n            wfa.dataPill(equipment_items.Records, 'records'),\n"
                      "            { $id: Now.ID['new_employee_onboarding__each_item'], annotation: 'One hardware request per item' },\n"
                      "            (equipment_item) => {\n", self.onb)
        self.assertIn("ah_short_description: wfa.dataPill(equipment_item.name, 'string'),", self.onb)
        self.assertIn("record: wfa.dataPill(open_task.sys_id, 'reference'),", self.onb)

    def test_relative_wait_with_schedule(self):   # wfa-flow-logic-api.md:459, :484
        self.assertIn("        wfa.flowLogic.waitForADuration({\n            $id: Now.ID['new_employee_onboarding__wait_t2'],\n"
                      "            annotation: 'Wait until two working days before the start date',\n"
                      "            durationType: 'relative_duration',\n            duration: Duration({ days: 2 }),\n"
                      "            relativeOperator: 'before',\n"
                      "            relativeDatetime: `${wfa.dataPill(form.start_date, 'glide_date')}`,\n"
                      "            schedule: 'PLACEHOLDER:working_days_schedule',\n        })\n", self.onb)
        self.assertIn("relativeDatetime: `${wfa.dataPill(form.review_by, 'glide_date_time')}`,", self.all)

    def test_explicit_wait(self):   # wfa-flow-logic-api.md:473
        self.assertIn("durationType: 'explicit_duration',\n                    duration: Duration({ hours: 1 }),\n                })\n", self.all)

    def test_try_catch(self):   # wfa-flow-logic-api.md:273
        self.assertIn("        wfa.flowLogic.tryCatch(\n            { $id: Now.ID['all_constructs__safe_create'], annotation: 'Create the daily check record' },\n"
                      "            {\n                try: () => {\n", self.all)
        self.assertIn("                },\n                catch: () => {\n", self.all)

    def test_do_until(self):   # wfa-flow-logic-api.md:346 — until is the last statement
        self.assertIn("                wfa.flowLogic.until(`${wfa.dataPill(first_new.status, 'string')}=1`)\n            }\n        )\n", self.all)

    def test_text_fields_and_wrapped_pills(self):   # wfa-flow-guide.md:127; action-api.md:411
        self.assertIn("record: `${wfa.dataPill(latest_p1.Record, 'reference')}`,", self.all)
        self.assertIn("timeout_duration: Duration({ days: 1 }),", self.all)
        self.assertIn("log_message: `Onboarding complete for ${wfa.dataPill(form.new_hire.name, 'string')}`,", self.onb)

    def test_filled_placeholder_uses_its_value(self):
        self.assertIn("recipients: '+15550100',", self.all)

    def test_french_strings_are_escaped(self):   # Review Focus 1
        fr = render(fixture('onboarding-fr.json'))
        self.assertIn("name: 'Intégration d\\'un nouvel employé',", fr)
        self.assertIn("approval_reason: 'Approuver l\\'intégration et confirmer la liste d\\'équipement',", fr)
        self.assertIn("ah_subject: `Intégration de ${wfa.dataPill(form.new_hire.name, 'string')} : date de début "
                      "${wfa.dataPill(form.start_date, 'glide_date')}`,", fr)
        self.assertIn("equipment: ListCollectorVariable({ question: 'Équipement (ordinateur portable, écran, téléphone…)',", fr)
        self.assertIn("$id: Now.ID['integration_nouvel_employe__ask_manager']", fr)

    def test_escaping(self):
        self.assertEqual(create.ts_str("l'a\\b\nc"), "'l\\'a\\\\b\\nc'")
        self.assertEqual(create.tpl_text('a`b${c}'), 'a\\`b\\${c}')

    def test_no_action_import_when_unused(self):
        s = minimal(steps=[{'id': 'w', 'label': 'w', 'logic': 'wait_duration', 'duration': {'minutes': 5}}])
        self.assertIn("import { Flow, wfa, trigger } from '@servicenow/sdk/automation'", render(s))


if __name__ == '__main__':
    unittest.main()
