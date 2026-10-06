/** Copy the maintained web bundle verbatim; never downloads or edits images. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';

export const digest=b=>createHash('sha256').update(b).digest('hex');
export const defaultAssets=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../assets/canvas-editor');
export const BUNDLE_FILES=['编辑画布.html','网页资源/后端服务.cmd','使用说明.md','网页资源/hub.mjs','网页资源/launch.ps1','网页资源/native-runtime.mjs','网页资源/native-runtime-manifest.json','网页资源/native-worker.mjs','网页资源/native-matting.mjs','网页资源/shared-sampling.mjs','网页资源/shared-worker.mjs','网页资源/shared-matting.mjs'];

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
 for(const id of ['initial-project','demo-data']){const m=files.find(f=>f.path==='编辑画布.html').bytes.toString('utf8').match(new RegExp('<script id="'+id+'" type="application/json">([\\s\\S]*?)</script>'));if(!m||JSON.parse(m[1])!==null)throw Error('通用网页模板含人物数据：'+id);}
 const html=files.find(f=>f.path==='编辑画布.html').bytes.toString('utf8'),algorithm=files.find(f=>f.path==='网页资源/shared-sampling.mjs').bytes.toString('utf8').replaceAll('export function ','function '),worker=html.match(/<script id="shared-sampling-worker" type="text\/js-worker">([\s\S]*?)<\/script>/)?.[1];
 if(!worker||!worker.includes(algorithm))throw Error('网页内嵌Shared Sampling与维护算法不符。');
 files.push({path:'网页资源/release.json',sha256:digest(bytes),size:bytes.length,bytes});
 return {source,manifest,files};
}

export async function migrateDelivery(output,stamp=new Date().toISOString().replace(/[:.]/g,'-')){
 const root=path.resolve(output),moves=[];
 let entries=[];try{entries=await fs.readdir(root,{withFileTypes:true});}catch(e){if(e.code!=='ENOENT')throw e;}
 const mapping=[['画布清单.json','网页资源/画布清单.json'],['交付索引.js','网页资源/交付索引.js'],['editor.html','制作记录/网页备份/'+stamp+'/editor.html'],['start.cmd','制作记录/网页备份/'+stamp+'/start.cmd']];
 for(const entry of entries)if(entry.isFile()&&/\.(png|jpe?g)\.json$/i.test(entry.name))mapping.push([entry.name,'制作记录/'+entry.name]);
 let oldData=[];try{oldData=await fs.readdir(await localPath(root,'制作记录/画布数据'));}catch(e){if(e.code!=='ENOENT')throw e;}
 for(const name of oldData)if(/^\d{6}_(特写|正面|左侧面|背面)(?:_历史导入_[a-f0-9]{8})?\.png\.js$/.test(name))mapping.push(['制作记录/画布数据/'+name,'网页资源/画布数据/'+name]);
 // Preflight every exact file before moving any existing delivery data.
 for(const [from,to] of mapping){const source=await localPath(root,from),dest=await localPath(root,to);let bytes;try{bytes=await fs.readFile(source);}catch(e){if(e.code==='ENOENT')continue;throw e;}let duplicate=false;try{const existing=await fs.readFile(dest);if(!from.startsWith('制作记录/画布数据/')||digest(existing)!==digest(bytes))throw Error('迁移目标已经存在，拒绝覆盖：'+to);duplicate=true;}catch(e){if(e.code!=='ENOENT')throw e;}moves.push({from,to,source,dest,duplicate,sha256:digest(bytes)});}
 for(const relative of ['画布清单.json','交付索引.js','网页资源/画布清单.json','网页资源/交付索引.js']){
  let text;try{text=await fs.readFile(await localPath(root,relative),'utf8');}catch(e){if(e.code==='ENOENT')continue;throw e;}
  if(relative.endsWith('.js')){const prefix='window.CHARACTER_DELIVERY=';text=text.trim();if(!text.startsWith(prefix)||!text.endsWith(';'))throw Error('旧交付索引格式无效。');JSON.parse(text.slice(prefix.length,-1));}else JSON.parse(text);
 }
 await fs.mkdir(await localPath(root,'网页资源'),{recursive:true});
 for(const move of moves){await fs.mkdir(path.dirname(move.dest),{recursive:true});if(move.duplicate)await fs.unlink(move.source);else await fs.rename(move.source,move.dest);}
 const oldFolder=await localPath(root,'制作记录/画布数据');try{if(!(await fs.readdir(oldFolder)).length)await fs.rmdir(oldFolder);}catch(e){if(e.code!=='ENOENT')throw e;}
 for(const relative of ['网页资源/画布清单.json','网页资源/交付索引.js']){
  const file=await localPath(root,relative);let text;try{text=await fs.readFile(file,'utf8');}catch(e){if(e.code==='ENOENT')continue;throw e;}
  const isScript=relative.endsWith('.js'),prefix='window.CHARACTER_DELIVERY=';if(isScript&&(!text.trim().startsWith(prefix)||!text.trim().endsWith(';')))throw Error('旧交付索引格式无效。');
  const data=JSON.parse(isScript?text.trim().slice(prefix.length,-1):text);let changed=false;
  for(const entry of [...(data.current||[]),...(data.candidates||[])]){
   if(entry.dataScript?.startsWith('制作记录/画布数据/')){const next=entry.dataScript.replace('制作记录/画布数据/','网页资源/画布数据/');try{await fs.access(await localPath(root,next));entry.dataScript=next;changed=true;}catch(e){if(e.code!=='ENOENT')throw e;}}
   if(entry.data&&entry.dataScript?.startsWith('网页资源/画布数据/')){await fs.access(await localPath(root,entry.dataScript));delete entry.data;changed=true;}
  }
  if(changed){const backup=await localPath(root,'制作记录/网页备份/'+stamp+'/'+relative);await fs.mkdir(path.dirname(backup),{recursive:true});await fs.writeFile(backup,text,{flag:'wx'});const serialized=JSON.stringify(data).replace(/</g,'\\u003c').replace(/\u2028/g,'\\u2028').replace(/\u2029/g,'\\u2029');await fs.writeFile(file,isScript?prefix+serialized+';\n':JSON.stringify(data,null,2)+'\n');}
 }
 return moves.map(({from,to,sha256})=>({from,to,sha256}));
}

export async function releaseCanvas(output,{assets=defaultAssets,prepared}={}){
 if(typeof output!=='string'||!output.trim())throw Error('请指定网页释放目录。');
 const root=path.resolve(output),bundle=prepared||await prepareRelease(assets);
 const releaseRecord=await localPath(root,'制作记录/网页释放.json'),stamp=new Date().toISOString().replace(/[:.]/g,'-');
 const targets=[];for(const item of bundle.files){const dest=await localPath(root,item.path);let old;try{old=await fs.readFile(dest);}catch(e){if(e.code!=='ENOENT')throw e;}targets.push({...item,dest,old});}
 await fs.mkdir(root,{recursive:true});const migrations=await migrateDelivery(root,stamp);let copied=0,backups=0;
 for(const item of targets){if(item.old&&digest(item.old)===item.sha256)continue;if(item.old){const backup=await localPath(root,'制作记录/网页备份/'+stamp+'/'+item.path);await fs.mkdir(path.dirname(backup),{recursive:true});await fs.writeFile(backup,item.old,{flag:'wx'});backups++;}await fs.mkdir(path.dirname(item.dest),{recursive:true});await fs.writeFile(item.dest,item.bytes);copied++;}
 const record={schemaVersion:1,uiRevision:bundle.manifest.uiRevision,source:'assets/canvas-editor',releasedAt:new Date().toISOString(),files:bundle.files.map(({path,sha256,size})=>({path,sha256,size}))};
 await fs.mkdir(path.dirname(releaseRecord),{recursive:true});await fs.writeFile(releaseRecord,JSON.stringify(record,null,2)+'\n');
 return {root,uiRevision:record.uiRevision,files:record.files.length,copied,backups,migrations};
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 try{console.log(JSON.stringify(await releaseCanvas(process.argv[2])));}catch(e){console.error(e.message);process.exitCode=1;}
}
