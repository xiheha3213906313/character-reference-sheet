"""Freeze a visually reviewed prompt spec; no image generation or visual judging."""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

STAGES = {'head': '头部特写', 'front': '全身正面', 'back': '全身背面', 'left': '人物自身左侧全身'}
EDIT_CLEAN = '保持画面色彩、细节干净，不要添加噪点、颗粒、污渍、褶皱。'

def text(value, name, optional=False):
    if not isinstance(value, str) or (not optional and not value.strip()):
        raise ValueError(f'{name} must be a nonempty string')
    return value.strip()

def render(spec):
    required = {'operation','stage','aspect_ratio','references','framing','identity',
                'critical_constraints','allowed_changes','presentation','quality'}
    optional = {'character_name','outfit','height_cm','baseline_reference','edit_target'}
    if not isinstance(spec, dict) or not required <= spec.keys():
        raise ValueError(f'Missing spec fields: {sorted(required - set(spec) if isinstance(spec, dict) else required)}')
    if set(spec) - required - optional:
        raise ValueError(f'Unknown spec fields: {sorted(set(spec)-required-optional)}')
    operation, stage = spec['operation'], spec['stage']
    if operation not in ('generate','edit') or stage not in STAGES:
        raise ValueError('operation must be generate/edit; stage must be head/front/back/left')
    ratio = text(spec['aspect_ratio'], 'aspect_ratio')
    if not re.fullmatch(r'[1-9][0-9]*:[1-9][0-9]*', ratio):
        raise ValueError('aspect_ratio must be a positive ratio such as 3:4')
    refs = spec['references']
    if not isinstance(refs,list) or not refs:
        raise ValueError('references must be a nonempty ordered list')
    for i, ref in enumerate(refs,1):
        if (not isinstance(ref,dict) or not {'image','role'} <= ref.keys()
            or set(ref)-{'image','role','kind'}):
            raise ValueError(f'references[{i}] requires image and role; optional kind is file/provider')
        if ref.get('kind','file') not in ('file','provider'):
            raise ValueError(f'references[{i}].kind must be file/provider')
        text(ref['image'],f'references[{i}].image')
        text(ref['role'],f'references[{i}].role')
    framing, presentation = spec['framing'], spec['presentation']
    if not isinstance(framing,dict) or set(framing) != {'required','preferred'}:
        raise ValueError('framing requires exactly required and preferred')
    if not isinstance(presentation,dict) or set(presentation) != {'lighting','expression','background'}:
        raise ValueError('presentation requires exactly lighting, expression, background')
    allowed = spec['allowed_changes']
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
    changes='；'.join(v.strip() for v in allowed) if allowed else '不改变已有视觉设计'
    lines.append(f'允许调整：{changes}；其余身份、设计、本色和材料保持原参考。')
    lines.append('构图必须：'+text(framing['required'],'framing.required'))
    preferred=text(framing['preferred'],'framing.preferred',optional=True)
    if preferred: lines.append('在上述完整性条件下协调：'+preferred)
    lines.append('身份：'+text(spec['identity'],'identity'))
    ids=[]
    for constraint in constraints:
        core={'id','kind','source_indices','statement'}
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
        if not needed<=constraint.keys() or set(constraint)-permitted:
            raise ValueError(f'{kind} constraint requires {sorted(needed)}; permitted fields: {sorted(permitted)}')
        cid=text(constraint['id'],'constraint.id')
        if cid in ids: raise ValueError(f'Duplicate constraint id: {cid}')
        ids.append(cid)
        indices=constraint['source_indices']
        if (not isinstance(indices,list) or not indices or len(set(indices))!=len(indices)
            or any(isinstance(i,bool) or not isinstance(i,int) or not 1<=i<=len(refs) for i in indices)):
            raise ValueError(f'{cid}: source_indices must identify actual ordered references')
        source='、'.join(f'图{i}' for i in indices)
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
    for key,label in (('lighting','人物光照'),('expression','表情'),('background','背景')):
        lines.append(label+'：'+text(presentation[key],'presentation.'+key))
    lines.append('质量：'+text(spec['quality'],'quality'))
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
                'prompt_sha256':hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
                'included_constraint_ids':ids,'references':ref_records}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(prompt,encoding='utf-8',newline='\n')
        metadata.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'prompt_file':str(args.output.resolve()),'metadata_file':str(metadata.resolve()),'prompt_sha256':record['prompt_sha256']},ensure_ascii=False))
    except (OSError,ValueError,TypeError) as exc:
        parser.exit(2,str(exc)+'\n')

if __name__=='__main__': main()
