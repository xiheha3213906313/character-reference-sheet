"""Prepare/register/record-review/status for v2 reviews; never generates or judges pictures."""
import argparse
import copy
import json
from pathlib import Path
import re
import shutil
import sys

from review_v2 import (BRIEF, STATE, SELECTION, WORKFLOW, DEPENDENCIES, PURPOSES, check_v2,
                       digest, fingerprint, read, recipe_binding, require, reviewer_valid,
                       selection_binding, target_binding, validate_visual)
from review_v2 import image_bytes_for_review
from review_v2 import processing_binding
from review_v2 import validate_review_integrity
from review_v2 import ACCEPTANCE_STANDARD, maker_valid, stage_budget, validate_self_check, validate_calls
from check_delivery import ROLES, within
from review_policy import DEFAULT_GENERATION_LIMIT, REVIEWER_TASK, normalize_targets, primary_kinds, check_quality_observations, check_self_observations
from review_assets import comparison, pixel_equivalence, native_regions, background_candidates, mark_background_samples
from review_v2 import dependency_binding, visual_binding, validate_inherited_visual
from review_v2 import approved_at_limit

LABELS = {'head': '特写', 'front': '正面', 'back': '背面', 'left': '左侧面'}


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temp.replace(path)


def local(root, path):
    return Path(path).resolve().relative_to(root.resolve()).as_posix()


def source_path(root, value):
    require(isinstance(value, str) and bool(value.strip()), '缺少实际文件路径')
    path = Path(value)
    return path.resolve() if path.is_absolute() else within(root, value)


