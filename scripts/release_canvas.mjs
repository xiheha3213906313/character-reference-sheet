/** Copy the maintained web bundle verbatim; never downloads or edits images. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';

export const digest=b=>createHash('sha256').update(b).digest('hex');
export const defaultAssets=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../assets/canvas-editor');
export const BUNDLE_FILES=['editor.html','start.cmd','使用说明.md','网页资源/hub.mjs','网页资源/launch.ps1','网页资源/native-runtime.mjs','网页资源/native-runtime-manifest.json','网页资源/native-worker.mjs','网页资源/native-matting.mjs'];

export async function localPath(root,relative){
 if(typeof relative!=='string'||!relative||relative.includes('\\')||relative.split('/').some(p=>!p||p==='.'||p==='..'||p.includes(':')))throw Error('无效相对路径：'+relative);
 const base=path.resolve(root),target=path.resolve(base,...relative.split('/'));
 if(!target.startsWith(base+path.sep))throw Error('路径越界：'+relative);
 let cursor=base;for(const part of ['',...relative.split('/')]){if(part)cursor=path.join(cursor,part);try{if((await fs.lstat(cursor)).isSymbolicLink())throw Error('不写入链接路径：'+cursor);}catch(e){if(e.code!=='ENOENT')throw e;}}
 return target;
}

export async function prepareRelease(assets=defaultAssets){
 const source=path.resolve(assets),manifestFile=await localPath(source,'网页资源/release.json');
 const bytes=await fs.readFile(manifestFile),manifest=JSON.parse(bytes);
 if(manifest.schemaVersion!==1||!Array.isArray(manifest.files)||manifest.files.length!==BUNDLE_FILES.length||new Set(manifest.files.map(f=>f.path)).size!==BUNDLE_FILES.length||BUNDLE_FILES.some(p=>!manifest.files.some(f=>f.path===p)))throw Error('网页释放清单的文件集合不完整。');
 const files=[];for(const item of manifest.files){const b=await fs.readFile(await localPath(source,item.path));if(digest(b)!==item.sha256||b.length!==item.size)throw Error('网页资源与释放清单不符：'+item.path);files.push({...item,bytes:b});}
 for(const id of ['initial-project','demo-data']){const m=files.find(f=>f.path==='editor.html').bytes.toString('utf8').match(new RegExp('<script id="'+id+'" type="application/json">([\\s\\S]*?)</script>'));if(!m||JSON.parse(m[1])!==null)throw Error('通用网页模板含人物数据：'+id);}
 files.push({path:'网页资源/release.json',sha256:digest(bytes),size:bytes.length,bytes});
 return {source,manifest,files};
}

export async function releaseCanvas(output,{assets=defaultAssets,prepared}={}){
 if(typeof output!=='string'||!output.trim())throw Error('请指定网页释放目录。');
 const root=path.resolve(output),bundle=prepared||await prepareRelease(assets);
 const releaseRecord=await localPath(root,'制作记录/网页释放.json'),stamp=new Date().toISOString().replace(/[:.]/g,'-');
 const targets=[];for(const item of bundle.files){const dest=await localPath(root,item.path);let old;try{old=await fs.readFile(dest);}catch(e){if(e.code!=='ENOENT')throw e;}targets.push({...item,dest,old});}
 await fs.mkdir(root,{recursive:true});let copied=0,backups=0;
 for(const item of targets){if(item.old&&digest(item.old)===item.sha256)continue;if(item.old){const backup=await localPath(root,'制作记录/网页备份/'+stamp+'/'+item.path);await fs.mkdir(path.dirname(backup),{recursive:true});await fs.writeFile(backup,item.old,{flag:'wx'});backups++;}await fs.mkdir(path.dirname(item.dest),{recursive:true});await fs.writeFile(item.dest,item.bytes);copied++;}
 const record={schemaVersion:1,uiRevision:bundle.manifest.uiRevision,source:'assets/canvas-editor',releasedAt:new Date().toISOString(),files:bundle.files.map(({path,sha256,size})=>({path,sha256,size}))};
 await fs.mkdir(path.dirname(releaseRecord),{recursive:true});await fs.writeFile(releaseRecord,JSON.stringify(record,null,2)+'\n');
 return {root,uiRevision:record.uiRevision,files:record.files.length,copied,backups};
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 try{console.log(JSON.stringify(await releaseCanvas(process.argv[2])));}catch(e){console.error(e.message);process.exitCode=1;}
}
