"""Shared defaults for generation targets and visual review; no visual decisions."""
from prompt_templates import EYE_ACCEPTANCE, ARMS_ACCEPTANCE

DEFAULT_GENERATION_LIMIT = 6
CROP_ACCEPTANCE = (
    '所有阶段允许轻微裁切：头发、羽毛、尖刺或饰件末端被画边切掉一点，只要主体、身份与主要造型仍清楚且整体不明显残缺，直接通过构图项。'
    '触边、没有留白同样通过；不要求每个尖端完整，不量裁切百分比，也不因放大后发现末端缺一点而返工或补证。'
    '只有正常整体观看已明显残缺、影响主体或主要造型才拒绝，并说明实际影响；提示词的完整入画与留白是生成目标，不提高验收门槛。'
)
FOLLOWUP_ACCESSORIES = (
    '本阶段第二次及后续复核（返修、补证、所选新增候选均适用）放宽饰品判定：只要不明显违和、突兀，就当它是如此设计，相关项直接pass。'
    '位置、长度、形状、连接形式、承载面或前后分布的差异本身不是否决理由；不要仅因不同于原图、上游或制作方案就fail/pending。'
    '制作端自拟的禁止位置、完全无链饰等目标及上轮失败意见不能覆盖此尺度，也不能换成design/spatial等另一检查项继续拒绝同一合理差异。'
    '仍实际看图并写差异与接受依据；只有整体观看明显违和、突兀，例如明显悬浮、穿模或连接完全不成立，才继续拒绝并说明具体表现。'
    '用户明确指定必须修正的具体设计仍遵从；制作模型的推断、提示词和空间记录不算用户指令。其他身份、画质等检查按原尺度执行。'
)
ACCESSORY_ACCEPTANCE = ('首次复核饰品按原图核对承载物、连接、侧别与可见面，区分看不见与清楚无饰；'
                        '原图未展示背面不能证明背面无饰，制作方案不是原图事实。'
                        '首次按此检查，第二次及后续以以下放宽规则为准。' + FOLLOWUP_ACCESSORIES)


def acceptance_instruction(context=None, subsequent=False):
    """Expose the applicable scale at handoff; never decide a visual result."""
    return CROP_ACCEPTANCE + (FOLLOWUP_ACCESSORIES if subsequent or (context or {}).get('mode') == 'followup'
                              else ACCESSORY_ACCEPTANCE)


REVIEWER_TASK = ('独立复核当前候选。读取任务包、generation_context中的实际生图提示词与有序参考用途、报告模板、必要原始证据、候选及有效上游；'
                 '只处理本阶段；完成当前所需查看与报告后一次返回最终路径和结论。不要向主模型发中途合格判断、进度消息或催确认，也不调度其他代理。'
                 '不接收制作模型的自评结论或推荐答案。证据已由主流程一次生成，直接查看，不自行裁图、拼图或生成背景。'
                 '提示词只说明参考分工与生成意图，不是图片合格证明，也不是更严格的验收门槛。'
                 '按当前阶段和evaluation_scope核对可见内容；侧面头图补头饰或发型，不要求正面特写展示侧脸轮廓。'
                 '当前角度不可见的部分说明正常投影/遮挡，并核对可见部分及矛盾；没有矛盾可通过该项，不能仅因视角不同拒绝或要求补证。'
                 '子代理执行时reviewer.mode固定subagent，填本人实际agent_id；完整自评按实际模式与原因记录。'
                 '遵循review_sequence：先看按检查项组织的参考—候选拼图，'
                 '再看判断原生质量确实需要的局部和缩小图。背景只看alpha_compare深浅底拼图，不再分别打开light/dark。'
                 '已在拼图看清的全图、参考或局部不重复打开；只有看不清或矛盾才补更合适证据。'
                 '服装须比较其相对颈根、肩、上臂的位置和露肤范围，不能以同色同款代替穿戴位置判断。'
                 '背面设计、裙摆、拖尾及脚部遮挡以原始背面/侧后依据为准；正面剪影是低优先级身体比例辅助，不作服装轮廓蒙版。'
                 '不能因剪影露脚要求后视也露脚，不能为露鞋抬高或分开原本盖脚的裙摆。'
                 '正面原图只用于材料纹理和表面光影，不以其服装裁片、裙层或装饰位置判定背面。'
                 'planned_检查项来自逐图选材计划：原始材料局部不能被上游生成图取代，须实际对比纹理、清晰度和材质表现。'
                 + ACCESSORY_ACCEPTANCE + CROP_ACCEPTANCE +
                 '饰品项具体写承载物、可见面、连接处和遮挡；不能只写同款同色或整体位置自然。'
                 '制作目标是待核验的方案，不能当作原图事实；目标误写或与后方原图冲突时按原图指出差异。'
                 + EYE_ACCEPTANCE +
                 '自然遮挡的后片只查可见边界与连接，无明显矛盾可按合理设计通过，不要求看穿头发。'
                 '长袖可自然遮手，不能仅因未露手指或提示词自拟露指要求拒绝；只查手臂姿势与可见连接是否明显不合理。'
                 'alpha_compare的编号对应采样候选，实际确认空白填true，不确定或非空保留false，无需删点或查像素。'
                 '按任务包尺度判断，分别记录构图、头发和面部实际表现；返修优先复查上次问题，合理细节偏差就当如此设计。'
                 '返回实际观察、证据id和pass/fail/pending，不生图、不排序、不做文件/哈希检查。')
