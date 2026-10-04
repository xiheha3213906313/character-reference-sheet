"""Export a reviewed existing-alpha PNG onto white; no masking or subject retouching."""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export(source, output):
    from PIL import Image, ImageChops
    source, output = source.resolve(), output.resolve()
    sidecar = output.with_suffix(output.suffix + '.json')
    if source == output or output.exists() or sidecar.exists():
        raise ValueError('使用新输出路径；禁止覆盖原RGBA、已有图片或来源记录')
    if output.suffix.lower() != '.png':
        raise ValueError('输出必须为PNG')
    with Image.open(source) as image:
        if image.format != 'PNG' or not ('A' in image.getbands() or 'transparency' in image.info):
            raise ValueError('输入需为具有已检查Alpha的透明PNG，本脚本不估计或制造蒙版')
        rgba = image.convert('RGBA')
    alpha = rgba.getchannel('A')
    low, high = alpha.getextrema()
    if low != 0 or high == 0:
        raise ValueError('输入必须含真实全透明背景及非空主体；全不透明或全空图不适用')
    white = Image.new('RGBA', rgba.size, (255,255,255,255))
    result = Image.alpha_composite(white, rgba).convert('RGB')
    opaque = alpha.point(lambda value: 255 if value == 255 else 0)
    difference = ImageChops.difference(rgba.convert('RGB'), result)
    unchanged = Image.composite(difference, Image.new('RGB',rgba.size), opaque).getbbox() is None
    if not unchanged:
        raise ValueError('完全不透明主体RGB发生变化，停止导出')
    histogram = alpha.histogram()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation keeps already completed images intact, including a concurrent writer.
    with output.open('xb') as stream:
        result.save(stream, format='PNG')
    record = {
        'operation':'alpha_composite_export_on_white', 'semantic_visual_review':False,
        'source':str(source), 'source_sha256':digest(source),
        'output':str(output), 'output_sha256':digest(output), 'size':list(rgba.size),
        'background_rgb':[255,255,255], 'output_mode':'RGB',
        'fully_transparent_pixels':histogram[0], 'fully_opaque_pixels':histogram[255],
        'fully_opaque_subject_rgb_unchanged':unchanged,
        'uses_existing_alpha_only':True, 'resized':False,
    }
    with sidecar.open('x', encoding='utf-8') as stream:
        json.dump(record,stream,ensure_ascii=False,indent=2)
    return record


def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    try:
        record=export(args.source,args.output)
    except (OSError,ValueError,ImportError) as exc:
        print(json.dumps({'export_completed':False,'error':str(exc)},ensure_ascii=False))
        return 1
    print(json.dumps({'export_completed':True,**record},ensure_ascii=False,indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
