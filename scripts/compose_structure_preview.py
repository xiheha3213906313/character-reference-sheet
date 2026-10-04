"""Compose a reference-only layer illustration from reviewed component assets.

Requires Pillow. Never use the preview as a delivered model-generated character.
Part order is far-to-near. Existing output directories are never overwritten.
"""
from pathlib import Path
from PIL import Image
import PIL
import argparse,hashlib,json,math

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def compose(manifest,output):
    file=Path(manifest).resolve();base=file.parent;c=json.loads(file.read_text(encoding='utf-8'))
    src=(base/c['base']).resolve();im=Image.open(src).convert('RGBA');rows=[]
    for part in c['parts']:
        path=(base/part['image']).resolve();component=Image.open(path)
        if 'A' not in component.getbands():raise ValueError('Component requires genuine reviewed Alpha')
        component=component.convert('RGBA');scale=float(part['scale'])
        if not math.isfinite(scale) or not 0<scale<=1:raise ValueError('Use a positive uniform scale no greater than 1')
        xy=part['xy']
        if len(xy)!=2 or any(not isinstance(v,int) for v in xy):raise ValueError('xy must be two integer canvas coordinates')
        size=(max(1,round(component.width*scale)),max(1,round(component.height*scale)))
        component=component.resize(size,Image.Resampling.LANCZOS);visible=component.getchannel('A').point(lambda a:255 if a>4 else 0).getbbox()
        if visible is None:raise ValueError('Component is empty')
        bounds=[xy[0]+visible[0],xy[1]+visible[1],xy[0]+visible[2],xy[1]+visible[3]]
        if not c.get('allow_partial_component',False) and (bounds[0]<0 or bounds[1]<0 or bounds[2]>im.width or bounds[3]>im.height):raise ValueError('Visible complete component would be clipped')
        im.alpha_composite(component,tuple(xy))
        rows.append({'image':str(path),'sha256':sha(path),'scale':scale,'xy':xy,'visible_canvas_bounds':bounds,'purpose':part.get('purpose'),'evidence':part.get('evidence')})
    crop=c.get('crop',[0,0,im.width,im.height])
    if len(crop)!=4 or any(not isinstance(v,int) for v in crop) or not (0<=crop[0]<crop[2]<=im.width and 0<=crop[1]<crop[3]<=im.height):raise ValueError('Invalid context crop')
    im=im.crop(tuple(crop));bg=Image.new('RGBA',im.size,'white');bg.alpha_composite(im)
    dest=Path(output).resolve()
    if dest.exists():raise FileExistsError('Use a new reference version directory')
    dest.mkdir(parents=True);image=dest/'reference_only_preview.png';bg.convert('RGB').save(image)
    record={'manifest':str(file),'manifest_sha256':sha(file),'base':str(src),'base_sha256':sha(src),'parts':rows,'crop':crop,'pillow_version':PIL.__version__,'role':'reference_only; not a generated or approved final character','visual_review':'not_evaluated','output_sha256':sha(image)}
    (dest/'reference_record.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return record

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('manifest');parser.add_argument('--output',required=True)
    args=parser.parse_args();compose(args.manifest,args.output);print(json.dumps({'output':args.output,'role':'reference_only','visual_review':'not_evaluated'}))