REVIEWER_TASK += ('报告各项evidence_requirement列出主要证据选项和pass所需覆盖的参考；仅引用真正看过的项，'
                 '这些提示不代表已查看。预检一次返回全部字段错误，在本次交接内修正。'
                 '报告无效与视觉待核实分别处理：缺字段、引用错误不改成pending、不请求supplement或重生图；'
                 'pending只表示实际看图后仍不确定的视觉问题。')
FRAMING = {
    'head': '3:4头部特写，头脸为主体、面部大致居中，主要轮廓基本完整，下缘在胸部及以上。零散飞丝触边或出画、长发穿出左右画边允许，不要求锁骨裁切或固定留白比例。',
    'front': '9:16朝前全身，主体及主要造型基本完整，位置大致合理。静态直立，双脚并列落地，无明显跨步、前后错脚或踮脚；允许自然小偏移，不量精确对齐。衣裙自然遮脚允许，不要求露出鞋底。',
    'back': '9:16背向全身，不回头，主体及主要造型基本完整，位置大致合理，不要求精确180度或固定边距百分比。衣裙遮脚按后方原图保留，不要求露出双鞋或鞋底。静态直立，不作跨步姿态。',
    'left': '9:16朝画面左的近侧面全身，允许自然小幅转角；明显约45度三分之四视角、朝向错误或严重头身扭转才失败，不测精确90度。主体及主要造型基本完整、位置大致合理，不要求固定边距百分比。静态直立，双脚自然投影重叠及衣裙遮脚允许，不要求同时展示双鞋。',
}
QUALITY = (
    '按实际像素占比及原画风分别检查头发与面部。头发的发型、发量大致正确，主要发束边界和层次清楚；'
    '发型/走向成立不代表清晰度通过；重要区域明显模糊、涂抹或糊成片即失败，不要求问题达到大面积。'
    '面部无无依据的灰脏、色块、涂抹或融化；'
    '区分合理妆容、阴影和真实标记。原图模糊或成像脏污不作为复制目标，应恢复有依据的清晰结构；'
    '不以锐化、磨皮或新增纹理代替恢复。用户明确指出的模糊/脏污按本次已确认修复目标处理，'
    '不能自行解释为风格、妆容或阴影而撤销。不可见的面部或头发说明实际原因。'
)
for _role in ('front', 'back', 'left'):
    FRAMING[_role] += ARMS_ACCEPTANCE
