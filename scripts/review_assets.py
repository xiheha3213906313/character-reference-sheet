"""Comparison rendering and verifiable lossless canvas changes, never visual QA."""
import argparse
import hashlib
import io
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat


def foreground_mask(rgba):
    """Locate diagnostic regions, never certify segmentation or visual quality."""
    alpha = rgba.getchannel('A')
    if alpha.getextrema()[0] < 255:
        return alpha.point(lambda value: 255 if value > 4 else 0), 'alpha_gt_4'
    contrast = ImageChops.difference(rgba.convert('RGB'), Image.new('RGB', rgba.size, 'white'))
    red, green, blue = contrast.split()
    contrast = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    return contrast.point(lambda value: 255 if value > 16 else 0), 'near_white_contrast_locator_only'


def native_regions(rgba, stage):
    """Body-relative native crops; exclude empty canvas instead of blind grid tiles."""
    mask, method = foreground_mask(rgba)
    bounds = mask.getbbox()
    if bounds is None:
        return []
    x, y, right, bottom = bounds
    width, height = right - x, bottom - y
    anchors = ([('head_hair', (0, 0, 1, .60)), ('face', (.25, .22, .75, .85)),
                ('shoulder_clothing', (.05, .58, .95, 1))] if stage == 'head' else
               [('head_hair', (.15, 0, .85, .20)), ('torso_waist', (.10, .17, .90, .40)),
                ('skirt_hip_hands', (0, .35, 1, .65)), ('legs', (.18, .61, .82, .91)),
                ('shoes', (.18, .88, .82, 1))])
    result = []
    seen = set()
    for name, fractions in anchors:
        left, top, r, b = fractions
        box = [x + int(left * width), y + int(top * height),
               min(right, x + max(1, round(r * width))), min(bottom, y + max(1, round(b * height)))]
        occupied = mask.crop(box).getbbox()
        if occupied is None:
            continue
        # Tighten each region in place, retaining a small native context margin.
        padding = max(2, round(min(width, height) * .012))
        tight = [max(box[0], box[0] + occupied[0] - padding), max(box[1], box[1] + occupied[1] - padding),
                 min(box[2], box[0] + occupied[2] + padding), min(box[3], box[1] + occupied[3] + padding)]
        fraction = ImageStat.Stat(mask.crop(tight)).mean[0] / 255
        if fraction < .03 or tuple(tight) in seen:
            continue
        seen.add(tuple(tight))
        result.append({'region': name, 'crop': tight, 'foreground_fraction': round(fraction, 4),
                       'locator': method, 'subject_bounds': list(bounds), 'scale': 1})
    return result


