"""Read-only full-delivery record validation; --files-only is an explicit reduced mode."""
import argparse
import base64
import hashlib
import io
import json
import math
import os
import re
from pathlib import Path
import struct
import sys

ROLES = ("head", "front", "back", "left")


def overview_name(character_name, outfit_name, extension='.jpg'):
    for value in (character_name, outfit_name):
        if not isinstance(value, str) or not value.strip() or re.search(r'[<>:"/\\|?*\x00-\x1f]', value):
            raise ValueError('角色与造型名称须为有效文件名文本')
    if re.fullmatch(r'(套装|造型|款式|outfit|set)[ _-]*([0-9]+|[A-Za-z])', outfit_name, re.I):
        raise ValueError('造型后缀须描述服装内容，不能只用套装编号')
    if extension.lower() not in ('.jpg', '.jpeg', '.png'):
        raise ValueError('总览扩展名须为JPG，或用户另指定的PNG')
    return f'{character_name.strip()}_{outfit_name.strip()}{extension.lower()}'


def within(base, relative):
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("路径必须为非空相对路径")
    part = Path(relative)
    if part.is_absolute():
        raise ValueError("清单图片/文件夹路径必须为相对路径")
    target = (base / part).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        raise ValueError("路径超出指定交付目录")
    return target


def check_delivery_location(manifest, root, files_only=False):
    """Check the actual destination, not a workspace copy labelled as delivered."""
    if files_only:
        return None
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("交付根目录不存在")
    override = manifest.get('delivery_directory_override_reason', '')
    if not isinstance(override, str):
        raise ValueError("delivery_directory_override_reason须为用户要求的文本")
    if override.strip():
        return {'root': str(root), 'mode': 'user_override', 'reason': override.strip()}
    material = manifest.get('material_directory')
    if not isinstance(material, str) or not material.strip() or not Path(material).is_absolute():
        raise ValueError("完整交付须登记素材目录的实际绝对路径material_directory")
    material = Path(material).resolve()
    if not material.is_dir():
        raise ValueError("登记的素材目录不存在")
    try:
        relative = root.relative_to(material)
    except ValueError:
        raise ValueError("最终交付目录不在素材目录内；工作区暂存检查请用--files-only")
    if not relative.parts:
        raise ValueError("须在素材目录内新建交付子目录，保留原始素材")
    return {'root': str(root), 'mode': 'material_directory', 'material_directory': str(material)}


def check_description(folder, outfit, required=True):
    if not required and not outfit.get('description_file'):
        return None
    description = within(folder, outfit.get('description_file', '角色描述.md'))
    if not description.is_file():
        raise ValueError("角色描述文件不存在；每套默认须交付角色描述.md")
    if description.suffix.lower() != '.md':
        raise ValueError("角色描述须为Markdown文档")
    if not description.read_text(encoding='utf-8-sig').strip():
        raise ValueError("角色描述文件为空")
    return {'file': str(description), 'sha256': hashlib.sha256(description.read_bytes()).hexdigest()}


def ratio(value):
    try:
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError
        w, h = map(int, parts)
        if w <= 0 or h <= 0:
            raise ValueError
        return w, h
    except (AttributeError, TypeError, ValueError):
        raise ValueError("比例应为正整数形式，例如3:4")


def png_size(path):
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError("不是具有IHDR的PNG文件")
    w, h = struct.unpack(">II", header[16:24])
    if not w or not h:
        raise ValueError("无效PNG尺寸")
    return w, h


def canvas_image_data(folder, entry):
    script_path = entry.get('dataScript', '')
    if not re.fullmatch(r'(网页资源|制作记录)/画布数据/\d{6}_(特写|正面|左侧面|背面)(?:_历史导入_[a-f0-9]{8})?\.png\.js', script_path):
        raise ValueError('候选加载数据路径无效')
    script = within(folder, script_path).read_text(encoding='utf-8-sig').strip()
    prefix, suffix = 'window.dispatchEvent(new CustomEvent("character-image",{detail:', '}));'
    if not script.startswith(prefix) or not script.endswith(suffix):
        raise ValueError('候选加载脚本格式不符：' + script_path)
    payload = json.loads(script[len(prefix):-len(suffix)])
    if payload.get('id') != entry.get('id') or not str(payload.get('data', '')).startswith('data:image/png;base64,'):
        raise ValueError('候选加载脚本身份或数据格式不符')
    data = base64.b64decode(payload['data'].split(',', 1)[1], validate=True)
    if hashlib.sha256(data).hexdigest() != entry.get('sha256'):
        raise ValueError('候选加载数据与账本哈希不符')
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'PNG':
                raise ValueError('不是PNG编码')
            size = image.size
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except (OSError, ValueError, SyntaxError) as exc:
        raise ValueError('候选PNG无法完整解码：' + script_path + '：' + str(exc)) from exc
    if list(size) != [entry.get('width'), entry.get('height')]:
        raise ValueError('候选加载数据尺寸与账本不符')
    return data


