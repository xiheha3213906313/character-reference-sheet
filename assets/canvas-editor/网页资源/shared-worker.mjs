import {parentPort} from 'node:worker_threads';
import {sharedSampling} from './shared-sampling.mjs';
parentPort.once('message',({pixels,width,height,options})=>{
 try{const rgba=sharedSampling(new Uint8Array(pixels),width,height,options);parentPort.postMessage({pixels:rgba.buffer,backend:'Shared Sampling · CPU'},[rgba.buffer]);}
 catch(error){parentPort.postMessage({error:String(error.message||error)});}
});
