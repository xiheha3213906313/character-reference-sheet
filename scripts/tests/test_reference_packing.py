"""Reference capacity, aligned proportions and region roles; no visual truth claims."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_prompt
from material_plan import validate
from reference_packing import aligned_board, fit, substitute


class ReferencePackingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='reference-pack-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def image(self, name, size, color):
        path = self.root / (name + '.png')
        image = Image.new('RGB', size, color)
        ImageDraw.Draw(image).line((0, 0, size[0] - 1, size[1] - 1), fill='black', width=1)
        image.save(path)
        return str(path)

    def spec(self, refs, constraints=None, stage='back'):
        return {'operation': 'generate', 'stage': stage, 'identity': 'Synthetic identity.',
                'references': refs, 'critical_constraints': constraints or [
                    {'id': 'material', 'kind': 'material', 'source_indices': [1], 'statement': 'Synthetic material.'}]}

    def assert_rendered_sources(self, path, saved):
        with Image.open(path) as board:
            self.assertGreaterEqual(board.width * 16, board.height * 9)
            self.assertLessEqual(board.width * 3, board.height * 4)
            for panel in saved['regions']:
                with Image.open(panel['image']) as original:
                    native = original.crop(panel['crop']).convert('RGBA')
                    expected = Image.new('RGB', native.size, 'white')
                    expected.paste(native, (0, 0), native.getchannel('A'))
                    self.assertEqual(list(native.size), panel['native_size'])
                    displayed = panel['display_size']
                    self.assertGreaterEqual(displayed[0], native.width)
                    self.assertGreaterEqual(displayed[1], native.height)
                    # Proportions may differ only by integer pixel rounding.
                    self.assertLessEqual(abs(displayed[0] - native.width * displayed[1] / native.height), 1)
                    if list(native.size) != displayed:
                        expected = expected.resize(displayed, Image.Resampling.LANCZOS)
                    self.assertEqual(expected.tobytes(), board.crop(panel['box']).tobytes())
                    self.assertEqual([displayed[0] / native.width, displayed[1] / native.height], panel['actual_scale_xy'])

    def test_portraits_side_by_side_with_native_pixels(self):
        panels = [{'image': self.image(str(i), (120, 200), color), 'label': str(i)}
                  for i, color in enumerate(('red', 'blue'))]
        path, saved = aligned_board(self.root, panels)
        self.assert_rendered_sources(path, saved)
        self.assertTrue(saved['regions'][0]['location'].startswith('左侧'))
        self.assertTrue(saved['regions'][1]['location'].startswith('右侧'))
        self.assertEqual((path, saved), aligned_board(self.root, panels))

    def test_landscapes_stack_and_crops_preserve_pixels(self):
        panels = [{'image': self.image(str(i), (220, 100), color), 'crop': [10, 10, 210, 90]}
                  for i, color in enumerate(('green', 'yellow'))]
        path, saved = aligned_board(self.root, panels)
        self.assert_rendered_sources(path, saved)
        self.assertTrue(saved['regions'][0]['location'].startswith('上方'))
        self.assertTrue(saved['regions'][1]['location'].startswith('下方'))

    def test_three_wide_regions_never_form_a_long_strip(self):
        path, saved = aligned_board(self.root, [{'image': self.image(str(i), (800, 100), color)}
            for i, color in enumerate(('red', 'blue', 'green'))])
        self.assert_rendered_sources(path, saved)
        self.assertEqual(3, len(saved['rows']))
        self.assertLessEqual(saved['output_size'][0], 850)

    def test_unequal_landscapes_align_width_without_square_padding(self):
        panels = [{'image': self.image('upper', (634, 365), 'red')},
                  {'image': self.image('lower', (834, 372), 'blue')}]
        path, saved = aligned_board(self.root, panels)
        self.assert_rendered_sources(path, saved)
        self.assertEqual(2, len(saved['rows']))
        self.assertEqual([834, 834], [r['display_size'][0] for r in saved['regions']])
        self.assertEqual(saved['content_size'], saved['output_size'])
        self.assertNotEqual(*saved['output_size'])
        self.assertTrue(saved['resized'])

    def test_unequal_portraits_align_height_with_landscape_bound(self):
        panels = [{'image': self.image('rear', (805, 1037), 'red')},
                  {'image': self.image('side', (785, 1198), 'blue')}]
        path, saved = aligned_board(self.root, panels)
        self.assert_rendered_sources(path, saved)
        self.assertEqual(1, len(saved['rows']))
        self.assertEqual([1198, 1198], [r['display_size'][1] for r in saved['regions']])
        width, height = saved['output_size']
        self.assertLessEqual(height * 4 - width * 3, 3)

    def test_mixed_orientations_align_width_with_portrait_bound(self):
        panels = [{'image': self.image('portrait', (785, 1198), 'red')},
                  {'image': self.image('landscape', (834, 372), 'blue')}]
        path, saved = aligned_board(self.root, panels)
        self.assert_rendered_sources(path, saved)
        self.assertEqual(2, len(saved['rows']))
        self.assertEqual([834, 834], [r['display_size'][0] for r in saved['regions']])
        width, height = saved['output_size']
        self.assertLessEqual(width * 16 - height * 9, 15)

    def test_single_extreme_region_is_padded_only_to_required_bound(self):
        for size in ((50, 900), (900, 50)):
            path, saved = aligned_board(self.root, [{'image': self.image(str(size), size, 'green')}])
            self.assert_rendered_sources(path, saved)
            self.assertEqual(list(size), saved['regions'][0]['display_size'])
            self.assertFalse(saved['resized'])
            self.assertNotEqual(*saved['output_size'])

    def test_back_pair_is_preferred_and_prompt_uses_real_regions(self):
        refs = [{'image': self.image('front', (100, 180), 'pink'), 'role': 'Material only.', 'guide': 'front_material'},
                {'image': self.image('mask', (100, 180), 'black'), 'role': 'Shape only.', 'guide': 'back_silhouette'},
                {'image': self.image('rear', (100, 180), 'purple'), 'role': 'Rear design.', 'guide': 'rear_design'}]
        constraints = [{'id': key, 'kind': kind, 'source_indices': [i], 'statement': 'Synthetic ' + key}
                       for i, (key, kind) in enumerate((('cloth', 'material'), ('body', 'shape'), ('skirt', 'layer')), 1)]
        spec = self.spec(refs, constraints)
        packed, mapping, report = fit(self.root, spec, ['front', 'mask', 'rear'], 2)
        self.assertEqual(2, len(packed['references']))
        self.assertEqual(mapping[1]['index'], mapping[2]['index'])
        self.assertNotEqual(mapping[1]['region'], mapping[2]['region'])
        self.assertEqual({'front', 'mask'}, {r['source_key'] for r in report['collages'][0]['regions']})
        prompt, _ = build_prompt.render(packed)
        self.assertIn('图1左侧', prompt)
        self.assertIn('图1右侧', prompt)
        self.assertIn('设计cloth（图1左侧', prompt)
        self.assertIn('设计body（图1右侧', prompt)
        wrong = copy.deepcopy(packed)
        wrong['critical_constraints'][0]['kind'] = 'layer'
        with self.assertRaisesRegex(ValueError, '正面原图仅能支持材料'):
            build_prompt.render(wrong)
        wrong = copy.deepcopy(packed)
        wrong['critical_constraints'][0].pop('source_regions')
        with self.assertRaisesRegex(ValueError, 'must name its actual region'):
            build_prompt.render(wrong)

    def test_repeated_merges_preserve_every_required_source_and_constraint(self):
        refs = [{'image': self.image(str(i), (40 + i, 70), (i * 20, 50, 80)), 'role': 'Fact ' + str(i)} for i in range(8)]
        constraints = [{'id': 'fact_' + str(i), 'kind': 'material', 'source_indices': [i + 1], 'statement': 'Fact ' + str(i)} for i in range(8)]
        packed, mapping, report = fit(self.root, self.spec(refs, constraints), [str(i) for i in range(8)], 2)
        self.assertEqual(2, report['after_count'])
        self.assertEqual(set(range(1, 9)), set(mapping))
        self.assertEqual(8, sum(len(r.get('regions', [r])) for r in packed['references']))
        prompt, ids = build_prompt.render(packed)
        self.assertEqual(8, len(ids))
        for c in packed['critical_constraints']:
            self.assertTrue(c['source_regions'])
        for collage in report['collages']:
            import json
            saved = json.loads((self.root / collage['record_file']).read_text(encoding='utf-8'))
            self.assert_rendered_sources(self.root / collage['file'], saved)

    def test_same_file_merges_roles_without_using_a_slot(self):
        image = self.image('same', (80, 120), 'blue')
        refs = [{'image': image, 'role': 'Identity fact.'}, {'image': image, 'role': 'Cloth fact.'}]
        packed, mapping, report = fit(self.root, self.spec(refs), ['s001', 's001'], 1)
        self.assertEqual(1, len(packed['references']))
        self.assertIn('Identity fact.', packed['references'][0]['role'])
        self.assertIn('Cloth fact.', packed['references'][0]['role'])
        self.assertEqual(mapping[1], mapping[2])
        self.assertEqual([], report['collages'])

    def test_edit_baseline_never_becomes_a_collage(self):
        refs = [{'image': self.image(str(i), (80, 120), color), 'role': 'Reference ' + str(i)}
                for i, color in enumerate(('red', 'green', 'blue'))]
        spec = self.spec(refs)
        spec.update(operation='edit', baseline_reference=2, edit_target='Synthetic edit.')
        packed, mapping, _ = fit(self.root, spec, ['a', 'baseline', 'c'], 2)
        self.assertEqual(refs[1]['image'], packed['references'][packed['baseline_reference'] - 1]['image'])
        self.assertNotIn('region', mapping[2])
        build_prompt.render(packed)
        with self.assertRaisesRegex(ValueError, '唯一编辑底图'):
            fit(self.root, spec, ['a', 'baseline', 'c'], 1)

    def test_only_explicit_fully_covered_exclusions_can_replace_blurry_sources(self):
        common = {'views': ['front'], 'accessories': [], 'observation': 'Synthetic visible content.', 'quality': 'Synthetic quality.', 'selection_reason': 'Synthetic selection.'}
        materials = [{**common, 'source_id': 'blur', 'decision': 'exclude', 'uses': [],
                      'covered_by': {'source_id': 'clear', 'reason': 'Synthetic same visible facts, clearer edges.'}},
                     {**common, 'source_id': 'clear', 'decision': 'adopt', 'uses': [
                         {'id': 'face', 'kind': 'identity', 'stages': ['head'], 'target': 'Synthetic identity.'}]}]
        sources = [{'source_id': key, 'width': 80, 'height': 120} for key in ('blur', 'clear')]
        validate(materials, sources)
        rewritten, changes = substitute({'references': [{'source_id': 'blur', 'role': 'Identity.'}]}, materials)
        self.assertEqual('clear', rewritten['references'][0]['source_id'])
        self.assertEqual('blur', changes[0]['removed_source'])
        materials[0]['decision'] = 'partial'
        materials[0]['uses'] = [dict(materials[1]['uses'][0], id='unique_detail')]
        with self.assertRaisesRegex(ValueError, 'covered_by仅用于'):
            validate(materials, sources)


if __name__ == '__main__':
    unittest.main()
