"""Fit reference capacity with explicit substitutions and aligned, bounded collages."""
import copy
import itertools
import math
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from compose_review import load_panel, find_font
from review_v2 import digest, fingerprint, read, require
import review_workflow as workflow


def substitute(spec, materials):
    """Only the maker's recorded full-coverage judgement can replace a blurry source."""
    result, changes = copy.deepcopy(spec), []
    require(isinstance(result, dict), 'prompt须为对象')
    require(isinstance(result.get('references', []), list) and all(isinstance(r, dict) for r in result.get('references', [])),
            'prompt.references须为对象列表')
    replacements = {m['source_id']: m['covered_by'] for m in materials if m.get('covered_by')}
    for ref in result.get('references', []):
        if ref.get('source_id') in replacements:
            old = ref['source_id']
            ref['source_id'] = replacements[old]['source_id']
            changes.append({'removed_source': old, **replacements[old]})
        for panel in ref.get('panels', []):
            require(isinstance(panel, dict) and isinstance(panel.get('source_id'), str), 'prompt.references[].panels每项须含source_id和crop')
            require(panel['source_id'] not in replacements,
                    '被替代原图的裁切坐标不能套到清晰图；删除该重复局部，或按清晰原图的真实区域更新crop')
    return result, changes


def aligned_board(root, panels):
    """Align rows by proportional sizing; bound W/H to 9/16..4/3, without forcing square."""
    directory = root / '制作记录/参考辅助'
    signature = {'layout': 'aligned-bounded-1', 'panels': []}
    images = []
    for panel in panels:
        path = Path(panel['image'])
        pixels, info = load_panel(path, panel.get('crop'))
        images.append(pixels)
        signature['panels'].append({**panel, 'image': str(path), 'sha256': digest(path), 'crop': info['crop']})
    require(images, '拼图至少包含一个真实区域')
    key = fingerprint(signature)
    path = directory / (key + '.png')
    record = path.with_suffix('.png.json')
    if path.exists():
        saved = read(record)
        require(saved['output_sha256'] == digest(path), '参考拼图或旁录已变化')
        return path, saved
    margin, gap, title = 12, 12, 30
    count = len(images)
    orders = [tuple(range(count))]
    if count <= 5:
        orders = list(itertools.permutations(range(count)))
    else:
        orders += [tuple(sorted(range(count), key=lambda i: (-images[i].height, -images[i].width))),
                   tuple(sorted(range(count), key=lambda i: (-images[i].width, -images[i].height)))]
    best = None
    for order in orders:
        # Exhaustive row partitions for ordinary packs; bounded shelf choices for larger sets.
        cuts = range(1 << (count - 1)) if count <= 10 else [0, (1 << (count - 1)) - 1,
                                                        sum(1 << i for i in range(1, count - 1, 2))]
        for cut in cuts:
            rows = [[]]
            for position, index in enumerate(order):
                rows[-1].append(index)
                if position < count - 1 and cut & (1 << position):
                    rows.append([])
            ratios = [sum((Fraction(images[i].width, images[i].height) for i in row), Fraction()) for row in rows]
            # One common row width: singleton rows become equal-width vertical panels;
            # panels sharing a row have equal height. Never downsample a source region.
            row_width = max(math.ceil(max(images[i].height for i in row) * ratio) + gap * (len(row) - 1)
                            for row, ratio in zip(rows, ratios))
            sizes, heights = {}, []
            for row, ratio in zip(rows, ratios):
                available = row_width - gap * (len(row) - 1)
                image_height = round(Fraction(available) / ratio)
                exact = [Fraction(available) * Fraction(images[i].width, images[i].height) / ratio for i in row]
                widths = [math.floor(value) for value in exact]
                remainder = available - sum(widths)
                for position in sorted(range(len(row)), key=lambda p: (-(exact[p] - widths[p]), p))[:remainder]:
                    widths[position] += 1
                for i, image_width in zip(row, widths):
                    sizes[i] = (image_width, image_height)
                heights.append(image_height + title)
            width, height = row_width + 2 * margin, sum(heights) + gap * (len(rows) - 1) + 2 * margin
            output_width, output_height = width, height
            if width * 16 < height * 9:
                output_width = (height * 9 + 15) // 16
            elif width * 3 > height * 4:
                output_height = (width * 3 + 3) // 4
            area = output_width * output_height
            score = (area, area - width * height, max(output_width, output_height), order, cut)
            if best is None or score < best[0]:
                best = (score, rows, sizes, heights, width, height, output_width, output_height)
    _, rows, sizes, heights, width, height, output_width, output_height = best
    canvas = Image.new('RGB', (output_width, output_height), 'white')
    draw = ImageDraw.Draw(canvas)
    font_path = find_font(None)
    font = ImageFont.truetype(str(font_path), 22) if font_path else ImageFont.load_default(size=22)
    records, regions = [], []
    y = margin + (output_height - height) // 2
    for row_index, row in enumerate(rows):
        x = margin + (output_width - width) // 2
        record_row = {'panels': []}
        for column, index in enumerate(row):
            pixels, panel = images[index], signature['panels'][index]
            native_size = pixels.size
            if sizes[index] != native_size:
                pixels = pixels.resize(sizes[index], Image.Resampling.LANCZOS)
            if count == 2:
                location = ('左侧' if column == 0 else '右侧') if len(rows) == 1 else ('上方' if row_index == 0 else '下方')
            else:
                location = f'第{row_index + 1}行第{column + 1}列'
            region_id = f'p{index + 1}'
            location += f'（{region_id}区）'
            label = panel.get('label', region_id)
            draw.text((x, y), f'{region_id} {label}', fill='black', font=font)
            canvas.paste(pixels, (x, y + title), pixels.getchannel('A') if pixels.mode == 'RGBA' else None)
            box = [x, y + title, x + pixels.width, y + title + pixels.height]
            item = {**panel, 'region_id': region_id, 'location': location, 'box': box,
                    'native_size': list(native_size), 'display_size': list(pixels.size),
                    'actual_scale_xy': [pixels.width / native_size[0], pixels.height / native_size[1]]}
            record_row['panels'].append(item)
            regions.append(item)
            x += pixels.width + gap
        records.append(record_row)
        y += heights[row_index] + gap
    directory.mkdir(parents=True, exist_ok=True)
    canvas.save(path)
    saved = {'artifact': 'aligned_bounded_reference', 'output_sha256': digest(path), 'output_size': list(canvas.size),
             'content_size': [width, height], 'rows': records, 'regions': regions,
             'aspect_bounds': {'portrait': [9, 16], 'landscape': [4, 3]}, 'resampling': 'LANCZOS',
             'visual_assessment': 'not_evaluated',
             'resized': any(r['native_size'] != r['display_size'] for r in regions)}
    workflow.write(record, saved)
    return path, saved