ACCEPTANCE_STANDARD = (
    '整体和细节都实际看，以可接受为准。用户明确要求优先；模型自拟目标不能提高以下默认门槛。'
    + EYE_ACCEPTANCE +
    ACCESSORY_ACCEPTANCE + CROP_ACCEPTANCE +
    '这适用于全部检查项，包括design/spatial中的模型自拟留白、位置和露脚目标；'
    '生成提示词中的留白是取景缓冲，未达到留白不能单独判fail/pending。'
    '裁切须按整体影响判断；仅末端缺一点或接触上沿不能判失败。'
    + ' '.join(FRAMING.values()) + QUALITY +
    '少量飞丝、交叉、彩边或局部笔触瑕疵允许，不逐根核发丝。对齐大致合理即可。'
    '特写服装对照原图颈根、肩线、上臂位置和露肤范围；本应画外可以不出现。'
    '松垂上臂的外套被明显上提到肩颈、露肩变成正常穿肩等属于穿戴关系改变；合理小偏移允许。'
    '侧面服装露面结合实际转角和布料形状判断，不因可见少量前面图案拒绝。'
    '背面优先按原始背面/侧后图检查，前后可见的裙层、后摆、裁片和装饰不必相同。'
    '后方裙摆长度、拖尾和脚部遮挡优先于正面剪影；剪影仅辅助身体大比例，不提供服装外轮廓或露脚答案。'
    '合理的头发、饰带遮挡不是待核实理由；只核验可见部分的穿戴、边界和连接，'
    '未出现矛盾时说明遮挡范围即可通过该项，不声称已经证实隐藏接缝。关键可见关系确有冲突或看不清才补证。'
    + FOLLOWUP_ACCESSORIES
)

# Keep generation's full-frame aim, but bind every framing review to the same lenient scale.
for _role in FRAMING:
    FRAMING[_role] += CROP_ACCEPTANCE


def normalize_targets(role, goals):
    """Default framing is canonical. A stricter user target needs its actual quote."""
    import copy
    result = copy.deepcopy(goals)
    for goal in result:
        if goal.get('group') == 'look' and goal.get('aspect') == 'framing':
            if goal.get('requirement_origin') == 'user':
                if not isinstance(goal.get('user_requirement'), str) or not goal['user_requirement'].strip():
                    raise ValueError('用户构图覆盖须记录user_requirement原话')
            else:
                goal['target'] = FRAMING[role]
                goal['requirement_origin'] = 'default'
        # The acceptance standard remains authoritative for free-form spatial goals.
    return result


def primary_kinds(group):
    return {'native_quality': {'native'}, 'reduced_quality': {'reduced'},
            'background': {'background'}}.get(group, {'whole', 'source', 'upstream', 'native', 'comparison'})


def check_quality_observations(report):
    quality = report.get('quality_observations', {})
    if not isinstance(quality, dict) or any(not isinstance(quality.get(key), str) or not quality[key].strip()
                                            for key in ('hair', 'face')):
        raise ValueError('完整复核须分别写quality_observations.hair和face的实际观察；不可见说明原因')


def check_self_observations(report, packet):
    artifacts = {a['id']: a for a in packet['evidence']}
    viewed = report.get('viewed_evidence_ids', [])
    if (not isinstance(viewed, list) or any(not isinstance(i, str) for i in viewed)
            or len(viewed) != len(set(viewed)) or not set(viewed) <= set(artifacts)
            or not any(artifacts[i]['kind'] == 'comparison' for i in viewed)):
        raise ValueError('简单自评只需实际看参考—候选拼图并记录有效viewed_evidence_ids；不要求全图、原生或背景检查')


def stage_agents(root):
    from review_v2 import read, SELECTION, WORKFLOW
    result = {}
    # A fresh existing_review has no generation-selection history.
    selections = read(root / SELECTION)['stages'] if (root / SELECTION).is_file() else {}
    history = read(root / WORKFLOW)['history'] if (root / WORKFLOW).is_file() else []
    groups = [(h['stage'], h['selection']) for h in history] + list(selections.items())
    for role, group in groups:
        for review in [*group.get('reviews', {}).values(), *group.get('processing_reviews', {}).values()]:
            reviewer = review.get('reviewer', {})
            if reviewer.get('mode') == 'subagent':
                result.setdefault(role, []).append(reviewer['agent_id'])
    return result


def stage_reviewer(root, role):
    agents = stage_agents(root).get(role, [])
    return agents[-1] if agents else None


def validate_stage_reviewer(root, role, report):
    from review_v2 import require
    reviewer = report.get('reviewer', {})
    if reviewer.get('mode') != 'subagent':
        return
    agents = stage_agents(root)
    identifier = reviewer.get('agent_id')
    require(not any(identifier in ids for other, ids in agents.items() if other != role),
            '每阶段须独立复核代理；该agent_id已用于其他阶段，不得换着复用')
    known = agents.get(role, [])
    require(not known or identifier == known[-1] or bool(report.get('reviewer_replacement_reason')),
            '本阶段复核复用本阶段原子代理；确实不可用才记录reviewer_replacement_reason')


