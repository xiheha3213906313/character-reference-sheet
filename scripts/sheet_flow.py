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

IMAGES = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}
CHARACTER_FIELDS = {'name', 'height_cm', 'age_years', 'appearance', 'outfit', 'outfit_name', 'setting_basis'}
MATERIAL_VIEWS = {'front', 'back', 'side', 'rear_oblique', 'detail', 'unclear'}
REAR_VIEWS = {'back', 'rear_oblique'}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def nonempty(value, label):
    require(isinstance(value, str) and bool(value.strip()), label + '须为非空文本')
    return value.strip()


def strict(value, allowed, required=()):
    require(isinstance(value, dict), '输入须为JSON对象')
    require(set(value) <= set(allowed), '未知字段：' + ', '.join(sorted(set(value) - set(allowed))))
    require(set(required) <= set(value), '缺少字段：' + ', '.join(sorted(set(required) - set(value))))


def start(root, source_directory):
    require(not (root / FLOW).exists(), '项目已开始；用status继续，不能覆盖来源清单')
    require(not any((root / f).exists() for f in (workflow.BRIEF, workflow.STATE, workflow.SELECTION)),
            '此目录已有制作记录；新接口请用独立项目目录，不改写已有任务')
    source_directory = Path(source_directory).resolve()
    require(source_directory.is_dir(), '素材目录不存在')
    files = sorted(p for p in source_directory.iterdir() if p.is_file() and p.suffix.lower() in IMAGES)
    require(bool(files), '素材目录没有支持的图片；只清点该目录，不递归混入旧交付')
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
            'sources': sources, 'materials': [], 'character': None, 'stages': {}, 'requests': {}, 'delivery': None}
    workflow.write(root / FLOW, flow)
    export(root, flow)
    worksheet = {'character': {k: None if k in ('height_cm', 'age_years') else '' for k in sorted(CHARACTER_FIELDS)},
                 'materials': [{'source_id': s['source_id'], 'views': [], 'observation': '', 'quality': '',
                                'decision': '', 'selection_reason': '', 'uses': []} for s in sources],
                 'prompt': {'operation': 'generate', 'identity': '', 'references': [], 'critical_constraints': []}}
    workflow.write(root / '制作记录/阶段输入/首次准备.json', worksheet)
    return {'next_action': 'analyze_sources_then_prepare_head', 'sources': sources,
            'input_file': str(root / '制作记录/阶段输入/首次准备.json'),
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
    result = [{'id': 'identity', 'group': 'identity', 'target': spec['identity'], 'source_ids': list(dict.fromkeys(keys))}]
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
                       'target': target, 'source_ids': list(dict.fromkeys(keys[i - 1] for i in c['source_indices']))})
    common = prompt_templates.common(role, spec.get('user_overrides', {}), spec.get('background_mode', 'transparent'))
    look = {'color_style': '保留有效原图的角色本色、材料与画风，排除成像污染。',
            'lighting': common['lighting'], 'framing': FRAMING[role],
            'head_pose': '头部自然端正，不仰头，面部朝前。', 'gaze': '自然正视镜头。',
            'expression': common['expression']}
    ratio_override = spec.get('user_overrides', {}).get('aspect_ratio')
    if ratio_override:
        look['framing'] = look['framing'].replace('3:4' if role == 'head' else '9:16', ratio_override['value'])
    for aspect in ('color_style', 'lighting', 'framing', 'head_pose', 'gaze', 'expression'):
        if role in ('back', 'left') and aspect in ('head_pose', 'gaze', 'expression'):
            continue
        goal = {'id': 'look_' + aspect, 'group': 'look', 'aspect': aspect, 'target': look[aspect],
                'source_ids': list(dict.fromkeys(keys))}
        override_key = {'framing': 'framing_required'}.get(aspect, aspect)
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
                       'source_ids': list(dict.fromkeys(keys))})
    return result


