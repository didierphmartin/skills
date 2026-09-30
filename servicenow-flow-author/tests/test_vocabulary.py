import json, unittest
from pathlib import Path

VOCAB = json.loads((Path(__file__).resolve().parent.parent / 'references' / 'fluent-vocabulary.json').read_text(encoding='utf-8'))


class VocabularyTest(unittest.TestCase):
    def test_pinned_sdk_version(self):
        self.assertEqual(VOCAB['sdk'], '4.13.3')

    def test_triggers_from_trigger_api(self):
        self.assertEqual(len(VOCAB['triggers']), 13)
        rc = VOCAB['triggers']['record_created']
        self.assertEqual(rc['fluent'], 'trigger.record.created')
        self.assertTrue(rc['inputs']['table']['required'])
        self.assertEqual(rc['outputs']['current']['table_from'], 'table')
        self.assertEqual(VOCAB['triggers']['scheduled_daily']['inputs']['time']['type'], 'Time')
        self.assertTrue(rc['doc'].startswith('docs/api/flow/trigger-api.md:'))

    def test_actions_from_action_api(self):
        documented = [k for k, a in VOCAB['actions'].items() if a['documented']]
        self.assertEqual(len(documented), 31)
        self.assertEqual(sorted(k for k, a in VOCAB['actions'].items() if not a['documented']),
                         ['addWorknoteLinkToContext', 'recordProducer'])
        ask = VOCAB['actions']['askForApproval']
        self.assertTrue(ask['inputs']['record']['required'] and ask['inputs']['approval_conditions']['required'])
        self.assertEqual(ask['outputs']['approval_state']['pill'], 'choice')
        self.assertEqual(set(VOCAB['actions']['lookUpRecord']['outputs']), {'Record', 'Table', 'status', 'error_message'})
        self.assertEqual(VOCAB['actions']['lookUpRecords']['outputs']['Records']['pill'], 'records')
        self.assertEqual(VOCAB['actions']['createTask']['inputs']['field_values']['type'], 'TemplateValue')
        self.assertEqual(VOCAB['actions']['log']['inputs']['log_level']['values'], ['info', 'warn', 'error'])
        for a in VOCAB['actions'].values():
            self.assertTrue(a['doc'].startswith('docs/api/flow/action-api.md:'), a)

    def test_input_rules_cite_sources(self):
        self.assertIn('sendEmail.ah_body', VOCAB['input_rules']['no_pills'])
        self.assertIn('waitForCondition.record', VOCAB['input_rules']['wrap_pill'])
        self.assertIn('log.log_message', VOCAB['input_rules']['template_text'])

    def test_table_index(self):
        t = VOCAB['tables']
        self.assertEqual(len(t), 186)
        self.assertEqual(t['sys_user']['fields']['manager'], {'kind': 'reference', 'ref': 'sys_user'})
        self.assertEqual(t['task']['fields']['additional_assignee_list'], {'kind': 'list', 'ref': 'sys_user'})
        self.assertEqual(t['task']['fields']['due_date']['kind'], 'datetime')
        self.assertEqual(t['sc_task']['extends'], 'task')

    def test_catalog_from_the_service_catalog_docs(self):   # decision 6
        c = VOCAB['catalog']
        self.assertEqual(len(c['variables']), 15)
        self.assertEqual(c['variables']['list_collector']['builder'], 'ListCollectorVariable')
        self.assertEqual(c['variables']['list_collector']['extra']['prop'], 'listTable')
        self.assertEqual(c['variables']['reference']['extra']['prop'], 'referenceTable')
        self.assertEqual(c['variables']['date']['pill'], 'glide_date')
        self.assertNotIn('mandatory', c['variables']['checkbox']['props'])
        self.assertIn('LabelVariable', c['unsupported_variables'])
        self.assertTrue(c['item']['properties']['name']['required'])
        self.assertIn('appliesOnCatalogTasks', c['ui_policy']['properties'])
        self.assertIn('mandatory', c['ui_policy']['actions'])
        for v in c['variables'].values():
            self.assertTrue(v['doc'].startswith('docs/api/service-catalog/variables/'), v)

    def test_catalog_actions_follow_the_typings(self):   # forms research: action-api.md vs typings + sample
        a = VOCAB['actions']
        self.assertEqual(a['createCatalogTask']['outputs'], {'catalog_task': a['createCatalogTask']['outputs']['catalog_task']})
        self.assertEqual(a['createCatalogTask']['outputs']['catalog_task']['table'], 'sc_task')
        self.assertIn('OutputsWithDotwalk', a['getCatalogVariables']['dynamic_outputs'])
        self.assertIn('wfa-flow-actions-guide.md:1080', a['createCatalogTask']['catalog_only'])
        self.assertIn('createCatalogTask.ah_requested_item', VOCAB['input_rules']['wrap_pill'])

    def test_unsupported_features_name_an_alternative(self):
        self.assertIn('do_until', VOCAB['unsupported']['go_back_to'])
        self.assertIn('else_if', VOCAB['unsupported']['make_a_decision'])


if __name__ == '__main__':
    unittest.main()
