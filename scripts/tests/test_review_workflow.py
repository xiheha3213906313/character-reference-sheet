"""Workflow regression tests; fixture observations are fabricated, never visual truth.

The tiny PNGs exercise file/version plumbing only. No image generator is called and
no test asserts that a model correctly assessed their visual contents.
"""
import base64
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import review_gate
import review_v2 as core
import review_workflow as workflow


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='review-workflow-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'fixture-source.png'
        self.image = self.root / 'fixture-output.png'
        self.make_png(self.source, (10, 20, 30, 255))
        self.make_png(self.image, (40, 50, 60, 255))
        self.prompt = self.root / 'fixture-prompt.txt'
        self.prompt.write_text('Fabricated fixture prompt; never submitted to an image tool.', encoding='utf-8')
        self.call_record = self.root / 'fixture-call.txt'
        self.call_record.write_text('Fabricated test call receipt; not a real generation.', encoding='utf-8')
        self.recipe = {'tool': 'fixture_only', 'parameters': {'transparent_background': True},
                       'prompt_file': str(self.prompt),
                       'inputs': [{'file': str(self.source), 'purpose': 'fixture reference'}]}
        self.reviewer = {'mode': 'subagent', 'model': 'inherited', 'agent_id': 'fabricated-test-agent'}
        self.values = workflow.load_all(self.root)
        self.save()

    @staticmethod
    def make_png(path, color):
        image = Image.new('RGBA', (12, 12), (0, 0, 0, 0))
        for x in range(3, 9):
            for y in range(3, 9):
                image.putpixel((x, y), color)
        image.save(path)

    @staticmethod
    def goals(role):
        groups = ['identity', 'design', 'native_quality', 'reduced_quality', 'background']
        result = [{'id': group, 'group': group, 'target': 'Fabricated target for ' + group,
                   'source_ids': ['primary']} for group in groups]
        for aspect in ['color_style', 'lighting', 'framing', 'head_pose', 'gaze', 'expression']:
            result.append({'id': 'look_' + aspect, 'group': 'look', 'aspect': aspect,
                           'target': 'Fabricated target for ' + aspect, 'source_ids': ['primary']})
        if role in ('back', 'left'):
            result.append({'id': 'spatial', 'group': 'spatial', 'target': 'Fabricated spatial target',
                           'source_ids': ['primary']})
        return result

    def save(self):
        workflow.save_all(self.root, self.values)

    def prepare(self, role='head', **overrides):
        config = {'sources': {'primary': {'file': str(self.source)}},
                  'checks': {role: self.goals(role)}, 'recipe': copy.deepcopy(self.recipe)}
        config.update(overrides)
        workflow.prepare(self.root, role, config, self.values)
        self.save()

    def register(self, identifier, role='head', error=None, recipe=None):
        config = {'id': identifier, 'recipe': copy.deepcopy(recipe or self.recipe),
                  'evidence_file': str(self.call_record)}
        if error is None:
            config['output'] = str(self.image)
        else:
            config['error'] = error
        workflow.register(self.root, role, config, self.values)
        self.save()

    def call(self, identifier, role='head', include_processing=False):
        choice = self.values[2]['stages'][role]
        calls = choice['attempts'] + (choice.get('processing', []) if include_processing else [])
        return next(call for call in calls if call['id'] == identifier)

    def report(self, identifier, role='head', result='pass', round_number=0,
               reviewer=None, partial=False, issue_key=None, processing=False):
        call = self.call(identifier, role, include_processing=processing)
        if not processing and not call.get('self_check'):
            self.simple_check(identifier, role)
        packet = core.read(self.root / call['packet_file'])
        checks = []
        for goal in self.values[0]['checks'][role]:
            group = goal['group']
            evidence_ids = {'native_quality': ['native_001'], 'reduced_quality': ['reduced'],
                            'background': ['light', 'dark']}.get(group, ['whole', 'source_primary'])
            item = {'id': goal['id'], 'result': result,
                    'reference_observation': 'Fabricated reference observation for a plumbing test.',
                    'candidate_observation': 'Fabricated candidate observation for a plumbing test.',
                    'comparison_basis': 'Fabricated explicit reference comparison; no visual judgment.',
                    'evidence_ids': evidence_ids}
            if group == 'background':
                item['empty_background_samples'] = [{'xy': [0, 0], 'confirmed_empty': True}]
            if issue_key is not None:
                item['issue_key'] = issue_key
            checks.append(item)
        if partial:
            checks = checks[:1]
        return {'kind': 'visual', 'call_id': identifier,
                'packet_sha256': core.digest(self.root / call['packet_file']),
                'reviewer': copy.deepcopy(reviewer or (self.reviewer if role == 'head' else {**self.reviewer, 'agent_id': self.reviewer['agent_id'] + '-' + role})),
                'viewed_evidence_ids': [artifact['id'] for artifact in packet['evidence']],
                'checks': checks, 'supplement_round': round_number,
                'quality_observations': {'hair': 'Fabricated hair detail observation.', 'face': 'Fabricated face surface observation.'},
                'background_policy': {'mode': 'transparent', 'alpha_source': 'model'}}

    def simple_check(self, identifier, role='head', result='pass', issue_key='fixture_issue'):
        call = self.call(identifier, role)
        workflow.record_review(self.root, role, {
            'kind': 'self-check', 'call_id': identifier, 'packet_sha256': call['packet_sha256'],
            'reviewer': {'mode': 'self', 'model': 'inherited'}, 'result': result, 'issue_key': issue_key,
            'observation': 'Fabricated simple self-check; no model visual judgment.',
            'framing_observation': 'Fabricated framing observation; not a visual judgment.',
            'viewed_evidence_ids': ['compare_001'],
            'quality_observations': {'hair': 'Fabricated hair observation; not a visual judgment.',
                                     'face': 'Fabricated face observation; not a visual judgment.'}}, self.values)
        self.save()

    def test_reviewer_handoff_is_unavailable_until_maker_views_and_passes(self):
        self.prepare()
        self.register('first')
        status = workflow.status(self.root, 'head', self.values)
        self.assertFalse(status['reviewer_dispatch_ready'])
        self.assertNotIn('reviewer_handoff', status)
        self.assertTrue((self.root / status['self_check_template_file']).is_file())
        call = self.call('first')
        visual_template = (self.root / call['packet_file']).with_name('head-first-report-template.json')
        self.assertTrue(visual_template.exists())
        template_before = core.digest(visual_template)
        with self.assertRaisesRegex(ValueError, '先简单自评'):
            workflow.review_dispatch(self.root, call)
        self.simple_check('first')
        self.assertEqual(template_before, core.digest(visual_template))
        status = workflow.status(self.root, 'head', self.values)
        self.assertTrue(status['reviewer_dispatch_ready'])
        handoff = status['reviewer_handoff']
        self.assertTrue((self.root / handoff['report_template_file']).is_file())
        self.assertNotIn('self_check', handoff)
        self.assertNotIn('observation', handoff)
        self.assertEqual('visual', core.read(self.root / handoff['report_template_file'])['kind'])

    def test_maker_failure_never_releases_reviewer_handoff(self):
        self.prepare()
        self.register('first')
        self.simple_check('first', result='fail')
        status = workflow.status(self.root, 'head', self.values)
        self.assertFalse(status['reviewer_dispatch_ready'])
        self.assertNotIn('reviewer_handoff', status)
        self.assertEqual('revise_input_or_repair', status['next_action'])

    def test_self_check_only_needs_actual_comparison_and_short_observation(self):
        self.prepare()
        self.register('first')
        call = self.call('first')
        template = core.read((self.root / call['packet_file']).with_name('head-first-self-check-template.json'))
        template.update(result='pass', observation='Fabricated observation.',
                        quality_observations={'hair': 'Fabricated hair.', 'face': 'Fabricated face.'})
        cases = [({}, '拼图'), ({'viewed_evidence_ids': ['whole', 'native_001']}, '拼图'),
                 ({'viewed_evidence_ids': ['unknown']}, '拼图')]
        for fields, error in cases:
            report = dict(template, **fields)
            with self.subTest(fields=fields), self.assertRaisesRegex(ValueError, error):
                workflow.record_review(self.root, 'head', report, self.values)
        self.assertNotIn('self_check', call)
        template.update(viewed_evidence_ids=['compare_001'])
        workflow.record_review(self.root, 'head', template, self.values)
        self.assertEqual('pass', call['self_check']['result'])

    def test_handoff_closes_when_candidate_evidence_or_target_changes(self):
        for changed in ('image', 'evidence', 'target'):
            with self.subTest(changed=changed):
                self.setUp()
                self.prepare()
                self.register('first')
                self.simple_check('first')
                call = self.call('first')
                if changed == 'image':
                    self.make_png(self.root / call['output']['file'], (99, 30, 10, 255))
                elif changed == 'evidence':
                    packet = core.read(self.root / call['packet_file'])
                    whole = next(a for a in packet['evidence'] if a['id'] == 'whole')
                    self.make_png(self.root / whole['file'], (99, 30, 10, 255))
                else:
                    self.values[0]['checks']['head'][0]['target'] += ' changed'
                status = workflow.status(self.root, 'head', self.values)
                self.assertFalse(status['reviewer_dispatch_ready'])
                self.assertNotIn('reviewer_handoff', status)

    def test_selected_extra_requires_its_own_maker_handoff(self):
        self.approve_first()
        self.register('extra1')
        self.register('extra2')
        self.rank('extra1')
        status = workflow.status(self.root, 'head', self.values)
        self.assertFalse(status['reviewer_dispatch_ready'])
        self.simple_check('extra1')
        status = workflow.status(self.root, 'head', self.values)
        self.assertTrue(status['reviewer_dispatch_ready'])
        self.assertEqual('review_selected', status['next_action'])

    def review(self, identifier, role='head', **overrides):
        report = self.report(identifier, role, **overrides)
        workflow.record_review(self.root, role, report, self.values)
        self.save()

    def rank(self, preferred='first', role='head'):
        choice = self.values[2]['stages'][role]
        identifiers = [call['id'] for call in choice['attempts']
                       if (call['id'] == choice['baseline_call'] or call['phase'] == 'extra') and not call.get('error')]
        workflow.record_review(self.root, role, {'kind': 'selection', 'preferred_call': preferred,
                               'selection_reason': 'Fabricated preference for test control flow.',
                               'reviewer': {'mode': 'self', 'model': 'inherited'}, 'viewed_call_ids': identifiers}, self.values)
        self.save()

    def approve_first(self, role='head', **prepare_overrides):
        self.prepare(role, **prepare_overrides)
        self.register('first', role)
        self.review('first', role)

    def complete(self, role='head'):
        self.approve_first(role)
        self.register('extra1', role)
        self.register('extra2', role)
        self.rank(role=role)

    def gate(self, role='head', baseline=False):
        return review_gate.check(self.values[1], self.root, stage=role, baseline=baseline)

    def test_first_requires_full_review_and_exactly_two_extra_calls(self):
        self.prepare()
        self.assertEqual('generate_first', workflow.status(self.root, 'head', self.values)['next_action'])
        self.register('first')
        with self.assertRaisesRegex(ValueError, '首张须先'):
            self.register('premature')
        self.review('first', partial=True)
        self.assertEqual('supplement_evidence', workflow.status(self.root, 'head', self.values)['next_action'])
        with self.assertRaisesRegex(ValueError, '待核实不能直接重生成'):
            self.register('unjustified_retry')
        self.review('first', round_number=1)
        self.assertTrue(self.gate(baseline=True)['recorded_approval_valid'])
        self.assertFalse(self.gate()['recorded_approval_valid'])
        self.register('extra1')
        self.assertEqual(1, workflow.status(self.root, 'head', self.values)['remaining_calls'])
        with self.assertRaisesRegex(ValueError, '先完成固定追加'):
            self.rank()
        self.register('extra2')
        with self.assertRaisesRegex(ValueError, '固定追加次数已用完'):
            self.register('unjustified_extra3')
        self.rank()
        result = self.gate()
        self.assertTrue(result['recorded_approval_valid'], result['errors'])
        self.assertTrue(result['downstream_ready'])

    def test_fresh_root_can_prepare_without_preexisting_workflow_json(self):
        fresh = self.root / 'fresh'
        fresh.mkdir()
        values = workflow.load_all(fresh)
        config = {'sources': {'primary': {'file': str(self.source)}},
                  'checks': {'head': self.goals('head')}, 'recipe': self.recipe}
        workflow.prepare(fresh, 'head', config, values)
        workflow.save_all(fresh, values)
        self.assertEqual('generate_first', workflow.status(fresh, 'head', values)['next_action'])

    def test_unselected_extras_have_no_checklists_or_approval_requirement(self):
        self.complete()
        choice = self.values[2]['stages']['head']
        self.assertEqual({'first'}, set(choice['reviews']))
        self.assertTrue(all('checks' not in call and 'packet_file' not in call
                            for call in choice['attempts'] if call['phase'] == 'extra'))
        self.assertEqual('first', choice['selected_call'])
        self.assertTrue(self.gate()['recorded_approval_valid'])
        with self.assertRaisesRegex(ValueError, '落选追加候选'):
            workflow.record_review(self.root, 'head', {'call_id': 'extra1', 'reviewer': self.reviewer}, self.values)

    def test_failed_selected_extra_falls_back_without_new_generation(self):
        self.approve_first()
        self.register('extra1')
        self.register('extra2')
        self.rank('extra1')
        self.assertEqual('self_check_selected', workflow.status(self.root, 'head', self.values)['next_action'])
        self.review('extra1', result='fail', partial=True, issue_key='fabricated_identity_drift')
        choice = self.values[2]['stages']['head']
        self.assertEqual('first', choice['selected_call'])
        self.assertEqual(3, len(choice['attempts']))
        self.assertEqual('continue', workflow.status(self.root, 'head', self.values)['next_action'])
        self.assertTrue(self.gate()['recorded_approval_valid'])
        with self.assertRaisesRegex(ValueError, '固定追加次数已用完'):
            self.register('replacement')

    def test_pending_selected_extra_gets_only_one_supplement_then_falls_back(self):
        self.approve_first()
        self.register('extra1')
        self.register('extra2')
        self.rank('extra1')
        self.review('extra1', result='pending', partial=True)
        self.assertIsNone(self.values[2]['stages']['head']['selected_call'])
        self.assertEqual('supplement_evidence', workflow.status(self.root, 'head', self.values)['next_action'])
        self.review('extra1', result='pending', partial=True, round_number=1)
        self.assertEqual('first', self.values[2]['stages']['head']['selected_call'])
        self.assertTrue(self.gate()['recorded_approval_valid'])
        with self.assertRaisesRegex(ValueError, '仅补证一次'):
            self.review('extra1', result='pending', partial=True, round_number=1)

    def test_pending_first_blocks_regeneration_and_has_no_false_approval(self):
        self.prepare()
        self.register('first')
        self.review('first', result='pending', partial=True)
        self.review('first', result='pending', partial=True, round_number=1)
        self.assertEqual('blocked_uncertain', workflow.status(self.root, 'head', self.values)['next_action'])
        self.assertIsNone(self.values[2]['stages']['head']['baseline_call'])
        self.assertFalse(self.gate(baseline=True)['recorded_approval_valid'])
        with self.assertRaisesRegex(ValueError, '待核实不能直接重生成'):
            self.register('retry')

    def test_selected_extra_must_pass_full_review_before_downstream(self):
        self.approve_first()
        self.register('extra1')
        self.register('extra2')
        self.rank('extra1')
        self.assertFalse(self.gate()['downstream_ready'])
        self.review('extra1')
        self.assertEqual('extra1', self.values[2]['stages']['head']['selected_call'])
        self.assertTrue(self.gate()['downstream_ready'])

    def test_reviewer_modes_enforce_real_reason_and_user_model(self):
        default = {'mode': 'prefer_subagent', 'model': None}
        for code in ('unsupported', 'startup_failed'):
            with self.subTest(code=code):
                core.reviewer_valid({'mode': 'self', 'model': 'inherited', 'reason_code': code,
                                     'reason': 'Fabricated environment limitation.'}, default)
        user_disabled = {'mode': 'self', 'user_override_reason': 'User explicitly disabled subagents.'}
        core.reviewer_valid({'mode': 'self', 'model': 'inherited', 'reason_code': 'user_disabled',
                             'reason': 'User explicitly disabled subagents.'}, user_disabled)
        specified = {'mode': 'prefer_subagent', 'model': 'user-specified-test-model'}
        core.reviewer_valid({'mode': 'subagent', 'model': 'user-specified-test-model', 'agent_id': 'test'}, specified)
        for reviewer, policy in [
                ({'mode': 'self', 'model': 'inherited', 'reason_code': 'user_disabled', 'reason': 'invented'}, default),
                ({'mode': 'self', 'model': 'inherited', 'reason_code': 'unsupported'}, default),
                (self.reviewer, user_disabled), (self.reviewer, specified),
                ({'mode': 'subagent', 'model': 'inherited'}, default)]:
            with self.subTest(reviewer=reviewer, policy=policy), self.assertRaises(ValueError):
                core.reviewer_valid(reviewer, policy)

    def test_self_review_modes_are_recorded_by_full_flow(self):
        self.reviewer = {'mode': 'self', 'model': 'inherited', 'reason_code': 'user_disabled',
                         'reason': 'User explicitly disabled subagents for fixture.'}
        policy = {'mode': 'self', 'user_override_reason': 'User explicitly disabled subagents for fixture.'}
        self.approve_first(reviewer_policy=policy)
        self.register('extra1')
        self.register('extra2')
        self.rank()
        result = self.gate()
        self.assertTrue(result['recorded_approval_valid'], result['errors'])
        self.assertEqual('self', result['checked'][0]['reviewer_mode'])

    def test_tool_failure_consumes_extra_slot_and_is_honestly_limited(self):
        self.approve_first()
        self.register('extra1', error={'code': 'fixture_tool_error', 'message': 'Fabricated transport failure.'})
        self.register('extra2')
        self.rank()
        choice = self.values[2]['stages']['head']
        self.assertEqual(['first', 'extra2'], choice['ranking']['viewed_call_ids'])
        self.assertNotIn('output', self.call('extra1'))
        result = self.gate()
        self.assertTrue(result['recorded_approval_valid'], result['errors'])
        self.assertEqual('limited_tool_error', result['checked'][0]['selection_status'])
        with self.assertRaisesRegex(ValueError, '固定追加次数已用完'):
            self.register('replacement')

    def test_recipe_input_order_parameters_and_prompt_cannot_drift(self):
        secondary = self.root / 'fixture-secondary.png'
        self.make_png(secondary, (101, 102, 103, 255))
        self.recipe['inputs'].append({'file': str(secondary), 'purpose': 'second fixture reference'})
        self.approve_first()
        changed = copy.deepcopy(self.recipe)
        changed['inputs'].reverse()
        with self.assertRaisesRegex(ValueError, '配置不一致'):
            self.register('changed_order', recipe=changed)
        changed = copy.deepcopy(self.recipe)
        changed['parameters']['transparent_background'] = False
        with self.assertRaisesRegex(ValueError, '配置不一致'):
            self.register('changed_parameters', recipe=changed)
        self.prompt.write_text('Changed fixture prompt.', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '配置不一致'):
            self.register('changed_prompt')

    def test_image_mutation_invalidates_approval_and_does_not_generate(self):
        self.complete()
        self.make_png(self.root / self.call('first')['output']['file'], (70, 80, 90, 255))
        result = self.gate()
        self.assertFalse(result['recorded_approval_valid'])
        self.assertTrue(any('图片' in message and '已变化' in message for message in result['errors']), result['errors'])
        self.assertEqual('repair_records_or_reprepare', workflow.status(self.root, 'head', self.values)['next_action'])

    def test_source_snapshot_mutation_invalidates_approval(self):
        self.complete()
        snap = self.root / self.values[0]['sources']['primary']['file']
        self.make_png(snap, (90, 80, 70, 255))
        result = self.gate()
        self.assertFalse(result['recorded_approval_valid'])
        self.assertTrue(any('来源' in message for message in result['errors']), result['errors'])

    def test_target_mutation_invalidates_only_bound_approval(self):
        self.complete()
        self.values[0]['checks']['head'][0]['target'] = 'Changed fabricated semantic target.'
        self.save()
        result = self.gate()
        self.assertFalse(result['recorded_approval_valid'])
        self.assertTrue(any('目标版本变化' in message for message in result['errors']), result['errors'])
        self.assertEqual('repair_records_or_reprepare', workflow.status(self.root, 'head', self.values)['next_action'])

    def test_evidence_mutation_cannot_be_repaired_by_metadata_alone(self):
        self.complete()
        packet_path = self.root / self.call('first')['packet_file']
        packet = core.read(packet_path)
        native = next(a for a in packet['evidence'] if a['id'] == 'native_001')
        self.make_png(self.root / native['file'], (91, 92, 93, 255))
        self.assertFalse(self.gate()['recorded_approval_valid'])
        self.values[3]['operator_note'] = 'Added unrelated record field; not visual approval.'
        self.save()
        self.assertFalse(self.gate()['recorded_approval_valid'])

    def test_original_review_receipt_mutation_invalidates_approval(self):
        self.complete()
        review = self.values[2]['stages']['head']['reviews']['first']
        receipt = self.root / review['report_file']
        report = core.read(receipt)
        report['checks'][0]['candidate_observation'] = 'Changed fabricated report after approval.'
        workflow.write(receipt, report)
        result = self.gate()
        self.assertFalse(result['recorded_approval_valid'], 'Approval must bind the retained original reviewer report.')

    def test_gate_rejects_fallback_before_pending_extra_supplement(self):
        self.approve_first()
        self.register('extra1')
        self.register('extra2')
        self.rank('extra1')
        self.review('extra1', result='pending', partial=True)
        choice = self.values[2]['stages']['head']
        self.assertIsNone(choice['selected_call'])
        # Simulate an inconsistent externally edited record, with its mechanical
        # checksum refreshed. The gate must still enforce the semantic sequence.
        choice['selected_call'] = 'first'
        self.values[1]['stages']['head']['selection_sha256'] = core.selection_binding(choice)
        self.save()
        result = self.gate()
        self.assertFalse(result['recorded_approval_valid'], 'A pending winner requires its one supplement before fallback.')

    def test_add_unrelated_stage_records_does_not_invalidate_head(self):
        self.complete()
        binding = self.values[1]['stages']['head']['binding_sha256']
        self.prepare('front')
        self.register('front_first', 'front')
        self.assertTrue(self.gate()['recorded_approval_valid'])
        self.assertEqual(binding, self.values[1]['stages']['head']['binding_sha256'])
        self.assertFalse(self.gate('front')['recorded_approval_valid'])

    def test_mutating_source_used_only_by_other_stage_keeps_head_valid(self):
        self.complete()
        secondary = self.root / 'unrelated-source.png'
        self.make_png(secondary, (150, 151, 152, 255))
        front_goals = self.goals('front')
        for goal in front_goals:
            goal['source_ids'] = ['secondary']
        self.prepare('front', sources={'secondary': {'file': str(secondary)}}, checks={'front': front_goals})
        self.assertTrue(self.gate()['recorded_approval_valid'])
        self.make_png(self.root / self.values[0]['sources']['secondary']['file'], (160, 161, 162, 255))
        result = self.gate()
        self.assertTrue(result['recorded_approval_valid'], result['errors'])

    def test_upstream_target_change_invalidates_downstream_binding(self):
        self.complete()
        self.complete('front')
        self.assertTrue(self.gate('front')['recorded_approval_valid'])
        self.values[0]['checks']['head'][0]['target'] += ' changed'
        self.save()
        self.assertFalse(self.gate('front')['recorded_approval_valid'])
        self.assertEqual('repair_records_or_reprepare', workflow.status(self.root, 'front', self.values)['next_action'])

    def test_same_issue_counts_across_call_ids_then_allows_one_strategy_change(self):
        self.prepare()
        for index in range(3):
            identifier = 'failed_' + str(index)
            self.register(identifier)
            self.review(identifier, result='fail', partial=True, issue_key='same_identity_issue')
        failure = self.values[3]['failures']['head/same_identity_issue']
        self.assertEqual(3, failure['count'])
        self.assertEqual('stop_and_diagnose', workflow.status(self.root, 'head', self.values)['next_action'])
        with self.assertRaisesRegex(ValueError, '停止条件'):
            self.register('too_many')
        change = {'issue_key': 'same_identity_issue', 'diagnosis': 'Fabricated repeated failure diagnosis.',
                  'change': 'Fabricated change to strategy.'}
        self.prompt.write_text('Changed fixture prompt after repeated issue diagnosis.', encoding='utf-8')
        self.prepare(strategy_change=change)
        self.register('new_strategy')
        self.review('new_strategy', result='fail', partial=True, issue_key='same_identity_issue')
        self.assertEqual(4, failure['count'])
        self.assertEqual('select_best_at_limit', workflow.status(self.root, 'head', self.values)['next_action'])
        with self.assertRaisesRegex(ValueError, '上限'):
            self.register('again')
        self.limit_select('failed_0')
        self.assertTrue(self.gate()['downstream_ready'], self.gate()['errors'])
        self.assertFalse(self.gate()['recorded_approval_valid'])
        with self.assertRaisesRegex(ValueError, '不再重置失败计数'):
            self.prepare(strategy_change=change)

    def test_observation_requires_explicit_viewed_evidence_and_specific_content(self):
        self.prepare()
        self.register('first')
        report = self.report('first')
        report['viewed_evidence_ids'] = []
        with self.assertRaisesRegex(ValueError, '实际看过'):
            workflow.record_review(self.root, 'head', report, self.values)
        report = self.report('first')
        report['checks'][0]['candidate_observation'] = ''
        with self.assertRaisesRegex(ValueError, '实际候选观察'):
            workflow.record_review(self.root, 'head', report, self.values)
        report = self.report('first')
        report['checks'][0]['reference_observation'] = ''
        with self.assertRaisesRegex(ValueError, '原始证据观察|参考'):
            workflow.record_review(self.root, 'head', report, self.values)

    def assert_missing_reference_rejected(self, result):
        self.prepare()
        self.register('first')
        report = self.report('first', result=result, partial=True)
        report['checks'][0].pop('reference_observation')
        with self.assertRaisesRegex(ValueError, '原始证据观察|参考'):
            workflow.record_review(self.root, 'head', report, self.values)

    def test_failed_observations_require_reference_facts(self):
        self.assert_missing_reference_rejected('fail')

    def test_pending_observations_require_reference_facts(self):
        self.assert_missing_reference_rejected('pending')

    def test_status_is_read_only_in_memory_and_on_disk_including_cli(self):
        self.complete()
        before_values = copy.deepcopy(self.values)
        before_files = {path.relative_to(self.root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
                        for path in self.root.rglob('*') if path.is_file()}
        workflow.status(self.root, 'head', self.values)
        self.assertEqual(before_values, self.values)
        run = subprocess.run([sys.executable, str(SCRIPTS / 'review_workflow.py'), 'status',
                              '--root', str(self.root), '--stage', 'head'], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(0, run.returncode, run.stderr + run.stdout)
        self.assertEqual('continue', json.loads(run.stdout)['next_action'])
        after_files = {path.relative_to(self.root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
                       for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before_files, after_files)



    def test_rejects_noncurrent_records_without_rewriting(self):
        path = self.root / core.STATE
        for version in (None, 1, 3):
            with self.subTest(version=version):
                record = {'stages': {}}
                if version is not None:
                    record['schema_version'] = version
                workflow.write(path, record)
                before = path.read_bytes()
                with self.assertRaisesRegex(ValueError, '仅支持schema_version:2'):
                    review_gate.check(record, self.root, stage='head')
                with self.assertRaisesRegex(ValueError, '仅支持schema_version:2'):
                    workflow.load_all(self.root)
                run = subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPTS / 'review_gate.py'),
                                      str(path), '--root', str(self.root), '--stage', 'head'],
                                     capture_output=True, text=True, encoding='utf-8')
                self.assertNotEqual(0, run.returncode)
                self.assertFalse(json.loads(run.stdout)['recorded_approval_valid'])
                self.assertEqual(before, path.read_bytes())

    def test_removed_history_options_are_rejected(self):
        for option in (['--criteria', 'original'], ['--policy-revision', 'abcdef0']):
            with self.subTest(option=option):
                run = subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPTS / 'review_gate.py'),
                                      str(self.root / core.STATE), *option],
                                     capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(2, run.returncode)
                self.assertIn('unrecognized arguments', run.stderr)

    def test_current_visual_records_reject_wrong_purpose_and_false_empty_samples(self):
        self.complete()
        valid = self.values[1]['stages']['head']
        wrong = copy.deepcopy(valid)
        native = next(item for item in wrong['checks'] if item['id'] == 'native_quality')
        native['evidence'][0]['purpose'] = 'reference_compare'
        with self.assertRaisesRegex(ValueError, 'native_detail'):
            core.validate_visual(wrong, self.values[0], 'head', self.root, wrong['reviewer_policy'])
        wrong = copy.deepcopy(valid)
        background = next(item for item in wrong['checks'] if item['id'] == 'background')
        background['empty_background_samples'][0]['xy'] = [6, 6]
        with self.assertRaisesRegex(ValueError, '实际背景像素'):
            core.validate_visual(wrong, self.values[0], 'head', self.root, wrong['reviewer_policy'])

    def test_exact_and_near_white_backgrounds_check_actual_pixels(self):
        self.recipe['parameters']['transparent_background'] = False
        for role, mode, color in [('head', 'exact_white', 255), ('front', 'near_white_rgb_fallback', 248)]:
            canvas = Image.new('RGBA', (12, 12), (color, color, color, 255))
            for x in range(3, 9):
                for y in range(3, 9):
                    canvas.putpixel((x, y), (40, 50, 60, 255))
            canvas.save(self.image)
            self.prepare(role)
            self.register('first', role)
            report = self.report('first', role)
            report['background_policy'] = {'mode': mode}
            if mode == 'near_white_rgb_fallback':
                report['background_policy'].update(alpha_limitation='output', reason='Fabricated fixture capability limit.')
            workflow.record_review(self.root, role, report, self.values)
            self.save()
            self.register('extra1', role)
            self.register('extra2', role)
            self.rank(role=role)
            self.assertTrue(self.gate(role)['recorded_approval_valid'])
            sample = next(item for item in self.values[1]['stages'][role]['checks'] if item['id'] == 'background')['empty_background_samples'][0]
            self.assertEqual([color, color, color], sample['rgb'])

    def test_existing_review_import_needs_no_recipe_generation_log_or_selection(self):
        config = {'task_mode': 'existing_review', 'scope': 'single_stage',
                  'sources': {'primary': {'file': str(self.source)}}, 'checks': {'head': self.goals('head')}}
        workflow.prepare(self.root, 'head', config, self.values)
        self.save()
        workflow.register(self.root, 'head', {'id': 'imported', 'output': str(self.image)}, self.values)
        self.save()
        choice = self.values[2]['stages']['head']
        self.assertIsNone(choice['recipe'])
        receipt = core.read(self.root / self.call('imported')['evidence_file'])
        self.assertEqual('existing_candidate_review', receipt['operation'])
        self.assertNotIn('tool', receipt)
        self.assertIsNone(self.call('imported')['recipe_sha256'])
        self.review('imported')
        self.assertNotIn('ranking', choice)
        self.assertEqual(1, len(choice['attempts']))
        self.assertEqual('review_complete', workflow.status(self.root, 'head', self.values)['next_action'])
        (self.root / core.SELECTION).unlink()
        result = self.gate()
        self.assertTrue(result['recorded_approval_valid'], result['errors'])
        self.assertEqual('existing_review', result['task_mode'])

    def test_canvas_packaging_retains_approval_after_candidate_pngs_removed(self):
        self.approve_first()
        self.make_png(self.image, (80, 90, 100, 255))
        self.register('extra1')
        self.make_png(self.image, (110, 120, 130, 255))
        self.register('extra2')
        self.rank()
        candidates = []
        for call in self.values[2]['stages']['head']['attempts']:
            source = self.root / call['output']['file']
            raw = source.read_bytes()
            identifier = 'fixture-' + call['id']
            script_path = '网页资源/画布数据/' + source.name + '.js'
            script = self.root / script_path
            script.parent.mkdir(parents=True, exist_ok=True)
            payload = {'id': identifier, 'data': 'data:image/png;base64,' + base64.b64encode(raw).decode('ascii')}
            script.write_text('window.dispatchEvent(new CustomEvent("character-image",{detail:' +
                              json.dumps(payload) + '}));', encoding='utf-8')
            candidates.append({'id': identifier, 'view': 'head', 'dataScript': script_path,
                               'sha256': core.digest(source), 'width': 12, 'height': 12})
            source.unlink()
        workflow.write(self.root / '网页资源/画布清单.json', {'candidates': candidates})
        result = self.gate()
        self.assertTrue(result['recorded_approval_valid'], result['errors'])
        self.assertEqual('continue', workflow.status(self.root, 'head', self.values)['next_action'])
        # Corrupt one archived payload while retaining its declared digest.
        script = self.root / candidates[1]['dataScript']
        script.write_text(script.read_text(encoding='utf-8').replace('data:image/png;', 'data:image/jpeg;'), encoding='utf-8')
        self.assertFalse(self.gate()['recorded_approval_valid'])

    def check_user_call_count(self, count):
        self.approve_first(required_calls=count, user_override_reason='User explicitly limited fixture candidate count.')
        for index in range(count - 1):
            self.register('extra' + str(index))
        self.rank()
        result = self.gate()
        self.assertTrue(result['recorded_approval_valid'], result['errors'])
        self.assertEqual(count, len(self.values[2]['stages']['head']['attempts']))
        with self.assertRaisesRegex(ValueError, '固定追加次数已用完'):
            self.register('unrequested_extra')
        with self.assertRaisesRegex(ValueError, '不能临时改'):
            self.prepare(required_calls=3, user_override_reason=None)

    def test_user_can_explicitly_limit_to_one_call(self):
        self.check_user_call_count(1)

    def test_user_can_explicitly_limit_to_two_calls(self):
        self.check_user_call_count(2)

    def test_cli_prepare_register_review_persist_without_hidden_in_memory_state(self):
        def cli(command, config):
            config_file = self.root / (command + '-fixture-config.json')
            workflow.write(config_file, config)
            run = subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPTS / 'review_workflow.py'), command,
                                  '--root', str(self.root), '--stage', 'head', '--config', str(config_file)],
                                 capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(0, run.returncode, run.stderr + run.stdout)
            self.values = workflow.load_all(self.root)
            return json.loads(run.stdout)
        prepared = cli('prepare', {'sources': {'primary': {'file': str(self.source)}},
                                  'checks': {'head': self.goals('head')}, 'recipe': self.recipe})
        self.assertEqual('generate_first', prepared['next_action'])
        registered = cli('register', {'id': 'first', 'recipe': self.recipe,
                                     'output': str(self.image), 'evidence_file': str(self.call_record)})
        self.assertEqual('self_check_first', registered['next_action'])
        reviewed = cli('record-review', self.report('first'))
        self.assertEqual('generate_extra', reviewed['next_action'])
        self.assertTrue(self.gate(baseline=True)['recorded_approval_valid'])
        self.assertFalse(self.gate()['recorded_approval_valid'])

    def check_ranking_is_immutable(self, result):
        self.approve_first()
        self.register('extra1')
        self.register('extra2')
        self.rank('extra1')
        self.review('extra1', result=result, partial=True)
        previous = copy.deepcopy(self.values[2]['stages']['head'])
        with self.assertRaises(ValueError):
            self.rank('extra2')
        self.assertEqual(previous, self.values[2]['stages']['head'])
        self.assertEqual('extra1', previous['ranking']['preferred_call'])

    def test_ranking_cannot_switch_to_another_extra_after_failure(self):
        self.check_ranking_is_immutable('fail')

    def test_ranking_cannot_switch_to_another_extra_to_avoid_pending_supplement(self):
        self.check_ranking_is_immutable('pending')

    def test_old_candidate_id_cannot_be_reused_after_recipe_change(self):
        self.prepare()
        self.register('first')
        self.review('first', result='fail', partial=True)
        original = self.call('first')
        packet_path = self.root / original['packet_file']
        report_path = self.root / self.values[2]['stages']['head']['reviews']['first']['report_file']
        before = {path: path.read_bytes() for path in (packet_path, report_path)}
        self.prompt.write_text('Revised fabricated fixture prompt.', encoding='utf-8')
        self.prepare()
        self.assertEqual(1, len(self.values[3]['history']))
        with self.assertRaises(ValueError):
            self.register('first')
        self.assertEqual([], self.values[2]['stages']['head']['attempts'])
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_fallback_requires_intact_failed_winner_report(self):
        self.approve_first()
        self.register('extra1')
        self.register('extra2')
        self.rank('extra1')
        self.review('extra1', result='fail', partial=True)
        self.assertTrue(self.gate()['recorded_approval_valid'])
        failed = self.values[2]['stages']['head']['reviews']['extra1']
        receipt = self.root / failed['report_file']
        report = core.read(receipt)
        report['checks'][0]['candidate_observation'] += ' altered after fallback'
        workflow.write(receipt, report)
        self.assertFalse(self.gate()['recorded_approval_valid'])

    def test_single_stage_never_approves_full_sheet_or_downstream(self):
        self.approve_first(scope='single_stage', required_calls=1,
                           user_override_reason='User explicitly requested one fixture candidate.')
        self.rank()
        result = self.gate()
        self.assertTrue(result['recorded_approval_valid'], result['errors'])
        self.assertFalse(result['downstream_ready'])
        for options in ({}, {'before': 'front'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                review_gate.check(self.values[1], self.root, **options)
        run = subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPTS / 'review_gate.py'),
                              str(self.root / core.STATE), '--root', str(self.root), '--before', 'front'],
                             capture_output=True, text=True, encoding='utf-8')
        self.assertNotEqual(0, run.returncode)
        self.assertFalse(json.loads(run.stdout)['recorded_approval_valid'])


    def register_fixture_processing(self, identifier='processed'):
        output = self.root / 'fixture-processed-output.png'
        self.make_png(output, (200, 190, 180, 255))
        provenance = self.root / 'fixture-processing-record.json'
        workflow.write(provenance, {'source_sha256': self.values[1]['stages']['head']['sha256'],
                                    'output_sha256': core.digest(output),
                                    'operation': 'Fabricated file fixture processing for record tests.'})
        workflow.register(self.root, 'head', {'kind': 'processing', 'id': identifier,
                                            'output': str(output), 'record_file': str(provenance)}, self.values)
        self.save()
        return output

    def test_processing_needs_explicit_review_and_does_not_consume_generation_slots(self):
        self.complete()
        before_calls = copy.deepcopy(self.values[2]['stages']['head']['attempts'])
        output = self.register_fixture_processing()
        self.assertEqual('review_processed', workflow.status(self.root, 'head', self.values)['next_action'])
        self.assertFalse(self.gate()['recorded_approval_valid'])
        self.assertEqual(before_calls, self.values[2]['stages']['head']['attempts'])
        self.review('processed', processing=True)
        result = self.gate()
        self.assertTrue(result['recorded_approval_valid'], result['errors'])
        self.assertEqual(core.digest(output), self.values[1]['stages']['head']['sha256'])
        self.assertEqual(3, len(self.values[2]['stages']['head']['attempts']))
        self.assertEqual('continue', workflow.status(self.root, 'head', self.values)['next_action'])
        step = self.values[2]['stages']['head']['processing'][0]
        provenance = core.read(self.root / step['record_file'])
        provenance['source_sha256'] = '0' * 64
        workflow.write(self.root / step['record_file'], provenance)
        self.assertFalse(self.gate()['recorded_approval_valid'])

    def test_existing_review_processing_requests_review_of_new_output(self):
        config = {'task_mode': 'existing_review',
                  'sources': {'primary': {'file': str(self.source)}}, 'checks': {'head': self.goals('head')}}
        workflow.prepare(self.root, 'head', config, self.values)
        self.save()
        workflow.register(self.root, 'head', {'id': 'imported', 'output': str(self.image)}, self.values)
        self.save()
        self.review('imported')
        self.assertTrue(self.gate()['recorded_approval_valid'])
        self.register_fixture_processing()
        self.assertEqual('review_processed', workflow.status(self.root, 'head', self.values)['next_action'])
        self.review('processed', processing=True)
        result = self.gate()
        self.assertTrue(result['recorded_approval_valid'], result['errors'])
        self.assertEqual(1, len(self.values[2]['stages']['head']['attempts']))
        step = self.values[2]['stages']['head']['processing'][0]
        provenance = core.read(self.root / step['record_file'])
        provenance['source_sha256'] = '0' * 64
        workflow.write(self.root / step['record_file'], provenance)
        self.assertFalse(self.gate()['recorded_approval_valid'])

    def test_old_front_approval_cannot_be_restored_by_patching_upstream_metadata(self):
        self.prepare(generation_limit=9)  # Explicit fixture budget for two complete head versions.
        self.complete()
        self.complete('front')
        self.assertTrue(self.gate('front')['recorded_approval_valid'])
        old_binding = self.values[1]['stages']['head']['binding_sha256']
        self.prompt.write_text('Changed fixture head generation recipe.', encoding='utf-8')
        self.make_png(self.image, (170, 180, 190, 255))
        self.prepare()
        self.register('new_head')
        self.review('new_head')
        self.register('new_head_extra1')
        self.register('new_head_extra2')
        self.rank('new_head')
        new_binding = self.values[1]['stages']['head']['binding_sha256']
        self.assertNotEqual(old_binding, new_binding)
        self.assertTrue(self.gate()['recorded_approval_valid'])
        self.assertFalse(self.gate('front')['recorded_approval_valid'])
        # Refresh all derived mechanical hashes after only editing the normalized
        # dependency; the frozen review packet still records the old head.
        front = self.values[2]['stages']['front']
        front['reviews']['first']['dependencies'] = {'head': new_binding}
        workflow.refresh(self.root, 'front', self.values)
        self.save()
        self.assertFalse(self.gate('front')['recorded_approval_valid'])

    def test_pending_round_cannot_be_upgraded_by_normalized_metadata_edit(self):
        self.approve_first()
        self.register('extra1')
        self.register('extra2')
        self.rank('extra1')
        self.review('extra1', result='pending', partial=True)
        choice = self.values[2]['stages']['head']
        choice['reviews']['extra1']['supplement_round'] = 1
        workflow.refresh(self.root, 'head', self.values)
        self.save()
        self.assertEqual('first', choice['selected_call'])
        result = self.gate()
        self.assertFalse(result['recorded_approval_valid'], 'Metadata cannot substitute for a real supplement report.')

    def test_deleting_report_binding_cannot_hide_a_damaged_original_report(self):
        self.complete()
        review = self.values[2]['stages']['head']['reviews']['first']
        receipt = self.root / review['report_file']
        receipt.write_text('{"fabricated_damage": true}', encoding='utf-8')
        review.pop('report_file')
        review.pop('report_sha256')
        workflow.refresh(self.root, 'head', self.values)
        self.save()
        self.assertFalse(self.gate(baseline=True)['recorded_approval_valid'])
        self.assertFalse(self.gate()['recorded_approval_valid'])

    def test_pending_report_cannot_authorize_regeneration_by_changing_normalized_decision(self):
        self.prepare()
        self.register('first')
        self.review('first', result='pending', partial=True)
        self.values[2]['stages']['head']['reviews']['first']['decision'] = 'rejected'
        self.save()
        result = workflow.status(self.root, 'head', self.values)
        self.assertEqual('repair_records', result['next_action'])
        with self.assertRaises(ValueError):
            self.register('new_call_after_fake_rejection')
        self.assertEqual(1, len(self.values[2]['stages']['head']['attempts']))

    def test_old_failed_review_cannot_justify_fallback_for_a_different_extra(self):
        self.prepare()
        self.make_png(self.image, (101, 102, 103, 255))
        self.register('old_failed')
        self.review('old_failed', result='fail')
        self.make_png(self.image, (40, 50, 60, 255))
        self.register('first')
        self.review('first')
        self.register('extra1')
        self.register('extra2')
        self.rank('extra1')
        self.assertNotEqual(self.call('old_failed')['output']['sha256'], self.call('extra1')['output']['sha256'])
        choice = self.values[2]['stages']['head']
        choice['reviews']['extra1'] = copy.deepcopy(choice['reviews']['old_failed'])
        workflow.refresh(self.root, 'head', self.values)
        self.save()
        self.assertEqual('first', choice['selected_call'])
        self.assertFalse(self.gate()['recorded_approval_valid'], 'A different candidate report cannot justify fallback.')

    def test_processing_provenance_cannot_be_replaced_by_different_operation_with_same_hashes(self):
        self.complete()
        self.register_fixture_processing()
        self.review('processed', processing=True)
        self.assertTrue(self.gate()['recorded_approval_valid'])
        step = self.values[2]['stages']['head']['processing'][0]
        replacement = core.read(self.root / step['record_file'])
        replacement['operation'] = 'Another fabricated operation with identical input/output hashes.'
        path = self.root / 'fabricated-replacement-processing.json'
        workflow.write(path, replacement)
        step['record_file'] = path.relative_to(self.root).as_posix()
        step['record_sha256'] = core.digest(path)
        workflow.refresh(self.root, 'head', self.values)
        self.save()
        self.assertFalse(self.gate()['recorded_approval_valid'], 'Retained review must bind the actual processing provenance.')

    def limit_select(self, preferred, reviewer=None):
        choice = self.values[2]['stages']['head']
        budget = core.stage_budget('head', choice, self.values[3])
        ids = [call['id'] for entry in budget['ledger'] for call in entry['attempts'] if not call.get('error')]
        workflow.record_review(self.root, 'head', {
            'kind': 'limit-selection', 'preferred_call': preferred, 'viewed_call_ids': ids,
            'selection_reason': 'Fabricated best available choice at a real limit.',
            'reviewer': reviewer or {'mode': 'self', 'model': 'inherited'}}, self.values)
        self.save()

    def test_simple_self_check_precedes_agent_and_failure_avoids_agent_review(self):
        self.prepare()
        self.register('bad')
        self.assertEqual('self_check_first', workflow.status(self.root, 'head', self.values)['next_action'])
        with self.assertRaisesRegex(ValueError, '先简单自评'):
            workflow.record_review(self.root, 'head', {'kind': 'visual', 'call_id': 'bad', 'reviewer': self.reviewer}, self.values)
        self.simple_check('bad', result='fail')
        self.assertFalse(self.values[2]['stages']['head']['reviews'])
        self.assertEqual('revise_input_or_repair', workflow.status(self.root, 'head', self.values)['next_action'])
        self.register('fixed')
        self.simple_check('fixed')
        self.assertEqual('review_first', workflow.status(self.root, 'head', self.values)['next_action'])
        self.review('fixed')
        self.assertTrue(self.gate(baseline=True)['recorded_approval_valid'])

    def test_repair_keeps_failed_details_and_same_agent_across_prepare(self):
        self.prepare()
        self.register('bad')
        self.review('bad', result='fail', partial=True, issue_key='accessory_position')
        self.prompt.write_text('Changed fixture repair prompt.', encoding='utf-8')
        self.prepare()
        self.register('fixed')
        packet = core.read(self.root / self.call('fixed')['packet_file'])
        self.assertEqual('followup', packet['review_context']['mode'])
        self.assertEqual(self.reviewer['agent_id'], packet['review_context']['agent_id'])
        self.assertEqual('accessory_position', packet['review_context']['issues'][0]['issue_key'])
        report = self.report('fixed')
        report['reviewer']['agent_id'] = 'different-test-agent'
        with self.assertRaisesRegex(ValueError, '原子代理'):
            workflow.record_review(self.root, 'head', report, self.values)
        report['reviewer_replacement_reason'] = 'Fabricated original agent unavailable.'
        workflow.record_review(self.root, 'head', report, self.values)
        self.save()
        self.assertTrue(self.gate(baseline=True)['recorded_approval_valid'])

    def test_selected_extra_simple_failure_falls_back_without_agent(self):
        self.approve_first()
        self.register('extra1')
        self.register('extra2')
        self.rank('extra1')
        self.simple_check('extra1', result='fail')
        self.assertNotIn('extra1', self.values[2]['stages']['head']['reviews'])
        self.assertEqual('first', self.values[2]['stages']['head']['selected_call'])
        self.assertTrue(self.gate()['recorded_approval_valid'])
        self.assertEqual('continue', workflow.status(self.root, 'head', self.values)['next_action'])
        self.assertEqual(3, len(self.values[2]['stages']['head']['attempts']))

    def test_stage_limit_self_selects_without_false_approval_and_allows_downstream(self):
        self.prepare(generation_limit=2)
        self.register('bad')
        self.simple_check('bad', result='fail')
        self.register('better')
        self.assertEqual('select_best_at_limit', workflow.status(self.root, 'head', self.values)['next_action'])
        with self.assertRaisesRegex(ValueError, '上限'):
            self.register('excess')
        with self.assertRaisesRegex(ValueError, '上限'):
            self.simple_check('better')
        with self.assertRaisesRegex(ValueError, '自己执行'):
            self.limit_select('better', self.reviewer)
        self.limit_select('better')
        result = self.gate()
        self.assertTrue(result['record_integrity_valid'], result['errors'])
        self.assertTrue(result['downstream_ready'])
        self.assertFalse(result['recorded_approval_valid'])
        self.assertEqual('selected_without_approval', result['visual_assessment'])
        self.assertEqual('selected_unreviewed', self.values[1]['stages']['head']['decision'])
        self.assertFalse(self.gate(baseline=True)['record_integrity_valid'])
        self.complete('front')
        self.assertTrue(self.gate('front')['downstream_ready'])
        self.assertFalse(self.gate('front')['recorded_approval_valid'])
        self.assertTrue(self.gate()['record_integrity_valid'], 'Later stage events must not invalidate limit selection.')

    def test_limit_cannot_be_claimed_early_or_selected_by_subagent(self):
        self.prepare(generation_limit=2)
        self.register('first')
        with self.assertRaisesRegex(ValueError, '尚未达到'):
            self.limit_select('first')
        self.assertNotIn('limit_selection', self.values[2]['stages']['head'])
        self.assertFalse(self.gate()['record_integrity_valid'])

    def test_limit_counts_across_recipe_changes_and_can_choose_archived_image(self):
        self.prepare(generation_limit=2)
        self.register('earlier')
        self.simple_check('earlier', result='fail')
        self.prompt.write_text('Different fixture prompt.', encoding='utf-8')
        self.prepare()
        self.make_png(self.image, (99, 80, 70, 255))
        self.register('latest')
        self.limit_select('earlier')
        result = self.gate()
        self.assertTrue(result['downstream_ready'], result['errors'])
        self.assertEqual('earlier', self.values[2]['stages']['head']['selected_call'])
        self.values[1]['stages']['head']['decision'] = 'approved'
        self.save()
        self.assertFalse(self.gate()['record_integrity_valid'], 'Adoption must never be relabeled approval.')

    def test_limit_records_still_invalidate_on_image_target_and_upstream_changes(self):
        self.prepare(generation_limit=1)
        self.register('only')
        self.limit_select('only')
        self.complete('front')
        self.values[0]['checks']['head'][0]['target'] += ' changed'
        self.save()
        self.assertFalse(self.gate()['record_integrity_valid'])
        self.assertFalse(self.gate('front')['downstream_ready'])

    def test_limit_all_tool_failures_have_no_image_to_select(self):
        self.prepare(generation_limit=1)
        self.register('error', error='Fabricated failure, no output.')
        self.assertEqual('report_tool_failure', workflow.status(self.root, 'head', self.values)['next_action'])
        with self.assertRaises(ValueError):
            self.limit_select('error')

    def test_limit_processing_preserves_unreviewed_status_and_real_chain(self):
        self.prepare(generation_limit=1)
        self.register('only')
        self.limit_select('only')
        self.register_fixture_processing()
        result = self.gate()
        self.assertTrue(result['downstream_ready'], result['errors'])
        self.assertFalse(result['recorded_approval_valid'])
        self.assertEqual('selected_unreviewed', self.values[1]['stages']['head']['decision'])
        self.assertEqual('continue', workflow.status(self.root, 'head', self.values)['next_action'])
        with self.assertRaisesRegex(ValueError, '取消'):
            self.review('processed', processing=True)

    def test_default_six_call_ceiling_survives_different_failure_labels(self):
        self.prepare()
        self.assertEqual(6, self.values[2]['stages']['head']['generation_limit'])
        for index in range(6):
            self.register('attempt_' + str(index))
            if index < 5:
                self.simple_check('attempt_' + str(index), result='fail', issue_key='different_' + str(index))
        status = workflow.status(self.root, 'head', self.values)
        self.assertEqual(('select_best_at_limit', 6), (status['next_action'], status['call_count']))
        with self.assertRaisesRegex(ValueError, '上限'):
            self.register('seventh')
        self.limit_select('attempt_0')
        self.assertTrue(self.gate()['downstream_ready'])
        self.assertFalse(self.gate()['recorded_approval_valid'])

    def test_ceiling_selection_reuses_valid_baseline_without_new_review(self):
        self.prepare()
        for index in range(3):
            self.register('failed_' + str(index))
            self.simple_check('failed_' + str(index), result='fail', issue_key='different_' + str(index))
        self.register('good')
        self.review('good')
        self.register('extra1')
        self.register('extra2')
        self.limit_select('good')
        result = self.gate()
        self.assertTrue(result['recorded_approval_valid'], result['errors'])
        self.assertEqual('approved', self.values[1]['stages']['head']['decision'])
        self.assertEqual('recorded_only', result['visual_assessment'])
        self.assertEqual(1, len(self.values[2]['stages']['head']['reviews']))

    def test_default_framing_cannot_be_strengthened_by_model_target(self):
        goals = self.goals('head')
        framing = next(g for g in goals if g.get('aspect') == 'framing')
        framing['target'] = 'Must have exactly 5% empty margin and end at clavicle.'
        self.prepare(checks={'head': goals})
        actual = next(g for g in self.values[0]['checks']['head'] if g.get('aspect') == 'framing')
        self.assertIn('胸部及以上', actual['target'])
        self.assertIn('飞丝触边', actual['target'])
        self.assertNotIn('5%', actual['target'])
        self.assertEqual('default', actual['requirement_origin'])

    def test_explicit_user_framing_override_needs_real_requirement(self):
        goals = self.goals('head')
        framing = next(g for g in goals if g.get('aspect') == 'framing')
        framing.update(requirement_origin='user', target='User-specific crop.')
        with self.assertRaisesRegex(ValueError, 'user_requirement'):
            self.prepare(checks={'head': goals})
        framing['user_requirement'] = 'Fabricated user quote for this fixture only.'
        self.prepare(checks={'head': goals})
        actual = next(g for g in self.values[0]['checks']['head'] if g.get('aspect') == 'framing')
        self.assertEqual('User-specific crop.', actual['target'])

    def test_normal_ranking_is_maker_only(self):
        self.approve_first()
        self.register('extra1')
        self.register('extra2')
        with self.assertRaisesRegex(ValueError, '制作模型自己'):
            workflow.record_review(self.root, 'head', {'kind': 'selection', 'preferred_call': 'first',
                'viewed_call_ids': ['first', 'extra1', 'extra2'], 'selection_reason': 'fixture',
                'reviewer': self.reviewer}, self.values)
        self.rank()
        self.assertEqual('self', self.values[2]['stages']['head']['ranking']['reviewer']['mode'])

    def test_self_check_cannot_pass_with_only_file_and_alpha_observation(self):
        self.prepare()
        self.register('first')
        call = self.call('first')
        with self.assertRaisesRegex(ValueError, '拼图'):
            workflow.record_review(self.root, 'head', {'kind': 'self-check', 'call_id': 'first',
                'packet_sha256': call['packet_sha256'], 'reviewer': {'mode': 'self', 'model': 'inherited'},
                'result': 'pass', 'observation': 'File dimensions and alpha are valid.'}, self.values)
        self.assertNotIn('self_check', call)

    def test_visual_pass_requires_hair_and_face_observations(self):
        self.prepare()
        self.register('first')
        report = self.report('first')
        report.pop('quality_observations')
        with self.assertRaisesRegex(ValueError, 'quality_observations'):
            workflow.record_review(self.root, 'head', report, self.values)
        self.assertIsNone(self.values[2]['stages']['head']['baseline_call'])

    def test_comparison_and_alpha_board_cover_without_opening_every_source(self):
        self.prepare()
        self.register('first')
        report = self.report('first')
        packet = core.read(self.root / self.call('first')['packet_file'])
        board = next(a for a in packet['evidence'] if a['kind'] == 'comparison')
        self.assertEqual(2, len(board['panels']))
        self.assertTrue((self.root / board['layout_record']).is_file())
        report['viewed_evidence_ids'] = ['whole', 'compare_001', 'native_001', 'reduced', 'alpha_compare']
        for check in report['checks']:
            if check['id'] == 'background':
                check['evidence_ids'] = ['alpha_compare']
            elif check['id'] not in ('native_quality', 'reduced_quality'):
                check['evidence_ids'] = ['compare_001']
        workflow.record_review(self.root, 'head', report, self.values)
        self.save()
        self.register('extra1')
        self.register('extra2')
        self.rank()
        self.assertTrue(self.gate()['recorded_approval_valid'])

    def test_quality_evidence_allows_reference_context_but_requires_native(self):
        self.prepare()
        self.register('first')
        report = self.report('first')
        native = next(c for c in report['checks'] if c['id'] == 'native_quality')
        native['evidence_ids'] = ['source_primary']
        with self.assertRaisesRegex(ValueError, '主要证据'):
            workflow.record_review(self.root, 'head', report, self.values)
        native['evidence_ids'] = ['native_001', 'source_primary']
        workflow.record_review(self.root, 'head', report, self.values)
        self.save()
        evidence = next(c for c in self.values[1]['stages']['head']['checks'] if c['id'] == 'native_quality')['evidence']
        self.assertEqual(['native_detail', 'context_reference'], [a['purpose'] for a in evidence])
        self.assertTrue(self.gate(baseline=True)['recorded_approval_valid'])

    def run_report_cli(self, report, dry_run=True):
        path = self.root / 'fixture-report-config.json'
        workflow.write(path, report)
        before = {str(p.relative_to(self.root)): core.digest(p) for p in self.root.rglob('*') if p.is_file()}
        args = [sys.executable, '-X', 'utf8', str(SCRIPTS / 'review_workflow.py'), 'record-review',
                '--root', str(self.root), '--stage', 'head', '--config', str(path)]
        run = subprocess.run(args + (['--dry-run'] if dry_run else []), capture_output=True, text=True, encoding='utf-8')
        after = {str(p.relative_to(self.root)): core.digest(p) for p in self.root.rglob('*') if p.is_file()}
        if dry_run:
            self.assertEqual(before, after, 'Dry run must not write records, receipts or evidence.')
        return run, json.loads(run.stdout)

    def test_dry_run_reports_multiple_field_errors_without_changes(self):
        self.prepare()
        self.register('first')
        report = self.report('first')
        report['checks'][0]['reference_observation'] = ''
        report['checks'][0]['comparison_basis'] = ''
        report['background_policy']['alpha_source'] = 'descriptive non-enum text'
        next(c for c in report['checks'] if c['id'] == 'background')['empty_background_samples'] = []
        run, output = self.run_report_cli(report)
        self.assertEqual(1, run.returncode)
        self.assertGreaterEqual(len(output['field_errors']), 4)
        self.assertEqual('repair_records', output['next_action'])

    def test_valid_dry_run_is_not_approval_and_same_report_records_normally(self):
        self.prepare()
        self.register('first')
        report = self.report('first')
        run, output = self.run_report_cli(report)
        self.assertEqual(0, run.returncode, run.stdout)
        self.assertEqual('not_recorded', output['visual_assessment'])
        self.assertIsNone(self.values[2]['stages']['head']['baseline_call'])
        run, output = self.run_report_cli(report, dry_run=False)
        self.assertEqual(0, run.returncode, run.stdout)
        self.values = workflow.load_all(self.root)
        self.assertTrue(self.gate(baseline=True)['recorded_approval_valid'])

    def test_preflight_keeps_collecting_errors_for_malformed_fields(self):
        self.prepare()
        self.register('first')
        report = self.report('first')
        report['extra_evidence'] = [{'id': [], 'file': 3, 'kind': 'unknown'}]
        report['viewed_evidence_ids'] = [3, []]
        report['checks'][0]['id'] = []
        report['checks'][1]['evidence_ids'] = None
        run, output = self.run_report_cli(report)
        self.assertEqual(1, run.returncode)
        self.assertGreaterEqual(len(output['field_errors']), 5)

    def test_preflight_accepts_real_absolute_supplement_path(self):
        self.prepare()
        self.register('first')
        report = self.report('first', result='pending')
        report['extra_evidence'] = [{'id': 'detail_context', 'file': str(self.source), 'kind': 'source'}]
        run, output = self.run_report_cli(report)
        self.assertEqual(0, run.returncode, output)

    def test_generation_limit_cannot_be_disabled_with_null(self):
        with self.assertRaisesRegex(ValueError, '正整数'):
            self.prepare(generation_limit=None)

    def test_changed_source_cache_cannot_become_new_reference_evidence(self):
        self.prepare()
        self.register('first')
        self.simple_check('first', result='fail')
        packet = core.read(self.root / self.call('first')['packet_file'])
        reference = next(a for a in packet['evidence'] if a['id'] == 'source_primary')
        expected = reference['sha256']
        self.make_png(self.root / reference['file'], (255, 0, 0, 255))
        self.assertNotEqual(expected, core.digest(self.root / reference['file']))
        self.register('second')
        packet = core.read(self.root / self.call('second')['packet_file'])
        reference = next(a for a in packet['evidence'] if a['id'] == 'source_primary')
        self.assertEqual(expected, reference['sha256'])

    def register_lossless(self, identifier='pad', operation='transparent_canvas_pad'):
        current = self.values[1]['stages']['head']
        output = self.root / ('fixture-' + identifier + '.png')
        with Image.open(self.root / current['image']) as source:
            image = source.convert('RGBA')
        offset = [0, 0] if operation == 'png_reencode' else [1, 2]
        canvas = Image.new('RGBA', image.size if operation == 'png_reencode' else (image.width + 2, image.height + 4))
        canvas.paste(image, tuple(offset))
        canvas.save(output)
        provenance = self.root / ('fixture-' + identifier + '-provenance.json')
        workflow.write(provenance, {'input_sha256': current['sha256'], 'output_sha256': core.digest(output),
                                    'operation': operation, 'offset': offset})
        workflow.register(self.root, 'head', {'kind': 'processing', 'id': identifier, 'output': str(output),
                                            'record_file': str(provenance)}, self.values)
        self.save()
        return output

    def check_lossless(self, identifier='pad', result='pass'):
        step = self.values[2]['stages']['head']['processing'][-1]
        workflow.record_review(self.root, 'head', {'kind': 'processing-check', 'call_id': identifier,
            'packet_sha256': step['packet_sha256'], 'reviewer': {'mode': 'self', 'model': 'inherited'},
            'result': result, 'framing_observation': 'Fabricated framing observation; fixture only.',
            'background_observation': 'Fabricated background observation; fixture only.',
            'viewed_evidence_ids': ['whole', 'alpha_compare']}, self.values)
        self.save()

    def test_lossless_padding_needs_only_maker_framing_and_background_check(self):
        self.complete()
        self.register_lossless()
        self.assertEqual('self_check_processed', workflow.status(self.root, 'head', self.values)['next_action'])
        self.assertFalse(self.gate()['recorded_approval_valid'])
        self.check_lossless()
        result = self.gate()
        self.assertTrue(result['recorded_approval_valid'], result['errors'])
        self.assertEqual('pixel_equivalent_with_framing_check', result['checked'][-1]['approval_basis'])
        self.assertEqual(1, len(self.values[2]['stages']['head']['reviews']))

    def test_lossless_padding_preserves_existing_downstream_approval(self):
        self.complete()
        self.complete('front')
        before = core.fingerprint(self.values[1]['stages']['front'])
        self.register_lossless()
        self.check_lossless()
        self.assertEqual(before, core.fingerprint(self.values[1]['stages']['front']))
        result = self.gate('front')
        self.assertTrue(result['recorded_approval_valid'], result['errors'])

    def test_reencoding_ignores_hidden_rgb_but_never_alpha_or_visible_changes(self):
        from review_assets import pixel_equivalence
        before = self.image.read_bytes()
        with Image.open(self.image) as image:
            image.putpixel((0, 0), (255, 0, 255, 0))
            image.save(self.image)
        self.assertTrue(pixel_equivalence(before, self.image.read_bytes(), 'png_reencode'))
        with Image.open(self.image) as image:
            image.putpixel((3, 3), (1, 2, 3, 255))
            image.save(self.image)
        with self.assertRaisesRegex(ValueError, '可见人物像素'):
            pixel_equivalence(before, self.image.read_bytes(), 'png_reencode')
        with Image.open(self.image) as image:
            image.putpixel((3, 3), (40, 50, 60, 253))
            image.save(self.image)
        with self.assertRaisesRegex(ValueError, 'Alpha变化'):
            pixel_equivalence(before, self.image.read_bytes(), 'png_reencode')

    def test_lossless_framing_failure_never_inherits_approval(self):
        self.complete()
        self.register_lossless()
        self.check_lossless(result='fail')
        self.assertFalse(self.gate()['recorded_approval_valid'])
        self.assertEqual('report_processing_failure', workflow.status(self.root, 'head', self.values)['next_action'])

    def test_lossless_proof_cannot_restore_changed_target(self):
        self.complete()
        self.register_lossless()
        self.check_lossless()
        self.values[0]['checks']['head'][0]['target'] += ' changed'
        self.save()
        self.assertFalse(self.gate()['recorded_approval_valid'])

    def test_lossless_chains_remain_bound_and_reject_forged_content_binding(self):
        self.complete()
        self.register_lossless()
        self.check_lossless()
        self.register_lossless('encode', 'png_reencode')
        self.check_lossless('encode')
        self.assertTrue(self.gate()['recorded_approval_valid'])
        self.values[1]['stages']['head']['dependency_binding_sha256'] = 'invented'
        self.save()
        self.assertFalse(self.gate()['recorded_approval_valid'])

    def test_lossless_report_dry_run_checks_before_recording_and_rejects_repeat(self):
        self.complete()
        self.register_lossless()
        call = self.call('pad', include_processing=True)
        report = {'kind': 'processing-check', 'call_id': 'pad', 'packet_sha256': call['packet_sha256'],
                  'reviewer': {'mode': 'self', 'model': 'inherited'}, 'result': 'pass',
                  'framing_observation': 'Fabricated framing observation.',
                  'background_observation': 'Fabricated background observation.',
                  'viewed_evidence_ids': ['whole', 'alpha_compare']}
        run, output = self.run_report_cli(report)
        self.assertEqual(0, run.returncode, output)
        self.assertEqual('unreviewed', self.values[1]['stages']['head']['decision'])
        run, output = self.run_report_cli(report, dry_run=False)
        self.assertEqual(0, run.returncode, output)
        self.values = workflow.load_all(self.root)
        self.assertTrue(self.gate()['recorded_approval_valid'])
        run, output = self.run_report_cli(report)
        self.assertEqual(1, run.returncode)
        self.assertIn('call_id', [e['field'] for e in output['field_errors']])

    def test_small_aspect_rounding_is_accepted_but_wrong_ratio_is_not(self):
        from check_delivery import aspect_matches
        self.assertTrue(aspect_matches((941, 1671), (9, 16)))
        self.assertTrue(aspect_matches((1086, 1448), (3, 4)))
        self.assertFalse(aspect_matches((941, 1600), (9, 16)))
        self.assertFalse(aspect_matches((1024, 1024), (9, 16)))


