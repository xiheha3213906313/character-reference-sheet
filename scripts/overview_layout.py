"""Pillow-only overview geometry; full silhouettes, never semantic approval."""
import math
from PIL import Image, ImageChops

ORDER = ('head', 'front', 'left', 'back')
ALPHA_THRESHOLD = 4


def support(image):
    """Opaque RGB rectangles remain rectangles; no unrequested background removal."""
    return image.convert('RGBA').getchannel('A').point(
        lambda value: 255 if value > ALPHA_THRESHOLD else 0)


def edge_support(image):
    """Measure visible edges without changing alpha or the composited RGB."""
    rgba = image.convert('RGBA')
    if rgba.getchannel('A').getextrema() != (255, 255):
        return support(rgba)
    difference = ImageChops.difference(rgba.convert('RGB'), Image.new('RGB', rgba.size, 'white'))
    red, green, blue = difference.split()
    return ImageChops.lighter(ImageChops.lighter(red, green), blue).point(
        lambda value: 255 if value > 12 else 0)


def local_rows(image, size):
    alpha = support(image.resize(size, Image.Resampling.LANCZOS))
    return mask_rows(alpha)


def mask_rows(mask):
    return [mask.crop((0, y, mask.width, y + 1)).getbbox() for y in range(mask.height)]


def prepared_masks(image, size):
    resized = image.resize(size, Image.Resampling.LANCZOS)
    mask, edge = support(resized), edge_support(resized)
    return {'mask': mask, 'edge': edge, 'area': mask.histogram()[255],
            'edge_area': edge.histogram()[255], 'box': mask.getbbox(), 'edge_box': edge.getbbox()}


def placed_rows(rows, y, canvas_height):
    result = [None] * canvas_height
    for offset, box in enumerate(rows):
        if box:
            result[y + offset] = (box[0], box[2] - 1)
    return result


def separated_x(previous, current, gap):
    return max((a[1] - b[0] + gap + 1 for a, b in zip(previous, current)
                if a is not None and b is not None), default=0)


def pair_pixels(first, first_pos, second, second_pos):
    a, b = first['box'], second['box']
    if not a or not b:
        return 0
    ax, ay = first_pos; bx, by = second_pos
    box = (max(a[0] + ax, b[0] + bx), max(a[1] + ay, b[1] + by),
           min(a[2] + ax, b[2] + bx), min(a[3] + ay, b[3] + by))
    if box[0] >= box[2] or box[1] >= box[3]:
        return 0
    crops = [data['mask'].crop((box[0] - x, box[1] - y, box[2] - x, box[3] - y))
             for data, (x, y) in ((first, first_pos), (second, second_pos))]
    return ImageChops.multiply(*crops).histogram()[255]


def bounded_distance(first, first_y, second, second_y, separated, allowed):
    """Find the smallest sampled distance satisfying this pair's own area limit."""
    limit = min(first['area'], second['area']) * .005
    for penetration in sorted({0, allowed, *range(0, allowed + 1, 4)}, reverse=True):
        distance = max(0, separated - penetration)
        if pair_pixels(first, (0, first_y), second, (distance, second_y)) <= limit:
            return distance
    return separated


