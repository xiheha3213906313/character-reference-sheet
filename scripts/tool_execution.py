"""Fixed functions.exec image batch wrapper and durable receipt recovery; no visual decisions."""
import base64
import json
import os
from pathlib import Path
import shlex
import sys

from review_v2 import fingerprint, read, require, digest


def paths(root, role, requests):
    key = role + '-' + fingerprint([r['request_id'] for r in requests])[:16]
    directory = root / '制作记录/回执缓存'
    return {name: directory / (key + '-' + name + '.json') for name in ('started', 'returned', 'cached')}


def python_command(code):
    if os.name == 'nt':
        quote = lambda value: "'" + value.replace("'", "''") + "'"
        return '& ' + quote(sys.executable) + ' -B -X utf8 -c ' + quote(code)
    return shlex.quote(sys.executable) + ' -B -X utf8 -c ' + shlex.quote(code)


def encoded_path(path):
    return base64.b64encode(str(path).encode('utf-8')).decode('ascii')


def recovery_action(root, role, requests, submit):
    """Status stays read-only. A started wrapper is never a license to resend tools."""
    files = paths(root, role, requests)
    cached = files['cached'] if files['cached'].is_file() else files['returned']
    if cached.is_file():
        return {'next_action': 'receive', 'stage': role, 'tool_call_count': 0,
                'receipt_config_file': str(cached), 'submit_command': submit('receive', root, role, cached),
                'receipt_recovery': 'saved_tool_return',
                'instruction': '工具回执已保存，直接receive；不再生图。解析/登记错误只修回执字段。'}
    if files['started'].is_file():
        return {'next_action': 'recover_receipts', 'stage': role, 'tool_call_count': 0,
                'execution_marker_file': str(files['started']),
                'instruction': '本批已开始调用但尚未保存整批回执。先从当前执行上下文的store恢复真实返回，'
                               '或等待在途工具；不能重发、猜输出或把缺失回执登记成工具失败。'
                               '执行标记仅记录调用意图，不独立证明工具成功或失败。'}
    return None


