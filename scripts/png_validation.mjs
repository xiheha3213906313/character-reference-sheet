/** Validate complete PNG chunks and decoded scanlines using Node's built-in zlib. */
import {inflateSync} from 'node:zlib';

const signature=Buffer.from([137,80,78,71,13,10,26,10]);
const crcTable=Uint32Array.from({length:256},(_,n)=>{
 for(let k=0;k<8;k++)n=(n&1)?0xedb88320^(n>>>1):n>>>1;
 return n>>>0;
});
function crc32(bytes){let c=0xffffffff;for(const v of bytes)c=crcTable[(c^v)&255]^(c>>>8);return(c^0xffffffff)>>>0;}

export function pngSize(bytes,label='PNG'){
 const invalid=reason=>{throw Error('无效或不完整PNG：'+label+'（'+reason+'）');};
 if(!Buffer.isBuffer(bytes)||bytes.length<33||!bytes.subarray(0,8).equals(signature))invalid('文件头');
 let offset=8,header=null,palette=false,ended=false,idatEnded=false;
 const compressed=[];
 while(offset<bytes.length){
  if(offset+12>bytes.length)invalid('数据块截断');
  const length=bytes.readUInt32BE(offset),end=offset+12+length;
  if(length>0x7fffffff||end>bytes.length)invalid('数据块截断');
  const type=bytes.toString('latin1',offset+4,offset+8),data=bytes.subarray(offset+8,end-4);
  if(!/^[A-Za-z]{4}$/.test(type)||type[2]!==type[2].toUpperCase())invalid('数据块类型');
  if(crc32(bytes.subarray(offset+4,end-4))!==bytes.readUInt32BE(end-4))invalid(type+'校验失败');
  if(!header&&type!=='IHDR')invalid('缺少首部');
  if(type==='IHDR'){
   if(header||length!==13)invalid('重复或损坏IHDR');
   const width=data.readUInt32BE(0),height=data.readUInt32BE(4),bits=data[8],color=data[9],interlace=data[12];
   const depths={0:[1,2,4,8,16],2:[8,16],3:[1,2,4,8],4:[8,16],6:[8,16]};
   if(!width||!height||width>0x7fffffff||height>0x7fffffff||!depths[color]?.includes(bits)||data[10]!==0||data[11]!==0||interlace>1)invalid('IHDR参数');
   header={width,height,bits,color,interlace};
  }else if(type==='PLTE'){
   if(palette||compressed.length||!length||length%3||length>768||[0,4].includes(header.color)||(header.color===3&&length/3>2**header.bits))invalid('调色板');
   palette=true;
  }else if(type==='IDAT'){
   if(idatEnded||(header.color===3&&!palette))invalid('图像数据顺序');
   compressed.push(data);
  }else if(type==='IEND'){
   if(length||!compressed.length||end!==bytes.length)invalid('缺失图像数据或尾部异常');
   ended=true;offset=end;break;
  }else if(type[0]===type[0].toUpperCase())invalid('不支持的关键数据块');
  if(compressed.length&&type!=='IDAT')idatEnded=true;
  offset=end;
 }
 if(!ended)invalid('缺少IEND');
 const channels={0:1,2:3,3:1,4:2,6:4}[header.color];
 const passes=header.interlace?[[0,0,8,8],[4,0,8,8],[0,4,4,8],[2,0,4,4],[0,2,2,4],[1,0,2,2],[0,1,1,2]]:[[0,0,1,1]];
 const rows=[];let expected=0;
 for(const[x,y,dx,dy]of passes){
  const w=Math.max(0,Math.ceil((header.width-x)/dx)),h=Math.max(0,Math.ceil((header.height-y)/dy));
  if(!w||!h)continue;
  const stride=1+Math.ceil(w*channels*header.bits/8);rows.push({h,stride});expected+=h*stride;
 }
 // Bound validation memory; normal generated character images are far below this.
 if(!Number.isSafeInteger(expected)||expected>512*1024*1024)invalid('解码数据超过校验容量');
 const packed=Buffer.concat(compressed);let decoded;
 try{
  const result=inflateSync(packed,{maxOutputLength:expected+1,info:true});
  if(result.engine.bytesWritten!==packed.length)invalid('压缩数据尾部异常');
  decoded=result.buffer;
 }catch(error){invalid('图像数据无法解码：'+error.message);}
 if(decoded.length!==expected)invalid('解码长度与尺寸不符');
 let cursor=0;for(const{h,stride}of rows)for(let y=0;y<h;y++){if(decoded[cursor]>4)invalid('扫描行过滤器');cursor+=stride;}
 return{width:header.width,height:header.height};
}