def prepare(root, role, config):
    strict(config, {'character', 'materials', 'prompt', 'review_regions', 'comparisons', 'reviewer_policy',
                    'generation_limit', 'required_calls', 'user_override_reason', 'strategy_change',
                    'max_reference_images', 'tool', 'parameters'}, {'prompt'})
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
    values = workflow.load_all(root)
    from material_plan import inject
    from reference_packing import substitute, fit
    preferred, replacements = substitute(config['prompt'], staged['materials'])
    planned, coverage = inject(role, preferred, staged)
    semantic_spec, sources, keys = resolve_prompt(root, role, planned, staged, values)
    maximum = config.get('max_reference_images', 5)
    build_prompt.render(semantic_spec)  # Validate each original role before any merging.
    goals = targets_from_prompt(role, semantic_spec, keys)
    spec, reference_map, packing = fit(root, semantic_spec, keys, maximum)
    packing['source_replacements'] = replacements
    prompt, _ = build_prompt.render(spec)
    parameters = config.get('parameters', {})
    require(isinstance(parameters, dict), 'parameters须为对象')
    require(not (set(parameters) & {'prompt', 'referenced_image_paths', 'num_last_images_to_include'}),
            'parameters不能覆盖已编译提示词或有序参考')
    if config.get('tool', 'image_gen.imagegen') == 'image_gen.imagegen':
        strict(parameters, {'transparent_background'})
    background = spec.get('background_mode', 'transparent') == 'transparent'
    require('transparent_background' not in parameters or type(parameters['transparent_background']) is bool
            and parameters['transparent_background'] == background, 'transparent_background须与prompt.background_mode一致')
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
        completed = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).with_name('build_prompt.py')),
                                    str(spec_file), '--output', str(prompt_file)], capture_output=True, encoding='utf-8')
        require(completed.returncode == 0, completed.stderr)
    require(prompt_file.read_text(encoding='utf-8') == prompt and metadata.is_file(), '编译提示词或元数据已变化')
    recipe = {'tool': config.get('tool', 'image_gen.imagegen'), 'parameters': parameters,
              'prompt_file': str(prompt_file),
              'inputs': [{'file': r['image'], 'purpose': r['role']} for r in spec['references']]}
    low = {'sources': sources, 'checks': {role: goals}, 'recipe': recipe}
    for k in ('reviewer_policy', 'generation_limit', 'required_calls', 'user_override_reason', 'strategy_change'):
        if k in config:
            low[k] = config[k]
    workflow.prepare(root, role, low, values)
    workflow.save_all(root, values)
    staged['stages'][role] = {'input_file': workflow.local(root, source_config),
                             'prompt_metadata': {'file': workflow.local(root, metadata), 'sha256': digest(metadata)},
                             'review_regions': config.get('review_regions', []),
                             'comparisons': config.get('comparisons'), 'source_keys': keys}
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
    return requests


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
        board = comparison(directory, key, panels)
    else:
        board = {'file': str(output), 'sha256': digest(output)}
    return {'file': workflow.local(root, output), 'sha256': board['sha256'], 'call_ids': identifiers}


