"""Stage-local review bindings and v2 selection checks. No image semantics are inferred."""
import hashlib
import io
import json
from pathlib import Path

from check_delivery import ROLES, within, canvas_image_data, png_size
from review_policy import ACCEPTANCE_STANDARD, check_quality_observations
from review_assets import validate_comparison, pixel_equivalence

DEPENDENCIES = {'head': (), 'front': ('head',), 'back': ('front',), 'left': ('front', 'back')}
BRIEF = '制作记录/验收目标.json'
STATE = '制作记录/验收状态.json'
SELECTION = '制作记录/三选一记录.json'
WORKFLOW = '制作记录/流程状态.json'
PURPOSES = {'identity': 'reference_compare', 'design': 'reference_compare', 'look': 'reference_compare',
            'native_quality': 'native_detail', 'reduced_quality': 'reduced_view',
            'background': 'source_background', 'spatial': 'cross_view'}


def read(path):
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(value, dict):
        raise ValueError(f'JSON须为对象：{path}')
    return value


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def target_binding(brief, role):
    goals = brief.get('checks', {}).get(role)
    require(isinstance(goals, list) and bool(goals), f'{role}缺少验收目标')
    required_groups = set(PURPOSES) - ({'spatial'} if role not in ('back', 'left') else set())
    require(all(isinstance(goal, dict) and isinstance(goal.get('id'), str) and bool(goal['id'].strip())
                and isinstance(goal.get('target'), str) and bool(goal['target'].strip())
                and isinstance(goal.get('source_ids'), list) and bool(goal['source_ids']) for goal in goals),
            f'{role}目标须有唯一id、具体target和有效source_ids')
    require(len({g['id'] for g in goals}) == len(goals), '目标id重复')
    require({g.get('group') for g in goals} == required_groups, f'{role}目标分组不完整或无效')
    aspects = [g.get('aspect') for g in goals if g['group'] == 'look']
    allowed_aspects = {'color_style', 'lighting', 'framing', 'head_pose', 'gaze', 'expression'}
    required_aspects = allowed_aspects if role in ('head', 'front') else {'color_style', 'lighting', 'framing'}
    require(len(aspects) == len(set(aspects)) and set(aspects) <= allowed_aspects and required_aspects <= set(aspects),
            f'{role}须有独立完整的呈现aspect')
    ids = {source for goal in goals for source in goal.get('source_ids', [])}
    sources = brief.get('sources', {})
    require(ids and ids <= set(sources), f'{role}目标缺少有效来源')
    return fingerprint({'rules_version': brief.get('rules_version'), 'checks': goals,
                        'sources': {key: sources[key] for key in sorted(ids)}})


def recipe_binding(recipe):
    return fingerprint({key: recipe.get(key) for key in ('prompt_sha256', 'inputs', 'tool', 'parameters')})


def maker_valid(reviewer):
    require(isinstance(reviewer, dict) and reviewer.get('mode') == 'self'
            and isinstance(reviewer.get('model'), str) and bool(reviewer['model'].strip()),
            '简单自评、候选排序和处理自查须由制作模型自己执行')


def validate_self_check(call, root, require_pass=True):
    require(bool(call.get('self_check')), '先简单自评通过再交复核')
    check = call.get('self_check', {})
    report = read(within(root, check.get('report_file')))
    require(digest(within(root, check['report_file'])) == check.get('report_sha256'), '简单自评记录已变化')
    maker_valid(report.get('reviewer'))
    require(report.get('kind') == 'self-check' and report.get('call_id') == call['id']
            and check.get('candidate_sha256') == call['output']['sha256']
            and report.get('packet_sha256') == call.get('packet_sha256'), '简单自评与当前候选不符')
    require(report.get('result') in ('pass', 'fail') and bool(report.get('observation')),
            '简单自评须有实际观察及pass/fail')
    from review_policy import check_self_observations
    packet_path = within(root, call['packet_file'])
    require(digest(packet_path) == call['packet_sha256'], '简单自评任务包已变化')
    packet = read(packet_path)
    check_self_observations(report, packet)
    for artifact in packet['evidence']:
        if artifact['id'] in report['viewed_evidence_ids']:
            require(digest(within(root, artifact['file'])) == artifact['sha256'], '简单自评已看证据发生变化')
            if artifact['kind'] == 'comparison':
                from review_assets import validate_comparison
                validate_comparison(artifact, root)
    require(check.get('result') == report['result'], '简单自评结果与实际报告不符')
    require(not require_pass or report['result'] == 'pass', '先简单自评通过再交复核')


