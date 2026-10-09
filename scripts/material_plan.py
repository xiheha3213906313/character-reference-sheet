"""Validate once-written source analysis and route its facts into real stage inputs."""
import copy
import re

from review_v2 import require

VIEWS = {'front', 'back', 'side', 'rear_oblique', 'detail', 'unclear'}
KINDS = {'identity', 'design', 'material'}
ROLES = {'head', 'front', 'back', 'left'}


class PlanError(ValueError):
    """All incompatible routes, with exact semantic fields; no partial plan save."""
    def __init__(self, errors):
        self.field_errors = errors
        super().__init__('；'.join(e['message'] for e in errors))


def route_errors(materials):
    rear = any(m['decision'] != 'exclude' and set(m['views']) & {'back', 'rear_oblique'} for m in materials)
    errors = []
    for i, material in enumerate(materials):
        front_only = 'front' in material['views'] and not set(material['views']) & {'back', 'rear_oblique'}
        for j, use in enumerate(material['uses']):
            if rear and front_only and 'back' in use['stages'] and use['kind'] != 'material':
                errors.append({'field': f'materials[{i}].uses[{j}]', 'source_id': material['source_id'],
                    'use_id': use['id'], 'stage': 'back',
                    'message': material['source_id'] + '/' + use['id'] + '的正面设计/身份不能指定为背面设计依据',
                    'correction': '仅属材料纹理时改kind为material；含设计时只从本项stages移除back，'
                                  '将背面可用的材料事实另列kind:material用途。不要连带删除front/left设计依据。'})
    return errors


def validate(materials, sources):
    from sheet_flow import strict, nonempty
    require(isinstance(materials, list) and all(isinstance(m, dict) for m in materials), 'materials须为逐图分析列表')
    require(len(materials) == len(sources) and {m.get('source_id') for m in materials} ==
            {s['source_id'] for s in sources}, '逐图分析须覆盖全部来源且不重复')
    lookup, identifiers = {s['source_id']: s for s in sources}, set()
    for m in materials:
        fields = {'source_id', 'views', 'observation', 'quality', 'decision', 'selection_reason', 'uses'}
        strict(m, fields | {'covered_by', 'accessories'}, fields | {'accessories'})
        from accessory_plan import observations
        observations(m['accessories'], m['source_id'])
        require(isinstance(m['views'], list) and bool(m['views']) and
                all(isinstance(v, str) and v in VIEWS for v in m['views']) and
                len(m['views']) == len(set(m['views'])), 'views须为固定且不重复的实际视角')
        for field in ('observation', 'quality', 'selection_reason'):
            nonempty(m[field], m['source_id'] + '.' + field)
        require(m['decision'] in ('adopt', 'partial', 'exclude'), 'decision须为adopt/partial/exclude')
        require(isinstance(m['uses'], list), 'uses须为结构化阶段用途列表')
        require(bool(m['uses']) == (m['decision'] != 'exclude'), '采用素材须有阶段用途；排除素材不得有调用用途')
        for use in m['uses']:
            strict(use, {'id', 'kind', 'stages', 'target', 'crop'}, {'id', 'kind', 'stages', 'target'})
            require(isinstance(use['id'], str) and re.fullmatch(r'[a-z][a-z0-9_]*', use['id']) and
                    use['id'] not in identifiers, 'uses.id须为全部素材中唯一的英文特征编号')
            identifiers.add(use['id'])
            require(isinstance(use['kind'], str) and use['kind'] in KINDS, 'uses.kind须为identity/design/material')
            require(isinstance(use['stages'], list) and bool(use['stages']) and
                    all(isinstance(r, str) and r in ROLES for r in use['stages']) and len(use['stages']) == len(set(use['stages'])),
                    'uses.stages须明确列出适用阶段head/front/back/left')
            nonempty(use['target'], 'uses.target')
            if 'crop' in use:
                c, s = use['crop'], lookup[m['source_id']]
                require(isinstance(c, list) and len(c) == 4 and all(type(v) is int for v in c) and
                        0 <= c[0] < c[2] <= s['width'] and 0 <= c[1] < c[3] <= s['height'],
                        'uses.crop须为本张原图内的原生像素区域')
        if 'covered_by' in m:
            strict(m['covered_by'], {'source_id', 'reason'}, {'source_id', 'reason'})
            target = next((other for other in materials if other['source_id'] == m['covered_by']['source_id']), None)
            require(m['decision'] == 'exclude' and not m['uses'] and target is not None and target is not m
                    and target['decision'] in ('adopt', 'partial'),
                    'covered_by仅用于没有独有必需细节的排除图，必须指向已采用的清晰替代图')
            nonempty(m['covered_by']['reason'], 'covered_by.reason须说明清晰替代图覆盖的全部相关信息')
    errors = route_errors(materials)
    if errors:
        raise PlanError(errors)


