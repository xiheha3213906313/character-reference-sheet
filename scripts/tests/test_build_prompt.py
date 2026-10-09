"""Fixed common prompt contract; no model or image generation calls."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import build_prompt
import prompt_templates as templates


class PromptTest(unittest.TestCase):
    def spec(self, stage='head'):
        return {'operation': 'generate', 'stage': stage,
                'identity': 'Fabricated identity for prompt tests.',
                'references': [{'image': 'source.png', 'role': 'Fabricated reference role.'}],
                'critical_constraints': [{'id': 'visible_design', 'kind': 'shape', 'source_indices': [1],
                                          'statement': 'Fabricated visible design.'}]}

    def test_no_extra_constraints_uses_empty_list_without_fabricating_a_goal(self):
        spec = self.spec('front')
        spec['critical_constraints'] = []
        prompt, ids = build_prompt.render(spec)
        self.assertEqual([], ids)
        self.assertIn(templates.ARMS, prompt)
        self.assertNotIn('Fabricated visible design.', prompt)

    def test_each_stage_always_includes_common_parts_once(self):
        for stage in templates.STAGES:
            with self.subTest(stage=stage):
                prompt, ids = build_prompt.render(self.spec(stage))
                self.assertIn('比例'+templates.RATIOS[stage], prompt)
                for part in (templates.FRAMING[stage]['required'], templates.FRAMING[stage]['preferred'],
                             templates.CHANGES[stage], templates.LIGHTING, templates.EXPRESSION, templates.EYES, templates.QUALITY):
                    self.assertEqual(1, prompt.count(part))
                self.assertEqual(['visible_design'], ids)

    def test_head_template_keeps_top_buffer_without_requiring_all_long_hair(self):
        prompt, _ = build_prompt.render(self.spec())
        for clause in ('略微拉远取景以容纳完整头部轮廓', '最高重要轮廓上方留出明显空白',
                       '胸部及以上取景', '长马尾及长发束可以自然延续出左右画边', '零散飞丝触边允许'):
            self.assertIn(clause, prompt)
        for obsolete in ('下缘到锁骨', '全部发丝', '精确90度'):
            self.assertNotIn(obsolete, prompt)

    def test_all_full_views_share_arms_at_sides_and_accessory_ownership(self):
        from review_policy import FRAMING
        for stage in ('front', 'back', 'left'):
            prompt, _ = build_prompt.render(self.spec(stage))
            self.assertEqual(1, prompt.count(templates.ARMS))
            self.assertEqual(1, prompt.count(templates.ACCESSORIES))
            self.assertIn(templates.ARMS_ACCEPTANCE, FRAMING[stage])
        head, _ = build_prompt.render(self.spec('head'))
        self.assertNotIn(templates.ARMS, head)
        self.assertIn(templates.ACCESSORIES, head)

    def test_character_changes_do_not_rewrite_common_sections(self):
        first = self.spec()
        other = copy.deepcopy(first)
        other['identity'] = 'Another fabricated identity.'
        other['critical_constraints'][0]['statement'] = 'Another fabricated design.'
        a, _ = build_prompt.render(first)
        b, _ = build_prompt.render(other)
        self.assertEqual(a.replace(first['identity'], other['identity']).replace('Fabricated visible design.', 'Another fabricated design.'), b)

    def test_arbitrary_common_fields_are_rejected(self):
        for key in ('framing', 'presentation', 'quality', 'aspect_ratio'):
            spec = dict(self.spec(), **{key: 'Model improvised common wording.'})
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'Unknown spec fields'):
                build_prompt.render(spec)

    def test_explicit_user_override_replaces_default_without_conflicting_duplicates(self):
        spec = self.spec()
        spec['user_overrides'] = {'expression': {'value': '保留用户指定的轻微露齿笑。', 'user_quote': '特写沿用原图微笑'},
                                  'aspect_ratio': {'value': '4:5', 'user_quote': '用4比5'}}
        prompt, _ = build_prompt.render(spec)
        self.assertNotIn(templates.EXPRESSION, prompt)
        self.assertNotIn('3:4', prompt)
        self.assertIn('4:5头部近景', prompt)
        self.assertEqual(1, prompt.count('保留用户指定的轻微露齿笑。'))
        spec['user_overrides']['expression'].pop('user_quote')
        with self.assertRaisesRegex(ValueError, 'user_quote'):
            build_prompt.render(spec)

    def test_white_fallback_is_one_fixed_background_with_reason(self):
        spec = dict(self.spec(), background_mode='white')
        with self.assertRaisesRegex(ValueError, 'background_fallback_reason'):
            build_prompt.render(spec)
        spec['background_fallback_reason'] = 'Fabricated output capability limitation.'
        prompt, _ = build_prompt.render(spec)
        self.assertIn(templates.BACKGROUNDS['white'], prompt)
        self.assertNotIn(templates.BACKGROUNDS['transparent'], prompt)

    def test_explicit_eye_override_replaces_default_and_requires_user_quote(self):
        spec = self.spec()
        spec['user_overrides'] = {'eyes': {'value': '保持用户指定的闭眼。', 'user_quote': '这套要闭眼'}}
        prompt, _ = build_prompt.render(spec)
        self.assertNotIn(templates.EYES, prompt)
        self.assertEqual(1, prompt.count('眼部呈现：保持用户指定的闭眼。'))
        spec['user_overrides']['eyes'].pop('user_quote')
        with self.assertRaisesRegex(ValueError, 'user_quote'):
            build_prompt.render(spec)

    def test_edit_uses_fixed_retention_sentence_and_actual_target(self):
        spec = dict(self.spec(), operation='edit', baseline_reference=1, edit_target='Fabricated repair.')
        prompt, _ = build_prompt.render(spec)
        self.assertIn('唯一编辑底图为图1', prompt)
        self.assertIn('Fabricated repair.', prompt)
        self.assertEqual(1, prompt.count(templates.EDIT_CLEAN))
        self.assertNotIn(templates.CHANGES['head'], prompt)

    def test_auxiliary_roles_and_correction_are_fixed_and_bound_to_actual_inputs(self):
        spec = self.spec('back')
        spec['references'].append({'image': 'silhouette.png', 'role': 'Actual silhouette input.', 'guide': 'back_silhouette'})
        prompt, _ = build_prompt.render(spec)
        self.assertIn(templates.GUIDES['back_silhouette'].format(index=2), prompt)
        spec['stage'] = 'head'
        with self.assertRaisesRegex(ValueError, 'back stage'):
            build_prompt.render(spec)
        spec = dict(self.spec(), corrections=['gaze_up'])
        prompt, _ = build_prompt.render(spec)
        self.assertEqual(1, prompt.count(templates.CORRECTIONS['gaze_up']))

    def test_cli_freezes_real_reference_order_and_template_provenance(self):
        with tempfile.TemporaryDirectory(prefix='fixed-prompt-') as directory:
            root = Path(directory)
            spec = self.spec()
            (root / 'source.png').write_bytes(b'Fabricated file bytes; not a visual reference.')
            source = root / 'spec.json'
            source.write_text(json.dumps(spec), encoding='utf-8')
            target = root / 'prompt.txt'
            result = subprocess.run([sys.executable, str(SCRIPTS / 'build_prompt.py'), str(source), '--output', str(target)],
                                    capture_output=True, encoding='utf-8')
            self.assertEqual(0, result.returncode, result.stderr)
            meta = json.loads(target.with_suffix('.txt.json').read_text(encoding='utf-8'))
            self.assertEqual(templates.TEMPLATE_ID, meta['template_id'])
            self.assertEqual(hashlib.sha256(Path(templates.__file__).read_bytes()).hexdigest(), meta['template_sha256'])
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), meta['prompt_sha256'])
            self.assertEqual(str((root / 'source.png').resolve()), meta['references'][0]['image'])
            repeat = subprocess.run([sys.executable, str(SCRIPTS / 'build_prompt.py'), str(source), '--output', str(target)],
                                    capture_output=True, encoding='utf-8')
            self.assertNotEqual(0, repeat.returncode)


    def test_rear_policy_uses_originals_and_masks_cannot_answer_design(self):
        spec = self.spec('back')
        spec['references'][0]['guide'] = 'back_silhouette'
        spec['references'].append({'image': 'rear.png', 'role': 'Synthetic rear source.', 'guide': 'rear_design'})
        prompt, _ = build_prompt.render(spec)
        self.assertEqual(1, prompt.count(templates.BACK_REFERENCE_RULE))
        self.assertIn('图2是背面或侧后原始参考', prompt)
        spec['critical_constraints'][0]['kind'] = 'layer'
        with self.assertRaisesRegex(ValueError, '剪影只能支持外形比例'):
            build_prompt.render(spec)
        spec['critical_constraints'][0]['source_indices'] = [2]
        build_prompt.render(spec)


if __name__ == '__main__':
    unittest.main()