def stage_budget(role, choice, workflow):
    ledger = [{'recipe': item['selection'].get('recipe'), 'attempts': item['selection'].get('attempts', [])}
              for item in workflow.get('history', []) if item['stage'] == role]
    ledger.append({'recipe': choice.get('recipe'), 'attempts': choice.get('attempts', [])})
    failures = {key: value for key, value in workflow.get('failures', {}).items() if key.startswith(role + '/')}
    strategies = {key: value for key, value in workflow.get('strategy_changes', {}).items() if key.startswith(role + '/')}
    count = sum(len(item['attempts']) for item in ledger)
    limit = choice.get('generation_limit')
    require(limit is None or (type(limit) is int and limit > 0), 'generation_limit须为正整数')
    reason = 'generation_limit' if limit is not None and count >= limit else None
    if any(key in strategies and value['count'] > strategies[key]['failures_before'] for key, value in failures.items()):
        reason = reason or 'strategy_exhausted'
    return {'ledger': ledger, 'failures': failures, 'strategy_changes': strategies,
            'generation_limit': limit, 'call_count': count, 'limit_reason': reason}


def reviewer_valid(reviewer, policy):
    require(isinstance(reviewer, dict), '缺少实际复核方式')
    mode = reviewer.get('mode')
    require(mode in ('subagent', 'self'), '复核方式须为subagent或self')
    require(isinstance(reviewer.get('model'), str) and bool(reviewer['model'].strip()), '须记录复核模型或inherited')
    if mode == 'subagent':
        require(policy.get('mode', 'prefer_subagent') != 'self', '用户已禁止子代理')
        require(bool(reviewer.get('agent_id')), '子代理复核须记录实际agent_id')
        if policy.get('model'):
            require(reviewer['model'] == policy['model'], '复核模型与用户指定不符')
    else:
        reason = reviewer.get('reason_code')
        allowed = ('user_disabled',) if policy.get('mode') == 'self' else ('unsupported', 'startup_failed')
        require(reason in allowed and bool(reviewer.get('reason')), '自评须如实记录用户禁用或不可用原因')


def image_bytes_for_review(review, root, role):
    image = within(root, review['image'])
    if image.exists():
        data = image.read_bytes()
    else:
        manifest = within(root, review.get('canvas_manifest', '网页资源/画布清单.json'))
        candidates = read(manifest).get('candidates', [])
        matches = [c for c in candidates if c.get('sha256') == review['sha256']
                   and c.get('view') == {'left': 'side'}.get(role, role)]
        require(len(matches) == 1, '原生候选与对应完整画布数据均缺失')
        data = canvas_image_data(manifest.parent.parent, matches[0])
    require(hashlib.sha256(data).hexdigest() == review['sha256'], '复核图片字节已变化')
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        require(image.format == 'PNG', '复核图片须为完整PNG')
        image.verify()
    with Image.open(io.BytesIO(data)) as image:
        image.load()
    return data


def selection_image(reference, root, role):
    """Validate a current candidate file or an explicit canvas data reference."""
    require(isinstance(reference, dict), '三选一输出引用须为对象')
    if reference.get('file'):
        image_bytes_for_review({'image': reference['file'], 'sha256': reference.get('sha256')}, root, role)
        return
    manifest = within(root, reference.get('canvas_manifest'))
    require(manifest.name == '画布清单.json' and manifest.parent.name == '网页资源', '需引用规范的画布清单')
    matches = [item for item in read(manifest).get('candidates', []) if item.get('id') == reference.get('candidate_id')]
    require(len(matches) == 1 and matches[0].get('view') == {'left': 'side'}.get(role, role), '候选不存在、重复或视图不符')
    data = canvas_image_data(manifest.parent.parent, matches[0])
    require(hashlib.sha256(data).hexdigest() == reference.get('sha256'), '三选一候选图片已变化')
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        require(image.format == 'PNG', '候选须为完整PNG')
        image.verify()
    with Image.open(io.BytesIO(data)) as image:
        image.load()


def background_sample_valid(rgb, mode):
    if not isinstance(rgb, list) or len(rgb) != 3 or any(type(v) is not int or not 0 <= v <= 255 for v in rgb):
        return False
    return rgb == [255, 255, 255] if mode == 'exact_white' else min(rgb) >= 245 and max(rgb) - min(rgb) <= 8


