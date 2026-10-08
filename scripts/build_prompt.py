"""Freeze a visually reviewed prompt spec; no image generation or visual judging."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import prompt_templates as templates
from prompt_templates import STAGES, EDIT_CLEAN

def text(value, name, optional=False):
    if not isinstance(value, str) or (not optional and not value.strip()):
        raise ValueError(f'{name} must be a nonempty string')
    return value.strip()

def render(spec):
    required = {'operation','stage','references','identity','critical_constraints'}
    optional = {'character_name','outfit','height_cm','baseline_reference','edit_target',
                'allowed_changes','quality_notes','user_overrides','background_mode','background_fallback_reason','corrections'}
    if not isinstance(spec, dict) or not required <= spec.keys():
        raise ValueError(f'Missing spec fields: {sorted(required - set(spec) if isinstance(spec, dict) else required)}')
    if set(spec) - required - optional:
        raise ValueError(f'Unknown spec fields: {sorted(set(spec)-required-optional)}')
    operation, stage = spec['operation'], spec['stage']
    if operation not in ('generate','edit') or stage not in STAGES:
        raise ValueError('operation must be generate/edit; stage must be head/front/back/left')
    background_mode = spec.get('background_mode', 'transparent')
    if background_mode not in templates.BACKGROUNDS:
        raise ValueError('background_mode must be transparent/white')
    if background_mode == 'white':
        text(spec.get('background_fallback_reason'), 'background_fallback_reason')
    common = templates.common(stage, spec.get('user_overrides', {}), background_mode)
    ratio = common['aspect_ratio']
    if not re.fullmatch(r'[1-9][0-9]*:[1-9][0-9]*', ratio):
        raise ValueError('aspect_ratio must be a positive ratio such as 3:4')
    refs = spec['references']
    if not isinstance(refs,list) or not refs:
        raise ValueError('references must be a nonempty ordered list')
    for i, ref in enumerate(refs,1):
        if (not isinstance(ref,dict) or not {'image','role'} <= ref.keys()
            or set(ref)-{'image','role','kind','guide','regions'}):
            raise ValueError(f'references[{i}] requires image and role; optional kind, guide and regions')
        if ref.get('kind','file') not in ('file','provider'):
            raise ValueError(f'references[{i}].kind must be file/provider')
        text(ref['image'],f'references[{i}].image')
        text(ref['role'],f'references[{i}].role')
        if 'guide' in ref:
            if ref['guide'] not in templates.GUIDES:
                raise ValueError(f'references[{i}].guide is unknown')
            if ref['guide'] in ('back_silhouette', 'rear_design', 'front_material') and stage != 'back':
                raise ValueError('back_silhouette/rear_design/front_material are only for the back stage')
            if ref['guide'] == 'edit_annotation' and operation != 'edit':
                raise ValueError('edit_annotation is only for an edit')
        if 'regions' in ref:
            if 'guide' in ref or not isinstance(ref['regions'], list) or len(ref['regions']) < 2:
                raise ValueError('A collage requires separate regions and no whole-image guide')
            region_ids = set()
            for region in ref['regions']:
                core = {'id', 'location', 'box', 'role', 'source_key'}
                if not isinstance(region, dict) or not core <= region.keys() or set(region) - core - {'guide'}:
                    raise ValueError('Each collage region requires id/location/box/role/source_key')
                for field in ('id', 'location', 'role', 'source_key'):
                    text(region[field], 'region.' + field)
                if region['id'] in region_ids:
                    raise ValueError('Duplicate collage region id')
                region_ids.add(region['id'])
                box = region['box']
                if not isinstance(box, list) or len(box) != 4 or any(type(v) is not int for v in box) or not 0 <= box[0] < box[2] or not 0 <= box[1] < box[3]:
                    raise ValueError('Invalid collage region box')
                guide = region.get('guide')
                if guide and (guide not in templates.GUIDES or stage != 'back' and guide in ('front_material', 'back_silhouette', 'rear_design')):
                    raise ValueError('Invalid collage region guide for this stage')
                if guide == 'edit_annotation' and operation != 'edit':
                    raise ValueError('edit_annotation region is only for an edit')
    corrections = spec.get('corrections', [])
    if (not isinstance(corrections, list) or any(not isinstance(c, str) or c not in templates.CORRECTIONS for c in corrections)
            or len(corrections) != len(set(corrections))):
        raise ValueError('corrections must identify distinct supported fixed correction templates')
    if 'gaze_up' in corrections and (stage not in ('head', 'front') or any(k in spec.get('user_overrides', {}) for k in ('framing_required',))):
        raise ValueError('gaze_up requires a front-facing head/front framing without user framing override')
    allowed = spec.get('allowed_changes', [])
    if not isinstance(allowed,list) or any(not isinstance(v,str) or not v.strip() for v in allowed):
        raise ValueError('allowed_changes must be a list of nonempty strings')
    constraints = spec['critical_constraints']
    if not isinstance(constraints,list) or not constraints:
        raise ValueError('critical_constraints must be a nonempty list')
    lines = [f"任务：{'生成' if operation=='generate' else '编辑'}参考中的同一角色、本套{STAGES[stage]}，比例{ratio}。"]
    if stage!='head' and 'height_cm' in spec:
        height=spec['height_cm']
        if isinstance(height,bool) or not isinstance(height,(int,float)) or height<=0:
            raise ValueError('height_cm must be a positive adopted height')
        lines.append(f'沿用本角色身高{height:g}cm及原体型比例。')
    if operation=='edit':
        baseline=spec.get('baseline_reference')
        if isinstance(baseline,bool) or not isinstance(baseline,int) or not 1<=baseline<=len(refs):
            raise ValueError('edit requires a valid baseline_reference')
        lines.append(f"唯一编辑底图为图{baseline}。本次修复：{text(spec.get('edit_target'),'edit_target')}")
    changes = '；'.join([templates.CHANGES[stage]] + [v.strip() for v in allowed]) if operation == 'generate' else ('；'.join(v.strip() for v in allowed) if allowed else '仅修复上述目标')
    lines.append(f'允许调整：{changes}；其余身份、设计、本色和材料保持原参考。')
    lines.append('构图必须：'+common['framing_required'])
    preferred=common['framing_preferred']
    if preferred: lines.append('在上述完整性条件下协调：'+preferred)
    lines.append('身份：'+text(spec['identity'],'identity'))
    if stage == 'back':
        lines.append('背面依据：' + templates.BACK_REFERENCE_RULE)
    ids=[]
    for constraint in constraints:
        core={'id','kind','source_indices','statement'}
        optional_constraint = {'source_regions'}
        ext={'attachment','path','end_anchor','frame_behavior'}
        if not isinstance(constraint,dict) or not core<=constraint.keys():
            raise ValueError('Each critical constraint requires id/kind/source_indices/statement')
        kind=constraint['kind']
        if kind not in ('shape','extent','layer','material','spatial'):
            raise ValueError('Unsupported constraint kind')
        if kind=='extent':
            visibility=constraint.get('visibility','complete')
            if visibility not in ('complete','partial'):
                raise ValueError('extent visibility must be complete/partial; omit wholly out-of-frame objects')
            permitted=core | ext | {'visibility'}
            needed=core | (ext if visibility=='complete' else {'path','frame_behavior'})
        else:
            permitted=needed=core
        if not needed<=constraint.keys() or set(constraint)-permitted-optional_constraint:
            raise ValueError(f'{kind} constraint requires {sorted(needed)}; permitted fields: {sorted(permitted)}')
        cid=text(constraint['id'],'constraint.id')
        if cid in ids: raise ValueError(f'Duplicate constraint id: {cid}')
        ids.append(cid)
        indices=constraint['source_indices']
        if (not isinstance(indices,list) or not indices or len(set(indices))!=len(indices)
            or any(isinstance(i,bool) or not isinstance(i,int) or not 1<=i<=len(refs) for i in indices)):
            raise ValueError(f'{cid}: source_indices must identify actual ordered references')
        selectors = constraint.get('source_regions', [{'index': i} for i in indices])
        if not isinstance(selectors, list) or not selectors or any(not isinstance(s, dict) or set(s) - {'index', 'region'} or type(s.get('index')) is not int or s['index'] not in indices for s in selectors):
            raise ValueError(f'{cid}: invalid source_regions')
        if set(s['index'] for s in selectors) != set(indices) or len({(s['index'], s.get('region')) for s in selectors}) != len(selectors):
            raise ValueError(f'{cid}: source_regions must cover the actual source_indices once per region')
        guides, labels = [], []
        for selection in selectors:
            i = selection['index']
            ref = refs[i - 1]
            if 'regions' in ref:
                region = next((r for r in ref['regions'] if r['id'] == selection.get('region')), None)
                if region is None:
                    raise ValueError(f'{cid}: collage source must name its actual region')
                guides.append(region.get('guide'))
                labels.append(f"图{i}{region['location']}")
            else:
                if 'region' in selection:
                    raise ValueError(f'{cid}: region used on an unpacked reference')
                guides.append(ref.get('guide'))
                labels.append(f'图{i}')
        source='、'.join(labels)
        if stage == 'back' and kind != 'shape' and all(g == 'back_silhouette' for g in guides):
            raise ValueError(f'{cid}: 剪影只能支持外形比例；背面设计、材料、裙层或连接须引用实际后方证据，不能只引用正面剪影')
        if stage == 'back' and kind != 'material' and all(g == 'front_material' for g in guides):
            raise ValueError(f'{cid}: 正面原图仅能支持材料纹理和表面光影；身形引用剪影，后方具体设计引用后方依据或明确推断')
        if stage == 'back' and kind not in ('shape', 'material') and all(
                g in ('front_material', 'back_silhouette') for g in guides):
            raise ValueError(f'{cid}: 正面原图与剪影均不提供背面具体服装设计答案')
        line=f"设计{cid}（{source}）：{text(constraint['statement'],cid+'.statement')}"
        if kind=='extent':
            if 'attachment' in constraint:
                line += ' 固定点：'+text(constraint['attachment'],cid+'.attachment')
            line += '；路径：'+text(constraint['path'],cid+'.path')
            if 'end_anchor' in constraint:
                line += '；真实末端：'+text(constraint['end_anchor'],cid+'.end_anchor')
            line += '；画幅处理：'+text(constraint['frame_behavior'],cid+'.frame_behavior')
        lines.append(line)
    lines.append('输入用途：'+'；'.join(f"图{i}={ref['role'].strip()}" for i,ref in enumerate(refs,1))+'。')
    for i, ref in enumerate(refs, 1):
        if ref.get('guide'):
            lines.append('辅助输入：'+templates.GUIDES[ref['guide']].format(index=i))
        for region in ref.get('regions', []):
            label = f"{i}{region['location']}"
            lines.append('拼图分工：' + f"图{label}={region['role']}")
            if region.get('guide'):
                lines.append('区域限定：' + templates.GUIDES[region['guide']].format(index=label))
    for correction in corrections:
        lines.append('针对性修正：'+templates.CORRECTIONS[correction])
    for key,label in (('lighting','人物光照'),('expression','表情'),('background','背景')):
        lines.append(label+'：'+common[key])
    notes = text(spec.get('quality_notes', ''), 'quality_notes', optional=True)
    lines.append('质量：'+templates.QUALITY + (' 本轮恢复目标：'+notes if notes else ''))
    if operation=='edit': lines.append(EDIT_CLEAN)
    return '\n'.join(lines)+'\n', ids

def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'): stream.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        raw=args.spec.read_bytes()
        spec=json.loads(raw.decode('utf-8-sig'))
        prompt,ids=render(spec)
        metadata=args.output.with_suffix(args.output.suffix+'.json')
        if args.output.exists() or metadata.exists():
            raise ValueError('Output already exists; use a new versioned output path')
        ref_records=[]
        for i,ref in enumerate(spec['references'],1):
            kind=ref.get('kind','file')
            if kind=='file':
                path=Path(ref['image'])
                if not path.is_absolute(): path=args.spec.resolve().parent/path
                path=path.resolve()
                if not path.is_file():
                    raise ValueError(f'references[{i}]: local reference does not exist or is not a file: {path}')
                payload=path.read_bytes()
                if not payload: raise ValueError(f'references[{i}]: local reference is empty: {path}')
                ref_records.append({'index':i,**ref,'kind':kind,'image':str(path),
                    'sha256':hashlib.sha256(payload).hexdigest(),'file_bytes_verified':True})
            else:
                ref_records.append({'index':i,**ref,'kind':kind,'sha256':None,
                    'provider_availability_verified_by_script':False})
        record={'scope':'prompt_assembly_only','semantic_review_performed_by_script':False,
                'spec_file':str(args.spec.resolve()),'spec_sha256':hashlib.sha256(raw).hexdigest(),
                'compiler_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'template_id': templates.TEMPLATE_ID,
                'template_sha256': hashlib.sha256(Path(templates.__file__).read_bytes()).hexdigest(),
                'user_overrides': spec.get('user_overrides', {}),
                'prompt_sha256':hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
                'included_constraint_ids':ids,'references':ref_records}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(prompt,encoding='utf-8',newline='\n')
        metadata.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'prompt_file':str(args.output.resolve()),'metadata_file':str(metadata.resolve()),'prompt_sha256':record['prompt_sha256']},ensure_ascii=False))
    except (OSError,ValueError,TypeError) as exc:
        parser.exit(2,str(exc)+'\n')

if __name__=='__main__': main()
