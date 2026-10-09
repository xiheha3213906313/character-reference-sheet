"""Validate observed accessories and final view decisions; compile once-written semantics."""
import re

from review_v2 import require

STAGES = ('head', 'front', 'back', 'left')
LABELS = {'head': '特写', 'front': '正面', 'back': '背面', 'left': '左侧面'}
VISIBILITY = {'visible', 'not_visible', 'unclear', 'out_of_frame'}


def observations(items, label):
    from sheet_flow import strict, nonempty
    require(isinstance(items, list), label + '.accessories须为饰品观察列表，无饰品填[]')
    seen = set()
    for item in items:
        strict(item, {'id', 'name', 'carrier', 'location', 'visibility', 'observation', 'other_views'},
               {'id', 'name', 'carrier', 'location', 'visibility', 'observation'})
        require(isinstance(item['id'], str) and re.fullmatch(r'[a-z][a-z0-9_]*', item['id']) and item['id'] not in seen,
                label + '.accessories.id须为同图不重复的英文编号，同一实体跨图沿用同一id')
        seen.add(item['id'])
        for field in ('name', 'carrier', 'location', 'observation'):
            nonempty(item[field], label + '.accessories.' + field)
        require(item['visibility'] in VISIBILITY, label + '.accessories.visibility无效')
        predictions = item.get('other_views', {})
        require(isinstance(predictions, dict) and set(predictions) <= set(STAGES) and
                all(v in VISIBILITY | {'possibly_hidden'} for v in predictions.values()),
                label + '.accessories.other_views须为阶段可见性初判，可能遮挡用possibly_hidden')


def validate(plan, materials):
    from sheet_flow import strict, nonempty
    require(isinstance(plan, list), 'accessory_visibility须为全部逐图分析完成后的汇总列表，无饰品填[]')
    inventory = {item['id'] for m in materials for item in m['accessories']}
    require(all(isinstance(item, dict) for item in plan) and len(plan) == len(inventory) and
            {item.get('id') for item in plan} == inventory, 'accessory_visibility须覆盖全部已识别饰品且不重复，先逐图观察再综合判断')
    lookup = {m['source_id']: m for m in materials}
    for item in plan:
        strict(item, {'id', 'name', 'carrier', 'location', 'stages'}, {'id', 'name', 'carrier', 'location', 'stages'})
        for field in ('name', 'carrier', 'location'):
            nonempty(item[field], 'accessory_visibility.' + field)
        stages = item['stages']
        require(isinstance(stages, dict) and set(stages) == set(STAGES), '每个饰品汇总须有head/front/back/left四阶段可见性')
        for role, view in stages.items():
            strict(view, {'visibility', 'source_ids', 'reason', 'location'}, {'visibility', 'source_ids', 'reason'})
            if 'location' in view:
                nonempty(view['location'], 'accessory_visibility.stages.' + role + '.location')
            require(view['visibility'] in VISIBILITY, '最终可见性不接受possibly_hidden，仍不确定填unclear')
            ids = view['source_ids']
            require(isinstance(ids, list) and bool(ids) and len(ids) == len(set(ids)) and
                    all(key in lookup and lookup[key]['decision'] != 'exclude' for key in ids),
                    '汇总可见性须引用已采用的实际来源编号')
            require(any(any(a['id'] == item['id'] for a in lookup[key]['accessories']) for key in ids),
                    '汇总来源至少有一张记录了该饰品的实际可见/不可见观察')
            nonempty(view['reason'], 'accessory_visibility.stages.' + role + '.reason')
            rear = [m for m in materials if m['decision'] != 'exclude' and set(m['views']) & {'back', 'rear_oblique'}]
            if role == 'back' and rear and view['visibility'] == 'visible':
                require(any(m['source_id'] in ids and any(a['id'] == item['id'] and a['visibility'] == 'visible'
                        for a in m['accessories']) for m in rear),
                        '背面可见不能由正面同一实体推断：' + item['id'] + '须有后方实际可见观察，否则填unclear或有证据的not_visible')


def statement(item, role):
    view = item['stages'][role]
    position = item['carrier'] + '的' + view.get('location', item['location'])
    name = item['name']
    if view['visibility'] == 'not_visible':
        target = f'本{LABELS[role]}图的{position}不存在{name}，不得在该位置生成该饰品；即使传入的正面或其他参考显示该饰品，也不能复制到本视图。'
    elif view['visibility'] == 'out_of_frame':
        target = f'{position}的{name}在本视图画幅外，不为显示该饰品移动、抬高或复制它。'
    elif view['visibility'] == 'visible':
        target = f'本视图在{position}可见{name}，保留对应承载物、连接与当前投影，不换面、不换侧。'
    else:
        target = f'{position}的{name}在本视图尚无确定显示依据；按真实承载面与遮挡合理呈现，不凭正面参考新增到另一面。'
    return target + '综合依据：' + view['reason']


def stage_plan(plan, role):
    return {item['id']: {'target': statement(item, role),
                         'source_ids': item['stages'][role]['source_ids']} for item in plan}


def refresh_prepared(root, role, previous, current, values):
    """Invalidate affected prepared targets; do not revive or manufacture visual reviews."""
    affected = [stage for stage in previous['stages']
                if stage_plan(previous['accessory_visibility'], stage) != stage_plan(current['accessory_visibility'], stage)]
    earlier = [stage for stage in affected if STAGES.index(stage) < STAGES.index(role)]
    require(not earlier, '饰品汇总改变了上游目标，先prepare --stage ' + (earlier[0] if earlier else role))
    brief = values[0]
    for stage in affected:
        if stage == role:
            continue  # Its complete targets and recipe are rebuilt by this prepare.
        sources = {}
        goals = inject(root, stage, {}, sources, current)
        mapping = previous['stages'][stage]['review_reference_map']
        for goal in goals:
            goal['source_ids'] = list(dict.fromkeys(leaf for key in goal['source_ids'] for leaf in mapping.get(key, [key])))
        import review_workflow as workflow
        for key, source in sources.items():
            brief['sources'][key] = workflow.snapshot(root, source['file'], '参考')
        brief['checks'][stage] = [g for g in brief['checks'][stage] if not g['id'].startswith('design_visibility_')] + goals


def inject(root, role, spec, sources, flow):
    """Final visibility is model judgement; script only copies it into prompt and targets."""
    notes, goals = [], []
    lookup = {s['source_id']: s for s in flow['sources']}
    for item in flow['accessory_visibility']:
        view = item['stages'][role]
        target = statement(item, role)
        notes.append(target)
        for key in view['source_ids']:
            source = lookup[key]
            sources[key] = {'file': str(root / source['file'])}
        goals.append({'id': 'design_visibility_' + item['id'], 'group': 'design', 'target': target,
                      'source_ids': view['source_ids'],
                      'reference_display': 'context_only' if view['visibility'] == 'out_of_frame' else 'compare',
                      'evaluation_scope': '可见性汇总是本次采用方案，不是原图自动证明。核对该饰品实际承载物、可见面与遮挡；不可见/画外只查是否被错误复制，不要求显示隐藏饰品。'})
    spec['visibility_notes'] = notes
    return goals