def validate_check_records(review, brief, role, root):
    """Validate explicit observations and actual background pixels for this stage only."""
    from PIL import Image
    goals = {goal['id']: goal for goal in brief['checks'][role]}
    for key in {key for goal in goals.values() for key in goal['source_ids']}:
        source = brief['sources'][key]
        require(digest(within(root, source['file'])) == source.get('sha256'), f'{role}来源{key}已变化')
    checks = review.get('checks')
    require(isinstance(checks, list) and all(isinstance(item, dict) for item in checks), f'{role}逐项观察记录缺失')
    ids = [item.get('id') for item in checks]
    require(len(ids) == len(set(ids)) and set(ids) == set(goals), f'{role}逐项记录遗漏、重复或出现目标外检查项')
    image_data = image_bytes_for_review(review, root, role)
    for item in checks:
        key, group = item['id'], goals[item['id']]['group']
        require(item.get('result') == 'pass', f'{role}/{key}未通过')
        for field in ('reference_observation', 'candidate_observation') + (('comparison_basis',) if group in ('identity', 'design', 'spatial') else ()):
            require(isinstance(item.get(field), str) and bool(item[field].strip()), f'{role}/{key}缺少具体{field}')
        evidence = item.get('evidence')
        require(isinstance(evidence, list) and bool(evidence), f'{role}/{key}没有实际图像证据')
        require(any(isinstance(a, dict) and a.get('purpose') == PURPOSES[group] for a in evidence), f'{role}/{key}缺少适用的{PURPOSES[group]}证据')
        for artifact in evidence:
            require(isinstance(artifact, dict) and artifact.get('viewed') is True, f'{role}/{key}证据未记录实际查看')
            path = within(root, artifact.get('file'))
            png_size(path)
            require(digest(path) == artifact.get('sha256') and artifact.get('candidate_sha256') == review['sha256'], f'{role}/{key}证据已变化或来自其他候选')
        if group != 'background':
            continue
        policy = review.get('background_policy', {'mode': 'exact_white'})
        require(isinstance(policy, dict) and policy.get('mode') in ('transparent', 'exact_white', 'near_white_rgb_fallback'), f'{role}/{key}背景策略无效')
        mode = policy['mode']
        if mode == 'transparent':
            require(policy.get('alpha_source') in ('model', 'tool_extraction'), f'{role}/{key}需记录真实Alpha来源')
            backgrounds = {b for a in evidence if a.get('purpose') == 'source_background'
                           for b in a.get('backgrounds', [a.get('background')])}
            require({'light', 'dark'} <= backgrounds, f'{role}/{key}缺少实际查看的深浅底Alpha证据')
        if mode == 'near_white_rgb_fallback':
            require(isinstance(policy.get('reason'), str) and bool(policy['reason'].strip()), f'{role}/{key}缺少Alpha能力降级依据')
            require(policy.get('alpha_limitation') in ('input', 'output', 'both'), f'{role}/{key}须记录真实Alpha输入/输出限制')
        samples = item.get('empty_background_samples')
        require(isinstance(samples, list) and bool(samples), f'{role}/{key}空背景像素抽样缺失')
        with Image.open(io.BytesIO(image_data)) as image:
            rgba = image.convert('RGBA')
            if mode == 'transparent':
                low, high = rgba.getchannel('A').getextrema()
                require(low == 0 and high > 0, f'{role}/{key}没有真实透明空白和非空主体')
            for sample in samples:
                require(isinstance(sample, dict) and sample.get('confirmed_empty') is True, f'{role}/{key}空背景位置须经实际确认')
                xy = sample.get('xy')
                require(isinstance(xy, list) and len(xy) == 2 and all(type(v) is int for v in xy)
                        and 0 <= xy[0] < rgba.width and 0 <= xy[1] < rgba.height, f'{role}/{key}背景样本坐标无效')
                pixel = rgba.getpixel(tuple(xy))
                valid = (sample.get('alpha') == 0 and pixel[3] == 0) if mode == 'transparent' else (
                    pixel[3] == 255 and list(pixel[:3]) == sample.get('rgb') and background_sample_valid(sample.get('rgb'), mode))
                require(valid, f'{role}/{key}实际背景像素或采样记录不符合{mode}')


