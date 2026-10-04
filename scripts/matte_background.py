"""Create a reviewed-candidate alpha PNG from a flat background, local rembg, or a supplied mask.

Requires Pillow and numpy. Flat mode is conservative border-connected color removal,
not semantic segmentation. It cannot recover true transparency from a flattened photo.
Default rembg mode requires rembg[cpu] and its model; installation is never automatic.
"""
import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def border_connected(mask, seeds=()):
    """Run-length union-find, 4-connected; avoids a Python loop per image pixel."""
    import numpy as np
    h, w = mask.shape
    parents, touches, rows = [], [], []

    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    previous = []
    for y in range(h):
        edges = np.flatnonzero(np.diff(np.pad(mask[y].astype(np.int8), (1, 1))))
        runs = []
        for a, b in edges.reshape(-1, 2):
            i = len(parents)
            parents.append(i)
            touches.append(y in (0, h - 1) or a == 0 or b == w)
            runs.append((int(a), int(b), i))
        j = 0
        for a, b, i in runs:
            while j < len(previous) and previous[j][1] <= a:
                j += 1
            k = j
            while k < len(previous) and previous[k][0] < b:
                p, q = root(i), root(previous[k][2])
                if p != q:
                    parents[q] = p
                    touches[p] = touches[p] or touches[q]
                k += 1
        rows.append(runs)
        previous = runs
    for x, y in seeds:
        if not 0 <= x < w or not 0 <= y < h or not mask[y, x]:
            raise ValueError(f'Background seed is outside the eligible background: {(x, y)}')
        for a, b, i in rows[y]:
            if a <= x < b:
                touches[root(i)] = True
                break
    result = np.zeros((h, w), dtype=bool)
    for y, runs in enumerate(rows):
        for a, b, i in runs:
            if touches[root(i)]:
                result[y, a:b] = True
    return result


def flat_alpha(image, background, tolerance, feather, seeds=(), protect=None):
    import numpy as np
    from PIL import Image, ImageFilter
    rgb = np.asarray(image.convert('RGB')).astype(np.int16)
    distance = np.max(np.abs(rgb - np.asarray(background, dtype=np.int16)), axis=2)
    eligible = distance <= tolerance
    if protect is not None:
        eligible &= np.asarray(protect) == 0
    removed = border_connected(eligible, seeds)
    alpha = Image.fromarray(np.where(removed, 0, 255).astype(np.uint8))
    if feather:
        softened = np.asarray(alpha.filter(ImageFilter.GaussianBlur(feather)))
        # Do not grow alpha into known background; feather only the existing edge.
        alpha = Image.fromarray(np.where(removed, 0, softened).astype(np.uint8))
    if protect is not None:
        alpha = Image.fromarray(np.maximum(np.asarray(alpha), np.asarray(protect)))
    return alpha


def diagnostic(rgba, folder, stem):
    from PIL import Image, ImageDraw
    folder.mkdir(parents=True, exist_ok=True)
    reduced = rgba.copy()
    reduced.thumbnail((1000, 900), Image.Resampling.LANCZOS)
    paths = []
    for name, color in (('浅底', (230, 230, 230, 255)), ('深底', (45, 49, 55, 255))):
        output = folder / f'{stem}_{name}.png'
        if output.exists():
            raise ValueError(f'Diagnostic output exists: {output}')
        result = Image.alpha_composite(Image.new('RGBA', reduced.size, color), reduced).convert('RGB')
        result.save(output)
        paths.append(str(output))
    return paths


