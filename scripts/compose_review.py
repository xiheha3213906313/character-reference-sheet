"""Compose evidence comparisons or head/front/left/back previews; requires Pillow."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys

from check_delivery import ROLES, within, overview_name

DISPLAY_ORDER = ("head", "front", "left", "back")
LABELS = {"head": "头部特写", "front": "正面", "left": "左侧面", "back": "背面"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def find_font(explicit):
    if explicit:
        path = Path(explicit).expanduser().resolve()
        if not path.is_file():
            raise ValueError("指定字体文件不存在")
        return path
    windows = Path(os.environ.get("WINDIR") or os.environ.get("SystemRoot") or "/nonexistent") / "Fonts"
    candidates = [windows / "msyh.ttc", windows / "simhei.ttf",
                  Path("/System/Library/Fonts/PingFang.ttc"),
                  Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
                  Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc")]
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise ValueError("未找到中文字体；请用--font指定本环境的字体文件")


def load_panel(path, crop=None):
    from PIL import Image
    path = path.resolve()
    with Image.open(path) as source:
        source.load()
        dimensions = list(source.size)
        if crop is None:
            crop = [0, 0, *dimensions]
        if (not isinstance(crop, list) or len(crop) != 4 or
                any(type(v) is not int for v in crop)):
            raise ValueError("crop必须是四个整数[x0,y0,x1,y1]")
        x0, y0, x1, y1 = crop
        if not (0 <= x0 < x1 <= dimensions[0] and 0 <= y0 < y1 <= dimensions[1]):
            raise ValueError(f"裁切框超出原图：{path.name}")
        rgba = source.crop(crop).convert("RGBA")
        result = Image.new("RGB", rgba.size, "white")
        result.paste(rgba, mask=rgba.getchannel("A"))
    return result, {"file": str(path), "sha256": digest(path), "dimensions": dimensions, "crop": crop}


def fit_font(draw, text, font_path, size, width):
    from PIL import ImageFont
    while size >= 12:
        font = ImageFont.truetype(str(font_path), size)
        if draw.textbbox((0, 0), text, font=font)[2] <= width:
            return font
        size -= 1
    raise ValueError("文字过长；请缩短标题/标签或增加栏宽")


def save(canvas, output, record, sources):
    output = output.resolve()
    if output.suffix.lower() != ".png":
        raise ValueError("输出必须使用.png扩展名")
    if output in {p.resolve() for p in sources}:
        raise ValueError("输出不能覆盖输入图片")
    sidecar = output.with_suffix(output.suffix + ".json")
    if sidecar in {p.resolve() for p in sources}:
        raise ValueError("记录不能覆盖输入文件")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="PNG")
    record.update({"output_file": output.name, "output_sha256": digest(output), "canvas": list(canvas.size)})
    sidecar.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"image": str(output), "record": str(sidecar)}


def compare(args, font_path):
    from PIL import Image, ImageDraw, ImageFont
    spec = read_json(args.spec)
    if not isinstance(spec, dict) or not isinstance(spec.get("rows"), list) or not spec["rows"]:
        raise ValueError("对照配置需包含非空rows数组")
    width = spec.get("column_width", 600)
    if type(width) is not int or not 200 <= width <= 4000:
        raise ValueError("column_width须为200至4000的整数")
    margin, gap, row_gap = 8, 8, 12
    prepared, sources, records = [], [], []
    for row in spec["rows"]:
        if not isinstance(row, dict) or not isinstance(row.get("panels"), list) or not row["panels"]:
            raise ValueError("每行需包含非空panels数组")
        panels = []
        for panel in row["panels"]:
            if not isinstance(panel, dict) or not isinstance(panel.get("image"), str):
                raise ValueError("每栏必须有image路径")
            path = (args.spec.parent / panel["image"]).resolve()
            pixels, info = load_panel(path, panel.get("crop"))
            scale = panel.get("scale", 1)
            if type(scale) not in (int, float) or not math.isfinite(scale) or not 0 < scale <= 1:
                raise ValueError("scale须大于0且不超过1")
            anchor = panel.get("anchor_y")
            if anchor is not None and (type(anchor) not in (int, float) or not math.isfinite(anchor) or
                    not info["crop"][1] <= anchor <= info["crop"][3]):
                raise ValueError("anchor_y必须在原图裁切范围内")
            panels.append({"pixels": pixels, "info": info, "scale": scale, "anchor": anchor,
                           "label": str(panel.get("label") or "")})
            sources.append(path)
        if any(p["anchor"] is not None for p in panels) and not all(p["anchor"] is not None for p in panels):
            raise ValueError("同一行anchor_y需全部填写或全部省略")
        # Fit the entire row with one shared multiplier, preserving chosen relative scales.
        common = min(1, width / max(p["pixels"].width * p["scale"] for p in panels))
        for panel in panels:
            scale = panel["scale"] * common
            original = panel["pixels"].size
            size = tuple(max(1, round(v * scale)) for v in original)
            if size != original:
                panel["pixels"] = panel["pixels"].resize(size, Image.Resampling.LANCZOS)
            panel["info"].update({"label": panel["label"], "requested_scale": panel["scale"],
                                  "actual_scale_xy": [size[0] / original[0], size[1] / original[1]],
                                  "display_size": list(size), "anchor_y": panel["anchor"]})
            panel["anchor_display"] = (panel["anchor"] - panel["info"]["crop"][1]) * size[1] / original[1] if panel["anchor"] is not None else 0
        baseline = round(max(p["anchor_display"] for p in panels))
        for panel in panels:
            panel["top"] = round(baseline - panel["anchor_display"])
        height = max(p["top"] + p["pixels"].height for p in panels)
        guides = row.get("guides", [])
        if not isinstance(guides, list):
            raise ValueError("guides须为数组")
        for guide in guides:
            if (not isinstance(guide, dict) or type(guide.get("offset")) not in (int, float) or
                    not math.isfinite(guide["offset"]) or not 0 <= baseline + guide["offset"] < height):
                raise ValueError("辅助线offset须在对齐后的图像区域内")
        label_height = 24 if any(p["label"] for p in panels) else 0
        prepared.append((row, panels, baseline, height, label_height))
        records.append({"title": row.get("title", ""), "common_fit_scale": common,
                        "alignment_y_in_row": baseline, "guides": guides, "panels": [p["info"] for p in panels]})
    # Explanations/titles stay in the sidecar. Only explicit source labels and guides are drawn.
    label_font = ImageFont.truetype(str(font_path), 16)
    guide_font = ImageFont.truetype(str(font_path), 12)
    measuring = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    count = max(len(panels) for _, panels, _, _, _ in prepared)
    column_widths = [0] * count
    for record, (row, panels, baseline, height, label_height) in zip(records, prepared):
        guide_gutter = max((math.ceil(measuring.textlength(str(g["label"]), font=guide_font)) + 6
                            for g in row.get("guides", []) if g.get("label")), default=0)
        record.update({"guide_gutter": guide_gutter, "label_height": label_height})
        for index, panel in enumerate(panels):
            label_width = math.ceil(measuring.textlength(panel["label"], font=label_font))
            slot = max(panel["pixels"].width + guide_gutter, min(width, label_width))
            column_widths[index] = max(column_widths[index], slot)
    canvas_width = margin * 2 + sum(column_widths) + (count - 1) * gap
    canvas_height = margin * 2 + sum(label_height + h for _, _, _, h, label_height in prepared) + row_gap * (len(prepared) - 1)
    canvas = Image.new("RGB", (canvas_width, canvas_height), "white")
    draw = ImageDraw.Draw(canvas)
    y = margin
    for record, (row, panels, baseline, height, label_height) in zip(records, prepared):
        x = margin
        for index, panel in enumerate(panels):
            slot = column_widths[index]
            gutter = record["guide_gutter"]
            if panel["label"]:
                font = fit_font(draw, panel["label"], font_path, 16, slot)
                draw.text((x, y), panel["label"], font=font, fill="#222222")
            position = (x + gutter + (slot - gutter - panel["pixels"].width) // 2,
                        y + label_height + panel["top"])
            canvas.paste(panel["pixels"], position)
            panel["info"]["position"] = list(position)
            for guide in row.get("guides", []):
                gy = round(y + label_height + baseline + guide["offset"])
                if not position[1] <= gy < position[1] + panel["pixels"].height:
                    continue
                draw.line((position[0], gy, position[0] + panel["pixels"].width - 1, gy), fill="#d53535", width=1)
                if guide.get("label"):
                    guide_label = str(guide["label"])
                    label_w = math.ceil(draw.textlength(guide_label, font=guide_font))
                    draw.text((position[0] - label_w - 6, max(y + label_height, gy - 7)),
                              guide_label, font=guide_font, fill="#b02020")
            x += slot + gap
        y += label_height + height + row_gap
    return [save(canvas, args.output, {"kind": "comparison", "font": str(font_path),
                                     "title": spec.get("title", ""), "titles_rendered": False,
                                     "column_widths": column_widths, "rows": records,
                                     "visual_status": "not_evaluated"}, [*sources, args.spec])]


def preview(args, font_path):
    from PIL import Image, ImageChops
    from overview_layout import plan as plan_layout, metrics as layout_metrics, settings_from_layout, overlap_within_limit
    manifest = read_json(args.manifest)
    root = (args.root or args.manifest.parent).resolve()
    if not isinstance(manifest, dict) or not isinstance(manifest.get("outfits"), list) or not manifest["outfits"]:
        raise ValueError("清单需包含非空outfits数组")
    width, height = 2560, 1440
    plans, used = [], set()
    # Validate all output/input paths before writing any preview.
    folders = [within(root, outfit["folder"]) for outfit in manifest["outfits"]]
    for outfit, folder in zip(manifest["outfits"], folders):
        if not isinstance(outfit.get("files"), dict) or set(outfit["files"]) != set(ROLES):
            raise ValueError("每套files需包含head/front/back/left四项")
        output = within(root, outfit.get("preview_file"))
        if output.name != overview_name(manifest.get('character_name'), outfit.get('name')):
            raise ValueError('总览文件名须为角色名称_描述性造型后缀.png')
        if output.suffix.lower() != ".png" or output in used:
            raise ValueError("每套preview_file须为独立PNG路径")
        if any(output.is_relative_to(other) or output.with_suffix(".png.json").is_relative_to(other) for other in folders):
            raise ValueError("预览及旁录不能放在任一四张成图文件夹中")
        used.add(output)
        inputs = {role: within(folder, outfit["files"][role]) for role in DISPLAY_ORDER}
        plans.append((outfit, output, inputs))
    outputs = []
    for outfit, output, inputs in plans:
        canvas = Image.new("RGBA", (width, height), "white")
        layout = outfit.get('layout', {})
        settings = settings_from_layout(layout, (width, height))
        boxes = layout.get('subject_boxes', {})
        if not isinstance(boxes, dict) or set(boxes) - set(DISPLAY_ORDER):
            raise ValueError('subject_boxes使用head/front/left/back键')
        prepared = {}
        for role in DISPLAY_ORDER:
            with Image.open(inputs[role]) as source:
                rgba = source.convert('RGBA')
            dimensions = list(rgba.size)
            if role in boxes:
                box = boxes[role]
                if (not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box)
                        or not 0 <= box[0] < box[2] <= rgba.width or not 0 <= box[1] < box[3] <= rgba.height):
                    raise ValueError('主体框须为原图范围内的四整数')
                crop_method = 'reviewed_subject_box'
            elif rgba.getchannel('A').getextrema()[0] < 255:
                box = rgba.getchannel('A').point(lambda value: 255 if value > 4 else 0).getbbox()
                crop_method = 'visible_alpha_bounds_threshold_4'
            else:
                # This crops outer blank margins only. It never manufactures alpha or removes interior pixels.
                difference = ImageChops.difference(rgba.convert('RGB'), Image.new('RGB', rgba.size, 'white'))
                channels = difference.split()
                contrast = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
                box = contrast.point(lambda v: 255 if v > 12 else 0).getbbox()
                crop_method = 'outer_near_white_bounds_only'
                if box:
                    box = (max(0, box[0] - 4), max(0, box[1] - 4),
                           min(rgba.width, box[2] + 4), min(rgba.height, box[3] + 4))
            if not box:
                raise ValueError(f'{role}无可排版主体')
            pixels = rgba.crop(box)
            info = {'role': role, 'file': inputs[role].relative_to(root).as_posix(),
                    'sha256': digest(inputs[role]), 'dimensions': dimensions,
                    'crop': list(box), 'crop_method': crop_method}
            prepared[role] = (pixels, info)
        images = {role: prepared[role][0] for role in DISPLAY_ORDER}
        positions, sizes, chosen_layout = plan_layout(images, settings, (width, height))
        geometry = layout_metrics(images, positions, sizes, (width, height))
        if geometry['head_visible_width'] < settings['head_min_width']:
            raise ValueError('特写实际主体显示宽度不足，不能用空白框凑三分之一占比')
        if geometry['right_margin'] != settings['right_margin']:
            raise ValueError('背面可见轮廓未对齐右边缘，透明空白框贴边不能代替主体贴边')
        if any(geometry['clipped_visible_pixels'].values()):
            raise ValueError('贴边排版裁掉可见主体，不能裁发尾、尾翼或裙摆')
        if not overlap_within_limit(geometry, images):
            raise ValueError('交叠超过外缘辅助上限；不能叠压主体或用白底框覆盖其他视图')
        # Bodies first, portrait last: the portrait is always the highest layer.
        for index, role in enumerate(('front', 'left', 'back', 'head')):
            pixels, info = prepared[role]
            size = sizes[role]
            scale = size[1] / pixels.height
            pixels = pixels.resize(size, Image.Resampling.LANCZOS)
            position = positions[role]
            canvas.alpha_composite(pixels, position)
            info.update({'display_size': list(size), 'position': list(position),
                         'scale': scale, 'layer': index})
        info_list = [prepared[r][1] for r in DISPLAY_ORDER]
        record = {"kind": "preview", "outfit": outfit.get("name"), "display_order": list(DISPLAY_ORDER),
                  "background": "#FFFFFF", "text_rendered": False,
                  "paint_order": ['front', 'left', 'back', 'head'],
                  "layout": chosen_layout, "layout_settings": settings, "layout_metrics": geometry, "inputs": info_list,
                  "requires_overlap_review": chosen_layout['requires_overlap_review'],
                  "visual_status": "not_evaluated"}
        outputs.append(save(canvas.convert('RGB'), output, record, [*inputs.values(), args.manifest]))
    return outputs


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    comparison = sub.add_parser("compare", help="Compose full-frame and crop comparison rows")
    comparison.add_argument("spec", type=Path)
    comparison.add_argument("--output", type=Path, required=True)
    comparison.add_argument("--font")
    overview = sub.add_parser("preview", help="Compose each outfit's accepted four PNGs")
    overview.add_argument("manifest", type=Path)
    overview.add_argument("--root", type=Path)
    overview.add_argument("--font")
    args = parser.parse_args()
    try:
        font_path = find_font(args.font) if args.command == 'compare' else None
        outputs = compare(args, font_path) if args.command == "compare" else preview(args, font_path)
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        print(json.dumps({"created": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"created": True, "outputs": outputs, "visual_status": "not_evaluated"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
