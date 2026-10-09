"""Read provider-neutral local image receipts; never infer call ids or visual passes."""
import json
import re
from pathlib import Path

from PIL import Image

from review_v2 import digest, require


def output_path(root, value, selected=None):
    """Prefer structured local paths, otherwise accept one unambiguous readable image."""
    candidates = []

    def add(value):
        if not isinstance(value, str) or not value.strip():
            return
        value = value.strip().strip('`"\'')
        if value.startswith('file://'):
            from urllib.parse import unquote, urlparse
            parsed = urlparse(value)
            value = unquote(parsed.path)
            if re.match(r'^/[A-Za-z]:', value):
                value = value[1:]
        if re.match(r'^[a-zA-Z]+://', value):
            return  # No downloads or remote-path guesses.
        path = Path(value)
        path = path if path.is_absolute() else root / path
        try:
            if path.is_file():
                with Image.open(path) as image:
                    require(getattr(image, 'n_frames', 1) == 1, '工具返回多帧图片，须明确指定本轮单张输出')
                    image.load()
                resolved = path.resolve()
                if resolved not in candidates:
                    candidates.append(resolved)
        except (OSError, ValueError):
            return

    def visit(value):
        if isinstance(value, dict):
            # These are receipt data fields, never instructions to execute.
            for key in ('savedPath', 'saved_path', 'path', 'file', 'output', 'output_path', 'image_path', 'uri'):
                if key in value:
                    visit(value[key])
            for key in ('content', 'structuredContent', 'result', 'text', 'output_hint', 'images', 'outputs', 'data'):
                if key in value:
                    visit(value[key])
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            if value.startswith('data:'):
                return  # Embedded binary is not a local path or text receipt.
            add(value)
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError):
                decoded = None
            if isinstance(decoded, (dict, list)):
                visit(decoded)
            # Anchored at every local-path start, not one provider's filename/suffix.
            starts = list(re.finditer(r'[A-Za-z]:[\\/]|(?<![\w:/])/(?!/)', value))
            for position, match in enumerate(starts):
                chunk = value[match.start():starts[position + 1].start() if position + 1 < len(starts) else len(value)]
                # Quotes/newlines and punctuation bound ordinary prose paths; spaces in names survive.
                chunk = re.split(r'[\r\n`"<>]', chunk, maxsplit=1)[0]
                for end in reversed([m.start() for m in re.finditer(r'\s|[,;。]', chunk)] + [len(chunk)]):
                    add(chunk[:end])

    visit(value)
    if selected:
        path = output_path(root, selected)
        require(path in candidates, '显式output必须是原始result列出的本地输出之一，不能换成别的图片')
        return path
    require(len(candidates) == 1,
            'output/result须包含唯一可读取的本地单张图片；多个输出请明确output路径，不能猜选或从文件名推断调用id')
    return candidates[0]


def normalize(root, call):
    """Accept explicit output/error or a raw successful tool result, preserving the original."""
    require(isinstance(call, dict), 'calls每项须为对象')
    require(bool(call.get('error')) != bool(call.get('output') or call.get('result')),
            '每次调用须为成功result/output或失败error；error不能与成功回执混填')
    identifier = call.get('tool_call_id')
    embedded = call.get('result', {}).get('tool_call_id') if isinstance(call.get('result'), dict) else None
    require(identifier is None or isinstance(identifier, str) and identifier.strip(),
            'tool_call_id为当前工具提供的非空真实编号或null；不能填空字符串、猜编号或读取聊天寻找')
    require(embedded is None or isinstance(embedded, str) and embedded.strip(), '回执tool_call_id格式无效')
    require(not identifier or not embedded or identifier == embedded, '显式tool_call_id与原始回执编号冲突')
    identifier = identifier or embedded
    require(identifier or call.get('result') or call.get('error'),
            '缺少真实tool_call_id且只有output路径：将工具原始回执填result，编号未提供时保留null；不读取聊天寻找')
    result = {'request_id': call['request_id'], 'tool_call_id': identifier,
              'tool_call_id_status': 'recorded' if identifier else 'unavailable_in_current_receipt',
              'raw_receipt': next(call[k] for k in ('result', 'output', 'error') if call.get(k))}
    failure = call.get('error')
    if isinstance(call.get('result'), dict) and call['result'].get('isError') is True:
        require(not call.get('output'), '工具原始回执标为失败，不能另填output将它变为成功')
        failure = call['result']
    if failure:
        require(isinstance(failure, (str, dict)), 'error须为真实错误文本或对象')
        result['error'] = failure
    else:
        path = output_path(root, call['result'], call.get('output')) if call.get('result') else output_path(root, call['output'])
        with Image.open(path) as image:
            result.update(output=str(path), original_output={
                'file': str(path), 'sha256': digest(path), 'format': image.format,
                'dimensions': list(image.size), 'conversion': 'none' if image.format == 'PNG' else 'decoded_pixels_to_png'})
    return result


def archive_output(root, call):
    """Keep original bytes; a PNG working copy preserves decoded pixels, with no resampling."""
    if not call.get('output'):
        return None
    import shutil
    source = Path(call['output'])
    directory = root / '制作记录/工具原始输出'
    directory.mkdir(parents=True, exist_ok=True)
    original = directory / (call['original_output']['sha256'] + source.suffix)
    if not original.exists():
        shutil.copyfile(source, original)
    require(digest(original) == call['original_output']['sha256'], '原始工具输出归档已变化')
    call['original_output']['archive_file'] = original.relative_to(root).as_posix()
    if call['original_output']['format'] == 'PNG':
        return str(original)
    working = directory / (call['original_output']['sha256'] + '-decoded.png')
    with Image.open(original) as image:
        rgba = image.convert('RGBA')
    if working.exists():
        with Image.open(working) as image:
            require(image.convert('RGBA').tobytes() == rgba.tobytes() and image.size == rgba.size, 'PNG工作副本已变化')
    else:
        rgba.save(working)
    return str(working)
