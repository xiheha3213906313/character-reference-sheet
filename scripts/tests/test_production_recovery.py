"""Six audited workflow regressions. Synthetic data only; no provider or visual assertions."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import test_sheet_flow as fixtures
import sheet_flow as api
import review_workflow as workflow
import review_reading as reading
from production_records import FLOW
from review_v2 import read, digest


class ProductionRecoveryTest(unittest.TestCase):
    for name in ('setUp', 'config', 'image', 'receive', 'simple', 'packet', 'report', 'baseline', 'finish_stage', 'structure'):
        locals()[name] = getattr(fixtures.SheetFlowTest, name)

    def snapshot(self):
        return {str(p): digest(p) for p in self.root.rglob('*') if p.is_file()}

    def test_removing_only_back_membership_preserves_upstream_approval_and_requests(self):
        config = self.config()
        config['materials'][0]['uses'][0]['stages'] = ['head', 'front', 'back', 'left']
        self.receive(api.prepare(self.root, 'head', config))
        # Complete the real approval plumbing without changing the material plan.
        action = api.advance(self.root, 'head')
        handoff = self.simple(action)
        extras = api.review(self.root, 'head', self.report(handoff))
        board = self.receive(extras)['ranking_board']
        api.rank(self.root, 'head', {'preferred_call': board['call_ids'][0], 'reason': 'Synthetic choice.', 'board_sha256': board['sha256']})
        self.finish_stage('front')
        state, calls = copy.deepcopy(workflow.load_all(self.root)[1]), copy.deepcopy(read(self.root / FLOW)['requests'])
        plan = {k: copy.deepcopy(read(self.root / FLOW)[k]) for k in ('materials', 'accessory_visibility')}
        plan['materials'][0]['uses'][0]['stages'].remove('back')
        result = api.update_plan(self.root, 'back', plan)
        self.assertEqual([], result['affected_prepared_stages'])
        self.assertEqual('analyze_front_structure', result['next_action'])
        self.assertTrue(Path(result['config_file']).is_file())
        self.assertEqual(state, workflow.load_all(self.root)[1])
        self.assertEqual(calls, read(self.root / FLOW)['requests'])
        plan['materials'][0]['uses'][0]['target'] += ' Changed actual identity target.'
        result = api.update_plan(self.root, 'head', plan)
        self.assertEqual(['head', 'front'], result['affected_prepared_stages'])

    def test_no_rear_source_reports_every_invalid_back_route_before_any_stage(self):
        config = self.config()
        config['materials'][0]['views'] = ['front']
        config['materials'][0]['uses'] = [
            {'id': 'identity', 'kind': 'identity', 'stages': ['head', 'back'], 'target': 'Synthetic front identity.'},
            {'id': 'design', 'kind': 'design', 'stages': ['front', 'back', 'left'], 'target': 'Synthetic front design.'}]
        before = self.snapshot()
        with self.assertRaises(ValueError) as caught:
            api.prepare(self.root, 'head', config)
        self.assertEqual({'identity', 'design'}, {e['use_id'] for e in caught.exception.field_errors})
        self.assertEqual(before, self.snapshot())

    def test_short_action_preserves_complete_frozen_arguments_and_status_is_read_only(self):
        config = self.config()
        config['prompt']['identity'] += ' 完整长身份约束' * 1200
        request = api.prepare(self.root, 'head', config)
        public = api.public_action(request)
        self.assertNotIn('arguments', public['requests'][0])
        self.assertLess(len(json.dumps(public, ensure_ascii=False).encode('utf-8')), 13000)
        saved = read(Path(public['requests_file']))
        self.assertEqual(request['requests'][0]['arguments'], saved['requests'][0]['arguments'])
        self.assertTrue(config['prompt']['identity'] in saved['requests'][0]['arguments']['prompt'])
        before = self.snapshot()
        api.advance(self.root, 'head', mutate=False)
        self.assertEqual(before, self.snapshot())
        Path(public['execution_file']).unlink()
        before = self.snapshot()
        recovery = api.advance(self.root, 'head', mutate=False)
        self.assertEqual('restore_execution', recovery['next_action'])
        self.assertEqual(before, self.snapshot())
        process = subprocess.run([sys.executable, '-B', api.__file__, 'restore-execution', '--root', str(self.root)],
                                 capture_output=True, encoding='utf-8')
        self.assertEqual(0, process.returncode, process.stdout + process.stderr)
        self.assertEqual([r['request_id'] for r in request['requests']],
                         [r['request_id'] for r in json.loads(process.stdout)['requests']])

    def test_all_semantic_text_is_losslessly_paged_and_repeated_reads_do_not_write(self):
        handoff = self.simple(self.receive(api.prepare(self.root, 'head', self.config())))
        packet_path = self.root / handoff['packet_file']
        template_path = self.root / handoff['reviewer_handoff']['report_template_file']
        packet = read(packet_path)
        prompt = '全文🙂中文\\"\n' * 1000
        packet['generation_context']['prompt'] = prompt
        packet['generation_context']['literal_marker_fixture'] = {'reuse_text': 'Literal data, not a reference.'}
        packet['generation_context']['literal_nested_fixture'] = {'literal_object': {'reuse_text': 'Literal nested data.'}}
        for check in packet['checks']:
            check['target'] += ' 长目标末尾不能丢失🙂' * 30
        workflow.write(packet_path, packet)
        manifest_path = reading.build(self.root, packet_path, template_path)
        manifest = read(manifest_path)
        before = self.snapshot()
        items = []
        process = subprocess.run([sys.executable, '-B', api.__file__, 'read-review', '--root', str(self.root),
                                  '--manifest', str(manifest_path), '--page', '1'],
                                 capture_output=True, encoding='utf-8')
        self.assertEqual(0, process.returncode, process.stdout + process.stderr)
        self.assertEqual('page_001', json.loads(process.stdout)['page_id'])
        for number in range(1, len(manifest['pages']) + 1):
            page = reading.page(self.root, manifest_path, number)
            self.assertLess(len(json.dumps(page, ensure_ascii=False).encode('utf-8')), 8000)
            items.extend(page['content'])
            self.assertEqual(page, reading.page(self.root, manifest_path, number))
        self.assertEqual(before, self.snapshot())
        encoded = json.loads(''.join(item['text'] for item in items))
        def decode(value, literal=False):
            if isinstance(value, dict):
                if not literal and set(value) == {'reuse_text'}:
                    return encoded['shared_text'][value['reuse_text']]
                if not literal and set(value) == {'literal_object'}:
                    return decode(value['literal_object'], literal=True)
                return {k: decode(v) for k, v in value.items()}
            if isinstance(value, list):
                return [decode(v) for v in value]
            return value
        document = decode(encoded['document'])
        self.assertEqual(reading.semantic_document(packet, read(template_path)), document)
        self.assertEqual(prompt, document['generation_context']['prompt'])
        page_file = self.root / manifest['pages'][0]['file']
        with page_file.open('ab') as stream:
            stream.write(b' ')
        with self.assertRaisesRegex(ValueError, '阅读页已变化'):
            reading.page(self.root, manifest_path, 1)

    def test_missing_or_stale_full_reading_declaration_blocks_without_changing_visual_results(self):
        handoff = self.simple(self.receive(api.prepare(self.root, 'head', self.config())))
        report = self.report(handoff)
        before = self.snapshot()
        for change in ('missing', 'skipped', 'stale'):
            invalid = copy.deepcopy(report)
            if change == 'missing':
                invalid.pop('reading_confirmation')
            elif change == 'skipped':
                invalid['reading_confirmation']['completed_page_ids'].pop(0)
            else:
                invalid['reading_confirmation']['manifest_sha256'] = '0' * 64
            for result in (api.check_report(self.root, 'head', invalid), api.review(self.root, 'head', invalid)):
                self.assertEqual('invalid', result['report_validation'])
                self.assertFalse(result['visual_state_changed'])
                self.assertEqual({'pass'}, set(result['reported_visual_results'].values()))
                self.assertNotIn('requests', result)
        self.assertEqual(before, self.snapshot())
        result = api.review(self.root, 'head', report)
        self.assertEqual(report['reviewer'], result['registered_reviewer'])
        self.assertEqual(report['call_id'], result['registered_call_id'])

    def test_truncated_request_batch_or_changed_execution_bundle_never_calls_provider(self):
        runtime = Path(sys.executable).parents[1]
        node, shell = runtime / 'node/bin/node.exe', runtime / 'native/powershell/pwsh.exe'
        if os.name != 'nt' or not node.is_file() or not shell.is_file():
            self.skipTest('Windows Node and shell integration')
        request = self.baseline()
        requests_path = Path(request['requests_file'])
        saved = read(requests_path)
        workflow.write(requests_path, dict(saved, requests=saved['requests'][:1]))
        fixture = self.base / 'batch-rejection.json'
        workflow.write(fixture, {'code': request['execution_code'], 'results': [], 'allow_first_error': True, 'shell': str(shell)})
        def execute():
            process = subprocess.run([str(node), str(Path(__file__).with_name('exec_batch_harness.mjs')), str(fixture)],
                                     capture_output=True, encoding='utf-8', timeout=60)
            self.assertEqual(0, process.returncode, process.stderr)
            return json.loads(process.stdout)
        self.assertEqual(0, execute()['call_count'])
        workflow.write(requests_path, saved)
        with requests_path.with_suffix('.exec.json').open('ab') as stream:
            stream.write(b' ')
        self.assertEqual(0, execute()['call_count'])