def fit(root, spec, keys, maximum):
    """Deduplicate exact roles, then merge until capacity fits; retain all semantic sources."""
    result = copy.deepcopy(spec)
    require(type(maximum) is int and maximum > 0, 'max_reference_images须为正整数')
    require(len(keys) == len(spec['references']), '参考键与实际输入不一致，不能拼合')
    groups, duplicates, dimensions = [], [], {}
    baseline = spec.get('baseline_reference') if spec['operation'] == 'edit' else None
    for index, (ref, key) in enumerate(zip(spec['references'], keys), 1):
        atom = {'index': index, 'source_key': key, 'image': ref['image'], 'role': ref['role'],
                **({'guide': ref['guide']} if ref.get('guide') else {})}
        match = next((g for g in groups if len(g) == 1 and index != baseline and g[0]['index'] != baseline
                      and digest(Path(g[0]['image'])) == digest(Path(ref['image']))
                      and g[0].get('guide') == ref.get('guide')), None)
        if match:
            duplicates.append({'index': index, 'kept_index': match[0]['index'], 'reason': '相同文件与guide，保留并合并用途'})
            match[0].setdefault('aliases', []).append(index)
            if ref['role'] not in match[0]['role']:
                match[0]['role'] += '；' + ref['role']
        else:
            groups.append([atom])
    while len(groups) > maximum:
        choices = []
        for a, b in itertools.combinations(range(len(groups)), 2):
            atoms = groups[a] + groups[b]
            if any(p['index'] == baseline for p in atoms):
                continue  # Editing still needs its original unique baseline, not a collage.
            guides = {p.get('guide') for p in atoms}
            pair = guides == {'front_material', 'back_silhouette'}
            same = len(guides) == 1
            for p in atoms:
                if p['image'] not in dimensions:
                    with Image.open(p['image']) as image:
                        dimensions[p['image']] = image.size
            area = sum(dimensions[p['image']][0] * dimensions[p['image']][1] for p in atoms)
            choices.append(((0 if pair else 1 if same else 2, len(atoms), area, a, b), a, b))
        require(choices, '容量不足以同时保留唯一编辑底图与必要参考；调整工具容量，不能拼合或替换编辑底图')
        _, a, b = min(choices)
        groups[a] += groups[b]
        del groups[b]
    mapping, refs, packed = {}, [], []
    for index, atoms in enumerate(groups, 1):
        if len(atoms) == 1:
            ref = spec['references'][atoms[0]['index'] - 1]
            refs.append(copy.deepcopy(ref))
            refs[-1]['role'] = atoms[0]['role']
            region_id = None
            locations = {}
        else:
            atoms.sort(key=lambda a: a['index'])
            path, saved = aligned_board(root, [dict(p, label=p.get('guide', p['source_key'])) for p in atoms])
            regions = [{'id': r['region_id'], 'location': r['location'], 'box': r['box'],
                        'role': r['role'], 'source_key': r['source_key'],
                        **({'guide': r['guide']} if r.get('guide') else {})} for r in saved['regions']]
            refs.append({'image': str(path), 'role': '分区参考图，严格按各区域的限定用途参考。', 'regions': regions})
            locations = {r['index']: r['region_id'] for r in saved['regions']}
            packed.append({'reference_index': index, 'file': workflow.local(root, path), 'sha256': digest(path),
                           'record_file': workflow.local(root, path.with_suffix('.png.json')),
                           'record_sha256': digest(path.with_suffix('.png.json')), 'regions': regions})
        for atom in atoms:
            region_id = locations.get(atom['index'])
            for old in [atom['index'], *atom.get('aliases', [])]:
                mapping[old] = {'index': index, **({'region': region_id} if region_id else {})}
    result['references'] = refs
    for c in result['critical_constraints']:
        selections = [mapping[i] for i in c['source_indices']]
        c['source_indices'] = list(dict.fromkeys(s['index'] for s in selections))
        if any('region' in s for s in selections):
            c['source_regions'] = [s for i, s in enumerate(selections) if s not in selections[:i]]
    if baseline:
        result['baseline_reference'] = mapping[baseline]['index']
    return result, mapping, {'capacity': maximum, 'before_count': len(keys), 'after_count': len(refs),
                             'duplicates_removed': duplicates, 'collages': packed}
