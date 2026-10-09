"""Read-only report preflight. Collect field errors without recording visual approval."""
from pathlib import Path

from PIL import Image

from check_delivery import within
from review_policy import check_quality_observations, check_self_observations, primary_kinds
from review_v2 import (digest, maker_valid, read, reviewer_valid, stage_budget, target_binding,
                       validate_self_check)


def production_input_errors(report):
    """Freeze the ordinary review envelope; low-level special interfaces stay separate."""
    errors = []

    def known(value, allowed, path):
        if isinstance(value, dict):
            for key in sorted(set(value) - set(allowed)):
                errors.append({'field': path + '.' + key, 'message': '未知字段；沿用报告模板，不自行改格式'})

    known(report, {'kind', 'call_id', 'packet_sha256', 'reviewer', 'viewed_evidence_ids', 'checks',
                   'quality_observations', 'background_policy', 'supplement_round', 'extra_evidence',
                   'reviewer_replacement_reason'}, 'report')
    known(report.get('reviewer'), {'mode', 'model', 'agent_id', 'reason_code', 'reason'}, 'reviewer')
    known(report.get('quality_observations'), {'hair', 'face'}, 'quality_observations')
    known(report.get('background_policy'), {'mode', 'alpha_source', 'alpha_limitation', 'reason'}, 'background_policy')
    checks = report.get('checks', [])
    if isinstance(checks, list):
        for index, item in enumerate(checks):
            known(item, {'id', 'result', 'reference_observation', 'candidate_observation', 'comparison_basis',
                         'evidence_ids', 'empty_background_samples', 'issue_key'}, f'checks[{index}]')
    return errors


def production_evidence_errors(root, role, report, flow):
    """Supplement metadata is produced by the script, never relabelled by the reviewer."""
    if report.get('supplement_round') != 1:
        return [{'field': 'extra_evidence', 'message': '首次复核只用预制证据；确实待核实时走一次supplement入口'}] if report.get('extra_evidence') else []
    try:
        ref = flow['stages'][role].get('supplement')
        if not ref or digest(root / ref['file']) != ref['sha256']:
            raise ValueError('先执行返回的supplement入口，不能手写或替换补证批次')
        batch = read(root / ref['file'])
        if batch['packet_sha256'] != report.get('packet_sha256') or report.get('extra_evidence') != batch['evidence']:
            raise ValueError('沿用脚本生成的extra_evidence，不改用途、标签或来源')
        for entry in batch['evidence']:
            if digest(root / entry['file']) != entry['sha256']:
                raise ValueError('脚本补证文件已变化，不沿用旧引用')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return [{'field': 'extra_evidence', 'message': str(exc)}]
    return []


