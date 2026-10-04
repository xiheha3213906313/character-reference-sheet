"""Extract a native-size Alpha silhouette and mirror left-right; requires Pillow."""

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageOps


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(source, output_dir, threshold=4):
    if not 0 <= threshold <= 254:
        raise ValueError('Alpha threshold must be between 0 and 254.')
    source = Path(source).resolve(strict=True)
    output_dir = Path(output_dir).resolve()
    paths = {
        'front': output_dir / 'front-silhouette.png',
        'back': output_dir / 'back-silhouette.png',
        'record': output_dir / 'silhouette-record.json',
    }
    for path in paths.values():
        if path.exists():
            raise FileExistsError(f'Refusing to overwrite: {path}')
    source_hash = sha256(source)
    with Image.open(source) as image:
        if image.getexif().get(274, 1) != 1:
            raise ValueError('Normalize and verify source orientation before extracting the silhouette.')
        if 'A' not in image.getbands() and 'transparency' not in image.info:
            raise ValueError('Source has no Alpha. Prepare and visually verify a foreground segmentation first.')
        alpha = image.convert('RGBA').getchannel('A')
        alpha_range = alpha.getextrema()
        if alpha_range[0] > threshold:
            raise ValueError('Alpha has no background at this threshold. Use a verified segmented source.')
        if alpha_range[1] <= threshold:
            raise ValueError('No foreground remains at this Alpha threshold.')
        front = alpha.point([0 if value > threshold else 255 for value in range(256)]).convert('RGB')
        size = image.size
    back = ImageOps.mirror(front)
    # The operation preserves y, canvas size, foreground holes and thin visible parts.
    if ImageOps.mirror(back).tobytes() != front.tobytes():
        raise RuntimeError('Mirror verification failed.')
    if sha256(source) != source_hash:
        raise RuntimeError('Source changed during extraction; retry with a fixed source.')
    output_dir.mkdir(parents=True, exist_ok=True)
    front.save(paths['front'], format='PNG')
    back.save(paths['back'], format='PNG')
    record = {
        'source': str(source),
        'source_sha256': source_hash,
        'source_size': list(size),
        'source_alpha_extrema': list(alpha_range),
        'alpha_threshold': threshold,
        'foreground_rule': 'Alpha > threshold; black foreground, white background',
        'transform': 'left-right mirror: (x,y) -> (width-1-x,y); no crop, resize or redraw',
        'pixelwise_horizontal_mirror_verified': True,
        'outputs': {
            key: {'path': str(paths[key]), 'sha256': sha256(paths[key])}
            for key in ('front', 'back')
        },
        'helper_sha256': sha256(Path(__file__).resolve()),
        'review_status': 'unreviewed',
        'limitation': 'Reference preparation only; does not validate segmentation, design, topology or occlusion.',
    }
    paths['record'].write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return paths['record']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path, help='Verified transparent front reference')
    parser.add_argument('--output-dir', required=True, type=Path, help='Directory for two masks and provenance')
    parser.add_argument('--alpha-threshold', type=int, default=4)
    args = parser.parse_args()
    try:
        record = prepare(args.source, args.output_dir, args.alpha_threshold)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f'Error: {exc}\n')
    print(record)


if __name__ == '__main__':
    main()
