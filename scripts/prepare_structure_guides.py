"""Render reference guides from an explicitly reviewed structure manifest.

This prepares references only. It neither generates characters nor repairs outputs.
Coordinates and mask files describe the TARGET view; only source alpha is mirrored.
Requires Python 3.10+ and Pillow. No model download, GPU or detector is required.
"""
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageOps
import PIL
import argparse, hashlib, json, math

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def point(v):
    if len(v)!=2 or any(not 0<=float(x)<=1 for x in v):
        raise ValueError('Target coordinates must be normalized to [0,1]')
    return tuple(float(x) for x in v)

def shape_mask(spec, size, base):
    w,h=size; out=Image.new('L',size,0); d=ImageDraw.Draw(out)
    kind=spec['kind']
    if kind=='mask':
        im=Image.open(base/spec['file']).convert('L')
        if im.size!=size:raise ValueError('Mask must share the target canvas')
        return im.point(lambda v:255 if v>spec.get('threshold',127) else 0)
    if kind=='polygon':
        pts=[point(v) for v in spec['points']]
        if len(pts)<3:raise ValueError('Polygon needs at least three points')
        d.polygon([(round(x*(w-1)),round(y*(h-1))) for x,y in pts],fill=255)
    elif kind in ['ellipse','annulus']:
        a,b=point(spec['box'][:2]),point(spec['box'][2:])
        if a[0]>=b[0] or a[1]>=b[1]:raise ValueError('Invalid ellipse bounds')
        box=(round(a[0]*(w-1)),round(a[1]*(h-1)),round(b[0]*(w-1)),round(b[1]*(h-1)))
        if kind=='annulus':
            width=float(spec['width_fraction'])*w
            if not math.isfinite(width) or not 1<=width<min(box[2]-box[0],box[3]-box[1])/2:raise ValueError('Annular band must have positive width and an open center')
            d.ellipse(box,outline=255,width=round(width))
        else:d.ellipse(box,fill=255)
    elif kind=='path':
        pts=[point(v) for v in spec['points']]
        if len(pts)<2:raise ValueError('Path needs at least two points')
        profile=spec.get('width_profile',[[0,spec.get('width_fraction',.02)],[1,spec.get('width_fraction',.02)]])
        if len(profile)<2 or profile[0][0]!=0 or profile[-1][0]!=1 or any(a[0]>=b[0] for a,b in zip(profile,profile[1:])) or any(not 0<x[1]<=1 for x in profile):
            raise ValueError('Width profile needs ordered 0..1 positions and positive canvas-width fractions')
        pp=[(x*(w-1),y*(h-1)) for x,y in pts]
        lengths=[math.dist(a,b) for a,b in zip(pp,pp[1:])]; total=sum(lengths)
        if total==0:raise ValueError('Path has zero length')
        elapsed=0
        for a,b,length in zip(pp,pp[1:],lengths):
            for i in range(max(1,math.ceil(length))+1):
                t=i/max(1,math.ceil(length));s=max(0,min(1,(elapsed+t*length)/total))
                lo,hi=next((x,y) for x,y in zip(profile,profile[1:]) if x[0]<=s<=y[0])
                width=lo[1]+(hi[1]-lo[1])*(s-lo[0])/(hi[0]-lo[0]);r=width*w/2
                x=a[0]+(b[0]-a[0])*t;y=a[1]+(b[1]-a[1])*t
                d.ellipse((x-r,y-r,x+r,y+r),fill=255)
            elapsed+=length
    else:raise ValueError('Supported shapes: path, polygon, ellipse, annulus, mask')
    return out

def render(manifest, dest):
    file=Path(manifest).resolve();base=file.parent;c=json.loads(file.read_text(encoding='utf-8'))
    if c.get('schema_version')!=1:raise ValueError('Unsupported schema version')
    src=(base/c['source']).resolve();im=Image.open(src)
    size=tuple(c.get('canvas',im.size))
    if size!=im.size:raise ValueError('Source and target canvas must match; pre-register upstream')
    if 'body_mask' in c:
        body=shape_mask(c['body_mask'],size,base)
    else:
        if 'A' not in im.getbands() or im.getchannel('A').getextrema()==(255,255):
            raise ValueError('Opaque source requires an explicit reviewed foreground mask')
        alpha=im.getchannel('A');alpha=ImageOps.mirror(alpha) if c.get('mirror_x',True) else alpha
        body=alpha.point(lambda v:255 if v>c.get('alpha_threshold',4) else 0)
    parts=c.get('parts',[]);ids=[x['id'] for x in parts]
    if len(set(ids))!=len(ids):raise ValueError('Duplicate part ID')
    masks={x['id']:shape_mask(x['shape'],size,base) for x in parts}
    for x in parts:
        if type(x['depth_value']) is not int or not 1<=x['depth_value']<=255:raise ValueError('Foreground depth values must be integers in 1..255')
        if x.get('expected_count',1)<1:raise ValueError('Invalid expected part count')
        for far in x.get('nearer_than',[]):
            if far not in masks or x['depth_value']<=next(v['depth_value'] for v in parts if v['id']==far):
                raise ValueError('Depth values conflict with declared local overlap')
    body_depth=c.get('body_depth',130)
    if type(body_depth) is not int or not 1<=body_depth<=255:
        raise ValueError('Body depth must be an integer in 1..255')
    guide=Image.new('L',size,255);guide.paste(c.get('body_gray',160),mask=body)
    depth=Image.new('L',size,0);depth.paste(body_depth,mask=body)
    for x in sorted(parts,key=lambda x:x['depth_value']):
        mask=masks[x['id']]
        layer=Image.new('L',size,0);layer.paste(x['depth_value'],mask=mask)
        depth=ImageChops.lighter(depth,layer)
        if x.get('show_in_guide',True):guide.paste(x.get('guide_gray',32),mask=mask)
    dest=Path(dest).resolve()
    if dest.exists():raise FileExistsError('Use a new version directory; do not overwrite frozen guides')
    dest.mkdir(parents=True)
    guide.convert('RGB').save(dest/'silhouette_structure.png');depth.convert('RGB').save(dest/'rear_layers.png')
    body.save(dest/'reviewed_body_mask.png')
    assets=[{'source':str(src),'sha256':digest(src)}]
    for spec in [c.get('body_mask',{})]+[x['shape'] for x in parts]:
        if spec.get('kind')=='mask':
            p=(base/spec['file']).resolve();assets.append({'source':str(p),'sha256':digest(p)})
    rec={'schema_version':1,'manifest':str(file),'manifest_sha256':digest(file),'source_assets':assets,'canvas':size,'coordinates':'normalized target-view coordinates; mask files already in target view','source_alpha_transform':{'mirror_x':c.get('mirror_x',True),'alpha_threshold':c.get('alpha_threshold',4),'used': 'body_mask' not in c},'parts':parts,'pillow_version':PIL.__version__,'depth_meaning':'authored ordinal rear-camera layer hypothesis; brighter foreground nearer; not measured 3D and not opacity','visual_review':'not_evaluated','outputs':[]}
    for p in dest.glob('*.png'):rec['outputs'].append({'file':p.name,'sha256':digest(p)})
    (dest/'guide_record.json').write_text(json.dumps(rec,ensure_ascii=False,indent=2),encoding='utf-8')
    return rec

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('manifest');parser.add_argument('--output',required=True)
    args=parser.parse_args();rec=render(args.manifest,args.output)
    print(json.dumps({'canvas':rec['canvas'],'output':str(Path(args.output).resolve()),'visual_review':'not_evaluated'},ensure_ascii=False))