def advance(root, role, flow=None, values=None, retry=False, mutate=True):
    flow = flow or read_flow(root)
    values = values or workflow.load_all(root)
    status = workflow.status(root, role, values)
    pending = [r for r in flow['requests'].values() if r['stage'] == role and r['state'] == 'awaiting_result']
    if pending:
        recipe = values[2]['stages'][role]['recipe']
        require(all(r['recipe_sha256'] == workflow.recipe_binding(recipe) for r in pending), '待执行请求的配方已变化')
        arguments = {'prompt': (root / recipe['prompt_file']).read_text(encoding='utf-8'),
                     'referenced_image_paths': [str(root / ref['file']) for ref in recipe['inputs']], **recipe['parameters']}
        return {'next_action': 'generate_parallel' if len(pending) > 1 else 'generate',
                'requests': [dict(r, arguments=arguments) for r in pending],
                'reference_coverage': flow['stages'][role]['reference_coverage'],
                'reference_packing': flow['stages'][role]['reference_packing'],
                'instruction': '尚未调用才原样使用arguments；已有真实工具返回则直接receive，恢复时不重发。'
                               '两项同批并行发出，全部返回后一次receive；中间不看图、不登记单张、不排序。'}
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
                'ranking_board': board, 'instruction': '只看这张候选拼图，提交preferred_call和一句reason；落选图不逐项验收。'}
    if action in ('self_check_first', 'self_check_selected'):
        packet = read(root / status['packet_file'])
        return {'next_action': 'self-check', 'stage': role,
                'call_id': packet['call_id'], 'review_token': status['packet_sha256'],
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
    if action == 'continue':
        following = ROLES.index(role) + 1
        status['next_action'] = 'prepare_next_stage' if following < len(ROLES) else 'deliver'
        status['next_stage'] = ROLES[following] if following < len(ROLES) else None
        status['read_now'] = ['references/spatial-logic.md'] if role == 'front' else []
    return status


def comparisons_for_stage(stage_config, goals):
    keys = stage_config['source_keys']
    if stage_config.get('comparisons'):
        result = []
        for spec in stage_config['comparisons']:
            row = copy.deepcopy(spec)
            indices = row.pop('reference_indices')
            row['reference_ids'] = ['source_' + keys[i - 1] for i in indices]
            result.append(row)
        return result
    # Group by semantic evidence, not by incidental file ordering. Deduplicate identical panels.
    groups = {}
    for goal in goals:
        if goal['group'] not in ('identity', 'design'):
            continue
        ids = tuple('source_' + k for k in goal['source_ids'])
        for pos in range(0, len(ids), 2):
            group = ids[pos:pos + 2]
            groups.setdefault(group, []).append(goal['id'])
    return [{'reference_ids': list(ids), 'check_ids': check_ids} for ids, check_ids in groups.items()]


def receive(root, role, config):
    strict(config, {'calls'}, {'calls'})
    flow, values = read_flow(root), workflow.load_all(root)
    pending = {r['request_id']: r for r in flow['requests'].values() if r['stage'] == role and r['state'] == 'awaiting_result'}
    calls = config['calls']
    require(isinstance(calls, list) and bool(calls) and len(calls) == len(pending)
            and {c.get('request_id') for c in calls} == set(pending), '须一次登记本批全部真实结果，不能在两次追加中间插入操作')
    lookup = {c['request_id']: c for c in calls}
    actual_ids = [c.get('tool_call_id') for c in calls]
    require(all(isinstance(i, str) and i.strip() for i in actual_ids) and len(actual_ids) == len(set(actual_ids)), '须提供互不重复的真实tool_call_id')
    used = {r['actual_tool_call_id'] for r in flow['requests'].values() if r['actual_tool_call_id']}
    require(not (set(actual_ids) & used), '真实工具调用id已登记，不能重复计数')
    for identifier, request in pending.items():
        c = lookup[identifier]
        strict(c, {'request_id', 'tool_call_id', 'output', 'error'}, {'request_id', 'tool_call_id'})
        require(bool(c.get('output')) != bool(c.get('error')), '每次调用须有output或error之一')
        require(isinstance(c.get('output'), str) if c.get('output') else isinstance(c.get('error'), (str, dict)),
                'output须为实际路径文本，error须为实际错误文本或对象')
        require(request['recipe_sha256'] == workflow.recipe_binding(values[2]['stages'][role]['recipe']), '请求配方已变化')
        if c.get('output'):
            with Image.open(workflow.source_path(root, c['output'])) as image:
                require(image.format == 'PNG', '工具输出须为实际PNG')
                image.load()
    # Preflight the whole batch before writing any receipt or registering a candidate.
    for identifier, request in pending.items():
        c = lookup[identifier]
        receipt = root / f'制作记录/调用记录/{identifier}-receipt.json'
        workflow.write(receipt, {'schema_version': 2, 'request_id': identifier, 'actual_tool_call_id': c['tool_call_id'],
                                 'recipe_sha256': request['recipe_sha256'], 'received_at': utc_now(),
                                 'output': c.get('output'), 'error': c.get('error'),
                                 'provenance': 'model_reported_real_tool_result; verify against chat tool log'})
        conf = {'id': identifier, 'recipe': values[2]['stages'][role]['recipe'], 'evidence_file': str(receipt),
                **({'output': c['output']} if c.get('output') else {'error': c['error']}),
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
    from review_report import report_errors, production_input_errors
    errors = production_input_errors(config) + report_errors(root, role, config, values)
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
    parser.add_argument('command', choices=('start', 'prepare', 'receive', 'self-check', 'review', 'supplement', 'rank', 'deliver', 'status'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--stage', choices=ROLES, default='head')
    parser.add_argument('--sources', type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--config', type=Path)
    group.add_argument('--json')
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        config = read(args.config) if args.config else json.loads(args.json) if args.json else None
        if args.command == 'start':
            require(args.sources is not None, 'start需要--sources')
            result = start(root, args.sources)
        elif args.command == 'status':
            result = advance(root, args.stage, mutate=False)
        elif args.command == 'deliver':
            from production_delivery import deliver
            result = deliver(root, config or {})
        elif args.command == 'supplement':
            from production_evidence import supplement
            require(config is not None, 'supplement需要--config或--json')
            result = supplement(root, args.stage, config)
        else:
            require(config is not None, args.command + '需要--config或--json')
            result = {'prepare': prepare, 'receive': receive, 'self-check': simple_check,
                      'review': review, 'rank': rank}[args.command](root, args.stage, config)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 2 if result.get('field_errors') or result.get('errors') else 0
    except (OSError, ValueError, TypeError, KeyError, IndexError, subprocess.SubprocessError) as exc:
        print(json.dumps({'next_action': 'correct_input', 'errors': [str(exc)],
                          'interface_document': 'references/production-api.md', 'script_source_required': False}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