def settings_from_layout(layout, canvas=(2560, 1440)):
    width, height = canvas
    settings = {'head_width': 880, 'head_height': height, 'head_min_width': math.ceil(width / 3),
                'body_height': 1200, 'body_min_height': 960, 'body_bottom': height - 10,
                'max_vertical_offset': 0, 'gap': 0, 'right_margin': 0,
                'maximize_subjects': True, 'edge_overlap_px': 128, 'edge_overlap_limits': {}}
    reasons = {'head_min_width_reason', 'right_margin_reason', 'fixed_scale_reason'}
    if not isinstance(layout, dict) or set(layout) - (set(settings) | reasons | {'subject_boxes', 'overlap_px'}):
        raise ValueError('layout须为对象且不含未知选项')
    if layout.get('overlap_px', 0) != 0:
        raise ValueError('不能用overlap_px强制叠压；使用受限外缘搜索并实际检查交叠')
    settings.update({k: v for k, v in layout.items() if k in settings})
    if type(settings['maximize_subjects']) is not bool:
        raise ValueError('maximize_subjects须为布尔值')
    if any(type(v) is not int for k, v in settings.items() if k not in ('maximize_subjects', 'edge_overlap_limits')):
        raise ValueError('layout尺寸须为整数')
    limits = settings['edge_overlap_limits']
    valid_pairs = {f'{r}:{s}' for i, r in enumerate(ORDER) for s in ORDER[i + 1:]}
    if (not isinstance(limits, dict) or set(limits) - valid_pairs
            or any(type(v) is not int or not 0 <= v <= settings['edge_overlap_px'] for v in limits.values())):
        raise ValueError('edge_overlap_limits须为有效视图对及其非负搜索范围，不超过全局范围')
    if (settings['head_min_width'] < math.ceil(width / 3) and not str(layout.get('head_min_width_reason', '')).strip()
            or settings['right_margin'] and not str(layout.get('right_margin_reason', '')).strip()
            or not settings['maximize_subjects'] and not str(layout.get('fixed_scale_reason', '')).strip()):
        raise ValueError('小特写、右留白或固定小尺寸须记录用户另行指定的理由')
    if not (400 <= settings['head_min_width'] <= settings['head_width'] <= width
            and 600 <= settings['head_height'] <= height
            and 600 <= settings['body_min_height'] <= settings['body_height'] <= settings['body_bottom'] <= height
            and 0 <= settings['gap'] <= 120 and 0 <= settings['max_vertical_offset'] <= 60
            and 0 <= settings['right_margin'] < width and 0 <= settings['edge_overlap_px'] <= width):
        raise ValueError('layout超出画布或受限外缘搜索范围')
    settings.update({k: layout[k] for k in reasons if k in layout})
    return settings


def overlap_within_limit(geometry, images, opaque=None):
    """Geometric eligibility only. Positive overlap still needs visual review."""
    if opaque is None:
        opaque = {r: images[r].convert('RGBA').getchannel('A').getextrema()[0] == 255 for r in ORDER}
    for pair, overlap in geometry['pair_intersections'].items():
        if not overlap['pixels']:
            continue
        a, b = pair.split(':')
        if any(opaque[r] for r in (a, b)):
            return False  # An opaque white rectangle must not cover another view.
        if overlap['pixels'] > min(geometry['visible_areas'][a], geometry['visible_areas'][b]) * .005:
            return False
    return True