def validate_review_integrity(review, brief, role, root, policy, expected_call=None):
    """Check retained observations, including a rejected/pending winner used to justify fallback."""
    require(review.get('target_sha256') == target_binding(brief, role), '复核目标已变化')
    reviewer_valid(review.get('reviewer'), policy)
    report_path = within(root, review.get('report_file'))
    require(digest(report_path) == review.get('report_sha256'), '原始复核报告已变化或缺失')
    report = read(report_path)
    require(report.get('packet_sha256') == review.get('packet_sha256') and report.get('reviewer') == review.get('reviewer'),
            '复核报告与归档结论不符')
    require(report.get('supplement_round', 0) == review.get('supplement_round', 0), '补证轮次与真实报告不符')
    packet_path = within(root, review.get('packet_file'))
    require(digest(packet_path) == review.get('packet_sha256'), '复核任务包已变化')
    packet = read(packet_path)
    require(packet.get('stage') == role, '复核任务包不属于当前阶段')
    if expected_call is not None:
        require(packet.get('call_id') == expected_call.get('id') and
                review.get('sha256') == expected_call.get('output', {}).get('sha256'),
                '复核报告不属于当前实际调用或输出')
        if expected_call.get('phase'):
            validate_self_check(expected_call, root)
        context = packet.get('review_context', {})
        if context.get('agent_id') and review['reviewer']['mode'] == 'subagent':
            require(review['reviewer'].get('agent_id') == context['agent_id'] or bool(report.get('reviewer_replacement_reason')),
                    '返修应交回原子代理；无法复用须记录真实原因')
    require(packet.get('candidate_sha256') == review.get('sha256') and packet.get('target_sha256') == review.get('target_sha256'),
            '复核任务包与当前图片/目标不符')
    require(packet.get('dependencies') == review.get('dependencies'), '不能只修改依赖字段恢复旧视觉批准')
    require(packet.get('reviewer_policy') == policy and review.get('reviewer_policy') == policy,
            '复核任务包与实际复核策略不符')
    require(report.get('call_id') == packet.get('call_id'), '复核报告与实际调用不符')
    if review.get('supplement_round') == 1:
        previous = review.get('previous_review', {})
        require(previous.get('decision') == 'unreviewed' and previous.get('supplement_round') == 0, '补证须保留真实首次待核实记录')
        validate_review_integrity(previous, brief, role, root, policy, expected_call)
    goals = {goal['id']: goal for goal in brief['checks'][role]}
    observations = review.get('checks', [])
    raw = {item['id']: item for item in report.get('checks', [])}
    require(observations and len(raw) == len(observations), '缺少实际复核观察')
    for item in observations:
        require(item.get('id') in goals and item['id'] in raw, '复核检查项不符')
        for key in ('result', 'reference_observation', 'candidate_observation', 'comparison_basis'):
            require(item.get(key) == raw[item['id']].get(key), '归档观察与原始复核报告不符')
        require(item.get('result') in ('pass', 'fail', 'pending') and bool(item.get('reference_observation'))
                and bool(item.get('candidate_observation')), '复核须保留参考事实与实际观察')
        require(bool(item.get('evidence')), '缺少复核证据')
        for evidence in item['evidence']:
            require(evidence.get('viewed') is True and evidence.get('candidate_sha256') == review.get('sha256')
                    and digest(within(root, evidence['file'])) == evidence.get('sha256'), '复核证据缺失或变化')
    derived = 'rejected' if any(c['result'] == 'fail' for c in observations) else (
        'approved' if len(observations) == len(goals) and all(c['result'] == 'pass' for c in observations) else 'unreviewed')
    require(review.get('decision') == derived, '复核状态与实际观察不符')


def validate_visual(review, brief, role, root, policy, require_report=True, expected_call=None):
    """Validate the current stage's explicit approval and retained evidence."""
    if review.get('approval_basis') == 'pixel_equivalent_with_framing_check':
        return validate_inherited_visual(review, brief, role, root)
    require(review.get('decision') == 'approved', f'{role}视觉结论为{review.get("decision", "unreviewed")}')
    require(review.get('target_sha256') == target_binding(brief, role), f'{role}目标或来源版本变化，复核失效')
    reviewer_valid(review.get('reviewer'), policy)
    if require_report or review.get('report_file'):
        validate_review_integrity(review, brief, role, root, policy, expected_call)
    packet = within(root, review.get('packet_file'))
    require(digest(packet) == review.get('packet_sha256'), f'{role}复核任务包已变化')
    packet_data = read(packet)
    if packet_data.get('self_check_fields') and (require_report or review.get('report_file')):
        check_quality_observations(read(within(root, review['report_file'])))
    require(packet_data.get('candidate_sha256') == review.get('sha256') and
            packet_data.get('target_sha256') == review.get('target_sha256'), '复核任务包与图片/目标不符')
    require(packet_data.get('dependencies') == review.get('dependencies') and packet_data.get('reviewer_policy') == policy,
            '复核任务包的上游依赖或策略与当前记录不符')
    require(bool(review.get('processing')) == bool(packet_data.get('processing_sha256')),
            '处理链与实际复核任务包的类型不符')
    if packet_data.get('processing_sha256'):
        require(processing_binding(review.get('processing', [])) == packet_data['processing_sha256'], '处理记录与实际复核任务包不符')
        require(validate_processing(review['processing'], root) == review['sha256'], '处理版本链未到达当前复核图')
    for artifact in packet_data.get('evidence', []):
        require(digest(within(root, artifact['file'])) == artifact['sha256'], f'证据已变化：{artifact["id"]}')
        if artifact.get('layout_record'):
            validate_comparison(artifact, root)
    validate_check_records(review, brief, role, root)


def visual_binding(review):
    return fingerprint({'image': review['sha256'], 'target': review['target_sha256'], 'dependencies': review['dependencies']})


