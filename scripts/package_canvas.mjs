/** Archive explicit image roles, build local indexes, copy the maintained web bundle. */
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {digest,localPath,prepareRelease,releaseCanvas} from './release_canvas.mjs';
export const ROLES={head:['01_特写.png','特写'],front:['02_正面.png','正面'],side:['03_左侧面.png','左侧面'],back:['04_背面.png','背面']};
const readJSON=async(p,fallback)=>{try{return JSON.parse(await fs.readFile(p,'utf8'));}catch(e){if(e.code==='ENOENT')return fallback;throw e;}};
const safeJSON=j=>JSON.stringify(j).replace(/</g,'\\u003c').replace(/\u2028/g,'\\u2028').replace(/\u2029/g,'\\u2029');
async function image(src,expected){const b=await fs.readFile(src);if(b.length<24||!b.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]))||b.toString('ascii',12,16)!=='IHDR'||!b.readUInt32BE(16)||!b.readUInt32BE(20))throw Error('需要有效PNG：'+src);const sha256=digest(b);if(expected&&expected!==sha256)throw Error('来源哈希不匹配：'+src);return {b,sha256,width:b.readUInt32BE(16),height:b.readUInt32BE(20)};}
async function noConflict(dest,b){try{if(digest(await fs.readFile(dest))!==digest(b))throw Error('已有候选内容不同，拒绝覆盖：'+dest);}catch(e){if(e.code!=='ENOENT')throw e;}}
async function unchangedCopy(dest,b){await noConflict(dest,b);try{await fs.writeFile(dest,b,{flag:'wx'});}catch(e){if(e.code!=='EEXIST')throw e;}}

