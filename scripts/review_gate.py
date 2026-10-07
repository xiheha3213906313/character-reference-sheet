"""Validate recorded visual reviews and fixed versions; does NOT inspect image semantics."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from check_delivery import ROLES, canvas_image_data, png_size, within

GROUPS = {"identity", "design", "look", "native_quality", "reduced_quality", "background"}
DEPENDENCIES = {"head": (), "front": ("head",), "back": ("front",), "left": ("front", "back")}
LOOK_ASPECTS = {"color_style", "lighting", "framing", "head_pose", "gaze", "expression"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON需为对象：{path.name}")
    return value


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}不能为空")


def background_sample_valid(rgb, mode):
    if not isinstance(rgb,list) or len(rgb)!=3 or any(type(v) is not int or not 0<=v<=255 for v in rgb):
        return False
    return rgb==[255,255,255] if mode=='exact_white' else min(rgb)>=245 and max(rgb)-min(rgb)<=8


def selection_image(reference, root, role):
    """Read a retained PNG or its candidate data after delivery packing."""
    if not isinstance(reference, dict):
        raise ValueError('三选一输出引用须为对象')
    expected = reference.get('sha256')
    if reference.get('file'):
        path = within(root, reference['file'])
        png_size(path)
        try:
            from PIL import Image
        except ImportError as exc:
            raise ValueError('三选一候选完整解码需要Pillow；不能只按文件头宣称有效') from exc
        with Image.open(path) as image:
            if image.format != 'PNG':
                raise ValueError('三选一输出须为PNG')
            image.verify()
        with Image.open(path) as image:
            image.load()
        actual = digest(path)
    else:
        manifest_path = within(root, reference.get('canvas_manifest'))
        if manifest_path.name != '画布清单.json' or manifest_path.parent.name != '网页资源':
            raise ValueError('三选一需引用规范的画布清单')
        candidates = read(manifest_path).get('candidates', [])
        matches = [item for item in candidates if item.get('id') == reference.get('candidate_id')]
        if len(matches) != 1 or matches[0].get('view') != {'left': 'side'}.get(role, role):
            raise ValueError('三选一候选不存在、重复或视图不符')
        actual = hashlib.sha256(canvas_image_data(manifest_path.parent.parent, matches[0])).hexdigest()
    if actual != expected:
        raise ValueError('三选一候选图片已变化')
    return actual


def check_selection(selection, root, role, current_hash, goals):
    if selection.get('schema_version') != 1:
        raise ValueError('三选一记录schema_version须为1')
    stages = selection.get('stages')
    if not isinstance(stages, dict):
        raise ValueError('三选一记录stages须为对象')
    data = stages.get(role)
    if not isinstance(data, dict):
        raise ValueError(f'{role}缺少三选一记录')
    recipe = data.get('recipe')
    if not isinstance(recipe, dict):
        raise ValueError(f'{role}缺少共同调用配置')
    prompt = within(root, recipe.get('prompt_file'))
    if not prompt.read_text(encoding='utf-8-sig').strip() or digest(prompt) != recipe.get('prompt_sha256'):
        raise ValueError(f'{role}三选一提示词缺失或已变化')
    nonempty(recipe.get('tool'), f'{role}三选一工具')
    if not isinstance(recipe.get('parameters'), dict):
        raise ValueError(f'{role}三选一可控参数须为对象')
    inputs = recipe.get('inputs')
    if not isinstance(inputs, list) or not inputs:
        raise ValueError(f'{role}三选一缺少有序输入')
    for entry in inputs:
        if not isinstance(entry, dict):
            raise ValueError('三选一输入须为对象')
        nonempty(entry.get('purpose'), '三选一输入用途')
        raw = entry.get('file')
        nonempty(raw, '三选一输入文件')
        path = Path(raw) if Path(raw).is_absolute() else within(root, raw)
        if digest(path) != entry.get('sha256'):
            raise ValueError(f'{role}三选一输入已变化')
    calls = data.get('calls')
    required_calls = data.get('required_calls', 3)
    if type(required_calls) is not int or not 1 <= required_calls <= 3:
        raise ValueError(f'{role}required_calls须为1..3')
    if required_calls != 3:
        nonempty(data.get('user_override_reason'), f'{role}用户明确限制候选数量的依据')
    if not isinstance(calls, list) or len(calls) != required_calls:
        raise ValueError(f'{role}需{required_calls}次真实调用记录')
    ids, selected_hash = set(), None
    for index, call in enumerate(calls):
        if not isinstance(call, dict):
            raise ValueError('三选一调用须为对象')
        identifier = call.get('id')
        nonempty(identifier, '三选一调用id')
        if identifier in ids:
            raise ValueError(f'{role}独立调用id重复')
        ids.add(identifier)
        if any(call.get(key) != recipe.get(key) for key in ('prompt_sha256', 'inputs', 'tool', 'parameters')):
            raise ValueError(f'{role}/{identifier}不是相同提示词、有序输入、工具和参数')
        evidence = within(root, call.get('evidence_file'))
        if not evidence.stat().st_size or digest(evidence) != call.get('evidence_sha256'):
            raise ValueError(f'{role}/{identifier}调用证据缺失或已变化')
        output_hash = selection_image(call.get('output'), root, role)
        verdicts = call.get('checks')
        if (not isinstance(verdicts, dict) or set(verdicts) != set(goals)
                or any(value not in ('pass', 'fail', 'pending') for value in verdicts.values())):
            raise ValueError(f'{role}/{identifier}三选一逐项结论遗漏或无效')
        passed = all(value == 'pass' for value in verdicts.values())
        if call.get('result') != ('pass' if passed else 'fail'):
            raise ValueError(f'{role}/{identifier}总体结论与逐项结论不符')
        nonempty(call.get('observation'), f'{role}/{identifier}三选一实际观察')
        if index == 0 and not passed:
            raise ValueError(f'{role}追加两次调用前的首张必须合格')
        if identifier == data.get('selected_call'):
            if not passed:
                raise ValueError(f'{role}不能选择未通过的候选')
            selected_hash = output_hash
    if selected_hash is None:
        raise ValueError(f'{role}所选调用不存在')
    nonempty(data.get('selection_reason'), f'{role}具体选择依据')
    processing = data.get('processing', [])
    if not isinstance(processing, list):
        raise ValueError(f'{role}后处理链须为数组')
    for step in processing:
        if not isinstance(step, dict) or step.get('input_sha256') != selected_hash:
            raise ValueError(f'{role}后处理链输入版本不连续')
        path = within(root, step.get('record_file'))
        if digest(path) != step.get('record_sha256'):
            raise ValueError(f'{role}后处理记录已变化')
        provenance = read(path)
        if ((provenance.get('input_sha256') or provenance.get('source_sha256')) != selected_hash
                or provenance.get('output_sha256') != step.get('output_sha256')):
            raise ValueError(f'{role}后处理旁录与版本链不符')
        nonempty(step.get('output_sha256'), f'{role}后处理输出哈希')
        selected_hash = step['output_sha256']
    if selected_hash != current_hash:
        raise ValueError(f'{role}当前图不是三选一所选版本或其已记录后处理结果')


def check(record, root, stage=None, before=None, expected_task_mode=None):
    root = root.resolve()
    errors, checked, active, finished, sampled = [], [], set(), set(), []
    if stage is not None and before is not None:
        raise ValueError("stage和before不能同时指定")
    if stage is not None and stage not in ROLES or before is not None and before not in ROLES:
        raise ValueError("未知视图")
    brief_path = within(root, record.get("brief_file"))
    if digest(brief_path) != record.get("brief_sha256"):
        raise ValueError("验收目标底稿已变化，需重新核对当前版本，不能沿用旧结论")
    brief = read(brief_path)
    sources = brief.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("底稿缺少固定来源")
    for key, source in sources.items():
        if not isinstance(source, dict):
            raise ValueError(f"来源{key}不是对象")
        nonempty(source.get("file"), f"来源{key}路径")
        part = Path(source["file"])
        path = part if part.is_absolute() else within(root, source["file"])
        if digest(path) != source.get("sha256"):
            raise ValueError(f"来源{key}已变化，旧验收目标失效")
    plans, stages = brief.get("checks"), record.get("stages")
    if not isinstance(plans, dict) or not isinstance(stages, dict):
        raise ValueError("底稿checks或验收stages缺失")
    scope = brief.get("scope", "full_sheet")
    if scope not in ("full_sheet", "single_stage"):
        raise ValueError("底稿scope无效")
    if scope == "single_stage" and (stage is None or before is not None):
        raise ValueError("单阶段记录只支持--stage，不批准完整套或下游制作")
    task_mode = brief.get('task_mode', 'production' if scope == 'full_sheet' else 'existing_review')
    if task_mode not in ('production', 'existing_review'):
        raise ValueError('task_mode须为production或existing_review；仅生成试跑不批准验收')
    if expected_task_mode is not None and task_mode != expected_task_mode:
        raise ValueError('交付与验收底稿的task_mode不一致')
    selection = None
    compiled = {}
    for role in ROLES if scope == "full_sheet" else (stage,):
        required_groups = GROUPS | ({"spatial"} if role in ("back", "left") else set())
        plan = plans.get(role)
        if not isinstance(plan, list) or not plan:
            raise ValueError(f"{role}缺少固定的验收目标")
        goals, observed_groups, aspects = {}, set(), set()
        for goal in plan:
            if not isinstance(goal, dict):
                raise ValueError(f"{role}底稿检查项不是对象")
            key = goal.get("id")
            nonempty(key, "检查项id")
            if key in goals or goal.get("group") not in required_groups:
                raise ValueError(f"{role}检查项重复或分组无效")
            nonempty(goal.get("target"), f"{role}/{key}正确目标")
            ids = goal.get("source_ids")
            if not isinstance(ids, list) or not ids or any(v not in sources for v in ids):
                raise ValueError(f"{role}/{key}缺少有效来源")
            goals[key] = goal
            observed_groups.add(goal["group"])
            if goal['group'] == 'look':
                aspect = goal.get('aspect')
                if aspect not in LOOK_ASPECTS or aspect in aspects:
                    raise ValueError(f'{role}呈现项须有独立且不重复的aspect；旧底稿需补齐')
                aspects.add(aspect)
        if observed_groups != required_groups:
            raise ValueError(f"{role}底稿未覆盖{sorted(required_groups-observed_groups)}")
        required_aspects = {'color_style', 'lighting', 'framing'} | ({'head_pose', 'gaze', 'expression'} if role in ('head', 'front') else set())
        if not required_aspects <= aspects:
            raise ValueError(f'{role}底稿缺少独立呈现项{sorted(required_aspects-aspects)}')
        compiled[role] = goals

    def approve(role):
        nonlocal selection
        if role in finished:
            return
        if role in active:
            raise ValueError("依赖循环")
        active.add(role)
        data = stages.get(role)
        if not isinstance(data, dict):
            raise ValueError(f"{role}缺少当前候选")
        image = within(root, data.get("image"))
        png_size(image)
        image_hash = digest(image)
        if image_hash != data.get("sha256"):
            raise ValueError(f"{role}图片已变化，旧结论失效")
        if data.get("decision") != "approved":
            raise ValueError(f"{role}状态为{data.get('decision', 'unreviewed')}，不能使用为合格上游")
        goals = compiled[role]
        if scope == 'full_sheet' and task_mode == 'production':
            if selection is None:
                selection_path = within(root, record.get('selection_file', '制作记录/三选一记录.json'))
                if digest(selection_path) != record.get('selection_sha256'):
                    raise ValueError('三选一记录缺失或已变化，需绑定selection_sha256')
                selection = read(selection_path)
            check_selection(selection, root, role, image_hash, goals)
        checks = data.get("checks")
        if not isinstance(checks, list) or any(not isinstance(item, dict) for item in checks):
            raise ValueError(f"{role}逐项观察记录缺失")
        ids = [item.get("id") for item in checks]
        if len(ids) != len(set(ids)) or set(ids) != set(goals):
            raise ValueError(f"{role}逐项记录遗漏、重复或出现底稿外检查项")
        for item in checks:
            key = item["id"]
            if item.get("result") != "pass":
                raise ValueError(f"{role}/{key}为{item.get('result', 'pending')}，不能整体通过")
            nonempty(item.get("reference_observation"), f"{role}/{key}原始证据观察")
            nonempty(item.get("candidate_observation"), f"{role}/{key}候选实际观察")
            if goals[key]["group"] in ("identity", "design", "spatial"):
                nonempty(item.get("comparison_basis"), f"{role}/{key}对象尺度或空间比较依据")
            evidence = item.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise ValueError(f"{role}/{key}没有实际图像证据")
            purpose = {"identity":"reference_compare", "design":"reference_compare", "look":"reference_compare",
                       "native_quality":"native_detail", "reduced_quality":"reduced_view",
                       "background":"source_background", "spatial":"cross_view"}[goals[key]["group"]]
            if not any(isinstance(a, dict) and a.get("purpose") == purpose for a in evidence):
                raise ValueError(f"{role}/{key}缺少适用的{purpose}证据")
            for artifact in evidence:
                if not isinstance(artifact, dict) or artifact.get("viewed") is not True:
                    raise ValueError(f"{role}/{key}证据未记录实际查看")
                path = within(root, artifact.get("file"))
                png_size(path)
                if digest(path) != artifact.get("sha256") or artifact.get("candidate_sha256") != image_hash:
                    raise ValueError(f"{role}/{key}证据已变化或来自其他候选")
            if goals[key]["group"] == "background":
                policy = data.get('background_policy', {'mode':'exact_white'})
                if not isinstance(policy,dict) or policy.get('mode') not in ('transparent','exact_white','near_white_rgb_fallback'):
                    raise ValueError(f'{role}/{key}背景策略无效')
                mode=policy['mode']
                if mode == 'transparent':
                    if policy.get('alpha_source') not in ('model', 'tool_extraction'):
                        raise ValueError(f'{role}/{key}需记录真实Alpha来源')
                    if not {'light', 'dark'} <= {a.get('background') for a in evidence if a.get('purpose') == 'source_background'}:
                        raise ValueError(f'{role}/{key}缺少实际查看的深浅底Alpha证据')
                if mode=='near_white_rgb_fallback':
                    nonempty(policy.get('reason'),f'{role}/{key}Alpha能力降级依据')
                    if policy.get('alpha_limitation') not in ('input','output','both'):
                        raise ValueError(f'{role}/{key}须记录真实Alpha输入/输出限制')
                samples = item.get("empty_background_samples")
                if not isinstance(samples, list) or not samples:
                    raise ValueError(f"{role}/{key}空背景像素抽样缺失")
                if any(not isinstance(s, dict) or
                       (s.get('alpha') != 0 if mode == 'transparent' else not background_sample_valid(s.get("rgb"),mode)) or
                       not isinstance(s.get("xy"), list) or len(s["xy"]) != 2 or
                       s.get("confirmed_empty") is not True for s in samples):
                    raise ValueError(f"{role}/{key}空背景抽样不符合{mode}目标")
                try:
                    from PIL import Image
                except ImportError:
                    Image = None
                if Image is not None:
                    with Image.open(image) as pixels:
                        rgba = pixels.convert("RGBA")
                        if mode == 'transparent':
                            low, high = rgba.getchannel('A').getextrema()
                            if low != 0 or high == 0:
                                raise ValueError(f'{role}/{key}没有真实透明空白和非空主体')
                        for sample in samples:
                            xy = sample["xy"]
                            if any(type(v) is not int for v in xy) or not 0 <= xy[0] < rgba.width or not 0 <= xy[1] < rgba.height:
                                raise ValueError(f"{role}/{key}背景样本坐标无效")
                            pixel=rgba.getpixel(tuple(xy))
                            valid = pixel[3] == 0 if mode == 'transparent' else (pixel[3] == 255 and
                                    list(pixel[:3]) == sample['rgb'] and background_sample_valid(list(pixel[:3]), mode))
                            if not valid:
                                raise ValueError(f"{role}/{key}实际不透明背景像素或采样记录不符合{mode}")
                    sampled.append(role)
        dependencies = data.get("dependencies")
        if not isinstance(dependencies, dict):
            raise ValueError(f"{role}依赖版本记录缺失")
        expected = set(DEPENDENCIES[role]) if scope == "full_sheet" else set()
        if set(dependencies) != expected:
            raise ValueError(f"{role}依赖应为{sorted(expected)}")
        for upstream in expected:
            upstream_data = stages.get(upstream)
            if not isinstance(upstream_data, dict) or dependencies[upstream] != upstream_data.get("sha256"):
                raise ValueError(f"{role}引用的{upstream}不是当前版本")
            approve(upstream)
        active.remove(role)
        finished.add(role)
        checked.append({"role": role, "image": str(image), "sha256": image_hash, "check_count": len(checks)})

    targets = DEPENDENCIES[before] if before is not None else ((stage,) if stage else ROLES)
    for role in targets:
        try:
            approve(role)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append(str(exc))
            active.clear()
    return {"scope": "review_records_and_versions", "model_visual_checks": False,
            "mode": "before_stage" if before else "stage" if stage else "full_sheet",
            "task_mode": task_mode,
            "background_sample_pixels_verified": sampled,
            "recorded_approval_valid": not errors, "before": before, "stage": stage,
            "checked": checked, "errors": errors}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path)
    parser.add_argument("--root", type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--stage", choices=ROLES)
    group.add_argument("--before", choices=ROLES)
    args = parser.parse_args()
    try:
        result = check(read(args.record), (args.root or args.record.parent).resolve(), args.stage, args.before)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = {"scope": "review_records_and_versions", "model_visual_checks": False,
                  "recorded_approval_valid": False, "errors": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["recorded_approval_valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