def dependency_binding(review):
    if review.get('approval_basis') == 'pixel_equivalent_with_framing_check':
        return dependency_binding(review['inherited_review'])
    return visual_binding(review)


def validate_inherited_visual(review, brief, role, root):
    require(review.get('decision') == 'approved', '无损处理尚未通过构图和背景自查')
    require(review.get('target_sha256') == target_binding(brief, role), '无损处理目标已变化')
    report_path = within(root, review['report_file'])
    require(digest(report_path) == review['report_sha256'], '处理自查报告已变化')
    report = read(report_path)
    maker_valid(report.get('reviewer'))
    require(report.get('kind') == 'processing-check' and report.get('result') == 'pass'
            and bool(report.get('framing_observation')) and bool(report.get('background_observation')),
            '须有制作模型实际构图/背景自查，不能自动继承批准')
    require(report.get('reviewer') == review.get('reviewer'), '处理自查方式与真实报告不符')
    packet_path = within(root, review['packet_file'])
    require(digest(packet_path) == review['packet_sha256'] == report.get('packet_sha256'), '处理自查任务包已变化')
    packet = read(packet_path)
    require(packet['stage'] == role and packet['call_id'] == report.get('call_id')
            and packet['candidate_sha256'] == review['sha256'] and packet['target_sha256'] == review['target_sha256']
            and packet['dependencies'] == review['dependencies'], '处理自查与图片/目标/上游不符')
    previous = review.get('inherited_review', {})
    require(fingerprint(previous) == packet.get('inherited_review_sha256') == review.get('inherited_review_sha256'),
            '源批准绑定已变化')
    require(previous.get('dependencies') == review['dependencies'] and previous.get('target_sha256') == review['target_sha256'],
            '只能复用相同目标和真实上游的批准')
    validate_visual(previous, brief, role, root, previous['reviewer_policy'])
    steps = review.get('processing', [])
    require(processing_binding(steps) == packet.get('processing_sha256') and
            validate_processing(steps, root) == review['sha256'], '处理版本链不符')
    step = steps[-1]
    require(step['input_sha256'] == previous['sha256'] and step['id'] == report['call_id'], '无损处理输入不是源批准版本')
    provenance = read(within(root, step['record_file']))
    proof = pixel_equivalence(image_bytes_for_review(previous, root, role), image_bytes_for_review(review, root, role),
                              provenance.get('operation'), provenance.get('offset'))
    require(proof is not None and proof == packet.get('pixel_equivalence') == step.get('pixel_equivalence'),
            '没有当前可验证的无损像素证明')
    require(review.get('dependency_binding_sha256', dependency_binding(review)) == dependency_binding(review),
            '无损处理的下游内容绑定不符')
    artifacts = {a['id']: a for a in packet['evidence']}
    viewed = report.get('viewed_evidence_ids', [])
    require({'whole', 'alpha_compare'} <= set(viewed) <= set(artifacts), '处理自查须实际看全图和明暗底对照')
    for artifact in artifacts.values():
        require(digest(within(root, artifact['file'])) == artifact['sha256'], '无损处理证据已变化')
        if artifact.get('layout_record'):
            validate_comparison(artifact, root)


def selection_binding(data):
    return fingerprint({key: data.get(key) for key in
                        ('recipe', 'attempts', 'baseline_call', 'required_calls', 'user_override_reason',
                         'reviewer_policy', 'baseline_approval_event', 'ranking', 'selected_call', 'processing',
                         'generation_limit', 'limit_selection')})


def processing_binding(steps):
    return fingerprint([{key: step.get(key) for key in ('id', 'input_sha256', 'output_sha256', 'record_file', 'record_sha256',
                                                      'pixel_equivalence', 'inherited_review_sha256')}
                        for step in steps])


def validate_processing(steps, root, input_hash=None):
    require(isinstance(steps, list) and bool(steps), '缺少实际处理版本链')
    current = input_hash or steps[0].get('input_sha256')
    for step in steps:
        require(step.get('input_sha256') == current, '后处理版本链不连续')
        require(digest(within(root, step['record_file'])) == step['record_sha256'], '后处理旁录已变化')
        provenance = read(within(root, step['record_file']))
        require((provenance.get('input_sha256') or provenance.get('source_sha256')) == current
                and provenance.get('output_sha256') == step.get('output_sha256'), '后处理真实记录与版本链不符')
        current = step['output_sha256']
    return current


