"""Lossless, bounded review pages. Reading confirmations are reviewer declarations, not tool-log proof."""
import copy
import json
from pathlib import Path

from review_v2 import read, digest, fingerprint, require, within

PAGE_BYTES = 4200


def semantic_document(packet, template):
    """Keep all review semantics; omit repeated blank report rows and mechanical hashes only."""
    fields = ('stage', 'call_id', 'generation_context', 'checks', 'acceptance_standard', 'review_context',
              'background_policy', 'background_sample_candidates', 'reviewer_instructions', 'review_sequence', 'sources')
    document = {key: copy.deepcopy(packet[key]) for key in fields if key in packet}
    # Keep complete ordered purposes, guides and collage regions, never a character-count synopsis.
    document['evidence'] = [{k: v for k, v in e.items() if k not in ('sha256', 'layout_record', 'panels')}
                            for e in packet['evidence']]
    document['report_fields'] = {k: v for k, v in template.items()
                                 if k not in ('checks', 'reading_confirmation', 'extra_evidence')}
    document['evidence_requirements'] = {item['id']: item.get('evidence_requirement', {}) for item in template['checks']}
    if template.get('extra_evidence'):
        document['extra_evidence'] = template['extra_evidence']
    return document


def encoded_document(document):
    """Share exact repeated text, with collision escaping; never summarize a value."""
    counts = {}
    def count(value):
        if isinstance(value, dict):
            for child in value.values():
                count(child)
        elif isinstance(value, list):
            for child in value:
                count(child)
        elif isinstance(value, str) and len(value) > 40:
            counts[value] = counts.get(value, 0) + 1
    count(document)
    keys = {value: f't{index:04d}' for index, value in enumerate(
        (v for v, n in counts.items() if n > 1), 1)}
    def encode(value):
        if isinstance(value, dict):
            result = {k: encode(v) for k, v in value.items()}
            return {'literal_object': result} if set(value) in ({'reuse_text'}, {'literal_object'}) else result
        if isinstance(value, list):
            return [encode(v) for v in value]
        if isinstance(value, str) and value in keys:
            return {'reuse_text': keys[value]}
        return value
    return {'shared_text': {key: value for value, key in keys.items()}, 'document': encode(document)}


def entries(document):
    """Consecutive bounded JSON text fragments avoid thousands of repeated field paths."""
    source = json.dumps(encoded_document(document), ensure_ascii=False, separators=(',', ':'))
    fragments, current, size = [], [], 0
    # Account for JSON string escaping in the CLI envelope, not just source bytes.
    for char in source:
        length = len(json.dumps(char, ensure_ascii=False).encode('utf-8')) - 2
        if current and size + length > PAGE_BYTES - 180:
            fragments.append(''.join(current))
            current, size = [], 0
        current.append(char)
        size += length
    if current:
        fragments.append(''.join(current))
    return [{'text_part': i, 'text': part} for i, part in enumerate(fragments, 1)]


def manifest_path(template_path):
    return template_path.with_suffix('.reading.json')


