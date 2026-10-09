"""Bind maker-observed front occlusion to the selected image and original evidence."""
import copy
from pathlib import Path

from production_records import read_flow, ROLES
from review_v2 import read, digest, fingerprint, require
import review_workflow as workflow


def inventory(flow):
    items = [{'id': 'hair_flow', 'name': '头发、发辫走向与前后遮挡'},
             {'id': 'clothing_layers', 'name': '服装穿戴、层次与可见特征'}]
    items += [{'id': 'accessory_' + a['id'], 'name': a['name']} for a in flow['accessory_visibility']]
    items += [{'id': 'feature_' + u['id'], 'name': u['target']} for m in flow['materials']
              if m['decision'] != 'exclude' for u in m['uses'] if u['kind'] == 'design']
    return items


def binding(flow, values):
    from sheet_flow import plan_signature
    front = values[1]['stages'].get('front', {})
    require(front.get('decision') in ('approved', 'selected_unreviewed') and front.get('sha256'), '先完成正面选优')
    require(not front.get('plan_requires_prepare'), '正面目标已失效，先重新prepare正面')
    return fingerprint({'front_sha256': front['sha256'], 'front_dependencies': front['dependencies'],
                        'plans': {r: plan_signature(flow, r) for r in ('back', 'left')},
                        'inventory': inventory(flow)})


def valid(root, flow, values):
    ref = flow.get('front_structure')
    if not ref:
        return False
    path = root / ref['file']
    if not path.is_file() or digest(path) != ref['sha256']:
        return False
    front = values[1]['stages']['front']
    if not (root / front['image']).is_file() or digest(root / front['image']) != front['sha256']:
        return False
    saved = read(path)
    return (saved['binding_sha256'] == binding(flow, values) and
            all((root / r['file']).is_file() and digest(root / r['file']) == r['sha256'] for r in saved['sources'].values()))


def action(root, flow, values, mutate=True):
    from sheet_flow import submit_command
    token = binding(flow, values)
    path = root / f'制作记录/阶段输入/front-{token[:16]}-structure.json'
    if mutate and not path.exists():
        items = []
        for item in inventory(flow):
            items.append({**item, 'front_observation': '', 'attachment': '', 'path': '', 'occluded_by': '',
                          'source_ids': ['front_selected'], 'stages': {r: {'visibility': 'unclear',
                          'visible_portion': '', 'hidden_portion': '', 'reason': '', 'source_ids': []} for r in ('back', 'left')}})
        workflow.write(path, {'binding_sha256': token, 'items': items})
    front = values[1]['stages']['front']
    return {'next_action': 'analyze_front_structure', 'stage': 'front', 'config_file': str(path),
            'submit_command': submit_command('record-structure', root, 'front', path),
            'selected_front': {'file': str(root / front['image']), 'sha256': front['sha256'], 'decision': front['decision']},
            'rear_sources': [dict(s, file=str(root / s['file'])) for s in flow['sources']
                             if any(m['source_id'] == s['source_id'] and m['decision'] != 'exclude' and
                                    set(m['views']) & {'back', 'rear_oblique'} for m in flow['materials'])],
            'instruction': '正面三选一已结束，制作模型自己分析所选正面的头发/发辫、服装特征和每件饰品。'
                           '复用刚看过的排序图及已看清的原图；只为未看清的关系查看必要原图，不重新验收或唤起代理。'
                           '逐项填写固定模板中的附着、走向、遮挡物，以及背面/左侧可见和隐藏部分。'
                           '有后方原图先看它实际可见边界，不能把“同一实体”推成背面完整可见。'
                           '例如辫子跨肩垂在胸前，后脑可见不代表辫端在背后；不得把前侧辫端和束饰移到肩后。'
                           '不确定保留unclear并说明范围；图像关系的模型判断由脚本编译一次，不能重写提示词或另写分析文档。'}


