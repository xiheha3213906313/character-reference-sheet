"""Fixed production API. See references/production-api.md; never calls image models."""
import argparse
import copy
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image

import build_prompt
import prompt_templates
import review_workflow as workflow
from review_v2 import (read, digest, fingerprint, require, target_binding, stage_budget,
                       image_bytes_for_review)
from review_policy import FRAMING, QUALITY
from review_assets import comparison
from production_records import FLOW, RECORD, ROLES, export, read_flow

IMAGES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}
CHARACTER_FIELDS = {'name', 'height_cm', 'age_years', 'appearance', 'outfit', 'outfit_name', 'setting_basis'}
MATERIAL_VIEWS = {'front', 'back', 'side', 'rear_oblique', 'detail', 'unclear'}
REAR_VIEWS = {'back', 'rear_oblique'}
PREPARE_FIELDS = {'character', 'materials', 'accessory_visibility', 'prompt', 'review_regions', 'comparisons',
                  'reviewer_policy', 'generation_limit', 'required_calls', 'user_override_reason', 'strategy_change',
                  'max_reference_images', 'tool', 'parameters', 'argument_fields'}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def nonempty(value, label):
    require(isinstance(value, str) and bool(value.strip()), label + '须为非空文本')
    return value.strip()


def strict(value, allowed, required=()):
    require(isinstance(value, dict), '输入须为JSON对象')
    require(set(value) <= set(allowed), '未知字段：' + ', '.join(sorted(set(value) - set(allowed))))
    require(set(required) <= set(value), '缺少字段：' + ', '.join(sorted(set(required) - set(value))))


def start(root, source_directory, destination=None, destination_user_quote=None):
    require(not (root / FLOW).exists(), '项目已开始；用status继续，不能覆盖来源清单')
    require(not any((root / f).exists() for f in (workflow.BRIEF, workflow.STATE, workflow.SELECTION)),
            '此目录已有制作记录；新接口请用独立项目目录，不改写已有任务')
    source_directory = Path(source_directory).resolve()
    require(source_directory.is_dir(), '素材目录不存在')
    files = sorted(p for p in source_directory.iterdir() if p.is_file() and p.suffix.lower() in IMAGES)
    require(bool(files), '素材目录没有支持的图片；只清点该目录，不递归混入旧交付')
    require(bool(destination) == bool(destination_user_quote), '仅用户明确另定目的地时同时提供destination和destination_user_quote原话')
    sources = []
    for index, file in enumerate(files, 1):
        with Image.open(file) as image:
            image.load()
            width, height = image.size
            mode = image.mode
        snap = workflow.snapshot(root, str(file), '参考')
        sources.append({'source_id': f's{index:03d}', 'original_file': str(file), **snap,
                        'width': width, 'height': height, 'mode': mode})
    flow = {'schema_version': 3, 'created_at': utc_now(), 'source_directory': str(source_directory),
            'sources': sources, 'materials': [], 'accessory_visibility': [], 'front_structure': None,
            'delivery_override': {'destination': str(Path(destination).resolve()), 'user_quote': nonempty(destination_user_quote, 'destination_user_quote')} if destination else None,
            'character': None, 'stages': {}, 'requests': {}, 'delivery': None}
    workflow.write(root / FLOW, flow)
    export(root, flow)
    worksheet = {'character': {k: None if k in ('height_cm', 'age_years') else '' for k in sorted(CHARACTER_FIELDS)},
                 'materials': [{'source_id': s['source_id'], 'views': [], 'observation': '', 'quality': '',
                                'decision': '', 'selection_reason': '', 'uses': [], 'accessories': []} for s in sources],
                 'accessory_visibility': [],
                 'prompt': {'operation': 'generate', 'identity': '', 'references': [], 'critical_constraints': []}}
    workflow.write(root / '制作记录/阶段输入/首次准备.json', worksheet)
    return {'next_action': 'analyze_sources_then_prepare_head', 'sources': sources,
            'input_file': str(root / '制作记录/阶段输入/首次准备.json'),
            'submit_command': submit_command('prepare', root, 'head', root / '制作记录/阶段输入/首次准备.json'),
            'read_now': ['references/material-selection.md', 'references/production-api.md#prepare'],
            'do_not_read_yet': ['references/production-delivery-api.md', 'later-stage references'],
            'source_access': 'forbidden_during_production'}


def validate_character(character):
    strict(character, CHARACTER_FIELDS, CHARACTER_FIELDS)
    for key in CHARACTER_FIELDS - {'height_cm', 'age_years'}:
        nonempty(character[key], 'character.' + key)
    for key in ('height_cm', 'age_years'):
        require(type(character[key]) in (int, float) and character[key] > 0, key + '须为正数')
    from check_delivery import overview_name
    overview_name(character['name'], character['outfit_name'])


def validate_materials(materials, sources):
    from material_plan import validate
    validate(materials, sources)


def plan_signature(flow, role):
    return fingerprint({'character': flow['character'],
        'uses': [{'source_id': m['source_id'], 'views': m['views'], 'use': u}
                 for m in flow['materials'] if m['decision'] != 'exclude'
                 for u in m['uses'] if role in u['stages']],
        'accessories': [{k: a[k] for k in ('id', 'name', 'carrier', 'location')} |
                       {'stage': a['stages'][role]} for a in flow['accessory_visibility']]})


def invalidate_changed_plan(root, flow, staged, values):
    changed = {r for r in ROLES if r in flow['stages'] and plan_signature(flow, r) != plan_signature(staged, r)}
    affected = [r for r in ROLES if r in flow['stages'] and
                (r in changed or any(up in changed for up in workflow.DEPENDENCIES[r]))]
    configurations = {}
    for affected_role in affected:
        state = values[1]['stages'][affected_role]
        state.update(decision='unreviewed', plan_requires_prepare=True)
        previous = read(root / flow['stages'][affected_role]['input_file'])
        updated = {k: v for k, v in previous.items() if k not in ('character', 'materials', 'accessory_visibility')}
        path = root / f'制作记录/阶段输入/{affected_role}-{plan_signature(staged, affected_role)[:16]}-prepare.json'
        workflow.write(path, updated)
        staged['stages'][affected_role]['reprepare_input_file'] = workflow.local(root, path)
        configurations[affected_role] = str(path)
    return affected, configurations