class EvidenceRegionTest(unittest.TestCase):
    def test_body_relative_crops_preserve_native_pixels_and_exclude_blank_canvas(self):
        from PIL import ImageDraw
        from review_assets import native_regions
        image = Image.new('RGBA', (1000, 1800))
        draw = ImageDraw.Draw(image)
        draw.ellipse((620, 150, 720, 340), fill=(30, 50, 70, 255))
        draw.rectangle((590, 320, 750, 680), fill=(30, 50, 70, 255))
        draw.polygon([(590, 650), (750, 650), (810, 1080), (530, 1080)], fill=(30, 50, 70, 255))
        draw.rectangle((605, 1070, 655, 1620), fill=(30, 50, 70, 255))
        draw.rectangle((685, 1070, 735, 1620), fill=(30, 50, 70, 255))
        regions = native_regions(image, 'front')
        self.assertEqual(['head_hair', 'torso_waist', 'skirt_hip_hands', 'legs', 'shoes'], [r['region'] for r in regions])
        for region in regions:
            self.assertEqual(1, region['scale'])
            self.assertGreater(region['foreground_fraction'], .03)
            self.assertGreaterEqual(region['crop'][0], 530)
            self.assertIsNotNone(image.crop(region['crop']).getchannel('A').getbbox())
        self.assertLess(regions[0]['crop'][3], 500)
        self.assertGreater(regions[-1]['crop'][1], 1400)
        self.assertEqual([], native_regions(Image.new('RGBA', (1000, 1800)), 'front'))

    def test_background_candidates_skip_occupied_corners_without_visual_pass(self):
        from PIL import ImageDraw
        from review_assets import background_candidates
        image = Image.new('RGBA', (300, 400))
        ImageDraw.Draw(image).rectangle((0, 220, 299, 399), fill=(50, 60, 70, 255))
        samples = background_candidates(image)
        self.assertEqual(2, len(samples))
        for sample in samples:
            self.assertLess(sample['xy'][1], 220)
            self.assertEqual(0, image.getpixel(tuple(sample['xy']))[3])
            self.assertFalse(sample['confirmed_empty'])


if __name__ == '__main__':
    unittest.main()