def matte(source, output, args, session=None):
    import numpy as np
    from PIL import Image, ImageOps
    source, output = source.resolve(), output.resolve()
    sidecar = output.with_suffix(output.suffix + '.json')
    if output == source or output.exists() or sidecar.exists() or output.suffix.lower() != '.png':
        raise ValueError('Use a new PNG output; never overwrite an input or existing record')
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert('RGBA')
    mask_records = {}
    protect = None
    for label, path in (('protect_mask', args.protect_mask), ('alpha_mask', args.alpha_mask)):
        if path:
            with Image.open(path) as opened:
                mask = opened.convert('L')
            if mask.size != image.size:
                raise ValueError('Masks must match the oriented original dimensions')
            mask_records[label] = {'file': str(path.resolve()), 'sha256': digest(path)}
            if label == 'protect_mask':
                protect = mask
            else:
                supplied_alpha = mask
    low, high = image.getchannel('A').getextrema()
    foreground_rgb = image.copy()
    if low < 255 and high > 0:
        alpha, origin = image.getchannel('A'), 'existing_alpha_preserved'
    elif args.method == 'mask':
        if not args.alpha_mask:
            raise ValueError('Mask mode needs --alpha-mask')
        alpha, origin = supplied_alpha, 'supplied_mask'
    elif args.method == 'rembg':
        from rembg import remove
        with contextlib.redirect_stdout(sys.stderr):
            extracted = remove(image.convert('RGB'), session=session, only_mask=not args.alpha_matting,
                               alpha_matting=args.alpha_matting)
        if extracted.size != image.size:
            raise ValueError('Segmentation returned a different size; do not rescale or concatenate subjects')
        if args.alpha_matting:
            foreground_rgb = extracted.convert('RGBA')
            alpha = foreground_rgb.getchannel('A')
        else:
            alpha = extracted.convert('L')
        origin = 'local_segmentation_estimate'
    else:
        alpha = flat_alpha(image, args.background, args.tolerance, args.feather,
                           args.background_seed, protect)
        origin = 'flat_border_connected_estimate'
    if protect is not None and args.method != 'flat':
        alpha = Image.fromarray(np.maximum(np.asarray(alpha), np.asarray(protect)))
    lo, hi = alpha.getextrema()
    if lo != 0 or hi == 0:
        raise ValueError('No fully transparent background or no subject; do not claim success')
    result = foreground_rgb
    result.putalpha(alpha)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        result.save(stream, format='PNG')
    record = {'operation': 'python_background_matting', 'visual_status': 'not_evaluated',
              'alpha_origin': origin, 'source': str(source), 'source_sha256': digest(source),
              'output': str(output), 'output_sha256': digest(output), 'size': list(result.size),
              'method': args.method, 'model': args.model if args.method == 'rembg' else None,
              'background_rgb': args.background, 'tolerance': args.tolerance,
              'feather': args.feather, 'background_seeds': args.background_seed,
              'masks': mask_records,
              'rgb_preserved': bool(np.array_equal(np.asarray(result)[:, :, :3], np.asarray(image)[:, :, :3])),
              'resized': False,
              'true_material_alpha_recovered': False,
              'alpha_extrema': [lo, hi], 'diagnostics': []}
    if args.preview_dir:
        record['diagnostics'] = diagnostic(result, args.preview_dir, output.stem)
    sidecar.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return record


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--output', type=Path)
    target.add_argument('--output-dir', type=Path)
    parser.add_argument('--method', choices=('flat', 'rembg', 'mask'), default='rembg')
    parser.add_argument('--background', default='FFFFFF', help='Flat background RGB hex')
    parser.add_argument('--tolerance', type=int, default=10, help='Color distance 0..60; lower preserves pale details')
    parser.add_argument('--feather', type=float, default=0.4, help='Edge-only alpha softening radius 0..2')
    parser.add_argument('--background-seed', nargs=2, type=int, action='append', default=[])
    parser.add_argument('--protect-mask', type=Path, help='L mask, white protects opaque foreground')
    parser.add_argument('--alpha-mask', type=Path)
    parser.add_argument('--model', default='isnet-general-use')
    parser.add_argument('--alpha-matting', action=argparse.BooleanOptionalAction, default=True,
                        help='Estimate foreground colors and soft alpha (default); --no-alpha-matting keeps input RGB')
    parser.add_argument('--preview-dir', type=Path)
    args = parser.parse_args()
    try:
        color = args.background.lstrip('#')
        if len(color) != 6 or not 0 <= args.tolerance <= 60 or not 0 <= args.feather <= 2:
            raise ValueError('Invalid color, tolerance, or feather')
        args.background = [int(color[i:i + 2], 16) for i in (0, 2, 4)]
        if args.source.is_dir():
            if not args.output_dir or args.alpha_mask or args.protect_mask or args.background_seed:
                raise ValueError('Directory mode requires --output-dir and no shared per-image masks/seeds')
            files = sorted(p for p in args.source.iterdir() if p.is_file() and p.suffix.lower() in
                           ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'))
        else:
            files = [args.source]
        if not files:
            raise ValueError('No images')
        plans = [(p, args.output or args.output_dir / f'{p.stem}_{p.suffix.lstrip(".")}_透明.png') for p in files]
        for source, output in plans:
            if not source.is_file() or source.resolve() == output.resolve() or output.exists() or output.with_suffix('.png.json').exists():
                raise ValueError(f'Input missing or output already exists: {source} -> {output}')
        session = None
        if args.method == 'rembg':
            from rembg import new_session
            with contextlib.redirect_stdout(sys.stderr):
                session = new_session(args.model, providers=['CPUExecutionProvider'])
        results, failures = [], []
        for source, output in plans:
            try:
                results.append(matte(source, output, args, session))
            except (OSError, ValueError) as exc:
                failures.append({'source': str(source), 'error': str(exc)})
        print(json.dumps({'candidates': results, 'failures': failures, 'visual_status': 'not_evaluated'},
                         ensure_ascii=False, indent=2))
        return 1 if failures else 0
    except (OSError, ValueError, ImportError) as exc:
        print(json.dumps({'created': False, 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