def update_plan(root, role, config, input_path=None):
    """Update shared facts explicitly, without reserving calls or reviving approvals."""
    strict(config, PREPARE_FIELDS, {'materials', 'accessory_visibility'})
    flow = read_flow(root)
    require(flow['character'] is not None, '首次选材用prepare；update-plan仅更新已保存计划')
    require(not any(r['state'] == 'awaiting_result' for r in flow['requests'].values()), '先receive已发出请求，再更新选材')
    staged = copy.deepcopy(flow)
    validate_materials(config['materials'], flow['sources'])
    staged['materials'] = config['materials']
    if 'character' in config:
        validate_character(config['character'])
        staged['character'] = config['character']
    from accessory_plan import validate
    validate(config['accessory_visibility'], staged['materials'])
    staged['accessory_visibility'] = config['accessory_visibility']
    if input_path:
        staged['plan_input_file'] = str(input_path.resolve())
    values = workflow.load_all(root)
    affected, configurations = invalidate_changed_plan(root, flow, staged, values)
    workflow.save_all(root, values)
    save(root, staged, values)
    next_role = affected[0] if affected else role
    return {'next_action': 'prepare', 'stage': next_role, 'plan_saved': True,
            'affected_prepared_stages': affected, 'prepare_config_files': configurations,
            **({'config_file': configurations[next_role],
                'submit_command': submit_command('prepare', root, next_role, configurations[next_role])}
               if next_role in configurations else {}),
            'instruction': '计划已保存；按最早受影响阶段重新prepare，后续依次处理。无已准备阶段受影响时，'
                           '原样重试当前阶段prepare。update-plan不调用生图、不重置次数、不恢复视觉批准；prompt不在此命令应用。'}


def resolve_prompt(root, role, spec, flow, values):
    spec = copy.deepcopy(spec)
    spec['stage'] = role
    if not spec.get('identity'):
        spec['identity'] = flow['character']['appearance']
    spec.setdefault('character_name', flow['character']['name'])
    spec.setdefault('height_cm', flow['character']['height_cm'])
    refs, sources, keys = [], {}, []
    lookup = {s['source_id']: s for s in flow['sources']}
    materials = {m['source_id']: m for m in flow['materials']}
    rear_ids = [m['source_id'] for m in flow['materials'] if m['decision'] != 'exclude' and REAR_VIEWS & set(m['views'])]
    rear_mode = role == 'back' and bool(rear_ids)
    if role == 'back':
        # Keep caller indices stable. The original and mirrored mask have disjoint roles.
        for guide in ('front_material', 'back_silhouette'):
            if not any(r.get('stage') == 'front' and r.get('guide', 'front_material') == guide
                       for r in spec.get('references', [])):
                spec.setdefault('references', []).append({'stage': 'front', 'guide': guide,
                                                          'role': prompt_templates.GUIDE_ROLES[guide]})
    if rear_mode:
        require(not any(c.get('id') == 'rear_reference' for c in spec.get('critical_constraints', [])),
                'rear_reference是接口保留的背面原图检查项，角色约束另取id')
        included = {ref.get('source_id') for ref in spec.get('references', [])}
        included.update(p['source_id'] for ref in spec.get('references', []) for p in ref.get('panels', []))
        if not included.intersection(rear_ids):
            primary = next((key for key in rear_ids if 'back' in materials[key]['views']), rear_ids[0])
            spec.setdefault('references', []).append({'source_id': primary, 'role': '背面原始设计依据'})
    for ref in spec.get('references', []):
        strict(ref, {'source_id', 'stage', 'file', 'panels', 'role', 'guide'}, {'role'})
        require(sum(k in ref for k in ('source_id', 'stage', 'file', 'panels')) == 1, '参考须选source_id/stage/file/panels一种')
        nonempty(ref['role'], 'reference.role')
        guide = ref.get('guide')
        if 'source_id' in ref:
            key = ref['source_id']
            require(key in lookup and materials[key]['decision'] != 'exclude', '参考未采用或不存在：' + key)
            record = lookup[key]
            path = root / record['file']
            require(digest(path) == record['sha256'], '来源快照变化：' + key)
            if rear_mode:
                if key in rear_ids:
                    guide = 'rear_design'
                elif 'front' in materials[key]['views']:
                    guide = 'front_material'
        elif 'stage' in ref:
            up = ref['stage']
            require(up in workflow.DEPENDENCIES[role], '当前阶段不能使用此上游：' + str(up))
            workflow.dependencies(root, values[1], role, values[0])
            review = values[1]['stages'][up]
            key = 'upstream_' + up
            path = root / '制作记录/参考' / (review['sha256'] + '.png')
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(image_bytes_for_review(review, root, up))
            if role == 'back' and up == 'front':
                if guide == 'back_silhouette':
                    from production_evidence import front_silhouette
                    path = front_silhouette(root, path)
                    key = 'upstream_front_silhouette'
                else:
                    key, guide = 'upstream_front_material', 'front_material'
        elif 'panels' in ref:
            from production_evidence import reference_board
            if rear_mode:
                panel_ids = [p['source_id'] for p in ref['panels']]
                front_ids = {k for k in panel_ids if 'front' in materials[k]['views'] and not REAR_VIEWS & set(materials[k]['views'])}
                require(not (front_ids and set(panel_ids).intersection(rear_ids)),
                        '正面材质与后方设计分工不同，不能混在同一参考拼图')
                if set(panel_ids).intersection(rear_ids):
                    guide = 'rear_design'
                elif front_ids:
                    guide = 'front_material'
            path = reference_board(root, ref['panels'], flow)
            key = 'aux_' + digest(path)[:16]
            layout = read(path.with_suffix('.png.json'))
            regions = []
            for panel in layout['regions']:
                source_id = panel['label']
                uses = [u['target'] for u in materials[source_id]['uses'] if role in u['stages']]
                regions.append(panel['location'] + '是' + source_id + '原生局部：' + ('；'.join(uses) if uses else ref['role']))
            ref['role'] += '。该图内部位置：' + '；'.join(regions)
        else:
            path = workflow.source_path(root, ref['file'])
            file_sha = digest(path)
            key = 'aux_' + file_sha[:16]
            if rear_mode:
                front = values[1]['stages'].get('front', {})
                if file_sha == front.get('sha256') or any(file_sha == s['sha256'] and 'front' in materials[s['source_id']]['views']
                                and not REAR_VIEWS & set(materials[s['source_id']]['views']) for s in flow['sources']):
                    guide = 'front_material'
        sources[key] = {'file': str(path)}
        keys.append(key)
        purpose = prompt_templates.GUIDE_ROLES.get(guide, ref['role'])
        if 'panels' in ref and guide:
            purpose += '。该图内部位置：' + '；'.join(regions)
        refs.append({'image': str(path), 'role': purpose, **({'guide': guide} if guide else {})})
    require(refs, '至少提供一项真实参考')
    spec['references'] = refs
    return spec, sources, keys