export async function packageCanvas(config,{assets,baseDir=process.cwd()}={}){
 if(!config||typeof config.character!=='string'||!config.character.trim()||typeof config.output!=='string'||!config.output.trim()||!config.current||Array.isArray(config.current))throw Error('需要 character、output 和 current。');
 if(Object.keys(config.current).some(r=>!ROLES[r]))throw Error('current只接受head/front/side/back；制作清单的left请映射为side。');
 const root=path.resolve(baseDir,config.output),sourcePath=p=>{if(typeof p!=='string'||!p.trim())throw Error('图片路径为空。');return path.resolve(baseDir,p);};
 const bundle=await prepareRelease(assets);
 const manifestPath=await localPath(root,'画布清单.json');let existing=await readJSON(manifestPath,null);
 if(!existing){const legacy=await readJSON(await localPath(root,'交付清单.json'),null);existing=legacy?.version===2&&Array.isArray(legacy.candidates)?legacy:{version:2,character:config.character,candidates:[]};}
 if(existing.character!==config.character||!Array.isArray(existing.candidates))throw Error('已有账本不是此人物的候选目录。');
 const candidates=existing.candidates.map(c=>({...c})),keys=new Map(),bytes=new Map();let sequence=0;
 for(const c of candidates){if(!ROLES[c.view]||!/^候选\/\d{6}_(特写|正面|左侧面|背面)(?:_历史导入_[a-f0-9]{8})?\.png$/.test(c.path)||c.dataScript!=='制作记录/画布数据/'+c.name+'.js')throw Error('已有候选路径不符合规范。');const img=await image(await localPath(root,c.path),c.sha256),key=c.view+':'+img.sha256;if(keys.has(key))throw Error('已有账本重复候选。');keys.set(key,c);bytes.set(c.id,img.b);sequence=Math.max(sequence,c.sequence||0);}
 const all=config.candidates||[];if(!Array.isArray(all))throw Error('candidates应为数组。');
 for(let i=1;i<all.length;i++)if(all[i].generatedAt&&all[i-1].generatedAt&&Date.parse(all[i].generatedAt)<Date.parse(all[i-1].generatedAt))throw Error('候选输入必须按实际生成顺序排列。');
 for(const entry of all){
  if(!ROLES[entry.view])throw Error('候选只允许head/front/side/back。');const img=await image(sourcePath(entry.path),entry.sha256),key=entry.view+':'+img.sha256;let c=keys.get(key);
  if(c){if(entry.statusLabel)c.statusLabel=entry.statusLabel;if(entry.provenance)c.provenance=entry.provenance;continue;}
  const known=!entry.legacy;if(known&&!entry.generatedAt&&!entry.orderEvidence)throw Error('新候选需要真实时间或顺序证据；历史导入请标legacy。');if(entry.generatedAt&&!Number.isFinite(Date.parse(entry.generatedAt)))throw Error('无效生成时间。');
  const num=known?++sequence:0,name=String(num).padStart(6,'0')+'_'+ROLES[entry.view][1]+(known?'':'_历史导入_'+img.sha256.slice(0,8))+'.png';
  c={id:entry.view+'-'+img.sha256,view:entry.view,name,path:'候选/'+name,sequence:num,generatedAt:entry.generatedAt||null,orderEvidence:entry.orderEvidence||null,statusLabel:entry.statusLabel||'未验收',sha256:img.sha256,width:img.width,height:img.height,source:entry.source||sourcePath(entry.path),dataScript:'制作记录/画布数据/'+name+'.js'};
  if(entry.provenance)c.provenance=entry.provenance;
  await noConflict(await localPath(root,c.path),img.b);candidates.push(c);keys.set(key,c);bytes.set(c.id,img.b);
 }
 const current=[];for(const view of Object.keys(ROLES)){const entry=config.current[view];if(!entry)throw Error('缺少当前视图 '+view);const img=await image(sourcePath(typeof entry==='string'?entry:entry.path),typeof entry==='string'?undefined:entry.sha256),c=keys.get(view+':'+img.sha256);if(!c)throw Error('当前视图必须也在候选记录中：'+view);current.push({...c,path:ROLES[view][0],name:ROLES[view][0],data:'data:image/png;base64,'+img.b.toString('base64'),width:img.width,height:img.height});}
 // Resolve every output before writing. Preserve every archived candidate on reruns.
 for(const c of candidates){await localPath(root,c.path);await localPath(root,c.dataScript);}for(const c of current)await localPath(root,c.path);
 let disk=[];try{disk=await fs.readdir(await localPath(root,'候选'));}catch(e){if(e.code!=='ENOENT')throw e;}
 const registered=new Set(candidates.map(c=>c.name));if(disk.some(n=>/^\d{6}_(特写|正面|左侧面|背面).*\.png$/.test(n)&&!registered.has(n)))throw Error('目录有未登记的视图候选，请明确来源后加入配置；不会删减图片。');
 await localPath(root,'交付索引.js');await localPath(root,'制作记录/网页释放.json');for(const f of bundle.files)await localPath(root,f.path);
 await fs.mkdir(root,{recursive:true});await fs.mkdir(await localPath(root,'候选'),{recursive:true});await fs.mkdir(await localPath(root,'制作记录/画布数据'),{recursive:true});
 for(const c of candidates){const b=bytes.get(c.id);await unchangedCopy(await localPath(root,c.path),b);await fs.writeFile(await localPath(root,c.dataScript),'window.dispatchEvent(new CustomEvent("character-image",{detail:'+safeJSON({id:c.id,data:'data:image/png;base64,'+b.toString('base64')})+'}));\n');}
 for(const c of current){const b=bytes.get(c.id),dest=await localPath(root,c.path);try{const old=await fs.readFile(dest);if(digest(old)!==c.sha256){const backup=await localPath(root,'制作记录/被替换单图/'+c.name.slice(0,-4)+'_'+digest(old).slice(0,12)+'.png');await fs.mkdir(path.dirname(backup),{recursive:true});await unchangedCopy(backup,old);}}catch(e){if(e.code!=='ENOENT')throw e;}await fs.writeFile(dest,b);}
 const manifest={version:2,character:config.character,current:current.map(({data,...c})=>c),candidates};await fs.writeFile(manifestPath,JSON.stringify(manifest,null,2)+'\n');await fs.writeFile(await localPath(root,'交付索引.js'),'window.CHARACTER_DELIVERY='+safeJSON({...manifest,current})+';\n');
 const release=await releaseCanvas(root,{prepared:bundle});return {root,count:candidates.length,current:current.map(c=>c.name),sequence,release};
}

if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 try{const file=path.resolve(process.argv[2]);console.log(JSON.stringify(await packageCanvas(JSON.parse(await fs.readFile(file,'utf8')),{assets:process.argv[3],baseDir:path.dirname(file)})));}catch(e){console.error(e.message);process.exitCode=1;}
}