def execution_code(root, role, requests, requests_file, capture_command, create=False):
    """The caller runs this returned code verbatim, with actual tools and frozen arguments."""
    if any(r['tool'] != 'image_gen.imagegen' for r in requests):
        return None  # Other providers retain the documented manual capture interface.
    files = paths(root, role, requests)
    read_command = python_command('import hashlib,json,math; from pathlib import Path; '
        'p=Path(' + repr(str(requests_file)) + '); b=p.read_bytes(); '
        'assert hashlib.sha256(b).hexdigest()==' + repr(digest(requests_file)) + '; '
        's=b.decode("utf-8"); i=__REQUEST_PART__; '
        'print(json.dumps({"part":i,"total_parts":math.ceil(len(s)/4000),"text":s[i*4000:(i+1)*4000]},ensure_ascii=False))')
    probe_command = python_command('import json; from pathlib import Path; '
                                  'p=Path(' + repr(str(files['returned'])) + '); '
                                  'print(json.dumps({"returned":p.is_file(),'
                                  '"started":Path(' + repr(str(files['started'])) + ').is_file()}))')
    marker = {'request_ids': [r['request_id'] for r in requests],
              'semantics': 'wrapper_started; not_independent_tool_execution_proof'}
    marker_bytes = base64.b64encode(json.dumps(marker).encode('utf-8')).decode('ascii')
    start_command = python_command('import base64; from pathlib import Path; '
                                  'p=Path(base64.b64decode("' + encoded_path(files['started']) + '").decode("utf-8")); '
                                  'p.parent.mkdir(parents=True,exist_ok=True); '
                                  'p.write_bytes(base64.b64decode("' + marker_bytes + '"))')
    write_prefix = ('import base64; from pathlib import Path; '
                    'p=Path(base64.b64decode("' + encoded_path(files['returned']) + '").decode("utf-8")); '
                    'p.parent.mkdir(parents=True,exist_ok=True); t=p.with_name(p.name+".tmp"); ')
    template = (Path(__file__).parent / 'image_batch_exec.js').read_text(encoding='utf-8')
    parameters = {'key': 'sheet-batch:' + fingerprint([str(root), role, [r['request_id'] for r in requests]]),
                  'request_ids': [r['request_id'] for r in requests],
                  'read_command': read_command, 'probe_command': probe_command, 'start_command': start_command,
                  'write_prefix': write_prefix, 'python': str(sys.executable), 'windows': os.name == 'nt',
                  'capture_command': capture_command, 'cwd': str(root),
                  'receipt_file': str(files['returned'])}
    code = template.replace('__BATCH_CONFIG__', json.dumps(parameters, ensure_ascii=False))
    bundle = requests_file.with_suffix('.exec.json')
    from review_workflow import write
    value = {'request_ids': parameters['request_ids'], 'code': code}
    if bundle.is_file():
        require(read(bundle) == value, '固定执行包已变化，不能覆盖本批执行代码')
    elif create:
        write(bundle, value)
    else:
        return None
    read_bundle = python_command('import hashlib; from pathlib import Path; '
        'p=Path(' + repr(str(bundle)) + '); b=p.read_bytes(); '
        'assert hashlib.sha256(b).hexdigest()==' + repr(digest(bundle)) + '; '
        'print(b.decode("utf-8"))')
    return ('// @exec: {"yield_time_ms": 120000, "max_output_tokens": 2000}\n'
            'const sheetBundleRead = await tools.exec_command({cmd:' + json.dumps(read_bundle, ensure_ascii=False) +
            ',workdir:' + json.dumps(str(root), ensure_ascii=False) + ',max_output_tokens:20000});\n'
            'if (sheetBundleRead.exit_code !== 0) throw new Error(sheetBundleRead.output);\n'
            'const sheetBundle = JSON.parse(sheetBundleRead.output);\n'
            'if (JSON.stringify(sheetBundle.request_ids) !== ' + json.dumps(json.dumps(parameters['request_ids'], separators=(',', ':'))) +
            ') throw new Error("Wrong execution batch");\n'
            'await eval("(async()=>{\\n" + sheetBundle.code + "\\n})()");')


def cache_receipts(root, role, requests, config):
    """Cache before path parsing; an unreadable success remains recoverable, not a failed tool."""
    files = paths(root, role, requests)
    calls = config.get('calls')
    require(isinstance(calls, list) and bool(calls) and all(isinstance(c, dict) for c in calls)
            and len(calls) == len(requests)
            and {c.get('request_id') for c in calls} == {r['request_id'] for r in requests},
            'capture须保存本批全部真实返回，不登记候选、不看图')
    require(all(bool(c.get('result') or c.get('output')) != bool(c.get('error')) for c in calls),
            'capture只保存实际成功回执或真实工具错误；丢失回执不能写error')
    if files['cached'].is_file():
        require(read(files['cached']) == config, '本批已缓存另一份真实回执，不覆盖或换成失败；恢复原回执后receive')
    else:
        from review_workflow import write
        write(files['cached'], config)
    return files['cached']


def check_cached_receipts(root, role, requests, calls):
    files = paths(root, role, requests)
    source = files['cached'] if files['cached'].is_file() else files['returned']
    if not source.is_file():
        return
    original = {c['request_id']: c for c in read(source)['calls']}
    for call in calls:
        saved = original.get(call['request_id'])
        require(saved is not None, '缓存回执缺少当前请求')
        for field in ('result', 'error'):
            require(call.get(field) == saved.get(field), '已有真实工具返回；不能换成丢失/解析失败或另一次工具结果')
        if not saved.get('result') and saved.get('output'):
            require(call.get('output') == saved['output'], '缓存的工具输出不能换成另一张图片')
        if saved.get('tool_call_id'):
            require(call.get('tool_call_id') == saved['tool_call_id'], '缓存中的真实调用id不能改写')
