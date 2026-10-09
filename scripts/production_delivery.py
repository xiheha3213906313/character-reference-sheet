"""Prepare and publish a complete existing-image delivery, without generating or judging images."""
import argparse
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

from PIL import Image

import check_delivery
import compose_review
import review_gate
import review_workflow as workflow
from review_v2 import read, digest, fingerprint, require, image_bytes_for_review
from production_records import FLOW, RECORD, ROLES, export, read_flow

FILES = {'head': '01_特写.png', 'front': '02_正面.png', 'left': '03_左侧面.png', 'back': '04_背面.png'}
DEFAULT_LAYOUT = {'edge_overlap_limits': {f'{a}:{b}': 0 for i, a in enumerate(FILES) for b in list(FILES)[i + 1:]}}


def node_runtime():
    bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    found = str(bundled) if bundled.is_file() else shutil.which('node')
    require(bool(found), 'Node runtime unavailable; use the environment-provided Node runtime')
    return found


def copy_records(root, bundle):
    shutil.copytree(root / '制作记录', bundle / '制作记录', dirs_exist_ok=True)
    shutil.copyfile(root / '角色描述.md', bundle / '角色描述.md')


def prepare_bundle(root, flow, values, binding, layout):
    bundle = root / '交付预备' / binding[:20]
    bundle.mkdir(parents=True, exist_ok=True)
    copy_records(root, bundle)
    candidates, current = [], {}
    ordered = []
    for role in ROLES:
        choice = values[2]['stages'][role]
        groups = [h['selection'] for h in values[3]['history'] if h['stage'] == role] + [choice]
        for group in groups:
            for call in group['attempts'] + group.get('processing', []):
                if not call.get('error'):
                    ordered.append((call.get('event', 0), role, call))
        view = 'side' if role == 'left' else role
        selected = values[1]['stages'][role]
        path = bundle / FILES[role]
        path.write_bytes(image_bytes_for_review(selected, root, role))
        current[view] = str(path)
    for event, role, call in sorted(ordered, key=lambda row: row[0]):
        path = root / call['output']['file']
        require(digest(path) == call['output']['sha256'], '候选文件已变化：' + call['id'])
        candidates.append({'path': str(path), 'view': 'side' if role == 'left' else role,
                           'sha256': call['output']['sha256'],
                           'orderEvidence': f'制作记录/制作记录.json#stage={role};call_id={call["id"]};event={event}',
                           'statusLabel': '已批准所选' if values[1]['stages'][role]['decision'] == 'approved'
                           and values[1]['stages'][role]['sha256'] == call['output']['sha256'] else '候选，详见制作记录'})
    character = flow['character']
    overview = check_delivery.overview_name(character['name'], character['outfit_name'])
    package = root / f'制作记录/交付接口/{binding}-package.json'
    workflow.write(package, {'character': overview[:-4], 'output': str(bundle), 'current': current, 'candidates': candidates})
    run = subprocess.run([node_runtime(), str(Path(__file__).with_name('package_canvas.mjs')), str(package)],
                         capture_output=True, encoding='utf-8')
    require(run.returncode == 0, run.stderr)
    manifest = {'character_name': character['name'], 'material_directory': flow['source_directory'],
                'outfits': [{'name': character['outfit_name'], 'folder': '.', 'files': FILES,
                             'review_file': workflow.STATE, 'preview_file': overview, 'layout': layout}]}
    requested_ratios = {}
    for role in ROLES:
        spec = read(root / flow['stages'][role]['input_file'])['prompt']
        requested_ratios[role] = spec.get('user_overrides', {}).get('aspect_ratio', {}).get('value',
                                                   '3:4' if role == 'head' else '9:16')
    require(len({requested_ratios[r] for r in ('front', 'back', 'left')}) == 1,
            '三视图比例不一致，不能按统一全身比例交付')
    manifest.update(head_ratio=requested_ratios['head'], body_ratio=requested_ratios['front'])
    manifest_file = bundle / '制作记录/交付清单.json'
    workflow.write(manifest_file, manifest)
    compose_review.preview(argparse.Namespace(manifest=manifest_file, root=bundle), None)
    # A second compact board exposes the closeup's wearing position against the full-body front.
    from review_assets import comparison
    with Image.open(bundle / FILES['front']) as image:
        width, height = image.size
    board = comparison(bundle / '制作记录/对照', '交付肩部关系', [
        {'file': bundle / FILES['head'], 'label': '特写肩胸'},
        {'file': bundle / FILES['front'], 'label': '正面上身', 'crop': [0, 0, width, max(1, height // 2)]}])
    return bundle, manifest, overview, board


def checked_copy(bundle, destination):
    """Resume only our bound copy, never overwrite unrelated destination content."""
    source_files = {p.relative_to(bundle).as_posix(): digest(p) for p in bundle.rglob('*') if p.is_file()}
    marker = destination.with_name('.' + destination.name + '.sheet-copy.json')
    expected_marker = {'artifact': 'sheet_copy_in_progress', 'source': str(bundle.resolve()),
                       'destination': str(destination.resolve()), 'files': source_files}
    owned = marker.is_file() and read(marker) == expected_marker
    if destination.exists():
        require(destination.is_dir(), '交付目标已存在且不是目录')
        existing = {p.relative_to(destination).as_posix(): digest(p) for p in destination.rglob('*') if p.is_file()}
        if existing == source_files:
            if owned:
                marker.unlink()
            return
        require(owned and set(existing) <= set(source_files) and all(source_files[k] == v for k,v in existing.items()),
                '目标目录已有不同内容且不是本次可续传复制；保留原目录，不覆盖旧交付')
    require(not marker.exists() or owned, '复制续传标记属于另一版本，保留原目标及标记')
    if not owned:
        workflow.write(marker, expected_marker)
    destination.mkdir(parents=True, exist_ok=True)
    import tempfile
    for relative, sha in source_files.items():
        target = destination / relative
        require(not target.is_symlink(), '交付目标内不能通过符号链接写入其他目录')
        if target.is_file():
            require(digest(target) == sha, '续传文件已变化，保留目标：' + relative)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix='.sheet-copy-', suffix='.tmp', dir=target.parent)
        os.close(handle)
        temporary = Path(temporary)
        try:
            shutil.copyfile(bundle / relative, temporary)
            require(digest(temporary) == sha, '复制文件校验失败：' + relative)
            require(not target.exists(), '续传期间目标新增文件，停止覆盖：' + relative)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    copied = {p.relative_to(destination).as_posix(): digest(p) for p in destination.rglob('*') if p.is_file()}
    require(copied == source_files, '最终目录复制校验失败')
    marker.unlink()


def destination_for(flow, overview, binding):
    """Choose a new material subdirectory once, then keep it through retries."""
    if flow.get('delivery_override'):
        return Path(flow['delivery_override']['destination']).resolve()
    base = Path(flow['source_directory']).resolve() / Path(overview).stem
    candidate = base
    number = 0
    while candidate.exists() or candidate.with_name('.' + candidate.name + '.sheet-copy.json').exists():
        number += 1
        suffix = '_' + binding[:8] + (f'_{number}' if number > 1 else '')
        candidate = base.with_name(base.name + suffix)
    return candidate


def deliver(root, config):
    from sheet_flow import strict, nonempty, save
    strict(config, {'inspection', 'layout'})
    flow, values = read_flow(root), workflow.load_all(root)
    require(set(values[1]['stages']) == set(ROLES), '四阶段未完成，不能提前准备交付')
    gate = review_gate.check(values[1], root)
    require(gate['downstream_ready'] and gate['record_integrity_valid'], '阶段记录失效：' + '；'.join(gate['errors']))
    previous = flow.get('delivery')
    # A review submission reuses the preview's exact layout unless explicitly changed.
    layout = config.get('layout', previous['layout'] if previous else DEFAULT_LAYOUT)
    from overview_layout import settings_from_layout
    settings_from_layout(layout)
    binding = fingerprint({'stages': values[1]['stages'], 'character': flow['character'], 'layout': layout})
    if previous and previous['binding_sha256'] == binding:
        bundle = root / previous['bundle_directory']
        manifest = read(bundle / '制作记录/交付清单.json')
        overview = previous['overview']['file']
        board = previous['relationship_board']
    else:
        require(not config.get('inspection'), '交付阶段、角色或显式layout变化，简查仍绑定旧预览；先deliver生成当前预览，使用返回的inspection_config_file，不需移动配置文件')
        export(root, flow, values)
        bundle, manifest, overview, rendered = prepare_bundle(root, flow, values, binding, layout)
        board = {'file': Path(rendered['file']).relative_to(bundle).as_posix(), 'sha256': rendered['sha256']}
        flow['delivery'] = {'state': 'pending_inspection', 'binding_sha256': binding, 'layout': layout,
                            'bundle_directory': workflow.local(root, bundle),
                            'overview': {'file': overview, 'sha256': digest(bundle / overview)},
                            'relationship_board': board, 'inspection': None,
                            'destination': str(destination_for(flow, overview, binding)),
                            'file_integrity': 'not_checked', 'record_integrity': 'not_checked',
                            'visual_assessment': 'pending'}
        save(root, flow, values)
    receipt = flow['delivery']
    require(digest(bundle / receipt['overview']['file']) == receipt['overview']['sha256']
            and digest(bundle / board['file']) == board['sha256'], '待查看的交付预览已变化')
    initial = check_delivery.check(manifest, bundle, files_only=True)
    require(initial['file_checks_passed'], '交付文件检查失败：' + '；'.join(initial['errors']))
    if not config.get('inspection'):
        inspection = {'binding_sha256': binding, 'result': 'pending',
                      'viewed_image_ids': [], 'layout_observation': '', 'relationships_observation': ''}
        config_file = root / f'制作记录/交付接口/{binding}-inspection.json'
        if not config_file.exists():
            workflow.write(config_file, {'layout': layout, 'inspection': inspection})
        arguments = ['deliver', '--root', str(root), '--config', str(config_file)]
        if os.name == 'nt':
            quote = lambda s: "'" + s.replace("'", "''") + "'"
            submit = '& ' + ' '.join(quote(s) for s in [str(Path(__file__).with_name('sheet_flow.ps1')), *arguments])
        else:
            import sys
            submit = shlex.join([sys.executable, str(Path(__file__).with_name('sheet_flow.py')), *arguments])
        return {'next_action': 'inspect_delivery', 'file_integrity': 'pass', 'record_integrity': 'pass',
                'visual_assessment': 'pending',
                'images': [{'id': 'overview', 'file': str(bundle / receipt['overview']['file'])},
                           {'id': 'relationships', 'file': str(bundle / board['file'])}],
                'inspection_template': inspection, 'inspection_config_file': str(config_file), 'submit_command': submit,
                'instruction': '实际看总览和特写—正面肩部拼图，只在inspection_config_file填实际观察、查看项和结论，再deliver --config该文件。排版已保存并自动复用，不重抄绑定值、不移动配置、不重复阶段完整验收。'}
    inspection = config['inspection']
    strict(inspection, {'binding_sha256', 'result', 'viewed_image_ids', 'layout_observation', 'relationships_observation'},
           {'binding_sha256', 'result', 'viewed_image_ids', 'layout_observation', 'relationships_observation'})
    require(inspection['binding_sha256'] == binding, '交付简查绑定的版本不符')
    require(inspection['result'] in ('pass', 'fail', 'pending'), '交付简查须为pass/fail/pending')
    require(set(inspection['viewed_image_ids']) == {'overview', 'relationships'}
            and len(inspection['viewed_image_ids']) == 2, '须实际看总览与肩部关系拼图')
    for key in ('layout_observation', 'relationships_observation'):
        nonempty(inspection[key], key)
    receipt['inspection'] = inspection
    receipt['visual_assessment'] = 'recorded_only' if inspection['result'] == 'pass' else inspection['result']
    if inspection['result'] != 'pass':
        save(root, flow, values)
        return {'next_action': 'repair_delivery' if inspection['result'] == 'fail' else 'supplement_delivery_evidence',
                'visual_assessment': inspection['result'], 'generated_images': False}
    default_destination = Path(flow['source_directory']) / Path(overview).stem
    override = flow.get('delivery_override')
    destination = Path(receipt['destination']).resolve()
    require(destination != root and not destination.is_relative_to(bundle) and not bundle.is_relative_to(destination),
            '交付目标不能覆盖制作根目录或交付预备目录')
    if override:
        manifest['delivery_directory_override_reason'] = nonempty(override['user_quote'], 'start时保存的用户目的地原话')
        workflow.write(bundle / '制作记录/交付清单.json', manifest)
    if receipt['state'] == 'complete' and receipt['destination'] == str(destination):
        checked_copy(bundle, destination)
        final = check_delivery.check(manifest, destination)
        require(final['recorded_delivery_valid'], '已有交付已变化，不能复用完成状态')
        return delivery_result(destination, overview, final)
    receipt.update(state='ready_to_copy', destination=str(destination), file_integrity='pass', record_integrity='pass')
    # Final records include the actual explicit visual check. Mechanical checks never create a pass.
    save(root, flow, values)
    copy_records(root, bundle)
    workflow.write(bundle / '制作记录/交付清单.json', manifest)
    before_copy = check_delivery.check(manifest, bundle, files_only=True)
    require(before_copy['file_checks_passed'], '复制前文件检查失败：' + '；'.join(before_copy['errors']))
    completed_flow = copy.deepcopy(flow)
    completed_flow['delivery']['state'] = 'complete'
    workflow.write(bundle / FLOW, completed_flow)
    export(bundle, completed_flow, values)
    checked_copy(bundle, destination)
    final = check_delivery.check(manifest, destination)
    require(final['recorded_delivery_valid'], '最终交付检查失败：' + '；'.join(final['errors'] + final['review_errors']))
    receipt['state'] = 'complete'
    save(root, flow, values)
    return delivery_result(destination, overview, final)


def delivery_result(destination, overview, final):
    return {'next_action': 'done', 'destination': str(destination), 'preview_file': str(destination / overview),
            'editor_file': str(destination / '编辑画布.html'), 'record_file': str(destination / RECORD),
            'file_integrity': final['file_integrity'], 'record_integrity': final['record_integrity'],
            'visual_assessment': final['visual_assessment'], 'delivery_visual_check': 'recorded_pass',
            'instruction': '交付复制与最终核对已完成；使用返回的目录、总览和网页入口后停止。不要重复deliver、列目录或自行再复制outputs。',
            'candidate_count': len(read(destination / '网页资源/画布清单.json')['candidates'])}
