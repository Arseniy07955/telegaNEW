(()=>{
'use strict';
// In-page control: stands in for the app behind the injected WebView
// boundary (TelegramWebProxy) with the same RPC workload as Bench.java, so
// the real bridge page + relay + network are measured without the Java
// transport and without WebView IPC. Ideal client: DATA <= 64 KiB, 4 MiB
// per-stream credit, WINDOW returned for every received frame at once.
const P=__PARAMS__;
const report=o=>{try{BenchReport.postMessage(JSON.stringify(o))}catch(e){}};
const now=()=>performance.now();
let recv=null;
const frame=(type,id,payload)=>{
 const n=payload?payload.byteLength:0,buf=new ArrayBuffer(8+n),v=new DataView(buf);
 v.setUint8(0,type);v.setUint8(1,(id>>>16)&255);v.setUint8(2,(id>>>8)&255);v.setUint8(3,id&255);v.setUint32(4,n);
 if(n)new Uint8Array(buf,8).set(payload);
 return buf;
};
const toPage=buf=>recv({data:buf});
const ZERO=new Uint8Array(65536);
const streams=new Map();let nextId=1;
function open(){const s={id:nextId++,credit:4194304,out:[],outBytes:0,hdr:[],skip:0,inflight:[]};streams.set(s.id,s);toPage(frame(1,s.id));return s}
function flush(s){
 while(s.out.length&&s.credit>0){
  const head=s.out[0],n=Math.min(head.byteLength,65536,s.credit);
  toPage(frame(2,s.id,head.subarray(0,n)));s.credit-=n;
  if(n===head.byteLength)s.out.shift();else s.out[0]=head.subarray(n);
 }
}
function rpc(s,reqLen,respLen,delay,cb){
 const h=new Uint8Array(16),v=new DataView(h.buffer);v.setUint32(0,reqLen);v.setUint32(4,respLen);v.setUint32(12,delay);
 s.inflight.push({t:now(),cb});s.out.push(h);
 let left=reqLen;while(left>0){const k=Math.min(left,65536);s.out.push(ZERO.subarray(0,k));left-=k}
 flush(s);
}
function onData(s,bytes){
 let i=0;
 while(i<bytes.length){
  if(s.skip>0){const k=Math.min(s.skip,bytes.length-i);s.skip-=k;i+=k;if(s.skip===0)done(s);continue}
  s.hdr.push(bytes[i++]);
  if(s.hdr.length===8){s.skip=((s.hdr[0]<<24)>>>0)+(s.hdr[1]<<16)+(s.hdr[2]<<8)+s.hdr[3];s.hdr=[];if(s.skip===0)done(s)}
 }
}
function done(s){const r=s.inflight.shift();r.cb(now()-r.t)}
function fromPage(buf){
 const v=new DataView(buf),type=v.getUint8(0),id=(v.getUint8(1)<<16)|(v.getUint8(2)<<8)|v.getUint8(3),len=v.getUint32(4);
 if(type===0x11){setTimeout(begin,0);return}
 const s=streams.get(id);if(!s)return;
 if(type===4){s.credit+=v.getUint32(8);flush(s)}
 else if(type===2){const p=new Uint8Array(4);new DataView(p.buffer).setUint32(0,len);toPage(frame(4,id,p));onData(s,new Uint8Array(buf,8,len))}
 else if(type===3)report({ok:false,error:'relay CLOSE '+id});
}
const pct=a=>{if(!a.length)return null;a=a.slice().sort((x,y)=>x-y);return{p50:+a[a.length>>1].toFixed(1),p90:+a[Math.min(a.length-1,Math.ceil(a.length*0.9)-1)].toFixed(1),max:+a[a.length-1].toFixed(1),n:a.length}};
function transfer(up,total,part,parallel,conns,cb){
 const cs=[];for(let i=0;i<conns;i++)cs.push(open());
 const parts=Math.max(1,Math.floor(total/part));let next=0,fin=0,inflight=0;const t0=now(),ms=[];
 const pump=()=>{while(next<parts&&inflight<parallel){const s=cs[next%conns];next++;inflight++;rpc(s,up?part:64,up?16:part,P.dc,d=>{ms.push(d);inflight--;fin++;if(fin===parts)cb({MBps:parts*part/1048576/((now()-t0)/1000),part_ms:pct(ms)});else pump()})}};
 pump();
}
let t00=now();
function begin(){
 const chat=open(),res={};
 const ping=cb=>rpc(chat,128,256,P.dc,cb);
 ping(()=>{res.first_ms=Math.round(now()-t00);const idle=[];let n=0;
  const idleLoop=()=>ping(d=>{idle.push(d);if(++n<P.idle)idleLoop();else{res.idle_ms=pct(idle);bulk()}});
  if(P.idle>0)idleLoop();else bulk();
  function bulk(){
   const jobs=[];if(P.up)jobs.push([true,P.up*1048576,P.upPart,P.upParallel,P.upConns]);if(P.down)jobs.push([false,P.down*1048576,P.downPart,P.downParallel,P.downConns]);
   if(!jobs.length){res.ok=true;report(res);return}
   const loaded=[];let stop=false,left=jobs.length;const t0=now();let total=0;
   const pl=()=>{if(stop)return;ping(d=>{loaded.push(d);setTimeout(pl,P.ping)})};pl();
   for(const j of jobs){total+=Math.floor(j[1]/j[2])*j[2];transfer(j[0],j[1],j[2],j[3],j[4],r=>{res[(j[0]?'up':'down')+'_MBps']=+r.MBps.toFixed(2);res[(j[0]?'up':'down')+'_part_ms']=r.part_ms;if(--left===0){stop=true;res.total_MBps=+(total/1048576/((now()-t0)/1000)).toFixed(2);res.loaded_ms=pct(loaded);res.ok=true;report(res)}})}
  }
 });
}
const bridge={
 postMessage(value){
  if(value instanceof ArrayBuffer){fromPage(value);return}
  let m;try{m=JSON.parse(value)}catch(e){return}
  if(m.t==='tproxy-android-init')setTimeout(()=>toPage(frame(0x10,0,new Uint8Array([1]))),0);
  else if(m.t==='close'||m.state==='failed')report({ok:false,error:'bridge failed'});
 },
 get onmessage(){return recv},set onmessage(f){recv=f}
};
Object.defineProperty(globalThis,'TelegramWebProxy',{value:bridge,configurable:false,writable:false});
})();