def check_editor(folder, files):
    """Check released bytes and local image data; never approves image quality."""
    release = json.loads(within(folder, '制作记录/网页释放.json').read_text(encoding='utf-8-sig'))
    required = {'编辑画布.html', '网页资源/后端服务.cmd', '使用说明.md', '网页资源/release.json',
                '网页资源/hub.mjs', '网页资源/launch.ps1', '网页资源/native-runtime.mjs',
                '网页资源/native-runtime-manifest.json', '网页资源/native-worker.mjs',
                '网页资源/native-matting.mjs', '网页资源/shared-sampling.mjs',
                '网页资源/shared-worker.mjs', '网页资源/shared-matting.mjs'}
    entries = release.get('files', [])
    if (release.get('schemaVersion') != 1 or len(entries) != len(required)
            or {e.get('path') for e in entries} != required):
        raise ValueError('网页释放记录缺失或文件集合不完整')
    for entry in entries:
        data = within(folder, entry['path']).read_bytes()
        if len(data) != entry.get('size') or hashlib.sha256(data).hexdigest() != entry.get('sha256'):
            raise ValueError('网页资源变更或缺失：' + entry['path'])
    bundle = json.loads(within(folder, '网页资源/release.json').read_text(encoding='utf-8-sig'))
    if bundle.get('uiRevision') != release.get('uiRevision'):
        raise ValueError('网页版本与释放记录不符')
    for entry in bundle.get('files', []):
        data = within(folder, entry['path']).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry.get('sha256'):
            raise ValueError('网页文件不是所记录的可释放模板')
    manifest = json.loads(within(folder, '网页资源/画布清单.json').read_text(encoding='utf-8-sig'))
    script = within(folder, '网页资源/交付索引.js').read_text(encoding='utf-8-sig').strip()
    prefix = 'window.CHARACTER_DELIVERY='
    if not script.startswith(prefix) or not script.endswith(';'):
        raise ValueError('交付索引格式不符')
    index = json.loads(script[len(prefix):-1])
    if (index.get('candidates') != manifest.get('candidates')
            or [{k: v for k, v in c.items() if k != 'data'} for c in index.get('current', [])]
            != manifest.get('current')):
        raise ValueError('本地网页索引与候选账本不一致')
    role_map = {'head': 'head', 'front': 'front', 'left': 'side', 'back': 'back'}
    current = {c['view']: c for c in index.get('current', [])}
    if len(index.get('current', [])) != 4 or set(current) != set(role_map.values()):
        raise ValueError('网页索引缺少当前四视图')
    for role, web_role in role_map.items():
        entry = current[web_role]
        data = within(folder, files[role]).read_bytes()
        if entry.get('path') != files[role] or entry.get('sha256') != hashlib.sha256(data).hexdigest():
            raise ValueError('网页当前图不是已声明的交付版本：' + role)
        if entry.get('data') is not None:
            if entry['data'] != 'data:image/png;base64,' + base64.b64encode(data).decode('ascii'):
                raise ValueError('旧网页内嵌当前图与PNG不一致：' + role)
        elif canvas_image_data(folder, entry) != data:
            raise ValueError('网页当前图与共用加载数据不一致：' + role)
    candidates = manifest.get('candidates', [])
    archived_paths = set()
    for entry in candidates:
        if not str(entry.get('path', '')).startswith('候选/') or entry.get('view') not in role_map.values():
            raise ValueError('候选账本路径或视图无效')
        data = canvas_image_data(folder, entry)
        png = within(folder, entry['path'])
        if png.exists() and png.read_bytes() != data:
            raise ValueError('单独候选PNG与加载数据不符：' + entry['path'])
        archived_paths.add(entry['path'])
    candidate_folder = within(folder, '候选')
    disk_paths = {'候选/' + p.name for p in candidate_folder.iterdir()
                  if p.is_file() and re.fullmatch(r'\d{6}_(特写|正面|左侧面|背面)(?:_历史导入_[a-f0-9]{8})?\.png', p.name)} if candidate_folder.exists() else set()
    if not disk_paths.issubset(archived_paths) or (manifest.get('candidatePngs') is True and disk_paths != archived_paths):
        raise ValueError('候选目录存在未登记或遗漏图片')
    if manifest.get('candidatePngs') is False and disk_paths:
        raise ValueError('本次不交付独立候选PNG，请备份移出旧候选目录，保留全部画布数据')
    candidate_ids = {c['id'] for c in candidates}
    if any(entry.get('id') not in candidate_ids for entry in current.values()):
        raise ValueError('当前四图缺少对应候选记录')
    return {'folder': str(folder), 'ui_revision': release.get('uiRevision'), 'candidate_count': len(candidates)}


