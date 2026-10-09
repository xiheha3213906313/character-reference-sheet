"""Regressions for view scoping, stage isolation, front occlusion and resumable delivery.

All images and observations here are synthetic plumbing fixtures, never visual answers.
"""
import copy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from PIL import Image

import test_sheet_flow as fixtures
import sheet_flow as api
import review_workflow as workflow
import production_delivery as delivery
import spatial_plan
from production_records import FLOW
from review_v2 import read, digest


class HandoffTest(unittest.TestCase):
    for name in ('setUp', 'config', 'image', 'receive', 'simple', 'packet', 'report', 'baseline', 'finish_stage', 'structure'):
        locals()[name] = getattr(fixtures.SheetFlowTest, name)

    def ready_front(self):
        self.finish_stage('head')
        ranking = self.receive(self.baseline('front'), 'front')
        board = ranking['ranking_board']
        return api.rank(self.root, 'front', {'preferred_call': board['call_ids'][0],
            'reason': 'Synthetic selected front.', 'board_sha256': board['sha256']})

    def test_front_selection_requires_fixed_structure_once_without_generation(self):
        action = self.ready_front()
        self.assertEqual('analyze_front_structure', action['next_action'])
        before = read(self.root / FLOW)['requests']
        self.assertEqual({'hair_flow', 'clothing_layers'}, {i['id'] for i in read(Path(action['config_file']))['items']})
        with self.assertRaisesRegex(ValueError, 'record-structure'):
            api.prepare(self.root, 'back', self.config('back'))
        next_action = self.structure(action)
        self.assertEqual('prepare_next_stage', next_action['next_action'])
        self.assertEqual('back', next_action['next_stage'])
        self.assertEqual(before, read(self.root / FLOW)['requests'])
        saved = read(self.root / FLOW)['front_structure']
        self.assertEqual('maker_observed_relations; not_visual_approval', read(self.root / saved['file'])['semantics'])

    def test_structure_missing_item_and_unsupported_rear_guess_do_not_save(self):
        action = self.ready_front()
        config = read(Path(action['config_file']))
        config['items'].pop()
        before = (self.root / FLOW).read_bytes()
        with self.assertRaisesRegex(ValueError, '全部特征'):
            spatial_plan.record(self.root, config)
        self.assertEqual(before, (self.root / FLOW).read_bytes())

    def test_braid_route_is_compiled_once_and_hidden_ends_are_explicit(self):
        action = self.ready_front()
        self.structure(action)
        ref = read(self.root / FLOW)['front_structure']
        config = read(self.root / ref['file'])
        config = {k: config[k] for k in ('binding_sha256', 'items')}
        hair = config['items'][0]
        hair.update(path='Synthetic braids pass over shoulders to the chest.', occluded_by='Synthetic shoulders and chest.')
        hair['stages']['back'].update(visibility='visible', visible_portion='Synthetic rear roots only.',
            hidden_portion='Synthetic lower braid and ties remain in front.')
        spatial_plan.record(self.root, config)
        request = api.prepare(self.root, 'back', self.config('back'))
        prompt = request['requests'][0]['arguments']['prompt']
        self.assertEqual(1, prompt.count(hair['path']))
        self.assertIn('不复制或移到背后', prompt)
        self.assertIn('后脑可见双辫根部不代表整条辫子垂在背后', prompt)

    def test_structure_semantic_change_invalidates_only_prepared_downstream(self):
        action = self.ready_front()
        self.structure(action)
        self.finish_stage('back')
        before = workflow.load_all(self.root)[1]['stages']
        calls = copy.deepcopy(read(self.root / FLOW)['requests'])
        ref = read(self.root / FLOW)['front_structure']
        saved = read(self.root / ref['file'])
        config = {k: saved[k] for k in ('binding_sha256', 'items')}
        config['items'][0]['path'] += ' Synthetic corrected attachment route.'
        spatial_plan.record(self.root, config)
        after = workflow.load_all(self.root)[1]['stages']
        self.assertEqual(before['head'], after['head'])
        self.assertEqual(before['front'], after['front'])
        self.assertEqual('unreviewed', after['back']['decision'])
        self.assertTrue(after['back']['plan_requires_prepare'])
        self.assertEqual(calls, read(self.root / FLOW)['requests'])

    def test_structure_tampering_or_selected_front_bytes_invalidates_binding(self):
        action = self.ready_front()
        self.structure(action)
        flow = read(self.root / FLOW)
        values = workflow.load_all(self.root)
        self.assertTrue(spatial_plan.valid(self.root, flow, values))
        with (self.root / values[1]['stages']['front']['image']).open('ab') as stream:
            stream.write(b'changed')
        self.assertFalse(spatial_plan.valid(self.root, flow, values))

    def test_each_stage_handoff_is_new_and_other_stage_agent_is_rejected(self):
        self.finish_stage('head')
        head_agent = 'synthetic-reviewer'
        simple = self.receive(api.prepare(self.root, 'front', self.config('front')), 'front')
        action = self.simple(simple, 'front')
        handoff = action['reviewer_handoff']
        self.assertEqual('spawn_new_stage_agent', handoff['dispatch_mode'])
        self.assertIsNone(handoff['agent_id'])
        self.assertEqual('review_front', handoff['task_name'])
        self.assertIn('fork_turns:none', handoff['task'])
        report = self.report(action, 'front')
        report['reviewer']['agent_id'] = head_agent
        report['reviewer_replacement_reason'] = 'Synthetic cannot permit cross-stage replacement.'
        before = (self.root / FLOW).read_bytes()
        result = api.review(self.root, 'front', report)
        self.assertEqual('correct_report_fields', result['next_action'])
        self.assertIn('其他阶段', str(result['field_errors']))
        self.assertEqual(before, (self.root / FLOW).read_bytes())

    def test_selected_extra_handoff_reuses_only_own_stage_agent(self):
        action = self.receive(self.baseline())
        board = action['ranking_board']
        simple = api.rank(self.root, 'head', {'preferred_call': board['call_ids'][1],
            'reason': 'Synthetic winning extra.', 'board_sha256': board['sha256']})
        handoff = self.simple(simple)['reviewer_handoff']
        self.assertEqual('reuse_stage_agent', handoff['dispatch_mode'])
        self.assertEqual('synthetic-reviewer', handoff['agent_id'])

    def test_forbidden_or_specified_agent_mode_is_explicit_in_handoff(self):
        config = self.config()
        config['reviewer_policy'] = {'mode': 'self', 'model': None, 'user_override_reason': 'Synthetic user forbids subagents.'}
        simple = self.receive(api.prepare(self.root, 'head', config))
        handoff = self.simple(simple)['reviewer_handoff']
        self.assertEqual('full_self_review', handoff['dispatch_mode'])
        self.assertIsNone(handoff['agent_id'])
        self.root = self.base / 'specified_model'
        api.start(self.root, self.sources)
        config['reviewer_policy'] = {'mode': 'prefer_subagent', 'model': 'synthetic-user-model'}
        simple = self.receive(api.prepare(self.root, 'head', config))
        handoff = self.simple(simple)['reviewer_handoff']
        self.assertEqual('spawn_new_stage_agent', handoff['dispatch_mode'])
        self.assertEqual('synthetic-user-model', handoff['model'])

    def test_front_structure_artifact_has_published_fixed_schema(self):
        import record_contract
        self.structure(self.ready_front())
        flow = read(self.root / FLOW)
        saved = read(self.root / flow['front_structure']['file'])
        schema = read(Path(__file__).parents[2] / 'references/front-structure.schema.json')
        record_contract.validate(saved, schema)
        saved['invented_field'] = 'reject'
        with self.assertRaisesRegex(ValueError, 'unknown field'):
            record_contract.validate(saved, schema)
        del saved['invented_field']
        saved['sources']['bad_source_key'] = saved['sources']['front_selected']
        with self.assertRaisesRegex(ValueError, 'invalid text'):
            record_contract.validate(saved, schema)

    def test_report_preflight_is_read_only_and_requires_native_and_reduced_evidence(self):
        action = self.simple(self.receive(api.prepare(self.root, 'head', self.config())))
        report = self.report(action)
        before = {p: p.read_bytes() for p in self.root.rglob('*.json')}
        good = api.check_report(self.root, 'head', report)
        self.assertEqual('report_ready', good['next_action'])
        self.assertFalse(good['visual_approval_recorded'])
        packet = read(self.root / action['packet_file'])
        for item in report['checks']:
            if item['id'] in ('native_quality', 'reduced_quality') or any(
                    g['id'] == item['id'] and g['group'] in ('native_quality', 'reduced_quality')
                    for g in packet['checks']):
                item['evidence_ids'] = ['compare_001']
        bad = api.check_report(self.root, 'head', report)
        self.assertEqual('correct_report_fields', bad['next_action'])
        self.assertIn('主要证据', str(bad['field_errors']))
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*.json')})
        handoff = action['reviewer_handoff']
        self.assertIn(handoff['report_output_file'], handoff['check_report_command'])

    def test_context_only_sources_do_not_create_opposite_view_comparisons(self):
        from review_references import groups
        goals = [dict(id='identity', group='identity', target='Synthetic face.', source_ids=['front']),
                 dict(id='rear_outside', group='design', target='Synthetic below crop.',
                      source_ids=['rear'], reference_display='context_only')]
        boards = groups(goals)
        self.assertEqual([{'reference_id': 'source_front', 'checks': [
            {'id': 'identity', 'target': 'Synthetic face.', 'evaluation_scope': ''}]}], boards[0]['reference_scopes'])
        self.assertEqual(['source_front'], boards[0]['reference_ids'])
        self.assertEqual(['identity'], boards[0]['check_ids'])

    def test_pass_cannot_reference_a_board_missing_its_applicable_original(self):
        from review_policy import check_review_coverage
        packet = {'evidence': [{'id': 'board', 'kind': 'comparison', 'source_ids': ['front']}],
                  'checks': [dict(id='rear', group='design', source_ids=['rear'])]}
        report = {'viewed_evidence_ids': ['board'], 'checks': [dict(id='rear', result='pass', evidence_ids=['board'])]}
        with self.assertRaisesRegex(ValueError, '全部适用参考'):
            check_review_coverage(packet, report)

    def test_back_visibility_cannot_be_inferred_from_front_entity(self):
        from accessory_plan import validate
        accessory = dict(id='ties', name='Synthetic tie', carrier='Synthetic braid', location='Synthetic chest',
                         visibility='visible', observation='Synthetic front observation.')
        materials = [dict(source_id='front', decision='adopt', views=['front'], accessories=[accessory]),
                     dict(source_id='rear', decision='partial', views=['back'], accessories=[])]
        plan = [{k: accessory[k] for k in ('id', 'name', 'carrier', 'location')} |
                {'stages': {r: dict(visibility='visible', source_ids=['front'], reason='Synthetic same entity.') for r in api.ROLES}}]
        with self.assertRaisesRegex(ValueError, '不能由正面同一实体推断'):
            validate(plan, materials)
        plan[0]['stages']['back']['visibility'] = 'unclear'
        validate(plan, materials)

    def test_original_output_hint_supports_other_formats_and_names_without_fake_base64(self):
        request = api.prepare(self.root, 'head', self.config())
        path = self.base / 'arbitrary source.webp'
        Image.new('RGBA', (36, 48), (60, 80, 100, 255)).save(path, lossless=True)
        raw = {'output_hint': 'Saved here: ' + str(path), 'image_url': 'data:image/webp;base64,placeholder'}
        action = api.receive(self.root, 'head', {'calls': [dict(request_id=request['requests'][0]['request_id'],
                                                              tool_call_id=None, result=raw)]})
        self.assertEqual('self-check', action['next_action'])
        receipt = read(next(self.root.glob('制作记录/调用记录/*-receipt.json')))
        self.assertEqual(raw, receipt['raw_receipt'])
        self.assertEqual('WEBP', receipt['original_output']['format'])

    def test_tiff_original_is_usable_in_comparisons_without_provider_filename_rules(self):
        source = self.base / 'tiff_sources'
        source.mkdir()
        Image.new('RGB', (36, 48), (90, 80, 70)).save(source / 'original scan.tif')
        self.root = self.base / 'tiff_project'
        api.start(self.root, source)
        action = self.receive(api.prepare(self.root, 'head', self.config()))
        self.assertTrue(action['comparisons'])
        packet = self.packet(action)
        self.assertTrue(any(e['id'] == 'source_s001' for e in packet['evidence']))

    def test_malformed_preflight_is_actionable_and_does_not_mutate(self):
        action = self.simple(self.receive(api.prepare(self.root, 'head', self.config())))
        report = self.report(action)
        report['checks'] = ['malformed item']
        before = (self.root / FLOW).read_bytes()
        result = api.check_report(self.root, 'head', report)
        self.assertEqual('correct_report_fields', result['next_action'])
        self.assertEqual(before, (self.root / FLOW).read_bytes())

    def test_delivery_destination_is_fixed_at_start_and_late_outputs_override_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '未知字段'):
            delivery.deliver(self.root, {'destination': str(self.base / 'outputs'), 'destination_reason': 'Synthetic invented request.'})
        with self.assertRaisesRegex(ValueError, '用户明确'):
            api.start(self.base / 'bad', self.sources, self.base / 'outputs')
        explicit = self.base / 'user chosen'
        root = self.base / 'explicit'
        api.start(root, self.sources, explicit, '请交付到 user chosen')
        self.assertEqual(str(explicit.resolve()), read(root / FLOW)['delivery_override']['destination'])

    def test_permission_interruption_resumes_same_directory_without_overwriting_foreign_files(self):
        bundle, destination = self.base / 'bundle', self.sources / 'delivery'
        bundle.mkdir()
        (bundle / 'a.txt').write_text('Synthetic A', encoding='utf-8')
        (bundle / 'b.txt').write_text('Synthetic B', encoding='utf-8')
        original = delivery.shutil.copyfile
        def interrupt(source, target):
            if Path(source).name == 'b.txt':
                raise PermissionError('Synthetic permission failure')
            return original(source, target)
        with patch.object(delivery.shutil, 'copyfile', side_effect=interrupt):
            with self.assertRaises(PermissionError):
                delivery.checked_copy(bundle, destination)
        self.assertTrue((destination / 'a.txt').is_file())
        self.assertFalse((destination / 'b.txt').exists())
        self.assertFalse(list(destination.glob('*.tmp')))
        delivery.checked_copy(bundle, destination)
        self.assertEqual(digest(bundle / 'b.txt'), digest(destination / 'b.txt'))
        self.assertFalse(destination.with_name('.' + destination.name + '.sheet-copy.json').exists())
        (destination / 'foreign.txt').write_text('preserve', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '保留原目录'):
            delivery.checked_copy(bundle, destination)
        self.assertEqual('preserve', (destination / 'foreign.txt').read_text(encoding='utf-8'))

    def test_new_default_delivery_stays_under_materials_and_avoids_old_directory(self):
        flow = read(self.root / FLOW)
        (self.sources / 'old').mkdir()
        path = delivery.destination_for(flow, 'old.jpg', 'a' * 64)
        self.assertEqual(self.sources.resolve(), path.parent)
        self.assertNotEqual(self.sources / 'old', path)

    def test_permission_response_preserves_exact_command_even_without_config(self):
        arguments = ['sheet_flow.py', 'start', '--root', str(self.base / 'new'), '--sources', str(self.sources)]
        with patch.object(api.sys, 'argv', arguments), patch.object(api, 'start', side_effect=PermissionError('Synthetic deny')), \
                patch.object(api.sys, 'stdout', new_callable=io.StringIO) as output:
            self.assertEqual(2, api.main())
            result = json.loads(output.getvalue())
        self.assertEqual('request_file_access', result['next_action'])
        self.assertIn(str(self.sources.resolve()), result['retry_command'])
        self.assertIn('start', result['retry_command'])
        self.assertFalse(result['script_source_required'])

    def test_supplement_preserves_applicable_source_scope_without_requiring_old_board(self):
        from production_evidence import supplement
        handoff = self.simple(self.receive(api.prepare(self.root, 'head', self.config())))
        report = self.report(handoff, result='pending')
        api.review(self.root, 'head', report)
        action = supplement(self.root, 'head', {'reason': 'Synthetic unclear spatial detail.',
            'comparisons': [{'id': 'spatial', 'reference_ids': ['source_s001'], 'check_ids': ['design_shoulder']}]})
        template = read(self.root / action['reviewer_handoff']['report_template_file'])
        report = self.report(handoff)
        report.update(supplement_round=1, extra_evidence=template['extra_evidence'])
        board = template['extra_evidence'][0]
        self.assertEqual(['s001'], board['source_ids'])
        goal = next(c for c in report['checks'] if c['id']=='design_shoulder')
        goal['evidence_ids'] = [board['id']]
        report['viewed_evidence_ids'].append(board['id'])
        self.assertEqual('report_ready', api.check_report(self.root, 'head', report)['next_action'])
        self.assertEqual('generate_parallel', api.review(self.root, 'head', report)['next_action'])

    def test_supplement_source_labels_cannot_be_rewritten_to_claim_coverage(self):
        from production_evidence import supplement
        handoff = self.simple(self.receive(api.prepare(self.root, 'head', self.config())))
        api.review(self.root, 'head', self.report(handoff, result='pending'))
        action = supplement(self.root, 'head', {'reason': 'Synthetic unclear detail.',
            'comparisons': [{'id':'detail', 'reference_ids':['source_s001']}]})
        report = self.report(handoff)
        report.update(supplement_round=1, extra_evidence=read(self.root / action['reviewer_handoff']['report_template_file'])['extra_evidence'])
        report['extra_evidence'][0]['source_ids'] = ['fabricated_other_view']
        before = (self.root / FLOW).read_bytes()
        result = api.check_report(self.root, 'head', report)
        self.assertEqual('correct_report_fields', result['next_action'])
        self.assertIn('标签或来源', str(result['field_errors']))
        self.assertEqual(before, (self.root / FLOW).read_bytes())


if __name__ == '__main__':
    unittest.main()
