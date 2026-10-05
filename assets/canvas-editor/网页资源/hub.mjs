/** Native Node.js only. No npm install, Python, upload, or automatic model download. */
import http from 'node:http';
import{nativeReady,installNative}from'./native-runtime.mjs';
import{NativeMatting}from'./native-matting.mjs';
import path from 'node:path';
import os from 'node:os';
import fs from 'node:fs';
import { promises as fsp } from 'node:fs';
import { pipeline } from 'node:stream/promises';
import { Transform } from 'node:stream';
import { createHash, randomBytes } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const HERE=path.dirname(fileURLToPath(import.meta.url));
const EDITOR=fs.existsSync(path.join(HERE,'editor.html'))?path.join(HERE,'editor.html'):path.join(HERE,'..','editor.html');
export const PORT=18743, ORIGIN=`http://127.0.0.1:${PORT}`, APP='character-canvas-v2';
export const REVISION=16;
export const CACHE=process.env.CHARACTER_CANVAS_CACHE||path.join(os.tmpdir(),'CharacterSheetCanvas','cache');
export const STATE=process.env.CHARACTER_CANVAS_STATE||path.join(process.env.LOCALAPPDATA||path.join(os.homedir(),'.cache'),'CharacterSheetCanvas');
const ORT_VERSION='1.30.0', RUNTIME_FILES=['ort.all.min.mjs','ort-wasm-simd-threaded.jsep.mjs','ort-wasm-simd-threaded.jsep.wasm'];
const RUNTIME=path.join(CACHE,'runtime',ORT_VERSION), MODEL_DIR=path.join(CACHE,'models');
export const MODELS={
  "ben2": {
    "repo": "onnx-community/BEN2-ONNX",
    "file": "onnx/model_fp16.onnx",
    "sizeInput": 1024,
    "mean": [
      0.485,
      0.456,
      0.406
    ],
    "std": [
      0.229,
      0.224,
      0.225
    ],
    "activation": "alpha",
    "revision": "c552aa82688edce09f0ac9d2e31ad53d9d629010",
    "size": 219121675,
    "sha256": "dfdc25f421f32a0d1268e0f2ff2153d340e8f1d52d3dd16f5dc33c1ce85cedf1",
    "engine": "native"
  },
  "full": {
    "repo": "onnx-community/BiRefNet-ONNX",
    "revision": "534d3c82d3bb8b2f0867db6dfbc3a525b8e42f67",
    "file": "onnx/model.onnx",
    "size": 972666916,
    "sha256": "58f621f00f5d756097615970a88a791584600dcf7c45b18a0a6267535a1ebd3c",
    "sizeInput": 1024,
    "engine": "native",
    "activation": "logits"
  },
  "rmbg2": {
    "repo": "xiheha/RMBG2.0",
    "revision": "main",
    "file": "RMBG-2.0-FP32 .onnx",
    "size": 1024331469,
    "sha256": "5b486f08200f513f460da46dd701db5fbb47d79b4be4b708a19444bcd4e79958",
    "sizeInput": 1024,
    "engine": "native",
    "activation": "alpha"
  }
};
export function modelURL(id){const m=MODELS[id];if(!m)throw new Error('Unknown model');return `https://huggingface.co/${m.repo}/resolve/${m.revision}/${m.file.split('/').map(encodeURIComponent).join('/')}`;}
export function modelSources(id){const primary=modelURL(id);return[primary,primary.replace('https://huggingface.co/','https://hf-mirror.com/')];}
export async function fetchDownload(url,options={}){
 const timeout=new AbortController(),timer=setTimeout(()=>timeout.abort(new Error('下载连接超时')),12000);try{return await fetch(url,{...options,signal:options.signal?AbortSignal.any([options.signal,timeout.signal]):timeout.signal});}finally{clearTimeout(timer);}
}
function downloadError(e){const code=e.cause?.code;return code==='UND_ERR_CONNECT_TIMEOUT'||e.message==='下载连接超时'?'连接超时':e.message==='fetch failed'?'无法连接下载地址'+(code?' ('+code+')':''):String(e.message||e);}
export async function downloadModelFile(id,destination,{signal,fetcher=fetchDownload,onProgress=()=>{},onSource=()=>{}}={}){
 const model=MODELS[id],failures=[];if(!model)throw new Error('Unknown model');for(const [index,url]of modelSources(id).entries()){if(signal?.aborted)throw signal.reason||new Error('下载已取消');onSource(index===0?'原站':'镜像');try{return await downloadFile(url,destination,{signal,fetcher,onProgress,expectedSize:model.size,expectedHash:model.sha256});}catch(e){if(signal?.aborted)throw e;failures.push((index===0?'原站':'镜像')+'：'+downloadError(e));}}throw new Error(failures.join('；'));
}
export async function hashFile(file){const hash=createHash('sha256');for await(const chunk of fs.createReadStream(file))hash.update(chunk);return hash.digest('hex');}
export async function verifiedModel(id){const m=MODELS[id];if(!m)return false;try{const stat=await fsp.stat(path.join(MODEL_DIR,id+'.onnx'));if(stat.size!==m.size)return false;const info=JSON.parse(await fsp.readFile(path.join(MODEL_DIR,id+'.json'),'utf8'));return info.sha256===m.sha256&&info.size===m.size&&info.mtimeMs===stat.mtimeMs;}catch{return false;}}
async function runtimeReady(){try{for(const file of RUNTIME_FILES){const stat=await fsp.stat(path.join(RUNTIME,file));if(stat.size<1000)return false;}const manifest=JSON.parse(await fsp.readFile(path.join(RUNTIME,'manifest.json'),'utf8'));return manifest.version===ORT_VERSION&&RUNTIME_FILES.every(f=>manifest.files[f]?.bytes>1000);}catch{return false;}}
export async function downloadFile(url,destination,{signal,onProgress=()=>{},expectedSize,expectedHash,fetcher=fetchDownload}={}){
 const partial=destination+'.partial';await fsp.mkdir(path.dirname(destination),{recursive:true});
 // Only a specifically named partial file within the chosen cache is removed.
 await fsp.rm(partial,{force:true});
 try{
  const res=await fetcher(url,{signal,redirect:'follow'});if(!res.ok||!res.body)throw new Error(`下载失败 (${res.status})`);
  const total=expectedSize||Number(res.headers.get('content-length'))||0,hash=createHash('sha256');let received=0;
  const count=new Transform({transform(chunk,enc,cb){received+=chunk.length;if(expectedSize&&received>expectedSize)return cb(new Error('模型大小不符'));hash.update(chunk);onProgress(received,total);cb(null,chunk);}});
  await pipeline(res.body,count,fs.createWriteStream(partial),{signal});
  if(expectedSize&&received!==expectedSize)throw new Error('下载不完整，请重试');
  if(!expectedSize&&received<1000)throw new Error('运行时文件异常');
  const sha256=hash.digest('hex');if(expectedHash&&sha256!==expectedHash)throw new Error('文件校验失败，请重试');
  await fsp.rename(partial,destination);return {bytes:received,sha256};
 }catch(e){await fsp.rm(partial,{force:true});throw e;}
}
async function ensureRuntime(signal,state,fetcher=fetchDownload){
 if(await runtimeReady())return;
 const manifest={version:ORT_VERSION,files:{}};await fsp.mkdir(RUNTIME,{recursive:true});
 for(const file of RUNTIME_FILES){state.phase='下载运行时';state.received=0;state.total=0;manifest.files[file]=await downloadFile(`https://cdn.jsdelivr.net/npm/onnxruntime-web@${ORT_VERSION}/dist/${file}`,path.join(RUNTIME,file),{signal,fetcher,onProgress:(n,t)=>{state.received=n;state.total=t;}});}
 await fsp.writeFile(path.join(RUNTIME,'manifest.json'),JSON.stringify(manifest,null,2));
}
function json(res,status,value){res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(value));}
async function readJSON(req){let body='';for await(const chunk of req){body+=chunk;if(body.length>16384)throw new Error('请求过大');}return JSON.parse(body||'{}');}
async function sendFile(req,res,file,type,cache=false){const stat=await fsp.stat(file);if(!stat.isFile())throw new Error('文件不可读');const headers={'Content-Type':type,'Content-Length':stat.size,'Cache-Control':cache?'public, max-age=31536000, immutable':'no-store','X-Content-Type-Options':'nosniff'};res.writeHead(200,headers);if(req.method==='HEAD')res.end();else await pipeline(fs.createReadStream(file),res);}
async function safeLocal(root,relative){const base=await fsp.realpath(root);const parts=relative.split('/');if(parts.some(p=>!p||p==='.'||p==='..'||p.includes('\\')||p.includes(':')))throw Error('路径无效');let cursor=base;for(const part of parts){cursor=path.join(cursor,part);if((await fsp.lstat(cursor)).isSymbolicLink())throw Error('不读取链接目录');}const resolved=await fsp.realpath(cursor);if(resolved!==base&&!resolved.startsWith(base+path.sep))throw Error('路径超出交付目录');return resolved;}
export async function startHub({port=PORT,fetcher=fetchDownload}={}){
 await fsp.mkdir(CACHE,{recursive:true});await fsp.mkdir(STATE,{recursive:true});const key=randomBytes(24).toString('hex'),control=randomBytes(32).toString('hex'),projects=new Map(),states={};
 for(const id of Object.keys(MODELS))states[id]={status:await verifiedModel(id)&&await nativeReady(CACHE)?'ready':'absent',received:0,total:MODELS[id].size};
 const origin=`http://127.0.0.1:${port}`,matting=new NativeMatting(CACHE),controlFile=path.join(STATE,`hub-control-${port}.json`);
 const server=http.createServer(async(req,res)=>{
  try{
   if(req.headers.host!==`127.0.0.1:${port}`){json(res,403,{error:'仅接受本机连接'});return;}
   const url=new URL(req.url,origin),pathname=url.pathname;
   if(req.headers.origin&&req.headers.origin!==origin){json(res,403,{error:'请使用启动器打开模型模式'});return;}
   if(req.method==='OPTIONS'){res.writeHead(405);res.end();return;}
   if(pathname==='/api/status'&&req.method==='GET'){for(const [id,s]of Object.entries(states))if(s.status==='ready'&&(!await verifiedModel(id)||!await nativeReady(CACHE)))s.status='absent';const models=Object.fromEntries(Object.entries(states).map(([id,s])=>[id,{status:s.status,phase:s.phase,received:s.received,total:s.total,error:s.error}]));json(res,200,{app:APP,revision:REVISION,key,cache:CACHE,controlFile,models});return;}
   if(pathname==='/internal/register'&&req.method==='POST'){
    if(req.headers['x-control-key']!==control){json(res,403,{error:'启动器认证失败'});return;}
    const body=await readJSON(req),file=path.resolve(String(body.file||''));if(!file.toLowerCase().endsWith('.html')||!(await fsp.stat(file)).isFile()){json(res,400,{error:'请选择有效 HTML 项目'});return;}
    await safeLocal(path.dirname(file),path.basename(file));
    const id=createHash('sha256').update(file).digest('hex').slice(0,20);projects.set(id,file);json(res,200,{url:origin+'/p/'+id+'/'});return;
   }
   const deliveryRoute=pathname.match(/^\/p\/([a-f0-9]{20})\/(catalog|files\/(.+)|交付索引\.js)$/);
   if(deliveryRoute&&projects.has(deliveryRoute[1])){
    const folder=path.dirname(projects.get(deliveryRoute[1]));
    if(deliveryRoute[2]==='catalog'){
     if(req.headers['x-editor-key']!==key){json(res,403,{error:'请刷新编辑器'});return;}
     let manifest={version:2,character:path.basename(folder),current:[],candidates:[]};try{manifest=JSON.parse(await fsp.readFile(path.join(folder,'画布清单.json'),'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
     const names={特写:'head',正面:'front',左侧面:'side',背面:'back'},rows=[];let entries=[];try{entries=await fsp.readdir(await safeLocal(folder,'候选'));}catch(e){if(e.code!=='ENOENT')throw e;}
     for(const name of entries){const m=name.match(/^(\d{6})_(特写|正面|左侧面|背面)(?:_历史导入_[a-f0-9]{8})?\.png$/);if(!m)continue;const relative='候选/'+name;const file=await safeLocal(folder,relative),b=await fsp.readFile(file);if(!b.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])))continue;const sha=createHash('sha256').update(b).digest('hex'),view=names[m[2]],old=manifest.candidates.find(c=>c.path===relative&&c.sha256===sha);rows.push({...old,id:view+'-'+sha,view,name,path:relative,sha256:sha,sequence:+m[1],statusLabel:old?.statusLabel||'未验收'});}
     const current=[];for(const [name,view]of Object.entries({'01_特写.png':'head','02_正面.png':'front','03_左侧面.png':'side','04_背面.png':'back'})){try{const b=await fsp.readFile(await safeLocal(folder,name)),sha=createHash('sha256').update(b).digest('hex'),old=manifest.current.find(c=>c.view===view);current.push({...old,id:view+'-'+sha,view,name,path:name,sha256:sha});}catch(e){if(e.code!=='ENOENT')throw e;}}
     json(res,200,{...manifest,current,candidates:rows});return;
    }
    const relative=deliveryRoute[2]==='交付索引.js'?'交付索引.js':decodeURIComponent(deliveryRoute[3]||'');
    const allowed=relative==='交付索引.js'||/^(01_特写|02_正面|03_左侧面|04_背面)\.png$/.test(relative)||/^候选\/\d{6}_(特写|正面|左侧面|背面)(?:_历史导入_[a-f0-9]{8})?\.png$/.test(relative);
    if(!allowed){json(res,403,{error:'不是交付图片'});return;}await sendFile(req,res,await safeLocal(folder,relative),relative.endsWith('.js')?'text/javascript; charset=utf-8':'image/png');return;
   }
   if(pathname.startsWith('/api/')&&req.headers['x-editor-key']!==key){json(res,403,{error:'请刷新编辑器后重试'});return;}
   const mutation=pathname.match(/^\/api\/(download|cancel)\/(full|ben2|rmbg2)$/);
   if(mutation&&req.method==='POST'){
    const [,action,id]=mutation,s=states[id];
    if(action==='cancel'){s.abort?.abort();json(res,200,{ok:true});return;}
    if(s.status==='ready'||s.status==='downloading'){json(res,200,{ok:true,status:s.status});return;}
    if(Object.values(states).some(v=>v.status==='downloading')){json(res,409,{error:'请等待当前下载完成，或点击圆环取消。'});return;}
    Object.assign(s,{status:'downloading',error:null,phase:'准备下载',received:0,total:MODELS[id].size,abort:new AbortController()});
    const m=MODELS[id],signal=s.abort.signal;
    s.promise=(async()=>{try{await installNative(CACHE,{signal,fetcher,onProgress:(n,t)=>{s.received=n;s.total=t;},onPhase:phase=>{s.phase=phase;s.received=0;s.total=0;}});s.phase='下载模型';s.received=0;s.total=m.size;const dest=path.join(MODEL_DIR,id+'.onnx');const info=await downloadModelFile(id,dest,{signal,fetcher,onProgress:(n,t)=>{s.received=n;s.total=t;},onSource:source=>{s.phase='下载模型 · '+source;s.received=0;s.total=m.size;}});const stat=await fsp.stat(dest);await fsp.writeFile(path.join(MODEL_DIR,id+'.json'),JSON.stringify({...info,sha256:m.sha256,size:m.size,mtimeMs:stat.mtimeMs,repo:m.repo,revision:m.revision},null,2));s.status='ready';s.phase='已下载';}catch(e){s.status=signal.aborted?'absent':'error';s.error=signal.aborted?null:String(e.message||e);}finally{s.abort=null;}})();
    json(res,202,{ok:true,status:'downloading'});return;
   }
   const matte=pathname.match(/^\/api\/matte\/(full|ben2|rmbg2)$/);
   if(matte&&req.method==='POST'){
    const id=matte[1],m=MODELS[id];if(states[id].status!=='ready'){json(res,409,{error:'请先下载所选模型'});return;}if(matting.busy){json(res,409,{error:'高质量模型正在处理，请稍后。'});return;}
    if(req.headers['content-type']!=='application/octet-stream'){json(res,415,{error:'模型输入格式不符'});return;}
    const size=m.sizeInput,expected=3*size*size*4,chunks=[];let length=0;for await(const chunk of req){length+=chunk.length;if(length>expected)throw Error('模型输入过大');chunks.push(chunk);}if(length!==expected)throw Error('模型输入不完整');const input=Buffer.concat(chunks),pixels=input.buffer.slice(input.byteOffset,input.byteOffset+input.byteLength),result=await matting.run(path.join(MODEL_DIR,id+'.onnx'),pixels,m.activation);res.writeHead(200,{'Content-Type':'application/octet-stream','Content-Length':size*size,'Cache-Control':'no-store','X-Matting-Backend':result.backend});res.end(Buffer.from(result.alpha));return;
   }
   if(pathname==='/api/demo'&&req.method==='GET'){const html=await fsp.readFile(EDITOR,'utf8'),match=html.match(/<script id="demo-data" type="application\/json">([\s\S]*?)<\/script>/);json(res,200,JSON.parse(match?.[1]||'null'));return;}
   if(pathname==='/api/stop'&&req.method==='POST'){if((matting.busy||Object.values(states).some(s=>s.status==='downloading'))){json(res,409,{error:'正在下载，请先取消'});return;}json(res,200,{ok:true});setTimeout(()=>server.close(),50);return;}
   if(!['GET','HEAD'].includes(req.method)){json(res,405,{error:'不支持此操作'});return;}
   if(pathname==='/'||pathname==='/editor.html'){await sendFile(req,res,EDITOR,'text/html; charset=utf-8');return;}
   const project=pathname.match(/^\/p\/([a-f0-9]{20})\/?$/);if(project&&projects.has(project[1])){await sendFile(req,res,projects.get(project[1]),'text/html; charset=utf-8');return;}
   const model=pathname.match(/^\/models\/(lite512|lite1024)\.onnx$/);if(model&&states[model[1]].status==='ready'){await sendFile(req,res,path.join(MODEL_DIR,model[1]+'.onnx'),'application/octet-stream',true);return;}
   const runtime=pathname.match(/^\/runtime\/([^/]+)$/);if(runtime&&RUNTIME_FILES.includes(runtime[1])&&await runtimeReady()){await sendFile(req,res,path.join(RUNTIME,runtime[1]),runtime[1].endsWith('.wasm')?'application/wasm':'text/javascript',true);return;}
   if(pathname==='/favicon.ico'){res.writeHead(204);res.end();return;}
   json(res,404,{error:'文件不存在'});
  }catch(e){if(!res.headersSent)json(res,500,{error:String(e.message||e)});else res.destroy();}
 });
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,'127.0.0.1',resolve);});
 await fsp.writeFile(controlFile,JSON.stringify({app:APP,key:control,port}),{mode:0o600});
 server.on('close',()=>{matting.close();for(const s of Object.values(states))s.abort?.abort();});
 return {server,key,control,states,origin};
}
async function health(){try{const r=await fetch(ORIGIN+'/api/status',{signal:AbortSignal.timeout(900)});const d=await r.json();if(d.app==='character-canvas-v1'||d.app===APP&&(d.revision||0)<REVISION){const stop=await fetch(ORIGIN+'/api/stop',{method:'POST',headers:{'X-Editor-Key':d.key},signal:AbortSignal.timeout(1500)});if(!stop.ok)throw Error('旧服务正在下载，请结束下载后启动新版。');await new Promise(r=>setTimeout(r,250));return null;}if(d.app!==APP)throw new Error('18743 端口被其他应用占用');return d;}catch(e){if(e.message.includes('其他应用')||e.message.includes('旧服务'))throw e;return null;}}
async function openProject(file,noBrowser=false){let status=await health();if(!status){const child=spawn(process.execPath,[fileURLToPath(import.meta.url),'--serve'],{detached:true,stdio:'ignore',windowsHide:true});child.unref();for(let i=0;i<35;i++){await new Promise(r=>setTimeout(r,200));status=await health();if(status)break;}if(!status)throw new Error('本机服务未能启动，请检查 Node.js 和端口 18743。');}let target=ORIGIN+'/';if(file){const info=JSON.parse(await fsp.readFile(status.controlFile||path.join(STATE,'hub-control.json'),'utf8'));const r=await fetch(ORIGIN+'/internal/register',{method:'POST',headers:{'Content-Type':'application/json','X-Control-Key':info.key},body:JSON.stringify({file:path.resolve(file)})});const data=await r.json();if(!r.ok)throw new Error(data.error);target=data.url;}if(noBrowser){console.log(target);return;}if(process.platform==='win32'){const ps=spawn('powershell.exe',['-NoProfile','-Command',`Start-Process '${target.replaceAll("'","''")}'`],{stdio:'ignore',windowsHide:true});ps.unref();}else if(process.platform==='darwin')spawn('open',[target],{stdio:'ignore'}).unref();else spawn('xdg-open',[target],{stdio:'ignore'}).unref();console.log(target);}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const args=process.argv.slice(2);try{if(args.includes('--serve')){await startHub();console.log('本机画布服务：'+ORIGIN);}else{const at=args.indexOf('--open');await openProject(at>=0?args[at+1]:EDITOR,args.includes('--no-browser'));}}catch(e){console.error(e.message);process.exitCode=1;}
}
