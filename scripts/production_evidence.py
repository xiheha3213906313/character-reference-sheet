"""One supplementary-evidence batch from existing pixels. No model or visual decisions."""
import copy
import argparse
from pathlib import Path
import re

from compose_review import load_panel
from production_records import FLOW, read_flow
from review_assets import comparison
from review_v2 import digest, read, require, fingerprint
import review_workflow as workflow


def front_silhouette(root, source):
    """Geometry-only mask; a separate front original has a material-only role."""
    import mirror_silhouette
    from PIL import Image
    with Image.open(source) as image:
        alpha = image.convert('RGBA').getchannel('A')
        require(alpha.getextrema()[0] <= 4 and alpha.getextrema()[1] > 4,
                '正面没有可用Alpha，无法可靠提取剪影；按背景接口准备并实际核验分割后再继续，不能把近白RGB直接当剪影')
    directory = root / '制作记录/参考辅助/背面剪影' / digest(source)
    record = directory / 'silhouette-record.json'
    if not record.exists():
        mirror_silhouette.prepare(source, directory)
    saved = read(record)
    require(saved['source_sha256'] == digest(source), '剪影上游已变化')
    for name, value in saved['outputs'].items():
        require(digest(directory / (name + '-silhouette.png')) == value['sha256'], '剪影或来源记录已变化')
    return directory / 'back-silhouette.png'


def reference_board(root, panels, flow):
    """Prepare native source crops in one prompt slot; provenance is generated once."""
    from sheet_flow import strict
    from reference_packing import aligned_board
    require(isinstance(panels, list) and 1 <= len(panels) <= 3, '参考辅助图须一至三个同主题原生局部')
    lookup = {s['source_id']: s for s in flow['sources']}
    adopted = {m['source_id'] for m in flow['materials'] if m['decision'] != 'exclude'}
    specs = []
    for panel in panels:
        strict(panel, {'source_id', 'crop'}, {'source_id', 'crop'})
        key = panel['source_id']
        require(key in lookup and key in adopted, '辅助图只能使用已采用原图')
        source = lookup[key]
        path = root / source['file']
        require(digest(path) == source['sha256'], '辅助图原始来源已变化')
        pixels, info = load_panel(path, panel['crop'])
        require(pixels.width <= 4000, '局部过宽，须缩小裁切范围；不自动缩小关键细节')
        specs.append({'image': str(path), 'label': key, 'crop': info['crop']})
    path, saved = aligned_board(root, specs)
    return path


