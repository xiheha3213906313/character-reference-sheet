"""Separate generation packs from flat review evidence, retaining native source pixels."""
from pathlib import Path

from compose_review import load_panel
from review_v2 import read, digest, fingerprint, require
import review_workflow as workflow


def expand(root, sources):
    mapping, additions = {}, {}

    def leaves(path, crop=None, trail=()):
        if crop is not None:
            from PIL import Image
            with Image.open(path) as image:
                if crop == [0, 0, image.width, image.height]:
                    crop = None
        sidecar = path.with_suffix('.png.json')
        layout = read(sidecar) if sidecar.is_file() else {}
        if layout.get('artifact') == 'aligned_bounded_reference' and crop is None:
            require(str(path) not in trail, '参考拼图来源循环')
            require(layout['output_sha256'] == digest(path), '参考拼图与来源旁录不符')
            result = []
            for region in layout['regions']:
                source = Path(region['image'])
                require(digest(source) == region['sha256'], '拼图原始来源已变化')
                result.extend(leaves(source, region.get('crop'), (*trail, str(path))))
            return result
        pixels, info = load_panel(path, crop)
        key = 'leaf_' + fingerprint({'sha256': digest(path), 'crop': info['crop']})[:20]
        output = root / '制作记录/参考辅助/验收原生' / (key + '.png')
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            from PIL import Image
            with Image.open(output) as image:
                require(image.size == pixels.size and image.convert('RGBA').tobytes() == pixels.convert('RGBA').tobytes(),
                        '验收原生局部已变化')
        else:
            pixels.save(output)
        workflow.write(output.with_suffix('.png.json'), {'artifact': 'native_review_reference',
                       'source_file': str(path), 'source_sha256': digest(path), 'crop': info['crop'],
                       'output_sha256': digest(output), 'native_size': list(pixels.size), 'resampled': False})
        additions[key] = {'file': str(output)}
        return [key]

    for key, source in sources.items():
        path = Path(source['file'])
        sidecar = path.with_suffix('.png.json')
        layout = read(sidecar) if sidecar.is_file() else {}
        mapping[key] = list(dict.fromkeys(leaves(path))) if layout.get('artifact') == 'aligned_bounded_reference' else [key]
    sources.update(additions)
    from PIL import Image
    import hashlib
    canonical = {}
    for key, values in mapping.items():
        result = []
        for leaf in values:
            with Image.open(sources[leaf]['file']) as image:
                rgba = image.convert('RGBA')
                signature = (rgba.size, hashlib.sha256(rgba.tobytes()).hexdigest())
            selected = canonical.setdefault(signature, leaf)
            if selected not in result:
                result.append(selected)
        mapping[key] = result
    return mapping


def remap_goals(goals, mapping, planned_sources=None, sources=None):
    for goal in goals:
        goal['source_ids'] = list(dict.fromkeys(leaf for key in goal['source_ids'] for leaf in mapping[key]))
        exact = (planned_sources or {}).get(goal['id'])
        if exact:
            sha, crop = exact
            matched = []
            for key in goal['source_ids']:
                path = Path(sources[key]['file'])
                meta = read(path.with_suffix('.png.json')) if path.with_suffix('.png.json').is_file() else {}
                if digest(path) == sha or meta.get('source_sha256') == sha and (crop is None or meta.get('crop') == crop):
                    matched.append(key)
            require(matched, '选材检查项找不到其原始来源/局部：' + goal['id'])
            goal['source_ids'] = matched


def groups(goals):
    """Each reference appears once; one board covers all checks using either of its references."""
    keys = list(dict.fromkeys(key for goal in goals if goal['group'] in ('identity', 'design', 'spatial')
                             and goal.get('reference_display') != 'context_only'
                             for key in goal['source_ids']))
    return [{'reference_ids': ['source_' + key for key in keys[pos:pos + 2]],
             'check_ids': [g['id'] for g in goals if g.get('reference_display') != 'context_only'
                           and set(g['source_ids']) & set(keys[pos:pos + 2])],
             'reference_scopes': [{'reference_id': 'source_' + key,
                 'checks': [{'id': g['id'], 'target': g['target'], 'evaluation_scope': g.get('evaluation_scope', '')}
                            for g in goals if key in g['source_ids'] and g.get('reference_display') != 'context_only']}
                                  for key in keys[pos:pos + 2]]}
            for pos in range(0, len(keys), 2)]
