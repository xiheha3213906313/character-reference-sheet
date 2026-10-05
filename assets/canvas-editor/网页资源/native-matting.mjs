import path from'node:path';import{Worker}from'node:worker_threads';import{nativeRoot}from'./native-runtime.mjs';
export class NativeMatting{
 constructor(cache){this.cache=cache;this.worker=null;this.model=null;this.busy=false;this.idle=null;this.reject=null;}
 async run(model,pixels,activation='logits'){if(this.busy)throw Error('高质量模型正在处理，请稍后。');this.busy=true;clearTimeout(this.idle);
  try{if(this.worker&&this.model!==model){const previous=this.worker;this.worker=null;this.model=null;await previous.terminate();}if(!this.worker){const created=new Worker(new URL('./native-worker.mjs',import.meta.url),{workerData:{runtime:nativeRoot(this.cache),model,activation}});this.worker=created;this.model=model;created.on('error',e=>{if(this.worker===created)this.reject?.(e);});created.on('exit',code=>{if(this.worker===created){this.worker=null;this.model=null;if(code!==0)this.reject?.(new Error('高质量模型进程已退出'));}});}
   const worker=this.worker;return await new Promise((resolve,reject)=>{let timer;const finish=(error,result)=>{clearTimeout(timer);worker.off('message',message);this.reject=null;error?reject(error):resolve(result);};const message=data=>data.error?finish(new Error(data.error)):finish(null,data);this.reject=e=>finish(e);worker.once('message',message);timer=setTimeout(()=>{finish(new Error('高质量模型处理超时，请重试'));this.close();},180000);worker.postMessage({pixels},[pixels]);});
  }finally{this.busy=false;this.idle=setTimeout(()=>this.close(),60000);this.idle.unref();}
 }
 close(){clearTimeout(this.idle);const worker=this.worker;this.worker=null;this.model=null;worker?.terminate();}
}