def targets_from_prompt(role, spec, keys):
    """Use the single semantic prompt input for targets; no repeated model transcription."""
    scope = prompt_templates.REFERENCE_SCOPE + prompt_templates.EYE_ACCEPTANCE
    usable = [k for k, ref in zip(keys, spec['references']) if ref.get('guide') not in ('front_material', 'back_silhouette')]
    identity = [keys[i - 1] for c in spec['critical_constraints'] if c['kind'] == 'shape'
                for i in c['source_indices']]
    identity += [k for k in usable if k == 'upstream_head']
    result = [{'id': 'identity', 'group': 'identity', 'target': spec['identity'],
               'evaluation_scope': scope, 'source_ids': list(dict.fromkeys(identity or usable or keys))}]
    rear_keys = [key for key, ref in zip(keys, spec['references']) if ref.get('guide') == 'rear_design']
    if role == 'back' and rear_keys:
        result.append({'id': 'design_rear_reference', 'group': 'design',
                       'target': prompt_templates.BACK_REFERENCE_RULE, 'source_ids': list(dict.fromkeys(rear_keys))})
    material_keys = [key for key, ref in zip(keys, spec['references']) if ref.get('guide') == 'front_material']
    if role == 'back' and material_keys:
        result.append({'id': 'design_front_material', 'group': 'design',
                       'target': prompt_templates.GUIDE_ROLES['front_material'], 'source_ids': list(dict.fromkeys(material_keys))})
    for c in spec['critical_constraints']:
        target = c['statement']
        for field, label in (('attachment', '固定点'), ('path', '路径'), ('end_anchor', '真实末端'), ('frame_behavior', '画幅处理')):
            if field in c:
                target += '；' + label + '：' + c[field]
        result.append({'id': 'design_' + c['id'], 'group': 'design',
                       'evaluation_scope': scope,
                       'target': target, 'source_ids': list(dict.fromkeys(keys[i - 1] for i in c['source_indices']))})
    result.append({'id': 'design_accessory_placement', 'group': 'design', 'target': prompt_templates.ACCESSORIES,
                   'evaluation_scope': scope, 'reference_display': 'context_only',
                   'source_ids': list(dict.fromkeys(usable or keys))})
    common = prompt_templates.common(role, spec.get('user_overrides', {}), spec.get('background_mode', 'transparent'))
    look = {'color_style': '保留有效原图的角色本色、材料与画风，排除成像污染。',
            'lighting': common['lighting'], 'framing': FRAMING[role],
            'head_pose': '头部自然端正，不仰头，面部朝前。',
            'gaze': common['eyes'] + ('视线按用户眼部目标及当前视角自然呈现。'
                                      if 'eyes' in spec.get('user_overrides', {}) else '自然正视镜头。'),
            'expression': common['expression']}
    ratio_override = spec.get('user_overrides', {}).get('aspect_ratio')
    if ratio_override:
        look['framing'] = look['framing'].replace('3:4' if role == 'head' else '9:16', ratio_override['value'])
    for aspect in ('color_style', 'lighting', 'framing', 'head_pose', 'gaze', 'expression'):
        if role in ('back', 'left') and aspect in ('head_pose', 'gaze', 'expression'):
            continue
        goal = {'id': 'look_' + aspect, 'group': 'look', 'aspect': aspect, 'target': look[aspect],
                'source_ids': list(dict.fromkeys(keys))}
        override_key = {'framing': 'framing_required', 'gaze': 'eyes'}.get(aspect, aspect)
        override = spec.get('user_overrides', {}).get(override_key)
        if override:
            goal.update(target=override['value'], requirement_origin='user', user_requirement=override['user_quote'])
        elif aspect == 'framing' and ratio_override:
            goal.update(requirement_origin='user', user_requirement=ratio_override['user_quote'])
        result.append(goal)
    for group, target in [('native_quality', QUALITY),
                          ('reduced_quality', '缩小观看仍清楚、干净，无明显糊块或脏污。'),
                          ('background', common['background'] + '；只看一次深浅底拼图，确认标号空白采样位置。')]:
        result.append({'id': group, 'group': group, 'target': target, 'source_ids': list(dict.fromkeys(keys))})
    if role in ('back', 'left'):
        result.append({'id': 'spatial', 'group': 'spatial',
                       'target': '与有效原始角度依据及上游比较部件归属、连接、内外层、相对长度及遮挡；合理小偏差接受。'
                                 '前后可见的裁片、裙层、后摆和饰件布局可以不同，不能要求复制正面；自然遮挡只查可见关系，不要求看穿头发。',
                       'source_ids': list(dict.fromkeys(usable or keys))})
    return result