def record(root, config):
    from sheet_flow import strict, nonempty, save
    flow, values = read_flow(root), workflow.load_all(root)
    require(not any(r['state'] == 'awaiting_result' for r in flow['requests'].values()), '先receive已有请求，再提交空间分析')
    require(workflow.status(root, 'front', values)['next_action'] == 'continue', '先完成正面选优')
    strict(config, {'binding_sha256', 'items'}, {'binding_sha256', 'items'})
    require(config['binding_sha256'] == binding(flow, values), '空间分析所选正面/素材版本已变化，使用当前返回模板')
    expected = {i['id']: i for i in inventory(flow)}
    items = config['items']
    require(isinstance(items, list) and all(isinstance(i, dict) for i in items) and
            len(items) == len(expected) and {i.get('id') for i in items} == set(expected), '空间分析须覆盖固定清单全部特征，不得漏项/重复')
    adopted = {m['source_id'] for m in flow['materials'] if m['decision'] != 'exclude'}
    rear = {m['source_id'] for m in flow['materials'] if m['decision'] != 'exclude' and set(m['views']) & {'back', 'rear_oblique'}}
    for item in items:
        strict(item, {'id', 'name', 'front_observation', 'attachment', 'path', 'occluded_by', 'source_ids', 'stages'},
               {'id', 'name', 'front_observation', 'attachment', 'path', 'occluded_by', 'source_ids', 'stages'})
        require(item['name'] == expected[item['id']]['name'], '固定特征名称不得改写')
        for field in ('front_observation', 'attachment', 'path', 'occluded_by'):
            nonempty(item[field], item['id'] + '.' + field)
        ids = item['source_ids']
        require(isinstance(ids, list) and 'front_selected' in ids and len(ids) == len(set(ids)) and
                set(ids) <= adopted | {'front_selected'}, '每项须引用front_selected及实际采用的原图编号')
        require(isinstance(item['stages'], dict) and set(item['stages']) == {'back', 'left'}, '须分别分析back/left')
        for role, view in item['stages'].items():
            strict(view, {'visibility', 'visible_portion', 'hidden_portion', 'reason', 'source_ids'},
                   {'visibility', 'visible_portion', 'hidden_portion', 'reason', 'source_ids'})
            require(view['visibility'] in ('visible', 'not_visible', 'unclear'), '空间可见性须为visible/not_visible/unclear')
            for field in ('visible_portion', 'hidden_portion', 'reason'):
                nonempty(view[field], item['id'] + '.' + role + '.' + field)
            ids = view['source_ids']
            require(isinstance(ids, list) and ids and len(ids) == len(set(ids)) and set(ids) <= adopted | {'front_selected'}, '阶段空间判断须引用实际依据')
            require(role != 'back' or not rear or view['visibility'] == 'unclear' or bool(set(ids) & rear),
                    '存在后方原图时，确定的背面可见性须核对后方依据；不能只从正面猜测：' + item['id'])
    saved = copy.deepcopy(config)
    source_ids = set(i for item in items for i in item['source_ids']) | set(i for item in items for v in item['stages'].values() for i in v['source_ids'])
    sources = {s['source_id']: {'file': s['file'], 'sha256': s['sha256']} for s in flow['sources'] if s['source_id'] in source_ids}
    front = values[1]['stages']['front']
    require(digest(root / front['image']) == front['sha256'], '所选正面文件已变化，不能记录空间分析')
    sources['front_selected'] = workflow.snapshot(root, str(root / front['image']), '参考')
    for ref in sources.values():
        require(digest(root / ref['file']) == ref['sha256'], '空间分析依据已变化')
    saved.update(artifact='front_structure_analysis', sources=sources, front_sha256=front['sha256'],
                 semantics='maker_observed_relations; not_visual_approval')
    from record_contract import validate
    validate(saved, read(Path(__file__).parents[1] / 'references/front-structure.schema.json'))
    path = root / ('制作记录/空间分析/' + fingerprint(saved) + '.json')
    workflow.write(path, saved)
    previous = flow.get('front_structure')
    flow['front_structure'] = {'file': workflow.local(root, path), 'sha256': digest(path)}
    if previous != flow['front_structure']:
        for role in ('back', 'left'):
            if role in flow['stages']:
                values[1]['stages'][role].update(decision='unreviewed', plan_requires_prepare=True)
                old = read(root / flow['stages'][role]['input_file'])
                old = {k:v for k,v in old.items() if k not in ('materials','accessory_visibility','character')}
                config_path = root / f'制作记录/阶段输入/{role}-{digest(path)[:16]}-prepare.json'
                workflow.write(config_path, old)
                flow['stages'][role]['reprepare_input_file'] = workflow.local(root, config_path)
    workflow.save_all(root, values)
    save(root, flow, values)
    from sheet_flow import advance
    return advance(root, 'front', flow, values)


def inject(root, role, flow, values, spec, sources):
    require(valid(root, flow, values), '先按正面选优返回动作完成record-structure；旧空间分析不能沿用')
    saved = read(root / flow['front_structure']['file'])
    goals, notes = [], []
    for item in saved['items']:
        view = item['stages'][role]
        text = (item['name'] + '：附着=' + item['attachment'] + '；真实走向=' + item['path'] +
                '；遮挡物=' + item['occluded_by'] + '。本视角可见部分=' + view['visible_portion'] +
                '；隐藏部分=' + view['hidden_portion'] + '；依据=' + view['reason'] + '。')
        if view['visibility'] == 'not_visible':
            text += '本视角上述规定位置不存在' + item['name'] + '，不生成隐藏部分；即使传入的正面或其他参考显示该饰品，也不复制或移到背后。'
        else:
            text += '只呈现有依据的可见部分；以上隐藏部分不在本视角显示，不复制或移到背后。未显示、遮挡或不确定的部分不按同一实体臆造为完整可见。'
        notes.append(text)
        ids = list(dict.fromkeys(view['source_ids']))
        keys = ['structure_' + key for key in ids]
        for key, original in zip(keys, ids):
            ref = saved['sources'][original]
            require(digest(root / ref['file']) == ref['sha256'], '空间分析来源已变化')
            sources[key] = {'file': str(root / ref['file'])}
        accessory = item['id'].startswith('accessory_')
        goals.append({'id': ('design_visibility_' + item['id'].removeprefix('accessory_') if accessory else 'spatial_' + item['id']),
                      'group': 'design' if accessory else 'spatial', 'target': text, 'source_ids': keys,
                      'evaluation_scope': '按本项原图与所选正面核对附着、跨肩走向、真实前后遮挡；分析表和提示词均不是视觉答案。'})
    spec['spatial_notes'] = notes
    # This later observed relation takes precedence over the initial visibility assumption.
    overrides = {item['id'].removeprefix('accessory_'): item for item in saved['items'] if item['id'].startswith('accessory_')}
    for index, item in enumerate(flow['accessory_visibility']):
        if item['id'] in overrides:
            view = overrides[item['id']]['stages'][role]
            # The full relation is already emitted once in spatial_notes.
            spec['visibility_notes'][index] = None
    spec['visibility_notes'] = [note for note in spec['visibility_notes'] if note is not None]
    return goals
