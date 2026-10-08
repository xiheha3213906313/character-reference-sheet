"""Fixed production-record projection and Markdown templates. No visual decisions."""
from pathlib import Path

from review_v2 import BRIEF, STATE, SELECTION, WORKFLOW, read, digest, require
from review_workflow import write, local, load_all, recipe_binding

FLOW = '制作记录/生产接口.json'
RECORD = '制作记录/制作记录.json'
ROLES = ('head', 'front', 'back', 'left')


def read_flow(root):
    flow = read(root / FLOW)
    require(flow.get('schema_version') == 3, '本接口仅处理当前制作记录格式3；旧记录保留，不转换或补造分析。新制作请start到新目录')
    return flow


def report_ref(root, value):
    if not value:
        return None
    path = root / value['report_file']
    return {'file': value['report_file'], 'sha256': value['report_sha256'], 'data': read(path)}


def export(root, flow=None, values=None):
    """Write one machine-readable projection; prose is always derived from it."""
    flow = flow or read_flow(root)
    require(flow.get('schema_version') == 3, '不能把旧制作记录自动导出为当前格式')
    brief, state, selections, workflow = values or load_all(root)
    stages = []
    recipes = {}
    for role in ROLES:
        choice = selections['stages'].get(role)
        if not choice:
            continue
        groups = [h['selection'] for h in workflow['history'] if h['stage'] == role] + [choice]
        calls = []
        for group in groups:
            recipes[recipe_binding(group['recipe'])] = group['recipe']
            for call in group['attempts']:
                review = group['reviews'].get(call['id'])
                calls.append({'call_id': call['id'], 'phase': call['phase'], 'event': call['event'],
                              'recipe_sha256': call.get('recipe_sha256'),
                              'receipt': {'file': call['evidence_file'], 'sha256': call['evidence_sha256']},
                              'review_packet': {'file': call['packet_file'], 'sha256': call['packet_sha256']}
                                               if call.get('packet_file') else None,
                              'output': call.get('output'), 'error': call.get('error'),
                              'self_check': report_ref(root, call.get('self_check')),
                              'visual_review': report_ref(root, review),
                              'review_decision': review.get('decision') if review else None})
        stages.append({'stage': role, 'decision': state['stages'][role]['decision'],
                       'target_sha256': state['stages'][role]['target_sha256'],
                       'dependencies': state['stages'][role]['dependencies'],
                       'targets': brief['checks'][role], 'recipe': choice['recipe'],
                       'target_sources': {key: brief['sources'][key] for key in sorted(
                           {s for g in brief['checks'][role] for s in g['source_ids']})},
                       'prompt_metadata': flow.get('stages', {}).get(role, {}).get('prompt_metadata'),
                       'reference_coverage': flow.get('stages', {}).get(role, {}).get('reference_coverage', []),
                       **({'reference_packing': flow['stages'][role]['reference_packing']}
                          if 'reference_packing' in flow.get('stages', {}).get(role, {}) else {}),
                       'generation_limit': choice['generation_limit'],
                       'baseline_call_id': choice.get('baseline_call'),
                       'selected_call_id': choice.get('selected_call'),
                       'reviewer_policy': choice['reviewer_policy'], 'calls': sorted(calls, key=lambda c: c['event']),
                       'ranking': choice.get('ranking'), 'limit_selection': choice.get('limit_selection'),
                       'ranking_evidence': choice.get('ranking_evidence'),
                       'supplement': flow.get('stages', {}).get(role, {}).get('supplement'),
                       'processing': choice.get('processing', []),
                       'processing_reviews': choice.get('processing_reviews', {})})
    record = {'schema_version': 3, 'record_type': 'character_reference_production', 'rules_version': 3,
              'created_at': flow['created_at'], 'character': flow.get('character'),
              'source_directory': flow['source_directory'], 'sources': flow['sources'],
              'material_analysis': flow.get('materials', []), 'stages': stages,
              'recipes': recipes, 'requests': list(flow.get('requests', {}).values()), 'delivery': flow.get('delivery'),
              'record_semantics': {'visual_results_are_model_observations': True,
                                   'script_performs_visual_review': False}}
    from record_contract import validate
    validate(record)
    write(root / RECORD, record)
    templates = Path(__file__).resolve().parents[1] / 'assets/record-templates'
    rows = '\n'.join('| ' + ' | '.join(cell(x) for x in (
        m['source_id'], ', '.join(m['views']), m['observation'], m['quality'], m['decision'], m['selection_reason'] +
        ('；清晰替代：' + m['covered_by']['source_id'] + '，' + m['covered_by']['reason'] if m.get('covered_by') else ''),
        '; '.join(u['id'] + ' [' + u['kind'] + ':' + ','.join(u['stages']) + '] ' + u['target'] +
                  (' crop=' + str(u['crop']) if 'crop' in u else '') for u in m['uses']))) + ' |'
        for m in record['material_analysis'])
    (root / '制作记录/图片选用文档.md').write_text(
        (templates / 'material-selection.md').read_text(encoding='utf-8').replace('{{rows}}', rows), encoding='utf-8')
    if record['character']:
        c = record['character']
        text = (templates / 'character.md').read_text(encoding='utf-8')
        for key, value in c.items():
            text = text.replace('{{' + key + '}}', str(value))
        (root / '角色描述.md').write_text(text, encoding='utf-8')
    rows = '\n'.join('| ' + ' | '.join(cell(x) for x in (
        s['stage'], s['decision'], s['baseline_call_id'], s['selected_call_id'], len(s['calls']))) + ' |'
        for s in stages)
    (root / '制作记录/制作记录.md').write_text(
        (templates / 'production.md').read_text(encoding='utf-8').replace('{{rows}}', rows), encoding='utf-8')
    return record


def cell(value):
    return ('—' if value is None else str(value)).replace('|', '\\|').replace('\r', '').replace('\n', '<br>')