def prepare(root, role, config, input_path=None):
    strict(config, PREPARE_FIELDS, {'prompt'})
    flow = read_flow(root)
    require(not any(r['state'] == 'awaiting_result' for r in flow['requests'].values()), '先登记已发出的调用结果，不重新准备')
    staged = copy.deepcopy(flow)
    if flow['character'] is None:
        require(role == 'head' and 'character' in config and 'materials' in config, '首次prepare须提交character/materials')
    if 'character' in config:
        validate_character(config['character'])
        staged['character'] = config['character']
    if 'materials' in config:
        validate_materials(config['materials'], flow['sources'])
        staged['materials'] = config['materials']
        staged['plan_input_file'] = str(input_path.resolve()) if input_path else str(root / '制作记录/阶段输入/首次准备.json')
        require('accessory_visibility' in config, '提交逐图materials时须同时提交分析完成后的accessory_visibility汇总')
    if 'accessory_visibility' in config:
        from accessory_plan import validate
        validate(config['accessory_visibility'], staged['materials'])
        staged['accessory_visibility'] = config['accessory_visibility']
    values = workflow.load_all(root)
    affected = [r for r in ROLES if r in flow['stages'] and plan_signature(flow, r) != plan_signature(staged, r)]
    require(not affected or ROLES.index(role) <= ROLES.index(affected[0]),
            '共享选材变化影响上游目标：先update-plan，由返回的最早阶段重新prepare；不能绕过上游批准失效')
    invalidate_changed_plan(root, flow, staged, values)
    from accessory_plan import refresh_prepared
    refresh_prepared(root, role, flow, staged, values)
    from material_plan import inject
    from reference_packing import substitute, fit
    preferred, replacements = substitute(config['prompt'], staged['materials'])
    planned, coverage = inject(role, preferred, staged)
    semantic_spec, sources, keys = resolve_prompt(root, role, planned, staged, values)
    from accessory_plan import inject as inject_accessories
    visibility_goals = inject_accessories(root, role, semantic_spec, sources, staged)
    if role in ('back', 'left'):
        from spatial_plan import inject as inject_structure
        visibility_goals = inject_structure(root, role, staged, values, semantic_spec, sources)
    maximum = config.get('max_reference_images', 5)
    build_prompt.render(semantic_spec)  # Validate each original role before any merging.
    goals = targets_from_prompt(role, semantic_spec, keys)
    goals.extend(visibility_goals)
    from review_references import expand, remap_goals
    review_map = expand(root, sources)
    lookup = {s['source_id']: s for s in staged['sources']}
    exact = {'design_planned_' + row['id']: (lookup[row['source_id']]['sha256'], row['crop']) for row in coverage}
    remap_goals(goals, review_map, exact, sources)
    identity_sources = list(dict.fromkeys(k for g in goals
        if g['id'] in {'design_planned_' + r['id'] for r in coverage if r['kind'] == 'identity'} for k in g['source_ids']))
    if identity_sources:
        next(g for g in goals if g['id'] == 'identity')['source_ids'] = list(dict.fromkeys(
            identity_sources + review_map.get('upstream_head', [])))
    spec, reference_map, packing = fit(root, semantic_spec, keys, maximum)
    packing['source_replacements'] = replacements
    prompt, _ = build_prompt.render(spec)
    parameters = config.get('parameters', {})
    require(isinstance(parameters, dict), 'parameters须为对象')
    tool = config.get('tool', 'image_gen.imagegen')
    fields = config.get('argument_fields', {'prompt': 'prompt', 'references': 'referenced_image_paths'})
    strict(fields, {'prompt', 'references'}, {'prompt', 'references'})
    require(all(isinstance(v, str) and v.strip() for v in fields.values()) and len(set(fields.values())) == 2,
            'argument_fields须明确实际工具的prompt/references字段名且不能相同')
    require(tool == 'image_gen.imagegen' or 'argument_fields' in config,
            '其他生图工具须提供argument_fields，按其实际接口映射提示词和参考图片参数')
    require(not (set(parameters) & {'prompt', 'referenced_image_paths', 'num_last_images_to_include'}),
            'parameters不能覆盖已编译提示词或有序参考')
    require(not (set(parameters) & set(fields.values())), 'parameters不能覆盖argument_fields指定的提示词和参考字段')
    if tool == 'image_gen.imagegen':
        strict(parameters, {'transparent_background'})
    background = spec.get('background_mode', 'transparent') == 'transparent'
    require('transparent_background' not in parameters or type(parameters['transparent_background']) is bool
            and parameters['transparent_background'] == background, 'transparent_background须与prompt.background_mode一致')
    if tool == 'image_gen.imagegen':
        parameters = {'transparent_background': background, **parameters}
    version = fingerprint(config)[:16]
    source_config = root / f'制作记录/阶段输入/{role}-{version}.json'
    if source_config.exists():
        require(read(source_config) == config, '阶段输入发生冲突')
    else:
        workflow.write(source_config, config)
    spec_file = root / f'制作记录/阶段输入/{role}-{version}-resolved.json'
    workflow.write(spec_file, spec)
    prompt_file = root / f'制作记录/提示词/{hashlib.sha256(prompt.encode("utf-8")).hexdigest()}.txt'
    metadata = prompt_file.with_suffix('.txt.json')
    if not prompt_file.exists():
        completed = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(Path(__file__).with_name('build_prompt.py')),
                                    str(spec_file), '--output', str(prompt_file)], capture_output=True, encoding='utf-8')
        require(completed.returncode == 0, completed.stderr)
    require(prompt_file.read_text(encoding='utf-8') == prompt and metadata.is_file(), '编译提示词或元数据已变化')
    recipe = {'tool': tool, 'parameters': parameters,
              **({'argument_fields': fields} if 'argument_fields' in config else {}),
              'prompt_file': str(prompt_file),
              'inputs': [{'file': r['image'], 'purpose': r['role'],
                          **{k: copy.deepcopy(r[k]) for k in ('guide', 'regions') if k in r}}
                         for r in spec['references']]}
    low = {'sources': sources, 'checks': {role: goals}, 'recipe': recipe}
    for k in ('reviewer_policy', 'generation_limit', 'required_calls', 'user_override_reason', 'strategy_change'):
        if k in config:
            low[k] = config[k]
    workflow.prepare(root, role, low, values)
    workflow.save_all(root, values)
    staged['stages'][role] = {'input_file': workflow.local(root, source_config),
                             'prompt_metadata': {'file': workflow.local(root, metadata), 'sha256': digest(metadata)},
                             'review_regions': config.get('review_regions', []),
                             'comparisons': config.get('comparisons'), 'source_keys': keys,
                             'review_reference_map': review_map}
    staged['stages'][role]['reference_packing'] = packing
    staged['stages'][role]['reference_coverage'] = [
        {**row, 'reference_key': keys[row['reference_index'] - 1],
         'reference_index': reference_map[row['reference_index']]['index'],
         **({'reference_region': reference_map[row['reference_index']]['region']}
            if 'region' in reference_map[row['reference_index']] else {})} for row in coverage]
    save(root, staged, values)
    return advance(root, role, staged, values, retry=True)