def build(root, packet_path, template_path):
    from review_workflow import write, local
    packet, template = read(packet_path), read(template_path)
    document = semantic_document(packet, template)
    token = fingerprint(document)
    directory = template_path.parent / ('阅读页-' + token[:20])
    pages, page = [], []
    for entry in entries(document):
        trial = [*page, entry]
        if page and len(json.dumps(trial, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > PAGE_BYTES:
            pages.append(page)
            page = []
        page.append(entry)
    if page:
        pages.append(page)
    references = []
    for index, content in enumerate(pages, 1):
        path = directory / f'{index:03d}.json'
        value = {'id': f'page_{index:03d}', 'content': content}
        if path.is_file():
            require(read(path) == value, '完整复核阅读页已变化')
        else:
            write(path, value)
        references.append({'id': value['id'], 'file': local(root, path), 'sha256': digest(path)})
    manifest = {'packet_file': local(root, packet_path), 'packet_sha256': digest(packet_path),
                'template_file': local(root, template_path), 'document_sha256': token, 'pages': references,
                'semantics': '按text_part顺序连接完整JSON；reuse_text引用shared_text的同一完整文本，literal_object包裹字面对象。声明已读不独立证明模型理解或工具调用。'}
    path = manifest_path(template_path)
    if not path.is_file() or read(path) != manifest:
        write(path, manifest)
    template['reading_confirmation'] = {'manifest_sha256': digest(path), 'completed_page_ids': []}
    write(template_path, template)
    return path


def command(root, path, page=1):
    from sheet_flow import invocation
    return invocation(['read-review', '--root', str(root), '--manifest', str(path), '--page', str(page)])


def handoff(root, packet_path, template_path):
    path = manifest_path(template_path)
    manifest = read(path)
    require(digest(packet_path) == manifest['packet_sha256'], '复核阅读入口绑定已变化')
    return {'reading_manifest_file': str(path), 'reading_manifest_sha256': digest(path),
            'reading_page_count': len(manifest['pages']), 'read_review_command': command(root, path),
            'reading_instruction': '直接执行read_review_command，按next_command读取完整页。不要输出原任务包/模板或自行摘要；'
                                   '按text_part顺序读取连续JSON；reuse_text引用shared_text的同一完整文本，literal_object是字面对象。'
                                   '输出截断时只重读该页，保留原代理及看图记录，不因此新建代理。'
                                   '读完全部页面后才看图与填写报告，reading_confirmation记录本人实际读完的全部page_id；'
                                   '模板是可编辑数据，无需全文打印。只按交接的准确路径编辑，不能搜目录猜模板。'}


def page(root, path, number):
    path = Path(path)
    relative = path.resolve().relative_to(root.resolve()).as_posix() if path.is_absolute() else str(path)
    path = within(root, relative)
    manifest = read(path)
    require(digest(within(root, manifest['packet_file'])) == manifest['packet_sha256'], '任务包已变化，恢复当前交接')
    require(type(number) is int and 1 <= number <= len(manifest['pages']), 'page须为有效页码')
    reference = manifest['pages'][number - 1]
    file = within(root, reference['file'])
    require(digest(file) == reference['sha256'], '复核阅读页已变化')
    return {'next_action': 'read_review_page' if number < len(manifest['pages']) else 'review_reading_complete',
            'page_id': reference['id'], 'page': number, 'page_count': len(manifest['pages']),
            'manifest_sha256': digest(path), **read(file),
            'next_command': command(root, path, number + 1) if number < len(manifest['pages']) else None,
            'required_page_ids': [p['id'] for p in manifest['pages']] if number == len(manifest['pages']) else None}


def expected_manifest(root, call, report):
    if report.get('supplement_round') == 1:
        from production_records import read_flow
        flow = read_flow(root)
        ref = flow['stages'][call['role']].get('supplement')
        require(ref is not None, '缺少补证阅读入口')
        saved = read(root / ref['file'])
        return manifest_path(root / saved['report_template_file'])
    packet_path = root / call['packet_file']
    return manifest_path(packet_path.with_name(packet_path.stem + '-report-template.json'))


def confirmation_errors(root, role, call, report):
    from production_records import FLOW
    if not (root / FLOW).is_file():
        return []  # Low-level/special review keeps its own interface.
    try:
        path = expected_manifest(root, dict(call, role=role), report)
        manifest = read(path)
        require(manifest['packet_sha256'] == report.get('packet_sha256'), '阅读页任务包与报告不一致')
        confirmation = report.get('reading_confirmation', {})
        require(isinstance(confirmation, dict) and set(confirmation) == {'manifest_sha256', 'completed_page_ids'},
                '沿用reading_confirmation两字段，不能省略完整阅读声明')
        require(confirmation['manifest_sha256'] == digest(path), '阅读声明须绑定本次完整阅读入口')
        ids = confirmation['completed_page_ids']
        required = [p['id'] for p in manifest['pages']]
        require(isinstance(ids, list) and all(isinstance(i, str) for i in ids) and len(ids) == len(set(ids))
                and set(ids) == set(required), '尚未声明完整阅读全部提示词、参考用途及检查目标；补读缺页，不改视觉结论或生图')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [{'field': 'reading_confirmation', 'message': str(exc)}]
    return []
