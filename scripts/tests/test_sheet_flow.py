"""Production-interface plumbing. All observations/tool ids are synthetic, never visual truth."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sheet_flow as api
import production_delivery as delivery
from production_records import FLOW, RECORD
import review_workflow as workflow
from review_v2 import read, digest


class SheetFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sheet-api-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.sources = self.base / 'sources'
        self.sources.mkdir()
        self.root = self.base / 'production'
        Image.new('RGB', (36, 48), (50, 70, 90)).save(self.sources / 'reference.png')
        self.start_response = api.start(self.root, self.sources)
        self.sequence = 0

    def config(self, role='head'):
        references = [{'source_id': 's001', 'role': 'Synthetic original design evidence.'}]
        if role != 'head':
            references.insert(0, {'stage': 'head' if role == 'front' else 'front', 'role': 'Synthetic valid upstream.'})
        if role == 'left':
            references.insert(1, {'stage': 'back', 'role': 'Synthetic valid rear upstream.'})
        config = {'prompt': {'operation': 'generate', 'identity': 'Synthetic character identity for interface tests.',
                             'references': references, 'critical_constraints': [
                                 {'id': 'shoulder', 'kind': 'spatial', 'source_indices': [len(references)],
                                  'statement': 'Synthetic shoulder position and wearing relation, not an actual visual observation.'}]}}
        if role == 'head':
            config.update(character={'name': '测试角色', 'height_cm': 163, 'age_years': 19,
                                     'appearance': 'Synthetic appearance.', 'outfit': 'Synthetic white jacket and black shirt.',
                                     'outfit_name': '白外套测试造型', 'setting_basis': 'Synthetic test fixture values, not estimated facts.'},
                          materials=[{'source_id': 's001', 'views': ['detail'], 'observation': 'Synthetic source observation | with newline\nfor template escaping.',
                                      'quality': 'Synthetic region quality and limits.', 'selection_reason': 'Synthetic complementary identity evidence.',
                                      'decision': 'adopt', 'uses': [{'id': 'identity_source', 'kind': 'identity', 'stages': ['head'], 'target': 'Synthetic identity/design evidence.'}]}])
        return config

    def image(self, role='head'):
        self.sequence += 1
        path = self.base / f'{role}-synthetic-{self.sequence}.png'
        size = (36, 48) if role == 'head' else (27, 48)
        image = Image.new('RGBA', size)
        ImageDraw.Draw(image).rectangle((5, 3, size[0] - 6, size[1] - 4), fill=(20 + self.sequence, 80, 100, 255))
        image.save(path)
        return path

    def receive(self, response, role='head', failed=()):
        calls = []
        for index, request in enumerate(response['requests']):
            c = {'request_id': request['request_id'], 'tool_call_id': 'synthetic-tool-' + request['request_id']}
            c.update({'error': 'Synthetic tool failure.'} if index in failed else {'output': str(self.image(role))})
            calls.append(c)
        return api.receive(self.root, role, {'calls': calls})

    def simple(self, response, role='head', result='pass'):
        return api.simple_check(self.root, role, {'call_id': response['call_id'], 'review_token': response['review_token'],
            'result': result, 'observation': 'Synthetic comparison-only observation; no visual assertion.',
            'viewed_evidence_ids': [response['comparisons'][0]['id']],
            **({'issue_key': 'synthetic_shoulder'} if result == 'fail' else {})})

    def report(self, response, role='head', result='pass', mode='subagent'):
        handoff = response['reviewer_handoff']
        packet = read(self.root / handoff['packet_file'])
        report = read(self.root / handoff['report_template_file'])
        report['reviewer'] = {'mode': mode, 'model': 'inherited', 'agent_id': 'synthetic-reviewer'}
        if mode == 'self':
            report['reviewer'].update(reason_code='user_disabled', reason='Synthetic user prohibition.')
        viewed = set()
        for goal, item in zip(packet['checks'], report['checks']):
            ids = {'native_quality': ['native_001'], 'reduced_quality': ['reduced'], 'background': ['alpha_compare']}.get(goal['group'], ['compare_001'])
            viewed.update(ids)
            item.update(result=result, reference_observation='Synthetic reference fact for plumbing only.',
                        candidate_observation='Synthetic candidate fact for plumbing only.',
                        comparison_basis='Synthetic positional comparison for plumbing only.', evidence_ids=ids)
            if goal['group'] == 'background':
                item['empty_background_samples'] = [{'xy': [0, 0], 'confirmed_empty': True}]
            if result == 'fail':
                item['issue_key'] = 'synthetic_shoulder'
        report['viewed_evidence_ids'] = sorted(viewed)
        report['quality_observations'] = {'hair': 'Synthetic native hair observation.', 'face': 'Synthetic native face observation.'}
        return report

    def baseline(self, role='head', config=None, mode='subagent'):
        request = api.prepare(self.root, role, config or self.config(role))
        simple = self.receive(request, role)
        review = self.simple(simple, role)
        return api.review(self.root, role, self.report(review, role, mode=mode))

    def finish_stage(self, role='head', limit=False):
        config = self.config(role)
        if limit:
            config['generation_limit'] = 1
            comparison = self.receive(api.prepare(self.root, role, config), role)
        else:
            comparison = self.receive(self.baseline(role), role)
        board = comparison['ranking_board']
        return api.rank(self.root, role, {'preferred_call': board['call_ids'][0], 'reason': 'Synthetic baseline preference.',
                                         'board_sha256': board['sha256']})

    def test_start_records_are_fixed_pending_and_markdown_is_derived(self):
        self.assertEqual('forbidden_during_production', self.start_response['source_access'])
        self.assertFalse(any('source' in s or 'implementation' in s for s in self.start_response['do_not_read_yet']))
        record = read(self.root / RECORD)
        self.assertEqual(3, record['schema_version'])
        self.assertIsNone(record['character'])
        self.assertEqual([], record['stages'])
        self.assertEqual([], record['material_analysis'])
        self.assertNotIn('viewed', record['sources'][0])
        with self.assertRaisesRegex(ValueError, '已开始'):
            api.start(self.root, self.sources)

    def test_prepare_reuses_single_semantic_input_for_prompt_targets_and_documents(self):
        request = api.prepare(self.root, 'head', self.config())
        self.assertEqual('generate', request['next_action'])
        record = read(self.root / RECORD)
        design = next(g for g in record['stages'][0]['targets'] if g['group'] == 'design')
        self.assertEqual(self.config()['prompt']['critical_constraints'][0]['statement'], design['target'])
        self.assertNotIn('arguments', record['requests'][0])
        self.assertEqual(1, len(list((self.root / '制作记录/提示词').glob('*.txt'))))
        self.assertIn('\\|', (self.root / '制作记录/图片选用文档.md').read_text(encoding='utf-8'))
        self.assertNotIn('setting_basis', (self.root / '角色描述.md').read_text(encoding='utf-8'))

    def test_simple_comparison_only_opens_handoff_then_reviewer_returns_parallel_requests(self):
        simple = self.receive(api.prepare(self.root, 'head', self.config()))
        self.assertEqual('self-check', simple['next_action'])
        handoff = self.simple(simple)
        self.assertTrue(handoff['reviewer_dispatch_ready'])
        extra = api.review(self.root, 'head', self.report(handoff))
        self.assertEqual('generate_parallel', extra['next_action'])
        self.assertEqual(2, len(extra['requests']))
        self.assertEqual(extra['requests'][0]['arguments'], extra['requests'][1]['arguments'])
        self.assertEqual('transparent', read(self.root / handoff['packet_file'])['background_policy']['mode'])

    def test_extras_must_be_received_together_and_remain_unreviewed(self):
        extra = self.baseline()
        first = extra['requests'][0]
        before = (self.root / FLOW).read_bytes()
        with self.assertRaisesRegex(ValueError, '一次登记'):
            api.receive(self.root, 'head', {'calls': [{'request_id': first['request_id'], 'tool_call_id': 'synthetic-partial', 'output': str(self.image())}]})
        self.assertEqual(before, (self.root / FLOW).read_bytes())
        ranking = self.receive(extra)
        self.assertEqual('rank', ranking['next_action'])
        calls = read(self.root / RECORD)['stages'][0]['calls']
        self.assertEqual(3, len(calls))
        self.assertTrue(all(c['self_check'] is None and c['visual_review'] is None for c in calls[1:]))
        self.assertTrue((self.root / ranking['ranking_board']['file']).is_file())

    def test_extra_failure_is_counted_and_does_not_reserve_replacement(self):
        ranking = self.receive(self.baseline(), failed=(1,))
        self.assertEqual(2, len(ranking['ranking_board']['call_ids']))
        record = read(self.root / RECORD)
        self.assertEqual(3, len(record['stages'][0]['calls']))
        self.assertIsNone(record['stages'][0]['calls'][-1]['output'])
        self.assertEqual(3, len(record['requests']))

    def test_winning_extra_failure_falls_back_without_new_requests(self):
        ranking = self.receive(self.baseline())
        board = ranking['ranking_board']
        simple = api.rank(self.root, 'head', {'preferred_call': board['call_ids'][1], 'reason': 'Synthetic improvement.', 'board_sha256': board['sha256']})
        handoff = self.simple(simple)
        result = api.review(self.root, 'head', self.report(handoff, result='fail'))
        self.assertEqual('prepare_next_stage', result['next_action'])
        stage = read(self.root / RECORD)['stages'][0]
        self.assertEqual(stage['baseline_call_id'], stage['selected_call_id'])
        self.assertEqual(3, len(read(self.root / FLOW)['requests']))

    def test_ceiling_selects_without_qualification_or_agent(self):
        result = self.finish_stage(limit=True)
        self.assertEqual('prepare_next_stage', result['next_action'])
        record = read(self.root / RECORD)
        self.assertEqual('selected_unreviewed', record['stages'][0]['decision'])
        self.assertIsNone(record['stages'][0]['calls'][0]['visual_review'])

    def test_user_disabled_review_is_recorded_honestly(self):
        config = self.config()
        config['reviewer_policy'] = {'mode': 'self', 'model': None, 'user_override_reason': 'Synthetic user prohibition.'}
        result = self.baseline(config=config, mode='self')
        self.assertEqual('generate_parallel', result['next_action'])
        report = read(self.root / RECORD)['stages'][0]['calls'][0]['visual_review']['data']
        self.assertEqual('self', report['reviewer']['mode'])

    def test_invalid_capacity_and_unknown_input_are_rejected_but_duplicates_fit(self):
        config = self.config()
        config['max_reference_images'] = 0
        with self.assertRaisesRegex(ValueError, 'max_reference_images'):
            api.prepare(self.root, 'head', config)
        self.assertEqual({}, read(self.root / FLOW)['requests'])
        config = self.config()
        config['surprise'] = 'must not ignore'
        with self.assertRaisesRegex(ValueError, '未知字段'):
            api.prepare(self.root, 'head', config)
        config = self.config()
        config['prompt']['references'] *= 6
        result = api.prepare(self.root, 'head', config)
        self.assertEqual(1, len(result['requests'][0]['arguments']['referenced_image_paths']))
        self.assertEqual(5, len(result['reference_packing']['duplicates_removed']))

    def test_bad_report_returns_field_errors_without_mutation(self):
        simple = self.receive(api.prepare(self.root, 'head', self.config()))
        handoff = self.simple(simple)
        report = self.report(handoff)
        report['checks'][0]['candidate_observation'] = ''
        report['checks'][0]['invented_field'] = 'Synthetic reject this field.'
        before = (self.root / RECORD).read_bytes()
        result = api.review(self.root, 'head', report)
        self.assertEqual('correct_report_fields', result['next_action'])
        self.assertTrue(result['field_errors'])
        self.assertTrue(any(e['field'].endswith('invented_field') for e in result['field_errors']))
        self.assertEqual(before, (self.root / RECORD).read_bytes())

    def test_status_is_read_only_and_preserves_reserved_ids(self):
        request = api.prepare(self.root, 'head', self.config())
        before = {p: digest(p) for p in self.root.rglob('*') if p.is_file()}
        status = api.advance(self.root, 'head', mutate=False)
        self.assertEqual(request, status)
        self.assertEqual(before, {p: digest(p) for p in self.root.rglob('*') if p.is_file()})

    def test_fixed_arguments_cannot_be_overridden_by_parameters(self):
        before = {p: digest(p) for p in self.root.rglob('*') if p.is_file()}
        for parameters in ({'prompt': 'bypass'}, {'referenced_image_paths': []},
                           {'num_last_images_to_include': 2}, {'transparent_background': False}, {'seed': 1}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                api.prepare(self.root, 'head', dict(self.config(), parameters=parameters))
        self.assertEqual(before, {p: digest(p) for p in self.root.rglob('*') if p.is_file()})

    def test_review_endpoint_cannot_bypass_board_ranking(self):
        with self.assertRaisesRegex(ValueError, 'review只接收visual'):
            api.review(self.root, 'head', {'kind': 'selection'})

    def test_invalid_second_output_does_not_write_first_receipt(self):
        response = self.baseline()
        before = {p: digest(p) for p in self.root.rglob('*') if p.is_file()}
        calls = [{'request_id': r['request_id'], 'tool_call_id': 'synthetic-bad-batch-' + str(i),
                  'output': str(self.image() if i == 0 else self.base / 'missing.png')}
                 for i, r in enumerate(response['requests'])]
        with self.assertRaises(OSError):
            api.receive(self.root, 'head', {'calls': calls})
        self.assertEqual(before, {p: digest(p) for p in self.root.rglob('*') if p.is_file()})

    def test_changed_recipe_history_remains_resolvable(self):
        first = self.receive(api.prepare(self.root, 'head', self.config()))
        self.simple(first, result='fail')
        revised = self.config()
        revised['prompt']['quality_notes'] = 'Synthetic targeted repair.'
        api.prepare(self.root, 'head', revised)
        record = read(self.root / RECORD)
        self.assertEqual(2, len(record['recipes']))
        call = record['stages'][0]['calls'][0]
        self.assertIn(call['recipe_sha256'], record['recipes'])
        self.assertEqual(call['review_packet']['sha256'], digest(self.root / call['review_packet']['file']))

    def test_extent_targets_and_user_ratio_are_not_lost(self):
        config = self.config()
        config['prompt']['user_overrides'] = {'aspect_ratio': {'value': '1:1', 'user_quote': 'Synthetic requested square crop.'}}
        config['prompt']['critical_constraints'][0] = {'id': 'chain', 'kind': 'extent', 'source_indices': [1],
            'statement': 'Synthetic hanging chain.', 'visibility': 'partial',
            'path': 'Synthetic cheek-to-chest route.', 'frame_behavior': 'Synthetic continue beyond the lower edge.'}
        api.prepare(self.root, 'head', config)
        targets = read(self.root / RECORD)['stages'][0]['targets']
        self.assertIn('Synthetic continue beyond the lower edge.', next(g for g in targets if g['group'] == 'design')['target'])
        framing = next(g for g in targets if g.get('aspect') == 'framing')
        self.assertIn('1:1', framing['target'])
        self.assertEqual('user', framing['requirement_origin'])

    def test_source_crops_are_built_inside_prepare_at_native_scale(self):
        config = self.config()
        config['materials'][0]['uses'][0]['crop'] = [0, 0, 20, 25]
        config['prompt']['references'] = [{'role': 'Synthetic complementary native regions.', 'panels': [
            {'source_id': 's001', 'crop': [0, 0, 20, 25]}, {'source_id': 's001', 'crop': [10, 20, 30, 45]}]}]
        request = api.prepare(self.root, 'head', config)
        self.assertEqual(1, len(request['requests'][0]['arguments']['referenced_image_paths']))
        self.assertEqual(1, request['reference_coverage'][0]['reference_index'])
        boards = list((self.root / '制作记录/参考辅助').glob('*.png.json'))
        self.assertEqual(1, len(boards))
        self.assertTrue(all(p['actual_scale_xy'] == [1, 1] for row in read(boards[0])['rows'] for p in row['panels']))
        stage = read(self.root / RECORD)['stages'][0]
        self.assertTrue(all(i in stage['target_sources'] for goal in stage['targets'] for i in goal['source_ids']))

    def test_supplement_is_one_pending_batch_and_does_not_generate(self):
        from production_evidence import supplement
        first = self.receive(api.prepare(self.root, 'head', self.config()))
        handoff = self.simple(first)
        api.review(self.root, 'head', self.report(handoff, result='pending'))
        config = {'reason': 'Synthetic uncertainty.', 'regions': [{'id': 'detail', 'crop': [0, 0, 20, 25]}],
                  'comparisons': [{'id': 'position', 'reference_ids': ['source_s001'], 'candidate_crop': [0, 0, 20, 25]}]}
        result = supplement(self.root, 'head', config)
        self.assertEqual('review_supplement', result['next_action'])
        self.assertEqual('synthetic-reviewer', result['reviewer_handoff']['previous_reviewer']['agent_id'])
        template = read(self.root / result['reviewer_handoff']['report_template_file'])
        self.assertTrue(all(c['result'] == 'pending' for c in template['checks']))
        self.assertEqual([], template['viewed_evidence_ids'])
        self.assertEqual(result, supplement(self.root, 'head', config))
        self.assertEqual('review_supplement', api.advance(self.root, 'head', mutate=False)['next_action'])
        with self.assertRaisesRegex(ValueError, '一次补证'):
            supplement(self.root, 'head', dict(config, reason='Synthetic second batch.'))
        report = self.report(handoff, result='pending')
        report.update(supplement_round=1, extra_evidence=template['extra_evidence'])
        report['reviewer']['agent_id'] = 'different-synthetic-reviewer'
        with self.assertRaisesRegex(ValueError, '原复核代理'):
            api.review(self.root, 'head', report)
        report['reviewer']['agent_id'] = 'synthetic-reviewer'
        done = api.review(self.root, 'head', report)
        self.assertEqual('blocked_uncertain', done['next_action'])
        self.assertEqual(1, len(read(self.root / FLOW)['requests']))
        with self.assertRaisesRegex(ValueError, '仅首次待核实'):
            supplement(self.root, 'head', config)

    def test_changed_board_or_candidate_cannot_be_ranked(self):
        ranking = self.receive(self.baseline())
        board = ranking['ranking_board']
        (self.root / board['file']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '拼图已变化'):
            api.rank(self.root, 'head', {'preferred_call': board['call_ids'][0], 'reason': 'Synthetic reason.', 'board_sha256': board['sha256']})

    def test_complete_delivery_is_one_scripted_package_and_explicit_visual_check(self):
        for role in api.ROLES:
            self.finish_stage(role)
        result = delivery.deliver(self.root, {})
        self.assertEqual('inspect_delivery', result['next_action'])
        self.assertIsNone(read(self.root / FLOW)['delivery']['inspection'])
        self.assertFalse((self.sources / '测试角色_白外套测试造型').exists())
        inspection = dict(result['inspection_template'], result='pass', viewed_image_ids=['overview', 'relationships'],
                          layout_observation='Synthetic layout observation; no visual truth.',
                          relationships_observation='Synthetic cross-view observation; no visual truth.')
        done = delivery.deliver(self.root, {'inspection': inspection})
        self.assertEqual('done', done['next_action'])
        self.assertEqual(12, done['candidate_count'])
        destination = Path(done['destination'])
        self.assertFalse((destination / '候选').exists())
        self.assertEqual(4, len(list(destination.glob('*.png'))))
        self.assertEqual('complete', read(destination / RECORD)['delivery']['state'])
        self.assertEqual(done, delivery.deliver(self.root, {'inspection': inspection}))

    def test_current_record_validates_against_published_schema(self):
        import record_contract
        self.finish_stage()
        schema = read(Path(api.__file__).resolve().parents[1] / 'references/production-record.schema.json')
        record = read(self.root / RECORD)
        record_contract.validate(record, schema)
        record['invented_field'] = 'reject me'
        with self.assertRaisesRegex(ValueError, 'unknown field'):
            record_contract.validate(record, schema)


    def test_receive_prebuilds_reviewer_assets_and_simple_check_does_not_rewrite_them(self):
        simple = self.receive(api.prepare(self.root, 'head', self.config()))
        call = workflow.load_all(self.root)[2]['stages']['head']['attempts'][0]
        packet = read(self.root / call['packet_file'])
        template = (self.root / call['packet_file']).with_name('head-' + call['id'] + '-report-template.json')
        self.assertTrue(template.is_file())
        draft = read(template)
        self.assertEqual('subagent', draft['reviewer']['mode'])
        self.assertEqual([], draft['viewed_evidence_ids'])
        self.assertTrue(all(c['result'] == 'pending' for c in draft['checks']))
        before = {e['file']: digest(self.root / e['file']) for e in packet['evidence']}
        template_hash = digest(template)
        review = self.simple(simple)
        self.assertEqual(template_hash, digest(template))
        self.assertEqual(before, {f: digest(self.root / f) for f in before})
        self.assertIn('reviewer_handoff', review)

    def test_unconfirmed_background_points_do_not_require_another_agent_round(self):
        simple = self.receive(api.prepare(self.root, 'head', self.config()))
        handoff = self.simple(simple)
        report = self.report(handoff)
        item = next(c for c in report['checks'] if c['id'] == 'background')
        item['empty_background_samples'].append({'xy': [15, 20], 'confirmed_empty': False})
        result = api.review(self.root, 'head', report)
        self.assertEqual('generate_parallel', result['next_action'])
        state_item = next(c for c in workflow.load_all(self.root)[1]['stages']['head']['checks'] if c['id'] == 'background')
        self.assertEqual(1, len(state_item['empty_background_samples']))
        frozen = read(self.root / RECORD)['stages'][0]['calls'][0]['visual_review']['data']
        self.assertFalse(next(c for c in frozen['checks'] if c['id'] == 'background')['empty_background_samples'][1]['confirmed_empty'])

    def rear_project(self):
        Image.new('RGB', (36, 48), (70, 80, 90)).save(self.sources / 'rear.png')
        self.root = self.base / 'rear-production'
        started = api.start(self.root, self.sources)
        # File order is rear.png, reference.png.
        config = self.config()
        config['materials'] = [
            {'source_id': s['source_id'], 'views': ['back' if Path(s['original_file']).name == 'rear.png' else 'front'],
             'observation': 'Synthetic actual angle label for routing tests.', 'quality': 'Synthetic readable angle.',
             'selection_reason': 'Synthetic complementary view.', 'decision': 'adopt',
             'uses': [{'id': 'identity_' + s['source_id'], 'kind': 'identity', 'stages': ['head'], 'target': 'Synthetic design.'}]}
            for s in started['sources']]
        extras = self.receive(self.baseline(config=config))
        board = extras['ranking_board']
        api.rank(self.root, 'head', {'preferred_call': board['call_ids'][0], 'reason': 'Synthetic baseline preference.', 'board_sha256': board['sha256']})
        self.finish_stage('front')

    def test_rear_original_joins_separate_front_material_and_geometry_inputs(self):
        self.rear_project()
        config = {'prompt': {'operation': 'generate', 'identity': 'Synthetic rear identity.',
                  'references': [{'stage': 'front', 'role': 'Deliberately overbroad colored front role; script must narrow it.'}],
                  'critical_constraints': [{'id': 'surface', 'kind': 'material', 'source_indices': [1], 'statement': 'Synthetic surface response.'}]}}
        request = api.prepare(self.root, 'back', config)
        args = request['requests'][0]['arguments']
        self.assertEqual(3, len(args['referenced_image_paths']))
        with Image.open(args['referenced_image_paths'][1]) as mask:
            self.assertEqual('RGB', mask.mode)
            self.assertTrue(set(mask.getdata()) <= {(0, 0, 0), (255, 255, 255)})
        front = workflow.load_all(self.root)[1]['stages']['front']
        self.assertEqual(front['sha256'], digest(Path(args['referenced_image_paths'][0])))
        with Image.open(self.root / front['image']) as image, Image.open(args['referenced_image_paths'][1]) as mask:
            expected = image.convert('RGBA').getchannel('A').point(lambda a: 0 if a > 4 else 255).transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            self.assertEqual(expected.tobytes(), mask.getchannel('R').tobytes())
        response = self.receive(request, 'back')
        call = workflow.load_all(self.root)[2]['stages']['back']['attempts'][0]
        packet = read(self.root / call['packet_file'])
        self.assertEqual({'front'}, set(packet['dependencies']))
        self.assertNotIn('upstream_front', [e['id'] for e in packet['evidence']])
        self.assertNotIn('source_upstream_front', [e['id'] for e in packet['evidence']])
        goal = next(g for g in packet['checks'] if g['id'] == 'design_rear_reference')
        self.assertEqual(['s001'], goal['source_ids'])
        self.assertIn('source_upstream_front_silhouette', [e['id'] for e in packet['evidence']])
        self.assertIn('source_upstream_front_material', [e['id'] for e in packet['evidence']])
        self.assertEqual('self-check', response['next_action'])

    def test_front_original_can_supply_material_but_not_rear_design(self):
        self.rear_project()
        config = self.config('back')
        config['prompt']['references'] = [{'source_id': 's002', 'role': 'Synthetic front original.'}]
        config['prompt']['critical_constraints'][0]['source_indices'] = [1]
        with self.assertRaisesRegex(ValueError, '正面原图仅能支持材料'):
            api.prepare(self.root, 'back', config)
        config['prompt']['references'] = [{'stage': 'front', 'role': 'Synthetic front.', 'guide': 'back_silhouette'}]
        config['prompt']['critical_constraints'][0]['kind'] = 'layer'
        with self.assertRaisesRegex(ValueError, '剪影只能支持外形比例'):
            api.prepare(self.root, 'back', config)

    def test_rear_only_request_automatically_receives_both_front_roles(self):
        self.rear_project()
        config = self.config('back')
        config['prompt']['references'] = [{'source_id': 's001', 'role': 'Synthetic rear source.'}]
        config['prompt']['critical_constraints'][0]['source_indices'] = [1]
        self.receive(api.prepare(self.root, 'back', config), 'back')
        call = workflow.load_all(self.root)[2]['stages']['back']['attempts'][0]
        packet = read(self.root / call['packet_file'])
        self.assertEqual({'front'}, set(packet['dependencies']))
        self.assertFalse(any(e.get('stage') == 'front' for e in packet['evidence']))
        self.assertTrue({'source_upstream_front_material', 'source_upstream_front_silhouette'} <= {e['id'] for e in packet['evidence']})

    def test_rear_file_alias_cannot_claim_front_is_rear_design(self):
        self.rear_project()
        config = self.config('back')
        front = workflow.load_all(self.root)[1]['stages']['front']
        config['prompt']['references'] = [{'file': front['image'], 'role': 'Synthetic colored front alias.'}]
        config['prompt']['critical_constraints'][0]['source_indices'] = [1]
        with self.assertRaisesRegex(ValueError, '正面原图仅能支持材料'):
            api.prepare(self.root, 'back', config)

    def test_material_views_are_fixed_and_required(self):
        config = self.config()
        config['materials'][0]['views'] = ['rear-ish']
        with self.assertRaisesRegex(ValueError, 'views'):
            api.prepare(self.root, 'head', config)
        del config['materials'][0]['views']
        with self.assertRaisesRegex(ValueError, 'views'):
            api.prepare(self.root, 'head', config)

    def test_material_plan_routes_native_detail_to_all_body_stages_and_targets(self):
        Image.new('RGB', (40, 30), (181, 160, 150)).save(self.sources / 'stockings.png')
        self.root = self.base / 'planned-material-production'
        api.start(self.root, self.sources)
        config = self.config()
        config['materials'].append({'source_id': 's002', 'views': ['detail'],
            'observation': 'Synthetic stocking macro with connected circular motifs.',
            'quality': 'Synthetic readable spacing; finest weave unknown.', 'selection_reason': 'Synthetic unique material macro.',
            'decision': 'partial', 'uses': [{'id': 'stocking_surface', 'kind': 'material',
                'stages': ['front', 'back', 'left'], 'target': 'Synthetic translucent fine weave and low-contrast motif scale.',
                'crop': [3, 4, 35, 26]}]})
        ranked = self.receive(self.baseline(config=config))
        board = ranked['ranking_board']
        api.rank(self.root, 'head', {'preferred_call': board['call_ids'][0], 'reason': 'Synthetic preference.', 'board_sha256': board['sha256']})
        for role in ('front', 'back', 'left'):
            ranked = self.receive(self.baseline(role), role)
            board = ranked['ranking_board']
            api.rank(self.root, role, {'preferred_call': board['call_ids'][0], 'reason': 'Synthetic preference.', 'board_sha256': board['sha256']})
            stage = next(s for s in read(self.root / RECORD)['stages'] if s['stage'] == role)
            coverage = stage['reference_coverage']
            self.assertEqual(1, len(coverage))
            self.assertEqual('s002', coverage[0]['source_id'])
            self.assertEqual([3, 4, 35, 26], coverage[0]['crop'])
            goal = next(g for g in stage['targets'] if g['id'] == 'design_planned_stocking_surface')
            key = coverage[0]['reference_key']
            self.assertEqual([key], goal['source_ids'])
            referenced = self.root / stage['target_sources'][key]['file']
            self.assertEqual(digest(referenced), stage['recipe']['inputs'][coverage[0]['reference_index'] - 1]['sha256'])
            sidecar = next(read(p) for p in self.root.glob('制作记录/参考辅助/*.png.json')
                           if read(p)['output_sha256'] == digest(referenced))
            self.assertEqual([3, 4, 35, 26], sidecar['rows'][0]['panels'][0]['crop'])
            self.assertEqual([1, 1], sidecar['rows'][0]['panels'][0]['actual_scale_xy'])

    def test_source_analysis_requires_quality_and_selection_reason_before_generation(self):
        for field in ('quality', 'selection_reason'):
            config = self.config()
            del config['materials'][0][field]
            with self.assertRaisesRegex(ValueError, field):
                api.prepare(self.root, 'head', config)
        self.assertEqual({}, read(self.root / FLOW)['requests'])

    def test_same_native_region_is_uploaded_once_for_multiple_planned_facts(self):
        config = self.config()
        use = config['materials'][0]['uses'][0]
        use['crop'] = [0, 0, 20, 25]
        config['materials'][0]['uses'].append(dict(use, id='cloth_surface', kind='material', target='Synthetic cloth surface.'))
        config['prompt'].update(references=[], critical_constraints=[])
        request = api.prepare(self.root, 'head', config)
        self.assertEqual(1, len(request['requests'][0]['arguments']['referenced_image_paths']))
        self.assertEqual([1, 1], [r['reference_index'] for r in request['reference_coverage']])

    def test_record_contract_rejects_ambiguous_routes_and_old_flow_without_mutation(self):
        import record_contract
        api.prepare(self.root, 'head', self.config())
        record = read(self.root / RECORD)
        record['material_analysis'][0]['uses'][0]['crop'] = [0, 0, 1]
        with self.assertRaisesRegex(ValueError, 'too few items'):
            record_contract.validate(record)
        record['material_analysis'][0]['uses'][0].pop('crop')
        record['material_analysis'][0]['uses'][0]['stages'] = ['head', 'head']
        with self.assertRaisesRegex(ValueError, 'duplicate items'):
            record_contract.validate(record)
        old = read(self.root / FLOW)
        old['schema_version'] = 2
        workflow.write(self.root / FLOW, old)
        before = digest(self.root / FLOW)
        with self.assertRaisesRegex(ValueError, '仅处理当前制作记录格式3'):
            api.advance(self.root, 'head')
        self.assertEqual(before, digest(self.root / FLOW))

    def test_front_material_guide_cannot_be_overridden_to_rear_design(self):
        self.rear_project()
        config = self.config('back')
        config['prompt']['references'] = [{'source_id': 's002', 'guide': 'rear_design', 'role': 'Misleading role.'}]
        config['prompt']['critical_constraints'][0].update(kind='material', source_indices=[1])
        result = api.prepare(self.root, 'back', config)
        self.assertEqual(4, len(result['requests'][0]['arguments']['referenced_image_paths']))
        spec_path = next(self.root.glob('制作记录/阶段输入/back-*-resolved.json'))
        self.assertEqual('front_material', read(spec_path)['references'][0]['guide'])

    def test_back_capacity_packs_front_pair_and_keeps_original_review_sources(self):
        self.rear_project()
        refs = [{'stage': 'front', 'role': 'Synthetic material.'}, {'source_id': 's001', 'role': 'Synthetic rear.'}]
        for i in range(3):
            path = self.base / f'auxiliary-{i}.png'
            Image.new('RGB', (45, 70), (20 * i, 100, 140)).save(path)
            refs.append({'file': str(path), 'role': f'Synthetic required detail {i}.'})
        config = {'prompt': {'operation': 'generate', 'references': refs, 'critical_constraints': [
            {'id': 'surface', 'kind': 'material', 'source_indices': [1], 'statement': 'Synthetic surface.'},
            {'id': 'rear', 'kind': 'spatial', 'source_indices': [2], 'statement': 'Synthetic actual rear relation.'}]}}
        request = api.prepare(self.root, 'back', config)
        self.assertEqual(5, len(request['requests'][0]['arguments']['referenced_image_paths']))
        packing = request['reference_packing']
        self.assertEqual(6, packing['before_count'])
        self.assertEqual({'upstream_front_material', 'upstream_front_silhouette'},
                         {r['source_key'] for r in packing['collages'][0]['regions']})
        self.receive(request, 'back')
        stage = next(s for s in read(self.root / RECORD)['stages'] if s['stage'] == 'back')
        self.assertEqual(packing, stage['reference_packing'])
        self.assertTrue({'upstream_front_material', 'upstream_front_silhouette', 's001'} <= set(stage['target_sources']))
        packet = read(self.root / workflow.load_all(self.root)[2]['stages']['back']['attempts'][0]['packet_file'])
        self.assertTrue({'source_upstream_front_material', 'source_upstream_front_silhouette'} <= {e['id'] for e in packet['evidence']})

    def test_declared_clear_replacement_removes_blurry_duplicate_before_packing(self):
        Image.new('RGB', (18, 24), (50, 70, 90)).save(self.sources / 'blur.png')
        self.root = self.base / 'replacement-production'
        api.start(self.root, self.sources)
        config = self.config()
        clear = dict(config['materials'][0], source_id='s002')
        blurry = {**clear, 'source_id': 's001', 'decision': 'exclude', 'uses': [],
                  'covered_by': {'source_id': 's002', 'reason': 'Synthetic clearer source covers all relevant facts.'}}
        config['materials'] = [blurry, clear]
        config['prompt']['references'] = [{'source_id': 's001', 'role': 'Identity.'}, {'source_id': 's002', 'role': 'Surface.'}]
        config['max_reference_images'] = 1
        request = api.prepare(self.root, 'head', config)
        self.assertEqual(1, len(request['requests'][0]['arguments']['referenced_image_paths']))
        self.assertEqual([], request['reference_packing']['collages'])
        self.assertEqual('s001', request['reference_packing']['source_replacements'][0]['removed_source'])

    def test_delivery_reuses_custom_layout_without_repeating_it_in_inspection(self):
        for role in api.ROLES:
            self.finish_stage(role)
        layout = dict(delivery.DEFAULT_LAYOUT, gap=3)
        preview = delivery.deliver(self.root, {'layout': layout})
        config_file = Path(preview['inspection_config_file'])
        self.assertTrue(config_file.is_file())
        self.assertIn(str(Path(api.__file__).with_suffix('.ps1')), preview['submit_command'])
        self.assertIn(str(config_file), preview['submit_command'])
        config = read(config_file)
        self.assertEqual(layout, config['layout'])
        config['inspection'].update(result='pass', viewed_image_ids=['overview', 'relationships'],
            layout_observation='Synthetic inspected layout.', relationships_observation='Synthetic inspected relationships.')
        # The documented minimal inspection still reuses the stored preview settings.
        done = delivery.deliver(self.root, {'inspection': config['inspection']})
        self.assertEqual('done', done['next_action'])
        self.assertEqual(layout, read(self.root / FLOW)['delivery']['layout'])
        with self.assertRaisesRegex(ValueError, '显式layout变化'):
            delivery.deliver(self.root, {'layout': dict(layout, gap=4), 'inspection': config['inspection']})


if __name__ == '__main__':
    unittest.main()
