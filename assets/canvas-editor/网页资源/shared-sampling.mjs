/**
 * Shared Sampling / Shared Matting: automatic trimap, sample gathering,
 * neighborhood sample refinement, and confidence-weighted local smoothing.
 * Gastal & Oliveira (2010): https://www.inf.ufrgs.br/~eslgastal/SharedMatting/
 * Math adapted from https://github.com/pymatting/pymatting (estimate_alpha_sm).
 * The image-gradient path uses both x and y increments.
 *
 * MIT License
 * Copyright (c) 2020 PyMatting
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */
const clamp=(v,lo=0,hi=1)=>Math.max(lo,Math.min(hi,v));
function edgeColor(rgba,w,h){
 const buckets=new Map(),step=Math.max(1,Math.floor(Math.max(w,h)/80));
 const add=i=>{if(rgba[4*i+3]<10)return;const r=rgba[4*i],g=rgba[4*i+1],b=rgba[4*i+2],id=[r,g,b].map(v=>Math.round(v/24)).join(',');const v=buckets.get(id)||[0,0,0,0];v[0]+=r;v[1]+=g;v[2]+=b;v[3]++;buckets.set(id,v);};
 for(let x=0;x<w;x+=step){add(x);add((h-1)*w+x);}for(let y=0;y<h;y+=step){add(y*w);add(y*w+w-1);}
 const best=[...buckets.values()].sort((a,b)=>b[3]-a[3])[0];return best?best.slice(0,3).map(v=>v/best[3]):[255,255,255];
}
// Manhattan distance from the opposite class. Outside the image is not a label:
// a subject touching the canvas edge must still be allowed to be foreground.
function classDistance(mask,w,h,label,onProgress=null){
 const n=w*h,d=new Uint16Array(n),far=w+h+1;for(let i=0;i<n;i++)d[i]=mask[i]===label?far:0;
 for(let y=0;y<h;y++){for(let x=0;x<w;x++){const i=y*w+x;if(x)d[i]=Math.min(d[i],d[i-1]+1);if(y)d[i]=Math.min(d[i],d[i-w]+1);}onProgress?.(.5*(y+1)/h);}
 for(let y=h-1;y>=0;y--){for(let x=w-1;x>=0;x--){const i=y*w+x;if(x+1<w)d[i]=Math.min(d[i],d[i+1]+1);if(y+1<h)d[i]=Math.min(d[i],d[i+w]+1);}onProgress?.(.5+.5*(h-y)/h);}
 return d;
}
export function automaticTrimap(rgba,w,h,opts={},onProgress=null){
 const n=w*h,key=opts.key||edgeColor(rgba,w,h),strength=clamp(Number(opts.strength??26),0,100),threshold=12+strength*1.45;
 const limit=threshold+12,distance=new Float32Array(n),background=new Uint8Array(n),visited=new Uint8Array(n),queue=new Int32Array(n);let head=0,tail=0;
 for(let i=0;i<n;i++){distance[i]=Math.hypot(rgba[4*i]-key[0],rgba[4*i+1]-key[1],rgba[4*i+2]-key[2]);if((i&32767)===0)onProgress?.(.12*i/n);}
 const visit=i=>{if(visited[i])return;visited[i]=1;if(rgba[4*i+3]===0||distance[i]<=limit){background[i]=1;queue[tail++]=i;}};
 if(opts.global){for(let i=0;i<n;i++){visit(i);if((i&32767)===0)onProgress?.(.12+.28*i/n);}}else{
  for(let x=0;x<w;x++){visit(x);visit((h-1)*w+x);}for(let y=0;y<h;y++){visit(y*w);visit(y*w+w-1);}
  while(head<tail){const i=queue[head++],x=i%w;if(x)visit(i-1);if(x+1<w)visit(i+1);if(i>=w)visit(i-w);if(i+w<n)visit(i+w);if((head&32767)===0)onProgress?.(.12+.28*head/n);}
 }
 if(!tail)throw Error('没有找到背景区域，请手动取色或调整强度。');
 if(tail===n)throw Error('没有找到可靠前景，已保留原图；请降低强度或重新取色。');
 const trimap=new Float32Array(n);trimap.fill(.5);
 onProgress?.(.4);
 const toForeground=classDistance(background,w,h,1,onProgress?v=>onProgress(.4+.225*v):null),toBackground=classDistance(background,w,h,0,onProgress?v=>onProgress(.625+.225*v):null),radius=Math.max(3,Math.round(Math.min(w,h)*.008)),strict=Math.max(4,threshold*.45);let fg=0,bg=0;
 for(let i=0;i<n;i++){
  // Pale material and the entire contour stay unknown until sampling. A color
  // threshold alone never produces the final alpha matte.
  if(rgba[4*i+3]===0||(background[i]&&toForeground[i]>radius&&distance[i]<=strict)){trimap[i]=0;bg++;}
  else if(!background[i]&&toBackground[i]>radius){trimap[i]=1;fg++;}
  if((i&32767)===0)onProgress?.(.85+.15*i/n);
 }
 if(!fg||!bg)throw Error('自动标记未找到足够的前景／背景，请调整强度或手动取色。');
 onProgress?.(1);return trimap;
}
export function sharedSampling(rgba,w,h,opts={},suppliedTrimap=null,onProgress=null){
 const n=w*h;if(!(rgba instanceof Uint8Array||rgba instanceof Uint8ClampedArray)||rgba.length!==n*4)throw Error('Shared Sampling 图片数据不完整。');
 // Fractions describe completed processing stages, not elapsed time or ETA.
 let last=-1,lastTime=0;const emit=value=>{if(!onProgress)return;const now=Date.now();if(value===1||value-last>=.01||now-lastTime>=100){last=value;lastTime=now;onProgress(value);}};emit(0);
 const trimap=suppliedTrimap?Float32Array.from(suppliedTrimap):automaticTrimap(rgba,w,h,opts,v=>emit(.16*v));emit(.16);
 if(trimap.length!==n)throw Error('Shared Sampling 标记尺寸不符。');
 const image=new Float32Array(n*3);for(let i=0;i<n;i++){for(let c=0;c<3;c++)image[3*i+c]=rgba[4*i+c]/255;if((i&32767)===0)emit(.16+.01*i/n);}
 const expanded=trimap.slice(),unknown=[];let nf=0,nb=0;const avgF=[0,0,0],avgB=[0,0,0];
 // Trimap expansion uses only the original known samples, never a newly
 // expanded neighbor, so the result is independent of traversal order.
 const offsets=[];for(let dy=-10;dy<=10;dy++)for(let dx=-10;dx<=10;dx++)if(dx*dx+dy*dy<=100)offsets.push([dx,dy,dx*dx+dy*dy]);offsets.sort((a,b)=>a[2]-b[2]);
 for(let y=0;y<h;y++)for(let x=0;x<w;x++){
  const p=y*w+x,pi=3*p;if(trimap[p]!==0&&trimap[p]!==1){
   for(const [dx,dy]of offsets){const xx=x+dx,yy=y+dy;if(xx<0||xx>=w||yy<0||yy>=h)continue;const q=yy*w+xx;if(trimap[q]!==0&&trimap[q]!==1)continue;const qi=3*q;
    if((image[pi]-image[qi])**2+(image[pi+1]-image[qi+1])**2+(image[pi+2]-image[qi+2])**2<=.02**2){expanded[p]=trimap[q];break;}
   }
  }
  if(expanded[p]===0){nb++;for(let c=0;c<3;c++)avgB[c]+=image[pi+c];}else if(expanded[p]===1){nf++;for(let c=0;c<3;c++)avgF[c]+=image[pi+c];}else unknown.push(p);
  if(x===w-1)emit(.17+.08*(y+1)/h);
 }
 if(!nf||!nb)throw Error('Shared Sampling 需要可靠的前景和背景标记。');
 for(let c=0;c<3;c++){avgF[c]/=nf;avgB[c]/=nb;}
 const F=new Float32Array(n*3),B=new Float32Array(n*3);for(let i=0;i<n;i++)for(let c=0;c<3;c++){F[3*i+c]=expanded[i]===1?image[3*i+c]:avgF[c];B[3*i+c]=expanded[i]===0?image[3*i+c]:avgB[c];}
 const estimate=(p,fr,fg,fb,br,bg,bb)=>{const i=3*p,dr=fr-br,dg=fg-bg,db=fb-bb;return clamp(((image[i]-br)*dr+(image[i+1]-bg)*dg+(image[i+2]-bb)*db)/(dr*dr+dg*dg+db*db+1e-5));};
 const error=(p,fr,fg,fb,br,bg,bb)=>{const a=estimate(p,fr,fg,fb,br,bg,bb),i=3*p;return (a*fr+(1-a)*br-image[i])**2+(a*fg+(1-a)*bg-image[i+1])**2+(a*fb+(1-a)*bb-image[i+2])**2;};
 const gradientPath=(x,y,sx,sy)=>{
  const steps=clamp(Math.ceil(Math.hypot(sx-x,sy-y)),1,10),dx=(sx-x)/steps,dy=(sy-y)/steps;if(dx===0&&dy===0)return 0;let total=0;
  for(let step=0;step<=steps;step++){const qx=clamp(Math.trunc(x+step*dx),0,w-1),qy=clamp(Math.trunc(y+step*dy),0,h-1),l=3*(qy*w+Math.max(0,qx-1)),r=3*(qy*w+Math.min(w-1,qx+1)),u=3*(Math.min(h-1,qy+1)*w+qx),d=3*(Math.max(0,qy-1)*w+qx);let e=0;
   for(let c=0;c<3;c++){const v=.5*(dx*(image[r+c]-image[l+c])+dy*(image[u+c]-image[d+c]));e+=v*v;}total+=Math.sqrt(e);
  }return total;
 };
 // Four rotated rays per unknown pixel; neighboring pixels later share the
 // gathered pairs. The deterministic 8×8 angle pattern avoids random flicker.
 let completed=0;emit(.25);
 for(const p of unknown){
  const x=p%w,y=Math.floor(p/w),start=2*Math.PI*((((x%8)+(y%8)*8)*99991)%64)/64,fs=[],bs=[];
  for(let k=0;k<4;k++){const angle=start+k*Math.PI/2,c=Math.cos(angle),s=Math.sin(angle);let hasF=false,hasB=false;
   for(let step=0;step<2*Math.max(w,h);step++){const xx=Math.trunc(x+step*c),yy=Math.trunc(y+step*s);if(xx<0||yy<0||xx>=w||yy>=h)break;const q=yy*w+xx;
    if(!hasF&&expanded[q]===1){fs.push([image[3*q],image[3*q+1],image[3*q+2],xx,yy]);hasF=true;}if(!hasB&&expanded[q]===0){bs.push([image[3*q],image[3*q+1],image[3*q+2],xx,yy]);hasB=true;}if(hasF&&hasB)break;
   }
  }
  if(!fs.length)fs.push([...avgF,x,y]);if(!bs.length)bs.push([...avgB,x,y]);
  const ef=fs.map(s=>gradientPath(x,y,s[3],s[4])),eb=bs.map(s=>gradientPath(x,y,s[3],s[4])),pf=Math.min(...eb)/(Math.min(...ef)+Math.min(...eb)+1e-5);let cost=Infinity,bestF=fs[0],bestB=bs[0];
  for(const f of fs)for(const b of bs){const a=estimate(p,f[0],f[1],f[2],b[0],b[1],b[2]),ap=pf+(1-2*pf)*a;let np=0;
   for(let dy=-1;dy<=1;dy++)for(let dx=-1;dx<=1;dx++){const q=clamp(y+dy,0,h-1)*w+clamp(x+dx,0,w-1);np+=error(q,f[0],f[1],f[2],b[0],b[1],b[2]);}
   const candidate=np**3*ap**2*Math.hypot(x-f[3],y-f[4])*Math.hypot(x-b[3],y-b[4])**4;if(candidate<cost){cost=candidate;bestF=f;bestB=b;}
  }
  for(let c=0;c<3;c++){F[3*p+c]=bestF[c];B[3*p+c]=bestB[c];}
  if((++completed&255)===0)emit(.25+.35*completed/unknown.length);
 }
 emit(.6);completed=0;
 const refinedF=F.slice(),refinedB=B.slice(),refinedAlpha=expanded.slice();
 // Select the three best neighboring pairs using their reconstruction error
 // at p, then average the pairs and recompute alpha at p.
 for(const p of unknown){const x=p%w,y=Math.floor(p/w),costs=[Infinity,Infinity,Infinity],samples=[p,p,p];
  for(let yy=Math.max(0,y-5);yy<=Math.min(h-1,y+5);yy++)for(let xx=Math.max(0,x-5);xx<=Math.min(w-1,x+5);xx++){const q=yy*w+xx,i=3*q,e=error(p,F[i],F[i+1],F[i+2],B[i],B[i+1],B[i+2]);let k=0;for(let j=1;j<3;j++)if(costs[j]>costs[k])k=j;if(e<costs[k]){costs[k]=e;samples[k]=q;}}
  for(let c=0;c<3;c++){refinedF[3*p+c]=(F[3*samples[0]+c]+F[3*samples[1]+c]+F[3*samples[2]+c])/3;refinedB[3*p+c]=(B[3*samples[0]+c]+B[3*samples[1]+c]+B[3*samples[2]+c])/3;}
  const i=3*p;refinedAlpha[p]=estimate(p,refinedF[i],refinedF[i+1],refinedF[i+2],refinedB[i],refinedB[i+1],refinedB[i+2]);
  if((++completed&255)===0)emit(.6+.16*completed/unknown.length);
 }
 emit(.76);completed=0;
 const finalF=refinedF.slice(),finalB=refinedB.slice(),alpha=expanded.slice(),W=new Float32Array(n),confidence=new Float32Array(n),sigma=121/(9*Math.PI),gaussian=[];
 for(let dy=-5;dy<=5;dy++)for(let dx=-5;dx<=5;dx++)gaussian.push([dx,dy,Math.exp(-(dx*dx+dy*dy)/sigma)]);
 for(const p of unknown){const x=p%w,y=Math.floor(p/w),ff=[0,0,0],bb=[0,0,0],ap=refinedAlpha[p];let sf=0,sb=0;
  for(const [dx,dy,weight]of gaussian){const xx=x+dx,yy=y+dy;if(xx<0||xx>=w||yy<0||yy>=h)continue;const q=yy*w+xx,wc=weight*(q===p?1:Math.abs(ap-refinedAlpha[q])),wf=Math.max(1e-5,wc*refinedAlpha[q]),wb=Math.max(1e-5,wc*(1-refinedAlpha[q]));sf+=wf;sb+=wb;for(let c=0;c<3;c++){ff[c]+=wf*refinedF[3*q+c];bb[c]+=wb*refinedB[3*q+c];}}
  for(let c=0;c<3;c++){ff[c]/=sf;bb[c]/=sb;finalF[3*p+c]=ff[c];finalB[3*p+c]=bb[c];}alpha[p]=estimate(p,...ff,...bb);W[p]=ap*(1-ap);
  if((++completed&255)===0)emit(.76+.1*completed/unknown.length);
 }
 emit(.86);completed=0;
 for(const p of unknown){const x=p%w,y=Math.floor(p/w);let sw=0,dfb=0;
  for(const [dx,dy]of gaussian){const xx=x+dx,yy=y+dy;if(xx<0||xx>=w||yy<0||yy>=h)continue;const q=yy*w+xx,i=3*q;sw+=W[q];dfb+=W[q]*Math.hypot(finalF[i]-finalB[i],finalF[i+1]-finalB[i+1],finalF[i+2]-finalB[i+2]);}
  const i=3*p,fd=Math.hypot(finalF[i]-finalB[i],finalF[i+1]-finalB[i+1],finalF[i+2]-finalB[i+2]);confidence[p]=Math.min(1,fd/(dfb/(sw+1e-5)+1e-5))*Math.exp(-Math.sqrt(error(p,finalF[i],finalF[i+1],finalF[i+2],finalB[i],finalB[i+1],finalB[i+2]))/.1);
  if((++completed&255)===0)emit(.86+.05*completed/unknown.length);
 }
 emit(.91);completed=0;
 for(const p of unknown){const x=p%w,y=Math.floor(p/w);let sum=0,weightSum=0;
  for(const [dx,dy,g]of gaussian){const xx=x+dx,yy=y+dy;if(xx<0||xx>=w||yy<0||yy>=h)continue;const q=yy*w+xx,weight=g+Number(expanded[q]===0||expanded[q]===1);sum+=weight*refinedAlpha[q];weightSum+=weight;}alpha[p]=clamp(confidence[p]*alpha[p]+(1-confidence[p])*sum/weightSum);
  if((++completed&255)===0)emit(.91+.06*completed/unknown.length);
 }
 emit(.97);
 // Return alpha plus estimated background RGB. The editor recovers foreground
 // colors at the ORIGINAL resolution; opaque interiors retain original pixels.
 const output=new Uint8Array(n*4);for(let i=0;i<n;i++){for(let c=0;c<3;c++)output[4*i+c]=Math.round(clamp(finalB[3*i+c])*255);output[4*i+3]=Math.round(clamp(alpha[i])*255);if((i&32767)===0)emit(.97+.03*i/n);}
 emit(1);return output;
}