def background_candidates(rgba):
    """Pick at most two real empty-looking patches; visual confirmation stays false."""
    transparent = rgba.getchannel('A').getextrema()[0] == 0
    radius = max(1, min(rgba.size) // 250)
    fractions = [(x, y) for y in (.02, .98, .10, .90, .25, .75, .50) for x in (.02, .98, .10, .90, .25, .75, .50)]
    result = []
    for fx, fy in fractions:
        x, y = round((rgba.width - 1) * fx), round((rgba.height - 1) * fy)
        patch = rgba.crop((max(0, x - radius), max(0, y - radius),
                           min(rgba.width, x + radius + 1), min(rgba.height, y + radius + 1)))
        if transparent:
            suitable = patch.getchannel('A').getextrema()[1] == 0
        else:
            extrema = patch.convert('RGB').getextrema()
            suitable = min(low for low, high in extrema) >= 245 and max(high for low, high in extrema) - min(low for low, high in extrema) <= 8
        if not suitable or any(abs(x - s['xy'][0]) + abs(y - s['xy'][1]) < min(rgba.size) * .25 for s in result):
            continue
        pixel = rgba.getpixel((x, y))
        result.append({'xy': [x, y], 'rgb': list(pixel[:3]), 'alpha': pixel[3], 'confirmed_empty': False})
        if len(result) == 2:
            break
    return result


def mark_background_samples(canvas, samples):
    draw = ImageDraw.Draw(canvas)
    radius = max(4, min(canvas.size) // 100)
    font = ImageFont.load_default(size=max(22, min(canvas.size) // 28))
    for index, sample in enumerate(samples, 1):
        x, y = sample['xy']
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=(220, 70, 40), width=max(1, radius // 3))
        label = str(index)
        bounds = draw.textbbox((0, 0), label, font=font)
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        label_x = x + radius + 3 if x < canvas.width / 2 else x - radius - width - 3
        draw.text((max(0, min(canvas.width - width - 2, label_x)), min(canvas.height - height - 2, y + radius + 2)),
                  label, font=font, fill=(220, 70, 40))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def comparison(directory, identifier, panels, kind='comparison', **metadata):
    """Reuse the maintained composer, with at most three useful panels per board."""
    import compose_review
    directory = Path(directory)
    if kind == 'comparison' and not 2 <= len(panels) <= 3:
        raise ValueError('验收对照只允许一至两张参考加一张候选；排序使用ranking类型')
    specs = []
    for panel in panels:
        path = Path(panel['file']).resolve()
        with Image.open(path) as image:
            w, h = image.size if not panel.get('crop') else (panel['crop'][2] - panel['crop'][0], panel['crop'][3] - panel['crop'][1])
        spec = {'image': str(path), 'label': panel['label'],
                'scale': min(1, 600 / w, 900 / h)}
        if panel.get('crop') is not None:
            spec['crop'] = panel['crop']
        specs.append(spec)
    spec_path = directory / (identifier + '-layout.json')
    spec_path.write_text(json.dumps({'column_width': 600, 'rows': [{'panels': specs}]}, ensure_ascii=False), encoding='utf-8')
    output = directory / (identifier + '.png')
    compose_review.compare(argparse.Namespace(spec=spec_path, output=output), compose_review.find_font(None))
    record = output.with_suffix('.png.json')
    return {'id': identifier, 'file': str(output), 'sha256': sha(output), 'kind': kind,
            'layout_record': str(record), 'layout_sha256': sha(record), **metadata}


def validate_comparison(artifact, root):
    from check_delivery import within
    record = within(root, artifact['layout_record'])
    if sha(record) != artifact['layout_sha256']:
        raise ValueError('拼图旁录已变化')
    layout = json.loads(record.read_text(encoding='utf-8-sig'))
    if layout['output_sha256'] != artifact['sha256']:
        raise ValueError('拼图与排版记录不符')
    # Composer sidecars retain absolute creation paths; the packet binds portable sources.
    panels = [p for row in layout['rows'] for p in row['panels']]
    if [p['sha256'] for p in panels] != [p['sha256'] for p in artifact['panels']]:
        raise ValueError('拼图来源与任务包不符')
    for panel in artifact['panels']:
        if sha(within(root, panel['file'])) != panel['sha256']:
            raise ValueError('拼图原始来源已变化')


def restore_source_cache(packet, brief, root):
    """Restore byte-identical derived PNG caches from unchanged original sources only."""
    from check_delivery import within
    for artifact in packet['evidence']:
        if artifact['kind'] != 'source':
            continue
        original = brief['sources'][artifact['source_id']]
        source = within(root, original['file'])
        if sha(source) != original['sha256'] or artifact.get('source_sha256') != original['sha256']:
            raise ValueError('原始来源已变化，不能修补诊断缓存')
        target = within(root, artifact['file'])
        if not target.is_relative_to(within(root, '制作记录/对照/来源')):
            raise ValueError('来源缓存位置无效')
        if target.is_file() and sha(target) == artifact['sha256']:
            continue
        with Image.open(source) as image:
            buffer = io.BytesIO()
            image.convert('RGBA').save(buffer, format='PNG')
        payload = buffer.getvalue()
        if hashlib.sha256(payload).hexdigest() != artifact['sha256']:
            raise ValueError('无法恢复相同字节的来源缓存；保持验收失效')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def pixel_equivalence(source_bytes, output_bytes, operation, offset=None):
    """Prove preserved RGBA (ignoring hidden RGB at alpha=0), not merely equal bounds."""
    if operation not in ('png_reencode', 'transparent_canvas_pad'):
        return None
    offset = [0, 0] if offset is None else offset
    if not isinstance(offset, list) or len(offset) != 2 or any(type(v) is not int or v < 0 for v in offset):
        raise ValueError('无损画布offset须为两个非负整数')
    with Image.open(io.BytesIO(source_bytes)) as source, Image.open(io.BytesIO(output_bytes)) as output:
        before, after = source.convert('RGBA'), output.convert('RGBA')
    x, y = offset
    if operation == 'png_reencode' and (offset != [0, 0] or before.size != after.size):
        raise ValueError('PNG重编码不能改变画幅或位置')
    if x + before.width > after.width or y + before.height > after.height:
        raise ValueError('无损画布不能裁掉原图')
    expected = Image.new('RGBA', after.size)
    expected.paste(before, (x, y))
    if ImageChops.difference(expected.getchannel('A'), after.getchannel('A')).getbbox():
        raise ValueError('Alpha变化，不能复用视觉批准')
    visible = expected.getchannel('A').point(lambda a: 255 if a else 0)
    for channel in 'RGB':
        difference = ImageChops.difference(expected.getchannel(channel), after.getchannel(channel))
        if ImageChops.multiply(difference, visible).getbbox():
            raise ValueError('可见人物像素变化，不能复用视觉批准')
    return {'operation': operation, 'offset': offset, 'input_size': list(before.size),
            'output_size': list(after.size), 'visible_rgba_preserved': True}