def plan(images, settings, canvas=(2560, 1440)):
    """Maximize full-view height, then portrait width, within reviewed edge limits."""
    width, height = canvas
    minimum = settings['head_min_width']
    head = images['head']
    cap = min(width, math.floor(settings['head_height'] * head.width / head.height))
    maximizing = settings.get('maximize_subjects', True)
    if not maximizing:
        cap = min(cap, settings['head_width'])
    if cap < minimum:
        raise ValueError('特写显示宽度达不到最低要求，不能靠拉伸凑占比')
    head_widths = sorted({minimum, cap, settings['head_width'], *range(minimum, cap + 1, 10)}, reverse=True)
    heads = {}
    for target in head_widths:
        if not minimum <= target <= cap:
            continue
        size = (target, round(head.height * target / head.width))
        y = height - size[1]
        visible = edge_support(head.resize(size, Image.Resampling.LANCZOS)).getbbox()
        if y >= 0 and visible and visible[2] - visible[0] >= minimum:
            masks = prepared_masks(head, size)
            heads[target] = (size, y, placed_rows(mask_rows(masks['mask']), y, height), masks)
    bottom = settings['body_bottom']
    upper = bottom if maximizing else settings['body_height']
    lower = settings['body_min_height']
    offsets = sorted({0, settings['max_vertical_offset'] // 2, settings['max_vertical_offset']})
    patterns = [(0, 0, 0), *[(a, 0, b) for a in offsets for b in offsets if a or b]]
    candidates = list(range(upper, lower - 1, -10))
    if candidates[-1] != lower:
        candidates.append(lower)
    allowed = settings.get('edge_overlap_px', 128)
    limits = settings.get('edge_overlap_limits', {})
    def penetration_limit(first, second):
        return limits.get(f'{first}:{second}', allowed)
    gaps = [*sorted({settings['gap'], 0, -allowed, *range(0, -allowed - 1, -4)}, reverse=True), None]
    opaque = {r: images[r].convert('RGBA').getchannel('A').getextrema()[0] == 255 for r in ORDER}
    examined = 0
    for body_height in candidates:
        bodies = {}
        for role in ORDER[1:]:
            image = images[role]
            size = (max(1, round(image.width * body_height / image.height)), body_height)
            masks = prepared_masks(image, size)
            bodies[role] = (size, mask_rows(masks['mask']), masks)
        back_edge = bodies['back'][2]['edge_box']
        if not back_edge:
            continue
        row_cache, distance_cache, bounded_cache = {}, {}, {}
        def rows(role, offset):
            key = (role, offset)
            if key not in row_cache:
                row_cache[key] = placed_rows(bodies[role][1], bottom - offset - body_height, height)
            return row_cache[key]
        def distance(first, first_offset, second, second_offset):
            key = (first, first_offset, second, second_offset)
            if key not in distance_cache:
                distance_cache[key] = separated_x(rows(first, first_offset), rows(second, second_offset), 0)
            return distance_cache[key]
        best = None
        for head_width, (head_size, head_y, head_rows, head_masks) in heads.items():
            head_distances, head_bounded = {}, {}
            for stagger in patterns:
                if any(bottom - off - body_height < 0 for off in stagger):
                    continue
                body_offsets = dict(zip(ORDER[1:], stagger))
                for role, off in body_offsets.items():
                    if (role, off) not in head_distances:
                        head_distances[(role, off)] = separated_x(head_rows, rows(role, off), 0)
                for gap in gaps:
                    examined += 1
                    positions, sizes = {'head': (0, head_y)}, {'head': head_size}
                    for index, role in enumerate(ORDER[1:]):
                        off = body_offsets[role]
                        key = (role, off)
                        head_distance = head_distances[key]
                        if gap is None:
                            if not (opaque['head'] or opaque[role]):
                                if key not in head_bounded:
                                    head_bounded[key] = bounded_distance(head_masks, head_y, bodies[role][2],
                                        bottom - off - body_height, head_distance, penetration_limit('head', role))
                                head_distance = head_bounded[key]
                        else:
                            pair_gap = max(0, gap) if opaque['head'] or opaque[role] else gap
                            pair_gap = max(pair_gap, -penetration_limit('head', role))
                            head_distance += pair_gap
                        constraints = [0, head_distance]
                        for previous in ORDER[1:index + 1]:
                            previous_offset = body_offsets[previous]
                            required = distance(previous, previous_offset, role, off)
                            if gap is None:
                                if not (opaque[previous] or opaque[role]):
                                    key = (previous, previous_offset, role, off)
                                    if key not in bounded_cache:
                                        bounded_cache[key] = bounded_distance(bodies[previous][2], bottom - previous_offset - body_height,
                                            bodies[role][2], bottom - off - body_height, required, penetration_limit(previous, role))
                                    required = bounded_cache[key]
                            else:
                                pair_gap = max(0, gap) if opaque[previous] or opaque[role] else gap
                                pair_gap = max(pair_gap, -penetration_limit(previous, role))
                                required += pair_gap
                            constraints.append(positions[previous][0] + required)
                        positions[role] = (max(constraints), bottom - off - body_height)
                        sizes[role] = bodies[role][0]
                    surplus = width - settings.get('right_margin', 0) - positions['back'][0] - back_edge[2]
                    if surplus < 0:
                        continue
                    for index, role in enumerate(ORDER[1:], 1):
                        x, y = positions[role];positions[role] = (x + surplus * index // 3, y)
                    if any(positions[r][0] + sizes[r][0] > width for r in ('head', 'front', 'left')):
                        continue
                    masks = {'head': head_masks, **{r: bodies[r][2] for r in ORDER[1:]}}
                    geometry = mask_metrics(masks, positions, canvas)
                    if any(geometry['clipped_visible_pixels'].values()) or not overlap_within_limit(geometry, images, opaque):
                        continue
                    overlap = sum(p['pixels'] for p in geometry['pair_intersections'].values())
                    score = (head_width, -overlap, -max(stagger), -sum(stagger))
                    if best is None or score > best[0]:
                        best = (score, positions, sizes, stagger, gap, surplus, geometry)
                    break  # Same size/offsets: deeper overlap cannot improve the size objective.
            if best:  # Head candidates descend; lower widths cannot improve the objective.
                break
        if best:
            _, positions, sizes, stagger, gap, surplus, geometry = best
            return positions, sizes, {'algorithm': 'maximize_subjects_with_bounded_edges',
                'head_min_width': minimum, 'head_width': sizes['head'][0],
                'body_height': body_height, 'body_bottoms': [bottom - v for v in stagger],
                'gap': gap if gap is not None else 'per_pair_bounds', 'max_vertical_offset': settings['max_vertical_offset'],
                'right_margin': settings.get('right_margin', 0), 'surplus_distributed_px': surplus,
                'search': {'objective': 'body_height_then_head_width', 'body_upper': upper,
                    'body_lower': lower, 'step_px': 10, 'head_upper': cap,
                    'edge_overlap_px': allowed, 'overlap_area_ratio_limit': .005,
                    'pair_specific_bounds_checked': True,
                    'reviewed_pair_search_limits': limits,
                    'candidate_count': examined, 'larger_body_candidates_rejected': sum(v > body_height for v in candidates)},
                'requires_overlap_review': [k for k, p in geometry['pair_intersections'].items() if p['pixels']]}
    raise ValueError('保留特写最低宽度及有效主体后无法排入画布；不裁人物或强行叠压')


def metrics(images, positions, sizes, canvas=(2560, 1440)):
    masks = {role: prepared_masks(images[role], sizes[role]) for role in ORDER}
    return mask_metrics(masks, positions, canvas)


def mask_metrics(prepared, positions, canvas):
    """Measure cached local masks; only intersect small shared regions during search."""
    areas, bounds, clipped, alpha_bounds = {}, {}, {}, {}
    for role in ORDER:
        data = prepared[role]
        x, y = positions[role]
        mask, edge = data['mask'], data['edge']
        inside = (max(0, -x), max(0, -y), min(mask.width, canvas[0] - x), min(mask.height, canvas[1] - y))
        def shifted(box):
            return [box[0] + x, box[1] + y, box[2] + x, box[3] + y] if box else None
        if inside == (0, 0, mask.width, mask.height):
            areas[role], clipped[role] = data['area'], 0
            bounds[role], alpha_bounds[role] = shifted(data['edge_box']), shifted(data['box'])
        else:
            cropped_mask, cropped_edge = mask.crop(inside), edge.crop(inside)
            areas[role] = cropped_mask.histogram()[255]
            clipped[role] = data['edge_area'] - cropped_edge.histogram()[255]
            def cropped_bounds(value):
                box = value.getbbox()
                return [box[0] + x + inside[0], box[1] + y + inside[1],
                        box[2] + x + inside[0], box[3] + y + inside[1]] if box else None
            bounds[role], alpha_bounds[role] = cropped_bounds(cropped_edge), cropped_bounds(cropped_mask)
    pairs = {}
    for i, role in enumerate(ORDER):
        for other in ORDER[i + 1:]:
            a, b = alpha_bounds[role], alpha_bounds[other]
            box = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])) if a and b else None
            pixels, overlap_box = 0, None
            if box and box[0] < box[2] and box[1] < box[3]:
                crops = []
                for r in (role, other):
                    x, y = positions[r]
                    crops.append(prepared[r]['mask'].crop((box[0] - x, box[1] - y, box[2] - x, box[3] - y)))
                intersection = ImageChops.multiply(*crops)
                pixels = intersection.histogram()[255]
                local = intersection.getbbox()
                if local:
                    overlap_box = [local[0] + box[0], local[1] + box[1], local[2] + box[0], local[3] + box[1]]
            pairs[f'{role}:{other}'] = {'pixels': pixels, 'box': overlap_box}
    head_box = bounds['head']
    return {'alpha_threshold': ALPHA_THRESHOLD, 'visible_areas': areas,
            'head_visible_width': head_box[2] - head_box[0] if head_box else 0,
            'visible_bounds': bounds,
            'right_margin': canvas[0] - bounds['back'][2] if bounds['back'] else None,
            'clipped_visible_pixels': clipped,
            'pair_intersections': pairs}