def snapshot(root, value, category):
    source = source_path(root, value)
    sha = digest(source)
    suffix = source.suffix.lower() or '.bin'
    destination = within(root, f'制作记录/{category}/{sha}{suffix}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        require(digest(destination) == sha, '快照文件已被修改')
    else:
        shutil.copyfile(source, destination)
    return {'file': local(root, destination), 'sha256': sha}


def recipe_snapshot(root, config):
    require(isinstance(config, dict), '须提供实际调用recipe')
    recipe = {'tool': config.get('tool'), 'parameters': config.get('parameters')}
    require(bool(recipe['tool']) and isinstance(recipe['parameters'], dict), '缺少实际tool/parameters')
    prompt = snapshot(root, config.get('prompt_file'), '提示词')
    require(bool(within(root, prompt['file']).read_text(encoding='utf-8-sig').strip()), '实际提示词为空')
    recipe.update(prompt_file=prompt['file'], prompt_sha256=prompt['sha256'])
    require(isinstance(config.get('inputs'), list) and bool(config['inputs']), '缺少实际有序输入')
    recipe['inputs'] = []
    for entry in config['inputs']:
        require(isinstance(entry, dict) and bool(entry.get('purpose')), '输入须明确用途')
        recipe['inputs'].append(dict(snapshot(root, entry.get('file'), '参考'), purpose=entry['purpose']))
    return recipe


def load_all(root):
    files = [within(root, name) for name in (BRIEF, STATE, SELECTION, WORKFLOW)]
    defaults = [dict(schema_version=2, rules_version=2, scope='full_sheet', task_mode='production', sources={}, checks={}),
                dict(schema_version=2, rules_version=2, brief_file=BRIEF, selection_file=SELECTION, stages={}),
                dict(schema_version=2, rules_version=2, stages={}),
                dict(schema_version=2, event=0, history=[], failures={}, strategy_changes={})]
    values = [read(path) if path.exists() else default for path, default in zip(files, defaults)]
    require(all(v.get('schema_version') == 2 for v in values), '仅支持schema_version:2的当前流程记录')
    return values


def save_all(root, values):
    # State is written last: interrupted writes cannot silently approve mismatched selections.
    for index in (0, 2, 3, 1):
        write(within(root, (BRIEF, STATE, SELECTION, WORKFLOW)[index]), values[index])


def next_event(workflow):
    workflow['event'] += 1
    return workflow['event']


def used_ids(role, choice, workflow):
    stages = [choice] + [entry['selection'] for entry in workflow['history'] if entry['stage'] == role]
    return {call['id'] for data in stages for call in data.get('attempts', []) + data.get('processing', [])}


def dependencies(root, state, role, brief):
    if brief.get('scope') == 'single_stage' or not DEPENDENCIES[role]:
        return {}
    for up in DEPENDENCIES[role]:
        require(state.get('stages', {}).get(up, {}).get('target_sha256') == target_binding(brief, up),
                f'{up}目标或共享来源已变化，先重新验收上游')
    result = check_v2(state, root, before=role)
    require(result['downstream_ready'], '下游前置检查失败：' + '；'.join(result['errors']))
    return {up: state['stages'][up].get('dependency_binding_sha256', state['stages'][up]['binding_sha256'])
            for up in DEPENDENCIES[role]}


def prepare(root, role, config, values):
    brief, state, selection, workflow = values
    require(config.get('task_mode', brief['task_mode']) in ('production', 'existing_review'), '未知task_mode')
    if selection['stages']:
        require(config.get('task_mode', brief['task_mode']) == brief['task_mode'], '已有制作不能改task_mode')
    brief['task_mode'] = config.get('task_mode', brief['task_mode'])
    brief['scope'] = config.get('scope', brief['scope'])
    require(brief['scope'] in ('full_sheet', 'single_stage'), '未知scope')
    for key, value in config.get('sources', {}).items():
        brief['sources'][key] = snapshot(root, value.get('file'), '参考')
    for key, goals in config.get('checks', {}).items():
        require(key in ROLES, '未知目标视图')
        brief['checks'][key] = normalize_targets(key, goals)
    target = target_binding(brief, role)
    deps = dependencies(root, state, role, brief)
    old = selection['stages'].get(role)
    generation_limit = config.get('generation_limit', (old or {}).get('generation_limit', DEFAULT_GENERATION_LIMIT))
    require(type(generation_limit) is int and generation_limit > 0, 'generation_limit须为正整数，默认6次')
    if old:
        require(generation_limit == old.get('generation_limit'), '已开始的阶段不能临时改生成上限')
    recipe = recipe_snapshot(root, config['recipe']) if 'recipe' in config else (old or {}).get('recipe')
    require(recipe is not None or brief['task_mode'] == 'existing_review', '首次制作prepare须提供recipe')
    policy = config.get('reviewer_policy', (old or {}).get('reviewer_policy', {'mode': 'prefer_subagent', 'model': None}))
    require(policy.get('mode') in ('prefer_subagent', 'self'), '复核策略须为prefer_subagent或self')
    require(policy.get('mode') != 'self' or bool(policy.get('user_override_reason')), '禁用子代理须记录用户要求')
    count = config.get('required_calls', (old or {}).get('required_calls', 3)) if brief['task_mode'] == 'production' else 1
    override = config.get('user_override_reason', (old or {}).get('user_override_reason'))
    require(type(count) is int and 1 <= count <= 3 and (count == 3 or bool(override) or brief['task_mode'] == 'existing_review'), '候选数量例外须有用户要求')
    changed = old and (recipe != old['recipe'] or target != state['stages'].get(role, {}).get('target_sha256')
                       or deps != state['stages'].get(role, {}).get('dependencies') or policy != old['reviewer_policy'])
    if old and old.get('baseline_call'):
        require(count == old['required_calls'] and override == old.get('user_override_reason'), '批准首张后不能临时改组选优次数')
    if changed:
        workflow['history'].append({'stage': role, 'reason': '目标、输入、策略或上游变化', 'selection': old})
        old = None
    if not old:
        old = {'recipe': recipe, 'attempts': [], 'reviews': {}, 'baseline_call': None, 'selected_call': None,
               'required_calls': count, 'user_override_reason': override, 'reviewer_policy': policy,
               'generation_limit': generation_limit}
        selection['stages'][role] = old
        state['stages'][role] = {'decision': 'unreviewed', 'target_sha256': target, 'dependencies': deps,
                                 'reviewer_policy': policy}
    elif not old.get('baseline_call'):
        old['required_calls'], old['user_override_reason'] = count, override
    change = config.get('strategy_change')
    if change:
        key = f'{role}/{change["issue_key"]}'
        require(workflow['failures'].get(key, {}).get('count', 0) >= 3, '策略诊断应绑定已累计三次的同一问题')
        require(bool(change.get('diagnosis')) and bool(change.get('change')), '须明确诊断与具体策略变化')
        require(key not in workflow['strategy_changes'], '新策略已尝试，不再重置失败计数')
        workflow['strategy_changes'][key] = dict(change, event=next_event(workflow),
                                                failures_before=workflow['failures'][key]['count'])
    brief.setdefault('skill_revision', fingerprint({name: digest(Path(__file__).with_name(name)) for name in
                                                   ('review_gate.py', 'review_v2.py', 'review_policy.py', 'review_workflow.py')}))


def retry_context(role, choice, workflow):
    stages = [entry['selection'] for entry in workflow.get('history', []) if entry['stage'] == role] + [choice]
    for previous in reversed(stages):
        for attempt in reversed(previous.get('attempts', [])):
            review = previous.get('reviews', {}).get(attempt['id'], {})
            if review.get('decision') == 'rejected':
                return {'mode': 'followup', 'agent_id': review.get('reviewer', {}).get('agent_id'),
                        'issues': [{key: item.get(key) for key in ('id', 'issue_key', 'reference_observation', 'candidate_observation')}
                                   for item in review['checks'] if item['result'] == 'fail'],
                        'instruction': '交回本阶段原复核子代理。先复查上次问题，并看新图整体、头发、面部和明显新增错误；'
                                       '不重复展开全部旧证据。饰品错位、长度等就当这样设计，关系合理即通过；'
                                       '不要求精确还原，明显画质错误仍拒绝。'}
    return {'mode': 'initial'}


def evidence_packet(root, role, call, brief, deps, policy, extra_evidence=None, review_context=None, minimal=False):
    from PIL import Image
    image_path = within(root, call['output']['file'])
    directory = within(root, f'制作记录/对照/{role}-{call["id"]}')
    directory.mkdir(parents=True, exist_ok=True)
    artifacts = []

    def add(identifier, path, kind, **kwargs):
        artifacts.append(dict(id=identifier, file=local(root, path), sha256=digest(path), kind=kind, **kwargs))

    shutil.copyfile(image_path, directory / 'whole.png')
    add('whole', directory / 'whole.png', 'whole')
    with Image.open(image_path) as source:
        rgba = source.convert('RGBA')
        reduced = rgba.copy()
        reduced.thumbnail((768, 768), Image.Resampling.LANCZOS)
        reduced.save(directory / 'reduced.png')
        add('reduced', directory / 'reduced.png', 'reduced')
        for index, region in enumerate([] if minimal else native_regions(rgba, role), 1):
            path = directory / f'native-{index:03d}.png'
            rgba.crop(region['crop']).save(path)
            add(f'native_{index:03d}', path, 'native', **region)
        samples = background_candidates(rgba)
        for identifier, color in [('light', (246, 246, 246)), ('dark', (54, 55, 62))]:
            canvas = Image.new('RGB', rgba.size, color)
            canvas.paste(rgba, mask=rgba.getchannel('A'))
            mark_background_samples(canvas, samples)
            canvas.save(directory / f'{identifier}.png')
            add(identifier, directory / f'{identifier}.png', 'background', background=identifier)
    used = set() if minimal else {key for goal in brief['checks'][role] for key in goal['source_ids']}
    for key in sorted(used):
        path = within(root, brief['sources'][key]['file'])
        if path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.bmp'):
            source_sha = digest(path)
            diagnostic = within(root, f'制作记录/对照/来源/{source_sha}.png')
            diagnostic.parent.mkdir(parents=True, exist_ok=True)
            cache_record = diagnostic.with_suffix('.png.json')
            try:
                cached = read(cache_record) if cache_record.is_file() else {}
            except (OSError, ValueError):
                cached = {}
            if not (diagnostic.is_file() and cached.get('source_sha256') == source_sha
                    and cached.get('output_sha256') == digest(diagnostic)):
                with Image.open(path) as image:
                    image.convert('RGBA').save(diagnostic)
                write(cache_record, {'source_sha256': source_sha, 'output_sha256': digest(diagnostic)})
            add('source_' + key, diagnostic, 'source', source_id=key, source_sha256=source_sha)
    for up in ([] if minimal else deps):
        if role == 'back' and up == 'front' and any(g['id'] == 'design_rear_reference' for g in brief['checks'][role]):
            # The two explicit source inputs already carry the mask/material role split.
            continue
        upstream = read(within(root, STATE))['stages'][up]
        path = directory / f'upstream-{up}.png'
        path.write_bytes(image_bytes_for_review(upstream, root, up))
        add('upstream_' + up, path, 'upstream', stage=up)
    for spec in extra_evidence or []:
        snap = snapshot(root, spec['file'], '对照/补证')
        require(spec.get('kind') in ('whole', 'native', 'reduced', 'background', 'source', 'upstream'), '补证种类无效')
        require(spec.get('id') not in {a['id'] for a in artifacts}, '补证id重复')
        artifacts.append(dict(spec, **snap))
    for region in call.get('review_regions', []):
        identifier = region['id']
        require(bool(re.fullmatch(r'[A-Za-z0-9_-]{1,60}', identifier)), '局部id无效')
        require('region_' + identifier not in {a['id'] for a in artifacts}, '局部id重复')
        from compose_review import load_panel
        pixels, info = load_panel(image_path, region['crop'])
        path = directory / ('region-' + identifier + '.png')
        pixels.save(path)
        add('region_' + identifier, path, 'native', crop=info['crop'], region=identifier)
    references, seen = [], set()
    for artifact in artifacts:
        if artifact['kind'] in ('source', 'upstream') and artifact['sha256'] not in seen:
            seen.add(artifact['sha256'])
            references.append(artifact)
    groups = call.get('comparisons') or [{'reference_ids': [a['id'] for a in references[i:i + 2]]}
                                        for i in range(0, len(references), 2)]
    for index, spec in enumerate(groups, 1):
        lookup = {a['id']: a for a in artifacts}
        ids = spec['reference_ids']
        require(isinstance(ids, list) and 1 <= len(ids) <= 2 and all(i in lookup for i in ids), '对照需一至两项真实参考id')
        panels = [{'file': within(root, lookup[i]['file']), 'label': i,
                   'crop': spec.get('reference_crops', {}).get(i)} for i in ids]
        panels.append({'file': directory / 'whole.png', 'label': '候选', 'crop': spec.get('candidate_crop')})
        board = comparison(directory, f'compare_{index:03d}', panels,
                           check_ids=spec.get('check_ids', []),
                           source_ids=[lookup[i].get('source_id') for i in ids if lookup[i].get('source_id')],
                           upstream_stages=[lookup[i].get('stage') for i in ids if lookup[i].get('stage')])
        board['panels'] = [{'file': local(root, p['file']), 'sha256': digest(p['file'])} for p in panels]
        board['file'], board['layout_record'] = local(root, board['file']), local(root, board['layout_record'])
        artifacts.append(board)
    lookup = {a['id']: a for a in artifacts}
    board_panels = [{'file': within(root, lookup[i]['file']), 'label': i} for i in ('light', 'dark')]
    board = comparison(directory, 'alpha_compare', board_panels, kind='background', backgrounds=['light', 'dark'])
    board['panels'] = [{'file': local(root, p['file']), 'sha256': digest(p['file'])} for p in board_panels]
    board['file'], board['layout_record'] = local(root, board['file']), local(root, board['layout_record'])
    artifacts.append(board)
    with Image.open(image_path) as output:
        rgba = output.convert('RGBA')
        transparent = rgba.getchannel('A').getextrema()[0] == 0
    params = call.get('recipe_parameters', {})
    background_policy = ({'mode': 'transparent', 'alpha_source': 'model' if params.get('transparent_background') is True else None}
                         if transparent else {'mode': 'near_white_rgb_fallback', 'alpha_limitation': 'output',
                                              'reason': '实际PNG输出没有透明空白；按RGB输出核验'})
    return {'schema_version': 2, 'stage': role, 'call_id': call['id'],
            'candidate_sha256': call['output']['sha256'], 'target_sha256': target_binding(brief, role),
            'dependencies': deps, 'reviewer_policy': policy, 'checks': brief['checks'][role],
            'acceptance_standard': ACCEPTANCE_STANDARD, 'review_context': review_context or {'mode': 'initial'},
            'packet_role': 'candidate_evidence', 'reviewer_role': 'visual_reviewer', 'background_policy': background_policy,
            'dispatch_prerequisite': 'valid_maker_self_check',
            'maker_instructions': '制作模型只看参考—候选拼图，简单检查取景、身份造型、头发面部和服装位置的明显错误；'
                                  '用一两句实际观察填写self-check-template，不展开全流程自评。自评失败自行修复；'
                                  'receive已一次生成全部必要复核图片；制作端仍只看这些对照拼图。'
                                  '自评通过且接口返回reviewer_dispatch_ready:true后，才启动或联系本阶段复核代理，不额外运行status。',
            'background_sample_candidates': samples, 'self_check_fields': ['observation', 'viewed_evidence_ids'],
            'reviewer_instructions': '所有证据已由receive准备好，直接查看，不自行重新裁图、拼图或生成背景。'
                                     '只看必要拼图和原生局部，返回模板中的实际观察与结论；不要运行哈希/采像素命令、排序、生图或打包。'
                                     '无需打开全部文件；拼图看清后不再打开其源文件。原生质量和缩小质量分开看。'
                                     '背景只开alpha_compare，图中编号对应background_sample_candidates顺序；确认空白点，无需检查坐标像素。'
                                     '非空或不确定的点保留false即可，脚本只使用明确确认的空白点；不再开light/dark。'
                                     '原生局部按region选择，不是必看清单；身体比例只是定位辅助。'
                                     '服装比较肩/颈根/上臂的相对位置和露肤范围，不能只核对颜色和款式。',
            'review_sequence': [{'group': 'comparison', 'evidence_ids': [a['id'] for a in artifacts if a['kind'] == 'comparison']},
                                {'group': 'native_quality', 'choose_relevant_only': True,
                                 'evidence_ids': [a['id'] for a in artifacts if a['kind'] == 'native']},
                                {'group': 'reduced_quality', 'evidence_ids': ['reduced']},
                                {'group': 'background', 'evidence_ids': ['alpha_compare']}],
            'sources': {key: brief['sources'][key] for key in sorted(used)}, 'evidence': artifacts}


def store_packet(root, role, call, packet):
    path = within(root, f'制作记录/复核任务/{role}-{call["id"]}.json')
    write(path, packet)
    call['packet_file'], call['packet_sha256'] = local(root, path), digest(path)
    if call.get('phase'):
        write(path.with_name(path.stem + '-self-check-template.json'), {
            'kind': 'self-check', 'call_id': call['id'], 'packet_sha256': call['packet_sha256'],
            'reviewer': {'mode': 'self', 'model': 'inherited'}, 'result': 'pending',
            'viewed_evidence_ids': [], 'observation': ''})
    store_visual_template(root, call, packet)


def store_visual_template(root, call, packet):
    path = within(root, call['packet_file'])
    policy = packet['reviewer_policy']
    reviewer = ({'mode': 'self', 'model': policy.get('model') or 'inherited', 'reason_code': 'user_disabled',
                 'reason': policy.get('user_override_reason', '')} if policy['mode'] == 'self' else
                {'mode': 'subagent', 'model': policy.get('model') or 'inherited',
                 'agent_id': packet.get('review_context', {}).get('agent_id') or ''})
    template = {'kind': 'visual', 'call_id': call['id'], 'packet_sha256': call['packet_sha256'],
                'reviewer': reviewer, 'viewed_evidence_ids': [],
                'checks': [{'id': goal['id'], 'result': 'pending', 'reference_observation': '',
                            'candidate_observation': '', 'comparison_basis': '', 'evidence_ids': [],
                            **({'empty_background_samples': packet['background_sample_candidates']} if goal['group'] == 'background' else {})}
                           for goal in packet['checks']]}
    template['background_policy'] = packet['background_policy']
    template['quality_observations'] = {'hair': '', 'face': ''}
    write(path.with_name(path.stem + '-report-template.json'), template)


def review_dispatch(root, call, processing=False):
    """Prepare a blind handoff only after a valid maker check; never calls an agent."""
    if not processing:
        validate_self_check(call, root)
    packet_path = within(root, call['packet_file'])
    require(digest(packet_path) == call['packet_sha256'], '复核交接的任务包已变化')
    packet = read(packet_path)
    require(packet['candidate_sha256'] == call['output']['sha256'], '复核交接须对应当前候选')
    from review_v2 import selection_image
    selection_image(call['output'], root, packet['stage'])
    template = within(root, call['packet_file']).with_name(Path(call['packet_file']).stem + '-report-template.json')
    require(template.is_file(), '自评通过后的复核模板缺失，修复记录后再交代理')
    return {'reviewer_dispatch_ready': True, 'review_context': packet.get('review_context'),
            'reviewer_handoff': {'packet_file': call['packet_file'], 'packet_sha256': call['packet_sha256'],
                                 'report_template_file': local(root, template), 'root': str(root.resolve()),
                                 'task': REVIEWER_TASK}}


def register(root, role, config, values):
    from PIL import Image
    brief, state, selection, workflow = values
    require(role in selection['stages'], '先prepare本阶段')
    choice = selection['stages'][role]
    require(state['stages'][role]['target_sha256'] == target_binding(brief, role), '先重新prepare变化的目标')
    deps = dependencies(root, state, role, brief)
    require(state['stages'][role]['dependencies'] == deps, '上游已变化，先prepare')
    if config.get('kind') == 'processing':
        register_processing(root, role, config, values)
        return
    require(not choice.get('limit_selection') and not stage_budget(role, choice, workflow)['limit_reason'],
            '单阶段生成上限已到，自己选最优图继续，不再生成')
    identifier = config.get('id')
    require(isinstance(identifier, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,80}', identifier)), '调用id须为短字母数字或下划线/横线')
    require(identifier not in used_ids(role, choice, workflow), '调用id重复（包含已归档历史），请用新id保留旧证据')
    production = brief['task_mode'] == 'production'
    recipe = recipe_snapshot(root, config.get('recipe')) if production else None
    require(recipe == choice['recipe'], '实际调用与冻结的成功配置不一致；先诊断/prepare，不能冒充复现')
    baseline_id = choice.get('baseline_call')
    if baseline_id:
        base = check_v2(state, root, stage=role, baseline=True)
        require(base['record_integrity_valid'], '首张批准已失效：' + '；'.join(base['errors']))
        extras = [c for c in choice['attempts'] if c.get('phase') == 'extra']
        require(len(extras) < choice['required_calls'] - 1, '固定追加次数已用完；不能因候选效果不好补抽')
    else:
        previous = choice['attempts'][-1] if choice['attempts'] else None
        if previous and not previous.get('error'):
            if previous.get('packet_file'):
                from review_assets import restore_source_cache
                packet_path = within(root, previous['packet_file'])
                require(digest(packet_path) == previous['packet_sha256'], '前次任务包已变化')
                restore_source_cache(read(packet_path), brief, root)
            previous_review = choice['reviews'].get(previous['id'], {})
            if previous_review:
                require(previous_review.get('decision') == 'rejected', '首张须先复核；待核实不能直接重生成')
                validate_review_integrity(previous_review, brief, role, root, choice['reviewer_policy'], previous)
            else:
                require(previous.get('self_check', {}).get('result') == 'fail', '首张须先简单自评或复核；待核实不能直接重生成')
                validate_self_check(previous, root, require_pass=False)
        for key, failure in workflow['failures'].items():
            if key.startswith(role + '/') and failure['count'] >= 3:
                strategy = workflow['strategy_changes'].get(key)
                require(strategy is not None and failure['count'] == strategy['failures_before'], '同一关键错误已达停止条件，先诊断或报告限制')
    if production:
        evidence = snapshot(root, config.get('evidence_file'), '调用记录')
        require(within(root, evidence['file']).stat().st_size > 0, '真实调用记录为空')
    else:
        imported = source_path(root, config.get('output'))
        receipt = within(root, f'制作记录/调用记录/{role}-{identifier}-import.json')
        write(receipt, {'operation': 'existing_candidate_review', 'source': str(imported), 'sha256': digest(imported)})
        evidence = {'file': local(root, receipt), 'sha256': digest(receipt)}
    call = {'id': identifier, 'phase': 'extra' if baseline_id else 'first', 'event': next_event(workflow),
            'recipe_sha256': recipe_binding(recipe) if production else None, 'evidence_file': evidence['file'], 'evidence_sha256': evidence['sha256']}
    call['recipe_parameters'] = copy.deepcopy(recipe['parameters']) if recipe else {}
    for key in ('review_regions', 'comparisons'):
        if key in config:
            call[key] = copy.deepcopy(config[key])
    if config.get('error'):
        require(not config.get('output'), '失败调用不能同时声明输出')
        call['error'] = config['error']
    else:
        source = source_path(root, config.get('output'))
        with Image.open(source) as image:
            require(image.format == 'PNG', '候选须为原生PNG')
            image.verify()
        with Image.open(source) as image:
            image.load()
        directory = within(root, '候选')
        directory.mkdir(parents=True, exist_ok=True)
        numbers = [int(p.name[:6]) for p in directory.glob('*.png') if re.match(r'^\d{6}_', p.name)]
        path = directory / f'{max(numbers, default=0) + 1:06d}_{LABELS[role]}.png'
        shutil.copyfile(source, path)
        call['output'] = {'file': local(root, path), 'sha256': digest(path)}
    choice['attempts'].append(call)
    if not call.get('error') and not baseline_id:
        packet = evidence_packet(root, role, call, brief, deps, choice['reviewer_policy'],
                                 review_context=retry_context(role, choice, workflow))
        store_packet(root, role, call, packet)


def register_processing(root, role, config, values):
    from PIL import Image
    brief, state, selection, workflow = values
    result = check_v2(state, root, stage=role)
    require(result['record_integrity_valid'], '先完成当前选图与批准或上限选优，再登记后处理')
    choice = selection['stages'][role]
    identifier = config.get('id')
    require(isinstance(identifier, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,80}', identifier)), '处理id无效')
    require(identifier not in used_ids(role, choice, workflow), '处理/调用id重复（包含已归档历史）')
    source = source_path(root, config.get('output'))
    with Image.open(source) as image:
        require(image.format == 'PNG', '处理结果须为PNG')
        image.verify()
    with Image.open(source) as image:
        image.load()
    sha = digest(source)
    record = snapshot(root, config.get('record_file'), '后处理记录')
    provenance = read(within(root, record['file']))
    current = state['stages'][role]['sha256']
    require((provenance.get('input_sha256') or provenance.get('source_sha256')) == current
            and provenance.get('output_sha256') == sha, '后处理旁录与真实输入/输出版本不符')
    output = within(root, f'制作记录/处理结果/{identifier}-{sha}.png')
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output)
    call = {'id': identifier, 'output': {'file': local(root, output), 'sha256': sha}}
    step = dict(call, input_sha256=current, output_sha256=sha, record_file=record['file'], record_sha256=record['sha256'],
                event=next_event(workflow))
    previous = copy.deepcopy(state['stages'][role])
    proof = pixel_equivalence(image_bytes_for_review(previous, root, role), source.read_bytes(),
                              provenance.get('operation'), provenance.get('offset'))
    inherit = proof is not None and previous['decision'] == 'approved'
    if inherit:
        step.update(pixel_equivalence=proof, inherited_review=previous, inherited_review_sha256=fingerprint(previous))
    packet = evidence_packet(root, role, call, brief, state['stages'][role]['dependencies'], choice['reviewer_policy'], minimal=inherit)
    packet['processing_sha256'] = processing_binding(choice.get('processing', []) + [step])
    if inherit:
        packet.update(pixel_equivalence=proof, inherited_review_sha256=step['inherited_review_sha256'],
                      background_policy=previous['background_policy'], reviewer_role='maker_processing_check')
    store_packet(root, role, call, packet)
    if inherit:
        path = within(root, call['packet_file'])
        write(path.with_name(path.stem + '-report-template.json'), {
            'kind': 'processing-check', 'call_id': identifier, 'packet_sha256': call['packet_sha256'],
            'reviewer': {'mode': 'self', 'model': 'inherited'}, 'result': 'pending',
            'framing_observation': '', 'background_observation': '', 'viewed_evidence_ids': []})
    step.update(packet_file=call['packet_file'], packet_sha256=call['packet_sha256'])
    choice.setdefault('processing', []).append(step)
    choice.setdefault('processing_reviews', {})
    state['stages'][role]['decision'] = 'unreviewed'
    if choice.get('limit_selection'):
        refresh(root, role, values)


def refresh(root, role, values):
    brief, state, selection, workflow = values
    choice = selection['stages'][role]
    if choice.get('limit_selection'):
        adopted = choice['limit_selection']
        ledger = stage_budget(role, choice, workflow)['ledger']
        call = next(call for entry in ledger for call in entry['attempts'] if call['id'] == adopted['preferred_call'])
        current = choice.get('processing', [])[-1] if choice.get('processing') else call
        reused = approved_at_limit(role, choice, workflow, adopted['preferred_call'], brief, root, state['stages'][role]['dependencies'])
        processed = choice.get('processing_reviews', {}).get(current['id']) if choice.get('processing') else None
        approved = reused if not choice.get('processing') else processed if processed and processed.get('decision') == 'approved' and reused else None
        review = copy.deepcopy(approved) if approved else {'image': current['output']['file'], 'sha256': current['output']['sha256'],
                  'target_sha256': target_binding(brief, role), 'dependencies': state['stages'][role]['dependencies'],
                  'decision': 'selected_unreviewed', 'checks': [], 'reviewer': adopted['reviewer'],
                  'reviewer_policy': choice['reviewer_policy']}
        review.update(selection_sha256=selection_binding(choice), limit_selection_sha256=adopted['report_sha256'])
        if approved:
            review.update(review_sha256=fingerprint(approved), binding_sha256=visual_binding(review),
                          dependency_binding_sha256=dependency_binding(review))
        else:
            review['binding_sha256'] = fingerprint({'image': review['sha256'], 'target': review['target_sha256'],
                                                   'dependencies': review['dependencies'], 'limit_selection': adopted['report_sha256']})
        state['stages'][role] = review
        return
    selected = choice.get('baseline_call')
    if choice.get('ranking'):
        preferred = choice['ranking']['preferred_call']
        review = choice['reviews'].get(preferred)
        if review and (review['decision'] == 'approved' or review['decision'] == 'rejected' or review.get('supplement_round') == 1):
            selected = preferred if review['decision'] == 'approved' else choice['baseline_call']
        elif next(call for call in choice['attempts'] if call['id'] == preferred).get('self_check', {}).get('result') == 'fail':
            selected = choice['baseline_call']
        elif preferred == choice['baseline_call']:
            selected = preferred
        else:
            selected = None
    choice['selected_call'] = selected
    if selected:
        chosen_review = choice['reviews'][selected]
        if choice.get('processing'):
            chosen_review = choice.get('processing_reviews', {}).get(choice['processing'][-1]['id'])
            if not chosen_review:
                return
        review = copy.deepcopy(chosen_review)
        review['review_sha256'] = fingerprint(chosen_review)
        review['selection_sha256'] = selection_binding(choice)
        review['binding_sha256'] = fingerprint({'image': review['sha256'], 'target': review['target_sha256'],
                                               'dependencies': review['dependencies']})
        review['dependency_binding_sha256'] = dependency_binding(review)
        state['stages'][role] = review


def record_self_check(root, role, config, values):
    brief, state, selection, workflow = values
    choice = selection['stages'][role]
    maker_valid(config.get('reviewer'))
    call = next((call for call in choice['attempts'] if call['id'] == config.get('call_id')), None)
    require(call is not None and not call.get('error') and bool(call.get('packet_file')), '简单自评须对应待复核的真实候选')
    require(not call.get('self_check') and call['id'] not in choice['reviews'], '本候选已有简单自评或复核，不能覆盖')
    packet = read(within(root, call['packet_file']))
    require(config.get('packet_sha256') == digest(within(root, call['packet_file'])), '简单自评须绑定当前任务包')
    require(packet['target_sha256'] == target_binding(brief, role) and
            packet['dependencies'] == dependencies(root, state, role, brief), '简单自评的目标或上游已变化')
    require(config.get('result') in ('pass', 'fail') and bool(config.get('observation')), '简单自评须写实际观察及pass/fail')
    check_self_observations(config, packet)
    require(config['result'] != 'fail' or bool(config.get('issue_key')), '自评明确失败须用稳定issue_key记录问题')
    receipt = within(root, f'制作记录/复核报告/{role}-{call["id"]}-self.json')
    require(not receipt.exists(), '不能覆盖简单自评记录')
    write(receipt, config)
    call['self_check'] = {'result': config['result'], 'candidate_sha256': call['output']['sha256'],
                          'report_file': local(root, receipt), 'report_sha256': digest(receipt), 'event': next_event(workflow)}
    if config['result'] == 'fail':
        key = f'{role}/{config["issue_key"]}'
        failure = workflow['failures'].setdefault(key, {'count': 0, 'calls': []})
        if call['id'] not in failure['calls']:
            failure['calls'].append(call['id'])
            failure['count'] += 1
    refresh(root, role, values)


def record_limit_selection(root, role, config, values):
    brief, state, selection, workflow = values
    choice = selection['stages'][role]
    require(brief['task_mode'] == 'production', '既有图片评审不使用生成上限选优')
    require(not choice.get('limit_selection'), '已完成上限选优，不重复更换')
    maker_valid(config.get('reviewer'))
    budget = stage_budget(role, choice, workflow)
    require(bool(budget['limit_reason']), '尚未达到单阶段生成上限，不能跳过验收')
    require(state['stages'][role]['target_sha256'] == target_binding(brief, role) and
            state['stages'][role]['dependencies'] == dependencies(root, state, role, brief), '目标或上游已变化，先prepare')
    available = set()
    for entry in budget['ledger']:
        if entry['attempts']:
            available.update(key for key, call in validate_calls(entry, root, role).items() if not call.get('error'))
    viewed = config.get('viewed_call_ids', [])
    require(isinstance(viewed, list) and len(viewed) == len(set(viewed)) and set(viewed) == available,
            '上限选优须实际比较全部可用候选，不把失败调用当图片')
    require(config.get('preferred_call') in available and bool(config.get('selection_reason')), '须选择实际可用的最优候选并写理由')
    receipt = within(root, f'制作记录/复核报告/{role}-limit-selection-{next_event(workflow)}.json')
    write(receipt, config)
    choice['limit_selection'] = {key: copy.deepcopy(config[key]) for key in
                                 ('preferred_call', 'selection_reason', 'viewed_call_ids', 'reviewer')}
    choice['limit_selection'].update(budget_sha256=fingerprint(budget), limit_reason=budget['limit_reason'],
                                     report_file=local(root, receipt), report_sha256=digest(receipt))
    reused = approved_at_limit(role, choice, workflow, config['preferred_call'], brief, root, state['stages'][role]['dependencies'])
    choice['limit_selection']['reused_review_sha256'] = fingerprint(reused) if reused else None
    choice['selected_call'] = config['preferred_call']
    refresh(root, role, values)


def record_processing_check(root, role, config, values):
    brief, state, selection, workflow = values
    choice = selection['stages'][role]
    require(bool(choice.get('processing')), '没有待自查的处理版本')
    step = choice['processing'][-1]
    require(step['id'] == config.get('call_id') and step.get('pixel_equivalence'), '仅无损处理使用简短自查')
    require(step['id'] not in choice['processing_reviews'], '处理自查已有真实报告，不覆盖')
    maker_valid(config.get('reviewer'))
    require(config.get('result') in ('pass', 'fail', 'pending') and bool(config.get('framing_observation'))
            and bool(config.get('background_observation')), '须写构图与背景的实际观察和结果')
    require(config.get('packet_sha256') == digest(within(root, step['packet_file'])), '处理自查须绑定当前任务包')
    packet = read(within(root, step['packet_file']))
    require(packet['target_sha256'] == target_binding(brief, role) and
            packet['dependencies'] == dependencies(root, state, role, brief), '处理自查目标或上游已变化')
    require({'whole', 'alpha_compare'} <= set(config.get('viewed_evidence_ids', [])) <= {a['id'] for a in packet['evidence']},
            '处理自查须实际看全图和明暗底对照')
    receipt = within(root, f'制作记录/复核报告/{role}-{step["id"]}-processing-check.json')
    require(not receipt.exists(), '不能覆盖处理自查历史')
    review = {'image': step['output']['file'], 'sha256': step['output']['sha256'],
              'target_sha256': packet['target_sha256'], 'dependencies': packet['dependencies'],
              'decision': {'pass': 'approved', 'fail': 'rejected', 'pending': 'unreviewed'}[config['result']],
              'checks': [], 'approval_basis': 'pixel_equivalent_with_framing_check',
              'inherited_review': copy.deepcopy(step['inherited_review']), 'inherited_review_sha256': step['inherited_review_sha256'],
              'reviewer': copy.deepcopy(config['reviewer']), 'reviewer_policy': choice['reviewer_policy'],
              'background_policy': copy.deepcopy(packet['background_policy']), 'processing': copy.deepcopy(choice['processing']),
              'packet_file': step['packet_file'], 'packet_sha256': step['packet_sha256'],
              'report_file': local(root, receipt), 'supplement_round': 0}
    write(receipt, config)
    review['report_sha256'] = digest(receipt)
    if review['decision'] == 'approved':
        validate_inherited_visual(review, brief, role, root)
    choice['processing_reviews'][step['id']] = review
    next_event(workflow)
    refresh(root, role, values)


def record_review(root, role, config, values):
    brief, state, selection, workflow = values
    choice = selection['stages'].get(role)
    require(choice is not None, '先prepare本阶段')
    kind = config.get('kind', 'visual')
    if kind == 'limit-selection':
        record_limit_selection(root, role, config, values)
        return
    if kind == 'processing-check':
        record_processing_check(root, role, config, values)
        return
    require(not choice.get('limit_selection'), '已到上限并选定，取消后续合格评价')
    require(brief['task_mode'] != 'production' or not stage_budget(role, choice, workflow)['limit_reason'],
            '单阶段生成上限已到，自己选最优图继续，不再合格评价')
    if kind == 'self-check':
        record_self_check(root, role, config, values)
        return
    calls = {call['id']: call for call in choice['attempts']}
    processing_calls = {step['id']: step for step in choice.get('processing', [])}
    if config.get('kind', 'visual') == 'selection':
        maker_valid(config.get('reviewer'))
        require(not choice.get('ranking'), '本组已完成比较；不能改排另一张补救或绕过补证')
        baseline = choice.get('baseline_call')
        require(baseline is not None, '首张尚未通过')
        group = [call for call in choice['attempts'] if call['id'] == baseline or call['phase'] == 'extra']
        require(len(group) == choice['required_calls'], '先完成固定追加调用；工具失败也须真实登记')
        available = {call['id'] for call in group if not call.get('error')}
        require(set(config.get('viewed_call_ids', [])) == available, '比较须明确记录实际看过的可用候选')
        require(config.get('preferred_call') in available and bool(config.get('selection_reason')), '缺少实际选择及理由')
        choice['ranking'] = {key: config[key] for key in ('preferred_call', 'selection_reason', 'reviewer', 'viewed_call_ids')}
        preferred = calls[config['preferred_call']]
        if preferred['id'] != baseline and preferred['id'] not in choice['reviews']:
            packet = evidence_packet(root, role, preferred, brief, dependencies(root, state, role, brief), choice['reviewer_policy'])
            store_packet(root, role, preferred, packet)
        refresh(root, role, values)
        return
    reviewer_valid(config.get('reviewer'), choice['reviewer_policy'])
    require(config.get('kind', 'visual') == 'visual', 'kind须为visual或selection')
    identifier = config.get('call_id')
    processing = identifier in processing_calls
    require((identifier in calls and not calls[identifier].get('error')) or processing, '复核须对应真实成功调用/处理版本')
    call = processing_calls[identifier] if processing else calls[identifier]
    if processing:
        require(identifier == choice['processing'][-1]['id'], '只复核当前处理版本')
        require(not call.get('pixel_equivalence'), '无损处理只需processing-check，不重复完整复核')
    if choice.get('baseline_call'):
        require(processing or identifier in (choice['baseline_call'], choice.get('ranking', {}).get('preferred_call')), '落选追加候选不做逐项验收')
    if not processing:
        validate_self_check(call, root)
    packet_file = call.get('packet_file')
    require(packet_file is not None, '先完成比较选择再核验新增优胜候选')
    packet = read(within(root, packet_file))
    expected_agent = packet.get('review_context', {}).get('agent_id')
    if expected_agent and config['reviewer']['mode'] == 'subagent':
        require(config['reviewer'].get('agent_id') == expected_agent or bool(config.get('reviewer_replacement_reason')),
                '返修应交回原子代理；无法复用须记录真实原因')
    require(config.get('packet_sha256') == digest(within(root, packet_file)), '报告须绑定实际查看的任务包哈希')
    require(packet['target_sha256'] == target_binding(brief, role), '目标已变化，不能补字段沿用旧复核')
    require(packet['dependencies'] == dependencies(root, state, role, brief), '上游已变化，旧任务包失效')
    review_collection = choice.setdefault('processing_reviews', {}) if processing else choice['reviews']
    previous = review_collection.get(identifier)
    round_number = config.get('supplement_round', 0)
    require(type(round_number) is int and round_number in (0, 1), '仅允许一次补证')
    if previous:
        require(previous['decision'] == 'unreviewed' and previous.get('supplement_round', 0) == 0 and round_number == 1,
                '不重复审已批准/已拒绝版本；待核实仅补证一次')
    else:
        require(round_number == 0, '首次报告补证轮次须为0')
    artifacts = {entry['id']: entry for entry in packet['evidence']}
    for spec in config.get('extra_evidence', []):
        require(spec.get('id') not in artifacts, '补证id重复')
        artifacts[spec['id']] = dict(spec, **snapshot(root, spec['file'], '对照/补证'))
    viewed = config.get('viewed_evidence_ids', [])
    require(isinstance(viewed, list) and len(viewed) == len(set(viewed)) and set(viewed) <= set(artifacts), '查看证据id无效或重复')
    goals = {goal['id']: goal for goal in brief['checks'][role]}
    checks, identifiers = [], set()
    for observation in config.get('checks', []):
        key = observation.get('id')
        require(key in goals and key not in identifiers, '检查项未知或重复')
        identifiers.add(key)
        require(observation.get('result') in ('pass', 'fail', 'pending'), '须明确pass/fail/pending')
        require(bool(observation.get('candidate_observation')), '须写实际候选观察')
        require(bool(observation.get('reference_observation')), '须写参考事实或该项实际适用的目标依据')
        if goals[key]['group'] in ('identity', 'design', 'spatial'):
            require(bool(observation.get('comparison_basis')), '须写对象尺度或空间比较依据')
        item = copy.deepcopy(observation)
        ids = item.pop('evidence_ids', [])
        require(bool(ids) and set(ids) <= set(viewed), '每项须引用明确实际看过的证据')
        group = goals[key]['group']
        allowed = primary_kinds(group)
        require(any(artifacts[i]['kind'] in allowed for i in ids), '证据种类不适用于当前检查项：缺少主要证据')
        item['evidence'] = [dict(file=artifacts[i]['file'], sha256=artifacts[i]['sha256'],
                                 candidate_sha256=call['output']['sha256'], viewed=True,
                                 purpose=PURPOSES[group] if artifacts[i]['kind'] in allowed else 'context_reference',
                                 **({key: artifacts[i][key] for key in ('background', 'backgrounds') if key in artifacts[i]})) for i in ids]
        if group == 'background':
            from PIL import Image
            samples = []
            with Image.open(within(root, call['output']['file'])) as source:
                rgba = source.convert('RGBA')
                for sample in item.get('empty_background_samples', []):
                    if sample.get('confirmed_empty') is not True:
                        continue
                    xy = sample.get('xy')
                    require(isinstance(xy, list) and len(xy) == 2 and all(type(v) is int for v in xy) and
                            0 <= xy[0] < rgba.width and 0 <= xy[1] < rgba.height, '空背景坐标无效')
                    pixel = rgba.getpixel(tuple(xy))
                    samples.append({'xy': xy, 'confirmed_empty': True, 'alpha': pixel[3], 'rgb': list(pixel[:3])})
            item['empty_background_samples'] = samples
        checks.append(item)
    require(bool(checks), '报告须至少有一条实际观察')
    decision = 'rejected' if any(c['result'] == 'fail' for c in checks) else (
        'approved' if identifiers == set(goals) and all(c['result'] == 'pass' for c in checks) else 'unreviewed')
    review = {'image': call['output']['file'], 'sha256': call['output']['sha256'], 'target_sha256': packet['target_sha256'],
              'dependencies': packet['dependencies'], 'decision': decision, 'checks': checks, 'reviewer': config['reviewer'],
              'reviewer_policy': choice['reviewer_policy'], 'packet_file': packet_file,
              'packet_sha256': config['packet_sha256'], 'supplement_round': round_number,
              'background_policy': config.get('background_policy', packet['background_policy'])}
    if processing:
        review['processing'] = copy.deepcopy(choice['processing'])
    # Explicit observations are normalized, never filled as pass/viewed by inference.
    if decision == 'approved':
        check_quality_observations(config)
        validate_visual(review, brief, role, root, choice['reviewer_policy'], require_report=False)
    receipt = within(root, f'制作记录/复核报告/{role}-{identifier}-{round_number}.json')
    require(not receipt.exists(), '复核报告已存在，不能覆盖历史')
    write(receipt, config)
    review['report_file'], review['report_sha256'] = local(root, receipt), digest(receipt)
    if previous:
        review['previous_review'] = previous
    review_collection[identifier] = review
    event = next_event(workflow)
    if decision == 'approved' and not choice.get('baseline_call'):
        choice['baseline_call'] = identifier
        choice['baseline_approval_event'] = event
    if decision == 'rejected':
        for item in checks:
            if item['result'] == 'fail':
                key = f'{role}/{item.get("issue_key", item["id"])}'
                failure = workflow['failures'].setdefault(key, {'count': 0, 'calls': []})
                if identifier not in failure['calls']:
                    failure['calls'].append(identifier)
                    failure['count'] += 1
    refresh(root, role, values)


def status(root, role, values):
    brief, state, selection, workflow = values
    choice = selection['stages'].get(role)
    response = {'stage': role, 'file_integrity': 'not_checked', 'record_integrity': 'pending',
                'visual_assessment': 'unreviewed', 'model_visual_checks': False, 'reviewer_dispatch_ready': False}
    if not choice:
        return dict(response, next_action='prepare')
    try:
        require(state['stages'][role]['target_sha256'] == target_binding(brief, role), '目标已变化')
        require(state['stages'][role]['dependencies'] == dependencies(root, state, role, brief), '上游已变化')
        for goal in brief['checks'][role]:
            for key in goal['source_ids']:
                entry = brief['sources'][key]
                require(digest(within(root, entry['file'])) == entry['sha256'], '来源已变化')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return dict(response, next_action='repair_records_or_reprepare', errors=[str(exc)])
    calls = choice['attempts']
    baseline = choice.get('baseline_call')
    if choice.get('limit_selection'):
        result = check_v2(state, root, stage=role)
        return dict(response, next_action='continue' if result['downstream_ready'] else 'repair_records_or_reprepare',
                    record_integrity='pass' if result['record_integrity_valid'] else 'fail',
                    visual_assessment=result['visual_assessment'], selected_call=choice['selected_call'], check=result)
    budget = stage_budget(role, choice, workflow)
    if brief['task_mode'] == 'production' and budget['limit_reason']:
        candidates = [call['id'] for entry in budget['ledger'] for call in entry['attempts'] if not call.get('error')]
        return dict(response, next_action='select_best_at_limit' if candidates else 'report_tool_failure',
                    candidate_ids=candidates, call_count=budget['call_count'], limit_reason=budget['limit_reason'],
                    visual_assessment='qualification_cancelled')
    if not baseline:
        if not calls:
            return dict(response, next_action='generate_first')
        latest = calls[-1]
        review = choice['reviews'].get(latest['id'])
        if latest.get('error'):
            return dict(response, next_action='report_tool_failure', tool_error=latest['error'])
        if not review:
            if not latest.get('self_check'):
                return dict(response, next_action='self_check_first', packet_file=latest['packet_file'], packet_sha256=latest['packet_sha256'],
                            self_check_template_file=local(root, within(root, latest['packet_file']).with_name(Path(latest['packet_file']).stem + '-self-check-template.json')))
            try:
                validate_self_check(latest, root, require_pass=False)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                return dict(response, next_action='repair_records', record_integrity='fail', errors=[str(exc)])
            if latest['self_check']['result'] == 'pass':
                try:
                    dispatch = review_dispatch(root, latest)
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    return dict(response, next_action='repair_records', record_integrity='fail', errors=[str(exc)])
                return dict(response, next_action='review_first', packet_file=latest['packet_file'], packet_sha256=latest['packet_sha256'], **dispatch)
            action = 'revise_input_or_repair'
            for key, failure in workflow['failures'].items():
                if key.startswith(role + '/') and failure['count'] >= 3 and key not in workflow['strategy_changes']:
                    action = 'stop_and_diagnose'
            return dict(response, next_action=action, visual_assessment='self_rejected')
        try:
            validate_review_integrity(review, brief, role, root, choice['reviewer_policy'], latest)
            require(review.get('dependencies') == state['stages'][role]['dependencies'], '首次复核的上游版本已变化')
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return dict(response, next_action='repair_records', record_integrity='fail', errors=[str(exc)])
        if review['decision'] == 'unreviewed':
            dispatch = review_dispatch(root, latest) if review['supplement_round'] == 0 else {}
            return dict(response, next_action='supplement_evidence' if review['supplement_round'] == 0 else 'blocked_uncertain',
                        visual_assessment='pending', packet_file=latest['packet_file'], packet_sha256=latest['packet_sha256'], **dispatch)
        action = 'revise_input_or_repair'
        for key, failure in workflow['failures'].items():
            if key.startswith(role + '/') and failure['count'] >= 3:
                strategy = workflow['strategy_changes'].get(key)
                if not strategy:
                    action = 'stop_and_diagnose'
        return dict(response, next_action=action, visual_assessment='rejected')
    if choice.get('processing'):
        latest = choice['processing'][-1]
        review = choice.get('processing_reviews', {}).get(latest['id'])
        if not review or review['decision'] != 'approved':
            action = ('self_check_processed' if latest.get('pixel_equivalence') else 'review_processed') if not review else ('report_processing_failure' if review['decision'] == 'rejected'
                     else 'supplement_evidence' if review['supplement_round'] == 0 else 'blocked_uncertain')
            dispatch = review_dispatch(root, latest, processing=True) if action in ('review_processed', 'supplement_evidence') else {}
            return dict(response, next_action=action, packet_file=latest['packet_file'], packet_sha256=latest['packet_sha256'], **dispatch)
    if brief['task_mode'] == 'existing_review':
        result = check_v2(state, root, stage=role)
        return dict(response, next_action=('review_complete' if brief.get('scope') == 'single_stage' else 'continue')
                    if result['recorded_approval_valid'] else 'repair_records_or_reprepare',
                    record_integrity='pass' if result['recorded_approval_valid'] else 'fail',
                    visual_assessment='recorded_approved' if result['recorded_approval_valid'] else 'not_approved', check=result)
    first = check_v2(state, root, stage=role, baseline=True)
    if not first['record_integrity_valid']:
        return dict(response, next_action='repair_records_or_reprepare', errors=first['errors'])
    extras = [call for call in calls if call['phase'] == 'extra']
    if len(extras) < choice['required_calls'] - 1:
        return dict(response, next_action='generate_extra', remaining_calls=choice['required_calls'] - 1 - len(extras),
                    visual_assessment='baseline_approved')
    if not choice.get('ranking'):
        return dict(response, next_action='compare_candidates', candidate_ids=[call['id'] for call in calls
                    if (call['id'] == baseline or call['phase'] == 'extra') and not call.get('error')])
    preferred = choice['ranking']['preferred_call']
    review = choice['reviews'].get(preferred)
    selected_call = next(call for call in calls if call['id'] == preferred)
    if not review:
        if not selected_call.get('self_check'):
            return dict(response, next_action='self_check_selected', packet_file=selected_call['packet_file'], packet_sha256=selected_call['packet_sha256'],
                        self_check_template_file=local(root, within(root, selected_call['packet_file']).with_name(Path(selected_call['packet_file']).stem + '-self-check-template.json')))
        try:
            validate_self_check(selected_call, root, require_pass=False)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return dict(response, next_action='repair_records', record_integrity='fail', errors=[str(exc)])
        if selected_call['self_check']['result'] == 'pass':
            try:
                dispatch = review_dispatch(root, selected_call)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                return dict(response, next_action='repair_records', record_integrity='fail', errors=[str(exc)])
            return dict(response, next_action='review_selected', packet_file=selected_call['packet_file'], packet_sha256=selected_call['packet_sha256'], **dispatch)
        result = check_v2(state, root, stage=role)
        return dict(response, next_action='continue' if result['downstream_ready'] else 'repair_records_or_reprepare',
                    selected_call=choice['selected_call'], visual_assessment=result['visual_assessment'], check=result)
    if review['decision'] == 'unreviewed' and review['supplement_round'] == 0:
        return dict(response, next_action='supplement_evidence', packet_file=selected_call['packet_file'], packet_sha256=selected_call['packet_sha256'],
                    **review_dispatch(root, selected_call))
    result = check_v2(state, root, stage=role)
    return dict(response, next_action='continue' if result['downstream_ready'] else 'repair_records_or_reprepare',
                record_integrity='pass' if result['record_integrity_valid'] else 'fail',
                visual_assessment=result['visual_assessment'],
                selected_call=choice.get('selected_call'), check=result)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'register', 'record-review', 'status'))
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--stage', required=True, choices=ROLES)
    parser.add_argument('--config', type=Path, help='Semantic brief/actual call/explicit reviewer report JSON')
    parser.add_argument('--dry-run', action='store_true', help='Validate all report fields without writing files')
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        values = load_all(root)
        require(not args.dry_run or args.command == 'record-review', '--dry-run仅用于record-review')
        if args.command != 'status':
            require(args.config is not None, '此操作须提供--config')
            config = read(args.config)
            if args.command == 'record-review':
                from review_report import report_errors
                errors = report_errors(root, args.stage, config, values)
                if errors:
                    print(json.dumps({'record_integrity': 'fail', 'model_visual_checks': False, 'next_action': 'repair_records',
                                      'dry_run': args.dry_run, 'errors': [e['field'] + ': ' + e['message'] for e in errors],
                                      'field_errors': errors}, ensure_ascii=False))
                    return 1
                if args.dry_run:
                    print(json.dumps({'record_integrity': 'pass', 'model_visual_checks': False, 'visual_assessment': 'not_recorded',
                                      'dry_run': True, 'next_action': 'record_review', 'errors': []}, ensure_ascii=False))
                    return 0
            {'prepare': prepare, 'register': register, 'record-review': record_review}[args.command](root, args.stage, config, values)
            save_all(root, values)
        response = status(root, args.stage, values)
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        print(json.dumps({'record_integrity': 'fail', 'model_visual_checks': False, 'next_action': 'repair_records',
                          'errors': [str(exc)]}, ensure_ascii=False))
        return 1
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