def validate_calls(data, root, role):
    recipe = data.get('recipe', {})
    require(bool(recipe.get('tool')) and isinstance(recipe.get('parameters'), dict), '缺少真实工具/参数')
    prompt = within(root, recipe.get('prompt_file'))
    require(digest(prompt) == recipe.get('prompt_sha256') and bool(prompt.read_text(encoding='utf-8-sig').strip()),
            '真实提示词已变化或缺失')
    require(isinstance(recipe.get('inputs'), list) and bool(recipe['inputs']), '缺少真实有序输入')
    for entry in recipe['inputs']:
        require(bool(entry.get('purpose')) and digest(within(root, entry['file'])) == entry['sha256'], '输入已变化或用途缺失')
    attempts = data.get('attempts', [])
    require(isinstance(attempts, list) and bool(attempts), f'{role}缺少真实调用')
    ids = set()
    for call in attempts:
        identifier = call.get('id')
        require(isinstance(identifier, str) and bool(identifier.strip()) and identifier not in ids, '调用id缺失或重复')
        ids.add(identifier)
        require(digest(within(root, call.get('evidence_file'))) == call.get('evidence_sha256'), '真实调用证据缺失或变化')
        require(call.get('recipe_sha256') == recipe_binding(recipe), '调用配置与本组选优配置不符')
        if call.get('error'):
            require(not call.get('output'), '失败调用不能冒充成功输出')
        else:
            output = call.get('output')
            selection_image(output, root, role)
    return {call['id']: call for call in attempts}


def approved_at_limit(role, choice, workflow, identifier, brief, root, dependencies):
    entries = [h['selection'] for h in workflow.get('history', []) if h['stage'] == role] + [choice]
    for entry in entries:
        review = entry.get('reviews', {}).get(identifier)
        if (review and review.get('decision') == 'approved' and review.get('target_sha256') == target_binding(brief, role)
                and review.get('dependencies') == dependencies):
            call = next(c for c in entry['attempts'] if c['id'] == identifier)
            validate_visual(review, brief, role, root, entry['reviewer_policy'], expected_call=call)
            return review
    return None


def validate_limit_selection(choice, data, root, role, target, dependencies, brief):
    """At a real ceiling, reuse valid approval or honestly adopt an unchecked image."""
    selection = choice.get('limit_selection', {})
    budget = stage_budget(role, choice, read(within(root, WORKFLOW)))
    require(bool(budget['limit_reason']), '尚未达到单阶段生成上限，不能跳过验收')
    require(selection.get('budget_sha256') == fingerprint(budget), '上限选优的调用历史或计数已变化')
    report_path = within(root, selection.get('report_file'))
    require(digest(report_path) == selection.get('report_sha256'), '上限选优报告已变化')
    report = read(report_path)
    maker_valid(report.get('reviewer'))
    require(report.get('kind') == 'limit-selection' and bool(report.get('selection_reason')), '缺少制作模型的上限选优依据')
    for key in ('preferred_call', 'selection_reason', 'viewed_call_ids', 'reviewer'):
        require(report.get(key) == selection.get(key), '上限选优与真实报告不符')
    calls = {}
    for entry in budget['ledger']:
        if entry['attempts']:
            entries = validate_calls(entry, root, role)
            require(not (set(entries) & set(calls)), '上限历史调用id重复')
            calls.update(entries)
    available = {key for key, call in calls.items() if not call.get('error')}
    viewed = report.get('viewed_call_ids', [])
    require(isinstance(viewed, list) and len(viewed) == len(set(viewed)) and set(viewed) == available,
            '上限选优须实际比较全部可用候选，不把失败调用当图片')
    selected = report.get('preferred_call')
    require(selected in available and choice.get('selected_call') == selected, '上限所选图不是实际可用候选')
    reused = approved_at_limit(role, choice, read(within(root, WORKFLOW)), selected, brief, root, dependencies)
    require(selection.get('reused_review_sha256') == (fingerprint(reused) if reused else None), '上限选优的原批准已失效')
    require(data.get('selection_sha256') == selection_binding(choice), '当前状态未绑定上限选择')
    require(data.get('limit_selection_sha256') == selection['report_sha256'], '当前状态未绑定上限选优报告')
    current_hash = calls[selected]['output']['sha256']
    if choice.get('processing'):
        current_hash = validate_processing(choice['processing'], root, current_hash)
    require(data.get('sha256') == current_hash, '上限选图或处理版本不符')
    image_bytes_for_review(data, root, role)
    inherited = choice.get('processing_reviews', {}).get(choice.get('processing', [{}])[-1].get('id')) if choice.get('processing') else None
    approved = bool(reused and (not choice.get('processing') or inherited and inherited.get('decision') == 'approved'))
    require(data.get('decision') == ('approved' if approved else 'selected_unreviewed'), '上限选优不能冒充视觉通过')
    if approved:
        actual = inherited or reused
        require(data.get('review_sha256') == fingerprint(actual), '当前上限选择未绑定原批准')
        validate_visual(data, brief, role, root, choice['reviewer_policy'])
        binding = visual_binding(data)
        content = dependency_binding(data)
    else:
        binding = fingerprint({'image': current_hash, 'target': target, 'dependencies': dependencies,
                               'limit_selection': selection['report_sha256']})
        content = binding
    require(data.get('binding_sha256') == binding, '上限选优版本绑定不符')
    require(data.get('dependency_binding_sha256', content) == content, '上限内容绑定不符')
    return {'binding': binding, 'dependency_binding': content, 'decision': data['decision']}