def evidence_sources(artifact):
    sources = artifact.get('source_ids', [])
    sources = sources if isinstance(sources, list) else []
    return {s for s in sources if isinstance(s, str)} | (
        {artifact['source_id']} if isinstance(artifact.get('source_id'), str) else set())


def evidence_requirements(packet, extra=()):
    """Mechanical citation guidance only; never mark evidence viewed or checks passed."""
    artifacts = packet['evidence'] + [a for a in extra if isinstance(a, dict) and isinstance(a.get('id'), str)]
    requirements = {}
    for goal in packet['checks']:
        kinds = primary_kinds(goal['group'])
        primary = [a['id'] for a in artifacts if a.get('kind') in kinds
                   and (goal['group'] != 'background' or a['id'] not in ('light', 'dark'))]
        sources = (goal['source_ids'] if goal['group'] in ('identity', 'design', 'spatial')
                   and goal.get('reference_display') != 'context_only' else [])
        requirements[goal['id']] = {
            'primary_kinds': sorted(kinds), 'primary_evidence_ids': primary,
            'required_reference_ids': list(sources),
            'reference_evidence_ids': {source: [a['id'] for a in artifacts if source in evidence_sources(a)]
                                       for source in sources},
            'instruction': '从实际看过的证据中引用至少一项主要证据；pass须覆盖全部适用参考。'
                           '原生局部按相关部位选择，不必遍历；这些编号仅为可用选项，不代表已看或通过。'}
    return requirements


def review_coverage_errors(packet, report):
    """Collect every pass-coverage defect, rather than stopping at the first goal."""
    errors = []
    extra, viewed, checks = (report.get('extra_evidence', []), report.get('viewed_evidence_ids', []),
                             report.get('checks', []))
    if not isinstance(extra, list) or any(not isinstance(a, dict) or not isinstance(a.get('id'), str) for a in extra):
        errors.append({'field': 'extra_evidence', 'message': 'extra_evidence须为带id的对象列表'})
        extra = []
    if not isinstance(viewed, list) or any(not isinstance(i, str) for i in viewed):
        errors.append({'field': 'viewed_evidence_ids', 'message': 'viewed_evidence_ids须为证据编号列表'})
        viewed = []
    if not isinstance(checks, list):
        return errors + [{'field': 'checks', 'message': 'checks须为检查项列表'}]
    artifacts = {a['id']: a for a in packet['evidence']}
    artifacts.update({a['id']: a for a in extra})
    viewed, goals = set(viewed), {g['id']: g for g in packet['checks']}
    hints = evidence_requirements(packet, extra)
    for index, item in enumerate(checks):
        field = f'checks[{index}].evidence_ids'
        if not isinstance(item, dict):
            errors.append({'field': f'checks[{index}]', 'message': 'checks每项须为对象'})
            continue
        key = item.get('id')
        if item.get('result') != 'pass' or not isinstance(key, str) or key not in goals:
            continue
        goal, ids = goals[key], item.get('evidence_ids', [])
        def error(message, **details):
            errors.append({'field': field, 'check_id': key, 'message': message + '：' + key,
                           'evidence_requirement': hints[key], **details})
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids):
            error('通过项须引用已实际看过的证据')
            continue
        if not set(ids) <= viewed or not set(ids) <= set(artifacts):
            error('通过项须引用已实际看过的证据',
                  unviewed_evidence_ids=sorted(set(ids) - viewed),
                  unknown_evidence_ids=sorted(set(ids) - set(artifacts)))
        valid = [artifacts[i] for i in ids if i in artifacts]
        if not any(a.get('kind') in primary_kinds(goal['group']) for a in valid):
            error('缺少主要证据')
        observed = set().union(*(evidence_sources(a) for a in valid))
        missing = set(hints[key]['required_reference_ids']) - observed
        if missing:
            error('通过项未核对全部适用参考', missing_reference_ids=sorted(missing))
    return errors


def check_review_coverage(packet, report):
    """Reject missing coverage in special/low-level callers too; no visual decisions."""
    errors = review_coverage_errors(packet, report)
    if errors:
        raise ValueError('; '.join(e['message'] for e in errors))
