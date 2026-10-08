"""Shared defaults for generation targets and visual review; no visual decisions."""

DEFAULT_GENERATION_LIMIT = 6
REVIEWER_TASK = ('独立复核当前候选。读取任务包和报告模板，只接收其中的用户目标、必要原始证据、候选及有效上游；'
                 '不接收制作模型的自评结论或推荐答案。证据已由主流程一次生成，直接查看，不自行裁图、拼图或生成背景。'
                 '子代理执行时reviewer.mode固定subagent，填本人实际agent_id；完整自评按实际模式与原因记录。'
                 '遵循review_sequence：先看按检查项组织的参考—候选拼图，'
                 '再看判断原生质量确实需要的局部和缩小图。背景只看alpha_compare深浅底拼图，不再分别打开light/dark。'
                 '已在拼图看清的全图、参考或局部不重复打开；只有看不清或矛盾才补更合适证据。'
                 '服装须比较其相对颈根、肩、上臂的位置和露肤范围，不能以同色同款代替穿戴位置判断。'
                 '背面设计以原始背面/侧后依据为准，正面剪影只看比例轮廓趋势，不要求完全一致；'
                 '正面原图只用于材料纹理和表面光影，不以其服装裁片、裙层或装饰位置判定背面。'
                 'planned_检查项来自逐图选材计划：原始材料局部不能被上游生成图取代，须实际对比纹理、清晰度和材质表现。'
                 '制作目标是待核验的方案，不能当作原图事实；目标误写或与后方原图冲突时按原图指出差异。'
                 '自然遮挡的后片只查可见边界与连接，无明显矛盾可按合理设计通过，不要求看穿头发。'
                 'alpha_compare的编号对应采样候选，实际确认空白填true，不确定或非空保留false，无需删点或查像素。'
                 '按任务包尺度判断，分别记录构图、头发和面部实际表现；返修优先复查上次问题，合理细节偏差就当如此设计。'
                 '返回实际观察、证据id和pass/fail/pending，不生图、不排序、不做文件/哈希检查。')
FRAMING = {
    'head': '3:4头部特写，头脸为主体、面部大致居中，头顶主体与重要头饰未截断，下缘在胸部及以上。零散飞丝触边或出画、长发穿出左右画边允许，不要求锁骨裁切或固定留白比例。',
    'front': '9:16朝前全身，头顶主体、重要头饰、身体及鞋底完整，位置大致合理；不要求固定边距百分比，零散飞丝触边允许。',
    'back': '9:16背向全身，不回头；头顶主体、重要头饰、身体及鞋底完整，位置大致合理，不要求精确180度或固定边距百分比。',
    'left': '9:16朝画面左的近侧面全身，允许自然小幅转角；明显约45度三分之四视角、朝向错误或严重头身扭转才失败，不测精确90度。主体完整、位置大致合理，不要求固定边距百分比。',
}
QUALITY = (
    '按实际像素占比及原画风分别检查头发与面部。头发的发型、发量大致正确，主要发束边界和层次清楚；'
    '发型/走向成立不代表清晰度通过；重要区域明显模糊、涂抹或糊成片即失败，不要求问题达到大面积。'
    '面部无无依据的灰脏、色块、涂抹或融化；'
    '区分合理妆容、阴影和真实标记。原图模糊或成像脏污不作为复制目标，应恢复有依据的清晰结构；'
    '不以锐化、磨皮或新增纹理代替恢复。用户明确指出的模糊/脏污按本次已确认修复目标处理，'
    '不能自行解释为风格、妆容或阴影而撤销。不可见的面部或头发说明实际原因。'
)
ACCEPTANCE_STANDARD = (
    '整体和细节都实际看，以可接受为准。用户明确要求优先；模型自拟目标不能提高以下默认门槛。'
    + ' '.join(FRAMING.values()) + QUALITY +
    '少量飞丝、交叉、彩边或局部笔触瑕疵允许，不逐根核发丝。对齐大致合理即可。'
    '特写服装对照原图颈根、肩线、上臂位置和露肤范围；本应画外可以不出现。'
    '松垂上臂的外套被明显上提到肩颈、露肩变成正常穿肩等属于穿戴关系改变；合理小偏移允许。'
    '侧面服装露面结合实际转角和布料形状判断，不因可见少量前面图案拒绝。'
    '背面优先按原始背面/侧后图检查，前后可见的裙层、后摆、裁片和装饰不必相同。'
    '合理的头发、饰带遮挡不是待核实理由；只核验可见部分的穿戴、边界和连接，'
    '未出现矛盾时说明遮挡范围即可通过该项，不声称已经证实隐藏接缝。关键可见关系确有冲突或看不清才补证。'
    '第二次及后续仍看上次细节，饰品位置、长度等就当这样设计，穿戴、连接和整体关系合理即通过；'
    '不追究合理微差，不放过明显画质错误。'
)


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