def check_v2(record, root, stage=None, before=None, expected_task_mode=None, baseline=False):
    require(record.get('schema_version') == 2, '仅支持schema_version:2的当前验收记录')
    root = Path(root).resolve()
    require(not (stage and before), 'stage和before不能同时指定')
    require(not baseline or (stage and not before), '首张检查须指定stage，不能批准下游')
    require(stage is None or stage in ROLES, '未知视图')
    require(before is None or before in ROLES, '未知下游视图')
    require(record.get('brief_file', BRIEF) == BRIEF and record.get('selection_file', SELECTION) == SELECTION,
            'v2使用规范的制作记录路径；移动项目根目录即可，不单独改名底稿/选择文件')
    brief = read(within(root, record.get('brief_file', BRIEF)))
    require(brief.get('schema_version') == 2 and brief.get('rules_version') == 2 and record.get('rules_version') == 2,
            'v2记录须声明schema_version和rules_version:2')
    scope = brief.get('scope', 'full_sheet')
    require(scope in ('full_sheet', 'single_stage'), '未知验收scope')
    require(scope != 'single_stage' or (stage is not None and before is None),
            '单阶段记录只支持--stage，不批准完整套或下游制作')
    mode = brief.get('task_mode', 'production')
    require(mode in ('production', 'existing_review'), '未知task_mode')
    require(expected_task_mode is None or expected_task_mode == mode, '交付与验收task_mode不一致')
    selection = read(within(root, record.get('selection_file', SELECTION))) if mode == 'production' else {'stages': {}}
    if mode == 'production':
        require(selection.get('schema_version') == 2 and selection.get('rules_version') == 2, 'v2选择记录版本不符')
    checked, errors, finished = [], [], {}

    def approve(role, baseline_only=False):
        if role in finished and not baseline_only:
            return finished[role]
        target = target_binding(brief, role)
        used = {key for goal in brief['checks'][role] for key in goal['source_ids']}
        for key in used:
            source = brief['sources'][key]
            require(digest(within(root, source['file'])) == source['sha256'], f'{role}来源{key}已变化')
        data = record.get('stages', {}).get(role, {})
        require(data.get('target_sha256') == target, f'{role}目标版本变化或缺少当前记录')
        expected_deps = {up: approve(up) for up in DEPENDENCIES[role]} if brief.get('scope', 'full_sheet') == 'full_sheet' else {}
        require(data.get('dependencies') == expected_deps, f'{role}引用的上游版本/目标不是当前版本')
        policy = data.get('reviewer_policy', {'mode': 'prefer_subagent'})
        selection_status = 'not_required'
        current_call = None
        if mode == 'production':
            choice = selection.get('stages', {}).get(role, {})
            if choice.get('limit_selection'):
                require(not baseline_only, '上限选优没有首张合格批准')
                adopted = validate_limit_selection(choice, data, root, role, target, expected_deps, brief)
                binding = adopted['binding']
                finished[role] = adopted['dependency_binding']
                checked.append({'role': role, 'image': str(within(root, data['image'])), 'sha256': data['sha256'],
                                'check_count': 0, 'binding_sha256': binding, 'selection_status': 'generation_limit',
                                'dependency_binding_sha256': adopted['dependency_binding'],
                                'reviewer_mode': data['reviewer']['mode'], 'decision': adopted['decision']})
                return adopted['dependency_binding']
            calls = validate_calls(choice, root, role)
            baseline_id = choice.get('baseline_call')
            require(baseline_id in calls and not calls[baseline_id].get('error'), f'{role}缺少合格首张')
            policy = choice.get('reviewer_policy', {'mode': 'prefer_subagent'})
            first_review = choice.get('reviews', {}).get(baseline_id, {})
            require(first_review.get('sha256') == calls[baseline_id]['output']['sha256'], '首张验收与实际调用不符')
            require(first_review.get('dependencies') == expected_deps, '首张批准引用的上游已失效')
            validate_visual(first_review, brief, role, root, policy, expected_call=calls[baseline_id])
            if baseline_only:
                return fingerprint({'image': first_review['sha256'], 'target': target, 'dependencies': expected_deps})
            count = choice.get('required_calls', 3)
            require(type(count) is int and 1 <= count <= 3, 'required_calls须为1..3')
            require(count == 3 or bool(choice.get('user_override_reason')), '减少候选须有用户明确要求')
            group = [call for call in choice['attempts'] if call['id'] == baseline_id or call.get('phase') == 'extra']
            require(len(group) == count and group[0]['id'] == baseline_id, f'{role}必须固定{count}次组选优调用，不能补抽')
            first_approval = choice.get('baseline_approval_event')
            require(type(first_approval) is int and all(type(c.get('event')) is int and c['event'] > first_approval
                    for c in group[1:]), '追加调用须发生在首张批准之后')
            ranking = choice.get('ranking', {})
            require(bool(ranking.get('selection_reason')) and ranking.get('preferred_call') in calls, '缺少真实比较选择')
            maker_valid(ranking.get('reviewer'))
            preferred = ranking['preferred_call']
            require(preferred in {c['id'] for c in group if not c.get('error')}, '不能选失败调用或本组外候选')
            selected = choice.get('selected_call')
            require(selected in (baseline_id, preferred), '最终选择须为优胜候选或合格保底图')
            if selected != preferred:
                preferred_review = choice.get('reviews', {}).get(preferred, {})
                if calls[preferred].get('self_check', {}).get('result') == 'fail' and not preferred_review:
                    validate_self_check(calls[preferred], root, require_pass=False)
                else:
                    require(preferred_review.get('decision') == 'rejected' or
                            (preferred_review.get('decision') == 'unreviewed' and preferred_review.get('supplement_round') == 1),
                            '回退须有优胜候选失败或补证后仍待核实记录')
                    validate_review_integrity(preferred_review, brief, role, root, policy, calls[preferred])
            final_review = choice.get('reviews', {}).get(selected, {})
            require(final_review.get('sha256') == calls[selected]['output']['sha256'], '最终验收与所选调用不符')
            validate_visual(final_review, brief, role, root, policy, expected_call=calls[selected])
            current_call = calls[selected]
            current_hash = calls[selected]['output']['sha256']
            for step in choice.get('processing', []):
                require(step.get('input_sha256') == current_hash, '后处理版本链不连续')
                require(digest(within(root, step['record_file'])) == step['record_sha256'], '后处理旁录已变化')
                provenance = read(within(root, step['record_file']))
                require((provenance.get('input_sha256') or provenance.get('source_sha256')) == current_hash and
                        provenance.get('output_sha256') == step.get('output_sha256'), '后处理真实记录与版本链不符')
                current_hash = step['output_sha256']
            if choice.get('processing'):
                final_review = choice.get('processing_reviews', {}).get(choice['processing'][-1]['id'], {})
                require(final_review.get('sha256') == current_hash, '当前处理版本尚未完整复核')
                require(processing_binding(final_review.get('processing', [])) == processing_binding(choice['processing']),
                        '当前选择的处理链不是实际复核的处理链')
                current_call = choice['processing'][-1]
            require(data.get('review_sha256') == fingerprint(final_review), '当前状态未绑定所选版本的复核')
            require(data.get('selection_sha256') == selection_binding(choice), '当前状态未绑定本阶段选择')
            require(data.get('sha256') == final_review.get('sha256'), '当前图不是所选候选')
            selection_status = 'limited_tool_error' if any(c.get('error') for c in group) else 'complete'
        validate_visual(data, brief, role, root, policy, expected_call=current_call)
        binding = fingerprint({'image': data['sha256'], 'target': target, 'dependencies': expected_deps})
        require(data.get('binding_sha256') == binding, '当前批准绑定不符')
        content_binding = dependency_binding(data)
        require(data.get('dependency_binding_sha256', content_binding) == content_binding, '当前下游内容绑定不符')
        finished[role] = content_binding
        checked.append({'role': role, 'image': str(within(root, data['image'])), 'sha256': data['sha256'],
                        'check_count': len(data['checks']), 'binding_sha256': binding, 'dependency_binding_sha256': content_binding,
                        'approval_basis': data.get('approval_basis', 'visual_review'),
                        'selection_status': selection_status, 'reviewer_mode': data['reviewer']['mode'], 'decision': 'approved'})
        return content_binding

    targets = DEPENDENCIES[before] if before else ((stage,) if stage else ROLES)
    for role in targets:
        try:
            approve(role, baseline)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append(str(exc))
    all_approved = all(item['decision'] == 'approved' for item in checked)
    return {'scope': 'review_records_and_versions', 'model_visual_checks': False, 'criteria_version': 2,
            'task_mode': mode, 'mode': 'baseline' if baseline else 'before_stage' if before else 'stage' if stage else 'full_sheet',
            'record_integrity_valid': not errors, 'recorded_approval_valid': not errors and all_approved,
            'downstream_ready': not errors and not baseline and scope == 'full_sheet',
            'visual_assessment': ('recorded_only' if all_approved else 'selected_without_approval') if not errors else 'not_approved',
            'checked': checked, 'errors': errors}