def check(manifest, root, force_preview=False, files_only=False):
    from PIL import Image, ImageChops
    from overview_layout import metrics as layout_metrics, plan as plan_layout, settings_from_layout, overlap_within_limit, local_rows, placed_rows, separated_x
    if not isinstance(manifest, dict):
        raise ValueError("清单必须为JSON对象")
    outfits = manifest.get("outfits")
    if not isinstance(outfits, list) or not outfits:
        raise ValueError("outfits必须为非空数组")
    head_ratio = ratio(manifest.get("head_ratio", "3:4"))
    body_ratio = ratio(manifest.get("body_ratio", "9:16"))
    if type(manifest.get("require_preview", False)) is not bool:
        raise ValueError("require_preview须为布尔值")
    require_preview = not files_only or force_preview or manifest.get("require_preview", False)
    if type(manifest.get("require_visual_review", False)) is not bool:
        raise ValueError("require_visual_review须为布尔值")
    if type(manifest.get('require_editor', not files_only)) is not bool:
        raise ValueError('require_editor须为布尔值')
    require_editor = manifest.get('require_editor', not files_only)
    require_review = not files_only
    require_description = manifest.get('require_description', not files_only)
    if type(require_description) is not bool:
        raise ValueError('require_description须为布尔值')
    if not files_only and not require_description:
        reason = manifest.get('description_omission_reason')
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('省略角色描述须记录用户明确要求description_omission_reason')
    errors, checked, descriptions, seen_paths, seen_folders = [], [], [], set(), set()
    preview_tasks, previews, folders, editors = [], [], [], []
    delivery_location = None
    try:
        delivery_location = check_delivery_location(manifest, root, files_only)
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f'delivery_location：{exc}')
    for index, outfit in enumerate(outfits, 1):
        if not isinstance(outfit, dict):
            errors.append(f"套图{index}不是对象")
            continue
        name = outfit.get("name") or f"套图{index}"
        if not isinstance(outfit.get('background_modes', {}), dict):
            errors.append(f'{name}：background_modes须为对象')
            continue
        files = outfit.get("files")
        if not isinstance(files, dict) or set(files) != set(ROLES):
            errors.append(f"{name}：files必须且只能有head/front/back/left四项")
            continue
        try:
            folder = within(root, outfit.get("folder"))
            folder_key = os.path.normcase(str(folder))
            if folder_key in seen_folders:
                raise ValueError("不同套图不能共用一个交付文件夹")
            seen_folders.add(folder_key)
            if not folder.is_dir():
                raise ValueError("套图文件夹不存在")
            folders.append(folder)
        except ValueError as exc:
            errors.append(f"{name}：{exc}")
            continue
        try:
            description = check_description(folder, outfit, require_description)
            if description is not None:
                descriptions.append({'outfit': name, **description})
        except (OSError, ValueError, TypeError, ImportError) as exc:
            errors.append(f"{name}/description：{exc}")
        declared_preview = within(root, outfit['preview_file']) if outfit.get('preview_file') else None
        pngs = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == '.png' and p.resolve() != declared_preview]
        if len(pngs) != 4:
            errors.append(f"{name}：目录外层除声明总览外有{len(pngs)}张PNG，要求四张当前图")
        expected = set()
        for role in ROLES:
            try:
                path = within(folder, files[role])
                key = os.path.normcase(str(path))
                if key in seen_paths:
                    raise ValueError("同一图片被多角色或多套图重复引用")
                seen_paths.add(key)
                expected.add(key)
                if not path.is_file():
                    raise ValueError("图片不存在")
                if path.suffix.lower() != ".png":
                    raise ValueError("图片须为PNG")
                w, h = png_size(path)
                rw, rh = head_ratio if role == "head" else body_ratio
                # Allow at most one pixel of integer-dimension rounding.
                if abs(w * rh - h * rw) > max(rw, rh):
                    raise ValueError(f"{w}×{h}不符合{rw}:{rh}（允许1像素取整误差）")
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                from PIL import Image
                with Image.open(path) as image:
                    low, high = image.convert('RGBA').getchannel('A').getextrema()
                if high == 0:
                    raise ValueError('图片为空透明画布')
                actual_background = 'transparent' if low < 255 else 'white'
                declared = outfit.get('background_modes', {}).get(role, outfit.get('background_mode', 'auto'))
                if declared not in ('auto', 'transparent', 'white') or declared not in ('auto', actual_background):
                    raise ValueError('实际Alpha与声明背景版本不符')
                if actual_background == 'transparent' and low != 0:
                    raise ValueError('透明背景没有真正全透明空白')
                checked.append({"outfit": name, "role": role, "file": str(path), "width": w, "height": h,
                                "sha256": digest, "background_mode": actual_background})
            except (OSError, ValueError, TypeError, ImportError) as exc:
                errors.append(f"{name}/{role}：{exc}")
        actual = {os.path.normcase(str(p.resolve())) for p in pngs}
        if actual != expected:
            errors.append(f"{name}：文件夹PNG与四角色清单不一致")
        if require_editor:
            try:
                editors.append(check_editor(folder, files))
            except (OSError, ValueError, TypeError, KeyError) as exc:
                errors.append(f'{name}/editor：{exc}')
        if require_preview or outfit.get("preview_file") is not None:
            preview_tasks.append((name, outfit, folder))
    seen_previews = set()
    for name, outfit, folder in preview_tasks:
        try:
            path = within(root, outfit.get("preview_file"))
            if any(path.is_relative_to(other) and other != folder for other in folders):
                raise ValueError('总览不能放在其他套图目录中')
            if path in {within(folder, p) for p in outfit['files'].values()}:
                raise ValueError('总览不能覆盖当前单图')
            key = os.path.normcase(str(path))
            if key in seen_previews:
                raise ValueError("不同套图不能共用同一预览")
            seen_previews.add(key)
            if not path.is_file() or path.suffix.lower() not in ('.jpg', '.jpeg', '.png'):
                raise ValueError('总览JPG不存在，或不是支持的图片格式')
            if path.name != overview_name(manifest.get('character_name'), outfit.get('name'), path.suffix):
                raise ValueError('总览文件名应为角色名称_描述性造型后缀.jpg')
            with Image.open(path) as image:
                w, h = image.size
                if image.format != ('PNG' if path.suffix.lower() == '.png' else 'JPEG'):
                    raise ValueError('总览扩展名与实际图片编码不符')
            if (w, h) != (2560, 1440):
                raise ValueError('四视图画布必须为2560×1440')
            preview_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            sidecar = within(root, outfit["preview_record_file"]) if outfit.get("preview_record_file") else within(path.parent, "制作记录/" + path.name + ".json")
            record = json.loads(sidecar.read_text(encoding="utf-8-sig"))
            order = ["head", "front", "left", "back"]
            if not isinstance(record, dict) or record.get("kind") != "preview" or record.get("display_order") != order:
                raise ValueError("预览来源记录缺失或顺序不是特写—正面—左侧面—背面")
            if record.get("output_sha256") != preview_hash or record.get("canvas") != [w, h]:
                raise ValueError("预览文件与旁录不一致，需重新制作")
            if record.get('text_rendered') is not False or record.get('paint_order') != ['front', 'left', 'back', 'head']:
                raise ValueError('总览须无文字、特写图层最高')
            with Image.open(path) as pixels:
                if pixels.convert('RGBA').getchannel('A').getextrema() != (255, 255):
                    raise ValueError('总览必须为不透明白画布')
            inputs = record.get("inputs")
            if not isinstance(inputs, list) or len(inputs) != 4 or any(not isinstance(item, dict) for item in inputs):
                raise ValueError("预览旁录需有四张实际来源")
            if [item.get("role") for item in inputs] != order:
                raise ValueError("预览来源角色顺序不正确")
            layout_settings = outfit.get('layout', {})
            if not isinstance(layout_settings, dict):
                raise ValueError('layout须为对象')
            minimum = layout_settings.get('head_min_width', math.ceil(w / 3))
            if type(minimum) is not int or not 400 <= minimum <= w:
                raise ValueError('特写最小显示宽度无效')
            if minimum < math.ceil(w / 3) and not str(layout_settings.get('head_min_width_reason', '')).strip():
                raise ValueError('特写低于三分之一须记录用户另行指定的理由')
            right_margin = layout_settings.get('right_margin', 0)
            if type(right_margin) is not int or not 0 <= right_margin < w:
                raise ValueError('right_margin须为画布宽度内的非负整数')
            if right_margin and not str(layout_settings.get('right_margin_reason', '')).strip():
                raise ValueError('右侧留白须记录用户另行指定的理由')
            actual_images, actual_positions, actual_sizes = {}, {}, {}
            for item in inputs:
                expected_path = within(folder, outfit["files"][item["role"]])
                recorded_path = within(root, item.get("file"))
                if recorded_path != expected_path or item.get("sha256") != hashlib.sha256(expected_path.read_bytes()).hexdigest():
                    raise ValueError(f"预览来源{item['role']}已变化或不符，需重新制作")
                pos, size = item.get('position'), item.get('display_size')
                if (not isinstance(pos, list) or not isinstance(size, list) or len(pos) != 2 or len(size) != 2
                        or any(type(v) is not int for v in pos + size) or min(pos) < 0 or min(size) <= 0
                        or (item['role'] != 'back' and pos[0] + size[0] > w) or pos[1] + size[1] > h):
                    raise ValueError('总览有图层超出画布')
                if item['role'] == 'head' and (pos[0] != 0 or pos[1] + size[1] != h or item.get('layer') != 3):
                    raise ValueError('特写须贴左下角且为最高图层')
                if item['role'] == 'head' and size[0] < minimum:
                    raise ValueError(f'特写显示宽度{size[0]}低于最低{minimum}，不能为避让而缩成小头像')
                with Image.open(expected_path) as source:
                    rgba = source.convert('RGBA')
                crop = item.get('crop')
                if (not isinstance(crop, list) or len(crop) != 4 or any(type(v) is not int for v in crop)
                        or not 0 <= crop[0] < crop[2] <= rgba.width
                        or not 0 <= crop[1] < crop[3] <= rgba.height):
                    raise ValueError('预览来源主体框无效')
                if rgba.getchannel('A').getextrema()[0] < 255:
                    visible = rgba.getchannel('A').point(lambda value: 255 if value > 4 else 0).getbbox()
                    if visible and (crop[0] > visible[0] or crop[1] > visible[1]
                                    or crop[2] < visible[2] or crop[3] < visible[3]):
                        raise ValueError('裁切框丢失可见主体，不能裁发尾或裙摆来消除交叠')
                pixels = rgba.crop(crop)
                if abs(size[0] * pixels.height - size[1] * pixels.width) > max(pixels.width, pixels.height):
                    raise ValueError('预览来源被非等比拉伸')
                actual_images[item['role']] = pixels
                actual_positions[item['role']] = tuple(pos)
                actual_sizes[item['role']] = tuple(size)
            body_centers = [actual_positions[role][0] + actual_sizes[role][0] / 2
                            for role in ('front', 'left', 'back')]
            if not body_centers[0] < body_centers[1] < body_centers[2]:
                raise ValueError('实际三视图位置须依次为正面—左侧面—背面')
            geometry = layout_metrics(actual_images, actual_positions, actual_sizes, (w, h))
            if geometry['head_visible_width'] < minimum:
                raise ValueError('特写实际可见宽度不足，不能用透明边框凑占比')
            if any(geometry['clipped_visible_pixels'].values()):
                raise ValueError('总览裁掉可见主体，贴边不能裁发尾、尾翼或裙摆')
            if geometry['right_margin'] != right_margin:
                raise ValueError(f"背面可见轮廓右留白{geometry['right_margin']}像素，应为{right_margin}；须按主体贴边，不按透明空白框")
            settings = settings_from_layout(layout_settings, (w, h))
            body_heights = [actual_sizes[r][1] for r in ('front', 'left', 'back')]
            body_tops = [actual_positions[r][1] for r in ('front', 'left', 'back')]
            body_bottoms = [actual_positions[r][1] + actual_sizes[r][1] for r in ('front', 'left', 'back')]
            if len(set(body_heights)) != 1:
                raise ValueError('三视图须同高，不能分别缩小来腾空间')
            if max(body_tops) - min(body_tops) > settings['max_vertical_offset'] or max(body_bottoms) - min(body_bottoms) > settings['max_vertical_offset']:
                raise ValueError('三视图上下错位超过许可；默认共用上下基准')
            for pair, bound in settings['edge_overlap_limits'].items():
                first, second = pair.split(':')
                rows = {r: placed_rows(local_rows(actual_images[r], actual_sizes[r]), actual_positions[r][1], h)
                        for r in (first, second)}
                separation = separated_x(rows[first], rows[second], 0)
                if actual_positions[second][0] - actual_positions[first][0] < separation - bound:
                    raise ValueError(f'{pair}超过实际局部检查后设置的外缘搜索限制')
            if not overlap_within_limit(geometry, actual_images):
                raise ValueError('交叠超过辅助上限或白底矩形覆盖其他视图')
            if settings['maximize_subjects']:
                _, larger_sizes, optimization = plan_layout(actual_images, settings, (w, h))
                current_height = min(actual_sizes[r][1] for r in ('front', 'left', 'back'))
                if larger_sizes['front'][1] >= current_height + 10:
                    raise ValueError(f"三视图仍可从{current_height}放大至{larger_sizes['front'][1]}像素；不能仅满足放入画布就交付")
                if larger_sizes['front'][1] == current_height and larger_sizes['head'][0] >= actual_sizes['head'][0] + 10:
                    raise ValueError('特写仍可放大，应继续利用可接受的横向空间')
            overlap_reviews = record.get('overlap_review', {})
            if not isinstance(overlap_reviews, dict):
                raise ValueError('overlap_review须为对象')
            for pair, overlap in geometry['pair_intersections'].items():
                if not overlap['pixels']:
                    continue
                first, second = pair.split(':')
                limit = min(geometry['visible_areas'][first], geometry['visible_areas'][second]) * .005
                if overlap['pixels'] > limit:
                    raise ValueError(f'{pair}可见主体交叠过大，发尾、衣料与裙摆也需完整避让')
                review = overlap_reviews.get(pair, {})
                if (not isinstance(review, dict) or review.get('judgment') != 'minor_edges_only'
                        or review.get('viewed') is not True or review.get('preview_sha256') != preview_hash
                        or not isinstance(review.get('observation'), str) or not review['observation'].strip()):
                    raise ValueError(f'{pair}有可见交叠，需实际局部检查，不能只保护脸和手脚')
                evidence = within(root, review.get('evidence_file'))
                if not evidence.is_file() or review.get('evidence_sha256') != hashlib.sha256(evidence.read_bytes()).hexdigest():
                    raise ValueError('微小外缘交叠的当前局部检查证据缺失或已变化')
                with Image.open(evidence) as local:
                    local.verify()
            reconstructed = Image.new('RGBA', (w, h), 'white')
            for role in ('front', 'left', 'back', 'head'):
                reconstructed.alpha_composite(actual_images[role].resize(actual_sizes[role], Image.Resampling.LANCZOS),
                                               actual_positions[role])
            expected_pixels = reconstructed.convert('RGB')
            if path.suffix.lower() in ('.jpg', '.jpeg'):
                export = record.get('export', {})
                if export.get('format') != 'JPEG' or export.get('quality') != 99 or export.get('chroma_subsampling') != '4:4:4':
                    raise ValueError('总览旁录须声明JPEG质量99及4:4:4采样')
                if export.get('rgb_sha256') and export['rgb_sha256'] != hashlib.sha256(expected_pixels.tobytes()).hexdigest():
                    raise ValueError('JPEG编码前画布与所记录的源图和位置不符')
                encoded = io.BytesIO()
                expected_pixels.save(encoded, format='JPEG', quality=99, subsampling=0, optimize=True)
                encoded.seek(0)
                with Image.open(encoded) as expected_jpeg:
                    tables = expected_jpeg.quantization
                    expected_pixels = expected_jpeg.convert('RGB')
                from PIL.JpegImagePlugin import get_sampling
                with Image.open(path) as actual_jpeg:
                    if actual_jpeg.quantization != tables or get_sampling(actual_jpeg) != 0:
                        raise ValueError('实际总览JPEG不是质量99、4:4:4编码')
            with Image.open(path) as actual_preview:
                if ImageChops.difference(expected_pixels, actual_preview.convert('RGB')).getbbox():
                    raise ValueError('实际总览与所记录的源图、缩放及位置不符，需重新制作')
            previews.append({"outfit": name, "file": str(path), "width": w, "height": h,
                             "sha256": preview_hash, "source_record": str(sidecar), "layout_metrics": geometry})
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append(f"{name}/preview：{exc}")
    transparent_images = [item for item in checked if item['background_mode'] == 'transparent']
    for outfit in outfits:
        if isinstance(outfit, dict) and (outfit.get('transparent_files') or outfit.get('white_export_records')):
            errors.append(f"{outfit.get('name', '套图')}：新交付不同时提交同图两种背景版本；临时转换放制作记录")
    review_errors, reviews = [], []
    if require_review:
        from review_gate import check as check_reviews, read as read_reviews
        for outfit in outfits:
            try:
                if not isinstance(outfit, dict):
                    raise ValueError("套图不是对象")
                review_path = within(root, outfit.get("review_file"))
                review_record = read_reviews(review_path)
                task_mode = outfit.get('task_mode', 'production')
                if task_mode not in ('production', 'existing_review'):
                    raise ValueError('task_mode须为production或existing_review；仅生成/未验收打包用--files-only')
                result = check_reviews(review_record, root, expected_task_mode=task_mode)
                if not result["recorded_approval_valid"]:
                    raise ValueError("；".join(result["errors"]))
                reviewed = {entry["role"]: entry for entry in result["checked"]}
                folder = within(root, outfit.get("folder"))
                for role in ROLES:
                    final = within(folder, outfit["files"][role])
                    if reviewed[role]["sha256"] != hashlib.sha256(final.read_bytes()).hexdigest():
                        raise ValueError(f"{role}交付文件不是已验收版本")
                reviews.append({"file": str(review_path), "result": result})
            except (OSError, ValueError, TypeError, KeyError) as exc:
                review_errors.append(f"{outfit.get('name','套图') if isinstance(outfit,dict) else '套图'}/visual_record：{exc}")
    return {"scope": "files_and_record_integrity", "model_visual_checks": False,
            "check_mode": "files_only" if files_only else "full_delivery",
            "delivery_location": delivery_location,
            "recorded_delivery_valid": not files_only and not errors and not review_errors,
            "file_checks_passed": not errors,
            "review_records_passed": not review_errors if require_review else None,
            "outfit_count": len(outfits), "png_count": len(checked), "description_count": len(descriptions),
            "preview_count": len(previews), "errors": errors, "review_errors": review_errors,
            "transparent_count":len(transparent_images), "transparent_images":transparent_images,
            "editor_count": len(editors), "editors": editors,
            "images": checked, "descriptions": descriptions, "previews": previews, "reviews": reviews}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="JSON delivery manifest")
    parser.add_argument("--root", type=Path, help="Delivery root; defaults to manifest parent")
    parser.add_argument("--require-preview", action="store_true", help="Require current four-view previews and source records")
    parser.add_argument("--files-only", action="store_true", help="Explicitly inspect files only; never approves a complete delivery")
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        result = check(manifest, (args.root or args.manifest.parent).resolve(), args.require_preview, args.files_only)
    except (OSError, ValueError) as exc:
        print(json.dumps({"scope": "files_and_record_integrity", "model_visual_checks": False,
                          "check_mode": "files_only" if args.files_only else "full_delivery",
                          "recorded_delivery_valid": False,
                          "file_checks_passed": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["file_checks_passed"] and result["review_records_passed"] is not False else 1


if __name__ == "__main__":
    sys.exit(main())