def save(root, flow, values):
    workflow.write(root / FLOW, flow)
    export(root, flow, values)


def reserve(root, role, count, flow, values):
    choice = values[2]['stages'][role]
    recipe = choice['recipe']
    requests = []
    for _ in range(count):
        identifier = role + '-' + f'{len(flow["requests"]) + 1:04d}'
        request = {'request_id': identifier, 'stage': role, 'state': 'awaiting_result',
                   'recipe_sha256': workflow.recipe_binding(recipe), 'tool': recipe['tool'],
                   'actual_tool_call_id': None, 'call_id': None}
        flow['requests'][identifier] = request
        requests.append(request)
    save(root, flow, values)
    receipt_file = receipt_config(root, role, requests)
    workflow.write(receipt_file, {'calls': [{'request_id': r['request_id'], 'tool_call_id': None, 'result': None} for r in requests]})
    workflow.write(requests_file(root, role, requests), {'tool_call_count': count,
        'requests': [{'request_id': r['request_id'], 'tool': r['tool'], 'recipe_sha256': r['recipe_sha256'],
                      'arguments': tool_arguments(root, recipe)} for r in requests]})
    return requests


def requests_file(root, role, requests):
    return root / ('制作记录/调用参数/' + role + '-' + fingerprint([r['request_id'] for r in requests])[:16] + '.json')


def tool_arguments(root, recipe):
    fields = recipe.get('argument_fields', {'prompt': 'prompt', 'references': 'referenced_image_paths'})
    return {fields['prompt']: (root / recipe['prompt_file']).read_text(encoding='utf-8'),
            fields['references']: [str(root / ref['file']) for ref in recipe['inputs']], **recipe['parameters']}


def action_config(root, command, role, key, initial, mutate):
    path = root / f'制作记录/阶段输入/{role}-{key[:16]}-{command}.json'
    if mutate and not path.exists():
        workflow.write(path, initial)
    return {'config_file': str(path), 'submit_command': submit_command(command, root, role, path)}


def receipt_config(root, role, requests):
    return root / ('制作记录/阶段输入/' + role + '-' + fingerprint([r['request_id'] for r in requests])[:16] + '-receive.json')


def submit_command(command, root, role, config_file):
    args = [command, '--root', str(root), '--stage', role]
    if config_file is not None:
        args += ['--config', str(config_file)]
    return invocation(args)


def invocation(args):
    import os
    import shlex
    if os.name == 'nt':
        quote = lambda s: "'" + s.replace("'", "''") + "'"
        return '& ' + ' '.join(quote(s) for s in [str(Path(__file__).with_suffix('.ps1')), *args])
    return shlex.join([sys.executable, str(Path(__file__)), *args])


def ranking_board(root, role, identifiers, values):
    choice = values[2]['stages'][role]
    groups = [h['selection'] for h in values[3]['history'] if h['stage'] == role] + [choice]
    lookup = {c['id']: c for g in groups for c in g['attempts'] if not c.get('error')}
    panels = [{'file': root / lookup[i]['output']['file'], 'label': i} for i in identifiers]
    directory = root / '制作记录/对照/候选排序'
    directory.mkdir(parents=True, exist_ok=True)
    key = role + '-' + fingerprint([lookup[i]['output'] for i in identifiers])[:16]
    output = directory / (key + '.png')
    if not output.exists():
        board = comparison(directory, key, panels, kind='ranking')
    else:
        board = {'file': str(output), 'sha256': digest(output)}
    return {'file': workflow.local(root, output), 'sha256': board['sha256'], 'call_ids': identifiers}


