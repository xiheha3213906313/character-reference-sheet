"""Observed receipt-loss and report-repair regressions. No real model or visual assertions."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

import test_sheet_flow as fixtures
import sheet_flow as api
import review_workflow as workflow
from review_v2 import read, digest
from production_records import FLOW, RECORD
from tool_execution import paths


class RecoveryTest(unittest.TestCase):
    for name in ('setUp', 'config', 'image', 'receive', 'simple', 'packet', 'report', 'baseline'):
        locals()[name] = getattr(fixtures.SheetFlowTest, name)

    def snapshot(self):
        return {p: digest(p) for p in self.root.rglob('*') if p.is_file()}

    def calls(self, request):
        return {'calls': [{'request_id': r['request_id'], 'tool_call_id': None,
                           'result': {'savedPath': str(self.image()), 'text': '真实返回，含中文与空格'}}
                          for r in request['requests']]}

    def test_cached_success_resumes_receive_and_cannot_be_relabelled_failure(self):
        request = api.prepare(self.root, 'head', self.config())
        config = self.calls(request)
        action = api.capture(self.root, 'head', config)
        self.assertTrue(action['receipt_cached'])
        self.assertEqual(config, read(Path(action['receipt_config_file'])))
        before = self.snapshot()
        status = api.advance(self.root, 'head', mutate=False)
        self.assertEqual('receive', status['next_action'])
        self.assertEqual(0, status['tool_call_count'])
        self.assertNotIn('requests', status)
        self.assertEqual(before, self.snapshot())
        lost = {'calls': [{'request_id': config['calls'][0]['request_id'], 'error': 'lost preview'}]}
        with self.assertRaisesRegex(ValueError, '已有真实工具返回'):
            api.receive(self.root, 'head', lost)
        self.assertEqual(before, self.snapshot())
        result = api.receive(self.root, 'head', config)
        self.assertEqual('self-check', result['next_action'])
        attempts = workflow.load_all(self.root)[2]['stages']['head']['attempts']
        self.assertEqual(1, len(attempts))
        self.assertNotIn('error', attempts[0])

    def test_unparseable_success_stays_cached_without_failed_attempt_or_regeneration(self):
        request = api.prepare(self.root, 'head', self.config())
        config = {'calls': [{'request_id': request['requests'][0]['request_id'],
                             'result': {'output_hint': 'Synthetic successful tool return; path missing'}}]}
        action = api.capture(self.root, 'head', config)
        before = self.snapshot()
        result = subprocess.run([sys.executable, '-B', str(Path(api.__file__)), 'receive', '--root', str(self.root),
                                 '--config', action['receipt_config_file']], capture_output=True, encoding='utf-8')
        self.assertEqual(2, result.returncode, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual('correct_receipt_fields', output['next_action'])
        self.assertFalse(output['tool_failure_recorded'])
        self.assertEqual(before, self.snapshot())
        self.assertEqual([], workflow.load_all(self.root)[2]['stages']['head']['attempts'])
        self.assertEqual('receive', api.prepare(self.root, 'head', self.config())['next_action'])

    def test_started_batch_with_missing_returns_blocks_resending_without_mutation(self):
        request = api.prepare(self.root, 'head', self.config())
        files = paths(self.root, 'head', request['requests'])
        workflow.write(files['started'], {'semantics': 'synthetic interrupted wrapper'})
        before = self.snapshot()
        for result in (api.advance(self.root, 'head', mutate=False), api.prepare(self.root, 'head', self.config())):
            self.assertEqual('recover_receipts', result['next_action'])
            self.assertEqual(0, result['tool_call_count'])
            self.assertNotIn('requests', result)
        self.assertEqual(before, self.snapshot())

    def test_real_failure_and_success_are_cached_together_then_counted_once(self):
        extras = self.baseline()
        config = self.calls(extras)
        config['calls'][1] = {'request_id': extras['requests'][1]['request_id'], 'error': 'Synthetic provider error'}
        api.capture(self.root, 'head', config)
        result = api.receive(self.root, 'head', config)
        self.assertEqual('rank', result['next_action'])
        attempts = workflow.load_all(self.root)[2]['stages']['head']['attempts']
        self.assertEqual(3, len(attempts))
        self.assertEqual(1, sum(bool(c.get('error')) for c in attempts))

    def test_templates_offer_evidence_choices_without_viewed_or_pass_flags(self):
        handoff = self.simple(self.receive(api.prepare(self.root, 'head', self.config())))
        packet = read(self.root / handoff['packet_file'])
        template = read(self.root / handoff['reviewer_handoff']['report_template_file'])
        self.assertEqual([], template['viewed_evidence_ids'])
        for goal, item in zip(packet['checks'], template['checks']):
            self.assertEqual('pending', item['result'])
            self.assertEqual([], item['evidence_ids'])
            hint = item['evidence_requirement']
            self.assertTrue(hint['primary_evidence_ids'])
            if goal['group'] in ('identity', 'design', 'spatial') and goal.get('reference_display') != 'context_only':
                self.assertEqual(goal['source_ids'], hint['required_reference_ids'])
            if goal['group'] == 'native_quality':
                self.assertTrue(all(i.startswith('native_') for i in hint['primary_evidence_ids']))
            if goal['group'] == 'reduced_quality':
                self.assertEqual(['reduced'], hint['primary_evidence_ids'])

    def test_all_reference_coverage_errors_are_returned_in_one_preflight(self):
        handoff = self.simple(self.receive(api.prepare(self.root, 'head', self.config())))
        report, packet = self.report(handoff), read(self.root / handoff['packet_file'])
        affected = []
        report['viewed_evidence_ids'].append('whole')
        for goal, item in zip(packet['checks'], report['checks']):
            if goal['group'] in ('identity', 'design', 'spatial') and goal.get('reference_display') != 'context_only':
                item['evidence_ids'] = ['whole']
                affected.append(item['id'])
        before, original = self.snapshot(), copy.deepcopy(report)
        result = api.check_report(self.root, 'head', report)
        missing = [e for e in result['field_errors'] if e.get('missing_reference_ids')]
        self.assertEqual(set(affected), {e['check_id'] for e in missing})
        self.assertGreater(len(missing), 1)
        self.assertTrue(all(e['evidence_requirement']['reference_evidence_ids'] for e in missing))
        self.assertEqual('invalid', result['report_validation'])
        self.assertEqual(original, report)
        self.assertEqual(before, self.snapshot())

    def test_invalid_report_preserves_visual_results_and_does_not_enable_supplement(self):
        handoff = self.simple(self.receive(api.prepare(self.root, 'head', self.config())))
        report = self.report(handoff)
        report['checks'][0]['candidate_observation'] = ''
        original, before = copy.deepcopy(report), self.snapshot()
        for result in (api.check_report(self.root, 'head', report), api.review(self.root, 'head', report)):
            self.assertEqual('invalid', result['report_validation'])
            self.assertFalse(result['visual_state_changed'])
            self.assertFalse(result['visual_approval_recorded'])
            self.assertTrue(all(v == 'pass' for v in result['reported_visual_results'].values()))
            self.assertNotIn('requests', result)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(original, report)
        with self.assertRaisesRegex(ValueError, '待核实'):
            from production_evidence import supplement
            supplement(self.root, 'head', {'reason': 'format error', 'regions': [{'id': 'x', 'crop': [0, 0, 10, 10]}]})
        report['checks'][0]['candidate_observation'] = 'Synthetic actual candidate observation'
        self.assertEqual('valid', api.check_report(self.root, 'head', report)['report_validation'])
        self.assertEqual('generate_parallel', api.review(self.root, 'head', report)['next_action'])

    def test_genuine_visual_pending_is_a_valid_report_and_keeps_supplement_flow(self):
        handoff = self.simple(self.receive(api.prepare(self.root, 'head', self.config())))
        report = self.report(handoff, result='pending')
        result = api.check_report(self.root, 'head', report)
        self.assertEqual('valid', result['report_validation'])
        self.assertTrue(all(v == 'pending' for v in result['reported_visual_results'].values()))
        result = api.review(self.root, 'head', report)
        self.assertEqual('valid', result['report_validation'])
        self.assertEqual('supplement_evidence', result['next_action'])

    def test_fixed_executor_preserves_raw_results_before_preview_and_never_repeats_calls(self):
        runtime = Path(sys.executable).parents[1]
        node = runtime / 'node/bin/node.exe' if os.name == 'nt' else Path(shutil.which('node') or '')
        shell = runtime / 'native/powershell/pwsh.exe' if os.name == 'nt' else Path('/bin/sh')
        if not node.is_file() or not shell.is_file():
            self.skipTest('Node/shell required for executor integration')
        request = api.prepare(self.root, 'head', self.config())
        result = {'output_hint': 'Saved image: ' + str(self.image()),
                  'image_url': 'data:image/png;base64,c3ludGhldGlj',
                  'text': ('实际回执与特殊字符 $() ` " \n' * 500)}
        fixture = self.base / 'wrapper-fixture.json'
        workflow.write(fixture, {'code': request['execution_code'], 'results': [result], 'runs': 3,
                                'fail_persist_once': True, 'allow_first_error': True, 'shell': str(shell)})
        process = subprocess.run([str(node), str(Path(__file__).with_name('exec_batch_harness.mjs')), str(fixture)],
                                 capture_output=True, encoding='utf-8', timeout=90)
        self.assertEqual(0, process.returncode, process.stderr)
        output = json.loads(process.stdout)
        self.assertEqual(1, output['call_count'])
        self.assertEqual([request['requests'][0]['arguments']], output['calls'])
        self.assertTrue(output['complete_binary_preserved'])
        self.assertTrue(any('expected_first_error' in m for m in output['messages']))
        self.assertTrue(any(m.get('preview_display') == 'unavailable' for m in output['messages']))
        status = api.advance(self.root, 'head', mutate=False)
        saved = read(Path(status['receipt_config_file']))
        self.assertEqual(result['text'], saved['calls'][0]['result']['text'])
        self.assertNotIn('image_url', saved['calls'][0]['result'])
        self.assertEqual('self-check', api.receive(self.root, 'head', saved)['next_action'])

    def test_fixed_parallel_executor_caches_real_failure_without_retrying_either_request(self):
        runtime = Path(sys.executable).parents[1]
        node = runtime / 'node/bin/node.exe' if os.name == 'nt' else Path(shutil.which('node') or '')
        shell = runtime / 'native/powershell/pwsh.exe' if os.name == 'nt' else Path('/bin/sh')
        if not node.is_file() or not shell.is_file():
            self.skipTest('Node/shell required for executor integration')
        config = self.config()
        config['prompt']['identity'] += ' 完整长参数🙂含中文、引号"与特殊字符$() ' * 1800
        request = self.baseline(config=config)
        result = {'savedPath': str(self.image())}
        fixture = self.base / 'parallel-wrapper-fixture.json'
        workflow.write(fixture, {'code': request['execution_code'], 'results': [result],
                                'fail_index': 1, 'runs': 2, 'shell': str(shell), 'max_output_bytes': 20000})
        process = subprocess.run([str(node), str(Path(__file__).with_name('exec_batch_harness.mjs')), str(fixture)],
                                 capture_output=True, encoding='utf-8', timeout=90)
        self.assertEqual(0, process.returncode, process.stderr)
        output = json.loads(process.stdout)
        self.assertEqual(2, output['call_count'])
        self.assertEqual([r['arguments'] for r in request['requests']], output['calls'])
        status = api.advance(self.root, 'head', mutate=False)
        saved = read(Path(status['receipt_config_file']))
        self.assertEqual(result, saved['calls'][0]['result'])
        self.assertIn('Synthetic actual provider failure', saved['calls'][1]['error'])
        self.assertEqual('rank', api.receive(self.root, 'head', saved)['next_action'])
        attempts = workflow.load_all(self.root)[2]['stages']['head']['attempts']
        self.assertEqual(3, len(attempts))
        self.assertEqual(1, sum(bool(c.get('error')) for c in attempts))