def supplement(root, role, config):
    from sheet_flow import strict, nonempty, save
    strict(config, {'reason', 'regions', 'comparisons'}, {'reason'})
    nonempty(config['reason'], 'reason')
    regions, comparisons = config.get('regions', []), config.get('comparisons', [])
    require(isinstance(regions, list) and isinstance(comparisons, list) and bool(regions or comparisons),
            '补证须提供必要regions或comparisons，不重新生图')
    flow, values = read_flow(root), workflow.load_all(root)
    status = workflow.status(root, role, values)
    require(status['next_action'] == 'supplement_evidence', '仅首次待核实允许一次补证')
    packet_path = root / status['packet_file']
    require(digest(packet_path) == status['packet_sha256'], '原复核任务包已变化')
    packet = read(packet_path)
    stage = flow['stages'][role]
    existing = stage.get('supplement')
    if existing:
        saved = read(root / existing['file'])
        require(saved['config'] == config and saved['packet_sha256'] == status['packet_sha256'],
                '本候选已整理过一次补证，复用原批次，不继续裁图')
        return handoff(root, status, saved, existing)
    directory = root / f'制作记录/对照/补证批次/{role}-{packet["call_id"]}'
    artifacts = {a['id']: a for a in packet['evidence']}
    generated, ids, pending_regions, pending_boards = [], set(), [], []

    def identifier(value):
        require(isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,60}', value)), '补证id无效')
        value = 'supp_' + value
        require(value not in ids and value not in artifacts, '补证id重复')
        ids.add(value)
        return value

    # Validate/crop everything before creating a batch, so input mistakes need no record repair.
    for region in regions:
        strict(region, {'id', 'crop', 'evidence_id'}, {'id', 'crop'})
        key = identifier(region['id'])
        source = artifacts.get(region.get('evidence_id', 'whole'))
        require(source is not None and source['kind'] in ('whole', 'native', 'source', 'upstream'), '局部来源id无效')
        require(digest(root / source['file']) == source['sha256'], '补证来源已变化')
        pixels, info = load_panel(root / source['file'], region['crop'])
        kind = source['kind'] if source['kind'] in ('source', 'upstream') else 'native'
        pending_regions.append((key, pixels, info, kind))
    for spec in comparisons:
        strict(spec, {'id', 'reference_ids', 'reference_crops', 'candidate_crop', 'check_ids'}, {'id', 'reference_ids'})
        key = identifier(spec['id'])
        refs = spec['reference_ids']
        require(isinstance(refs, list) and 1 <= len(refs) <= 2 and all(i in artifacts and
                artifacts[i]['kind'] in ('source', 'upstream') for i in refs), '对照需一至两项原包来源/上游id')
        panels = []
        for item in [*refs, 'whole']:
            source = artifacts[item]
            require(digest(root / source['file']) == source['sha256'], '对照来源已变化')
            crop = spec.get('candidate_crop') if item == 'whole' else spec.get('reference_crops', {}).get(item)
            load_panel(root / source['file'], crop)
            panels.append({'file': root / source['file'], 'label': item, 'crop': crop})
        require(set(spec.get('check_ids', [])) <= {g['id'] for g in packet['checks']}, '未知检查项id')
        pending_boards.append((key, panels, spec.get('check_ids', [])))
    directory.mkdir(parents=True, exist_ok=True)
    for key, pixels, info, kind in pending_regions:
        path = directory / (key + '.png')
        pixels.save(path)
        generated.append({'id': key, 'file': workflow.local(root, path), 'sha256': digest(path),
                          'kind': kind, 'source': info})
    for key, panels, checks in pending_boards:
        board = comparison(directory, key, panels, kind='whole', diagnostic_type='comparison', check_ids=checks)
        board['file'], board['layout_record'] = workflow.local(root, board['file']), workflow.local(root, board['layout_record'])
        generated.append(board)
    template_path = packet_path.with_name(packet_path.stem + '-report-template.json')
    template = read(template_path)
    template.update(supplement_round=1, extra_evidence=generated)
    previous = values[2]['stages'][role]['reviews'][packet['call_id']]
    template['reviewer'] = copy.deepcopy(previous['reviewer'])
    # Fresh pending fields; a cache/rendering operation cannot inherit visual passes or viewed flags.
    template_file = directory / 'report-template.json'
    workflow.write(template_file, template)
    saved = {'schema_version': 2, 'call_id': packet['call_id'], 'packet_sha256': status['packet_sha256'],
             'config': copy.deepcopy(config), 'evidence': generated, 'reviewer': previous['reviewer'],
             'report_template_file': workflow.local(root, template_file)}
    path = directory / 'supplement.json'
    workflow.write(path, saved)
    ref = {'file': workflow.local(root, path), 'sha256': digest(path)}
    stage['supplement'] = ref
    save(root, flow, values)
    return handoff(root, status, saved, ref)


def handoff(root, status, saved, ref):
    require(digest(root / ref['file']) == ref['sha256'], '补证批次记录已变化')
    for evidence in saved['evidence']:
        require(digest(root / evidence['file']) == evidence['sha256'], '补证图片已变化')
    result = copy.deepcopy(status)
    result['next_action'] = 'review_supplement'
    result['supplement_file'] = str(root / ref['file'])
    result['reviewer_handoff'].update(report_template_file=saved['report_template_file'],
        report_output_file=str((root / saved['report_template_file']).with_name('reviewer-output.json')),
        previous_reviewer=saved['reviewer'])
    result['reviewer_handoff']['task'] += (
        '只补看supplement_file中的必要证据，交回原复核代理，不重新生成。'
        '重新确认完整当前观察集合，不由脚本继承pass；仍不确定保留pending。'
        '直接写report_output_file，主模型review该文件。')
    return result