def advance(root, role, flow=None, values=None, retry=False, mutate=True):
    flow = flow or read_flow(root)
    values = values or workflow.load_all(root)
    affected = [r for r in ROLES[:ROLES.index(role) + 1]
                if values[1]['stages'].get(r, {}).get('plan_requires_prepare')]
    if affected:
        first = affected[0]
        path = root / flow['stages'][first]['reprepare_input_file']
        return {'next_action': 'prepare', 'stage': first, 'config_file': str(path),
                'submit_command': submit_command('prepare', root, first, path),
                'instruction': '共享计划已更新，旧批准已失效；使用本配置重新prepare，不发旧请求、不重置累计次数。'}
    status = workflow.status(root, role, values)
    pending = [r for r in flow['requests'].values() if r['stage'] == role and r['state'] == 'awaiting_result']
    if pending:
        recipe = values[2]['stages'][role]['recipe']
        require(all(r['recipe_sha256'] == workflow.recipe_binding(recipe) for r in pending), '待执行请求的配方已变化')
        arguments = tool_arguments(root, recipe)
        count = len(pending)
        batch_instruction = ('本批仅一项，只调用一次；返回后直接receive，不再调用生图。'
                             if count == 1 else
                             f'本批{count}项同批并行发出，每项只调用一次；全部返回后一次receive，中间不看图、不登记单张、不排序。')
        return {'next_action': 'generate_parallel' if count > 1 else 'generate',
                'tool_call_count': count,
                'requests': [dict(r, arguments=arguments) for r in pending],
                'requests_file': str(requests_file(root, role, pending)),
                'receipt_config_file': str(receipt_config(root, role, pending)),
                'submit_command': submit_command('receive', root, role, receipt_config(root, role, pending)),
                'reference_coverage': flow['stages'][role]['reference_coverage'],
                'reference_packing': flow['stages'][role]['reference_packing'],
                'instruction': '只执行本批requests，原样使用arguments；已有真实工具返回则直接receive，恢复时不重发。'
                               '工具已成功返回但预览/显示或receive失败，不代表生成失败，不允许重调生图；保留原结果修复回执。'
                               '不要截断打印完整返回值而丢掉路径；原始文本回执或output_hint等路径字段完整保留，不打印base64。'
                               '不调用测试、探测、占位或无关提示词，不为确认工具是否可用额外生图。' + batch_instruction +
                               '当前调用上下文保存request_id与回执的对应，直接填写receipt_config_file；成功填原始result，失败填error。'
                               '编号在当前回执/调用上下文可取得时填真实tool_call_id，未提供则保留null，脚本记录编号不可得。'
                               '参数已保存到requests_file；不读取/列出聊天，不查目录、写正则或从文件名猜编号。'}
    choice = values[2]['stages'].get(role)
    action = status['next_action']
    if mutate and (action in ('generate_first', 'generate_extra') or retry and action == 'report_tool_failure'):
        budget = stage_budget(role, choice, values[3])
        count = min(status.get('remaining_calls', 1), choice['generation_limit'] - budget['call_count'])
        require(count > 0, '已到上限，先status选优')
        reserve(root, role, count, flow, values)
        return advance(root, role, flow, values, mutate=False)
    if action in ('compare_candidates', 'select_best_at_limit'):
        board = ranking_board(root, role, status['candidate_ids'], values) if mutate else flow['stages'][role].get('ranking_board')
        if mutate:
            flow['stages'][role]['ranking_board'] = board
            save(root, flow, values)
        return {'next_action': 'rank', 'stage': role, 'at_limit': action == 'select_best_at_limit',
                'ranking_board': board,
                **action_config(root, 'rank', role, board['sha256'],
                                {'preferred_call': '', 'reason': '', 'board_sha256': board['sha256']}, mutate),
                'instruction': '只看ranking_board，直接在config_file填写preferred_call和一句reason，执行submit_command；不找旧模板或重抄哈希。'}
    if action in ('self_check_first', 'self_check_selected'):
        packet = read(root / status['packet_file'])
        return {'next_action': 'self-check', 'stage': role,
                'call_id': packet['call_id'], 'review_token': status['packet_sha256'],
                **action_config(root, 'self-check', role, status['packet_sha256'],
                    {'call_id': packet['call_id'], 'review_token': status['packet_sha256'], 'result': 'pending',
                     'observation': '', 'viewed_evidence_ids': []}, mutate),
                'comparisons': [{'id': a['id'], 'file': str(root / a['file'])} for a in packet['evidence'] if a['kind'] == 'comparison'],
                'instruction': packet['maker_instructions']}
    if action == 'supplement_evidence' and flow['stages'][role].get('supplement'):
        from production_evidence import handoff
        ref = flow['stages'][role]['supplement']
        saved = read(root / ref['file'])
        if saved['packet_sha256'] == status['packet_sha256']:
            return handoff(root, status, saved, ref)
    if status.get('reviewer_dispatch_ready'):
        status['reviewer_handoff']['report_output_file'] = str((root / status['reviewer_handoff']['report_template_file']).with_name(
            role + '-' + read(root / status['packet_file'])['call_id'] + '-reviewer-output.json'))
        status['reviewer_handoff']['task'] += '填写报告模板后直接写report_output_file，只回复报告路径和结论；制作端不重抄JSON。'
        handoff = status['reviewer_handoff']
        handoff['check_report_command'] = submit_command('check-report', root, role, Path(handoff['report_output_file']))
        handoff['task'] += ('本次文件：packet_file=' + str(root / handoff['packet_file']) +
                            '；report_template_file=' + str(root / handoff['report_template_file']) +
                            '；report_output_file=' + handoff['report_output_file'] + '。')
        handoff['task'] += '写完后执行只读预检：' + handoff['check_report_command'] + '。report_ready才一次返回最终报告。'
    if action == 'continue':
        if role == 'front':
            from spatial_plan import valid, action as structure_action
            if not valid(root, flow, values):
                return structure_action(root, flow, values, mutate)
        following = ROLES.index(role) + 1
        status['next_action'] = 'prepare_next_stage' if following < len(ROLES) else 'deliver'
        status['next_stage'] = ROLES[following] if following < len(ROLES) else None
        status['read_now'] = ['references/spatial-logic.md'] if role == 'front' else []
        if status['next_stage'] and flow['stages'].get(status['next_stage'], {}).get('reprepare_input_file'):
            path = root / flow['stages'][status['next_stage']]['reprepare_input_file']
            status.update(config_file=str(path), submit_command=submit_command('prepare', root, status['next_stage'], path))
        elif status['next_stage']:
            following = status['next_stage']
            path = root / f'制作记录/阶段输入/{following}-prepare.json'
            references = {'front': [{'stage': 'head', 'role': '已采用的角色面部身份、头发颜色和眼部基准'}],
                          'back': [],
                          'left': [{'stage': 'front', 'role': '已采用的正面身份与当前视角可见服装连接'},
                                   {'stage': 'back', 'role': '已采用的后方结构与当前视角可见的连接遮挡'}]}[following]
            if mutate and not path.exists():
                workflow.write(path, {'prompt': {'operation': 'generate', 'identity': '',
                                                'references': references, 'critical_constraints': []}})
            if path.exists():
                status.update(config_file=str(path), submit_command=submit_command('prepare', root, following, path))
    return status


def comparisons_for_stage(stage_config, goals):
    keys = stage_config['source_keys']
    if stage_config.get('comparisons'):
        result = []
        for spec in stage_config['comparisons']:
            row = copy.deepcopy(spec)
            indices = row.pop('reference_indices')
            require(isinstance(indices, list) and all(type(i) is int and 1 <= i <= len(keys) for i in indices),
                    'comparisons.reference_indices须为本阶段有效参考编号列表')
            mapping = stage_config['review_reference_map']
            ids = list(dict.fromkeys('source_' + leaf for i in indices for leaf in mapping[keys[i - 1]]))
            require(bool(ids), 'comparisons.reference_indices不能为空')
            for pos in range(0, len(ids), 2):
                result.append({**row, 'reference_ids': ids[pos:pos + 2]})
        return result
    from review_references import groups
    return groups(goals)