def inject(role, spec, flow):
    """Append planned evidence without shifting caller indices; reuse existing full inputs."""
    errors = route_errors(flow['materials'])
    if errors:
        raise PlanError(errors)
    result = copy.deepcopy(spec)
    refs = result.setdefault('references', [])
    constraints = result.setdefault('critical_constraints', [])
    require(isinstance(refs, list) and all(isinstance(r, dict) for r in refs),
            'prompt.references须为对象列表；按制作接口填写source_id/stage/file/panels及role')
    require(isinstance(constraints, list) and all(isinstance(c, dict) for c in constraints),
            'prompt.critical_constraints须为对象列表，不能填字符串；每项需id/kind/source_indices/statement，无额外约束填[]')
    require(all(isinstance(c.get('id'), str) for c in constraints), 'prompt.critical_constraints[].id须为文本')
    require(not any(c.get('id', '').startswith('planned_') for c in constraints), 'planned_为接口保留的选材目标前缀')
    require(not any(c['id'].startswith('visibility_') for c in constraints), 'visibility_为接口保留的饰品可见性目标前缀')
    pending, coverage = {}, []
    lookup = {s['source_id']: s for s in flow['sources']}
    for m in flow['materials']:
        for use in m['uses']:
            if role not in use['stages']:
                continue
            front_only = 'front' in m['views'] and not set(m['views']) & {'back', 'rear_oblique'}
            guide = 'front_material' if role == 'back' and front_only else None
            # An uncropped original already sent provides this fact without another input slot.
            index = next((i for i, ref in enumerate(refs, 1)
                          if ref.get('source_id') == m['source_id'] and 'crop' not in use), None)
            if index is None and 'crop' in use:
                index = next((i for i, ref in enumerate(refs, 1) if any(
                    panel == {'source_id': m['source_id'], 'crop': use['crop']} for panel in ref.get('panels', []))), None)
            row = {'id': use['id'], 'kind': use['kind'], 'target': use['target'],
                   'source_id': m['source_id'], 'crop': use.get('crop'), 'reference_index': index}
            coverage.append(row)
            if index is None:
                group = pending.setdefault((use['kind'], guide), [])
                panel = {'source_id': m['source_id'], 'crop': use.get('crop', [0, 0, lookup[m['source_id']]['width'], lookup[m['source_id']]['height']])}
                match = next((entry for (_, existing_guide), entries in pending.items() if existing_guide == guide
                              for entry in entries if entry['panel'] == panel), None)
                if match:
                    match['rows'].append(row)
                else:
                    group.append({'panel': panel, 'rows': [row]})
    for (kind, guide), group in pending.items():
        for offset in range(0, len(group), 3):
            batch = group[offset:offset + 3]
            refs.append({'panels': [entry['panel'] for entry in batch],
                         'role': '选材计划的互补证据：' + '；'.join(r['target'] for e in batch for r in e['rows']),
                         **({'guide': guide} if guide else {})})
            for entry in batch:
                for row in entry['rows']:
                    row['reference_index'] = len(refs)
    for row in coverage:
        constraints.append({'id': 'planned_' + row['id'],
                            'kind': {'identity': 'shape', 'design': 'spatial', 'material': 'material'}[row['kind']],
                            'source_indices': [row['reference_index']], 'statement': row['target']})
    return result, coverage
