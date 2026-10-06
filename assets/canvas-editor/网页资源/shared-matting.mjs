import {Worker} from 'node:worker_threads';
export class SharedMatting{
 constructor(){this.worker=null;this.busy=false;this.cancel=null;}
 async run(pixels,width,height,options){
  if(this.busy)throw Error('Shared Sampling 正在处理，请稍后。');this.busy=true;
  let worker;
  try{worker=new Worker(new URL('./shared-worker.mjs',import.meta.url));this.worker=worker;return await new Promise((resolve,reject)=>{
   let settled=false;const finish=(error,data)=>{if(settled)return;settled=true;clearTimeout(timer);this.cancel=null;error?reject(error):resolve(data);};
   const timer=setTimeout(()=>finish(new Error('Shared Sampling 处理超时，请缩小图片后重试。')),180000);
   this.cancel=()=>finish(new Error('Shared Sampling 服务已停止。'));
   worker.once('message',data=>data.error?finish(new Error(data.error)):finish(null,data));
   worker.once('error',error=>finish(error));worker.once('exit',()=>finish(new Error('Shared Sampling 进程已退出。')));
   worker.postMessage({pixels,width,height,options},[pixels]);
  });}finally{this.busy=false;this.cancel=null;if(this.worker===worker)this.worker=null;await worker?.terminate();}
 }
 close(){this.cancel?.();this.worker?.terminate();this.worker=null;}
}