def receive(root, role, config):
    strict(config, {'calls'}, {'calls'})
    flow, values = read_flow(root), workflow.load_all(root)
    pending = {r['request_id']: r for r in flow['requests'].values() if r['stage'] == role and r['state'] == 'awaiting_result'}
    calls = config['calls']
    require(isinstance(calls, list) and bool(calls) and len(calls) == len(pending)
            and all(isinstance(c, dict) for c in calls)
            and {c.get('request_id') for c in calls} == set(pending), '须一次登记本批全部真实结果，不能在两次追加中间插入操作')
    from tool_receipts import normalize, archive_output
    for c in calls:
        strict(c, {'request_id', 'tool_call_id', 'output', 'error', 'result'}, {'request_id'})
    calls = [normalize(root, c) for c in calls]
    lookup = {c['request_id']: c for c in calls}
    actual_ids = [c['tool_call_id'] for c in calls if c['tool_call_id']]
    require(len(actual_ids) == len(set(actual_ids)), '须提供互不重复的真实tool_call_id')
    used = {r['actual_tool_call_id'] for r in flow['requests'].values() if r['actual_tool_call_id']}
    require(not (set(actual_ids) & used), '真实工具调用id已登记，不能重复计数')
    output_files = [c['output'] for c in calls if c.get('output')]
    used_files = {read(root / f'制作记录/调用记录/{r["request_id"]}-receipt.json').get('output')
                  for r in flow['requests'].values() if r['state'] == 'recorded'}
    require(len(output_files) == len(set(output_files)) and not set(output_files) & used_files,
            '同一个工具输出文件已登记或被填入两项请求；核对当前批次回执，不猜配对或重复计数')
    for identifier, request in pending.items():
        c = lookup[identifier]
        require(bool(c.get('output')) != bool(c.get('error')), '每次调用须有output或error之一')
        require(isinstance(c.get('output'), str) if c.get('output') else isinstance(c.get('error'), (str, dict)),
                'output须为实际路径文本，error须为实际错误文本或对象')
        require(request['recipe_sha256'] == workflow.recipe_binding(values[2]['stages'][role]['recipe']), '请求配方已变化')
        if c.get('output'):
            with Image.open(workflow.source_path(root, c['output'])) as image:
                image.load()
    # Preflight the whole batch before writing any receipt or registering a candidate.
    for identifier, request in pending.items():
        c = lookup[identifier]
        archived = archive_output(root, c)
        receipt = root / f'制作记录/调用记录/{identifier}-receipt.json'
        workflow.write(receipt, {'schema_version': 2, 'request_id': identifier, 'actual_tool_call_id': c['tool_call_id'],
                                 'recipe_sha256': request['recipe_sha256'], 'received_at': utc_now(),
                                 'output': c.get('output'), 'error': c.get('error'),
                                 'tool_call_id_status': c['tool_call_id_status'],
                                 'raw_receipt': c['raw_receipt'], 'original_output': c.get('original_output'),
                                 'provenance': 'model_reported_tool_receipt; external_id_may_be_unavailable; not_independently_verified'})
        conf = {'id': identifier, 'recipe': values[2]['stages'][role]['recipe'], 'evidence_file': str(receipt),
                **({'output': archived} if c.get('output') else {'error': c['error']}),
                'comparisons': comparisons_for_stage(flow['stages'][role], values[0]['checks'][role]),
                'review_regions': flow['stages'][role]['review_regions']}
        # Recipe snapshots use paths relative to the production root.
        workflow.register(root, role, conf, values)
        request.update(state='recorded', actual_tool_call_id=c['tool_call_id'], call_id=identifier)
    workflow.save_all(root, values)
    save(root, flow, values)
    return advance(root, role, flow, values)


def simple_check(root, role, config):
    strict(config, {'call_id', 'review_token', 'result', 'observation', 'viewed_evidence_ids', 'issue_key'},
           {'call_id', 'review_token', 'result', 'observation', 'viewed_evidence_ids'})
    flow, values = read_flow(root), workflow.load_all(root)
    report = {'kind': 'self-check', 'reviewer': {'mode': 'self', 'model': 'inherited'},
              **{k: v for k, v in config.items() if k != 'review_token'}, 'packet_sha256': config['review_token']}
    workflow.record_review(root, role, report, values)
    workflow.save_all(root, values)
    save(root, flow, values)
    return advance(root, role, flow, values)


def check_report(root, role, config):
    """Read-only field/evidence preflight for the reviewer before its final handoff."""
    require(isinstance(config, dict) and config.get('kind', 'visual') == 'visual',
            'check-report只接收visual报告')
    from review_report import report_errors, production_input_errors, production_evidence_errors
    errors = production_input_errors(config) + production_evidence_errors(root, role, config, read_flow(root)) + report_errors(root, role, config, workflow.load_all(root))
    return {'next_action': 'correct_report_fields' if errors else 'report_ready',
            'field_errors': errors, 'visual_approval_recorded': False,
            'instruction': '按实际查看修正列出的字段；未看的证据须实际查看后才能填写。此命令只读，不记录视觉通过。' if errors
                           else '报告字段与引用就绪，一次向主模型返回最终报告路径和结论。主模型再执行review登记。'}


def review(root, role, config):
    require(isinstance(config, dict) and config.get('kind', 'visual') == 'visual',
            'review只接收visual报告；简评用self-check，排序用rank')
    flow, values = read_flow(root), workflow.load_all(root)
    if config.get('supplement_round') == 1:
        previous = values[2]['stages'][role]['reviews'].get(config.get('call_id'), {})
        expected = previous.get('reviewer', {}).get('agent_id')
        if expected and config.get('reviewer', {}).get('mode') == 'subagent':
            require(config['reviewer'].get('agent_id') == expected or bool(config.get('reviewer_replacement_reason')),
                    '补证交回原复核代理；不能复用时记录reviewer_replacement_reason')
    from review_report import report_errors, production_input_errors, production_evidence_errors
    errors = production_input_errors(config) + production_evidence_errors(root, role, config, flow) + report_errors(root, role, config, values)
    if errors:
        return {'next_action': 'correct_report_fields', 'field_errors': errors,
                'instruction': '只补列出的实际观察或记录字段，不生图、不改验收结论。'}
    workflow.record_review(root, role, config, values)
    workflow.save_all(root, values)
    save(root, flow, values)
    return advance(root, role, flow, values)