def report_errors(root, role, report, values):
    brief, state, selection, flow = values
    errors = []

    def need(condition, field, message):
        if not condition:
            errors.append({'field': field, 'message': message})

    def attempt(field, fn):
        try:
            fn()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append({'field': field, 'message': str(exc)})

    choice = selection['stages'].get(role)
    if choice is None:
        return [{'field': 'stage', 'message': '先prepare本阶段'}]
    if not isinstance(report, dict):
        return [{'field': 'report', 'message': '报告须为JSON对象'}]
    kind = report.get('kind', 'visual')
    need(kind in ('visual', 'self-check', 'selection', 'limit-selection', 'processing-check'), 'kind', '未知报告种类')
    attempt('reviewer', lambda: maker_valid(report.get('reviewer')) if kind != 'visual'
            else reviewer_valid(report.get('reviewer'), choice['reviewer_policy']))
    budget = stage_budget(role, choice, flow)
    if brief['task_mode'] == 'production' and budget['limit_reason'] and kind not in ('limit-selection', 'processing-check'):
        need(False, 'kind', '已到单阶段上限，由制作模型选优，不再资格复核')
    attempt('target_sha256', lambda: target_binding(brief, role))
    if kind in ('selection', 'limit-selection'):
        if kind == 'limit-selection':
            need(bool(budget['limit_reason']), 'kind', '尚未达到单阶段生成上限')
            need(not choice.get('limit_selection'), 'kind', '已完成上限选优，不重复更换')
            calls = [c for entry in budget['ledger'] for c in entry['attempts']]
        else:
            need(not choice.get('ranking'), 'kind', '本组已完成排序，不重新选图')
            baseline = choice.get('baseline_call')
            need(baseline is not None, 'kind', '首张尚未批准')
            calls = [c for c in choice['attempts'] if c['id'] == baseline or c['phase'] == 'extra']
            need(len(calls) == choice['required_calls'], 'viewed_call_ids', '先完成固定追加调用')
        available = {c['id'] for c in calls if not c.get('error')}
        viewed = report.get('viewed_call_ids', [])
        need(isinstance(viewed, list) and all(isinstance(i, str) for i in viewed)
             and len(viewed) == len(set(viewed)) and set(viewed) == available,
             'viewed_call_ids', '须明确看过全部可用候选，不包含失败调用')
        need(isinstance(report.get('preferred_call'), str) and report['preferred_call'] in available,
             'preferred_call', '须选择实际可用候选')
        need(bool(report.get('selection_reason')), 'selection_reason', '须写具体选择依据')
        return errors
    calls = choice['attempts'] + choice.get('processing', [])
    call = next((c for c in calls if c['id'] == report.get('call_id')), None)
    if call is None or call.get('error') or not call.get('packet_file'):
        need(False, 'call_id', '须对应有复核任务包的真实候选/处理版本')
        return errors
    try:
        packet = read(within(root, call['packet_file']))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        need(False, 'packet_file', str(exc))
        return errors
    need(report.get('packet_sha256') == digest(within(root, call['packet_file'])), 'packet_sha256', '须绑定实际任务包')
    need(packet['target_sha256'] == target_binding(brief, role), 'target_sha256', '复核目标已变化')
    need(packet['dependencies'] == state['stages'][role]['dependencies'], 'dependencies', '上游已变化')
    if kind == 'self-check':
        need(call in choice['attempts'] and call['id'] not in choice.get('reviews', {}),
             'call_id', '简单自评须对应尚未复核的生成候选')
        need(report.get('result') in ('pass', 'fail'), 'result', '须写pass/fail')
        need(bool(report.get('observation')), 'observation', '须写实际整体观察')
        attempt('self_check_observations', lambda: check_self_observations(report, packet))
        need(report.get('result') != 'fail' or bool(report.get('issue_key')), 'issue_key', '自评失败须有稳定问题id')
        need(not call.get('self_check'), 'call_id', '已有自评，不覆盖历史')
        return errors
    artifacts = {a['id']: a for a in packet['evidence']}
    extra = report.get('extra_evidence', [])
    need(isinstance(extra, list), 'extra_evidence', '补证须为列表')
    extra = extra if isinstance(extra, list) else []
    for index, spec in enumerate(extra):
        if not isinstance(spec, dict):
            need(False, f'extra_evidence[{index}]', '补证须为对象')
            continue
        valid_id = isinstance(spec.get('id'), str) and bool(spec['id']) and spec['id'] not in artifacts
        need(valid_id, f'extra_evidence[{index}].id', '证据id缺失、无效或重复')
        need(spec.get('kind') in ('whole', 'native', 'reduced', 'background', 'source', 'upstream'), f'extra_evidence[{index}].kind', '补证种类无效')
        if valid_id:
            artifacts[spec['id']] = spec
        def check_file():
            path = Path(spec.get('file', ''))
            digest(path if path.is_absolute() else within(root, spec.get('file')))
        attempt(f'extra_evidence[{index}].file', check_file)
    viewed = report.get('viewed_evidence_ids', [])
    valid_viewed = isinstance(viewed, list) and all(isinstance(i, str) for i in viewed) and len(viewed) == len(set(viewed)) and set(viewed) <= set(artifacts)
    need(valid_viewed, 'viewed_evidence_ids', '查看列表无效、重复或含未知证据')
    viewed = set(viewed) if valid_viewed else set()
    if kind == 'processing-check':
        need(bool(call.get('pixel_equivalence')), 'call_id', '仅无损处理使用简短自查')
        need(call is (choice.get('processing') or [None])[-1] and call['id'] not in choice.get('processing_reviews', {}),
             'call_id', '须对应尚未自查的最新处理版本')
        need(report.get('result') in ('pass', 'fail', 'pending'), 'result', '须写pass/fail/pending')
        for field in ('framing_observation', 'background_observation'):
            need(bool(report.get(field)), field, '须写实际观察')
        need({'whole', 'alpha_compare'} <= viewed, 'viewed_evidence_ids', '须实际看全图及明暗底对照')
        return errors
    if call.get('phase'):
        attempt('self_check', lambda: validate_self_check(call, root))
    else:
        need(call is (choice.get('processing') or [None])[-1], 'call_id', '只复核当前处理版本')
        need(not call.get('pixel_equivalence'), 'kind', '无损处理只需processing-check，不重复完整复核')
    expected = packet.get('review_context', {}).get('agent_id')
    from review_policy import validate_stage_reviewer, check_review_coverage
    attempt('reviewer.agent_id', lambda: validate_stage_reviewer(root, role, report))
    attempt('evidence_coverage', lambda: check_review_coverage(packet, report))
    if expected and isinstance(report.get('reviewer'), dict) and report['reviewer'].get('mode') == 'subagent':
        need(report['reviewer'].get('agent_id') == expected or bool(report.get('reviewer_replacement_reason')), 'reviewer.agent_id', '返修交回原代理；替换须记录原因')
    round_number = report.get('supplement_round', 0)
    need(type(round_number) is int and round_number in (0, 1), 'supplement_round', '仅允许一次补证')
    previous = choice.get('reviews' if call.get('phase') else 'processing_reviews', {}).get(call['id'])
    need((previous is None and round_number == 0) or
         (previous is not None and previous['decision'] == 'unreviewed' and previous.get('supplement_round', 0) == 0 and round_number == 1),
         'supplement_round', '首次为0；仅待核实可补证一次，不重审已批准/已拒绝版本')
    goals = {g['id']: g for g in brief['checks'][role]}
    checks = report.get('checks', [])
    need(isinstance(checks, list) and bool(checks), 'checks', '至少有一条实际观察')
    if not isinstance(checks, list):
        return errors
    identifiers = set()
    for index, item in enumerate(checks):
        field = f'checks[{index}]'
        if not isinstance(item, dict) or not isinstance(item.get('id'), str) or item['id'] not in goals or item['id'] in identifiers:
            need(False, field + '.id', '检查项未知或重复')
            continue
        identifiers.add(item['id'])
        group = goals[item['id']]['group']
        need(item.get('result') in ('pass', 'fail', 'pending'), field + '.result', '须写pass/fail/pending')
        for key in ('reference_observation', 'candidate_observation') + (('comparison_basis',) if group in ('identity', 'design', 'spatial') else ()):
            need(bool(item.get(key)), field + '.' + key, '须写具体观察或比较依据')
        ids = item.get('evidence_ids', [])
        valid_ids = isinstance(ids, list) and bool(ids) and all(isinstance(i, str) for i in ids) and set(ids) <= viewed
        need(valid_ids, field + '.evidence_ids', '须引用实际看过的证据')
        if valid_ids:
            need(any(artifacts[i].get('kind') in primary_kinds(group) for i in ids), field + '.evidence_ids', '缺少当前分组的主要证据')
        if group == 'background':
            policy = report.get('background_policy', packet['background_policy'])
            need(isinstance(policy, dict), 'background_policy', '须为对象')
            if not isinstance(policy, dict):
                continue
            mode = policy.get('mode')
            need(mode in ('transparent', 'exact_white', 'near_white_rgb_fallback'), 'background_policy.mode', '背景模式无效')
            if mode == 'transparent':
                need(policy.get('alpha_source') in ('model', 'tool_extraction'), 'background_policy.alpha_source', '真实Alpha来源须为model或tool_extraction')
                backgrounds = {b for i in (ids if valid_ids else []) if i in artifacts and artifacts[i].get('kind') == 'background'
                               for b in artifacts[i].get('backgrounds', [artifacts[i].get('background')])}
                need({'light', 'dark'} <= backgrounds, field + '.evidence_ids', '缺少深浅底证据')
            if mode == 'near_white_rgb_fallback':
                need(policy.get('alpha_limitation') in ('input', 'output', 'both') and bool(policy.get('reason')), 'background_policy', '须记录实际透明度限制及原因')
            samples = item.get('empty_background_samples', [])
            need(isinstance(samples, list), field + '.empty_background_samples', '采样须为列表')
            if item.get('result') == 'pass':
                need(isinstance(samples, list) and any(isinstance(s, dict) and s.get('confirmed_empty') is True for s in samples),
                     field + '.empty_background_samples', '须实际确认至少一处标号空白；其他点可保留false，不必删除')
            for sample_index, sample in enumerate(samples if isinstance(samples, list) else []):
                position = f'{field}.empty_background_samples[{sample_index}]'
                need(isinstance(sample, dict) and type(sample.get('confirmed_empty')) is bool,
                     position + '.confirmed_empty', '实际确认空白填true，非空或不确定保留false')
                xy = sample.get('xy') if isinstance(sample, dict) else None
                with Image.open(within(root, call['output']['file'])) as image:
                    need(isinstance(xy, list) and len(xy) == 2 and all(type(v) is int for v in xy)
                         and 0 <= xy[0] < image.width and 0 <= xy[1] < image.height, position + '.xy', '采样坐标无效')
    if identifiers == set(goals) and all(isinstance(c, dict) and c.get('result') == 'pass' for c in checks):
        attempt('quality_observations', lambda: check_quality_observations(report))
    elif not any(isinstance(c, dict) and c.get('result') == 'fail' for c in checks):
        need(identifiers == set(goals), 'checks', '复核报告遗漏检查项；完成本阶段所需查看后一次返回，缺字段不能作为补证轮次')
    return errors
