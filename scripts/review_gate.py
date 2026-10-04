"""Validate recorded visual reviews and fixed versions; does NOT inspect image semantics."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from check_delivery import ROLES, png_size, within

GROUPS = {"identity", "design", "look", "native_quality", "reduced_quality", "background"}
DEPENDENCIES = {"head": (), "front": ("head",), "back": ("front",), "left": ("front", "back")}


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


def check(record, root, stage=None, before=None):
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
    compiled = {}
    for role in ROLES if scope == "full_sheet" else (stage,):
        required_groups = GROUPS | ({"spatial"} if role in ("back", "left") else set())
        plan = plans.get(role)
        if not isinstance(plan, list) or not plan:
            raise ValueError(f"{role}缺少固定的验收目标")
        goals, observed_groups = {}, set()
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
        if observed_groups != required_groups:
            raise ValueError(f"{role}底稿未覆盖{sorted(required_groups-observed_groups)}")
        compiled[role] = goals

    def approve(role):
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
                    if policy.get('alpha_source') not in ('model', 'tool_extraction', 'python_matting'):
                        raise ValueError(f'{role}/{key}需记录真实Alpha来源')
                    if not {'light', 'dark'} <= {a.get('background') for a in evidence if a.get('purpose') == 'source_background'}:
                        raise ValueError(f'{role}/{key}缺少实际查看的深浅底Alpha证据')
                    if policy.get('alpha_source') == 'python_matting':
                        provenance = read(within(root, policy.get('provenance_file')))
                        if provenance.get('operation') != 'python_background_matting' or provenance.get('output_sha256') != image_hash:
                            raise ValueError(f'{role}/{key}抠像来源记录与当前图不符')
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