def rank(root, role, config):
    strict(config, {'preferred_call', 'reason', 'board_sha256'}, {'preferred_call', 'reason', 'board_sha256'})
    flow, values = read_flow(root), workflow.load_all(root)
    status = workflow.status(root, role, values)
    require(status['next_action'] in ('compare_candidates', 'select_best_at_limit'), '当前不是选优步骤')
    board = flow['stages'][role]['ranking_board']
    require(config['board_sha256'] == board['sha256'] == digest(root / board['file']), '所看的候选拼图已变化')
    all_groups = [h['selection'] for h in values[3]['history'] if h['stage'] == role] + [values[2]['stages'][role]]
    calls = {c['id']: c for g in all_groups for c in g['attempts'] if not c.get('error')}
    for identifier in board['call_ids']:
        output = calls[identifier]['output']
        require(digest(root / output['file']) == output['sha256'], '候选版本已变化')
    report = {'kind': 'limit-selection' if status['next_action'] == 'select_best_at_limit' else 'selection',
              'preferred_call': config['preferred_call'], 'selection_reason': nonempty(config['reason'], 'reason'),
              'viewed_call_ids': board['call_ids'], 'reviewer': {'mode': 'self', 'model': 'inherited'}}
    workflow.record_review(root, role, report, values)
    # Retain the exact board that the model declared viewing, without inventing image judgments.
    values[2]['stages'][role].setdefault('ranking_evidence', board)
    workflow.save_all(root, values)
    save(root, flow, values)
    return advance(root, role, flow, values)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('start', 'prepare', 'update-plan', 'record-structure', 'receive', 'self-check', 'check-report', 'review', 'supplement', 'rank', 'deliver', 'status'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--stage', choices=ROLES, default='head')
    parser.add_argument('--sources', type=Path)
    parser.add_argument('--destination', type=Path, help='Only at start, when explicitly specified by the user')
    parser.add_argument('--destination-user-quote', help='The user\'s exact destination request, only at start')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--config', type=Path)
    group.add_argument('--json')
    args = parser.parse_args()
    try:
        require(args.command == 'start' or (args.destination is None and args.destination_user_quote is None),
                '目的地仅允许start按用户原话指定，不能在其他阶段临时改路径')
        root = args.root.resolve()
        config_path = (args.config if args.config.is_absolute() else root / args.config) if args.config else None
        config = read(config_path) if config_path else json.loads(args.json) if args.json else None
        if args.command == 'start':
            require(args.sources is not None, 'start需要--sources')
            result = start(root, args.sources, args.destination, args.destination_user_quote)
        elif args.command == 'status':
            result = advance(root, args.stage, mutate=False)
        elif args.command == 'deliver':
            from production_delivery import deliver
            result = deliver(root, config or {})
        elif args.command == 'supplement':
            from production_evidence import supplement
            require(config is not None, 'supplement需要--config或--json')
            result = supplement(root, args.stage, config)
        elif args.command == 'record-structure':
            from spatial_plan import record
            require(config is not None, 'record-structure需要--config或--json')
            result = record(root, config)
        else:
            require(config is not None, args.command + '需要--config或--json')
            if args.command in ('prepare', 'update-plan'):
                result = {'prepare': prepare, 'update-plan': update_plan}[args.command](root, args.stage, config, config_path)
            else:
                result = {'receive': receive, 'self-check': simple_check,
                          'check-report': check_report, 'review': review, 'rank': rank}[args.command](root, args.stage, config)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 2 if result.get('field_errors') or result.get('errors') else 0
    except PermissionError as exc:
        retry_args = [args.command, '--root', str(root), '--stage', args.stage]
        if config_path:
            retry_args += ['--config', str(config_path)]
        elif args.json is not None:
            retry_args += ['--json', args.json]
        if args.sources is not None:
            retry_args += ['--sources', str(args.sources.resolve())]
        if args.destination is not None:
            retry_args += ['--destination', str(args.destination.resolve()), '--destination-user-quote', args.destination_user_quote]
        print(json.dumps({'next_action': 'request_file_access', 'errors': [str(exc)], 'file': exc.filename,
                          'retry_command': invocation(retry_args),
                          'instruction': '这是文件权限问题；用当前工具的权限申请机制原样执行retry_command，目标仍是素材目录。'
                                         '不能改destination、编造用户原话、复制到outputs或宣称交付完成。权限获准后原样重试，脚本续传本次部分复制。'
                                         '确实被拒绝则如实报告尚未交付，不把工作副本当成成品入口。',
                          'script_source_required': False}, ensure_ascii=False))
        return 2
    except (OSError, ValueError, TypeError, KeyError, IndexError, subprocess.SubprocessError) as exc:
        details = {}
        if hasattr(exc, 'field_errors'):
            details['field_errors'] = exc.field_errors
            plan_path = config_path
            if not config or 'materials' not in config:
                flow = read_flow(root)
                plan_path = Path(flow.get('plan_input_file', root / '制作记录/阶段输入/首次准备.json'))
            details.update(plan_config_file=str(plan_path) if plan_path else None,
                instruction='一次修完field_errors。首次含materials的prepare直接重试；修改已保存选材文件后先update-plan --stage当前阶段 --config该文件，再按返回推进。仅编辑文件不更新已保存计划。')
            if plan_path and (not config or 'materials' not in config):
                details['plan_submit_command'] = submit_command('update-plan', root, args.stage, plan_path)
        print(json.dumps({'next_action': 'correct_input', 'errors': [str(exc)], **details,
                          'interface_document': 'references/production-api.md', 'script_source_required': False}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
